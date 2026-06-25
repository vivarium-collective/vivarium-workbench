"""Render-only study enrichment helpers, extracted from server.py.

These four helpers were previously private to the stdlib server module and
reached only from ``lib/study_spec.py`` via a lazy ``import server``.  Moving
them here breaks the last ``lib → server`` import in the codebase, which the
flip design forbids.

All functions are **pure** or **ws_root-parameterised** (no module-level
globals):

reconcile_simset_with_runs      (was server._reconcile_simset_with_runs)
compute_param_enforcement       (was server._compute_param_enforcement)
collect_study_feedback          (was server._collect_study_feedback)
study_acceptance_criterion      (was server._study_acceptance_criterion)

The ``server.py`` shims keep their ``_``-prefixed names and delegate here,
so every existing call-site outside ``lib/`` continues to work unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Run-store summary helper (needed by reconcile_simset_with_runs)
# ---------------------------------------------------------------------------

_RUN_STORE_SUMMARY_CACHE: dict = {}


def _run_store_summary(store_abs: Path) -> dict:
    """Open a run store via RunReader and return what it actually contains:
    ``{generations, sim_minutes, n_observables}``. Cached by store path (run
    stores are immutable once written). Best-effort — returns {} on any failure.
    """
    key = str(store_abs)
    if key in _RUN_STORE_SUMMARY_CACHE:
        return _RUN_STORE_SUMMARY_CACHE[key]
    out: dict = {}
    try:
        from pbg_emitters.run_reader import RunReader  # noqa: PLC0415
        out = RunReader.open(str(store_abs)).summary() or {}
    except Exception:  # noqa: BLE001 — never break the study page
        out = {}
    _RUN_STORE_SUMMARY_CACHE[key] = out
    return out


# ---------------------------------------------------------------------------
# reconcile_simset_with_runs
# ---------------------------------------------------------------------------

def reconcile_simset_with_runs(sim_set, runs, ws_root: Optional[Path] = None):
    """Enrich each simulation_set entry with what ACTUALLY ran, so the
    Simulations tab reflects current status instead of the authored/synthesized
    plan's placeholders ("? min", "not set", "ready") when real runs exist.

    Fills seeds / status / run-count from the run records, and — by opening the
    run store — the real simulation time (minutes + generations) and number of
    readouts collected. Authored values win; run-derived values fill the gaps.
    Matching: a run that explicitly names the entry wins; otherwise the baseline
    entry absorbs the runs not claimed by a named variant (single-baseline case).
    """
    if not sim_set:
        return sim_set
    runs = [r for r in (runs or []) if isinstance(r, dict)]
    if not runs:
        return sim_set

    def _seeds(r):
        s = r.get("seeds")
        if isinstance(s, list):
            return [x for x in s if x is not None]
        return [r["seed"]] if r.get("seed") is not None else []

    def _named_match(entry, r):
        nm = entry.get("name")
        if not nm:
            return False
        return any(str(r.get(k)) == str(nm) for k in ("simulation", "sim", "entry", "variant", "name") if r.get(k))

    claimed = set()
    # Pass 1: explicit name matches. Pass 2: baseline entries absorb the rest.
    for use_baseline in (False, True):
        for entry in sim_set:
            if not isinstance(entry, dict):
                continue
            if use_baseline:
                # A run-absorbing "baseline" entry: explicitly flagged (synthesized
                # specs), the only entry (authored single-baseline studies), or
                # simply unperturbed (the reference run in a sweep).
                is_base = (entry.get("is_baseline") or len(sim_set) == 1
                           or not entry.get("perturbation"))
                if not is_base:
                    continue
                mruns = [r for i, r in enumerate(runs) if i not in claimed]
            else:
                mruns = [r for i, r in enumerate(runs) if i not in claimed and _named_match(entry, r)]
            if not mruns:
                continue
            for i, r in enumerate(runs):
                if r in mruns:
                    claimed.add(i)
            seeds = sorted({x for r in mruns for x in _seeds(r)})
            # Prefer the framework-baked run-record summary (generations /
            # sim_minutes / n_readouts persisted at record time by
            # pbg_superpowers.study_outcomes). Falls back to opening the store
            # only for legacy runs recorded before the summary was baked in.
            gens = [r.get("generations") for r in mruns if r.get("generations")]
            mins = [r.get("sim_minutes") or r.get("duration_min") for r in mruns
                    if r.get("sim_minutes") or r.get("duration_min")]
            reads = [r.get("n_readouts") for r in mruns if r.get("n_readouts")]
            ran = any(str(r.get("status", "")).lower() in ("completed", "ran", "done", "passed") for r in mruns)
            store_gens, store_min, store_obs = [], [], []
            if ws_root and not (gens and mins and reads):
                for r in mruns:
                    store = (r.get("emitter") or {}).get("store") or r.get("store")
                    if not store:
                        continue
                    summ = _run_store_summary(Path(ws_root) / store)
                    if summ.get("generations"):
                        store_gens.append(summ["generations"])
                    if summ.get("sim_minutes"):
                        store_min.append(summ["sim_minutes"])
                    if summ.get("n_observables"):
                        store_obs.append(summ["n_observables"])
            if seeds and not entry.get("seeds"):
                entry["seeds"] = seeds
            if (gens or store_gens) and not entry.get("generations"):
                entry["generations"] = max(gens + store_gens)
            if (mins or store_min) and not entry.get("duration_min"):
                entry["duration_min"] = max(mins + store_min)
            if (reads or store_obs) and not entry.get("n_readouts_collected"):
                entry["n_readouts_collected"] = max(reads + store_obs)
            if ran and (not entry.get("status") or entry.get("status") == "ready"):
                entry["status"] = "completed"
            entry["n_runs_recorded"] = len(mruns)
            entry["run_names"] = [r.get("name") for r in mruns if r.get("name")]
    return sim_set


# ---------------------------------------------------------------------------
# compute_param_enforcement
# ---------------------------------------------------------------------------

def compute_param_enforcement(spec: dict) -> Optional[dict]:
    """Check param drift per-run: each run against the params IT was supposed
    to apply.

    Returns ``{declared, checked_against_run, violations: [{param, expected,
    actual, kind, message, run}]}`` or ``None`` when the study declares no
    enforced params. Each run's expectation is resolved with
    :func:`resolve_run_expected` — a baseline run gets the baseline declared
    values, a variant run gets the baseline overlaid with that variant's
    ``parameter_overrides`` (linked via ``run.variant`` / ``run.simulation``).
    This removes the false positive where a variant run that legitimately
    overrides a baseline param was flagged against the single flat baseline
    dict; real drift (a run that didn't apply its OWN declaration) is still
    caught. The "applied" params are each run's recorded overrides
    (``runs_meta.params_json``), surfaced via ``spec["runs"]``.
    """
    from pbg_superpowers.param_enforcement import (  # noqa: PLC0415
        load_enforced_params, check_enforced_params, resolve_run_expected,
    )
    declared = load_enforced_params(spec)
    if not declared:
        return None
    runs = spec.get("runs") or []

    def _ts(r):
        v = (r or {}).get("started_at")
        return float(v) if isinstance(v, (int, float)) else 0.0

    # Newest-first; only runs that recorded an applied-params dict are checked.
    with_params = [
        r for r in sorted(runs, key=_ts, reverse=True)
        if isinstance(r, dict) and isinstance(r.get("params"), dict)
    ]

    def _emit(violations, run_id):
        return [
            {"param": v.param, "expected": v.expected, "actual": v.actual,
             "kind": v.kind, "message": v.describe(), "run": run_id}
            for v in violations
        ]

    if not with_params:
        # No run recorded applied params → surface the declared set as missing
        # against the newest run (or None), as before.
        newest = next((r for r in sorted(runs, key=_ts, reverse=True)
                       if isinstance(r, dict)), None)
        run_id = (newest or {}).get("run_id")
        violations = check_enforced_params(declared, {})
        return {
            "declared": declared,
            "checked_against_run": run_id,
            "violations": _emit(violations, run_id),
        }

    all_violations: list = []
    for r in with_params:
        expected = resolve_run_expected(spec, r, declared)
        applied = r.get("params") or {}
        all_violations.extend(
            _emit(check_enforced_params(expected, applied), r.get("run_id"))
        )

    return {
        "declared": declared,
        # The newest run anchors the report banner; per-violation `run` ties
        # each violation back to the run that drifted.
        "checked_against_run": with_params[0].get("run_id"),
        "violations": all_violations,
    }


# ---------------------------------------------------------------------------
# collect_study_feedback
# ---------------------------------------------------------------------------

def collect_study_feedback(ws_root: Path, study_slug: str) -> list:
    """Gather imported feedback annotations targeting one study.

    Scans every ``investigations/<inv>/`` for stored feedback (via
    pbg_superpowers' shared reader) and returns the annotations whose section
    id matches ``study-<slug>``, newest-first. Cross-investigation because a
    study's feedback is keyed by the study slug embedded in the section id,
    not by which investigation exported the report.
    """
    from pbg_superpowers.feedback_import import (  # noqa: PLC0415
        load_investigation_feedback, feedback_for_study,
    )
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths  # noqa: PLC0415
    wp = WorkspacePaths.load(ws_root)
    inv_root = wp.investigations
    if not inv_root.is_dir():
        return []
    out: list = []
    seen: set = set()
    for inv_dir in sorted(inv_root.iterdir()):
        if not inv_dir.is_dir():
            continue
        by_section = load_investigation_feedback(ws_root, inv_dir.name)
        for ann in feedback_for_study(by_section, study_slug):
            key = (ann.get("section"), ann.get("ts"), ann.get("text"))
            if key in seen:
                continue
            seen.add(key)
            out.append(ann)
    out.sort(key=lambda a: a.get("ts") or "", reverse=True)
    return out


# ---------------------------------------------------------------------------
# study_acceptance_criterion
# ---------------------------------------------------------------------------

def study_acceptance_criterion(ws_root: Path, name: str) -> Optional[dict]:
    """The owning investigation's PERSISTED acceptance criterion(s) for a study.

    Reads ``investigations/<owner>/investigation.yaml``'s
    ``executive.computed_acceptance`` (written by the spine's investigation
    acceptance evaluator) and filters its ``criteria`` to those covering this
    study. Returns ``{investigation, verdict_status, criteria}`` or ``None``
    when the study has no owning investigation / no persisted acceptance.
    Pure disk read — never recomputes.
    """
    import yaml as _yaml  # noqa: PLC0415
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths  # noqa: PLC0415
    wp = WorkspacePaths.load(ws_root)
    owner = wp.study_owner(name)
    if not owner:
        return None
    inv_file = wp.investigations / owner / "investigation.yaml"
    if not inv_file.is_file():
        return None
    data = _yaml.safe_load(inv_file.read_text(encoding="utf-8")) or {}
    ca = ((data.get("executive") or {}).get("computed_acceptance")
          or data.get("computed_acceptance") or {})
    if not isinstance(ca, dict):
        return None
    criteria = [c for c in (ca.get("criteria") or [])
                if isinstance(c, dict) and c.get("study") == name]
    if not criteria and not ca.get("verdict_status"):
        return None
    return {
        "investigation": owner,
        "verdict_status": ca.get("verdict_status"),
        "diverges_from_authored": ca.get("diverges_from_authored"),
        "criteria": criteria,
    }
