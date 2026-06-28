"""Tests for POST /api/save-run-as-variant (SP-C task 2)."""


def test_save_run_as_variant_route(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from vivarium_dashboard.api import app as appmod
    monkeypatch.setattr(appmod, "get_workspace", lambda: tmp_path, raising=False)
    captured = {}

    def _fake(ws, *, run_id, source_db, study, variant_name):
        captured.update(run_id=run_id, study=study, variant=variant_name)
        return {"study": study, "variant": variant_name, "composite": "c"}, 200

    monkeypatch.setattr(appmod._study_variants, "save_run_as_variant", _fake, raising=False)
    client = TestClient(appmod.create_app())
    r = client.post("/api/save-run-as-variant",
                    json={"run_id": "r1", "study": "demo", "variant_name": "fast"})
    assert r.status_code == 200 and r.json()["variant"] == "fast"
    assert captured["run_id"] == "r1"
