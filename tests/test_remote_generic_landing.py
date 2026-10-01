"""Landing a generic composite run's results (the zip a compose simulation serves) into the run store.

A run's emitter history is one state per emitted step, keyed by emitter name; the run store's ``history`` table is
one row per step (``simulation_id, step, global_time, state``) -- the table every existing viewer reads. Landing is
that mapping and nothing more.
"""
from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import composite_flush, remote_run, run_runner
from vivarium_workbench.lib.remote_run_landing import land_composite_results


def _zip(path: Path, history: object = None, *, name: str = "emitter_history.json") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        if history is not None:
            z.writestr(name, json.dumps(history))
        z.writestr("events.jsonl", "")
    return path


def _rows(db: Path, run_id: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM history WHERE simulation_id = ? ORDER BY step", (run_id,)).fetchall()
    finally:
        conn.close()


def test_each_emitted_state_becomes_one_history_row_in_step_order(tmp_path):
    states = [{"results": {"m": [i]}, "global_time": float(i) * 0.5} for i in range(3)]
    n = land_composite_results(_zip(tmp_path / "r.zip", {"emitter": states}), tmp_path / "runs.db", "r1")
    rows = _rows(tmp_path / "runs.db", "r1")
    assert n == 3 and [r["step"] for r in rows] == [0, 1, 2]
    assert [json.loads(r["state"]) for r in rows] == states          # the state is stored whole, untouched
    assert [r["global_time"] for r in rows] == [0.0, 0.5, 1.0]       # time is taken from the state when it has one


def test_a_state_without_a_time_is_stored_with_no_time_rather_than_an_invented_one(tmp_path):
    land_composite_results(_zip(tmp_path / "r.zip", {"emitter": [{"x": 1}]}), tmp_path / "runs.db", "r1")
    assert _rows(tmp_path / "runs.db", "r1")[0]["global_time"] is None


def test_landing_the_same_run_twice_replaces_it_instead_of_failing_or_doubling(tmp_path):
    db = tmp_path / "runs.db"
    land_composite_results(_zip(tmp_path / "a.zip", {"emitter": [{"v": 1}, {"v": 2}]}), db, "r1")
    land_composite_results(_zip(tmp_path / "b.zip", {"emitter": [{"v": 9}]}), db, "r1")
    assert [json.loads(r["state"]) for r in _rows(db, "r1")] == [{"v": 9}]


def test_other_runs_in_the_same_store_are_left_alone(tmp_path):
    db = tmp_path / "runs.db"
    land_composite_results(_zip(tmp_path / "a.zip", {"emitter": [{"v": 1}]}), db, "keep")
    land_composite_results(_zip(tmp_path / "b.zip", {"emitter": [{"v": 2}]}), db, "new")
    assert len(_rows(db, "keep")) == 1 and len(_rows(db, "new")) == 1


@pytest.mark.parametrize("history", [None, {}, {"emitter": "nope"}, ["not", "a", "dict"]])
def test_an_archive_that_is_not_an_emitter_history_is_refused_and_nothing_is_written(tmp_path, history):
    db = tmp_path / "runs.db"
    with pytest.raises(ValueError, match="emitter"):
        land_composite_results(_zip(tmp_path / "r.zip", history), db, "r1")
    assert not db.exists() or _rows(db, "r1") == []


def test_several_emitters_are_refused_not_silently_merged(tmp_path):
    """One history table row per step cannot say which emitter a state came from; guessing would be wrong data."""
    with pytest.raises(ValueError, match="emitter"):
        land_composite_results(_zip(tmp_path / "r.zip", {"a": [{}], "b": [{}]}), tmp_path / "runs.db", "r1")


# -- the detached runner lands a zip the same way -----------------------------------------------------

def _req(tmp_path: Path) -> run_runner.RunRequest:
    return run_runner.RunRequest(
        run_id="r1", spec_id="pkg.composites.demo", pkg="pkg", workspace=tmp_path, overrides={}, steps=3,
        emit_paths=[], db_file=str(tmp_path / "runs.db"), log_path=str(tmp_path / "run.log"), target="deployment")


def test_the_runner_lands_a_zip_into_the_run_store_and_records_the_steps_landed(tmp_path, monkeypatch):
    monkeypatch.setattr(composite_flush, "_auto_results_enabled", lambda run_dir: False)
    archive = _zip(tmp_path / "results.zip", {"emitter": [{"a": 1}, {"a": 2}]})
    monkeypatch.setattr(remote_run, "run_remote", lambda ws, spec_id, **k: archive)
    seen = {}
    monkeypatch.setattr(cr, "complete_metadata", lambda conn, **k: seen.update(k))
    assert run_runner._execute_remote(_req(tmp_path), tmp_path) == 0
    assert [json.loads(r["state"]) for r in _rows(tmp_path / "runs.db", "r1")] == [{"a": 1}, {"a": 2}]
    assert (seen["status"], seen["n_steps"]) == ("completed", 2)  # what landed, not what was asked (3)


def test_a_zip_that_cannot_be_landed_says_so_in_the_run_log_without_failing_a_completed_run(tmp_path, monkeypatch):
    """The remote run completed; a results zip the workbench cannot land (here: no emitter history) is reported in
    the run's log, and the run stays completed -- as an unlandable archive always has."""
    monkeypatch.setattr(composite_flush, "_auto_results_enabled", lambda run_dir: False)
    archive = _zip(tmp_path / "results.zip", None)
    monkeypatch.setattr(remote_run, "run_remote", lambda ws, spec_id, **k: archive)
    seen = {}
    monkeypatch.setattr(cr, "complete_metadata", lambda conn, **k: seen.update(k))
    assert run_runner._execute_remote(_req(tmp_path), tmp_path) == 0
    assert seen["status"] == "completed"
    assert "REMOTE RESULTS NOT LANDED" in (tmp_path / "run.log").read_text()
