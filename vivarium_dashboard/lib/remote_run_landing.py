"""Land a remote simulation's observable timeseries into a study's runs.db.

Reconstructs per-timestep composite-state JSON from the sms-api observables
payload ({time, series}) so the EXISTING SQLite chart pipeline renders the
remote run identically to a local one. Writes three things into the one
runs.db file: the dashboard runs_meta row, the pbg-emitters simulations row,
and the history rows. Pure DB/IO — no HTTP.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import time as _time
from pathlib import Path

from vivarium_dashboard.lib import composite_runs as cr


def _state_blobs(observables: dict) -> list[tuple[int, float, str]]:
    """Turn {time, series:{name:[...]}} into [(step, global_time, state_json), ...].

    Each state blob is {"observables": {name: value_at_that_step}}; chart
    selectors address values as ``observables/<name>``.
    """
    time = observables.get("time") or []
    series = observables.get("series") or {}
    blobs: list[tuple[int, float, str]] = []
    for i, t in enumerate(time):
        state = {"observables": {name: vals[i] for name, vals in series.items()}}
        blobs.append((i, float(t), json.dumps(state)))
    return blobs


def _init_emitter_tables(conn: sqlite3.Connection) -> None:
    """Create the pbg-emitters `history` + `simulations` tables inline.

    We replicate the pbg-emitters schema rather than importing pbg_emitters,
    which is a workspace-venv emitter dependency and not installed in the
    dashboard venv (the dashboard only reads runs.db).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        " simulation_id TEXT NOT NULL, step INTEGER NOT NULL,"
        " global_time REAL, state TEXT NOT NULL,"
        " PRIMARY KEY (simulation_id, step))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_sim_time ON history(simulation_id, global_time)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS simulations ("
        " simulation_id TEXT PRIMARY KEY, name TEXT, started_at TEXT NOT NULL,"
        " completed_at TEXT, elapsed_seconds REAL, composite_config TEXT, metadata TEXT)"
    )


def land_remote_run(
    study_dir: Path,
    *,
    spec_id: str,
    simulation_id: int,
    experiment_id: str,
    commit: str,
    observables: dict,
    label: str | None = None,
) -> str:
    """Land a remote run's observables into study_dir/runs.db; return the run_id."""
    study_dir = Path(study_dir)
    study_dir.mkdir(parents=True, exist_ok=True)
    db_path = study_dir / "runs.db"

    provenance = {
        "simulation_id": simulation_id,
        "experiment_id": experiment_id,
        "commit": commit,
        "backend": "ray",
        "source": "smsvpctest",
    }
    run_id = cr.generate_run_id(spec_id, params=provenance)
    blobs = _state_blobs(observables)
    started = _time.time()
    started_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. dashboard runs_meta row (status running)
    conn = cr.connect(db_path)
    try:
        cr.save_metadata(
            conn,
            spec_id=spec_id,
            run_id=run_id,
            params=provenance,
            label=label or "Remote run (smsvpctest)",
            started_at=started,
            n_steps=len(blobs),
        )
    finally:
        conn.close()

    # 2. simulations + history tables and rows (inline schema)
    hconn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        _init_emitter_tables(hconn)
        hconn.execute(
            "INSERT OR REPLACE INTO simulations "
            "(simulation_id, name, started_at, metadata) VALUES (?, ?, ?, ?)",
            (run_id, run_id, started_iso, json.dumps(provenance)),
        )
        hconn.executemany(
            "INSERT OR REPLACE INTO history (simulation_id, step, global_time, state) VALUES (?, ?, ?, ?)",
            [(run_id, step, gt, state) for (step, gt, state) in blobs],
        )
    finally:
        hconn.close()

    # 3. mark runs_meta completed
    conn = cr.connect(db_path)
    try:
        cr.complete_metadata(conn, run_id=run_id, n_steps=len(blobs), status="completed")
    finally:
        conn.close()

    return run_id
