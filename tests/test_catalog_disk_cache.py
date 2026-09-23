"""Tests for the persistent on-disk catalog cache (``lib/catalog_disk_cache.py``)
and its wiring into ``lib.registry.build_registry`` /
``lib.composites_query.composites_via_subprocess``.

The Registry and Composites panels both already had a 30s in-memory TTL cache
in front of the env-worker call that imports every installed process package
(multi-minute on some workspaces) — but that only survives within one
worker/pod's lifetime. This module adds a disk-backed layer underneath, keyed
by a signature of "what could change the catalog", so a cold worker/pod with
an unchanged venv skips the import walk entirely instead of just the 30s
window.

Each test builds a throwaway workspace under ``tmp_path`` (just a
``workspace.yaml`` — no real process package needed since the pool call is
always monkeypatched here) so the disk cache's own ``.pbg/registry-catalog/``
writes never touch a real fixture workspace.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from vivarium_workbench.lib import catalog_disk_cache as cdc
from vivarium_workbench.lib import composites_query
from vivarium_workbench.lib import registry


def _make_workspace(tmp_path: Path, name: str = "cache_test_ws") -> Path:
    ws = tmp_path / name
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\n"
        f"name: {name}\n"
        f"package_path: viva_{name}\n"
        "runtime:\n"
        "  default_emitter: sqlite\n"
        "phases: []\n"
        "observables: []\n"
        "visualizations: []\n"
        "simulations: []\n"
        "datasets: []\n",
        encoding="utf-8",
    )
    return ws


# ---------------------------------------------------------------------------
# (a) signature: stable / changes on workspace.yaml mtime
# ---------------------------------------------------------------------------

def test_signature_stable_across_calls_with_unchanged_workspace(tmp_path):
    ws = _make_workspace(tmp_path)
    sig1 = cdc.catalog_signature(ws)
    sig2 = cdc.catalog_signature(ws)
    assert sig1 == sig2
    assert sig1 != cdc.DISABLED
    assert len(sig1) == 40  # sha1 hexdigest


def test_signature_changes_when_workspace_yaml_content_changes(tmp_path):
    # The signature is keyed on workspace.yaml CONTENT, not mtime (so a baked
    # cache survives a seed-copy — see test_signature_is_copy_stable_across_mtime
    # _changes). A real content change must still invalidate it.
    ws = _make_workspace(tmp_path)
    sig1 = cdc.catalog_signature(ws)

    wy = ws / "workspace.yaml"
    wy.write_text(wy.read_text(encoding="utf-8") + "\n# a real edit\n", encoding="utf-8")

    sig2 = cdc.catalog_signature(ws)
    assert sig1 != sig2


# ---------------------------------------------------------------------------
# (b) store -> load round-trips a catalog dict
# ---------------------------------------------------------------------------

def test_store_then_load_round_trips_a_catalog_dict(tmp_path):
    ws = _make_workspace(tmp_path)
    sig = cdc.catalog_signature(ws)
    payload = {"processes": [{"name": "Foo", "address": "viva_x.processes.Foo"}], "types": []}

    assert cdc.load(ws, "registry", sig) is None  # nothing cached yet
    cdc.store(ws, "registry", sig, payload)
    assert cdc.load(ws, "registry", sig) == payload


def test_store_load_is_kind_scoped(tmp_path):
    """registry and composites cache entries for the SAME signature don't collide."""
    ws = _make_workspace(tmp_path)
    sig = cdc.catalog_signature(ws)
    reg_payload = {"processes": [], "types": []}
    comp_payload = {"composites": [{"id": "x"}]}

    cdc.store(ws, "registry", sig, reg_payload)
    cdc.store(ws, "composites", sig, comp_payload)

    assert cdc.load(ws, "registry", sig) == reg_payload
    assert cdc.load(ws, "composites", sig) == comp_payload


# ---------------------------------------------------------------------------
# (d) clear removes the files
# ---------------------------------------------------------------------------

def test_clear_removes_every_cached_kind(tmp_path):
    ws = _make_workspace(tmp_path)
    sig = cdc.catalog_signature(ws)
    cdc.store(ws, "registry", sig, {"processes": [], "types": []})
    cdc.store(ws, "composites", sig, {"composites": []})

    d = cdc.cache_dir(ws)
    assert list(d.glob("registry-*.json"))
    assert list(d.glob("composites-*.json"))

    cdc.clear(ws)

    assert not list(d.glob("registry-*.json"))
    assert not list(d.glob("composites-*.json"))
    # Never raises when there is nothing to clear.
    cdc.clear(ws)


# ---------------------------------------------------------------------------
# (e) a disabled/corrupt signature disables caching entirely
# ---------------------------------------------------------------------------

def test_disabled_signature_is_a_noop_for_load_and_store(tmp_path):
    ws = _make_workspace(tmp_path)
    cdc.store(ws, "registry", cdc.DISABLED, {"processes": [], "types": []})
    assert cdc.load(ws, "registry", cdc.DISABLED) is None

    d = cdc.cache_dir(ws)
    assert not list(d.glob("registry-*.json"))


def test_empty_signature_is_also_a_noop(tmp_path):
    ws = _make_workspace(tmp_path)
    cdc.store(ws, "registry", "", {"processes": [], "types": []})
    assert cdc.load(ws, "registry", "") is None


def test_unknown_kind_is_rejected(tmp_path):
    ws = _make_workspace(tmp_path)
    sig = cdc.catalog_signature(ws)
    cdc.store(ws, "bogus", sig, {"x": 1})
    assert cdc.load(ws, "bogus", sig) is None


# ---------------------------------------------------------------------------
# (c) build_registry: disk-cache HIT never calls the env-worker pool; a MISS
# calls it once and writes the file. Also: the persisted payload survives an
# in-memory cache clear (the "worker/pod restart" scenario this exists for).
# ---------------------------------------------------------------------------

class _ExplodingPool:
    """A pool whose ``.call`` always raises — used to prove a code path never
    reaches the env worker."""

    def call(self, *args, **kwargs):
        raise AssertionError("env-worker pool must not be called here")


def test_build_registry_disk_cache_hit_skips_the_env_worker(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    registry._REGISTRY_CACHE.clear()
    sig = cdc.catalog_signature(ws)
    raw_payload = {
        "processes": [
            {"name": "Foo", "address": "viva_x.processes.Foo", "kind": "process"},
        ],
        "types": [],
    }
    cdc.store(ws, "registry", sig, raw_payload)

    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_pool.get_pool", lambda: _ExplodingPool()
    )

    data = registry.build_registry(ws)

    assert data.get("error") is None, data.get("error")
    names = [p["name"] for p in data["processes"]]
    assert "Foo" in names
    # The annotate steps still ran on the cached data (run on load, cheap).
    foo = next(p for p in data["processes"] if p["name"] == "Foo")
    assert "run_command" in foo
    assert "use_count" in foo


def test_build_registry_disk_cache_miss_calls_pool_once_and_writes(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    registry._REGISTRY_CACHE.clear()
    sig = cdc.catalog_signature(ws)
    assert cdc.load(ws, "registry", sig) is None

    calls: list[str] = []
    raw_payload = {
        "processes": [{"name": "Bar", "address": "viva_x.processes.Bar", "kind": "process"}],
        "types": [],
    }

    class _FakePool:
        def call(self, ws_root, method, *args, **kwargs):
            calls.append(method)
            assert method == "registry_catalog"
            return raw_payload

    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_pool.get_pool", lambda: _FakePool()
    )

    data = registry.build_registry(ws)

    assert calls == ["registry_catalog"]
    assert data.get("error") is None, data.get("error")
    assert any(p["name"] == "Bar" for p in data["processes"])

    on_disk = cdc.load(ws, "registry", sig)
    assert on_disk is not None
    assert on_disk["processes"][0]["name"] == "Bar"
    # Stored RAW (pre-annotation): no run_command baked in on disk.
    assert "run_command" not in on_disk["processes"][0]


def test_build_registry_survives_an_in_memory_cache_clear(tmp_path, monkeypatch):
    """The scenario this exists for: an in-memory TTL cache clear (worker/pod
    restart) must be served from disk, not re-pay the pool call."""
    ws = _make_workspace(tmp_path)
    registry._REGISTRY_CACHE.clear()

    calls: list[str] = []
    raw_payload = {
        "processes": [{"name": "Baz", "address": "viva_x.processes.Baz", "kind": "process"}],
        "types": [],
    }

    class _FakePool:
        def call(self, ws_root, method, *args, **kwargs):
            calls.append(method)
            return raw_payload

    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_pool.get_pool", lambda: _FakePool()
    )
    registry.build_registry(ws)
    assert calls == ["registry_catalog"]

    # Simulate the "cold pod" moment: in-memory TTL cache gone, disk persists.
    registry._REGISTRY_CACHE.clear()
    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_pool.get_pool", lambda: _ExplodingPool()
    )

    data = registry.build_registry(ws)
    assert data.get("error") is None, data.get("error")
    assert any(p["name"] == "Baz" for p in data["processes"])


def test_build_registry_disabled_signature_never_caches(tmp_path, monkeypatch):
    """A corrupt/empty signature must disable caching outright: the pool is
    called on every request and nothing is ever written to disk."""
    ws = _make_workspace(tmp_path)
    registry._REGISTRY_CACHE.clear()
    monkeypatch.setattr(cdc, "catalog_signature", lambda ws_root: cdc.DISABLED)

    calls: list[str] = []
    raw_payload = {
        "processes": [{"name": "Qux", "address": "viva_x.processes.Qux", "kind": "process"}],
        "types": [],
    }

    class _FakePool:
        def call(self, ws_root, method, *args, **kwargs):
            calls.append(method)
            return raw_payload

    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_pool.get_pool", lambda: _FakePool()
    )

    registry.build_registry(ws)
    registry._REGISTRY_CACHE.clear()
    registry.build_registry(ws)

    assert calls == ["registry_catalog", "registry_catalog"], (
        "disabled signature must never be served from (or written to) disk"
    )
    assert not list(cdc.cache_dir(ws).glob("registry-*.json"))


# ---------------------------------------------------------------------------
# composites_via_subprocess: same disk-cache wiring, mirrored.
# ---------------------------------------------------------------------------

def test_composites_disk_cache_hit_skips_pool_and_subprocess(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    composites_query._COMPOSITES_CACHE.clear()
    sig = cdc.catalog_signature(ws)
    payload = {"composites": [{"id": "viva_x.composites.demo"}]}
    cdc.store(ws, "composites", sig, payload)

    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_pool.get_pool", lambda: _ExplodingPool()
    )

    def _boom_run(*args, **kwargs):
        raise AssertionError("subprocess must not run on a composites disk-cache hit")

    monkeypatch.setattr(composites_query.subprocess, "run", _boom_run)

    data = composites_query.composites_via_subprocess(ws)
    assert data == payload


def test_composites_disk_cache_miss_calls_pool_and_writes(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    composites_query._COMPOSITES_CACHE.clear()
    sig = cdc.catalog_signature(ws)
    assert cdc.load(ws, "composites", sig) is None

    payload = {"composites": [{"id": "viva_x.composites.demo"}]}

    class _FakePool:
        def call(self, ws_root, method, *args, **kwargs):
            assert method == "composites_full"
            return payload

    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_pool.get_pool", lambda: _FakePool()
    )

    data = composites_query.composites_via_subprocess(ws)
    assert data == payload
    assert cdc.load(ws, "composites", sig) == payload


# ---- copy-stable signature (image-baked cache survives a seed-copy) ---------

def _mk_ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text(
        "name: ws\npackage_path: pbg_x\nimports: {}\n", encoding="utf-8")
    (tmp_path / "pbg_x").mkdir()
    (tmp_path / "pbg_x" / "__init__.py").write_text("# pkg\n", encoding="utf-8")
    return tmp_path


def test_signature_is_copy_stable_across_mtime_changes(tmp_path):
    # A pre-baked cache is keyed on the seed workspace; the entrypoint copies the
    # seed into the user's pod, which changes file mtimes but NOT content. The
    # signature must be content-based so the baked cache still HITS (Fix 0).
    import os
    from vivarium_workbench.lib import catalog_disk_cache as c
    ws = _mk_ws(tmp_path)
    before = c.catalog_signature(ws)
    assert before != c.DISABLED
    os.utime(ws / "workspace.yaml", None)
    os.utime(ws / "pbg_x" / "__init__.py", None)
    assert c.catalog_signature(ws) == before


def test_signature_still_changes_on_real_content_change(tmp_path):
    from vivarium_workbench.lib import catalog_disk_cache as c
    ws = _mk_ws(tmp_path)
    before = c.catalog_signature(ws)
    (ws / "pbg_x" / "__init__.py").write_text("# pkg CHANGED\n", encoding="utf-8")
    assert c.catalog_signature(ws) != before
    after_pkg = c.catalog_signature(ws)
    (ws / "workspace.yaml").write_text(
        "name: ws\npackage_path: pbg_x\nimports: {foo: {}}\n", encoding="utf-8")
    assert c.catalog_signature(ws) != after_pkg
