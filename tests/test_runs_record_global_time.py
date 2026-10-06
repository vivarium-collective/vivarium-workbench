"""Every SQLite run history records the model time (``global_time``).

The SQLiteEmitter fills ``history.global_time`` from the emitted
``global_time``, and ``inject_sqlite_emitter`` mirrors whichever emitter the
run carries: a composite's own, the declared-path ``user_emitter``, or none.
A mirrored emitter that didn't wire ``global_time`` (most composites' own
emitters, and the flat ``user_emitter`` a study's readouts produce) left the
column NULL and the stored state without a time.

Real ``Composite`` objects, the real SQLiteEmitter and real study-run
subprocesses against the ``ws_increase_demo`` fixture; nothing is mocked.
"""
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import composite_subprocess as cs
from vivarium_workbench.testing.run_fixture import Run

FIXTURE_WS = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
PKG = "pbg_ws_increase_demo"


def _increase(path):
    return {"_type": "process", "address": "local:IncreaseProcess", "config": {"rate": 1.0},
            "inputs": {"level": path}, "outputs": {"level": path}, "interval": 0.5}


def _history(db_file, run_id):
    db = sqlite3.connect(db_file)
    rows = db.execute("SELECT global_time, state FROM history WHERE simulation_id = ? "
                      "ORDER BY step", (run_id,)).fetchall()
    db.close()
    return rows


@pytest.fixture
def ws(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, ws)
    sys.path.insert(0, str(ws))
    yield ws
    sys.path.remove(str(ws))
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]


def _run(ws, state, steps, run_id, emit_paths):
    db_file = ws / "workspace" / "studies" / "s1" / "runs.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    _, code = cs.run_composite_subprocess(
        ws, pkg=PKG, state=state, steps=steps, db_file=str(db_file),
        run_id=run_id, spec_id="test.legacy.increase", emit_paths=emit_paths,
        label="baseline")
    assert code == 200
    return db_file


def test_composite_with_its_own_emitter_records_global_time(ws):
    # the composite's own emitter emits only the level; no declared paths
    state = {"increase": _increase(["stores", "level"]), "stores": {"level": 1.0},
             "emitter": {"_type": "step", "address": "local:RAMEmitter",
                         "config": {"emit": {"level": "float"}},
                         "inputs": {"level": ["stores", "level"]}}}
    db_file = _run(ws, state, steps=2, run_id="own-1", emit_paths=[])
    rows = _history(db_file, "own-1")
    times = [t for t, _ in rows]
    assert times == [0.0, 0.5, 1.0, 1.5, 2.0]
    run = Run(db_file, "own-1")
    assert list(run.observable("global_time")) == times
    assert len(run.observable("level")) == len(times)      # its own keys are kept


def test_study_run_with_readouts_records_global_time(ws):
    # the emit paths a real study derives from a readout
    emit_paths = cr.collect_emit_paths_from_spec(
        {"readouts": [{"name": "level", "store_path": "stores.level"}]})
    state = {"increase": _increase(["stores", "level"]), "stores": {"level": 1.0}}
    db_file = _run(ws, state, steps=2, run_id="readout-1", emit_paths=emit_paths)
    times = [t for t, _ in _history(db_file, "readout-1")]
    assert times == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert list(Run(db_file, "readout-1").observable("global_time")) == times


def test_a_store_that_flattens_to_global_time_cannot_replace_it(ws):
    # declared path global/time flattens to the key "global_time"
    state = {"increase": _increase(["stores", "level"]), "stores": {"level": 1.0},
             "global": {"time": 42.0}}
    db_file = _run(ws, state, steps=2, run_id="collide-1",
                   emit_paths=["global/time", "stores/level"])
    assert [t for t, _ in _history(db_file, "collide-1")] == [0.0, 0.5, 1.0, 1.5, 2.0]
