"""Tests for the /api/catalog probe batching + caching (perf/catalog-load).

build_catalog used to spawn one Python subprocess per installed module for the
importability ("out of sync") check, and re-ran the bulk venv-distribution probe
on every call. These tests cover: the batched single-probe importability check,
the TTL memoization of both probes, and invalidation via clear_catalog_probe_cache.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import catalog as _catalog
from vivarium_workbench.lib import workspace_deps_views
from vivarium_workbench.lib.catalog import build_catalog


@pytest.fixture(autouse=True)
def _clear_caches():
    _catalog.clear_catalog_probe_cache()
    yield
    _catalog.clear_catalog_probe_cache()


def _fake_venv(tmp_path: Path) -> None:
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin" / "python3").write_text("")


# --------------------------------------------------------------------------
# _batch_import_check: one subprocess for many names, cached, invalidatable
# --------------------------------------------------------------------------

def test_batch_import_check_single_subprocess_and_caches(tmp_path, monkeypatch):
    _fake_venv(tmp_path)
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        # cmd = [python3, "-c", probe, json.dumps(names)] — echo one failure.
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(["badpkg"]), stderr="")

    monkeypatch.setattr(_catalog.subprocess, "run", fake_run)

    failed = _catalog._batch_import_check(tmp_path, ["goodpkg", "badpkg", "another"])
    assert failed == {"badpkg"}
    assert calls["n"] == 1  # ONE subprocess for all three names

    # Second call with the same name set is served from cache — no new subprocess.
    again = _catalog._batch_import_check(tmp_path, ["goodpkg", "badpkg", "another"])
    assert again == {"badpkg"}
    assert calls["n"] == 1

    # Invalidation forces a re-probe.
    _catalog.clear_catalog_probe_cache()
    _catalog._batch_import_check(tmp_path, ["goodpkg", "badpkg", "another"])
    assert calls["n"] == 2


def test_batch_import_check_no_venv_returns_empty(tmp_path, monkeypatch):
    # No .venv → no probe, degrade to "everything in sync" (empty failure set).
    ran = {"n": 0}
    monkeypatch.setattr(_catalog.subprocess, "run",
                        lambda *a, **k: ran.update(n=ran["n"] + 1))
    assert _catalog._batch_import_check(tmp_path, ["x"]) == set()
    assert ran["n"] == 0


def test_batch_import_check_empty_names_noop(tmp_path):
    assert _catalog._batch_import_check(tmp_path, []) == set()


def test_sync_reason_maps_failure_set():
    failures = {"foo"}
    assert _catalog._sync_reason("foo", failures)  # truthy reason string
    assert _catalog._sync_reason("Foo", failures)  # case-insensitive
    assert _catalog._sync_reason("bar", failures) is None
    assert _catalog._sync_reason(None, failures) is None


# --------------------------------------------------------------------------
# _detect_workspace_venv_distributions: cached across calls, invalidatable
# --------------------------------------------------------------------------

def test_venv_dist_probe_cached_and_invalidated(tmp_path, monkeypatch):
    _fake_venv(tmp_path)
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"foo": {"version": "1", "requires": []}}), stderr="")

    monkeypatch.setattr(_catalog.subprocess, "run", fake_run)

    d1 = _catalog._detect_workspace_venv_distributions(tmp_path)
    d2 = _catalog._detect_workspace_venv_distributions(tmp_path)
    assert "foo" in d1 and d1 == d2
    assert calls["n"] == 1  # second call served from cache

    _catalog.clear_catalog_probe_cache()
    _catalog._detect_workspace_venv_distributions(tmp_path)
    assert calls["n"] == 2


def test_clear_catalog_probe_cache_clears_both():
    _catalog._VENV_DIST_CACHE["x"] = {"data": {}, "ts": 0}
    _catalog._IMPORT_CHECK_CACHE["x"] = {"key": (), "failed": set(), "ts": 0}
    _catalog.clear_catalog_probe_cache()
    assert _catalog._VENV_DIST_CACHE == {} and _catalog._IMPORT_CHECK_CACHE == {}


# --------------------------------------------------------------------------
# build_catalog: batches the import probe ONCE, surfaces out_of_sync from it
# --------------------------------------------------------------------------

def test_build_catalog_probes_once_and_flags_out_of_sync(tmp_path, monkeypatch):
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump({
        "name": "ws",
        "imports": {
            "foo": {"source": "https://x/foo.git", "ref": "main", "mode": "pypi", "package": "foo"},
            "bar": {"source": "https://x/bar.git", "ref": "main", "mode": "pypi", "package": "bar"},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(workspace_deps_views, "module_registry",
                        lambda ws: [{"name": "foo", "package": "foo"},
                                    {"name": "bar", "package": "bar"}])
    monkeypatch.setattr("viva_superpowers.catalog.load_registry",
                        lambda _ws: [{"name": "foo", "package": "foo"},
                                     {"name": "bar", "package": "bar"}])
    monkeypatch.setattr(_catalog, "_detect_workspace_venv_distributions", lambda _w: {})

    probe_calls = {"n": 0}

    def fake_batch(ws, pkgs):
        probe_calls["n"] += 1
        # Assert both module packages are probed together in ONE call.
        assert {"foo", "bar"} <= set(pkgs)
        return {"foo"}  # foo fails to import → out_of_sync

    monkeypatch.setattr(_catalog, "_batch_import_check", fake_batch)

    modules = build_catalog(tmp_path)["modules"]
    assert probe_calls["n"] == 1  # ONE batched probe for the whole catalog

    by_name = {m["name"]: m for m in modules}
    assert by_name["foo"].get("out_of_sync") is True
    assert "out_of_sync" not in by_name["bar"] or by_name["bar"].get("out_of_sync") is not True
