"""SP5 — the "decisions needed" scan (Active Investigation Framework, Guide layer).

A PURE, deterministic aggregator. It makes NO new judgment: it GATHERS + RANKS
the divergences/gaps SP1–SP4 already compute into one per-investigation ranked
list — the "what needs my decision" entry point a navigator should LEAD with.

The six signals (all reuse — none reimplemented):

  1. Uncovered acceptance criterion  — ``linkage_index.ac_gating_matrix(..)["gaps"]``  (high)
  2. Verdict divergence              — persisted ``pipeline_gate.gate_evaluator.diverges_from_authored``
                                        + per-test ``computed_outcomes[t]["reconcile"]=="divergent"`` (high)
  3. Open expert feedback           — ``feedback_actions.study_feedback_actions``    (medium)
  4. Phantom / not-in-structure observable — ``readout_validation.validate_readouts`` (high, OPT-IN)
  5. Param drift                    — ``param_enforcement.check_enforced_params``     (high)
  6. Stale finding                  — greenfield ``_stale_findings`` classifier       (low)
  7. Invariant regression           — ``study.invariant_check[]`` status invalidated/weakened
                                        (C-INVAR; high/medium)

Isolation invariant (the SP4b lesson): the scan is build-free and cheap by
DEFAULT — signals 1,2,3,5,6 read YAML only. Signal 4 (phantom observable) needs
a composite build, so it is opt-in behind an INJECTED ``observables_for_ref``;
with no ``observables_for_ref`` the scan omits signal 4 entirely.

Best-effort PER SIGNAL: one raising source must never sink the scan (mirrors
``linkage_index``'s per-study tolerance). The output is EPHEMERAL — never written
back to YAML. AI-free: deterministic aggregation only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from viva_superpowers.workspace_paths import WorkspacePaths
from vivarium_workbench.lib import linkage_index
from vivarium_workbench.lib.investigation_members import investigation_member_slugs
from viva_superpowers import rigor
from viva_superpowers import viz_freshness


# ---------------------------------------------------------------------------
# Item shape + ranking
# ---------------------------------------------------------------------------

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _item(kind: str, severity: str, study: str | None, ref: str,
          title: str, detail: str, action_hint: str) -> dict:
    return {
        "kind": kind,
        "severity": severity,
        "study": study,
        "ref": ref,
        "title": title,
        "detail": detail,
        "action_hint": action_hint,
    }


# ---------------------------------------------------------------------------
# Signal 6: stale-finding classifier (greenfield)
# ---------------------------------------------------------------------------

def _stale_findings(spec: dict) -> list[dict]:
    """Return the findings in ``spec`` that are stale.

    A finding is stale ONLY when it declares a ``next_action`` (an explicit
    "what to do next") but carries no ``seeded_study`` link — i.e. the
    follow-through was specified but never materialized into a child study.

    A finding with NO ``next_action`` is NOT stale: it is a terminal
    observation (``novel``/``confirms``/``contradicts``/``partial`` result kept
    for the record), not a pending decision. Keying off ``next_action`` (a
    workflow field) rather than the scientific ``status`` avoids flagging the
    bulk of ordinary findings — the false-positive flood the plan warns against.
    """
    out: list[dict] = []
    for f in (spec.get("findings") or []):
        if not isinstance(f, dict):
            continue
        na = f.get("next_action")
        if not (isinstance(na, str) and na.strip()):
            continue  # no declared next step → not a pending decision
        seeded = f.get("seeded_study")
        has_seeded = isinstance(seeded, str) and seeded.strip() != ""
        if not has_seeded:
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Per-signal collectors (each best-effort, called inside its own try/except)
# ---------------------------------------------------------------------------

def _uncovered_ac_items(ws_root: Path, inv_slug: str) -> list[dict]:
    matrix = linkage_index.ac_gating_matrix(ws_root, inv_slug)
    items: list[dict] = []
    for gap in (matrix.get("gaps") or []):
        behavior = gap.get("behavior") or ""
        items.append(_item(
            "uncovered_ac", "high", None, behavior,
            f"Acceptance criterion '{behavior}' has no study link",
            "No study: link covers this criterion — it cannot be gated.",
            "link or author a study",
        ))
    return items


def _verdict_divergence_items(slug: str, spec: dict) -> list[dict]:
    items: list[dict] = []
    pg = spec.get("pipeline_gate")
    ge = pg.get("gate_evaluator") if isinstance(pg, dict) else None
    if isinstance(ge, dict) and ge.get("diverges_from_authored"):
        items.append(_item(
            "verdict_divergence", "high", slug, slug,
            f"Study '{slug}' computed verdict diverges from the authored gate",
            "pipeline_gate.gate_evaluator.diverges_from_authored is set.",
            "reconcile verdict",
        ))
    for run in (spec.get("runs") or []):
        if not isinstance(run, dict):
            continue
        co = run.get("computed_outcomes")
        if not isinstance(co, dict):
            continue
        for test, entry in co.items():
            if isinstance(entry, dict) and entry.get("reconcile") == "divergent":
                items.append(_item(
                    "verdict_divergence", "high", slug, str(test),
                    f"Test '{test}' computed outcome diverges from the authored outcome",
                    f"run '{run.get('name')}': computed_outcomes['{test}'].reconcile == 'divergent'.",
                    "reconcile verdict",
                ))
    return items


def _open_feedback_items(ws_root: Path, slug: str) -> list[dict]:
    from vivarium_workbench.lib.feedback_actions import study_feedback_actions

    res = study_feedback_actions(ws_root, slug)
    items: list[dict] = []
    for it in (res.get("items") or []):
        if it.get("status") != "open":
            continue
        items.append(_item(
            "open_feedback", "medium", slug, str(it.get("item_id") or ""),
            f"Unaddressed expert feedback on '{slug}'",
            it.get("text") or "",
            "apply or dismiss feedback",
        ))
    return items


def _run_applied_params(run: dict) -> dict | None:
    """The applied params of a recorded run, as a dict.

    ``study_outcomes._mechanical_record`` persists them from
    ``runs_meta.params_json`` UNPARSED, so a real run carries ``params`` (or
    ``params_json``) as a JSON STRING, not a dict — decode it. (An in-memory
    dict is also accepted.) Returns None when no usable params are present."""
    raw = run.get("params")
    if raw is None:
        raw = run.get("params_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            val = json.loads(raw)
        except Exception:  # noqa: BLE001 — malformed JSON → no usable params
            return None
        return val if isinstance(val, dict) else None
    return None


def _param_drift_items(slug: str, spec: dict) -> list[dict]:
    from vivarium_workbench.lib.param_enforcement import check_enforced_params, load_enforced_params

    declared = load_enforced_params(spec)
    if not declared:
        return []
    items: list[dict] = []
    for run in (spec.get("runs") or []):
        if not isinstance(run, dict):
            continue
        applied = _run_applied_params(run)
        if applied is None:
            # Can't assemble applied params for this run — skip it (best-effort).
            continue
        for v in check_enforced_params(declared, applied):
            items.append(_item(
                "param_drift", "high", slug, v.param,
                f"Enforced param '{v.param}' not honored by run '{run.get('name')}'",
                v.describe(),
                "re-run / update enforcement",
            ))
    return items


def _provenance_status_runs(spec: dict, status: str) -> list[dict]:
    """Runs in ``spec["runs"]`` whose ``provenance_status`` equals ``status``.

    ``provenance_status`` is stamped by ``vivarium_workbench.lib.rerun`` (Task
    3/5) onto a run's ``runs_meta`` row — pbg-superpowers cannot import
    vivarium_workbench, so it is read the same way every other signal here
    reads a run's fields: directly off the run dict as mechanically recorded
    onto the study's ``runs:`` list (mirrors ``_run_applied_params``'s
    dict-only access, no DB/vwb import)."""
    out: list[dict] = []
    for run in (spec.get("runs") or []):
        if isinstance(run, dict) and run.get("provenance_status") == status:
            out.append(run)
    return out


def _run_ref(run: dict) -> str:
    return str(run.get("run_id") or run.get("name") or "")


def _env_stale_items(slug: str, spec: dict) -> list[dict]:
    """Runs reproduced under a DIFFERENT environment than their original
    (reproducible-rerun-spine Task 5 / G3: ``provenance_status ==
    'env_stale'``, stamped by ``rerun._flag_env_drift`` as a Reproduce
    pre-check). A confirmed env drift makes the replay's result comparison
    NOT like-for-like — a caution, not necessarily a bug.

    Suppression is PER-RUN, not study-wide: a run is skipped only when its
    own recorded ``env_id`` equals the study's declared ``pinned_env:`` —
    i.e. THIS run's drift is the one the study owner explicitly accepted
    (e.g. a deliberate package/toolchain upgrade). Mirrors vwb
    ``rerun._flag_env_drift``'s own precise-match gate (it only suppresses
    the stamp when ``pinned == orig_env``): a study that pinned one accepted
    drift (env A -> B) must NOT also silently swallow an unrelated,
    different drift (env B -> C) on another run."""
    pinned = spec.get("pinned_env")
    items: list[dict] = []
    for run in _provenance_status_runs(spec, "env_stale"):
        if pinned and run.get("env_id") == pinned:
            continue  # THIS run's drift is the one accepted/pinned
        ref = _run_ref(run)
        items.append(_item(
            "env_stale", "medium", slug, ref,
            f"Run '{ref}' in study '{slug}' was reproduced under a different environment",
            "provenance_status == 'env_stale' — this run's env_id differs from the "
            "original run it reproduces; the result comparison is not like-for-like.",
            "pin the environment (study.yaml pinned_env) or re-run under the original environment",
        ))
    return items


def _nondeterministic_items(slug: str, spec: dict) -> list[dict]:
    """Runs with a CONFIRMED reproduction mismatch under an IDENTICAL
    environment + seed (reproducible-rerun-spine Task 3 / G4:
    ``provenance_status == 'nondeterministic'``, stamped by
    ``rerun.verify_reproduction``). Unlike ``env_stale`` this is not
    suppressed by ``pinned_env`` — the environment matched; the run itself
    failed to reproduce."""
    items: list[dict] = []
    for run in _provenance_status_runs(spec, "nondeterministic"):
        ref = _run_ref(run)
        items.append(_item(
            "nondeterministic", "high", slug, ref,
            f"Run '{ref}' in study '{slug}' failed to reproduce under an identical environment + seed",
            "provenance_status == 'nondeterministic' — result_fingerprint differed "
            "despite matching env_id and seed.",
            "investigate nondeterminism (uncontrolled randomness, threading, I/O ordering)",
        ))
    return items


def _stale_finding_items(slug: str, spec: dict) -> list[dict]:
    items: list[dict] = []
    for f in _stale_findings(spec):
        fid = str(f.get("id") or "")
        na = str(f.get("next_action") or "").strip()
        # critique #7: when the finding carries a machine-readable
        # next_action_type enum, use it as the action_hint and emit it on the
        # item so the navigator can filter by action.
        nat = f.get("next_action_type")
        nat = nat.strip() if isinstance(nat, str) and nat.strip() else None
        action_hint = nat if nat else "seed a study from this finding"
        it = _item(
            "stale_finding", "low", slug, fid,
            f"Finding '{fid}' declared a next_action but no study was seeded",
            f"next_action set ('{na}') but no seeded_study.",
            action_hint,
        )
        it["next_action_type"] = nat
        items.append(it)
    return items


def _unregistered_confirmatory_items(slug: str, spec: dict) -> list[dict]:
    """Confirmatory studies whose criteria were not pre-registered (critique #18).

    A confirmatory study that lacks a ``preregistered:`` block, or whose
    ``registered_before_run`` is not established, can't show its criteria
    predate the run. Medium when there's no block at all; low when a block
    exists but the run ordering is unconfirmed.
    """
    if rigor._study_type(spec) != "confirmatory":
        return []
    try:
        from viva_superpowers.study_verdict import preregistration_status
        pre = preregistration_status(spec)
    except Exception:  # noqa: BLE001 — defensive cross-module import
        return []
    if pre.get("preregistered") and pre.get("registered_before_run") is True:
        return []
    if not pre.get("preregistered"):
        severity = "medium"
        detail = ("confirmatory study has no preregistered: block — the "
                  "acceptance criteria can't be shown to predate the run.")
    else:
        severity = "low"
        detail = ("confirmatory study is pre-registered but registered_before_run "
                  "is not established (registered_at vs the run's start time).")
    return [_item(
        "confirmatory_not_preregistered", severity, slug, slug,
        f"Confirmatory study '{slug}' is not pre-registered",
        detail,
        "pre-register criteria before running",
    )]


def _diagnostic_already_seeded(spec: dict) -> bool:
    """True when a diagnose[] entry already carries a ``seeded_study`` stamp —
    reuse of the seeded_study stamp pattern (critique #19) so a fixed/seeded
    failure stops re-flagging."""
    cl = spec.get("conclusion_logic")
    fail = cl.get("if_primary_tests_fail") if isinstance(cl, dict) else None
    diag = fail.get("diagnose") if isinstance(fail, dict) else None
    if isinstance(diag, list):
        for d in diag:
            if isinstance(d, dict) and str(d.get("seeded_study") or "").strip():
                return True
    return False


def _diagnostic_branch_items(slug: str, spec: dict) -> list[dict]:
    """A failed / needs-calibration study with no seeded diagnostic child (#19).

    When ``roll_up_verdict`` reports the study ``failed`` or
    ``needs_calibration`` and no diagnose[] entry has been seeded yet, surface a
    high-severity prompt to seed a diagnostic study.
    """
    try:
        from viva_superpowers.study_verdict import roll_up_verdict
        result = roll_up_verdict(spec).get("result")
    except Exception:  # noqa: BLE001 — defensive
        return []
    if result not in ("failed", "needs_calibration"):
        return []
    if _diagnostic_already_seeded(spec):
        return []
    return [_item(
        "diagnostic_branch_needed", "high", slug, slug,
        f"Study '{slug}' verdict is '{result}' with no diagnostic study seeded",
        f"roll_up_verdict result is '{result}' but no "
        "conclusion_logic.if_primary_tests_fail.diagnose entry has a seeded_study.",
        "seed a diagnostic study",
    )]


_INVARIANT_STATUS_SEVERITY = {"invalidated": "high", "weakened": "medium"}


def _invariant_regression_items(slug: str, spec: dict) -> list[dict]:
    """Harvest a study's ``invariant_check[]`` entries whose status regressed.

    Each entry (written by the autopoiesis spine, C-INVAR) records re-running an
    *earlier* study's named measure in the current code state:
    ``{study, test, prior, now, status}``. A status of ``invalidated`` (a prior
    invariant no longer holds → high) or ``weakened`` (it still holds but moved
    the wrong way → medium) is a regression the navigator must decide on.
    ``preserved``/``strengthened`` are healthy and not surfaced.
    """
    items: list[dict] = []
    for chk in (spec.get("invariant_check") or []):
        if not isinstance(chk, dict):
            continue
        status = str(chk.get("status") or "").lower()
        severity = _INVARIANT_STATUS_SEVERITY.get(status)
        if severity is None:
            continue
        prior_study = str(chk.get("study") or "")
        test = str(chk.get("test") or "")
        prior = chk.get("prior")
        now = chk.get("now")
        items.append(_item(
            "invariant_regression", severity, slug,
            f"{prior_study}:{test}" if prior_study or test else slug,
            f"Invariant '{test}' from '{prior_study}' {status} in '{slug}'",
            f"re-run of '{prior_study}' measure '{test}': prior={prior!r} now={now!r} "
            f"→ status {status}.",
            "investigate invariant regression",
        ))
    return items


def _phantom_observable_items(
    slug: str, spec: dict, observables_for_ref: Callable[[str], Any],
) -> list[dict]:
    from vivarium_workbench.lib.readout_validation import validate_readouts

    items: list[dict] = []
    for ref in linkage_index._composites_of_study(spec):
        try:
            available = observables_for_ref(ref)
        except Exception:  # noqa: BLE001 — a build that raises skips this ref
            continue
        if not isinstance(available, dict):
            continue
        try:
            results = validate_readouts(spec, available=available)
        except Exception:  # noqa: BLE001
            continue
        for r in results:
            if r.get("status") == "not_in_structure":
                items.append(_item(
                    "phantom_observable", "high", slug, str(r.get("name") or ""),
                    f"Readout '{r.get('name')}' is not in the composite structure",
                    r.get("detail") or "",
                    "fix the readout",
                ))
    return items


# ---------------------------------------------------------------------------
# The aggregator
# ---------------------------------------------------------------------------

def _latest_run_json(ws_root, wp: WorkspacePaths, slug: str, filename: str):
    """Read ``<latest run_dir>/<filename>`` for a study, or ``None``. The run
    artifacts (report.json = severity gate, test_diff.json = cross-iteration diff)
    live under the runs-root, keyed by the study's latest run. Best-effort."""
    try:
        import json as _json
        from vivarium_workbench.lib.study_charts import latest_run_row
        from vivarium_workbench.lib.study_spec import study_dir
        latest = latest_run_row(study_dir(ws_root, slug) / "runs.db")
        if latest and latest.get("run_id"):
            f = wp.pbg / "runs" / latest["run_id"] / filename
            if f.is_file():
                return _json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return None


def _hard_gate_items(ws_root, wp: WorkspacePaths, slug: str) -> list[dict]:
    """One HIGH item per HARD gate axis (report.json ``gate.gated_by``): the exact
    hard-severity report-card axes making the study's severity gate FAIL — the
    actionable worklist an agent works first."""
    items: list[dict] = []
    rep = _latest_run_json(ws_root, wp, slug, "report.json")
    gate = rep.get("gate") if isinstance(rep, dict) else None
    if isinstance(gate, dict) and gate.get("status") == "fail":
        for gb in (gate.get("gated_by") or []):
            card, axid = gb.get("card"), gb.get("id")
            items.append(_item(
                "hard_gate", "high", slug, f"{card}:{axid}",
                f"Study '{slug}' hard gate axis {card}/{axid} mismatches",
                "A hard-severity report-card axis is a mismatch — the study's "
                "severity gate FAILS on it.",
                "root-cause the hard axis (fix the model or the cited band)"))
    return items


def _test_regression_items(ws_root, wp: WorkspacePaths, slug: str) -> list[dict]:
    """Items for axes that BROKE (pass→fail, HIGH) or REGRESSED (margin fell,
    MEDIUM) since the last run (test_diff.json ``per``) — what the last model
    edit moved the wrong way."""
    items: list[dict] = []
    diff = _latest_run_json(ws_root, wp, slug, "test_diff.json")
    for p in ((diff or {}).get("per") or []):
        change = p.get("change")
        if change == "broke":
            sev = "high"
        elif change == "regressed":
            sev = "medium"
        else:
            continue
        card, axid, md = p.get("card"), p.get("id"), p.get("margin_delta")
        items.append(_item(
            "test_regression", sev, slug, f"{card}:{axid}",
            f"Study '{slug}' axis {card}/{axid} {change} since the last run",
            (f"margin_delta={md}." if md is not None
             else f"axis {change} vs the previous run."),
            "investigate the regression (what did the last edit change?)"))
    return items


def _member_studies(wp: WorkspacePaths, inv_spec: dict) -> list[tuple[str, dict]]:
    """Resolve the investigation's member studies (slug, spec), filtered to its
    ``studies:`` list. Best-effort — unparseable studies are silently skipped."""
    members = investigation_member_slugs(inv_spec)
    # A missing/malformed ``studies:`` list means NO members — do NOT fall back
    # to scanning the whole workspace (that would pull in unrelated studies'
    # signals under this investigation).
    member_set = {m for m in members if isinstance(m, str)} if isinstance(members, list) else set()
    out: list[tuple[str, dict]] = []
    for slug, _sdir, spec in linkage_index._iter_studies(wp):
        if slug in member_set:
            out.append((slug, spec))
    return out


def _unresolved_composite_items(slug: str, spec: dict, known_ids: set) -> list[dict]:
    """Studies whose declared composite ref does NOT resolve to a registered
    composite id — a hard readiness blocker: the study cannot build or run. The
    concrete ref (e.g. ``viva_lumenoid.growth_runs.run_sheet_growth_2d``) is put
    in ``detail`` so the needs-attention surface shows what is wrong, not just a
    link to the study."""
    from vivarium_workbench.lib import composite_lookup
    out: list[dict] = []
    try:
        unresolved = composite_lookup.unresolved_study_composite_refs(spec, known_ids)
    except Exception:  # noqa: BLE001
        return out
    for ref in unresolved:
        out.append(_item(
            "unresolved_composite", "high", slug, ref,
            f"Study '{slug}' references an unregistered composite",
            f"composite not found in registry: {ref}",
            "declare a real .composite.yaml / fix the composite ref so the study can build + run",
        ))
    return out


def scan_investigation(
    ws_root: Path | str,
    inv_slug: str,
    *,
    observables_for_ref: Callable[[str], Any] | None = None,
) -> dict:
    """Aggregate SP1–SP4 signals for one investigation into a ranked list.

    Args:
        ws_root: workspace root.
        inv_slug: the investigation slug (``investigations/<slug>/investigation.yaml``).
        observables_for_ref: OPT-IN. A ``ref -> {"leaves", "catalogs"}`` callable
            (the dashboard's cached composite-build adapter). When provided, the
            phantom-observable signal (4) runs; otherwise it is omitted and the
            scan is fully build-free.

    Returns:
        ``{"investigation": slug, "items": [...], "summary": {...}}`` where each
        item is the normalized ``{kind, severity, study, ref, title, detail,
        action_hint}`` dict, sorted by severity (high→medium→low) then kind then
        ref. PURE — reads YAML only, writes nothing.
    """
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    inv_spec = linkage_index._load(
        wp.dir("investigations") / inv_slug / "investigation.yaml"
    ) or {}

    items: list[dict] = []

    # Live denominators for the per-check summary (what the scan actually looked
    # at), so the UI can show "N of M" rather than prose. Cheap: member studies
    # are already iterated below; the AC matrix is one YAML roll-up.
    member_pairs = _member_studies(wp, inv_spec)
    member_count = len(member_pairs)
    ac_total = 0
    ac_covered = 0
    try:
        _m = linkage_index.ac_gating_matrix(ws_root, inv_slug)
        _crit = _m.get("criteria") or []
        ac_total = len(_crit)
        ac_covered = ac_total - len(_m.get("gaps") or [])
    except Exception:  # noqa: BLE001
        pass

    # Signal 1 — uncovered acceptance criteria (investigation-level).
    try:
        items.extend(_uncovered_ac_items(ws_root, inv_slug))
    except Exception:  # noqa: BLE001
        pass

    # Per member study: signals 2,3,5,6 (+4 when opted in). Each in its own
    # try/except so one failing source never sinks the whole scan.
    # Composite-resolution readiness: the set of registered composite ids, computed
    # once (cached/tolerant). A study whose composite ref is not in it cannot run.
    try:
        from vivarium_workbench.lib import composite_lookup
        _known_ids = composite_lookup.known_composite_ids(ws_root)
    except Exception:  # noqa: BLE001
        _known_ids = set()

    for slug, spec in member_pairs:
        try:
            items.extend(_unresolved_composite_items(slug, spec, _known_ids))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_verdict_divergence_items(slug, spec))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_hard_gate_items(ws_root, wp, slug))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_test_regression_items(ws_root, wp, slug))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_open_feedback_items(ws_root, slug))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_param_drift_items(slug, spec))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_stale_finding_items(slug, spec))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_unregistered_confirmatory_items(slug, spec))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_diagnostic_branch_items(slug, spec))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_invariant_regression_items(slug, spec))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_env_stale_items(slug, spec))
        except Exception:  # noqa: BLE001
            pass
        try:
            items.extend(_nondeterministic_items(slug, spec))
        except Exception:  # noqa: BLE001
            pass
        if observables_for_ref is not None:
            try:
                items.extend(
                    _phantom_observable_items(slug, spec, observables_for_ref)
                )
            except Exception:  # noqa: BLE001
                pass

    items.sort(key=lambda i: (
        _SEVERITY_RANK.get(i["severity"], 99), i["kind"], i["ref"]
    ))

    summary = _summary(items)
    summary["member_count"] = member_count
    summary["ac_total"] = ac_total
    summary["ac_covered"] = ac_covered

    return {
        "investigation": inv_slug,
        "items": items,
        "summary": summary,
        "checks": _checks(summary, member_count, ac_total, ac_covered,
                          include_phantom=observables_for_ref is not None),
    }


# Each check the scan runs, with a live "examined" denominator. ``mode`` tells the
# UI how to read the numbers: 'coverage' => ac_covered/ac_total; 'studies' =>
# (member_count - flagged)/member_count passing. Order = how they're shown.
_CHECK_CATALOG = [
    ("uncovered_ac",             "Acceptance criteria covered",              "coverage"),
    ("unresolved_composite",     "Composites resolve in the registry",       "studies"),
    ("verdict_divergence",       "Verdicts match the authored gate",         "studies"),
    ("hard_gate",                "Hard gates passing",                        "studies"),
    ("test_regression",          "No behavior-test regressions",             "studies"),
    ("diagnostic_branch_needed", "Failed verdicts have a diagnostic study",  "studies"),
    ("invariant_regression",     "No invariant regressions",                 "studies"),
    ("open_feedback",            "Expert feedback addressed",                 "studies"),
    ("param_drift",              "No parameter drift",                        "studies"),
    ("unregistered_confirmatory","Confirmatory studies registered",          "studies"),
    ("stale_finding",            "No stale findings",                         "studies"),
    ("env_stale",                "Run environments current",                  "studies"),
    ("nondeterministic",         "Runs reproducible",                         "studies"),
    ("phantom_observable",       "Observables exist in the structure",       "studies"),
]


def _checks(summary: dict, member_count: int, ac_total: int, ac_covered: int,
            *, include_phantom: bool) -> list[dict]:
    """Per-check live rollup: {kind, label, mode, flagged, examined, passing}.

    Pure projection of the summary + denominators — invents no new judgment.
    """
    by_kind = summary.get("by_kind") or {}
    out: list[dict] = []
    for kind, label, mode in _CHECK_CATALOG:
        if kind == "phantom_observable" and not include_phantom:
            continue
        flagged = int(by_kind.get(kind, 0))
        if mode == "coverage":
            examined, passing = ac_total, ac_covered
        else:
            examined, passing = member_count, max(0, member_count - flagged)
        out.append({
            "kind": kind, "label": label, "mode": mode,
            "flagged": flagged, "examined": examined, "passing": passing,
        })
    return out


# ---------------------------------------------------------------------------
# Open epistemic debts (item 15 — negative knowledge)
# ---------------------------------------------------------------------------
#
# A per-STUDY collector (distinct from the per-investigation scan above). It
# harvests the "what this study does NOT yet know" signals that already exist —
# it invents no new store and recomputes nothing it can reuse. Pure spec->list.

_DEBT_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# rigor dimension id -> epistemic-debt kind. These are the GAP/WARN dims the
# contract enumerates (no-replication, no-controls, no-alternatives,
# not-falsifiable, interpretation-without-origin).
_RIGOR_DEBT_KIND = {
    "replication": "claim-untested",
    "negative_control": "control-absent",
    "alternatives": "alternative-not-excluded",
    "falsifiability": "claim-untested",
    "mechanism_origin": "claim-untested",
}
_RIGOR_DEBT_SEVERITY = {rigor.GAP: "high", rigor.WARN: "medium"}


def _debt(kind: str, ref: str, note: str, severity: str) -> dict:
    return {"kind": kind, "ref": ref, "note": note, "severity": severity}


def _iter_alternatives(spec: dict) -> list[dict]:
    out: list[dict] = []
    di = spec.get("discovery_implications") or {}
    if isinstance(di, dict):
        out.extend(a for a in (di.get("alternate_hypotheses") or []) if isinstance(a, dict))
    out.extend(a for a in (spec.get("alternative_hypotheses") or []) if isinstance(a, dict))
    return out


def _anchor_uncalibrated(anchor: object) -> bool:
    """A calibration_anchor that carries an uncalibrated marker — explicit
    ``uncalibrated``/pending status, a ``⚠`` glyph, or no literature target /
    observed value at all (the band exists but is unanchored)."""
    if not isinstance(anchor, dict):
        return False
    if anchor.get("uncalibrated"):
        return True
    status = str(anchor.get("status") or "").lower()
    if "uncalibrat" in status or "pending" in status:
        return True
    if any(isinstance(v, str) and "⚠" in v for v in anchor.values()):
        return True
    if anchor.get("literature_target") is None and anchor.get("observed_value") is None:
        return True
    return False


def _uncalibrated_debts(spec: dict) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    sources: list[tuple[str, object]] = []
    for f in (spec.get("findings") or []):
        if isinstance(f, dict) and "calibration_anchor" in f:
            sources.append((str(f.get("id") or f.get("statement") or "finding"),
                            f.get("calibration_anchor")))
    for section in ("behavior_tests", "tests"):
        for t in (spec.get(section) or []):
            if isinstance(t, dict) and "calibration_anchor" in t:
                sources.append((str(t.get("name") or "test"), t.get("calibration_anchor")))
    for ref, anchor in sources:
        if ref not in seen and _anchor_uncalibrated(anchor):
            seen.add(ref)
            out.append(_debt(
                "metric-uncalibrated", ref,
                f"Calibration anchor for '{ref}' is uncalibrated "
                "(no literature target / observed value, or marked ⚠).",
                "medium"))
    return out


def _stale_viz_debts(spec: dict) -> list[dict]:
    out: list[dict] = []
    for v in (spec.get("visualizations") or []):
        if not isinstance(v, dict):
            continue
        if str(v.get("freshness") or "").lower() == viz_freshness.STALE or v.get("stale") is True:
            ref = str(v.get("chart") or v.get("name") or "visualization")
            out.append(_debt(
                "viz-stale", ref,
                f"Visualization '{ref}' is stale relative to its source run "
                "(re-render to match the latest results).",
                "low"))
    return out


def open_epistemic_debts(spec: dict) -> list[dict]:
    """Harvest a study's OPEN epistemic debts (item 15 — negative knowledge).

    Returns a severity-sorted list of ``{"kind", "ref", "note", "severity"}``
    where ``kind`` is one of ``claim-untested | control-absent |
    metric-uncalibrated | alternative-not-excluded | region-unexplored |
    viz-stale``. PURE; reuses :mod:`viva_superpowers.rigor` +
    :mod:`viva_superpowers.viz_freshness` rather than reinventing the harvest, so
    it can't drift from the scorecard. Tolerant of a minimal/empty spec.
    """
    spec = spec or {}
    debts: list[dict] = []

    # 1. rigor GAP/WARN dimensions (no-replication, no-controls, no-alternatives,
    #    not-falsifiable, interpretation-without-origin).
    try:
        sc = rigor.study_rigor(spec)
    except Exception:  # noqa: BLE001 — never let the scorecard sink the harvest
        sc = {"dimensions": []}
    for d in (sc.get("dimensions") or []):
        kind = _RIGOR_DEBT_KIND.get(d.get("id"))
        sev = _RIGOR_DEBT_SEVERITY.get(d.get("severity"))
        if kind and sev:
            debts.append(_debt(kind, str(d.get("id")),
                               d.get("detail") or d.get("label") or "", sev))

    # 2. controls[] that have not produced a discriminating result.
    for c in (spec.get("controls") or []):
        if not isinstance(c, dict):
            continue
        result = str(c.get("result") or "").upper()
        if result == "PENDING" or not rigor._nonempty(c.get("observed")):
            name = str(c.get("name") or c.get("kind") or "control")
            why = "result PENDING" if result == "PENDING" else "no observed result recorded"
            debts.append(_debt(
                "control-absent", name,
                f"Control '{name}' has not produced a discriminating result ({why}).",
                "medium"))

    # 3. alternatives left on the table (not-excluded / untested).
    for a in _iter_alternatives(spec):
        status = str(a.get("status") or "").lower().replace("_", "-")
        if status in ("not-excluded", "untested"):
            claim = str(a.get("claim") or a.get("name") or "alternative")
            debts.append(_debt(
                "alternative-not-excluded", claim,
                f"Alternative explanation not ruled out ({status}): {claim}",
                "medium"))

    # 4. uncalibrated metrics (calibration_anchor markers).
    debts.extend(_uncalibrated_debts(spec))

    # 5. stale visualizations (viz-freshness markers).
    debts.extend(_stale_viz_debts(spec))

    # 6. remaining uncertainties → unexplored regions.
    di = spec.get("discovery_implications") or {}
    if isinstance(di, dict):
        for u in (di.get("remaining_uncertainties") or []):
            if isinstance(u, dict):
                text = u.get("note") or u.get("text") or u.get("question") or ""
            else:
                text = u
            text = str(text).strip()
            if text:
                debts.append(_debt("region-unexplored", "remaining_uncertainties", text, "low"))

    debts.sort(key=lambda d: (_DEBT_SEVERITY_RANK.get(d["severity"], 99), d["kind"], d["ref"]))
    return debts


def _summary(items: list[dict]) -> dict:
    by_severity = {"high": 0, "medium": 0, "low": 0}
    by_kind: dict[str, int] = {}
    for it in items:
        sev = it["severity"]
        if sev in by_severity:
            by_severity[sev] += 1
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
    return {
        "by_severity": by_severity,
        "by_kind": by_kind,
        "total": len(items),
    }
