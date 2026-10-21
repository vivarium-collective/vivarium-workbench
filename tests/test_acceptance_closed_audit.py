"""acceptance-closed: an investigation is *closed* when every acceptance
criterion is met by a supporting study verdict — the inquiry-level analogue of
a ground composite (every required hole filled). This is the ladder's top rung,
graded as an L5 audit check on each investigation block, computed fresh via the
canonical `roll_up_acceptance` evaluator. Folds into the workspace-wide Audit
tab the same way the observable-contract check folds into study blocks.
"""
from __future__ import annotations

from vivarium_workbench.lib import audit_views


# --- the pure mapping: roll_up_acceptance result -> graded check -------------

def test_passing_is_closed_pass():
    rolled = {"verdict_status": "passing",
              "criteria": [{"study": "a", "behavior": "b", "result": "passing"}],
              "unmet": []}
    chk = audit_views._grade_acceptance(rolled)
    assert chk["level"] == "L5" and chk["name"] == "acceptance-closed"
    assert chk["status"] == "pass" and chk["tier"] == "soft"


def test_failing_is_a_fail_and_names_unmet():
    rolled = {"verdict_status": "failing",
              "criteria": [{"study": "a", "behavior": "grows"}],
              "unmet": [{"study": "a", "behavior": "grows", "result": "failing"}]}
    chk = audit_views._grade_acceptance(rolled)
    assert chk["status"] == "fail"
    assert "a" in chk["detail"] and "grows" in chk["detail"]


def test_in_progress_is_a_warn():
    rolled = {"verdict_status": "in-progress",
              "criteria": [{"study": "a", "behavior": "b"}, {"study": "c", "behavior": "d"}],
              "unmet": [{"study": "c", "behavior": "d", "result": "in-progress"}]}
    assert audit_views._grade_acceptance(rolled)["status"] == "warn"


def test_passing_with_caveats_is_a_warn():
    rolled = {"verdict_status": "passing-with-caveats",
              "criteria": [{"study": "a", "behavior": "b"}], "unmet": []}
    assert audit_views._grade_acceptance(rolled)["status"] == "warn"


def test_no_criteria_skips():
    rolled = {"verdict_status": "in-progress", "criteria": [], "unmet": []}
    assert audit_views._grade_acceptance(rolled) is None


def test_non_dict_skips():
    assert audit_views._grade_acceptance(None) is None


# --- the fold into build_audit's investigation blocks -----------------------

def _stub_workspace_audit(monkeypatch, investigations):
    import viva_superpowers.study_audit as sa

    class _FakeReport:
        def as_dict(self):
            return {"studies": [], "investigations": investigations,
                    "summary": {"n_studies": 0,
                                "n_investigations": len(investigations),
                                "hard_failures": 0}}

    monkeypatch.setattr(sa, "audit_workspace",
                        lambda ws_root, extra_packages=None: _FakeReport())


def test_build_audit_folds_acceptance_closed_into_investigation_block(tmp_path, monkeypatch):
    _stub_workspace_audit(monkeypatch, [{"slug": "inv1", "worst": "pass", "checks": [
        {"level": "L0", "name": "structure", "status": "pass", "tier": "hard"}]}])
    monkeypatch.setattr(
        audit_views, "_acceptance_closed_check",
        lambda ws_root, slug: {"level": "L5", "name": "acceptance-closed",
                               "status": "fail", "tier": "soft", "detail": "x unmet"})
    report, code = audit_views.build_audit(tmp_path)
    assert code == 200
    blk = report["investigations"][0]
    assert "acceptance-closed" in [c["name"] for c in blk["checks"]]
    assert blk["worst"] == "fail"


def test_build_audit_skips_investigation_with_no_check(tmp_path, monkeypatch):
    _stub_workspace_audit(monkeypatch, [{"slug": "inv1", "worst": "pass", "checks": []}])
    monkeypatch.setattr(audit_views, "_acceptance_closed_check",
                        lambda ws_root, slug: None)
    report, _ = audit_views.build_audit(tmp_path)
    assert [c["name"] for c in report["investigations"][0]["checks"]] == []
