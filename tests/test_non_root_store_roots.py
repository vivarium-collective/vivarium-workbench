"""Non-root single-pod HeLx deployments run this process with a HOME it can't
write to (or that doesn't even exist) — the same class of bug
``env_worker._default_provision_target`` already fixed for its hardcoded
``/scratch`` default (a ``PermissionError`` on first write). This file covers
the three ``lib`` modules with an analogous HOME-based cache/store default:
``materialization.store_root``, ``remote_build_source.build_cache_root``, and
``repo_source.store_root`` — plus the shared fallback helper they all route
through, ``env_compat.home_or_tmp_default``.

Hermetic: no real ``uv``/``git``/network. Each test explicitly controls
``Path.home()`` and ``os.access`` rather than touching the real filesystem's
permissions.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from vivarium_workbench.lib import env_compat
from vivarium_workbench.lib import materialization as m
from vivarium_workbench.lib import remote_build_source as rbs
from vivarium_workbench.lib import repo_source as rs

# (module, function name, env var, HOME-relative leaf parts)
_ROOTS = [
    (m, "store_root", "VIVARIUM_WORKBENCH_VENV_STORE", (".cache", "vivarium-workbench", "venvs")),
    (rbs, "build_cache_root", "VIVARIUM_WORKBENCH_BUILD_CACHE", (".pbg", "build-cache")),
    (rs, "store_root", "VIVARIUM_WORKBENCH_REPO_STORE", (".cache", "vivarium-workbench", "repos")),
]


def _unset_all_overrides(monkeypatch):
    for _mod, _fn, env_var, _parts in _ROOTS:
        monkeypatch.delenv(env_var, raising=False)


# -- (a) env override always wins, regardless of HOME writability ------------
@pytest.mark.parametrize("mod,fn,env_var,parts", _ROOTS)
def test_env_override_wins(mod, fn, env_var, parts, tmp_path, monkeypatch):
    _unset_all_overrides(monkeypatch)
    target = tmp_path / "explicit-override"
    monkeypatch.setenv(env_var, str(target))
    assert getattr(mod, fn)() == target


# -- (b) HOME not writable -> falls back under tempfile.gettempdir() ---------
@pytest.mark.parametrize("mod,fn,env_var,parts", _ROOTS)
def test_home_not_writable_falls_back_to_tmp(mod, fn, env_var, parts, tmp_path, monkeypatch):
    _unset_all_overrides(monkeypatch)
    fake_home = tmp_path / "no-write-home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(os, "access", lambda *a, **k: False)

    got = getattr(mod, fn)()

    expected = Path(tempfile.gettempdir()) / "vivarium-workbench" / Path(*parts)
    assert got == expected
    assert str(fake_home) not in str(got)


# -- (c) HOME writable -> unchanged HOME-based default (back-compat) ---------
@pytest.mark.parametrize("mod,fn,env_var,parts", _ROOTS)
def test_home_writable_keeps_home_default(mod, fn, env_var, parts, tmp_path, monkeypatch):
    _unset_all_overrides(monkeypatch)
    fake_home = tmp_path / "writable-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    # Real os.access is used here (untouched) — fake_home is a real, writable dir.

    got = getattr(mod, fn)()

    assert got == fake_home.joinpath(*parts)


# -- env_compat.home_or_tmp_default: the shared primitive --------------------
def test_home_or_tmp_default_walks_up_to_nearest_existing_ancestor(tmp_path, monkeypatch):
    """The HOME-based candidate's own dir tree doesn't exist yet (typical first
    run — e.g. ``~/.cache`` was never created) — writability is decided by the
    nearest ancestor that DOES exist, not the not-yet-created leaf."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()  # exists and is writable; ".cache/vivarium-workbench/venvs" is not
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    got = env_compat.home_or_tmp_default(".cache", "vivarium-workbench", "venvs")
    assert got == fake_home / ".cache" / "vivarium-workbench" / "venvs"


def test_home_or_tmp_default_swallows_access_probe_errors(tmp_path, monkeypatch):
    """The writability PROBE (``os.access`` on the nearest existing ancestor) is
    guarded: if it raises (e.g. a permission-denied stat on some odd mount),
    that must read as "not writable" and fall back to the temp dir, never
    propagate and break the caller."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    def _boom(*a, **k):
        raise OSError("permission probe failed")

    monkeypatch.setattr(os, "access", _boom)
    got = env_compat.home_or_tmp_default(".cache", "vivarium-workbench", "venvs")
    assert got == Path(tempfile.gettempdir()) / "vivarium-workbench" / ".cache" / "vivarium-workbench" / "venvs"


# -- materialize() also isolates uv's OWN cache dir from a read-only HOME ----
def test_materialize_sets_uv_cache_dir_under_the_store(tmp_path, monkeypatch):
    """``materialize()`` sets UV_PROJECT_ENVIRONMENT already; it must also set
    UV_CACHE_DIR, or `uv sync` still writes its download cache to the default
    ``~/.cache/uv`` -- a second HOME write this fix would otherwise miss."""
    store = tmp_path / "venv-store"
    monkeypatch.setenv("VIVARIUM_WORKBENCH_VENV_STORE", str(store))

    src = tmp_path / "proj"
    src.mkdir()
    (src / "pyproject.toml").write_text(
        "[project]\nname = 'p'\nversion = '0'\nrequires-python = '>=3.11'\n")
    (src / "uv.lock").write_text("# lock\n")

    seen = {}

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, cwd, env, capture_output, text, timeout):
        seen["env"] = env
        # Fabricate the venv `uv sync` would have produced so materialize()
        # can find the interpreter and complete normally.
        venv_dir = store / m.environment_coordinate(src)
        (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
        py = venv_dir / "bin" / "python"
        py.write_text("#!/bin/sh\n")
        return _FakeCompleted()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    m.materialize(src)

    assert "UV_CACHE_DIR" in seen["env"]
    assert seen["env"]["UV_CACHE_DIR"].startswith(str(store))
    assert seen["env"]["UV_PROJECT_ENVIRONMENT"] == str(store / m.environment_coordinate(src))
