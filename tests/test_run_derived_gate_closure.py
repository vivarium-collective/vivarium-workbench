"""Investigation-level markings are run-aware: gate/acceptance status, closure +
verdict_status, and the Decisions badge derive from the discovery engine when not
authored (flagged *_source="derived"); authored values still win."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("discovery_engine")

from vivarium_workbench.lib.investigation_status import build_iset_summary  # noqa: E402
from vivarium_workbench.lib.investigations_index import build_investigations  # noqa: E402
from vivarium_workbench.lib.report_views import build_iset_detail  # noqa: E402

_TESTS = [{"name": "t1"}, {"name": "t2"}]


def _graded(result="PASS"):
    return [{"status": "completed", "timestamp": "2026-01-01",
             "outcomes": {"t1": {"result": result}, "t2": {"result": "PASS"}}}]


def _study(ws: Path, name: str, **fields) -> None:
    p = ws / "studies" / name / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(
        {"schema_version": 3, "name": name,
         "baseline": [{"composite": "x", "name": "b"}], **fields}, sort_keys=False))


def _inv(ws: Path, name: str, **fields) -> None:
    p = ws / "investigations" / name / "investigation.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"name": name, "title": name, **fields}, sort_keys=False))


def _summary(ws, name):
    rows = build_iset_summary(ws, study_has_runs=lambda *a, **k: False)
    return next(r for r in rows if r["name"] == name)


def _passing_ws(tmp_path, **inv_fields):
    ws = tmp_path / "ws"
    _study(ws, "s1", behavior_tests=_TESTS, runs=_graded())
    _study(ws, "s2", behavior_tests=_TESTS, runs=_graded())
    _inv(ws, "inv", studies=["s1", "s2"],
         acceptance_criteria=[{"study": "s1", "behavior": "t1"}], **inv_fields)
    return ws


# (a) closure + verdict_status ------------------------------------------------

def test_all_pass_derives_closed_and_supported(tmp_path):
    ws = _passing_ws(tmp_path, executive={"verdict_status": "in-progress"})
    d = build_iset_detail(ws, "inv")
    assert d["closure"]["closed"] is True
    assert d["closure"]["source"] == "derived"
    assert d["executive"]["verdict_status"] == "passing"       # was in-progress
    assert d["executive"]["verdict_status_source"] == "derived"
    s = _summary(ws, "inv")
    assert (s["closed"], s["closure_source"]) == (True, "derived")
    assert (s["verdict_status"], s["verdict_status_source"]) == ("passing", "derived")


def test_unmet_criterion_stays_open(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "s1", behavior_tests=_TESTS, runs=_graded("FAIL"))
    _inv(ws, "inv", studies=["s1"],
         acceptance_criteria=[{"study": "s1", "behavior": "t1"}])
    d = build_iset_detail(ws, "inv")
    assert d["closure"]["closed"] is False
    assert d["executive"]["verdict_status_source"] == "derived"
    assert d["executive"]["verdict_status"] == "failing"


def test_ungraded_members_derive_nothing(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "s1")
    _inv(ws, "inv", studies=["s1"], executive={"verdict_status": "in-progress"})
    d = build_iset_detail(ws, "inv")
    assert d["closure"]["closed"] is False
    assert d["closure"]["source"] is None                      # nothing graded
    assert d["executive"]["verdict_status"] == "in-progress"
    assert d["executive"].get("verdict_status_source") == "authored"


def test_authored_closed_and_authored_verdict_win(tmp_path):
    ws = _passing_ws(tmp_path, status="closed",
                     executive={"verdict_status": "failing"})
    d = build_iset_detail(ws, "inv")
    assert (d["closure"]["closed"], d["closure"]["source"]) == (True, "authored")
    assert d["executive"]["verdict_status"] == "failing"
    assert d["executive"]["verdict_status_source"] == "authored"
    s = _summary(ws, "inv")
    assert (s["closed"], s["closure_source"]) == (True, "authored")


def test_authored_open_wins_over_derived_closed(tmp_path):
    ws = _passing_ws(tmp_path, executive={"closure": "open"})
    d = build_iset_detail(ws, "inv")
    assert (d["closure"]["closed"], d["closure"]["source"]) == (False, "authored")


# (b) gate / acceptance -------------------------------------------------------

def test_gate_status_derived_from_graded_acceptance(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "ok", behavior_tests=_TESTS, runs=_graded())
    _study(ws, "auth", behavior_tests=_TESTS, runs=_graded(), gate_status="blocked")
    _study(ws, "none")
    _inv(ws, "inv", studies=["ok", "auth", "none"])
    rows = {r["name"]: r for r in build_investigations(ws)["investigations"]}
    assert (rows["ok"]["gate_status"], rows["ok"]["gate_status_source"]) == ("passed", "derived")
    assert (rows["auth"]["gate_status"], rows["auth"]["gate_status_source"]) == ("blocked", "authored")
    assert rows["none"]["gate_status"] is None
    d = {s["name"]: s for s in build_iset_detail(ws, "inv")["studies"]}
    assert (d["ok"]["gate_status"], d["ok"]["gate_status_source"]) == ("passed", "derived")


def test_plan_board_gate_met_without_authored_gate_status(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "s1", behavior_tests=_TESTS, runs=_graded())
    _inv(ws, "inv", studies=["s1"],
         plan_gates=[{"id": "A", "name": "A", "member_studies": ["s1"]}])
    board = build_iset_detail(ws, "inv")["plan_board"]
    assert board[0]["status"] == "passed"
    assert board[0]["status_source"] == "derived"


def test_plan_board_authored_gate_status_wins(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "s1", behavior_tests=_TESTS, runs=_graded(), gate_status="in_progress")
    _inv(ws, "inv", studies=["s1"],
         plan_gates=[{"id": "A", "name": "A", "member_studies": ["s1"]}])
    board = build_iset_detail(ws, "inv")["plan_board"]
    assert board[0]["status"] == "in_progress"
    assert board[0]["status_source"] == "authored"


# (c) decisions badge ---------------------------------------------------------

def test_cycle_resolved_decision_stops_counting(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "s1")
    _inv(ws, "inv", studies=["s1"],
         executive={"decisions_needed": [{"question": "Q-open?"}, {"question": "Q-done?"}]},
         resolutions=[{"kind": "open_decision", "target": "Q-done?"}])
    ex = build_iset_detail(ws, "inv")["executive"]
    assert len(ex["decisions_needed"]) == 2                    # authored list intact (resolve indices)
    assert ex["decisions_open_count"] == 1
    assert ex["decisions_open_source"] == "derived"
    assert ex["decisions_engine_resolved"] == [1]


def test_unresolved_decisions_all_count(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "s1")
    _inv(ws, "inv", studies=["s1"],
         executive={"decisions_needed": ["a?", {"question": "b?"}]})
    assert build_iset_detail(ws, "inv")["executive"]["decisions_open_count"] == 2


# endpoint smoke --------------------------------------------------------------

def test_endpoints_carry_derived_markings(tmp_path, dashboard_client):
    ws = _passing_ws(tmp_path, executive={"decisions_needed": ["a?"]})
    (ws / "workspace.yaml").write_text("name: test-ws\n")
    c = dashboard_client(workspace=ws)
    det = c.get("/api/investigation/inv").json()
    assert det["closure"]["closed"] is True and det["closure"]["source"] == "derived"
    assert det["executive"]["decisions_open_count"] == 1
    rows = c.get("/api/investigation-summaries").json()
    rows = rows.get("investigations", rows) if isinstance(rows, dict) else rows
    row = next(r for r in rows if r["name"] == "inv")
    assert row["closure_source"] == "derived"
