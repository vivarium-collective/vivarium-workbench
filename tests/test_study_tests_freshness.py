"""Phase 2b: the Tests/Audit view resolves a computed-grade's freshness SERVER-SIDE (a baked
fresh|stale flag), so the dashboard + the published bundle render 'stale -> re-grade' instead of a
confidently-wrong stored grade. `_run_freshness` is the resolver seam; the Phase-2 discovery_engine
grader_freshness does the actual comparison (stubbed here)."""
from __future__ import annotations

import vivarium_workbench.lib.study_tests as st


def test_run_freshness_none_when_resolver_unavailable(monkeypatch):
    monkeypatch.setattr(st, "_grade_freshness", None)
    assert st._run_freshness({"_grader": {"version": "3-freshness"}}) is None


def test_run_freshness_none_when_ungraded(monkeypatch):
    monkeypatch.setattr(st, "_grade_freshness", lambda g: "fresh")
    assert st._run_freshness({}) is None                 # no _grader
    assert st._run_freshness(None) is None
    assert st._run_freshness({"T": {"result": "FAIL"}}) is None   # grades but no stamp


def test_run_freshness_delegates_to_grade_freshness(monkeypatch):
    seen = {}
    def _fake(grader):
        seen["grader"] = grader
        return "stale" if grader.get("version") == "1-old" else "fresh"
    monkeypatch.setattr(st, "_grade_freshness", _fake)
    assert st._run_freshness({"_grader": {"version": "1-old"}}) == "stale"
    assert st._run_freshness({"_grader": {"version": "3-freshness"}}) == "fresh"
    assert seen["grader"] == {"version": "3-freshness"}   # the stamp is what's passed through
