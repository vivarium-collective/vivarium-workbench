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


def test_jsonl_only_new_run_sorts_before_older_legacy_run(tmp_path: Path):
    """A JSONL-only run (e.g. a fresh Composite-Explorer parquet run) with a
    newer completed_at must appear BEFORE an older legacy study.yaml/sqlite
    run in the merged, newest-first `simulations` list. The JSONL merge loop
    unions new run_ids onto the END of `sims` without re-sorting, so without
    a post-merge sort this JSONL-only row sinks to the bottom instead of
    surfacing at the top."""
    ws = tmp_path
    (ws / "studies" / "legacy").mkdir(parents=True, exist_ok=True)
    (ws / "studies" / "legacy" / "study.yaml").write_text(
        "name: legacy\n"
        "runs:\n"
        "  - name: old-run\n"
        "    status: completed\n"
        "    n_steps: 10\n"
        "    started_at: '2020-01-01T00:00:00Z'\n"
    )
    run_log.append_run_event(ws, {
        "run_id": "new-jsonl-run", "event": "started",
        "spec_id": "v2ecoli.composites.baseline", "started_at": 2_000_000_000.0,
        "status": "running", "emitter": "parquet", "study_slug": "loom",
    })
    run_log.append_run_event(ws, {
        "run_id": "new-jsonl-run", "event": "completed",
        "completed_at": 2_000_000_100.0, "status": "completed", "n_steps": 5,
    })

    data = simulations_index.build_simulations_data(ws)
    run_ids = [r["run_id"] for r in data["simulations"]]
    assert "old-run" in run_ids and "new-jsonl-run" in run_ids
    assert run_ids.index("new-jsonl-run") < run_ids.index("old-run")
