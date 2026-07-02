# tests/test_chain_derivation.py
from investigation_contracts import validate_chain
from vivarium_workbench.lib.chain_derivation import derive_chain_nodes


def _cv(study_extra=None, verdicts=None):
    spec = {"name": "s1", "gate_status": "passed"}
    if study_extra:
        spec.update(study_extra)
    if verdicts is not None:
        spec["conclusion_verdicts"] = verdicts
    return spec


def test_supported_passed_full_valid_chain():
    spec = _cv(verdicts=[{"claim": "X dominates", "verdict": "supported",
                          "basis": "Sobol 0.97"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert set(nodes) == {
        "finding/derived-s1-cv0", "evidence/derived-s1-cv0",
        "decision/derived-s1-cv0", "conclusion/derived-s1-cv0"}
    f = nodes["finding/derived-s1-cv0"]
    e = nodes["evidence/derived-s1-cv0"]
    d = nodes["decision/derived-s1-cv0"]
    c = nodes["conclusion/derived-s1-cv0"]
    assert f["type"] == "finding" and f["lifecycle_state"] == "asserted"
    assert f["statement"] == "X dominates" and len(f["runs"]) >= 1
    assert e["type"] == "evidence" and e["lifecycle_state"] == "accepted"
    assert e["statement"] == "Sobol 0.97"
    assert e["findings"] == ["finding/derived-s1-cv0"] and len(e["hypotheses"]) >= 1
    assert d["type"] == "decision" and d["outcome"] == "accept"
    assert d["evidence"] == ["evidence/derived-s1-cv0"]
    assert c["type"] == "conclusion" and c["lifecycle_state"] == "published"
    assert c["evidence"] == ["evidence/derived-s1-cv0"]
    assert c["decisions"] == ["decision/derived-s1-cv0"]
    assert validate_chain(nodes) == []  # sound


def test_refuted_passed_no_conclusion_valid():
    spec = _cv(verdicts=[{"claim": "Y holds", "verdict": "refuted", "basis": "b"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert "conclusion/derived-s1-cv0" not in nodes
    assert nodes["evidence/derived-s1-cv0"]["lifecycle_state"] == "rejected"
    assert nodes["decision/derived-s1-cv0"]["outcome"] == "reject"
    assert validate_chain(nodes) == []


def test_partial_passed_defer_no_conclusion():
    spec = _cv(verdicts=[{"claim": "Z partly", "verdict": "partial", "basis": "b"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert "conclusion/derived-s1-cv0" not in nodes
    assert nodes["decision/derived-s1-cv0"]["outcome"] == "defer"
    assert nodes["evidence/derived-s1-cv0"]["lifecycle_state"] == "proposed"
    assert validate_chain(nodes) == []


def test_not_passed_gate_proposed_no_decision():
    spec = _cv({"gate_status": "pending"},
               verdicts=[{"claim": "W maybe", "verdict": "supported", "basis": "b"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert set(nodes) == {"finding/derived-s1-cv0", "evidence/derived-s1-cv0"}
    assert nodes["evidence/derived-s1-cv0"]["lifecycle_state"] == "proposed"
    assert validate_chain(nodes) == []


def test_multiple_verdicts_distinct_chains():
    spec = _cv(verdicts=[
        {"claim": "A", "verdict": "supported", "basis": "ba"},
        {"claim": "B", "verdict": "supported", "basis": "bb"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert "conclusion/derived-s1-cv0" in nodes
    assert "conclusion/derived-s1-cv1" in nodes
    assert nodes["finding/derived-s1-cv1"]["statement"] == "B"


def test_findings_entries_lift_to_findings():
    spec = {"name": "s1", "gate_status": "pending",
            "findings": {"entries": [
                {"signature": "sig-1", "description": "a transport gap"}]}}
    nodes = derive_chain_nodes(spec, "s1")
    assert "finding/derived-s1-fe0" in nodes
    f = nodes["finding/derived-s1-fe0"]
    assert f["type"] == "finding" and f["statement"] == "a transport gap"
    assert validate_chain({k: v for k, v in nodes.items() if v["type"] == "finding"}) == []


def test_no_sources_empty():
    assert derive_chain_nodes({"name": "s1", "gate_status": "passed"}, "s1") == {}


def test_missing_basis_falls_back_to_claim():
    spec = _cv(verdicts=[{"claim": "claimtext", "verdict": "supported"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert nodes["evidence/derived-s1-cv0"]["statement"] == "claimtext"


def test_all_nodes_marked_derived():
    spec = _cv(verdicts=[{"claim": "X", "verdict": "supported", "basis": "b"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert all(n["provenance"]["actor"] == "derived" for n in nodes.values())


def test_skips_empty_claim_and_tolerates_non_list():
    assert derive_chain_nodes({"conclusion_verdicts": "garbage"}, "s1") == {}
    nodes = derive_chain_nodes(
        {"gate_status": "passed",
         "conclusion_verdicts": [{"claim": "  ", "verdict": "supported"},
                                 {"claim": "real", "verdict": "supported", "basis": "b"}]}, "s1")
    assert set(k.split("-cv")[-1] for k in nodes) == {"0"}  # only cv index 0 (the real one)
    assert validate_chain(nodes) == []


def test_deterministic():
    spec = _cv(verdicts=[{"claim": "X", "verdict": "supported", "basis": "b"}])
    assert derive_chain_nodes(spec, "s1") == derive_chain_nodes(spec, "s1")
