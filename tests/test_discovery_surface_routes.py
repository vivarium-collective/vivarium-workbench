"""Routes surfacing discovery_delta and the evidence-integrity ledger."""
import pytest
import yaml


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    inv = tmp_path / "investigations" / "demo-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({"name": "demo-inv", "studies": []}))


def test_discovery_delta_route(tmp_path, inproc_client):
    pytest.importorskip("discovery_engine")
    _ws(tmp_path)
    r = inproc_client(workspace=tmp_path).get("/api/discovery-delta/demo-inv")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    for k in ("added_or_qualified", "gated", "regressions", "proposals", "changed", "unchanged"):
        assert k in d
    assert d["unchanged"] is True  # no prior snapshot -> graceful


def test_evidence_integrity_route(tmp_path, inproc_client):
    pytest.importorskip("discovery_engine")
    _ws(tmp_path)
    r = inproc_client(workspace=tmp_path).get("/api/evidence-integrity/demo-inv")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert isinstance(d["ledger"], list) and "summary" in d and "by_study" in d
