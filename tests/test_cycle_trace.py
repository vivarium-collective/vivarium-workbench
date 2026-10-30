"""Tests for the discovery-engine cycle trace (lib.cycle_trace).

Runs the real pure cycle against a fixture investigation — no server needed.
Skips cleanly when discovery_engine isn't importable (it resolves via PYTHONPATH
at serve time; CI puts it on the path).
"""
from pathlib import Path

import pytest

from vivarium_workbench.lib import cycle_trace as ct

pytestmark = pytest.mark.skipif(
    not ct.engine_available(), reason="discovery_engine not importable")

_WS = Path(__file__).parent / "_fixtures" / "ws_federation_collision"
_SLUG = "local_inv"


def _step(trace, sid):
    return next(s for s in trace["steps"] if s["id"] == sid)


def test_step_graph_is_well_formed():
    ids = [s["id"] for s in ct.STEPS]
    assert len(ids) == len(set(ids)), "duplicate step id"
    known = set(ids)
    for s in ct.STEPS:
        for dep in s["depends_on"]:
            assert dep in known, f"{s['id']} depends on unknown {dep}"
            # a dependency must come earlier in the canonical order
            assert ids.index(dep) < ids.index(s["id"]), f"{s['id']} dep {dep} is later"
    assert any(s["kind"] == "mutating" for s in ct.STEPS)  # advance exists


def test_build_cycle_trace_runs_every_step_without_crashing():
    trace = ct.build_cycle_trace(_WS, _SLUG)
    assert trace["available"] is True
    assert trace["inv_slug"] == _SLUG
    produced = {s["id"] for s in trace["steps"]}
    # every pure step plus the (idle) mutating advance node is present
    assert produced == {s["id"] for s in ct.STEPS}
    for s in trace["steps"]:
        assert set(s) >= {"id", "label", "phase", "kind", "status",
                          "duration_ms", "summary"}
        assert s["status"] in {"ok", "empty", "error", "idle"}


def test_foundational_steps_succeed():
    trace = ct.build_cycle_trace(_WS, _SLUG)
    # loading inputs and the core projection must not error on a valid investigation
    assert _step(trace, "load_inputs")["status"] in {"ok", "empty"}
    assert _step(trace, "run_cycle")["status"] in {"ok", "empty"}
    assert _step(trace, "load_inputs")["result"]["inv_slug"] == _SLUG
    # the core projection returns the derive-first bundle keys
    cyc = _step(trace, "run_cycle")["result"]
    assert isinstance(cyc, dict) and "argument" in cyc and "validations" in cyc


def test_advance_node_is_idle_not_executed():
    trace = ct.build_cycle_trace(_WS, _SLUG)
    adv = _step(trace, "advance")
    assert adv["kind"] == "mutating"
    assert adv["status"] == "idle"  # never auto-run


def test_rerun_single_step_returns_fresh_result():
    out = ct.rerun_step(_WS, _SLUG, "run_cycle")
    assert out["available"] is True
    assert out["step"]["id"] == "run_cycle"
    assert "result" in out["step"]


def test_rerun_rejects_mutating_advance():
    out = ct.rerun_step(_WS, _SLUG, "advance")
    assert "error" in out and "mutating" in out["error"]


def test_missing_investigation_is_blocked_not_a_crash():
    # a slug with no investigation.yaml must degrade to a visible blocked trace,
    # never raise (which would 500 the endpoint and blank the panel)
    trace = ct.build_cycle_trace(_WS, "no-such-investigation")
    assert trace["available"] is True
    assert "error" in trace
    assert {s["id"] for s in trace["steps"]} == {s["id"] for s in ct.STEPS}
    assert _step(trace, "load_inputs")["status"] == "error"
    assert _step(trace, "run_cycle")["status"] == "blocked"


def test_list_cycle_investigations_only_returns_runnable():
    rows = ct.list_cycle_investigations(_WS)
    slugs = [r["slug"] for r in rows]
    assert _SLUG in slugs  # the real investigation is listed
    for r in rows:
        assert r["slug"] and r["title"]  # every row is usable in the picker


def test_sandbox_advance_never_touches_real_workspace(tmp_path):
    import shutil
    # copy the fixture so we can assert byte-for-byte it is unchanged
    ws = tmp_path / "ws"
    shutil.copytree(_WS, ws)
    before = {p.relative_to(ws): p.stat().st_mtime_ns
              for p in ws.rglob("*") if p.is_file()}
    out = ct.sandbox_advance(ws, _SLUG, max_runs=2)
    assert out["available"] is True and out["sandboxed"] is True
    after = {p.relative_to(ws): p.stat().st_mtime_ns
             for p in ws.rglob("*") if p.is_file()}
    assert before == after, "sandbox_advance mutated the real workspace"
