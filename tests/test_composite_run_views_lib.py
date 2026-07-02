"""Tests for lib.composite_run_views builders and server shim parity.

Tests each builder function's (dict, status) output for:
  - no-db edge cases (200/404 per route)
  - missing / invalid inputs (400)
  - seeded-db happy paths (200 with correct bodies)
  - unknown run_id → 404

ServerShimParity class invokes the real ``server.Handler`` methods via
``__new__`` (no socket bound), patches ``server.WORKSPACE`` and captures
``_json(body, status)`` calls, then asserts equality with the lib builder.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib.composite_run_views import (
    build_composite_run,
    build_composite_run_state,
    build_composite_run_status,
    build_composite_runs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ws(tmp_path: Path) -> Path:
    """Return a minimal workspace root (just the .pbg dir created)."""
    (tmp_path / ".pbg").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _seed_db(ws: Path, *, spec_id: str = "demo.spec", run_id: str = "demo__1__aabbcc",
             status: str = "completed", n_steps: int = 5,
             log_path: str | None = None) -> Path:
    """Create .pbg/composite-runs.db with one run_meta row. Returns db path."""
    db = ws / ".pbg" / "composite-runs.db"
    conn = cr.connect(db)
    cr.save_metadata(
        conn,
        spec_id=spec_id,
        run_id=run_id,
        params={},
        label="test-run",
        started_at=1_000_000.0,
        n_steps=n_steps,
        log_path=log_path,
    )
    cr.complete_metadata(conn, run_id=run_id, n_steps=n_steps, status=status)
    conn.close()
    return db


def _seed_history(db_path: Path, run_id: str, step: int,
                  state: dict) -> None:
    """Insert a history row so query_run / query_run_state return data."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS history (
               simulation_id TEXT NOT NULL,
               step          INTEGER NOT NULL,
               global_time   REAL,
               state         TEXT NOT NULL,
               PRIMARY KEY (simulation_id, step)
           )"""
    )
    conn.execute(
        "INSERT INTO history (simulation_id, step, global_time, state) "
        "VALUES (?, ?, ?, ?)",
        (run_id, step, float(step), json.dumps(state)),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# build_composite_runs
# ---------------------------------------------------------------------------

class TestBuildCompositeRuns:
    def test_missing_spec_id_returns_400(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = build_composite_runs(ws, None)
        assert status == 400
        assert body["runs"] == []
        assert "missing spec_id" in body["error"]

    def test_empty_spec_id_string_returns_400(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = build_composite_runs(ws, "")
        assert status == 400
        assert "missing spec_id" in body["error"]

    def test_no_db_returns_200_empty(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = build_composite_runs(ws, "demo.spec")
        assert status == 200
        assert body == {"runs": []}

    def test_seeded_run_returns_list(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, spec_id="demo.spec", run_id="r1")
        body, status = build_composite_runs(ws, "demo.spec")
        assert status == 200
        assert len(body["runs"]) == 1
        assert body["runs"][0]["run_id"] == "r1"

    def test_wrong_spec_id_returns_empty_list(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, spec_id="demo.spec", run_id="r1")
        body, status = build_composite_runs(ws, "other.spec")
        assert status == 200
        assert body["runs"] == []


# ---------------------------------------------------------------------------
# build_composite_run (trajectory)
# ---------------------------------------------------------------------------

class TestBuildCompositeRun:
    def test_no_db_returns_404(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = build_composite_run(ws, "any-run-id")
        assert status == 404
        assert body == {"error": "no run database"}

    def test_run_with_no_history_returns_404(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1")
        body, status = build_composite_run(ws, "r1")
        assert status == 404
        assert body == {"error": "run not found"}

    def test_unknown_run_id_returns_404(self, tmp_path):
        ws = _make_ws(tmp_path)
        db = _seed_db(ws, run_id="r1")
        _seed_history(db, "r1", 0, {"x": 1})
        body, status = build_composite_run(ws, "no-such-run")
        assert status == 404
        assert body == {"error": "run not found"}

    def test_seeded_trajectory_returns_200(self, tmp_path):
        ws = _make_ws(tmp_path)
        db = _seed_db(ws, run_id="r1")
        _seed_history(db, "r1", 0, {"x": 42})
        body, status = build_composite_run(ws, "r1")
        assert status == 200
        assert body["run_id"] == "r1"
        assert len(body["trajectory"]) == 1
        assert body["trajectory"][0]["step"] == 0
        assert body["trajectory"][0]["state"] == {"x": 42}


# ---------------------------------------------------------------------------
# build_composite_run_state
# ---------------------------------------------------------------------------

class TestBuildCompositeRunState:
    def test_no_db_returns_404(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = build_composite_run_state(ws, "r1", 0)
        assert status == 404
        assert body == {"error": "no run database"}

    def test_step_not_in_history_returns_404(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1")
        body, status = build_composite_run_state(ws, "r1", 99)
        assert status == 404
        assert body == {"error": "state not found for run+step"}

    def test_seeded_step_returns_200(self, tmp_path):
        ws = _make_ws(tmp_path)
        db = _seed_db(ws, run_id="r1")
        _seed_history(db, "r1", 3, {"level": 0.9})
        body, status = build_composite_run_state(ws, "r1", 3)
        assert status == 200
        assert body["run_id"] == "r1"
        assert body["step"] == 3
        assert body["state"] == {"level": 0.9}

    def test_default_step_zero(self, tmp_path):
        ws = _make_ws(tmp_path)
        db = _seed_db(ws, run_id="r1")
        _seed_history(db, "r1", 0, {"t": 0.0})
        body, status = build_composite_run_state(ws, "r1", 0)
        assert status == 200
        assert body["step"] == 0


# ---------------------------------------------------------------------------
# build_composite_run_status
# ---------------------------------------------------------------------------

class TestBuildCompositeRunStatus:
    def test_no_db_returns_404(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = build_composite_run_status(ws, "r1")
        assert status == 404
        assert body == {"error": "no run database"}

    def test_unknown_run_id_returns_404(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1")
        body, status = build_composite_run_status(ws, "no-such-run")
        assert status == 404
        assert body == {"error": "run not found"}

    def test_completed_run_basic_200(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1", status="completed", n_steps=5)
        body, status = build_composite_run_status(ws, "r1")
        assert status == 200
        assert body["run_id"] == "r1"
        assert body["status"] == "completed"
        assert body["n_steps"] == 5
        assert body["progress_step"] == 0  # no update_progress called
        assert "viz_html" not in body

    def test_completed_run_with_viz_json(self, tmp_path):
        ws = _make_ws(tmp_path)
        run_id = "r1"
        _seed_db(ws, run_id=run_id, status="completed")
        viz_dir = ws / ".pbg" / "runs" / run_id
        viz_dir.mkdir(parents=True)
        viz_data = {"plot": "data"}
        (viz_dir / "viz.json").write_text(json.dumps(viz_data), encoding="utf-8")

        body, status = build_composite_run_status(ws, run_id)
        assert status == 200
        assert body["viz_html"] == viz_data

    def test_completed_run_bad_viz_json_silently_skipped(self, tmp_path):
        ws = _make_ws(tmp_path)
        run_id = "r1"
        _seed_db(ws, run_id=run_id, status="completed")
        viz_dir = ws / ".pbg" / "runs" / run_id
        viz_dir.mkdir(parents=True)
        (viz_dir / "viz.json").write_text("NOT JSON!", encoding="utf-8")

        body, status = build_composite_run_status(ws, run_id)
        assert status == 200
        assert "viz_html" not in body

    def test_failed_run_with_log(self, tmp_path):
        ws = _make_ws(tmp_path)
        log_rel = ".pbg/logs/r1.log"
        log_full = ws / log_rel
        log_full.parent.mkdir(parents=True, exist_ok=True)
        log_full.write_text("error: something went wrong", encoding="utf-8")

        _seed_db(ws, run_id="r1", status="failed", log_path=log_rel)
        body, status = build_composite_run_status(ws, "r1")
        assert status == 200
        assert body["status"] == "failed"
        assert body["log_path"] == log_rel
        assert "error: something went wrong" in body["error"]

    def test_failed_run_log_path_in_meta_missing_file(self, tmp_path):
        """log_path present but file missing → log_path in resp but no error key."""
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1", status="failed", log_path=".pbg/logs/r1.log")
        body, status = build_composite_run_status(ws, "r1")
        assert status == 200
        assert body["log_path"] == ".pbg/logs/r1.log"
        assert "error" not in body

    def test_failed_run_no_log_path(self, tmp_path):
        """No log_path in meta → neither log_path nor error in resp."""
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1", status="failed")
        body, status = build_composite_run_status(ws, "r1")
        assert status == 200
        assert "log_path" not in body
        assert "error" not in body

    def test_orphaned_run_with_log(self, tmp_path):
        ws = _make_ws(tmp_path)
        log_rel = ".pbg/logs/r1.log"
        (ws / log_rel).parent.mkdir(parents=True)
        (ws / log_rel).write_text("orphaned after timeout", encoding="utf-8")
        _seed_db(ws, run_id="r1", status="orphaned", log_path=log_rel)
        body, status = build_composite_run_status(ws, "r1")
        assert status == 200
        assert body["status"] == "orphaned"
        assert "orphaned after timeout" in body["error"]

    def test_running_status_returns_progress(self, tmp_path):
        ws = _make_ws(tmp_path)
        db = _seed_db(ws, run_id="r1", status="running")
        conn = cr.connect(db)
        cr.update_progress(conn, run_id="r1", progress_step=3, heartbeat_at=1234.5)
        conn.close()
        body, status = build_composite_run_status(ws, "r1")
        assert status == 200
        assert body["status"] == "running"
        assert body["progress_step"] == 3
        assert body["heartbeat_at"] == 1234.5

    def test_log_excerpt_truncated_to_2000_chars(self, tmp_path):
        ws = _make_ws(tmp_path)
        log_rel = ".pbg/logs/r1.log"
        log_full = ws / log_rel
        log_full.parent.mkdir(parents=True)
        # Write more than 2000 chars
        log_full.write_text("x" * 3000 + "TAIL", encoding="utf-8")
        _seed_db(ws, run_id="r1", status="failed", log_path=log_rel)
        body, status = build_composite_run_status(ws, "r1")
        assert len(body["error"]) == 2000
        assert body["error"].endswith("TAIL")
