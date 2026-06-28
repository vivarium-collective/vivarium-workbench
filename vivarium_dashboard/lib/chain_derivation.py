"""Derive typed chain nodes from a study's existing result fields (RFC-0002 B2).

Read-time view: lifts each ``conclusion_verdicts[]`` entry into a deterministic
Finding->Evidence->Decision->Conclusion micro-chain (verdict + gate_status drive
the lifecycle states) and each ``findings.entries[]`` into a Finding node. Pure:
no I/O, no clock, no randomness. Every node is stamped ``provenance.actor =
"derived"`` so a lifted chain is never mistaken for a human-gated one. By
construction every emitted chain passes ``investigation_contracts.validate_chain``.
"""
from __future__ import annotations

_DECISION_OUTCOME = {"supported": "accept", "refuted": "reject", "partial": "defer"}
_EVIDENCE_STATE = {"accept": "accepted", "reject": "rejected", "defer": "proposed"}


def _prov(slug: str, source: str) -> dict:
    return {"actor": "derived", "agent_id": "chain-derivation", "timestamp": "",
            "source_objects": [f"study/{slug}"],
            "justification": f"derived from study.yaml {source}",
            "tool": "b2/chain-derivation", "commit": ""}


def derive_chain_nodes(study_spec: dict, slug: str) -> dict[str, dict]:
    nodes: dict[str, dict] = {}
    gate = str((study_spec or {}).get("gate_status", "")).strip().lower()
    passed = gate in ("passed", "pass")

    raw = (study_spec or {}).get("conclusion_verdicts")
    verdicts = [v for v in raw if isinstance(v, dict) and str(v.get("claim", "")).strip()] \
        if isinstance(raw, list) else []
    for i, cv in enumerate(verdicts):
        claim = str(cv["claim"]).strip()
        basis = str(cv.get("basis") or claim).strip()
        verdict = str(cv.get("verdict", "")).strip().lower()
        fid, eid = f"finding/derived-{slug}-cv{i}", f"evidence/derived-{slug}-cv{i}"
        did, cid = f"decision/derived-{slug}-cv{i}", f"conclusion/derived-{slug}-cv{i}"
        p = _prov(slug, f"conclusion_verdicts[{i}]")

        outcome = _DECISION_OUTCOME.get(verdict) if passed else None
        ev_state = _EVIDENCE_STATE.get(outcome, "proposed") if outcome else "proposed"

        nodes[fid] = {"id": fid, "type": "finding", "lifecycle_state": "asserted",
                      "owner": "derived", "provenance": p, "validation_status": "derived",
                      "statement": claim, "runs": [f"run/{slug}"]}
        nodes[eid] = {"id": eid, "type": "evidence", "lifecycle_state": ev_state,
                      "owner": "derived", "provenance": p, "validation_status": "derived",
                      "findings": [fid], "hypotheses": [f"hyp/derived-{slug}-cv{i}"],
                      "confidence": 0.0, "statement": basis}
        if outcome:
            nodes[did] = {"id": did, "type": "decision", "lifecycle_state": "recorded",
                          "owner": "derived", "provenance": p, "validation_status": "derived",
                          "evidence": [eid], "outcome": outcome,
                          "rationale": basis, "decided_by": "chain-derivation"}
        if verdict == "supported" and passed:
            nodes[cid] = {"id": cid, "type": "conclusion", "lifecycle_state": "published",
                          "owner": "derived", "provenance": p, "validation_status": "derived",
                          "evidence": [eid], "decisions": [did], "hypotheses": [],
                          "statement": claim}

    findings = (study_spec or {}).get("findings")
    entries = findings.get("entries") if isinstance(findings, dict) else None
    if isinstance(entries, list):
        for j, fe in enumerate(entries):
            if not isinstance(fe, dict):
                continue
            stmt = str(fe.get("description") or fe.get("signature") or "").strip()
            if not stmt:
                continue
            fid = f"finding/derived-{slug}-fe{j}"
            nodes[fid] = {"id": fid, "type": "finding", "lifecycle_state": "asserted",
                          "owner": "derived", "provenance": _prov(slug, f"findings.entries[{j}]"),
                          "validation_status": "derived",
                          "statement": stmt, "runs": [f"run/{slug}"]}
    return nodes
