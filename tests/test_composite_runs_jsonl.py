from pathlib import Path
from vivarium_workbench.lib import composite_runs, run_log


def test_save_and_complete_append_jsonl(tmp_path: Path):
    ws = tmp_path
    db = ws / ".pbg" / "composite-runs.db"
    conn = composite_runs.connect(db)
    rid = "spec__1__abc"
    composite_runs.save_metadata(
        conn, spec_id="spec", run_id=rid, params={}, label="baseline",
        started_at=1.0, n_steps=10, workspace=ws, emitter="parquet",
        study_slug="baseline", investigation_slug=None, origin="local")
    composite_runs.complete_metadata(
        conn, run_id=rid, n_steps=10, status="completed", workspace=ws)

    folded = run_log.fold_runs_jsonl(ws)
    rec = folded[rid]
    assert rec["emitter"] == "parquet"
    assert rec["study_slug"] == "baseline"
    assert rec["status"] == "completed"
    assert rec["started_at"] == 1.0 and rec["completed_at"] is not None


def test_save_without_workspace_still_writes_sqlite(tmp_path: Path):
    conn = composite_runs.connect(tmp_path / "x.db")
    composite_runs.save_metadata(
        conn, spec_id="s", run_id="s__1__a", params={}, label="l",
        started_at=1.0, n_steps=1)  # no workspace kwarg
    assert composite_runs.query_run_meta(conn, run_id="s__1__a") is not None
    assert not (tmp_path / run_log.RUN_LOG_RELPATH).exists()
