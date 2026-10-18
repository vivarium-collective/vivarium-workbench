"""Tests for the investigation plan-progress board derivation.

The board turns an investigation.yaml ``plan_gates`` block into live gate rows
whose status is DERIVED from member-study state (not the YAML's static status),
mirroring the viva-genesis build_plan_progress rules.
"""
from vivarium_workbench.lib.plan_board import build_plan_board


def _study(gate_status=None, findings=None):
    return {"gate_status": gate_status, "findings": findings or []}


def test_empty_plan_gates_returns_empty():
    assert build_plan_board([], {}) == []
    assert build_plan_board(None, {}) == []


def test_pending_when_no_scaffolded_members():
    gates = [{"id": "D", "name": "Reproduction", "extends": "C",
              "member_studies": [], "pending_studies": [{"plan_no": 33}]}]
    board = build_plan_board(gates, {})
    assert board[0]["status"] == "pending"


def test_in_progress_when_members_exist_prior_passed_not_all_done():
    gates = [{"id": "A", "name": "Chemical emergence", "extends": None,
              "member_studies": ["og-27", "og-28", "og-29"],
              "pending_studies": [],
              "criterion_findings": ["og29-beats-matched-null"]}]
    studies = {
        "og-27": _study(gate_status="passed"),
        "og-28": _study(gate_status="passed"),
        "og-29": _study(gate_status="ran"),   # not yet at Decision
    }
    board = build_plan_board(gates, studies)
    assert board[0]["status"] == "in_progress"


def test_blocked_when_scaffolded_but_prior_not_passed():
    gates = [
        {"id": "A", "name": "A", "extends": None,
         "member_studies": ["og-29"], "pending_studies": [],
         "criterion_findings": ["og29-beats-matched-null"]},
        {"id": "B", "name": "B", "extends": "A",
         "member_studies": ["og-30"], "pending_studies": []},
    ]
    studies = {"og-29": _study(gate_status="ran"), "og-30": _study(gate_status="ran")}
    board = build_plan_board(gates, studies)
    by_id = {g["id"]: g for g in board}
    assert by_id["A"]["status"] == "in_progress"
    assert by_id["B"]["status"] == "blocked"   # prior A not passed


def test_passed_requires_members_passed_criterion_met_no_pending_prior_passed():
    gates = [{"id": "A", "name": "A", "extends": None,
              "member_studies": ["og-29"], "pending_studies": [],
              "criterion_findings": ["og29-beats-matched-null"]}]
    studies = {"og-29": _study(
        gate_status="passed",
        findings=[{"id": "og29-beats-matched-null", "status": "confirms"}])}
    board = build_plan_board(gates, studies)
    assert board[0]["status"] == "passed"


def test_not_passed_when_criterion_finding_unconfirmed():
    gates = [{"id": "A", "name": "A", "extends": None,
              "member_studies": ["og-29"], "pending_studies": [],
              "criterion_findings": ["og29-beats-matched-null"]}]
    studies = {"og-29": _study(
        gate_status="passed",
        findings=[{"id": "og29-beats-matched-null", "status": "partial"}])}
    board = build_plan_board(gates, studies)
    assert board[0]["status"] == "in_progress"   # member passed but criterion not met


def test_full_spine_reproduces_design_phase_states():
    """A in_progress, B/C blocked, D/E pending — the real origins-of-life spine
    in its design phase (helper's verified integration result)."""
    gates = [
        {"id": "A", "extends": None, "member_studies": ["og-27", "og-28", "og-29"],
         "pending_studies": [], "criterion_findings": ["og29-beats-matched-null"]},
        {"id": "B", "extends": "A", "member_studies": ["og-30"],
         "pending_studies": [], "criterion_findings": ["og30-beats-matched-null"]},
        {"id": "C", "extends": "B", "member_studies": ["og-31"],
         "pending_studies": [{"plan_no": 32}]},
        {"id": "D", "extends": "C", "member_studies": [],
         "pending_studies": [{"plan_no": 33}, {"plan_no": 34}]},
        {"id": "E", "extends": "D", "optional": True, "member_studies": [],
         "pending_studies": [{"plan_no": 35}]},
    ]
    studies = {
        "og-27": _study(gate_status="ran"), "og-28": _study(gate_status="ran"),
        "og-29": _study(gate_status="ran"), "og-30": _study(gate_status="ran"),
        "og-31": _study(gate_status="ran"),
    }
    board = {g["id"]: g["status"] for g in build_plan_board(gates, studies)}
    assert board == {"A": "in_progress", "B": "blocked", "C": "blocked",
                     "D": "pending", "E": "pending"}


def test_preserves_gate_metadata_for_render():
    gates = [{"id": "A", "name": "Chemical emergence", "extends": None,
              "member_studies": ["og-29"], "pending_studies": [{"plan_no": 32, "title": "X"}],
              "criterion": "network persists", "criterion_findings": ["f1"]}]
    studies = {"og-29": _study(gate_status="ran")}
    row = build_plan_board(gates, studies)[0]
    assert row["id"] == "A"
    assert row["name"] == "Chemical emergence"
    assert row["criterion"] == "network persists"
    assert row["member_studies"] == ["og-29"]
    assert row["pending_studies"] == [{"plan_no": 32, "title": "X"}]


def test_off_spine_branch_passed_through():
    """An off-spine branch gate (e.g. a tested-and-refuted side-question) carries
    its off_spine flag through to the row so the render can mark it distinctly."""
    gates = [
        {"id": "A1", "name": "Persistence", "extends": None, "member_studies": ["s1"]},
        {"id": "A2", "name": "Autocatalysis", "extends": "A1", "off_spine": True,
         "member_studies": ["s2"]},
    ]
    studies = {"s1": _study(gate_status="passed"), "s2": _study(gate_status="ran")}
    rows = {g["id"]: g for g in build_plan_board(gates, studies)}
    assert rows["A2"]["off_spine"] is True
    assert rows["A1"]["off_spine"] is False
