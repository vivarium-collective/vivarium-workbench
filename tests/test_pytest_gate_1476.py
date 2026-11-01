"""#1476 — the computed gate must count a study's pytest results (last_test_run)
when the study's acceptance surface is its pytest suite rather than YAML
behavior tests.

Before the fix a study whose criteria are pytest files in ``tests/`` (results
recorded in ``last_test_run`` by ``POST /api/study-tests-run``) stayed blocked /
in-progress even after every test passed, because the gate derived pass/fail
ONLY from ``runs[].outcomes`` (graded behavior tests).
"""
from __future__ import annotations

from vivarium_workbench.lib import run_aware_investigation as rai
from vivarium_workbench.lib.investigations_index import _condition_satisfied


# A pytest-only study: a completed run, no gradeable behavior_tests, and a
# recorded pytest run with every test green (the real case: 37/37 passed).
_PYTEST_STUDY = {
    "name": "pytest-study",
    "runs": [{"name": "r1", "status": "completed"}],
    "last_test_run": {"timestamp": "t", "passed": 37, "failed": 0, "skipped": 0,
                      "duration_s": 1.0},
}


def test_pytest_gate_result_pass_and_fail():
    assert rai.pytest_gate_result(_PYTEST_STUDY) == "passed"

    failing = dict(_PYTEST_STUDY,
                   last_test_run={"passed": 20, "failed": 17, "skipped": 0})
    assert rai.pytest_gate_result(failing) == "failed"

    # A run that collected nothing ("no tests directory") never fabricates a pass.
    empty = dict(_PYTEST_STUDY,
                 last_test_run={"passed": 0, "failed": 0, "skipped": 0})
    assert rai.pytest_gate_result(empty) is None

    # No recorded pytest run at all → not a pytest-backed study.
    assert rai.pytest_gate_result({"name": "x", "runs": []}) is None


def test_last_test_run_counts_reads_both_shapes():
    # top-level last_test_run
    assert rai.last_test_run_counts(_PYTEST_STUDY) == (37, 0, 0)
    # tests.last_results (tests: is a config mapping)
    cfg = {"tests": {"pytest_args": [], "last_results": {"passed": 3, "failed": 1, "skipped": 2}}}
    assert rai.last_test_run_counts(cfg) == (3, 1, 2)
    # nothing recorded
    assert rai.last_test_run_counts({"name": "x"}) is None


def test_resolve_gate_status_counts_pytest_when_no_behavior_verdict():
    # THE BUG: 37/37 passed but the gate was (None, None) / stuck blocked.
    assert rai.resolve_gate_status(_PYTEST_STUDY) == ("passed", "derived")

    failing = dict(_PYTEST_STUDY,
                   last_test_run={"passed": 20, "failed": 17, "skipped": 0})
    assert rai.resolve_gate_status(failing) == ("failed", "derived")


def test_resolve_gate_status_authored_still_wins():
    authored = dict(_PYTEST_STUDY, gate_status="failed")
    assert rai.resolve_gate_status(authored) == ("failed", "authored")


def test_resolve_gate_status_real_behavior_fail_not_rescued_by_pytest():
    """A study with a FAILED graded behavior test is not rescued to 'passed' by a
    green pytest run — the behavior-test verdict wins."""
    spec = {
        "name": "mixed",
        "behavior_tests": [{"name": "t",
                            "measure": {"kind": "generation_average", "path": "m"},
                            "pass_if": {"op": "range", "low": 0, "high": 1}}],
        "runs": [{"name": "r1", "status": "completed",
                  "outcomes": {"t": {"result": "FAIL"}}}],
        "last_test_run": {"passed": 5, "failed": 0, "skipped": 0},
    }
    assert rai.resolve_gate_status(spec) == ("failed", "derived")


def test_fold_pytest_into_verdict_upgrades_blocked():
    cgv = {"result": "blocked", "blocked_by": ["t"], "evaluated_by": "code",
           "diverges_from_authored": True}
    folded = rai.fold_pytest_into_verdict(_PYTEST_STUDY, cgv)
    assert folded["result"] == "passed"
    assert folded["evaluated_by"] == "pytest"
    assert folded["blocked_by"] == []
    # with no authored gate_status there is nothing to diverge from
    assert folded["diverges_from_authored"] is False

    # a real behavior-test verdict is never overwritten
    passed_cgv = {"result": "passed"}
    assert rai.fold_pytest_into_verdict(_PYTEST_STUDY, passed_cgv)["result"] == "passed"
    nc = {"result": "needs_calibration"}
    assert rai.fold_pytest_into_verdict(_PYTEST_STUDY, nc)["result"] == "needs_calibration"


def test_fold_recomputes_divergence_against_authored():
    # authored gate_status 'passed' now agrees with the folded pytest 'passed'.
    spec = dict(_PYTEST_STUDY, gate_status="passed")
    cgv = {"result": "blocked", "diverges_from_authored": True}
    assert rai.fold_pytest_into_verdict(spec, cgv)["diverges_from_authored"] is False


def test_condition_satisfied_tests_passed_via_pytest():
    # A downstream prerequisite "tests-passed" unblocks on a green pytest parent.
    assert _condition_satisfied(_PYTEST_STUDY, "tests-passed") is True
    failing = dict(_PYTEST_STUDY,
                   last_test_run={"passed": 1, "failed": 2, "skipped": 0})
    assert _condition_satisfied(failing, "tests-passed") is False


def test_condition_satisfied_behavior_fail_not_rescued_by_green_pytest():
    """A graded behavior-test FAILURE keeps tests-passed False even when the
    study also has a green pytest run — the behavior fail wins here too, as in
    resolve_gate_status / fold_pytest_into_verdict."""
    mixed = {
        "name": "mixed",
        "behavior_tests": [{"name": "t",
                            "measure": {"kind": "generation_average", "path": "m"},
                            "pass_if": {"op": "range", "low": 0, "high": 1}}],
        "runs": [{"name": "r1", "status": "completed",
                  "outcomes": {"t": {"result": "FAIL"}}}],
        "last_test_run": {"passed": 5, "failed": 0, "skipped": 0},
    }
    assert _condition_satisfied(mixed, "tests-passed") is False
