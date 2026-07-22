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
    raw_list = raw if isinstance(raw, list) else []
    i = 0  # filtered index -> drives node ids (stable, matches tests)
    for raw_j, cv in enumerate(raw_list):
        if not (isinstance(cv, dict) and str(cv.get("claim", "")).strip()):
            continue
        claim = str(cv["claim"]).strip()
        basis = str(cv.get("basis") or claim).strip()
        verdict = str(cv.get("verdict", "")).strip().lower()
        fid, eid = f"finding/derived-{slug}-cv{i}", f"evidence/derived-{slug}-cv{i}"
        did, cid = f"decision/derived-{slug}-cv{i}", f"conclusion/derived-{slug}-cv{i}"
        src = f"conclusion_verdicts[{raw_j}]"  # raw index -> provenance justification

        outcome = _DECISION_OUTCOME.get(verdict) if passed else None
        ev_state = _EVIDENCE_STATE.get(outcome, "proposed") if outcome else "proposed"

        nodes[fid] = {"id": fid, "type": "finding", "lifecycle_state": "asserted",
                      "owner": "derived", "provenance": _prov(slug, src),
                      "validation_status": "derived",
                      "statement": claim, "runs": [f"run/{slug}"]}
        nodes[eid] = {"id": eid, "type": "evidence", "lifecycle_state": ev_state,
                      "owner": "derived", "provenance": _prov(slug, src),
                      "validation_status": "derived",
                      "findings": [fid], "hypotheses": [f"hyp/derived-{slug}-cv{i}"],
                      "confidence": 0.0, "statement": basis}
        if outcome:
            nodes[did] = {"id": did, "type": "decision", "lifecycle_state": "recorded",
                          "owner": "derived", "provenance": _prov(slug, src),
                          "validation_status": "derived",
                          "evidence": [eid], "outcome": outcome,
                          "rationale": basis, "decided_by": "chain-derivation"}
        if verdict == "supported" and passed:
            nodes[cid] = {"id": cid, "type": "conclusion", "lifecycle_state": "published",
                          "owner": "derived", "provenance": _prov(slug, src),
                          "validation_status": "derived",
                          "evidence": [eid], "decisions": [did], "hypotheses": [],
                          "statement": claim}
        i += 1

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

    # v4 studies carry findings as a LIST of {statement, status, evidence:{...}}
    # (not the {entries:[...]} dict shape above). Lift each into the SAME
    # Finding->Evidence->Decision(->Conclusion) micro-chain the conclusion_verdicts
    # path emits — sourced from the finding's status (confirms/contradicts/partial)
    # — so these investigations' cards populate the Evidence chain like the others.
    if isinstance(findings, list):
        _STATUS_VERDICT = {"confirms": "supported", "contradicts": "refuted",
                           "partial": "partial", "inconclusive": "partial"}
        k = 0
        for raw_j, fe in enumerate(findings):
            if not (isinstance(fe, dict) and str(fe.get("statement", "")).strip()):
                continue
            claim = str(fe["statement"]).strip()
            ev = fe.get("evidence") if isinstance(fe.get("evidence"), dict) else {}
            basis = str(ev.get("observed") or claim).strip()
            verdict = _STATUS_VERDICT.get(str(fe.get("status", "")).strip().lower(), "")
            fid, eid = f"finding/derived-{slug}-fl{k}", f"evidence/derived-{slug}-fl{k}"
            did, cid = f"decision/derived-{slug}-fl{k}", f"conclusion/derived-{slug}-fl{k}"
            src = f"findings[{raw_j}]"
            # The finding's status IS the verdict for these studies (they use
            # confidence/status rather than a separate gate), so derive the
            # decision directly from it rather than gating on gate_status.
            outcome = _DECISION_OUTCOME.get(verdict)
            ev_state = _EVIDENCE_STATE.get(outcome, "proposed") if outcome else "proposed"
            nodes[fid] = {"id": fid, "type": "finding", "lifecycle_state": "asserted",
                          "owner": "derived", "provenance": _prov(slug, src),
                          "validation_status": "derived",
                          "statement": claim, "runs": [f"run/{slug}"]}
            nodes[eid] = {"id": eid, "type": "evidence", "lifecycle_state": ev_state,
                          "owner": "derived", "provenance": _prov(slug, src),
                          "validation_status": "derived",
                          "findings": [fid], "hypotheses": [f"hyp/derived-{slug}-fl{k}"],
                          "confidence": 0.0, "statement": basis}
            if outcome:
                nodes[did] = {"id": did, "type": "decision", "lifecycle_state": "recorded",
                              "owner": "derived", "provenance": _prov(slug, src),
                              "validation_status": "derived",
                              "evidence": [eid], "outcome": outcome,
                              "rationale": basis, "decided_by": "chain-derivation"}
            if verdict == "supported":
                nodes[cid] = {"id": cid, "type": "conclusion", "lifecycle_state": "published",
                              "owner": "derived", "provenance": _prov(slug, src),
                              "validation_status": "derived",
                              "evidence": [eid], "decisions": [did], "hypotheses": [],
                              "statement": claim}
            k += 1
    return nodes
