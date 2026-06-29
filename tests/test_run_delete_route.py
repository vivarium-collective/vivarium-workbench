"""Tests for POST /api/run-delete (SP-C task 3)."""


def test_run_delete_route(monkeypatch, tmp_path):
    import time
    from fastapi.testclient import TestClient
    from vivarium_dashboard.api import app as appmod
    from vivarium_dashboard.lib import composite_runs as cr
    monkeypatch.setattr(appmod, "get_workspace", lambda: tmp_path, raising=False)
    db = tmp_path / ".pbg" / "composite-runs.db"; db.parent.mkdir(parents=True)
    conn = cr.connect(db)
    cr.save_metadata(conn, spec_id="s", run_id="s__1__a", params={}, label="L",
                     started_at=time.time(), n_steps=1)
    client = TestClient(appmod.create_app())
    r = client.post("/api/run-delete", json={"run_id": "s__1__a", "db_path": str(db)})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert cr.query_run_meta(cr.connect(db), run_id="s__1__a") is None
