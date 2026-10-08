"""``run_with_division`` reads division from the state only: in a single-cell
composite, a map update replacing the parent ``agents['0']`` with daughters
(#1292).

An exception from ``composite.run()`` always propagates, so the run is
recorded failed. Taking a raise as division (inside any composite with an
``agents`` store) recorded real failures as ``completed``, and on the
generator path it did so for composites without agents too: the declared-path
emitter wires the ``agents/0/<p>`` variants that collect_emit_paths_from_spec
adds for every readout, which creates an ``agents`` store.

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


def test_agents_composite_failure_propagates(ws):
    # a raise inside an agents composite is a failure, not division
    composite = _composite(_agents_state(rate=2.0, max_level=5.0))
    with pytest.raises(RuntimeError, match="exceeded max_level"):
        cr.run_with_division(composite, 10, chunk=1)


def _map_composite(agents, update_3):
    """A real Process that applies ``update_3`` (a process-bigraph map update)
    to the ``agents`` map on its third update."""
    from process_bigraph import Composite, Process, allocate_core

    class Edit(Process):
        config_schema = {}

        def initialize(self, config):
            self.updates = 0

        def inputs(self):
            return {}

        def outputs(self):
            return {"agents": "map[float]"}

        def update(self, state, interval):
            self.updates += 1
            return {"agents": update_3} if self.updates == 3 else {}

    core = allocate_core()
    core.register_link("Edit", Edit)
    return Composite({"state": {
        "agents": dict(agents),
        "edit": {"_type": "process", "address": "local:Edit", "interval": 1.0,
                 "outputs": {"agents": ["agents"]}},
    }}, core=core)


_DIVIDE = {"_remove": ["0"], "_add": [("1", 0.5), ("2", 0.5)]}


def test_division_stops_the_run_after_the_parent_is_replaced():
    composite = _map_composite({"0": 1.0}, _DIVIDE)
    assert cr.run_with_division(composite, 10, chunk=1) == 3
    assert composite.state["agents"] == {"1": 0.5, "2": 0.5}
    assert composite.state["global_time"] == 3.0


def test_division_mid_chunk_stops_at_the_end_of_that_chunk():
    # run(n) cannot stop part-way, so the chunk that divided runs to its end
    # and its ticks are counted: they ran
    composite = _map_composite({"0": 1.0}, _DIVIDE)
    assert cr.run_with_division(composite, 250, chunk=100) == 100
    assert composite.state["global_time"] == 100.0


def test_a_parent_removed_without_daughters_runs_every_step():
    composite = _map_composite({"0": 1.0}, {"_remove": ["0"]})
    assert cr.run_with_division(composite, 10, chunk=1) == 10
    assert composite.state["agents"] == {}


def test_a_colony_runs_every_step_when_agent_0_divides():
    composite = _map_composite({"0": 1.0, "1": 1.0}, _DIVIDE)
    assert cr.run_with_division(composite, 10, chunk=1) == 10


def test_a_cell_that_divides_itself_stops_the_run():
    # the v2ecoli shape: a division Step inside agents/0 removes its own agent
    # and adds daughters with fresh process edges, while a sibling Process runs
    from process_bigraph import Composite, Process, Step, allocate_core

    class Grow(Process):
        config_schema = {}

        def inputs(self):
            return {"mass": "float"}

        def outputs(self):
            return {"mass": "float"}

        def update(self, state, interval):
            return {"mass": state["mass"] * interval}

    def cell(mass, agent_id, threshold):
        return {"mass": mass,
                "grow": {"_type": "process", "address": "local:Grow", "interval": 1.0,
                         "inputs": {"mass": ["mass"]}, "outputs": {"mass": ["mass"]}},
                "division": {"_type": "step", "address": "local:SelfDivide",
                             "config": {"agent_id": agent_id, "threshold": threshold},
                             "inputs": {"mass": ["mass"]}, "outputs": {"agents": [".."]}}}

    class SelfDivide(Step):
        config_schema = {"agent_id": "string", "threshold": "float"}

        def inputs(self):
            return {"mass": "float"}

        def outputs(self):
            return {"agents": {"_type": "map", "_value": "node"}}

        def update(self, state):
            if state["mass"] < self.config["threshold"]:
                return {}
            parent, half = self.config["agent_id"], state["mass"] / 2
            return {"agents": {"_remove": [parent], "_add": [
                (parent + d, cell(half, parent + d, 1e300)) for d in ("0", "1")]}}

    core = allocate_core()
    core.register_link("Grow", Grow)
    core.register_link("SelfDivide", SelfDivide)
    composite = Composite({"state": {"agents": {"0": cell(1.0, "0", 4.0)}}}, core=core)
    # mass doubles each tick: 1 -> 2 -> 4, divides at t = 2
    ticks = cr.run_with_division(composite, 10, chunk=1)
    assert sorted(composite.state["agents"]) == ["00", "01"]
    assert ticks == composite.state["global_time"] < 10


@pytest.mark.parametrize("agents", [[{"x": 1.0}, {"x": 2.0}], [1.0, 2.0]])
def test_an_agents_list_runs_every_step(agents):
    # a spatial or particle simulator may keep agents as a list, not a map
    from process_bigraph import Composite, Process, allocate_core

    class Tick(Process):
        config_schema = {}

        def inputs(self):
            return {}

        def outputs(self):
            return {"level": "float"}

        def update(self, state, interval):
            return {"level": interval}

    core = allocate_core()
    core.register_link("Tick", Tick)
    composite = Composite({"state": {
        "agents": agents, "level": 0.0,
        "tick": {"_type": "process", "address": "local:Tick", "interval": 1.0,
                 "outputs": {"level": ["level"]}}}}, core=core)
    assert cr.run_with_division(composite, 10, chunk=1) == 10
    assert composite.state["global_time"] == 10


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


def test_study_run_whose_agent_raises_is_recorded_failed(ws):
    resp, code, row = _study_run(ws, _agents_state(rate=2.0, max_level=5.0),
                                 steps=150, run_id="agent-raises-1")
    assert code == 502
    assert "exceeded max_level" in resp["traceback"]
    assert row["status"] == "failed"


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
    # Evict any stale copy of this shared fixture package left cached in
    # sys.modules by an earlier test in the same worker (many tests import
    # pbg_ws_increase_demo.core). Without this, import_module is a no-op, the
    # appended `generators_1292` import never runs, and the `next(...)` below
    # raises StopIteration ("generator raised StopIteration") at fixture setup.
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]
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


def _generator_run(ws, spec_id, steps, run_id, emit_paths=("stores/level",)):
    db_file = ws / "workspace" / "studies" / "s1" / "runs.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    resp, code = cs.run_composite_subprocess(
        ws, pkg=PKG, state={}, steps=steps, db_file=str(db_file), run_id=run_id,
        spec_id=spec_id, emit_paths=list(emit_paths), label="baseline",
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


def test_generator_run_with_a_readout_whose_process_raises_is_recorded_failed(ws, generator_ids):
    # the emit paths a real study derives from a readout, including the
    # agents/0/<p> variant: it must not create an agents store that turns a
    # failure into "division"
    emit_paths = cr.collect_emit_paths_from_spec(
        {"readouts": [{"name": "level", "store_path": "stores.level"}]})
    assert "agents/0/stores/level" in emit_paths
    resp, code, row = _generator_run(ws, generator_ids["raises_1292"], 10, "gen-raises-2",
                                     emit_paths=emit_paths)
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


# --- declared-path emitter: a store created at run time --------------------

def test_declared_agent_path_is_recorded_when_agents_is_created_at_run_time():
    # an agents store absent from the spec-time state, created by a process
    # output: the declared path must still be wired and emitted
    from process_bigraph import Composite, Process, allocate_core, gather_emitter_results

    class Inoculate(Process):
        config_schema = {}

        def inputs(self):
            return {"agents": "map[float]"}

        def outputs(self):
            return {"agents": "map[float]"}

        def update(self, state, interval):
            if "0" not in state["agents"]:
                return {"agents": {"_add": [("0", 1.0)]}}
            return {"agents": {"0": 1.0}}

    core = allocate_core()
    core.register_link("Inoculate", Inoculate)
    state = cr.inject_emitter_for_declared_paths({"inoculate": {
        "_type": "process", "address": "local:Inoculate", "interval": 1.0,
        "inputs": {"agents": ["agents"]}, "outputs": {"agents": ["agents"]}}},
        ["agents/0"])
    composite = Composite({"state": state}, core=core)
    composite.run(3)
    emitted = gather_emitter_results(composite)[("user_emitter",)]
    assert [row["agents"]["0"] for row in emitted if row["agents"]] == [1.0, 2.0, 3.0]
