"""The diagnostic-branch signal fires on PRIMARY-test failure, not any failure.

`roll_up_verdict` returns 'failed' whenever *any* test fails — including a
diagnostic/secondary test while the primary tests pass (a legitimately
*qualified* study). The diagnostic-branch prompt ("primary tests fail → seed a
diagnostic study") must not fire on those, and must not fire on a study whose
authored verdict is a documented terminal *refutation* (the primary failure IS
the recorded conclusion, already diagnosed). It must still fire on a genuine,
undecided primary-test failure.
"""
from __future__ import annotations

import pytest

pytest.importorskip("viva_superpowers.study_verdict")

from vivarium_workbench.lib.needs_attention import _diagnostic_branch_items  # noqa: E402


def _spec(tests, outcomes, **extra):
    spec = {"name": "s", "behavior_tests": tests,
            "runs": [{"name": "r", "status": "completed", "outcomes": outcomes}]}
    spec.update(extra)
    return spec


def test_only_diagnostic_test_failing_does_not_flag():
    spec = _spec(
        [{"name": "p1", "classification": "primary"},
         {"name": "d1", "classification": "diagnostic"}],
        {"p1": {"result": "PASS"}, "d1": {"result": "FAIL"}},
        verdict="qualified",
    )
    assert _diagnostic_branch_items("s", spec) == []


def test_primary_failure_with_documented_refutation_does_not_flag():
    spec = _spec(
        [{"name": "p1", "classification": "primary"}],
        {"p1": {"result": "FAIL"}},
        verdict="refuted",
    )
    assert _diagnostic_branch_items("s", spec) == []


def test_undecided_primary_failure_still_flags():
    spec = _spec(
        [{"name": "p1", "classification": "primary"}],
        {"p1": {"result": "FAIL"}},
    )  # no authored verdict — a genuine undiagnosed primary failure
    items = _diagnostic_branch_items("s", spec)
    assert len(items) == 1 and items[0]["kind"] == "diagnostic_branch_needed"


def test_already_seeded_still_suppresses():
    spec = _spec(
        [{"name": "p1", "classification": "primary"}],
        {"p1": {"result": "FAIL"}},
        conclusion_logic={"if_primary_tests_fail": {"diagnose": [{"seeded_study": "s-diag"}]}},
    )
    assert _diagnostic_branch_items("s", spec) == []
