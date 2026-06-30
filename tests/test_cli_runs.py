import time

import pytest

from vivarium_dashboard.lib import cli_runs, composite_runs as cr


def test_run_study_server_dryrun_is_rejected(fixture_study_ws):
    """--dry-run is local-only; combining it with --server must be rejected
    without making any network call (400 returned before _post_server is
    reached, so a non-existent server URL does not raise ConnectionError)."""
    ws, study = fixture_study_ws
    resp, code = cli_runs.run_study(
        ws, study, server="http://localhost:9", dry_run=True
    )
    assert code == 400
    assert "local-only" in resp.get("error", "")


def test_run_study_dry_run_returns_request(fixture_study_ws):
    ws, study = fixture_study_ws
    resp, code = cli_runs.run_study(ws, study, steps=9, dry_run=True)
    assert code == 200 and resp["request"]["steps"] == 9


def test_run_study_param_overrides_layered(fixture_study_ws):
    ws, study = fixture_study_ws
    resp, code = cli_runs.run_study(
        ws, study, params={"seed": 3}, dry_run=True)
    assert resp["request"]["overrides"].get("seed") == 3


def test_find_run_and_list(fixture_study_with_recorded_run):
    ws, study, run_id = fixture_study_with_recorded_run
    db_file, row = cli_runs.find_run(ws, run_id)
    assert db_file and row["run_id"] == run_id
    runs = cli_runs.list_study_runs(ws, study)
    assert any(r["run_id"] == run_id for r in runs)


def test_rerun_replays_recorded_overrides(tmp_path, monkeypatch):
    """rerun() must pass the recorded params as overrides to composite_test_run."""
    import yaml as _yaml

    # Build a minimal workspace with a study.
    ws = tmp_path / "test_ws"
    slug = "demo-study"
    pkg = "pbg_demo"
    composite_id = f"{pkg}.composites.demo"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text(
        _yaml.safe_dump({"name": "demo", "package_path": pkg}),
        encoding="utf-8",
    )
    study_dir = ws / "studies" / slug
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        _yaml.safe_dump({
            "schema_version": 4,
            "name": slug,
            "conditions": {
                "baseline": {"composite": composite_id, "params": {"n_steps": 5}},
            },
        }),
        encoding="utf-8",
    )

    # Seed the runs.db with a recorded run that has params={"seed": 7}.
    spec_id = composite_id
    recorded_params = {"seed": 7}
    db_file = str(study_dir / "runs.db")
    run_id = cr.generate_run_id(spec_id, recorded_params)
    conn = cr.connect(db_file)
    try:
        cr.save_metadata(
            conn,
            spec_id=spec_id,
            run_id=run_id,
            params=recorded_params,
            label="baseline",
            started_at=time.time(),
            n_steps=3,
        )
        cr.complete_metadata(conn, run_id=run_id, n_steps=3, status="complete")
    finally:
        conn.close()

    # Monkeypatch composite_test_run to capture the body and return a fake
    # response. run_composite does `from vivarium_dashboard.lib import
    # composite_test_run_views`, which resolves the module as a package
    # attribute once any earlier test has imported it — so patch the function
    # ON the real module object (not a sys.modules swap, which that attribute
    # binding bypasses).
    from vivarium_dashboard.lib import composite_test_run_views as _ctrv
    captured = {}

    def fake_composite_test_run(ws_root, body):
        captured["body"] = dict(body)
        return {"run_id": "x"}, 202

    monkeypatch.setattr(_ctrv, "composite_test_run", fake_composite_test_run)

    resp, code = cli_runs.rerun(ws, run_id)
    assert code == 202
    assert captured["body"]["overrides"] == {"seed": 7}
    assert captured["body"]["steps"] == 3
