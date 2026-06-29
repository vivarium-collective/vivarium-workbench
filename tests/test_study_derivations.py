from vivarium_dashboard.lib import study_derivations as D


def test_conclusion_verdicts_passed_gate_completed_runs_interp_finding():
    spec = {
        "pipeline_gate": {"gate_evaluator": {"result": "passed"}},
        "runs": [{"status": "completed"}, {"status": "complete"}],
        "findings": [{"tier": "interpretation", "statement": "X dominates"}],
        "conclusion_verdicts": {"biological_validation": {"basis": "b1"}},
    }
    cv = D.conclusion_verdicts(spec)
    assert cv["biological_validation"] == {"result": "PASS", "basis": "b1"}
    assert cv["regression_compatibility"]["result"] == "PASS"
    assert cv["explanatory_gain"]["result"] == "PASS"


def test_regression_fail_when_a_run_errored():
    spec = {"runs": [{"status": "completed"}, {"status": "errored"}]}
    assert D.conclusion_verdicts(spec)["regression_compatibility"]["result"] == "FAIL"


def test_regression_partial_when_mixed_and_pending_when_none():
    assert D.conclusion_verdicts({"runs": [{"status": "completed"}, {"status": "queued"}]})["regression_compatibility"]["result"] == "PARTIAL"
    assert D.conclusion_verdicts({})["regression_compatibility"]["result"] == "PENDING"


def test_explanatory_gap_then_partial_then_pass():
    assert D.conclusion_verdicts({})["explanatory_gain"]["result"] == "GAP"
    assert D.conclusion_verdicts({"findings": [{"statement": "plain"}]})["explanatory_gain"]["result"] == "PARTIAL"
    assert D.conclusion_verdicts({"findings": [{"mechanism_origin": "y"}]})["explanatory_gain"]["result"] == "PASS"


def test_bio_failed_and_pending_normalization():
    assert D.conclusion_verdicts({"gate_status": "failed"})["biological_validation"]["result"] == "FAIL"
    assert D.conclusion_verdicts({"gate_status": "needs_calibration"})["biological_validation"]["result"] == "PARTIAL"
    assert D.conclusion_verdicts({})["biological_validation"]["result"] == "PENDING"


def test_verdict_insight_key_metrics():
    assert D.verdict({"gate_status": "passed"}) == "passing"
    assert D.verdict({}) == ""
    assert D.insight({"findings": [{"summary": "the insight"}]}) == "the insight"
    assert D.insight({}) == ""
    km = D.key_metrics({"runs": [{"outcomes": {"t1": {"result": "PASS", "observed": 1.2}}}]})
    assert km == [{"label": "t1", "value": 1.2, "status": "pass"}]


def test_derived_block_has_four_keys():
    b = D.derived_block({"gate_status": "passed"})
    assert set(b) == {"conclusion_verdicts", "verdict", "insight", "key_metrics"}
