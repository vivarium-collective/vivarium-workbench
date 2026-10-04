"""Tests for the Run wrapper backing the pytest `run` fixture.

The runs.db under test is produced by the REAL writers -- runs_meta via
``composite_runs.connect``/``save_metadata``/``complete_metadata`` and history
via ``viva_emitters.SQLiteEmitter`` -- never a hand-rolled schema. A
hand-rolled schema is how the fixture drifted from the writer unnoticed.
"""
import json
from pathlib import Path
import pytest
from process_bigraph import allocate_core
from viva_emitters.sqlite_emitter import SQLiteEmitter
from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.testing.run_fixture import Run, RunNotAvailableError


def _make_runs_db(path: Path, *, runs: list[dict]) -> None:
    """Write ``runs`` into ``path`` through the workbench's own run writers.

    Each run dict: {run_id, label, spec_id, params, started_at, status,
    n_steps, manifest, states: [emitted state dict per step]}
    """
    conn = cr.connect(path)  # creates runs_meta (+ migrations) like a real run
    try:
        for r in runs:
            cr.save_metadata(
                conn, spec_id=r.get("spec_id", "pkg.composites.demo"),
                run_id=r["run_id"], params=r.get("params", {}),
                label=r.get("label", "baseline"),
                started_at=r.get("started_at", 1.0),
                n_steps=r.get("n_steps", 0), manifest=r.get("manifest"),
            )
            cr.complete_metadata(conn, run_id=r["run_id"],
                                 n_steps=r.get("n_steps", 0),
                                 status=r.get("status", "completed"))
    finally:
        conn.close()
    core = allocate_core()
    for r in runs:
        emitter = SQLiteEmitter({"file_path": str(path.parent),
                                 "db_file": path.name,
                                 "simulation_id": r["run_id"]}, core)
        for state in r.get("states", []):
            emitter.update(state)
        emitter.close()


def _xs(values):
    return [{"x": v, "global_time": float(i)} for i, v in enumerate(values)]


def test_run_loads_latest_row(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[
        # inserted newest-first so latest != last-inserted / rowid order
        {"run_id": "new", "started_at": 200.0, "states": _xs([2.0])},
        {"run_id": "old", "started_at": 100.0, "states": _xs([1.0])},
    ])
    run = Run(db)
    assert run.run_id == "new"
    assert run.timestamp == 200.0
    assert run.observable("x")[-1] == 2.0


def test_run_exposes_params_seed_status(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{
        "run_id": "r1", "params": {"rate": 2.0, "seed": 42},
        "status": "completed", "n_steps": 100, "label": "high-rate",
        "spec_id": "pkg.composites.demo",
    }])
    run = Run(db)
    assert run.params == {"rate": 2.0, "seed": 42}
    assert run.seed == 42
    assert run.status == "completed"
    assert run.n_steps == 100
    assert run.label == "high-rate"
    assert run.variant is None  # not derivable from runs_meta; see Run.variant
    assert run.composite == "pkg.composites.demo"


def test_run_seed_prefers_manifest_then_none(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[
        {"run_id": "m", "started_at": 2.0, "params": {"seed": 1},
         "manifest": {"seed": 7, "params": {}}},
        {"run_id": "n", "started_at": 1.0, "params": {"rate": 1.0}},
    ])
    assert Run(db, run_id="m").seed == 7
    assert Run(db, run_id="n").seed is None


def test_run_reads_pre_migration_runs_meta(tmp_path):
    """A runs.db from before the migrated columns (manifest_json, ...) existed:
    the writer's original runs_meta DDL, never passed through connect()."""
    import sqlite3
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(db)
    conn.execute(cr._SCHEMA_RUNS_META)
    conn.execute(
        "INSERT INTO runs_meta (run_id, spec_id, label, params_json, started_at, "
        "n_steps, status) VALUES ('r1', 'pkg.c', 'baseline', '{\"seed\": 3}', 1.0, 2, 'completed')")
    conn.commit()
    conn.close()
    run = Run(db)
    assert run.seed == 3
    assert run.label == "baseline"


def test_run_observable_dotted_path_into_nested_map(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{"run_id": "r1", "states": [
        {"species": {"X": 1.0, "Y": 5.0}, "time": 0.0},
        {"species": {"X": 2.0, "Y": 4.0}, "time": 0.5},
        {"species": {"X": 3.0, "Y": 3.0}, "time": 1.0},
    ]}])
    run = Run(db)
    assert list(run.observable("species.X")) == [1.0, 2.0, 3.0]
    assert list(run.observable("time")) == [0.0, 0.5, 1.0]
    assert list(run.time) == [0.0, 1.0, 2.0]
    assert run.final("species.Y") == 3.0
    with pytest.raises(KeyError):
        run.final("species.missing")


def test_run_trajectory_flattens_nested_maps(tmp_path):
    pytest.importorskip("pandas")
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{"run_id": "r1", "states": [
        {"species": {"X": 1.0}, "time": 0.0},
        {"species": {"X": 2.0}, "time": 0.5},
    ]}])
    traj = Run(db).trajectory
    assert sorted(traj.columns) == ["species.X", "time"]
    assert list(traj.index) == [0, 1]
    assert list(traj["species.X"]) == [1.0, 2.0]


def test_run_trajectory_skips_empty_maps(tmp_path):
    """A step whose map is still empty (e.g. stores not yet initialised at
    t = 0) contributes no column, so it doesn't add an object-typed column of {}."""
    pytest.importorskip("pandas")
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{"run_id": "r1", "states": [
        {"species": {}, "time": 0.0},
        {"species": {"X": 2.0}, "time": 0.5},
    ]}])
    traj = Run(db).trajectory
    assert sorted(traj.columns) == ["species.X", "time"]
    assert traj["species.X"].isna().tolist() == [True, False]


def test_all_run_ids_ordered_by_started_at(tmp_path):
    from vivarium_workbench.testing.run_fixture import _all_run_ids
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[
        {"run_id": "c", "started_at": 30.0},
        {"run_id": "a", "started_at": 10.0},
        {"run_id": "b", "started_at": 20.0},
    ])
    assert _all_run_ids(db) == ["a", "b", "c"]


def test_run_observable_returns_array(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{"run_id": "r1", "states": _xs([1.0, 2.0, 3.0])}])
    run = Run(db)
    import numpy as np
    arr = run.observable("x")
    assert isinstance(arr, np.ndarray)
    assert list(arr) == [1.0, 2.0, 3.0]


def test_run_final_initial_helpers(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{"run_id": "r1", "states": _xs([1.0, 2.0, 3.0])}])
    run = Run(db)
    assert run.final("x") == 3.0
    assert run.initial("x") == 1.0


def test_run_cv(tmp_path):
    db = tmp_path / "runs.db"
    _make_runs_db(db, runs=[{"run_id": "r1", "states": _xs([10.0, 10.0, 10.0])}])
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


# Inline test of the `runs` fixture via subprocess pytest, since pytest's own
# fixture machinery is hard to invoke directly in a unit test.

import subprocess, sys, textwrap


def test_runs_fixture_parametrizes_over_all_runs(tmp_path):
    study = tmp_path / "studies" / "demo"
    study.mkdir(parents=True)
    (study / "study.yaml").write_text(
        "schema_version: 4\nname: demo\nbaseline: []\n"
        "tests: {auto_discover: true, data_source: all_runs, pytest_args: [], last_results: null}\n"
        "references: []\nimplementation_tasks: ''\n"
    )
    _make_runs_db(study / "runs.db", runs=[
        {"run_id": "a", "started_at": 100.0, "states": _xs([1.0])},
        {"run_id": "b", "started_at": 200.0, "states": _xs([2.0])},
    ])
    (study / "tests").mkdir()
    (study / "tests" / "conftest.py").write_text(
        "from vivarium_workbench.testing import run, runs, pytest_generate_tests  # noqa: F401\n"
    )
    (study / "tests" / "test_demo.py").write_text(textwrap.dedent("""
        def test_each_run(runs):
            assert runs.final("x") in (1.0, 2.0)
    """))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(study / "tests"), "-v"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
