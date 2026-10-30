from vivarium_workbench.lib.study_evidence import StudyEvidence
from vivarium_workbench.lib.study_ontology import derive_execution_status, derive_verdict


def _ev(execution):
    return StudyEvidence(execution=execution, gate="passed", evaluation="evaluated",
                         tests_passed=2, tests_total=2, n_runs=5)


def test_evidence_promotes_stale_not_run():
    spec = {"simulation_status": "not_run", "status": "planned"}
    assert derive_execution_status(spec, evidence=_ev("completed")) == "completed"


def test_authored_verdict_never_overridden_by_evidence():
    spec = {"verdict": "refuted"}
    assert derive_verdict(spec, evidence=_ev("completed")) == "refuted"


def test_verdict_not_fabricated_from_evidence():
    assert derive_verdict({}, evidence=_ev("completed")) is None


def test_no_evidence_preserves_legacy_behavior():
    spec = {"simulation_status": "not_run", "status": "planned"}
    assert derive_execution_status(spec) == "planned"


def test_not_run_evidence_falls_through_to_authored():
    assert derive_execution_status({"execution_status": "completed"}, evidence=_ev("not_run")) == "completed"
    assert derive_execution_status({"status": "planned"}, evidence=_ev("not_run")) == "planned"


def test_failed_evidence_overrides_stored_not_run():
    spec = {"simulation_status": "not_run", "status": "planned"}
    assert derive_execution_status(spec, evidence=_ev("failed")) == "failed"


def test_running_evidence_wins():
    assert derive_execution_status({"execution_status": "planned"}, evidence=_ev("running")) == "running"
