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


def test_every_step_carries_a_description_and_gate_field():
    # the panel shows a short per-stage description + gate note; both must be present
    for s in ct.STEPS:
        assert s.get("desc"), f"{s['id']} has no description"
        assert "gate" in s, f"{s['id']} is missing the gate field"
        assert isinstance(s["gate"], str)


def test_summary_exposes_gate_frontier_for_inspection():
    trace = ct.build_cycle_trace(_WS, _SLUG)
    summary = trace.get("summary")
    assert isinstance(summary, dict)
    # the engine ran and produced a gate summary (not the degraded {} fallback)
    assert "frontier" in summary and "blocking" in summary
    assert isinstance(summary["frontier"], list)
    assert isinstance(summary["blocking"], dict)
    for row in summary["frontier"]:
        assert set(row) >= {"kind", "kind_label", "target", "op", "blocking", "human"}
        assert isinstance(row["blocking"], bool)
        assert isinstance(row["human"], bool)
        # a blocking item is one of the three closure-blocking kinds
        if row["blocking"]:
            assert row["kind"] in ct._BLOCKING_KINDS
    # frontier size agrees with the number of items surfaced (capped at 60)
    assert summary.get("frontier_size", 0) >= len(summary["frontier"]) or len(summary["frontier"]) == 60


def test_frontier_rows_carry_study_links_and_action_flags():
    trace = ct.build_cycle_trace(_WS, _SLUG)
    for f in (trace.get("summary") or {}).get("frontier") or []:
        assert {"resolvable", "decision", "studies"} <= set(f)
        assert isinstance(f["studies"], list)
        for s in f["studies"]:
            assert set(s) >= {"slug", "label", "tab"}


def test_resolve_frontier_item_discharges_it(tmp_path):
    import shutil
    ws = tmp_path / "ws"
    shutil.copytree(_WS, ws)
    front = (ct.build_cycle_trace(ws, _SLUG).get("summary") or {}).get("frontier") or []
    resolvable = [f for f in front if f.get("resolvable") and f.get("target")]
    if not resolvable:
        pytest.skip("fixture has no resolvable frontier item")
    it = resolvable[0]
    out = ct.resolve_frontier_item(ws, _SLUG, it["kind"], it["target"], "resolved in test")
    assert out["available"] is True and out["ok"] is True
    assert out["resolution"]["kind"] == it["kind"]
    # the discharged item is gone from the refreshed frontier
    after = (out.get("after", {}).get("summary") or {}).get("frontier") or []
    assert not any(f["kind"] == it["kind"] and f["target"] == it["target"] for f in after)


def test_resolve_rejects_blank_rationale_and_bad_kind(tmp_path):
    import shutil
    ws = tmp_path / "ws"
    shutil.copytree(_WS, ws)
    assert ct.resolve_frontier_item(ws, _SLUG, "open_decision", "x", "   ")["ok"] is False
    assert ct.resolve_frontier_item(ws, _SLUG, "not_a_real_kind", "x", "why")["ok"] is False


def test_ai_steer_degrades_cleanly_without_a_decider(tmp_path, monkeypatch):
    import shutil
    ws = tmp_path / "ws"
    shutil.copytree(_WS, ws)
    monkeypatch.delenv("VIVA_DECIDER_CMD", raising=False)
    decisions = [f for f in (ct.build_cycle_trace(ws, _SLUG).get("summary") or {}).get("frontier") or []
                 if f.get("decision")]
    if not decisions:
        pytest.skip("fixture has no open decision to steer")
    out = ct.ai_steer_decision(ws, _SLUG, decisions[0]["target"])
    assert out["available"] is True and out["ok"] is False and out.get("needs_config") is True


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


def test_apply_cycle_writes_report_and_stays_in_its_workspace(tmp_path):
    import shutil
    ws = tmp_path / "ws"
    shutil.copytree(_WS, ws)
    out = ct.apply_cycle(ws, _SLUG, scope="all")
    assert out["available"] is True
    r = out["report"]
    # a well-formed report regardless of how thin the fixture is
    for k in ("verdicts", "edges", "cycle_state", "skipped", "commit"):
        assert k in r
    assert isinstance(r["verdicts"], list)
    # every written verdict is in the canonical study vocab
    from discovery_engine.discover import VERDICT_VOCAB
    for v in r["verdicts"]:
        assert v["to"] in VERDICT_VOCAB
    # the refreshed trace comes back too
    assert out["after"]["available"] is True


def test_apply_never_writes_an_offvocab_or_human_decision():
    # the mapping must only ever produce decisive, in-vocab verdicts; unresolved/gated skip
    assert set(ct._VERDICT_MAP.values()) <= {"supported", "qualified", "inconclusive", None}


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
