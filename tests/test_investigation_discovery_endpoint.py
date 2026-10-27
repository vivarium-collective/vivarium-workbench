"""HTTP endpoint test for GET /api/investigation-discovery/{slug}.

The live SPA's Decisions tab fetches this to render the discovery panel without
rendering the whole report. It wraps lib.investigation_report._build_discovery and
always returns a JSON object ({} when the installed viva_superpowers predates the
discovery engine), so the SPA degrades gracefully.
"""
import yaml


def test_investigation_discovery_endpoint_returns_dict(tmp_path, dashboard_client):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    inv = tmp_path / "investigations" / "demo-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({"name": "demo-inv", "studies": []}))
    client = dashboard_client(workspace=tmp_path)
    r = client.get("/api/investigation-discovery/demo-inv")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
