"""HTTP endpoint test for GET /api/investigation-discovery/{slug}.

The live SPA's Decisions tab fetches this to render the discovery panel without
rendering the whole report. It wraps lib.investigation_report._build_discovery and
always returns a JSON object ({} when the installed viva_superpowers predates the
discovery engine), so the SPA degrades gracefully.
"""
import yaml


def test_investigation_discovery_endpoint_returns_dict(tmp_path, inproc_client):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    inv = tmp_path / "investigations" / "demo-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({"name": "demo-inv", "studies": []}))
    client = inproc_client(workspace=tmp_path)
    r = client.get("/api/investigation-discovery/demo-inv")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, dict)
    # When the discovery engine is installed, the panel/report need a blocking count
    # so a CLOSED investigation can be labelled "closed — N residual (non-blocking)"
    # rather than imply its open items are unfinished. closed ⟹ 0 blocking.
    if "closed" in payload:
        assert isinstance(payload.get("closure_blocking"), int)
        assert payload["closure_blocking"] >= 0
        if payload["closed"]:
            assert payload["closure_blocking"] == 0
