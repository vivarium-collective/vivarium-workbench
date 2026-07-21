import json
from pathlib import Path
from vivarium_workbench.lib import run_log


def test_append_then_fold_merges_events(tmp_path: Path):
    ws = tmp_path
    run_log.append_run_event(ws, {
        "run_id": "spec__1__abc", "event": "started",
        "spec_id": "spec", "started_at": 1.0, "status": "running",
        "emitter": "parquet", "study_slug": "baseline",
    })
    run_log.append_run_event(ws, {
        "run_id": "spec__1__abc", "event": "completed",
        "completed_at": 2.0, "status": "completed", "n_steps": 10,
    })
    folded = run_log.fold_runs_jsonl(ws)
    rec = folded["spec__1__abc"]
    assert rec["status"] == "completed"
    assert rec["emitter"] == "parquet"       # carried from 'started'
    assert rec["started_at"] == 1.0
    assert rec["completed_at"] == 2.0
    assert rec["n_steps"] == 10
    assert "ts" in rec                        # auto-stamped


def test_append_is_line_delimited(tmp_path: Path):
    run_log.append_run_event(tmp_path, {"run_id": "a", "event": "started"})
    run_log.append_run_event(tmp_path, {"run_id": "b", "event": "started"})
    text = (tmp_path / run_log.RUN_LOG_RELPATH).read_text()
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "a"


def test_fold_missing_log_returns_empty(tmp_path: Path):
    assert run_log.fold_runs_jsonl(tmp_path) == {}
