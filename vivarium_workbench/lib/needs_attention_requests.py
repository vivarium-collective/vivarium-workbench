"""Bridge: needs-attention scan items -> typed discovery-cycle GenerationRequests.

The deterministic ``needs_attention`` scan (``lib/needs_attention.py``) detects
what is wrong in an investigation. The discovery cycle (``discovery_engine.phases``)
already knows how to *fix* typed work: a :class:`GenerationRequest` is dispatched
to a filler / auditing agent (a ``/viva-*`` skill or the ``$VIVA_FILLER_CMD``
subprocess) which authors the artifact, and ``apply_result`` writes it back. The
two surfaces were disjoint — this module is the missing seam.

It is a **pure projection**: ``scan_investigation(...)["items"]`` in, a list of
``GenerationRequest`` out, mapped by a small ``scan_kind -> generation_kind``
table that mirrors the frontier-op mapping in
``generation_emit.generation_requests``. No I/O, no clock — the needs-attention
scan is itself the freshness check (a bridged request simply stops being produced
once its underlying item clears), so no persistent proposal state is needed.

Gate policy (the "auto-fix safe kinds in the cycle" choice): the three low-risk
authoring kinds — ``interpret_finding``, ``explain``, ``author_tests`` (plus
``visualize``, whose figures are cheap/derivable) — default to ``auto`` so the
cycle's filler fixes them unattended; the structural branch-points
(``propose_study``, ``acquire_model``, ``resolve_decision``, ``hypothesize``,
``critique``) stay ``review``-gated behind an explicit action. An investigation
that has configured its own ``discovery.gates`` is respected verbatim instead.

Four scan kinds are *deterministic re-runs*, not authored acts — ``param_drift``,
``env_stale``, ``nondeterministic``, ``phantom_observable`` — exactly the class
the engine deliberately excludes from generation. They are returned separately as
re-run descriptors, never as filler requests.
"""

from __future__ import annotations

from typing import Any

# scan_kind -> generation kind. Mirrors generation_emit._OP_TO_KIND semantics.
# A scan kind absent here is either deterministic (see _DETERMINISTIC) or not yet
# routable to an authored fix, and is reported as unmapped rather than guessed.
_SCAN_TO_KIND: dict[str, str] = {
    "uncovered_ac":               "propose_study",
    "stale_finding":              "propose_study",
    "diagnostic_branch_needed":   "propose_study",
    "verdict_divergence":         "interpret_finding",
    "open_feedback":              "interpret_finding",
    "hard_gate":                  "interpret_finding",
    "invariant_regression":       "interpret_finding",
    "narrative_gap":              "explain",
    "test_regression":            "author_tests",
    "confirmatory_not_preregistered": "author_tests",
    "unresolved_composite":       "acquire_model",
    "viz_broken":                 "visualize",
    "viz_missing":                "visualize",
    "viz_stale":                  "visualize",
}

# Deterministic re-runs: a fix is a re-run on the existing spine, not something a
# filler authors. Surfaced as re-run descriptors with a human-readable remedy.
_DETERMINISTIC: dict[str, str] = {
    "param_drift":       "Re-run the study so the applied parameters match its pinned/enforced values.",
    "env_stale":         "Re-run in a current environment (refresh the run's deps/image).",
    "nondeterministic":  "Re-run with a fixed seed, or investigate the source of nondeterminism.",
    "phantom_observable": "Rebuild the composite, or correct the declared observable so it exists in the built structure.",
}

# The artifact contract each kind's filled artifact must satisfy. Mirrors
# generation_emit._PRODUCES (kept local to avoid importing a private engine name).
_PRODUCES: dict[str, str] = {
    "propose_study":     "study.yaml/v4",
    "acquire_model":     "model_spec/v1",
    "author_tests":      "behavior_tests[]",
    "interpret_finding": "finding/v1",
    "resolve_decision":  "decision/v1",
    "explain":           "explanation/v1",
    "visualize":         "viz_spec/v1",
    "hypothesize":       "hypothesis_proposal/v1",
    "critique":          "critique_report/v1",
}

# The "safe" kinds that auto-fix unattended when the investigation has NOT set its
# own gates. interpret_finding is review-by-default in the engine; the operator's
# "auto-fix safe kinds" choice promotes it (and the already-auto explain /
# author_tests / visualize) to auto here.
_NA_SAFE_AUTO: dict[str, str] = {
    "interpret_finding": "auto",
    "explain":           "auto",
    "author_tests":      "auto",
    "visualize":         "auto",
}


def _gate_config(inv_spec: dict | None):
    """The :class:`GateConfig` to stamp bridged requests with.

    An investigation that configured ``discovery.gates`` is respected verbatim.
    Otherwise the needs-attention safe-auto default applies (safe kinds auto, the
    rest review)."""
    from discovery_engine.phases.generation import GateConfig
    gates = ((inv_spec or {}).get("discovery") or {}).get("gates") \
        if isinstance(inv_spec, dict) else None
    if isinstance(gates, dict) and (gates.get("default") or gates.get("by_kind")):
        return GateConfig(default=gates.get("default"),
                          by_kind=dict(gates.get("by_kind") or {}))
    return GateConfig(default="review", by_kind=dict(_NA_SAFE_AUTO))


def bridge_items(items: list[dict], inv_spec: dict | None = None) -> dict:
    """Project needs-attention ``items`` into typed GenerationRequests + re-runs.

    Returns ``{"requests": [GenerationRequest], "reruns": [dict], "unmapped": [dict]}``.
    Pure: no I/O. ``items`` is ``scan_investigation(...)["items"]``; ``inv_spec`` is
    the investigation.yaml mapping (for ``discovery.gates`` + ``skill_map``). A
    scan kind with no authored-fix mapping and no deterministic remedy is reported
    under ``unmapped`` rather than silently dropped.

    Request ids are content-addressed by the engine on ``(kind, target,
    provenance)``; provenance carries ``scan_kind`` + ``ref`` so two distinct
    issues on one study produce distinct, stable ids (idempotent across scans)."""
    import dataclasses
    from discovery_engine.phases.generation import GenerationRequest
    try:
        # `skill_for` + the GenerationRequest.skill field were added to the engine
        # AFTER the 0.6.0 PyPI release. On an engine that has neither, we skip the
        # skill stamp entirely (the coordinator falls back to its own kind->skill
        # routing). Keeps this bridge working across engine versions.
        from discovery_engine.phases.generation import skill_for
    except ImportError:
        def skill_for(kind, overrides=None):  # type: ignore[misc]
            return ""
    _has_skill_field = any(f.name == "skill"
                           for f in dataclasses.fields(GenerationRequest))

    skill_overrides = ((inv_spec or {}).get("discovery") or {}).get("skill_map") \
        if isinstance(inv_spec, dict) else None
    gc = _gate_config(inv_spec)

    requests: list[GenerationRequest] = []
    reruns: list[dict] = []
    unmapped: list[dict] = []

    for it in items or []:
        if not isinstance(it, dict):
            continue
        scan_kind = str(it.get("kind") or "")
        study = it.get("study")
        ref = str(it.get("ref") or study or "")
        severity = str(it.get("severity") or "")
        title = str(it.get("title") or "")
        detail = str(it.get("detail") or "")
        hint = str(it.get("action_hint") or "")

        if scan_kind in _DETERMINISTIC:
            reruns.append({
                "scan_kind": scan_kind, "study": study, "ref": ref,
                "severity": severity, "title": title, "detail": detail,
                "action_hint": hint, "remedy": _DETERMINISTIC[scan_kind],
            })
            continue

        kind = _SCAN_TO_KIND.get(scan_kind)
        if kind is None:
            unmapped.append({"scan_kind": scan_kind, "study": study, "ref": ref,
                             "severity": severity, "title": title})
            continue

        target = str(study or ref or "")
        if not target:
            unmapped.append({"scan_kind": scan_kind, "ref": ref, "severity": severity,
                             "title": title, "reason": "no target study/ref"})
            continue

        provenance = {"source": "needs_attention", "scan_kind": scan_kind, "ref": ref}
        if study:
            provenance["study"] = str(study)
        context: dict[str, Any] = {
            "scan_kind": scan_kind, "severity": severity,
            "detail": detail, "action_hint": hint, "title": title,
        }
        nat = it.get("next_action_type")
        if isinstance(nat, str) and nat.strip():
            context["next_action_type"] = nat.strip()

        kwargs = dict(
            kind=kind,
            target=target,
            produces=_PRODUCES.get(kind, ""),
            rationale=title or detail,
            context=context,
            value={"severity": severity} if severity else {},
            provenance=provenance,
            gate=gc.resolve(kind),
        )
        if _has_skill_field:
            kwargs["skill"] = skill_for(kind, skill_overrides)
        requests.append(GenerationRequest(**kwargs))

    return {"requests": requests, "reruns": reruns, "unmapped": unmapped}


# ---------------------------------------------------------------------------
# Scan + bridge + (optional) dispatch to the filler / auditing agent.
# ---------------------------------------------------------------------------
# Safety cap: a single auto-fix batch never dispatches more than this many
# requests to the filler, so one click can't fan out an unbounded agent run.
_AUTOFIX_CAP = 12


def _load_inv_spec(ws_root, inv_slug: str) -> dict:
    """The investigation.yaml mapping (for ``discovery.gates`` / ``skill_map``).
    Dependency-light ``yaml.safe_load``; ``{}`` when absent/unreadable."""
    import yaml
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    from pathlib import Path
    try:
        p = WorkspacePaths.load(Path(ws_root)).investigations / inv_slug / "investigation.yaml"
        if not p.exists():
            return {}
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def scan_and_bridge(ws_root, inv_slug: str) -> dict:
    """Run the needs-attention scan and bridge its items in one call.

    Returns ``{requests, reruns, unmapped, items}`` where ``requests`` is the list
    of :class:`GenerationRequest` and ``items`` is the raw scan items (so a caller
    can correlate a request back to the item that produced it)."""
    from vivarium_workbench.lib import needs_attention as _na
    try:
        scan = _na.scan_investigation(ws_root, inv_slug)
    except Exception:  # noqa: BLE001
        scan = {"items": []}
    items = scan.get("items") or []
    inv_spec = _load_inv_spec(ws_root, inv_slug)
    out = bridge_items(items, inv_spec)
    out["items"] = items
    return out


def build_requests_payload(ws_root, inv_slug: str) -> dict:
    """GET /api/needs-attention-requests/{slug} builder — JSON-serializable.

    ``{investigation, total, auto_fixable, by_kind, by_gate, requests, reruns,
    unmapped}``. Pure read: the bridge writes nothing. ``auto_fixable`` is how many
    requests the cycle would fix unattended (gate==auto)."""
    b = scan_and_bridge(ws_root, inv_slug)
    reqs = b["requests"]
    by_kind: dict = {}
    by_gate = {"auto": 0, "review": 0}
    for r in reqs:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        by_gate[r.gate] = by_gate.get(r.gate, 0) + 1
    return {
        "investigation": inv_slug,
        "total": len(reqs),
        "auto_fixable": by_gate.get("auto", 0),
        "by_kind": by_kind,
        "by_gate": by_gate,
        "requests": [r.to_dict() for r in reqs],
        "reruns": b["reruns"],
        "unmapped": b["unmapped"],
    }


def run_autofix(ws_root, inv_slug: str, *, fill=None, only_auto: bool = True,
                cap: int = _AUTOFIX_CAP) -> dict:
    """Dispatch the bridged needs-attention requests to the filler / auditing agent.

    The cycle's filler seam (``$VIVA_FILLER_CMD`` via ``subprocess_filler``, or an
    injected ``fill`` for tests) authors each artifact; ``run_requests`` applies the
    ``auto``-gated ones and queues the rest. With ``only_auto`` (default) just the
    safe, auto-gated requests are dispatched — the "auto-fix safe kinds in the
    cycle" path; structural branch-points are left queued for review. Capped at
    ``cap`` requests so one call can't fan out unbounded. MUTATES the workspace
    (filled artifacts are written back + committed by the engine's apply op).

    With no ``$VIVA_FILLER_CMD`` configured, the filler produces nothing and every
    request comes back ``queued_for_review`` — a safe no-op. Returns
    ``{dispatched, outcomes, filler_configured}``."""
    import os
    from discovery_engine.phases.generation_apply import run_requests, subprocess_filler

    b = scan_and_bridge(ws_root, inv_slug)
    reqs = b["requests"]
    if only_auto:
        reqs = [r for r in reqs if r.gate == "auto"]
    reqs = reqs[:cap]
    fill = fill if fill is not None else subprocess_filler()
    outcomes = run_requests(reqs, fill=fill, ws_root=ws_root, inv_slug=inv_slug)
    return {
        "investigation": inv_slug,
        "dispatched": len(reqs),
        "filler_configured": bool(os.environ.get("VIVA_FILLER_CMD")),
        "outcomes": outcomes,
    }


def run_fix(ws_root, inv_slug: str, scan_kind: str, ref: str, *, fill=None) -> dict:
    """Dispatch ONE needs-attention item to the filler (user-triggered fix).

    Finds the bridged request for ``(scan_kind, ref)`` and fills it. An explicit
    user action is the gate, so the request is forced to ``auto`` for this one
    dispatch even if its kind is normally review-gated. MUTATES the workspace.
    Returns ``{found, outcome, filler_configured}``."""
    import os
    from dataclasses import replace
    from discovery_engine.phases.generation_apply import run_requests, subprocess_filler

    b = scan_and_bridge(ws_root, inv_slug)
    match = None
    for r in b["requests"]:
        if r.provenance.get("scan_kind") == scan_kind and r.provenance.get("ref") == ref:
            match = r
            break
    if match is None:
        return {"found": False, "investigation": inv_slug,
                "reason": f"no bridged request for {scan_kind!r}/{ref!r}"}
    # User click = consent: force this single request to auto so it actually fills.
    req = replace(match, gate="auto") if match.gate != "auto" else match
    fill = fill if fill is not None else subprocess_filler()
    outcomes = run_requests([req], fill=fill, ws_root=ws_root, inv_slug=inv_slug)
    return {
        "found": True,
        "investigation": inv_slug,
        "filler_configured": bool(os.environ.get("VIVA_FILLER_CMD")),
        "outcome": outcomes[0] if outcomes else None,
    }
