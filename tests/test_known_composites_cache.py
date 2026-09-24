"""Tests for the known_composite_ids memoization (perf/runs-load).

/api/simulations annotates each run with whether it maps to a registered
composite, calling known_composite_ids on EVERY request — and the Runs tab
re-hits it on a 15s poll. The underlying discovery runs FS + federation scans
AND an env_worker discover_composites call, so it's memoized per workspace with
a TTL, invalidated on catalog change.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vivarium_workbench.lib import active_workspace as _aw
from vivarium_workbench.lib import composite_lookup as _cl


@pytest.fixture(autouse=True)
def _clear():
    _cl.clear_known_composites_cache()
    yield
    _cl.clear_known_composites_cache()


def _counter(monkeypatch):
    calls = {"n": 0}

    def fake_discover(ws_root, package_path):
        calls["n"] += 1
        return {"pkg.composites.a": {"id": "pkg.composites.a"},
                "pkg.composites.b": {"id": "pkg.composites.b"}}

    monkeypatch.setattr(_cl, "discover_all_composites", fake_discover)
    return calls


def test_known_composite_ids_result_and_caches(tmp_path, monkeypatch):
    calls = _counter(monkeypatch)

    ids1 = _cl.known_composite_ids(tmp_path, "pkg")
    assert ids1 == {"pkg.composites.a", "pkg.composites.b"}
    assert calls["n"] == 1

    ids2 = _cl.known_composite_ids(tmp_path, "pkg")
    assert ids2 == ids1
    assert calls["n"] == 1  # served from cache — no second discovery


def test_returns_a_copy_not_the_cached_set(tmp_path, monkeypatch):
    _counter(monkeypatch)
    ids = _cl.known_composite_ids(tmp_path, "pkg")
    ids.add("caller-mutation")
    # A caller mutating the returned set must not corrupt the cache.
    ids_again = _cl.known_composite_ids(tmp_path, "pkg")
    assert "caller-mutation" not in ids_again


def test_clear_forces_recompute(tmp_path, monkeypatch):
    calls = _counter(monkeypatch)
    _cl.known_composite_ids(tmp_path, "pkg")
    _cl.clear_known_composites_cache()
    _cl.known_composite_ids(tmp_path, "pkg")
    assert calls["n"] == 2


def test_active_workspace_invalidate_clears(tmp_path, monkeypatch):
    calls = _counter(monkeypatch)
    _cl.known_composite_ids(tmp_path, "pkg")
    _aw.invalidate()  # fired by catalog install/uninstall
    _cl.known_composite_ids(tmp_path, "pkg")
    assert calls["n"] == 2


def test_ttl_zero_disables_cache(tmp_path, monkeypatch):
    calls = _counter(monkeypatch)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_KNOWN_COMPOSITES_TTL", "0")
    _cl.known_composite_ids(tmp_path, "pkg")
    _cl.known_composite_ids(tmp_path, "pkg")
    assert calls["n"] == 2  # TTL 0 → never served from cache


def test_distinct_workspaces_keyed_separately(tmp_path, monkeypatch):
    calls = _counter(monkeypatch)
    _cl.known_composite_ids(tmp_path / "a", "pkg")
    _cl.known_composite_ids(tmp_path / "b", "pkg")
    assert calls["n"] == 2  # different ws_root → separate cache entries
    _cl.known_composite_ids(tmp_path / "a", "pkg")
    assert calls["n"] == 2  # first workspace still cached
