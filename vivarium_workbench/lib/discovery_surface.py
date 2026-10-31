"""Read-only surfaces over discovery-engine outputs the UI previously ignored:
the per-cycle ``discovery_delta`` and the evidence-integrity ledger."""
from __future__ import annotations

from pathlib import Path


def _inputs(ws_root, slug: str):
    from discovery_engine import discover
    _, inv_spec, studies = discover.load_cycle_inputs(Path(ws_root), slug)
    try:
        runs_by = discover._load_runs_by_study(Path(ws_root), slug, studies)
    except Exception:
        runs_by = {}
    return inv_spec, studies, runs_by


def _ev_state(inv_spec, studies, runs_by):
    from discovery_engine import evidence_integrity
    return evidence_integrity.reconcile_evidence_state(inv_spec, studies, runs_by)


def build_discovery_delta(ws_root, slug: str) -> dict:
    try:
        from discovery_engine import evaluation
        inv_spec, studies, runs_by = _inputs(ws_root, slug)
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    prev = ((inv_spec or {}).get("cycle_state") or {}).get("evaluation_snapshot")
    ev = _ev_state(inv_spec, studies, runs_by)
    d = evaluation.discovery_delta(inv_spec, studies, ev, prev)
    if not isinstance(prev, dict):
        d["unchanged"] = True
        d["first_cycle"] = True
    else:
        d["first_cycle"] = False
    d["available"] = True
    return d


def build_evidence_integrity(ws_root, slug: str) -> dict:
    try:
        inv_spec, studies, runs_by = _inputs(ws_root, slug)
        ev = _ev_state(inv_spec, studies, runs_by)
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    ledger = [{
        "study": r.get("study"), "finding": r.get("finding"), "measure": r.get("measure"),
        "observed": r.get("observed"), "status": r.get("status"), "reason": r.get("reason"),
        "run_ids": r.get("run_ids") or [],
    } for r in (ev.get("ledger") or [])]
    return {"available": True, "ledger": ledger,
            "summary": ev.get("summary") or {}, "by_study": ev.get("by_study") or {}}
