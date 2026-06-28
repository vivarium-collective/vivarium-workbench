"""Author endpoints for the evidence chain (RFC-0002 Phase B). Each writes an
addressable node file atomically, then emits its event (drift guard)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from vivarium_dashboard.lib.atomic_io import atomic_write_text
from vivarium_dashboard.lib.event_log import emit_event
from vivarium_dashboard.lib.node_store import study_dir
from investigation_contracts import make_core
from investigation_contracts.lifecycle import initial_state


def _prov(actor: str, agent_id: str, srcs: list[str], why: str, tool: str) -> dict:
    return {"actor": actor, "agent_id": agent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_objects": list(srcs), "justification": why, "tool": tool, "commit": ""}


def _write_node(sdir: Path, subdir: str, fid: str, node: dict) -> None:
    d = sdir / subdir
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_text(d / f"{fid}.yaml", yaml.safe_dump(node, sort_keys=False))


def create_evidence(ws_root: Path, body: dict) -> tuple[dict, int]:
    slug = (body.get("study") or "").strip()
    findings = body.get("findings") or []
    hyps = [h for h in (body.get("hypotheses") or []) if str(h).strip()]
    sdir = study_dir(ws_root, slug)
    if sdir is None:
        return {"error": f"study not found: {slug}"}, 404
    if len(findings) < 1 or len(hyps) < 1:
        return {"error": "evidence requires >=1 finding and >=1 hypothesis"}, 400
    eid = "e" + uuid.uuid4().hex[:10]
    prov = _prov("agentic", body.get("agent_id", "unknown"), findings,
                 "evidence linked via /api/evidence", "api/evidence")
    node = {"id": f"evidence/{eid}", "type": "evidence",
            "lifecycle_state": initial_state("evidence"), "owner": "shared",
            "provenance": prov, "validation_status": "ok",
            "findings": list(findings), "hypotheses": list(hyps),
            "confidence": float(body.get("confidence") or 0.0),
            "statement": body.get("statement", "")}
    if not make_core().check("evidence", node):
        return {"error": "constructed evidence node failed contract validation"}, 500
    _write_node(sdir, "evidence", eid, node)
    event_id = emit_event(ws_root, type="EvidenceLinked", subject=f"evidence/{eid}",
                          transition={"from": "", "to": initial_state("evidence")},
                          actor="agentic", provenance=prov,
                          payload={"study": slug, "evidence_id": eid})
    return {"evidence_id": eid, "event_id": event_id}, 200
