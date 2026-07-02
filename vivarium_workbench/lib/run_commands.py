"""Canonical `vdash …` command strings for a study — the SINGLE source of truth.

Every advertising surface (single-study report, investigation SPA, study-detail
page) and the CLI's own help/examples consume these, so the commands shown to a
reviewer can never drift from what the CLI actually accepts.
"""

from __future__ import annotations


def study_run_commands(spec: dict, slug: str) -> dict:
    """Build the run-command strings for one study spec.

    Returns ``{"baseline", "variants": [{name, cmd}], "simulations":
    [{name, cmd}], "rerun_hint"}``. Pure; tolerant of missing sections.
    """
    base = f"vdash run study {slug}"
    conds = spec.get("conditions") or {}
    variants = []
    for v in (conds.get("variants") or []):
        if not isinstance(v, dict):
            continue
        name = v.get("name")
        if not name:
            continue
        variants.append({"name": name, "cmd": f"{base} --variant {name}"})
    simulations = []
    for s in (spec.get("simulation_set") or []):
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if not name:
            continue
        simulations.append({"name": name, "cmd": base})
    return {
        "baseline": base,
        "variants": variants,
        "simulations": simulations,
        "rerun_hint": "vdash rerun <run-id>",
    }
