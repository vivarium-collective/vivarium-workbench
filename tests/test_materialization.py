"""Managed environment materialization (materialization-lifecycle.md §9b): the
coordinate-keyed venv store + synchronous ``uv sync`` primitive.

The store is redirected to a tmp dir via ``VIVARIUM_WORKBENCH_VENV_STORE`` so no
test touches a real user cache. The offline tests fabricate a venv layout to
exercise the cache/coordinate logic without running ``uv``; one opt-in test runs
a real ``uv sync`` of a minimal no-dep project (skipped when ``uv`` is absent).
"""
import subprocess
import sys
from pathlib import Path

import pytest

from vivarium_workbench.lib import env_resolver, materialization as m


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Redirect the venv store to a tmp dir."""
    d = tmp_path / "venv-store"
    monkeypatch.setenv("VIVARIUM_WORKBENCH_VENV_STORE", str(d))
    return d


def _make_project(root: Path, *, lock: str = "lock-a") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'p'\nversion = '0'\nrequires-python = '>=3.11'\n")
    (root / "uv.lock").write_text(f"# {lock}\n")
    return root


def _fake_venv(venv_dir: Path, *, complete: bool = True) -> Path:
    """A minimal ``<venv>/bin/python``; ``complete`` also writes the completion
    marker so ``cached_interpreter`` treats it as a finished (cacheable) venv."""
    b = venv_dir / "bin"
    b.mkdir(parents=True, exist_ok=True)
    py = b / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    if complete:
        (venv_dir / m._MARKER).write_text("ok\n")
    return py


# -- coordinate --------------------------------------------------------------
def test_coordinate_is_deterministic(tmp_path):
    src = _make_project(tmp_path / "ws")
    assert m.environment_coordinate(src) == m.environment_coordinate(src)


def test_coordinate_changes_with_lock_content(tmp_path):
    a = _make_project(tmp_path / "a", lock="lock-a")
    b = _make_project(tmp_path / "b", lock="lock-b")
    assert m.environment_coordinate(a) != m.environment_coordinate(b)


def test_coordinate_is_source_scoped(tmp_path):
    """Same lock content, different source paths → different coordinates (no false
    venv sharing between checkouts with editable path-dep locks)."""
    a = _make_project(tmp_path / "a", lock="same")
    b = _make_project(tmp_path / "b", lock="same")
    assert m.environment_coordinate(a) != m.environment_coordinate(b)


# -- cache lookups -----------------------------------------------------------
def test_cached_interpreter_finds_a_present_venv(store, tmp_path):
    coord = "abc123"
    py = _fake_venv(store / coord)
    assert m.cached_interpreter(coord) == str(py)


def test_cached_interpreter_absent_is_none(store):
    assert m.cached_interpreter("nope") is None


def test_partial_venv_without_marker_is_not_cached(store, tmp_path):
    """An interrupted `uv sync` leaves a bin/python but no completion marker — it
    must NOT be treated as cached (it would be a broken interpreter)."""
    coord = "partial123"
    _fake_venv(store / coord, complete=False)
    assert m.cached_interpreter(coord) is None            # re-sync, don't serve it


def test_cached_interpreter_for_none_without_pyproject(store, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert m.cached_interpreter_for(bare) is None


def test_cached_interpreter_for_hits_the_store(store, tmp_path):
    src = _make_project(tmp_path / "ws")
    _fake_venv(store / m.environment_coordinate(src))
    got = m.cached_interpreter_for(src)
    assert got is not None and got.endswith("/bin/python")


# -- materialize (offline paths) ---------------------------------------------
def test_materialize_no_pyproject_is_sys_executable(store, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert m.materialize(bare) == sys.executable


def test_materialize_cache_hit_skips_uv(store, tmp_path, monkeypatch):
    src = _make_project(tmp_path / "ws")
    _fake_venv(store / m.environment_coordinate(src))

    def _boom(*a, **k):  # uv must NOT run on a cache hit
        raise AssertionError("uv sync should not run on a cache hit")

    monkeypatch.setattr(subprocess, "run", _boom)
    got = m.materialize(src)
    assert got.endswith("/bin/python")


# -- resolver integration (behavior-preserving) ------------------------------
def test_resolver_prefers_in_place_venv(tmp_path):
    ws = _make_project(tmp_path / "ws")
    inplace = _fake_venv(ws / ".venv")
    assert env_resolver.resolve_interpreter(ws) == str(inplace)


def test_resolver_falls_back_to_sys_executable(store, tmp_path):
    """No in-place .venv and no managed venv → running interpreter (today's
    behavior)."""
    ws = _make_project(tmp_path / "ws")
    assert env_resolver.resolve_interpreter(ws) == sys.executable


def test_resolver_uses_managed_venv_when_present(store, tmp_path):
    ws = _make_project(tmp_path / "ws")  # no in-place .venv
    managed = _fake_venv(store / m.environment_coordinate(ws))
    assert env_resolver.resolve_interpreter(ws) == str(managed)


# -- real uv sync (opt-in: needs uv; a tiny no-dep project, ~1s) --------------
@pytest.mark.skipif(not __import__("shutil").which("uv"), reason="uv not on PATH")
def test_materialize_runs_real_uv_sync(store, tmp_path, monkeypatch):
    """End-to-end: materialize a minimal project → a real venv with an
    interpreter; a second call is a cache hit (same path)."""
    # Pin uv to the running interpreter so it need not download a managed CPython
    # (materialize inherits os.environ). No deps → the sync is ~1s and offline.
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    src = tmp_path / "proj"
    src.mkdir()
    (src / "pyproject.toml").write_text(
        "[project]\nname = 'tinyproj'\nversion = '0'\nrequires-python = '>=3.11'\n")
    # A managed workspace ships a uv.lock; generate one so the coordinate is
    # lock-stable across materialize calls (idempotence).
    lock = subprocess.run(["uv", "lock"], cwd=str(src), capture_output=True, text=True)
    if lock.returncode != 0:
        pytest.skip(f"uv lock unavailable: {lock.stderr[:200]}")
    assert m.cached_interpreter_for(src) is None          # nothing yet
    try:
        interp = m.materialize(src, timeout=180)
    except m.MaterializationError as e:
        pytest.skip(f"uv sync unavailable in this env: {e} {e.tail[:200]}")
    assert Path(interp).is_file()
    assert str(store) in interp                           # built into the store
    assert m.cached_interpreter_for(src) == interp        # now a cache hit
    assert m.materialize(src, timeout=180) == interp      # reuses, no rebuild
