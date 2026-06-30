from vivarium_dashboard.lib import cli_runs


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
