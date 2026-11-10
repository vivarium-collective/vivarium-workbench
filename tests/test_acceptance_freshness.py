"""Phase 2b pt3: the acceptance roll-up carries a server-side fresh|stale flag — stale if ANY member
study's computed grade is behind the current grader, so the Audit tab shows 'stale -> re-grade'
instead of trusting a roll-up over outdated grades. The Phase-2 grade_freshness does the comparison
(stubbed here via a fake discovery_engine.grader_freshness module)."""
from __future__ import annotations

import sys
import types

import pytest

from vivarium_workbench.lib.report_views import _acceptance_freshness


@pytest.fixture
def stub_grade_freshness(monkeypatch):
    def _install(fn):
        mod = types.ModuleType("discovery_engine.grader_freshness")
        mod.grade_freshness = fn
        monkeypatch.setitem(sys.modules, "discovery_engine.grader_freshness", mod)
    return _install


def _study(*versions):
    return {"runs": [{"computed_outcomes": {"_grader": {"version": v}}} for v in versions]}


def test_none_when_nothing_graded(stub_grade_freshness):
    stub_grade_freshness(lambda g: "fresh")
    assert _acceptance_freshness({"s1": {"runs": [{"outcomes": {"t": {"result": "FAIL"}}}]}}) is None
    assert _acceptance_freshness({}) is None


def test_fresh_when_all_members_current(stub_grade_freshness):
    stub_grade_freshness(lambda g: "fresh")
    assert _acceptance_freshness({"s1": _study("3-freshness"), "s2": _study("3-freshness")}) == "fresh"


def test_stale_when_any_member_behind(stub_grade_freshness):
    stub_grade_freshness(lambda g: "stale" if g.get("version") == "1-old" else "fresh")
    # s2 carries an old grade -> the whole roll-up is stale
    assert _acceptance_freshness({"s1": _study("3-freshness"), "s2": _study("1-old")}) == "stale"


def test_none_when_resolver_unavailable(monkeypatch):
    # ensure the real (absent) module is used -> ImportError path -> None
    monkeypatch.delitem(sys.modules, "discovery_engine.grader_freshness", raising=False)
    import builtins
    real_import = builtins.__import__
    def _blocked(name, *a, **k):
        if name == "discovery_engine.grader_freshness":
            raise ImportError("blocked")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert _acceptance_freshness({"s1": _study("3-freshness")}) is None
