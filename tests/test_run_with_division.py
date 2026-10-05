"""``run_with_division`` treats early stops as division only for composites
with an ``agents`` store (#1292).

It was written for v2ecoli, where division makes ``composite.run()`` raise or
removes ``agents['0']``. Applied to every composite, it swallowed a process's
own failure (the study run was recorded ``completed`` with a truncated
trajectory) and stopped any composite without an ``agents`` store after its
first chunk.

Real ``Composite`` objects and real study-run subprocesses throughout, against
the ``ws_increase_demo`` fixture (same setup as
``test_composite_subprocess_fingerprint.py``); nothing here is mocked.
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import composite_subprocess as cs

FIXTURE_WS = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
PKG = "pbg_ws_increase_demo"


def _process(address, config, path):
    return {
        "_type": "process", "address": f"local:{address}", "config": config,
        "inputs": {"level": path}, "outputs": {"level": path}, "interval": 1.0,
    }


def _plain_state(address="IncreaseProcess", **config):
    return {"increase": _process(address, config, ["stores", "level"]),
            "stores": {"level": 1.0}}


def _agents_state(**config):
    """The v2ecoli shape: the cell lives under ``agents['0']``."""
    return {"agents": {"0": {
        "increase": _process("GuardedIncreaseProcess", config, ["level"]),
        "level": 1.0,
    }}}


@pytest.fixture
def ws(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, ws)
    sys.path.insert(0, str(ws))
    yield ws
    sys.path.remove(str(ws))
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]


def _composite(state):
    from process_bigraph import Composite
    from pbg_ws_increase_demo.core import build_core
    return Composite({"state": state}, core=build_core())


def _study_run(ws, state, steps, run_id):
    db_file = ws / "workspace" / "studies" / "s1" / "runs.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    resp, code = cs.run_composite_subprocess(
        ws, pkg=PKG, state=state, steps=steps, db_file=str(db_file),
        run_id=run_id, spec_id="test.legacy.increase",
        emit_paths=["stores/level"], label="baseline",
    )
    conn = cr.connect(db_file)
    row = cr.query_run_meta(conn, run_id=run_id)
    conn.close()
    return resp, code, row


# --- run_with_division on real composites ---------------------------------

def test_plain_composite_runs_every_step_past_one_chunk(ws):
    composite = _composite(_plain_state(rate=1.0))
    assert cr.run_with_division(composite, 250, chunk=100) == 250
    assert composite.state["global_time"] == 250


def test_plain_composite_failure_propagates(ws):
    # a float output is a delta, so each tick adds level*rate: 1 -> 3 -> 9,
    # and the update at level 9 raises
    composite = _composite(_plain_state("GuardedIncreaseProcess", rate=2.0, max_level=5.0))
    with pytest.raises(RuntimeError, match="exceeded max_level"):
        cr.run_with_division(composite, 10, chunk=1)


def test_agents_composite_still_stops_cleanly_at_division(ws):
    composite = _composite(_agents_state(rate=2.0, max_level=5.0))
    # 1 -> 3 -> 9, then the update at 9 raises: two ticks ran
    assert cr.run_with_division(composite, 10, chunk=1) == 2
    assert composite.state["agents"]["0"]["level"] == 9.0


# --- the study-run path end to end -----------------------------------------

def test_study_run_whose_process_raises_is_recorded_failed(ws):
    state = _plain_state("GuardedIncreaseProcess", rate=2.0, max_level=5.0)
    resp, code, row = _study_run(ws, state, steps=10, run_id="raises-1")
    assert code == 502
    assert "exceeded max_level" in resp["traceback"]
    assert row["status"] == "failed"


def test_study_run_runs_and_records_every_step_past_one_chunk(ws):
    _, code, row = _study_run(ws, _plain_state(rate=1.0), steps=150, run_id="long-1")
    assert code == 200
    assert row["status"] == "completed"
    assert row["n_steps"] == 150
    # rate 1 doubles the level each tick: the emitted trajectory must reach
    # 2**150 (exact in binary), not stop at 2**100 after the first chunk
    db = sqlite3.connect(ws / "workspace" / "studies" / "s1" / "runs.db")
    (last,) = db.execute("SELECT state FROM history WHERE simulation_id = 'long-1' "
                         "ORDER BY step DESC LIMIT 1").fetchone()
    db.close()
    assert json.loads(last)["stores_level"] == 2.0 ** 150


def test_study_run_records_steps_run_when_a_cell_divides(ws):
    # division inside the first (default 100-tick) chunk: no whole chunk ran
    _, code, row = _study_run(ws, _agents_state(rate=2.0, max_level=5.0),
                              steps=150, run_id="divides-1")
    assert code == 200
    assert row["status"] == "completed"
    assert row["n_steps"] == 0
