"""Pure-JSONL Simulations DB.

`build_simulations_data` migrates any run still living only in the legacy stores
(sqlite runs.db / study.yaml / parquet-zarr hives) into the append-only JSONL run
log, then folds JSONL alone. This lists every emitter (SQLite / XArray / Parquet)
uniformly in one Sim-DB with its data-store location intact, so retrieval keeps
working across emitters.
"""
from pathlib import Path

from vivarium_workbench.lib import run_log, simulations_index
from vivarium_workbench.lib.composite_runs import connect, save_metadata


def _seed_runs_db(ws: Path, study: str, run_id: str, *, params=None) -> None:
    """Seed a legacy sqlite-only run (no JSONL) in studies/<study>/runs.db."""
    db = ws / "studies" / study / "runs.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    save_metadata(conn, spec_id="v2ecoli.composites.baseline", run_id=run_id,
                  params=params or {}, label=run_id, started_at=100.0,
                  n_steps=5, log_path=None)  # workspace= omitted -> sqlite only
    conn.close()


def _rows(ws: Path) -> dict:
    data = simulations_index.build_simulations_data(ws)
    return {r["run_id"]: r for r in data["simulations"]}


def test_pure_jsonl_lists_all_emitters_with_store_location(tmp_path: Path):
    ws = tmp_path
    # SQLite-emitter run: data lives in runs.db, no native store.
    _seed_runs_db(ws, "s_sqlite", "run-sqlite")
    # XArray run: params store_path points at a .zarr store.
    (ws / "studies" / "s_xarray" / "runs.run-xarray.zarr").mkdir(parents=True)
    _seed_runs_db(ws, "s_xarray", "run-xarray",
                  params={"store_path": "studies/s_xarray/runs.run-xarray.zarr"})
    # Parquet run: params store_path points at a parquet-runs dir.
    (ws / "studies" / "s_parquet" / "parquet-runs").mkdir(parents=True)
    _seed_runs_db(ws, "s_parquet", "run-parquet",
                  params={"store_path": "studies/s_parquet/parquet-runs"})
    # JSONL-native run (dashboard dual-writes these): already in the log.
    run_log.append_run_event(ws, {
        "run_id": "run-native", "event": "started", "spec_id": "s",
        "started_at": 200.0, "status": "running", "emitter": "parquet",
        "store_path": "studies/native/parquet-runs", "study_slug": "native",
    })

    rows = _rows(ws)
    assert {"run-sqlite", "run-xarray", "run-parquet", "run-native"} <= set(rows)
    assert rows["run-sqlite"]["emitter_type"] == "SQLite"
    assert rows["run-xarray"]["emitter_type"] == "XArray"
    assert rows["run-parquet"]["emitter_type"] == "Parquet"
    assert rows["run-native"]["emitter_type"] == "Parquet"
    # Every run must be retrievable — the "⬇ Data" button gates on
    # store_path || db_path, so a JSONL row without either would be a dead run.
    for r in rows.values():
        assert r.get("store_path") or r.get("db_path"), \
            f"{r['run_id']} has no data-store location"


def test_legacy_sqlite_run_surfaces_via_backfill(tmp_path: Path):
    ws = tmp_path
    _seed_runs_db(ws, "s", "legacy-1")
    assert run_log.fold_runs_jsonl(ws) == {}          # not in the log yet
    assert "legacy-1" in _rows(ws)                     # surfaced by backfill
    assert "legacy-1" in run_log.fold_runs_jsonl(ws)   # and now migrated in


def test_backfill_is_idempotent(tmp_path: Path):
    ws = tmp_path
    _seed_runs_db(ws, "s", "r1")
    log = ws / ".pbg" / "runs.jsonl"
    n1 = simulations_index.backfill_index_into_jsonl(ws)
    lines1 = log.read_text().count("\n")
    n2 = simulations_index.backfill_index_into_jsonl(ws)
    lines2 = log.read_text().count("\n")
    assert n1 == 1 and n2 == 0            # second pass appends nothing
    assert lines1 == lines2               # log unchanged


def test_deleted_run_stays_deleted_across_backfill(tmp_path: Path):
    """A tombstoned run must not be resurrected by a subsequent backfill pass
    that still sees its sqlite row."""
    ws = tmp_path
    _seed_runs_db(ws, "s", "r1")
    assert "r1" in _rows(ws)                     # backfilled in
    run_log.append_run_event(ws, {"run_id": "r1", "event": "deleted"})
    assert "r1" not in _rows(ws)                 # gone, and backfill won't revive it
