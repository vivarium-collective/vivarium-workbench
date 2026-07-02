"""Tests for user-facing CLI run subcommands: run study|investigation|composite,
rerun, runs, status, logs."""
import json
from vivarium_workbench.cli import main


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


def test_run_study_seed_becomes_param(fixture_study_ws, capsys):
    """--seed N is forwarded as overrides.seed in the dry-run request."""
    ws, study = fixture_study_ws
    rc = main(["run", "study", study, "--workspace", str(ws),
               "--seed", "5", "--dry-run", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["request"]["overrides"]["seed"] == 5


def test_logs_follow_terminal_run_returns_promptly(fixture_study_with_recorded_run, capsys, tmp_path):
    """--follow on an already-terminal run prints the log and returns without polling."""
    ws, study, run_id = fixture_study_with_recorded_run
    # Write a small log file so the logs command can find it.
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    wp = WorkspacePaths.load(ws)
    study_dir = wp.study_dir(study)
    log_path = study_dir / f"{run_id}.log"
    log_path.write_text("hello from log\n", encoding="utf-8")

    # Patch log_path into the run row.
    db_file = str(study_dir / "runs.db")
    conn = cr.connect(db_file)
    try:
        conn.execute("UPDATE runs_meta SET log_path=? WHERE run_id=?",
                     (str(log_path.relative_to(ws)), run_id))
        conn.commit()
    finally:
        conn.close()

    rc = main(["logs", run_id, "--workspace", str(ws), "--follow"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "hello from log" in out
