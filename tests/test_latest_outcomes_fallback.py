"""_latest_outcomes must show a graded run's outcomes even when an auxiliary
run (no outcomes) is picked as canonical — otherwise the study page renders
"pending" despite real results (e.g. a study with a run.py-sweep run alongside
a graded baseline)."""
from vivarium_workbench.lib.study_spec import _latest_outcomes


def test_falls_back_to_run_with_outcomes():
    spec = {
        "runs": [
            # auxiliary sweep run — completed, carries observables but no outcomes
            {"run_id": "sweep-abc", "status": "completed", "outcomes": {}},
            # graded baseline — the real results
            {"run_id": "baseline", "status": "completed", "outcomes": {
                "t1": {"result": "PASS"},
                "t2": {"result": "FAIL"},
            }},
        ]
    }
    latest, rollup = _latest_outcomes(spec)
    assert set(latest) == {"t1", "t2"}
    assert rollup["PASS"] == 1
    assert rollup["FAIL"] == 1
    assert rollup["total"] == 2


def test_no_outcomes_anywhere_stays_empty():
    spec = {"runs": [{"run_id": "r", "status": "completed", "outcomes": {}}]}
    latest, rollup = _latest_outcomes(spec)
    assert latest == {}
    assert rollup["total"] == 0
