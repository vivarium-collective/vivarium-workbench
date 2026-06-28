"""POST /api/finding worker — write a Finding node, then emit FindingCreated."""
from __future__ import annotations

import uuid
from pathlib import Path

import yaml

from vivarium_dashboard.lib.atomic_io import atomic_write_text
from vivarium_dashboard.lib.event_log import emit_event
from investigation_contracts.lifecycle import initial_state


def _study_dir(ws_root: Path, slug: str) -> Path | None:
    try:
        from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
        wp = WorkspacePaths.load(ws_root)
        d = wp.studies / slug
        if d.is_dir():
            return d
    except Exception:  # noqa: BLE001
        pass
    d = Path(ws_root) / "studies" / slug
    return d if d.is_dir() else None


def create_finding(ws_root: Path, body: dict) -> tuple[dict, int]:
    slug = (body.get("study") or "").strip()
    statement = (body.get("statement") or "").strip()
    runs = body.get("runs") or []
    if not slug or not statement:
        return {"error": "study and statement are required"}, 400
    sdir = _study_dir(ws_root, slug)
    if sdir is None:
        return {"error": f"study not found: {slug}"}, 404

    fid = "f" + uuid.uuid4().hex[:10]
    prov = {"actor": "agentic", "agent_id": body.get("agent_id", "unknown"),
            "timestamp": "", "source_objects": list(runs),
            "justification": "finding proposed via /api/finding", "tool": "api/finding", "commit": ""}
    node = {
        "id": f"finding/{fid}", "type": "finding",
        "lifecycle_state": initial_state("finding"), "owner": "shared",
        "provenance": prov, "validation_status": "ok",
        "statement": statement, "runs": list(runs),
    }
    # 1) commit the state write (atomic)
    fdir = sdir / "findings"
    fdir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(fdir / f"{fid}.yaml", yaml.safe_dump(node, sort_keys=False))
    # 2) emit AFTER the commit
    event_id = emit_event(
        ws_root, type="FindingCreated", subject=f"finding/{fid}",
        transition={"from": "", "to": initial_state("finding")}, actor="agentic",
        provenance=prov, payload={"study": slug, "finding_id": fid, "statement": statement},
    )
    return {"finding_id": fid, "event_id": event_id}, 200
