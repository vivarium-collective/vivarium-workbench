"""Tests for the Run wrapper backing the pytest `run` fixture."""
import sqlite3, json
from pathlib import Path
import pytest
from vivarium_dashboard.testing.run_fixture import Run, RunNotAvailableError


def _make_runs_db(path: Path, *, runs: list[dict]) -> None:
    """Create a minimal runs.db with the given runs.

    Each run dict: {run_id, params, seed, status, n_steps, observables: {name: [values]}}
    """
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs_meta (
            run_id TEXT PRIMARY KEY,
            params TEXT,
            seed INTEGER,
            status TEXT,
            n_steps INTEGER,
            variant TEXT,
            composite TEXT,
            timestamp TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            run_id TEXT,
            step INTEGER,
            observable TEXT,
            value REAL
        )
    """)
    for r in runs:
        conn.execute(
            "INSERT INTO runs_meta(run_id, params, seed, status, n_steps, variant, composite, timestamp) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (r["run_id"], json.dumps(r.get("params", {})), r.get("seed"), r.get("status", "completed"),
             r.get("n_steps", 0), r.get("variant"), r.get("composite", "b"), r.get("timestamp", "")),
        )
        for obs_name, values in r.get("observables", {}).items():
            for step, v in enumerate(values):
                conn.execute(
                    "INSERT INTO history(run_id, step, observable, value) VALUES (?,?,?,?)",
                    (r["run_id"], step, obs_name, v),
                )
    conn.commit()
    conn.close()


def test_run_loads_latest_row(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[
        {"run_id": "old", "timestamp": "2026-05-14T00:00:00", "observables": {"x": [1.0]}},
        {"run_id": "new", "timestamp": "2026-05-15T00:00:00", "observables": {"x": [2.0]}},
    ])
    run = Run(db)
    assert run.observable("x")[-1] == 2.0


def test_run_exposes_params_seed_status(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{
        "run_id": "r1", "params": {"rate": 2.0}, "seed": 42,
        "status": "completed", "n_steps": 100, "variant": "high-rate", "composite": "baseline",
    }])
    run = Run(db)
    assert run.params == {"rate": 2.0}
    assert run.seed == 42
    assert run.status == "completed"
    assert run.n_steps == 100
    assert run.variant == "high-rate"
    assert run.composite == "baseline"


def test_run_observable_returns_array(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{"run_id": "r1", "observables": {"x": [1.0, 2.0, 3.0]}}])
    run = Run(db)
    import numpy as np
    arr = run.observable("x")
    assert isinstance(arr, np.ndarray)
    assert list(arr) == [1.0, 2.0, 3.0]


def test_run_final_initial_helpers(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{"run_id": "r1", "observables": {"x": [1.0, 2.0, 3.0]}}])
    run = Run(db)
    assert run.final("x") == 3.0
    assert run.initial("x") == 1.0


def test_run_cv(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{"run_id": "r1", "observables": {"x": [10.0, 10.0, 10.0]}}])
    run = Run(db)
    assert run.cv("x") == 0.0


def test_run_raises_when_db_missing(tmp_path):
    with pytest.raises(RunNotAvailableError):
        Run(tmp_path / "nonexistent.db")


def test_run_raises_when_no_runs(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[])
    with pytest.raises(RunNotAvailableError):
        Run(db)
