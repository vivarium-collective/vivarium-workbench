"""Tests for POST /api/run-delete (SP-C task 3)."""


def test_run_delete_route(monkeypatch, tmp_path):
    import time
    from fastapi.testclient import TestClient
    from vivarium_workbench.api import app as appmod
    from vivarium_workbench.lib import composite_runs as cr
    monkeypatch.setattr(appmod, "get_workspace", lambda: tmp_path, raising=False)
    db = tmp_path / ".pbg" / "composite-runs.db"; db.parent.mkdir(parents=True)
    conn = cr.connect(db)
    cr.save_metadata(conn, spec_id="s", run_id="s__1__a", params={}, label="L",
                     started_at=time.time(), n_steps=1)
    client = TestClient(appmod.create_app())
    r = client.post("/api/run-delete", json={"run_id": "s__1__a", "db_path": str(db)})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert cr.query_run_meta(cr.connect(db), run_id="s__1__a") is None


def test_run_delete_route_is_comprehensive_and_does_not_reappear(monkeypatch, tmp_path):
    """The Composite Explorer's Delete button (POST /api/run-delete) must FULLY
    remove a run — history, run dir, and a JSONL tombstone — not just the
    runs_meta row. It previously called composite_runs.delete_run (runs_meta +
    dir only), so after #554's pure-JSONL fold the run re-synthesised from its
    surviving `started` event and came back as an undeletable phantom.
    """
    import time
    from fastapi.testclient import TestClient
    from vivarium_workbench.api import app as appmod
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.simulations_index import list_simulations

    monkeypatch.setattr(appmod, "get_workspace", lambda: tmp_path, raising=False)
    db = tmp_path / ".pbg" / "composite-runs.db"; db.parent.mkdir(parents=True)
    conn = cr.connect(db)
    cr.save_metadata(conn, spec_id="s", run_id="s__2__b", params={}, label="L",
                     started_at=time.time(), n_steps=1)
    conn.execute("CREATE TABLE IF NOT EXISTS history "
                 "(simulation_id TEXT, step INTEGER, global_time REAL, state TEXT)")
    conn.execute("INSERT INTO history VALUES ('s__2__b', 0, 0.0, '{}')")
    conn.commit()
    run_dir = tmp_path / ".pbg" / "runs" / "s__2__b"
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text("{}")

    assert any(r.get("run_id") == "s__2__b" for r in list_simulations(tmp_path))

    client = TestClient(appmod.create_app())
    r = client.post("/api/run-delete", json={"run_id": "s__2__b"})
    assert r.status_code == 200 and r.json()["deleted"] is True

    # Fully gone: meta row, history rows, run dir — and it stays gone across a
    # fresh Sim-DB fold (the tombstone did its job).
    assert cr.query_run_meta(cr.connect(db), run_id="s__2__b") is None
    assert cr.connect(db).execute(
        "SELECT count(*) FROM history WHERE simulation_id='s__2__b'"
    ).fetchone()[0] == 0
    assert not run_dir.exists()
    assert not any(r.get("run_id") == "s__2__b" for r in list_simulations(tmp_path))


def test_run_delete_route_unknown_run_404(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from vivarium_workbench.api import app as appmod
    monkeypatch.setattr(appmod, "get_workspace", lambda: tmp_path, raising=False)
    (tmp_path / ".pbg").mkdir(parents=True)
    client = TestClient(appmod.create_app())
    r = client.post("/api/run-delete", json={"run_id": "no-such-run"})
    assert r.status_code == 404
