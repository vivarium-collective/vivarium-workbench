"""RFC-0007 validation panel: records, coverage matrix and epistemic-separation view.

Read-only projection of the discovery cycle's ``validations`` bundle. Nothing is
recomputed via ``run_validators`` and nothing is persisted.
"""
from __future__ import annotations

from vivarium_workbench.lib.cycle_cache import cached_run_cycle

_STATUSES = ("passed", "failed", "inconclusive", "error")


def build_validation_panel(ws_root, inv_slug: str) -> "dict | None":
    """Return the validation panel payload for one investigation, or ``None`` when
    the installed discovery engine predates the validation layer (the panel then
    renders nothing)."""
    try:
        from discovery_engine import discover
        from discovery_engine.reporting.validation import (
            _EXECUTION_ONLY_TYPES,
            VALIDATOR_SCOPES,
            VALIDATOR_TYPES,
            coverage_matrix,
            epistemic_separation_violations,
            stale_records,
        )

        _, inv_spec, studies = discover.load_cycle_inputs(ws_root, inv_slug)
        cyc = cached_run_cycle(discover, inv_spec, studies)
        records = cyc.get("validations") or []

        census = {s: 0 for s in _STATUSES}
        rows = []
        for r in records:
            res = r.get("result") or {}
            st = res.get("execution_status")
            if st is not None:
                census[st] = census.get(st, 0) + 1
            rows.append({
                "type": r.get("type"),
                "scope": r.get("scope"),
                "target": r.get("target"),
                "execution_status": st,
                "scientific_judgment": res.get("scientific_judgment"),
                "findings": r.get("findings"),
                "evidence": r.get("evidence"),
                "criteria": r.get("criteria"),
            })
        judged = sum(census.values())
        return {
            "census": census,
            "pass_rate": (census["passed"] / judged) if judged else 0.0,
            "stale_count": len(stale_records(records, inv_spec.get("validation_snapshot"))),
            "coverage": coverage_matrix(),
            "types": list(VALIDATOR_TYPES),
            "scopes": list(VALIDATOR_SCOPES),
            "execution_only_types": sorted(_EXECUTION_ONLY_TYPES),
            "violations": epistemic_separation_violations(records),
            "records": rows,
        }
    except Exception:
        return None
