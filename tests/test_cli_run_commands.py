"""Tests for user-facing CLI run subcommands: run study|investigation|composite,
rerun, runs, status, logs."""
import json
from vivarium_dashboard.cli import main


def test_run_study_dry_run_prints_request(fixture_study_ws, capsys):
    ws, study = fixture_study_ws
    rc = main(["run", "study", study, "--workspace", str(ws),
               "--steps", "8", "--dry-run", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["request"]["steps"] == 8


def test_runs_list_json(fixture_study_with_recorded_run, capsys):
    ws, study, run_id = fixture_study_with_recorded_run
    rc = main(["runs", study, "--workspace", str(ws), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and any(r["run_id"] == run_id for r in out)
