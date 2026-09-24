"""Route-level coverage for the authoring (+ New) endpoints, including that a
created process shows up in a subsequent /api/registry?refresh=1 (the cache
invalidation + warm-worker eviction path)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.fixture()
def ws_copy(tmp_path):
    dst = tmp_path / "ws_increase_demo"
    shutil.copytree(_FIXTURE, dst)
    return dst


def test_scaffold_endpoint(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    r = client.get("/api/registry/scaffold?kind=process&name=Foo")
    body = r.json()
    assert body["ok"] is True
    assert "class Foo(Process)" in body["source"]
    assert body["target"].endswith("processes.py")


def test_validate_endpoint(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    tpl = client.get("/api/registry/scaffold?kind=process&name=Foo").json()
    r = client.post("/api/registry/validate",
                    json={"kind": "process", "name": "Foo", "source": tpl["source"]})
    assert r.json()["valid"] is True


def test_create_then_registry_shows_it(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    tpl = client.get("/api/registry/scaffold?kind=process&name=GrowthProc").json()
    created = client.post("/api/registry/create",
                          json={"kind": "process", "name": "GrowthProc", "source": tpl["source"]}).json()
    assert created["ok"] is True
    assert created["registered"] is True
    # The warm worker was evicted + the registry cache cleared, so a refresh
    # rebuilds and the new class is discoverable over HTTP.
    reg = client.get("/api/registry?refresh=1").json()
    addrs = [p.get("address", "") for p in (reg.get("processes") or [])]
    assert any("GrowthProc" in a for a in addrs)


def test_create_collision_refused(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    tpl = client.get("/api/registry/scaffold?kind=process&name=IncreaseProcess").json()
    r = client.post("/api/registry/create",
                    json={"kind": "process", "name": "IncreaseProcess", "source": tpl["source"]})
    assert r.json()["ok"] is False
