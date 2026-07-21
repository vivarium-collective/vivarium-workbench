from pathlib import Path
from vivarium_workbench.lib import run_log, simulations_index


def test_jsonl_run_appears_with_emitter_and_time(tmp_path: Path):
    ws = tmp_path
    (ws / "studies").mkdir(parents=True, exist_ok=True)
    run_log.append_run_event(ws, {
        "run_id": "v2ecoli.composites.baseline__9__ff", "event": "started",
        "spec_id": "v2ecoli.composites.baseline", "started_at": 100.0,
        "status": "running", "emitter": "parquet", "study_slug": "baseline",
    })
    run_log.append_run_event(ws, {
        "run_id": "v2ecoli.composites.baseline__9__ff", "event": "completed",
        "completed_at": 160.0, "status": "completed", "n_steps": 2700,
    })
    data = simulations_index.build_simulations_data(ws)
    rows = {r["run_id"]: r for r in data["simulations"]}
    row = rows["v2ecoli.composites.baseline__9__ff"]
    assert row["emitter_type"] == "Parquet"
    assert (row.get("completed_at") or row.get("started_at")) == 160.0
    assert row["status"] == "completed"
