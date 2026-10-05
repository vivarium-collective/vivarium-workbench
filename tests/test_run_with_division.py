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
    # v2ecoli's own loop takes any raise inside an agents composite as division
    # (scripts/run_default_baseline.py), so this models division by a raise
    composite = _composite(_agents_state(rate=2.0, max_level=5.0))
    # 1 -> 3 -> 9, then the update at 9 raises: two ticks ran
    assert cr.run_with_division(composite, 10, chunk=1) == 2
    assert composite.state["agents"]["0"]["level"] == 9.0


def test_division_part_way_through_a_chunk_counts_the_ticks_it_ran(ws):
    # rate 1 doubles the level each tick; the update at tick 121 sees 2**120
    composite = _composite(_agents_state(rate=1.0, max_level=2.0 ** 119.5))
    assert cr.run_with_division(composite, 250, chunk=100) == 120


def test_agents_store_without_a_parent_agent_runs_every_step(ws):
    state = {"agents": {"a": {
        "increase": _process("IncreaseProcess", {"rate": 1.0}, ["level"]),
        "level": 1.0,
    }}}
    composite = _composite(state)
    assert cr.run_with_division(composite, 250, chunk=100) == 250
    assert composite.state["global_time"] == 250


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
    # 1 -> 3 -> 9, then the update at 9 raises: division after two ticks,
    # inside the first (default 100-tick) chunk
    _, code, row = _study_run(ws, _agents_state(rate=2.0, max_level=5.0),
                              steps=150, run_id="divides-1")
    assert code == 200
    assert row["status"] == "completed"
    assert row["n_steps"] == 2


# --- the generator path (the one #1292 was reported on) --------------------

_GENERATORS = '''
from process_bigraph.composite_generator import composite_generator


def _p(address, config):
    return {"_type": "process", "address": "local:" + address, "config": config,
            "inputs": {"level": ["stores", "level"]},
            "outputs": {"level": ["stores", "level"]}, "interval": 1.0}


@composite_generator(name="long_1292")
def long_1292(core=None):
    return {"state": {"increase": _p("IncreaseProcess", {"rate": 1.0}),
                      "stores": {"level": 1.0}}}


@composite_generator(name="raises_1292")
def raises_1292(core=None):
    return {"state": {"increase": _p("GuardedIncreaseProcess", {"rate": 2.0, "max_level": 5.0}),
                      "stores": {"level": 1.0}}}
'''


@pytest.fixture
def generator_ids(ws):
    """Two @composite_generator entries in this test's own workspace copy,
    imported by its core so the run subprocess registers them too. The spec
    registry is process-wide with no delete, so snapshot and restore it
    (as test_generator_parquet_e2e does)."""
    from process_bigraph import composite_spec
    pkg = ws / PKG
    (pkg / "generators_1292.py").write_text(_GENERATORS)
    core = pkg / "core.py"
    core.write_text(core.read_text() + f"\nimport {PKG}.generators_1292  # noqa: E402,F401\n")
    before = dict(composite_spec.all_specs())
    import importlib
    importlib.import_module(f"{PKG}.core")
    from process_bigraph.composite_generator import _REGISTRY
    ids = {name: next(k for k in _REGISTRY if k.endswith(name))
           for name in ("long_1292", "raises_1292")}
    try:
        yield ids
    finally:
        composite_spec.clear_registry()
        for spec in before.values():
            composite_spec.register(spec)


def _generator_run(ws, spec_id, steps, run_id):
    db_file = ws / "workspace" / "studies" / "s1" / "runs.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    resp, code = cs.run_composite_subprocess(
        ws, pkg=PKG, state={}, steps=steps, db_file=str(db_file), run_id=run_id,
        spec_id=spec_id, emit_paths=["stores/level"], label="baseline",
    )
    script = (db_file.parent / "sims" / f"{run_id}.subprocess.py").read_text()
    assert "build_generator(" in script, "expected the generator path"
    conn = cr.connect(db_file)
    row = cr.query_run_meta(conn, run_id=run_id)
    conn.close()
    return resp, code, row


def test_generator_run_whose_process_raises_is_recorded_failed(ws, generator_ids):
    resp, code, row = _generator_run(ws, generator_ids["raises_1292"], 10, "gen-raises-1")
    assert code == 502
    assert "exceeded max_level" in resp["traceback"]
    assert row["status"] == "failed"


def test_generator_run_runs_every_step_past_one_chunk(ws, generator_ids):
    _, code, row = _generator_run(ws, generator_ids["long_1292"], 150, "gen-long-1")
    assert code == 200
    assert row["status"] == "completed"
    assert row["n_steps"] == 150
    db = sqlite3.connect(ws / "workspace" / "studies" / "s1" / "runs.db")
    (last,) = db.execute("SELECT state FROM history WHERE simulation_id = 'gen-long-1' "
                         "ORDER BY step DESC LIMIT 1").fetchone()
    db.close()
    # this path emits the nested state, not flattened emit-path keys
    last = json.loads(last)
    assert last["global_time"] == 150.0
    assert last["stores"]["level"] == 2.0 ** 150
