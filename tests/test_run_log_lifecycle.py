"""JSONL run-log lifecycle: deletion tombstones and orphan reconciliation.

The append-only ``runs.jsonl`` is authoritative for a run's status in the
Simulations DB. Two lifecycle transitions used to happen in sqlite ONLY, so the
fold kept overriding them with stale JSONL state:

- ``delete_simulation`` removed the sqlite rows but left the ``started`` event,
  so the next fold re-synthesised the run — undeletable through the UI.
- ``reconcile_stale_runs`` -> ``mark_orphaned`` flipped dead-pid rows to
  ``orphaned`` in sqlite, but the fold still said ``running``, so a killed run
  read "running" forever across restarts.
"""
from pathlib import Path

import pytest

from vivarium_workbench.lib import (
    composite_runs as cr,
    run_log,
    run_registry,
    simulations_index,
)


# ---------------------------------------------------------------------------
# Deletion tombstones
# ---------------------------------------------------------------------------
def test_deleted_event_removes_run_from_fold(tmp_path: Path):
    run_log.append_run_event(tmp_path, {
        "run_id": "r1", "event": "started", "status": "running"})
    assert "r1" in run_log.fold_runs_jsonl(tmp_path)

    run_log.append_deleted_event(tmp_path, "r1")
    assert "r1" not in run_log.fold_runs_jsonl(tmp_path)


def test_deleted_run_does_not_resurrect_in_simulations_db(tmp_path: Path):
    """REGRESSION: the fold re-synthesised deleted runs from their surviving
    `started` event, so a deleted run reappeared in the Sims DB on the very
    next page load and could never be removed."""
    ws = tmp_path
    (ws / "studies").mkdir(parents=True, exist_ok=True)
    run_log.append_run_event(ws, {
        "run_id": "gone", "event": "started", "spec_id": "s",
        "started_at": 1.0, "status": "running"})
    ids = [r["run_id"] for r in simulations_index.build_simulations_data(ws)["simulations"]]
    assert "gone" in ids

    run_log.append_deleted_event(ws, "gone")
    ids = [r["run_id"] for r in simulations_index.build_simulations_data(ws)["simulations"]]
    assert "gone" not in ids


def test_rerun_with_same_id_revives_after_delete(tmp_path: Path):
    """A tombstone must not poison the run_id forever — a later `started`
    for the same id (a re-run) brings it back."""
    run_log.append_run_event(tmp_path, {
        "run_id": "reused", "event": "started", "status": "running"})
    run_log.append_deleted_event(tmp_path, "reused")
    run_log.append_run_event(tmp_path, {
        "run_id": "reused", "event": "started", "status": "running",
        "started_at": 99.0})

    folded = run_log.fold_runs_jsonl(tmp_path)
    assert folded["reused"]["started_at"] == 99.0


def test_tombstone_does_not_leak_into_other_runs(tmp_path: Path):
    run_log.append_run_event(tmp_path, {"run_id": "a", "event": "started"})
    run_log.append_run_event(tmp_path, {"run_id": "b", "event": "started"})
    run_log.append_deleted_event(tmp_path, "a")

    folded = run_log.fold_runs_jsonl(tmp_path)
    assert "a" not in folded and "b" in folded


# ---------------------------------------------------------------------------
# Orphan reconciliation
# ---------------------------------------------------------------------------
def test_mark_orphaned_mirrors_to_jsonl(tmp_path: Path):
    db = tmp_path / "composite-runs.db"
    conn = cr.connect(db)
    try:
        cr.save_metadata(conn, spec_id="s", run_id="k", params={}, label="k",
                         started_at=1.0, n_steps=5, workspace=tmp_path)
        cr.mark_orphaned(conn, run_id="k", workspace=tmp_path)
    finally:
        conn.close()

    assert run_log.fold_runs_jsonl(tmp_path)["k"]["status"] == "orphaned"


def test_mark_orphaned_without_workspace_is_sqlite_only(tmp_path: Path):
    """Back-compat: the workspace arg is optional and omitting it must not
    raise (several callers still pass only a connection)."""
    db = tmp_path / "composite-runs.db"
    conn = cr.connect(db)
    try:
        cr.save_metadata(conn, spec_id="s", run_id="k2", params={}, label="k2",
                         started_at=1.0, n_steps=5)
        cr.mark_orphaned(conn, run_id="k2")
        row = conn.execute(
            "SELECT status FROM runs_meta WHERE run_id='k2'").fetchone()
    finally:
        conn.close()
    assert row["status"] == "orphaned"
    assert run_log.fold_runs_jsonl(tmp_path) == {}


def test_killed_run_does_not_read_running_after_reconcile(tmp_path: Path):
    """REGRESSION: reconcile_stale_runs only touched sqlite, and the Sims DB
    lets JSONL win, so a killed run showed "running" forever."""
    ws = tmp_path
    (ws / "studies").mkdir(parents=True, exist_ok=True)
    pbg = ws / ".pbg"
    pbg.mkdir(parents=True, exist_ok=True)
    db = pbg / "composite-runs.db"

    conn = cr.connect(db)
    try:
        cr.save_metadata(conn, spec_id="s", run_id="killed", params={},
                         label="killed", started_at=1.0, n_steps=5,
                         workspace=ws)
        # No pid recorded -> reconcile treats it as dead.
        conn.execute("UPDATE runs_meta SET pid=NULL WHERE run_id='killed'")
        conn.commit()
    finally:
        conn.close()

    assert run_log.fold_runs_jsonl(ws)["killed"]["status"] == "running"

    n = run_registry.reconcile_stale_runs(db, workspace=ws)
    assert n == 1
    assert run_log.fold_runs_jsonl(ws)["killed"]["status"] == "orphaned"

    row = {r["run_id"]: r
           for r in simulations_index.build_simulations_data(ws)["simulations"]}["killed"]
    assert row["status"] == "orphaned"
