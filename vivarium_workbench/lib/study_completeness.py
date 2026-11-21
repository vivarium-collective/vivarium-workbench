"""Study-completeness gate — the publish-side consume (gate part 2).

The discovery-engine report linter detects studies that LOOK done but are
internally inconsistent (rules 1 & 2 of the study-completeness gate). This module
is the vivarium-workbench side: it collects those engine findings AND computes a
third, publish-side detection — a declared Model composite that does not resolve —
then exposes the combined ``study_completeness`` signal for the read-only publish:

  * a baked ``api/study-completeness/<inv>.json`` (surfaced on the investigation page),
  * a loud WARN summary + GitHub Actions ``::warning::`` annotations at publish,
  * a REFUSE under ``--strict`` / ``VIVARIUM_WORKBENCH_PUBLISH_STRICT=1``.

The three detections (family ``STUDY_COMPLETENESS_CHECKS``):

  1. ``status_claims_done_no_runs_recorded`` — claims done, no runs recorded.
  2. ``completed_but_verdict_pending``       — completed but verdict still pending.
  3. ``declared_composite_unresolved``       — declares a composite that does not
                                               resolve (computed HERE: the engine,
                                               by import boundary, cannot see the
                                               workbench composite registry/resolver).

**Version tolerance.** Rules 1 & 2 and the family constant live in the engine's
linter; the family export and rule 2 landed together (discovery-engine #129). An
older released engine lacks the export and the rule-2 check — the filter still
works (falling back to a local copy of the id set), and rule 2 simply does not
fire until the engine is updated. Rule 1 is present in 0.6.0; rule 3 is local.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

# Version-tolerant family import (see module docstring).
try:  # discovery-engine #129+
    from discovery_engine.reporting.linter import STUDY_COMPLETENESS_CHECKS
except Exception:  # pragma: no cover - shim for engines predating the export
    STUDY_COMPLETENESS_CHECKS = frozenset({
        "status_claims_done_no_runs_recorded",
        "completed_but_verdict_pending",
        "declared_composite_unresolved",
    })

RULE3_CHECK = "declared_composite_unresolved"
STRICT_ENV = "VIVARIUM_WORKBENCH_PUBLISH_STRICT"


def is_strict(flag: bool = False) -> bool:
    """Whether a study-completeness finding should REFUSE the publish.

    True when the ``--strict`` flag is passed OR ``VIVARIUM_WORKBENCH_PUBLISH_STRICT``
    is set to a truthy value. Default (neither) is WARN-only.
    """
    if flag:
        return True
    return os.environ.get(STRICT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Workspace enumeration
# ---------------------------------------------------------------------------

def _investigation_members(ws_root: Path) -> dict:
    """{investigation_slug: [member_study_slug, ...]} from each investigation.yaml."""
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    out: dict[str, list[str]] = {}
    try:
        wp = WorkspacePaths.load(ws_root)
        inv_root = wp.investigations
    except Exception:
        return out
    if not inv_root.is_dir():
        return out
    for inv_dir in sorted(d for d in inv_root.iterdir()
                          if d.is_dir() and (d / "investigation.yaml").is_file()):
        try:
            spec = yaml.safe_load((inv_dir / "investigation.yaml").read_text(encoding="utf-8")) or {}
        except Exception:
            spec = {}
        slugs: list[str] = []
        for s in (spec.get("studies") or []):
            if isinstance(s, str) and s.strip():
                slugs.append(s.strip())
            elif isinstance(s, dict) and isinstance(s.get("study"), str):
                slugs.append(s["study"])
        # de-dup, preserve order
        seen: set[str] = set()
        out[inv_dir.name] = [x for x in slugs if not (x in seen or seen.add(x))]
    return out


def _load_study_spec(ws_root: Path, slug: str) -> dict | None:
    """Raw study.yaml mapping for *slug* (declared composite refs live here)."""
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    try:
        wp = WorkspacePaths.load(ws_root)
        sd = wp.study_dir(slug)
    except Exception:
        return None
    f = sd / "study.yaml"
    if not f.is_file():
        return None
    try:
        return yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception:
        return None


def _declared_composite_refs(spec: dict) -> list[str]:
    """Composite ids a study DECLARES — mirrors
    discovery_engine.reporting.linter.context._collect_composite_refs (which is
    private, so it is reimplemented here). De-duplicated, in declaration order.
    """
    refs: list[str] = []

    def _add(v):
        if isinstance(v, str) and v.strip():
            refs.append(v.strip())

    baseline = (spec or {}).get("baseline")
    for b in (baseline if isinstance(baseline, list) else [baseline]):
        if isinstance(b, dict):
            _add(b.get("composite"))
    conditions = (spec or {}).get("conditions")
    if isinstance(conditions, dict):
        cb = conditions.get("baseline")
        for b in (cb if isinstance(cb, list) else [cb]):
            if isinstance(b, dict):
                _add(b.get("composite"))
        for v in (conditions.get("variants") or []):
            if isinstance(v, dict):
                _add(v.get("composite"))
    for s in ((spec or {}).get("simulation_set") or []):
        if isinstance(s, dict):
            _add(s.get("composite"))
            _add(s.get("base_model"))
    seen: set[str] = set()
    return [r for r in refs if not (r in seen or seen.add(r))]


def _resolves_real(ws_root: Path, ref: str) -> bool:
    """A declared composite resolves to a REAL card payload (id + parameters +
    state), the peer-agreed rule-3 contract. A local miss returns None; a degraded
    / error payload lacks those keys — either way, NOT real."""
    from vivarium_workbench.lib.composite_resolve import resolve_composite_for_request
    try:
        payload = resolve_composite_for_request(ws_root, ref, {})
    except Exception:
        return False
    if not isinstance(payload, dict) or payload.get("error"):
        return False
    return bool(payload.get("id")) and ("parameters" in payload) and ("state" in payload)


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

def _finding(investigation, study, check, level, field_path, message) -> dict:
    return {
        "investigation": investigation,
        "study": study,
        "check": check,
        "level": level,
        "field_path": field_path,
        "message": message,
    }


def _engine_family_findings(ws_root: Path, study_to_inv: dict, lint_findings=None) -> list[dict]:
    """Engine lint findings whose check is in STUDY_COMPLETENESS_CHECKS (rules 1 & 2)."""
    if lint_findings is None:
        try:
            from discovery_engine.reporting.linter import lint_workspace_report
            lint_findings = lint_workspace_report(ws_root)
        except Exception:
            return []
    out: list[dict] = []
    for f in lint_findings:
        d = f.to_dict() if hasattr(f, "to_dict") else dict(f)
        if d.get("check") not in STUDY_COMPLETENESS_CHECKS:
            continue
        slug = d.get("study_slug") or d.get("study")
        out.append(_finding(
            study_to_inv.get(slug), slug, d.get("check"),
            d.get("level") or "warning", d.get("field_path"), d.get("message"),
        ))
    return out


def _rule3_findings(ws_root: Path, inv_members: dict, resolve_cache: dict) -> list[dict]:
    out: list[dict] = []
    for inv, slugs in inv_members.items():
        for slug in slugs:
            spec = _load_study_spec(ws_root, slug)
            if not spec:
                continue
            for ref in _declared_composite_refs(spec):
                if ref not in resolve_cache:
                    resolve_cache[ref] = _resolves_real(ws_root, ref)
                if not resolve_cache[ref]:
                    out.append(_finding(
                        inv, slug, RULE3_CHECK, "warning", "baseline[].composite",
                        f"study {slug!r} declares composite {ref!r} but it does not "
                        "resolve to a real payload (id + parameters + state) — the "
                        "Model tab/card renders empty. Register the composite or "
                        "correct the reference.",
                    ))
    return out


def collect(ws_root, *, lint_findings=None, resolve_cache=None) -> dict:
    """Collect every study-completeness finding for *ws_root*.

    Returns ``{findings: [...], total: int, by_check: {check: n},
    by_investigation: {inv: [finding, ...]}}``. ``lint_findings`` (pre-computed
    engine findings) and ``resolve_cache`` ({ref: bool}, to avoid re-resolving a
    composite publish.py already resolved) are optional.
    """
    ws_root = Path(ws_root)
    inv_members = _investigation_members(ws_root)
    study_to_inv = {slug: inv for inv, slugs in inv_members.items() for slug in slugs}

    findings = _engine_family_findings(ws_root, study_to_inv, lint_findings=lint_findings)
    findings += _rule3_findings(ws_root, inv_members, resolve_cache if resolve_cache is not None else {})

    by_check: dict[str, int] = {}
    by_investigation: dict[str, list[dict]] = {}
    for f in findings:
        by_check[f["check"]] = by_check.get(f["check"], 0) + 1
        by_investigation.setdefault(f.get("investigation") or "", []).append(f)
    return {
        "findings": findings,
        "total": len(findings),
        "by_check": by_check,
        "by_investigation": by_investigation,
    }


# ---------------------------------------------------------------------------
# Publish-side surfacing
# ---------------------------------------------------------------------------

def ci_annotations(result: dict) -> list[str]:
    """GitHub Actions ``::warning::`` annotation lines, one per finding.

    Printed to stdout during the publish; GitHub surfaces each as a job
    annotation. (A plain, title-prefixed line everywhere else.)
    """
    lines: list[str] = []
    for f in result.get("findings", []):
        title = f"study-completeness: {f.get('check')}"
        where = f"{f.get('investigation') or '?'}/{f.get('study') or '?'}"
        msg = (f.get("message") or "").replace("\n", " ")
        lines.append(f"::warning title={title}::{where}: {msg}")
    return lines


def summary_lines(result: dict) -> list[str]:
    """Human-readable WARN summary lines for the publish log."""
    total = result.get("total", 0)
    if not total:
        return ["study-completeness: no incomplete studies — all clear."]
    by_check = result.get("by_check", {})
    head = (f"study-completeness: {total} finding(s) across "
            f"{len([k for k in result.get('by_investigation', {}) if k])} investigation(s) — "
            + ", ".join(f"{k}={v}" for k, v in sorted(by_check.items())))
    out = [head]
    for f in result.get("findings", []):
        out.append(f"  - [{f.get('check')}] {f.get('investigation') or '?'}/"
                   f"{f.get('study') or '?'}: {(f.get('message') or '')[:140]}")
    return out
