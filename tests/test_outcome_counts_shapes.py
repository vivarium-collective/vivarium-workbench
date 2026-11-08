"""`_outcome_counts` must tolerate every shape stored under a run's ``outcomes``.

Regression: viva-biomodels stores ``outcomes`` as a dict of {test-name: "pass"}
(bare result STRINGS, not {"result": ...} dicts). The old code called
``o.get("result")`` on each value and raised ``AttributeError: 'str' object has
no attribute 'get'`` inside ``render_investigation_report`` — which publish.py
swallows with a bare ``except: continue``, silently dropping the investigation
report HTML from the published read-only workbench bundle.
"""
from vivarium_workbench.lib.investigation_report import _outcome_counts


def test_dict_of_result_dicts():
    spec = {"runs": [{"outcomes": {"a": {"result": "PASS"}, "b": {"result": "fail"}}}]}
    assert _outcome_counts(spec) == {"passed": 1, "skipped": 0, "failed": 1}


def test_dict_of_bare_result_strings():
    # The viva-biomodels shape that used to crash the renderer.
    spec = {"runs": [{"outcomes": {"a": "pass", "b": "pass", "c": "fail", "d": "skip"}}]}
    assert _outcome_counts(spec) == {"passed": 2, "skipped": 1, "failed": 1}


def test_list_of_outcomes_mixed():
    spec = {"runs": [{"outcomes": ["pass", {"result": "FAIL"}, "N/A"]}]}
    assert _outcome_counts(spec) == {"passed": 1, "skipped": 1, "failed": 1}


def test_empty_and_missing_are_zero():
    assert _outcome_counts({}) == {"passed": 0, "skipped": 0, "failed": 0}
    assert _outcome_counts({"runs": [{"outcomes": {}}, {}]}) == {
        "passed": 0, "skipped": 0, "failed": 0,
    }
