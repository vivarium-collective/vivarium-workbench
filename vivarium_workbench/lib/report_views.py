"""Report / linkage / needs-attention / iset-detail view builders.

Extracted from ``vivarium_workbench.server`` so the FastAPI seam
(``api/app.py``) can call them without importing the stdlib server module.
The single implementation is shared: ``server.py`` keeps thin shims with
identical names/signatures that delegate here.

Phase A Batch 7 — five routes:
  GET /api/report-lint         → build_report_lint
  GET /api/linkage-index       → build_linkage_index
  GET /api/needs-attention     → build_needs_attention
  GET /api/inputs              → _inputs_payload lives in server.py (already ws_root-parameterized)
  GET /api/investigation/{slug}         → build_iset_detail
"""

from __future__ import annotations

import sys
import time as _time
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from vivarium_workbench.lib.investigation_status import (
    compute_investigation_status,
    _study_display_status,
    _STUDY_STATUS_FAILED,
    _STUDY_STATUS_COMPLETE,
    _STUDY_STATUS_RUNNING,
    _STUDY_STATUS_PLANNED,
)
from vivarium_workbench.lib.investigations_index import (
    _count_runs_for_study,
    _format_baseline_source,
)
from vivarium_workbench.lib.spec_norm import normalize_requirements as _normalize_requirements
from vivarium_workbench.lib.workspace_paths import WorkspacePaths
from vivarium_workbench.lib.investigation_members import investigation_member_slugs
from vivarium_workbench.lib import readouts_views as _readouts_views
from vivarium_workbench.lib import study_derivations as _study_derivations

# ---------------------------------------------------------------------------
# Module-level TTL cache for the linkage index (mirrors server._LINKAGE_CACHE)
# ---------------------------------------------------------------------------

_LINKAGE_CACHE: dict[Any, Any] = {}
_LINKAGE_TTL = 30.0  # seconds


def clear_cache() -> None:
    """Clear the linkage-index TTL cache.

    Called from ``server._invalidate_workspace_caches`` on a workspace switch so
    a re-pointed workspace never serves the previous workspace's cached linkage
    index. Mirrors the ``clear_cache`` contract of the other lib view modules
    (``observables_views``, ``composite_state_views``) — the source-switch hook
    invokes all three.
    """
    _LINKAGE_CACHE.clear()


# ---------------------------------------------------------------------------
# Private pure helpers (copied / adapted from server.py)
# ---------------------------------------------------------------------------

# Pre-evaluation effective-status values: a study in one of these has not (yet)
# reached a graded state, so committed report-card verdicts should promote it to
# "evaluated" (see the report-card safeguard in the investigation-graph builder).
_PRE_EVAL_STATUSES = frozenset({"build", "planned", "implementation", "design", "pending", ""})


def _compute_study_effective_status(
    status: str, has_runs: bool = False, has_active_run: bool = False
) -> str:
    """Derive a study's effective status from its declared status.

    Pure function — mirrors ``server.compute_study_effective_status``.
    """
    s = (status or "").strip()
    if s in _STUDY_STATUS_FAILED:
        return "failed"
    if s in _STUDY_STATUS_COMPLETE:
        return "complete"
    if s in _STUDY_STATUS_RUNNING or has_active_run:
        return "running"
    if s in _STUDY_STATUS_PLANNED:
        return "planned"
    return s or "planned"


def _has_active_run_for_study(
    ws_root: Path, name: str, spec: Optional[dict] = None, *, freshness_s: float = 300.0
) -> bool:
    """True only if a run for this study is actively executing right now.

    Mirrors ``server._has_active_run_for_study`` parameterised on ``ws_root``
    instead of the WORKSPACE global.
    """
    now = _time.time()

    def _fresh(hb: Any) -> bool:
        if hb is None:
            return False
        try:
            return (now - float(hb)) <= freshness_s
        except (TypeError, ValueError):
            return False

    # study.yaml runs[] (backfilled / legacy runs may carry status + heartbeat).
    for r in ((spec or {}).get("runs") or []):
        if not isinstance(r, dict):
            continue
        if str(r.get("status") or "").strip().lower() == "running" and _fresh(r.get("heartbeat_at")):
            return True

    # studies/<name>/runs.db rows.
    try:
        from vivarium_workbench.lib.study_spec import read_runs_db_for_study
        for r in read_runs_db_for_study(ws_root, name):
            if str((r or {}).get("status") or "").strip().lower() == "running" and _fresh(
                (r or {}).get("heartbeat_at")
            ):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _enrich_findings_with_weight(study_spec: dict) -> list:
    """Attach evidential weight to each finding.

    Mirrors ``server._enrich_findings_with_weight``.  Tolerant: if
    ``viva_superpowers.rigor.finding_evidential_weight`` is unavailable the
    findings pass through unchanged.
    """
    findings = study_spec.get("findings") or []
    try:
        from viva_superpowers.rigor import finding_evidential_weight
    except Exception:  # noqa: BLE001
        return findings
    out = []
    for f in findings:
        if isinstance(f, dict):
            try:
                w = finding_evidential_weight(study_spec, f)
            except Exception:  # noqa: BLE001
                w = None
            if w:
                f = {**f, "_evidential_weight": w}
        out.append(f)
    return out


def _coerce_list_field(spec: dict, field: str, *, source: str = "<unknown>") -> list:
    """Read ``spec[field]`` and return it as a list, degrading non-list values.

    Mirrors ``server._coerce_list_field``.
    """
    value = spec.get(field)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    print(
        f"warning: {source}: field {field!r} expected list, got "
        f"{type(value).__name__} — degrading to empty list. Check the "
        f"workspace yaml schema.",
        file=sys.stderr,
    )
    return []


def _composite_resolution_findings(ws_root: Path) -> list[dict]:
    """Return report-lint findings for composite refs that don't resolve.

    Mirrors ``server._composite_resolution_findings`` parameterised on
    ``ws_root`` instead of the WORKSPACE global.  Tolerant: returns ``[]``
    on any failure.
    """
    out: list[dict] = []
    try:
        from vivarium_workbench.lib.composite_lookup import (
            known_composite_ids,
            suggest_composite_ref,
            unresolved_study_composite_refs,
        )
        known = known_composite_ids(ws_root)
    except Exception:  # noqa: BLE001
        return out
    try:
        wp = WorkspacePaths.load(ws_root)
        studies_root = wp.studies
    except Exception:  # noqa: BLE001
        return out
    if not studies_root.is_dir():
        return out
    for d in sorted(studies_root.iterdir()):
        f = d / "study.yaml"
        if not f.is_file():
            continue
        try:
            spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(spec, dict):
            continue
        try:
            unresolved = unresolved_study_composite_refs(spec, known)
        except Exception:  # noqa: BLE001
            continue
        for ref in unresolved:
            try:
                suggestion = suggest_composite_ref(ref, known)
            except Exception:  # noqa: BLE001
                suggestion = None
            if suggestion:
                reason = "(it may not declare a real, registered composite)"
            else:
                reason = (
                    "(check the composite id — it should be the registered "
                    "generator id `<pkg>.composites.<module>.<name>`)"
                )
            message = (
                f"composite not found in registry: {ref} — the study "
                f"references a composite that doesn't resolve {reason}"
            )
            if suggestion:
                message += f" — did you mean `{suggestion}`?"
            out.append({
                "study": d.name,
                "check": "unresolved_composite",
                "severity": "warning",
                "message": message,
                "field_path": "baseline[].composite",
            })
    return out


def _iter_study_slugs(ws_root: Path):
    """Yield ``(slug, spec)`` for every parseable study in the workspace.

    Mirrors the enumeration pattern used by ``_composite_resolution_findings``.
    Tolerant: skips non-directories, missing/unparseable study.yaml files, and
    any structural failure.
    """
    try:
        wp = WorkspacePaths.load(ws_root)
        studies_root = wp.studies
    except Exception:  # noqa: BLE001
        return
    if not studies_root.is_dir():
        return
    for d in sorted(studies_root.iterdir()):
        if not d.is_dir():
            continue
        f = d / "study.yaml"
        if not f.is_file():
            continue
        try:
            spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(spec, dict):
            continue
        yield d.name, spec


def _readout_emit_plan_findings(ws_root: Path) -> list[dict]:
    """Findings for authored readouts whose store_path isn't an emit-plan leaf.

    Reuses ``readouts_views.build_study_readouts`` — any row with
    ``emit_status == 'not_in_emit_plan'`` (authored ``available`` readout that is
    missing a store_path or points off the emit plan) becomes an error finding
    feeding the readiness gaps. ``derived`` / ``emitted`` rows are clean.
    """
    out: list[dict] = []
    for slug, spec in _iter_study_slugs(ws_root):
        # Fix 2 (perf): skip composite build for studies with no authored readouts —
        # they can never produce not_in_emit_plan findings.
        if not (spec.get("readouts") or spec.get("observables")):
            continue
        try:
            body, status = _readouts_views.build_study_readouts(ws_root, slug)
        except Exception:  # noqa: BLE001
            continue
        if status not in (200, 422):
            continue
        # Fix 1: composite build-failure (422 + non-empty note) → skip per-row
        # fabrication findings.  The rows were built from an EMPTY emit plan, so
        # every authored available readout would show not_in_emit_plan — which is
        # a false positive, not a real authoring error.
        if status == 422 and body.get("note"):
            continue
        for row in body.get("rows", []) or []:
            if row.get("emit_status") != "not_in_emit_plan":
                continue
            sp = row.get("store_path") or "(missing)"
            out.append({
                "study": slug,
                "check": "readout-store-path",
                "severity": "error",
                "message": (f"readout {row.get('name')!r} store_path {sp} is not an "
                            f"emittable leaf of the composite (never-fabricate)."),
                "field_path": f"readouts.{row.get('name')}.store_path",
            })
    return out


def _question_approach_findings(ws_root: Path) -> list[dict]:
    """Deterministic 'missing_question' readiness gaps — one per study whose
    spec has no non-empty question (``purpose.question`` or legacy top-level
    ``question``). Reuses ``_iter_study_slugs`` (tolerant of unreadable specs).
    """
    out: list[dict] = []
    for slug, spec in _iter_study_slugs(ws_root):
        _purpose = spec.get("purpose")
        _purpose = _purpose if isinstance(_purpose, dict) else {}
        q = (_purpose.get("question") or spec.get("question") or "").strip()
        if not q:
            out.append({
                "study": spec.get("name") or slug,
                "check": "missing_question",
                "severity": "warning",
                "message": "Study has no question — add a Question & Approach.",
                "field_path": "purpose.question",
            })
    return out


def _visualization_gap_findings(ws_root: Path) -> list[dict]:
    """Deterministic 'visualization_gap' readiness gaps (Fable §5(A)/§6(c)
    Task V4, recalibrated by Task Vcal) — one per study whose visualizations
    don't clear the quality bar (``lib.viz_gate.study_visualization_status``).

    Task Vcal recalibrated this from a flat "always warning when not fully
    qualifying" into three outcomes, driven by ``gap_severity``:
      * ``"warning"`` — no interactive figure at all (the genuine
        empty/boring case).
      * ``"info"``    — has an interactive figure and has been run, but
        nothing's run-linked yet (a soft provenance nudge).
      * ``None``      — either fully qualifying OR the study simply hasn't
        been run yet — emits NOTHING (an unrun study isn't a viz problem).

    Mirrors ``_question_approach_findings``: reuses ``_iter_study_slugs``
    (tolerant of unreadable specs) and is itself best-effort per study — a
    study whose composite/runs can't be read never raises, it just reads as
    "no figures" (-> ``warning``, same as V4's tolerant fallback). Always
    soft (``warning``/``info``, never ``error``) — this is a nudge, not a
    block.
    """
    from vivarium_workbench.lib.viz_gate import study_visualization_status

    out: list[dict] = []
    for slug, spec in _iter_study_slugs(ws_root):
        try:
            status = study_visualization_status(ws_root, slug)
        except Exception:  # noqa: BLE001 — never let one study 500 the lint
            status = {"qualifies": False, "reason": "no figures", "gap_severity": "warning"}
        severity = status.get("gap_severity")
        if not severity:
            continue
        reason = status.get("reason") or "no qualifying figure"
        if reason == "no figures":
            message = ("Study has no visualizations — add a chart, embed, or "
                       "run to produce a figure.")
        elif reason == "no interactive figure (only static images)":
            message = ("Study's figures are all static images — add an "
                       "interactive figure (embedded Plotly/HTML, a native "
                       "gallery panel, or a .gif) so it qualifies.")
        elif reason == "no figure linked to a run":
            message = ("Study has an interactive figure but none is linked "
                       "to a run — link a figure to a real run for genuine "
                       "provenance.")
        else:
            message = f"Study's visualizations don't qualify ({reason})."
        out.append({
            "study": spec.get("name") or slug,
            "check": "visualization_gap",
            "severity": severity,
            "message": message,
            "field_path": "visualizations",
        })
    return out


def _linkage_cached_index(ws_root: Path) -> Optional[dict]:
    """Return the cached linkage index for ``ws_root`` (TTL-cached), or build
    and cache it.  Returns ``None`` when the index module is unavailable or
    the build fails — callers stay tolerant.
    """
    key = ("linkage", str(Path(ws_root)))
    now = _time.time()
    hit = _LINKAGE_CACHE.get(key)
    if hit is not None and now - hit[0] < _LINKAGE_TTL:
        return hit[1]  # type: ignore[no-any-return]
    try:
        from vivarium_workbench.lib.linkage_index import build_index
        index = build_index(ws_root)
    except Exception:  # noqa: BLE001
        return None
    _LINKAGE_CACHE[key] = (now, index)
    return index  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def build_report_lint(ws_root: Path) -> tuple[dict, int]:
    """GET /api/report-lint builder.

    Runs the deterministic linter (``viva_superpowers.report_linter``) over
    the workspace and returns ``(payload_dict, status_code)``.  Always 200 —
    tolerant on linter absence or workspace scan failure.

    Phase 2.1d: also applies ``<ws>/.pbg/report-lint-overrides.json`` the same
    way the old ``/viva-report`` skill/CLI did (``report_linter``-backed
    ``viva_superpowers.report.render_workspace_report``'s lint step) — an
    error-level finding whose ``override_key`` is logged in the override file
    is downgraded to a warning (message prefixed ``[overridden]``) via
    ``report_linter.apply_overrides``. This reuses each finding's own
    ``override_key`` (derived by ``report_linter._override_key`` at lint
    time) rather than re-deriving the formula here. Only applies to the
    findings produced by ``lint_workspace_report`` itself — the dashboard's
    own supplemental findings below (composite resolution, readout emit-plan,
    question/approach, visualization gap) were never part of the plugin's
    override system.
    """
    ws_root = Path(ws_root)
    try:
        from viva_superpowers.report_linter import (
            apply_overrides,
            lint_workspace_report,
            load_overrides,
        )
    except Exception:  # noqa: BLE001 — older viva_superpowers lacks the linter
        return {"findings": []}, 200
    try:
        raw = lint_workspace_report(ws_root)
    except Exception as e:  # noqa: BLE001
        return {"findings": [], "error": str(e)}, 200

    try:
        overrides = load_overrides(ws_root)
        raw = apply_overrides(raw, overrides)
    except Exception:  # noqa: BLE001 — override application must never break the endpoint
        pass

    findings = []
    for f in raw:
        d = f.to_dict() if hasattr(f, "to_dict") else dict(f)
        findings.append({
            "study":      d.get("study_slug") or d.get("study") or "<workspace>",
            "check":      d.get("check", ""),
            "severity":   d.get("level") or d.get("severity") or "info",
            "message":    d.get("message", ""),
            "field_path": d.get("field_path", ""),
        })

    findings.extend(_composite_resolution_findings(ws_root))
    findings.extend(_readout_emit_plan_findings(ws_root))
    findings.extend(_question_approach_findings(ws_root))
    findings.extend(_visualization_gap_findings(ws_root))
    return {"findings": findings}, 200


def build_linkage_index(
    ws_root: Path,
    *,
    investigation: Optional[str] = None,
    source: Optional[str] = None,
    observable: Optional[str] = None,
    observable_registry: Optional[str] = None,
    composite: Optional[str] = None,
    observables_for_ref_fn: Optional[Callable[[Path, str], Any]] = None,
) -> tuple[dict, int]:
    """GET /api/linkage-index builder.

    ``observables_for_ref_fn``: injectable callable ``(ws_root, ref) ->
    dict | tuple`` used for the SP4b observable_registry / composite paths.
    The server.py shim injects ``server._observables_for_ref`` so tests can
    monkeypatch it; the FastAPI route leaves it ``None`` (those paths degrade
    gracefully to empty payloads when no fn is supplied).
    """
    ws_root = Path(ws_root)
    try:
        from vivarium_workbench.lib import linkage_index as _li
    except Exception:  # noqa: BLE001
        return {"nodes": [], "edges": []}, 200

    def _obs_for_ref(ref: str) -> dict:
        if observables_for_ref_fn is None:
            return {}
        res = observables_for_ref_fn(ws_root, ref)
        if isinstance(res, dict):
            return res
        if isinstance(res, tuple) and res:
            try:
                import json as _json
                return _json.loads(res[0])  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                return {}
        return {}

    if observable_registry:
        try:
            return _li.studies_for_observable(
                ws_root, observable_registry, observables_for_ref=_obs_for_ref
            ), 200
        except Exception:  # noqa: BLE001
            return {"studies": [], "composites": []}, 200
    if composite:
        try:
            return _li.composite_emits(
                ws_root, composite, observables_for_ref=_obs_for_ref
            ), 200
        except Exception:  # noqa: BLE001
            return {"emits": [], "used_by_studies": []}, 200

    try:
        if source:
            return {"studies": _li.studies_for_source(ws_root, source)}, 200
        if observable:
            return {"findings": _li.findings_for_observable(ws_root, observable)}, 200
        if investigation:
            return {
                "investigation": investigation,
                "ac_matrix": _li.ac_gating_matrix(ws_root, investigation),
                "dag": _li.study_dag(ws_root, investigation),
            }, 200
        index = _linkage_cached_index(ws_root) or {"nodes": [], "edges": []}
        return index, 200
    except Exception as e:  # noqa: BLE001
        return {"nodes": [], "edges": [], "error": str(e)}, 200


def build_needs_attention(
    ws_root: Path, *, investigation: Optional[str] = None
) -> tuple[dict, int]:
    """GET /api/needs-attention builder.

    Runs ``vivarium_workbench.lib.needs_attention.scan_investigation`` and returns
    ``(payload_dict, 200)``.  Always 200 — tolerant on absence / scan failure.
    """
    ws_root = Path(ws_root)
    _empty: dict = {
        "investigation": investigation,
        "items": [],
        "summary": {
            "by_severity": {"high": 0, "medium": 0, "low": 0},
            "by_kind": {},
            "total": 0,
        },
    }
    try:
        from vivarium_workbench.lib import needs_attention as _na
    except Exception:  # noqa: BLE001
        return _empty, 200
    try:
        return _na.scan_investigation(ws_root, investigation), 200
    except Exception:  # noqa: BLE001
        return _empty, 200


def build_inputs(ws_root: Path, slug: Optional[str] = None) -> dict:
    """GET /api/inputs builder.

    Returns the loaded investigation's owned inputs (the investigation whose
    slug matches the current git branch, or ``slug`` when given), the repo-wide
    global inputs (workspace.yaml ``datasets`` + parsed BibTeX references), and
    that current slug.  Mirrors the SimulationsDB current-investigation-first
    layout.

    Mirrors ``server._inputs_payload`` parameterised on ``ws_root``; the
    current-branch slug comes from ``lib.investigation_status.current_branch_slug``.
    """
    from vivarium_workbench.lib.investigation_inputs import investigation_inputs
    from vivarium_workbench.lib.investigation_status import current_branch_slug
    from vivarium_workbench.lib.report import _parse_bib_entries, _enrich_with_file_info

    ws_root = Path(ws_root)
    current = slug or current_branch_slug(ws_root)
    if current:
        investigation = investigation_inputs(ws_root, current, repo_fallback=False)
    else:
        investigation = {"datasets": [], "references": [],
                         "expert_docs": [], "_repo_fallback": False}

    # Repo-level (global) inputs: reuse the same data sources the global Inputs
    # page builds from — workspace.yaml `datasets` (file-enriched) and the
    # parsed BibTeX references.
    try:
        ws = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        ws = {}
    try:
        global_datasets = _enrich_with_file_info(ws.get("datasets") or [], ws_root)
    except Exception:  # noqa: BLE001
        global_datasets = list(ws.get("datasets") or [])
    try:
        bib_entries = _parse_bib_entries(ws_root)
    except Exception:  # noqa: BLE001
        bib_entries = []
    global_references = bib_entries
    global_block = {"datasets": global_datasets, "references": global_references}

    # Enrich the investigation block:
    #  - references: the investigation's references are bare bib keys; join them
    #    against the parsed BibTeX entries so the UI gets rich dicts (title,
    #    author, year, journal, doi, url, bibtex). Unmatched keys are flagged.
    #  - datasets / expert_docs: ensure each carries a workspace-relative
    #    `path` (download href) and a `name`.
    by_key = {e.get("key"): e for e in bib_entries if isinstance(e, dict) and e.get("key")}
    # references_pdfs maps a bib key -> stored PDF path (drop-and-go uploads).
    pdf_by_key: dict = {}
    for rp in (ws.get("references_pdfs") or []):
        if isinstance(rp, dict) and rp.get("bib_key") and rp.get("path"):
            pdf_by_key[rp["bib_key"]] = rp["path"]

    def _enrich_ref(ref: Any) -> dict:
        key = ref if isinstance(ref, str) else (
            (ref or {}).get("key") or (ref or {}).get("bib_key") if isinstance(ref, dict) else None)
        if isinstance(ref, dict) and not key:
            # Already a rich dict without a recognizable key field; pass through.
            out = dict(ref)
        elif key and key in by_key:
            out = dict(by_key[key])
        elif key:
            out = {"key": key, "title": key, "_unmatched": True}
        else:
            out = {"key": str(ref), "title": str(ref), "_unmatched": True}
        k = out.get("key")
        if k and k in pdf_by_key and not out.get("pdf_path"):
            out["pdf_path"] = pdf_by_key[k]
        return out

    investigation["references"] = [_enrich_ref(r) for r in (investigation.get("references") or [])]

    def _norm_input(item: Any) -> dict:
        if isinstance(item, str):
            return {"name": item.rsplit("/", 1)[-1], "path": item}
        if isinstance(item, dict):
            out = dict(item)
            p = out.get("path") or out.get("url") or ""
            if not out.get("name"):
                out["name"] = (p.rsplit("/", 1)[-1] if p else "") or "(unnamed)"
            return out
        return {"name": str(item)}

    investigation["datasets"] = [_norm_input(d) for d in (investigation.get("datasets") or [])]
    investigation["expert_docs"] = [_norm_input(d) for d in (investigation.get("expert_docs") or [])]

    return {"investigation": investigation, "global": global_block, "current": current}


def build_iset_detail(ws_root: Path, name: str) -> Optional[dict]:
    """GET /api/investigation/<name> builder.

    Returns the full investigation-detail dict, or ``None`` when the
    ``investigation.yaml`` does not exist.

    Mirrors ``server.Handler._iset_detail_data`` parameterised on ``ws_root``
    instead of the WORKSPACE global, and with ``_ws_add_to_sys_path``
    replaced by an inline sys.path insert.
    """
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    spec_path = wp.investigations / name / "investigation.yaml"
    if not spec_path.is_file():
        return None
    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None

    # Make the workspace's own package importable (mirrors _ws_add_to_sys_path).
    ws_str = str(ws_root)
    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)

    from vivarium_workbench.lib.investigations import (  # noqa: PLC0415
        load_spec,
        InvestigationSpecError,
        normalize_dag_edges,
    )
    from vivarium_workbench.lib.run_commands import study_run_commands  # noqa: PLC0415
    from vivarium_workbench.lib.composite_lookup import (  # noqa: PLC0415
        composite_steps_index, baseline_steps_for_study,
    )

    # Composite default_n_steps map (best-effort) → a `--steps N` hint on each
    # study's run command, N being its baseline composite's natural run length.
    _steps_index = composite_steps_index(ws_root)
    _study_step_values: list[int] = []

    def _normalize_parents(study_spec: dict) -> list:  # type: ignore[return]
        return normalize_dag_edges(study_spec)

    studies_out: list[dict] = []
    for slug in investigation_member_slugs(spec):
        try:
            sp = wp.study_dir(slug, must_exist=True) / "study.yaml"
        except FileNotFoundError:
            sp = wp.investigations / slug / "spec.yaml"
        if not sp.is_file():
            studies_out.append({
                "name": slug, "status": "missing", "error": "study.yaml not found"
            })
            continue
        try:
            study_spec = load_spec(sp)
        except InvestigationSpecError as e:
            studies_out.append({"name": slug, "status": "invalid", "error": str(e)})
            continue

        sim_set = study_spec.get("simulation_set") or []
        beh_tests = (
            study_spec.get("behavior_tests")
            or study_spec.get("expected_behavior")
            or []
        )
        readouts = study_spec.get("readouts") or study_spec.get("observables") or []
        purpose = study_spec.get("purpose") or {}
        question = (
            (purpose.get("question") if isinstance(purpose, dict) else None)
            or study_spec.get("question", "")
        )
        follow_ups = study_spec.get("follow_up_studies") or []
        disc_impl = study_spec.get("discovery_implications") or {}
        disc_followups = (
            (disc_impl.get("followup_study_proposals") if isinstance(disc_impl, dict) else None)
            or []
        )
        findings = _enrich_findings_with_weight(study_spec)
        # Deterministic evidence chain for the DAG node: the raw study.yaml load
        # here has no enriched report_card_urls (that only happens in the study
        # detail loader), so a study whose only evidence is report cards would
        # render "pending". Discover its cards and derive one finding per card
        # when none are authored — so the node's "Finds:"/claim populate.
        node_claim = study_spec.get("claim")
        if not findings:
            try:
                from vivarium_workbench.lib.study_spec import (  # noqa: PLC0415
                    report_card_findings_for_study,
                    _VERDICT_SEVERITY,
                )
                derived, _rc_urls = report_card_findings_for_study(
                    ws_root, study_spec["name"], None)
                if derived:
                    findings = _enrich_findings_with_weight(
                        {**study_spec, "findings": derived})
                    if not node_claim:
                        # Headline the most-severe card's summary.
                        _worst = max(
                            derived,
                            key=lambda f: _VERDICT_SEVERITY.get(
                                (f or {}).get("verdict") or "", 0),
                        )
                        node_claim = (_worst.get("summary")
                                      or _worst.get("statement"))
            except Exception:  # noqa: BLE001
                pass
        n_runs_for_study = _count_runs_for_study(ws_root, study_spec["name"], study_spec)
        raw_status = study_spec.get("status", "planned")
        _active_run = _has_active_run_for_study(ws_root, study_spec["name"], study_spec)
        # The graph node's effective status comes from the SAME canonical helper
        # the flat /api/investigations index uses -- _study_display_status: the
        # multi-axis truth (gate > evaluation > simulation > implementation >
        # design > expert_review), with `running` gated on a real active run so a
        # stale `status: running` is demoted. Was
        # _compute_study_effective_status(raw `status`, ...), which read ONLY the
        # legacy top-level `status:` field -- so an `evaluation_status: evaluated`
        # study whose stale `status:` still said `planned` showed as "planned" in
        # the graph card while the sidebar (multi-axis) showed it done. Sharing
        # one helper makes the rail dot and the card badge agree by construction.
        _eff_status = _study_display_status(ws_root, study_spec["name"], study_spec)
        # Safeguard: a study can carry a COMPLETED evaluation as committed
        # report-card verdicts (viz/report_card/*.verdict.json) yet be left at a
        # pre-eval status (e.g. `build`) with no local runs.db — its report cards
        # then never surface and it reads as "investigating" even though the
        # graded analysis is done and committed. When such cards exist and
        # nothing is actively running, treat it as evaluated so the cards show.
        if (_eff_status in _PRE_EVAL_STATUSES and not _active_run
                and study_spec.get("report_cards")):
            try:
                from vivarium_workbench.lib.study_spec import (  # noqa: PLC0415
                    has_graded_report_cards,
                )
                if has_graded_report_cards(ws_root, study_spec["name"]):
                    _eff_status = "evaluated"
            except Exception:  # noqa: BLE001
                pass
        # Rigor count-split (viva-superpowers #285): pins vs acceptance vs
        # expected-fail counted separately, so the investigation view can show
        # the split ledger instead of one conflated gate result. Best-effort.
        try:
            from vivarium_workbench.lib.gate_rigor import verdict_count_split  # noqa: PLC0415
            _count_split = verdict_count_split(study_spec)
        except Exception:  # noqa: BLE001
            _count_split = None
        studies_out.append({
            "name":                  study_spec["name"],
            "status":                raw_status,
            "effective_status":      _eff_status,
            "phase":                 study_spec.get("phase"),
            "title":                 study_spec.get("title"),
            "question":              question,
            "n_variants":            len(sim_set) if sim_set else len(study_spec.get("variants") or []),
            "n_interventions":       len(study_spec.get("interventions") or []),
            "n_runs":                n_runs_for_study,
            "baseline_source":       _format_baseline_source(study_spec),
            "parent_studies":        _normalize_parents(study_spec),
            "n_behaviors":           len(beh_tests),
            "n_readouts":            len(readouts),
            "n_requirements":        len(_normalize_requirements(
                study_spec.get("implementation_requirements")
                or study_spec.get("gaps")
            )),
            "n_followups":           len(disc_followups) or len(follow_ups),
            "follow_up_studies":     follow_ups,
            "discovery_implications": disc_impl,
            "n_findings":            len(findings),
            "findings":              findings,
            "claim":                 node_claim,
            "confidence":            study_spec.get("confidence"),
            "design_status":         study_spec.get("design_status"),
            "implementation_status": study_spec.get("implementation_status"),
            "simulation_status":     study_spec.get("simulation_status"),
            "evaluation_status":     study_spec.get("evaluation_status"),
            "gate_status":           study_spec.get("gate_status"),
            "expert_review_status":  study_spec.get("expert_review_status"),
            # viva-superpowers #285 — the split verdict ledger (or None).
            "verdict_count_split":   _count_split,
            # Spine A2: persisted coded gate_evaluator.
            "computed_gate_verdict": (
                (study_spec.get("pipeline_gate") or {}).get("gate_evaluator")
                if isinstance(
                    (study_spec.get("pipeline_gate") or {}).get("gate_evaluator"), dict
                )
                else None
            ),
            "derived": _study_derivations.derived_block(study_spec),
            # Task 9: canonical CLI run-command strings (single source of truth)
            # so the investigation-report SPA can render run-command chips off the
            # projection without re-deriving — mirrors Task 6's wiring of
            # load_study_detail_spec for /api/study/<slug>.
            "run_commands": study_run_commands(
                study_spec, study_spec.get("name") or "",
                steps=(_study_steps := baseline_steps_for_study(study_spec, _steps_index)),
            ),
        })
        if _study_steps:
            _study_step_values.append(_study_steps)

    member_statuses = [s.get("status", "planning") for s in studies_out]
    member_has_runs = [(s.get("n_runs") or 0) > 0 for s in studies_out]
    effective_status = compute_investigation_status(
        member_statuses, has_runs=member_has_runs,
    )

    computed_acceptance: Optional[dict] = None
    try:
        from viva_superpowers.investigation_status import roll_up_acceptance
        from viva_superpowers import study_io as _sio
        studies_by_name: dict = {}
        for _sd in wp.iter_study_dirs():
            _syp = _sd / "study.yaml"
            if _syp.exists():
                try:
                    studies_by_name[_sd.name] = _sio.load_yaml_mapping(_syp)
                except Exception:  # noqa: BLE001
                    pass
        computed_acceptance = roll_up_acceptance(spec, studies_by_name)
        persisted_acc = (spec.get("executive") or {}).get("computed_acceptance")
        if isinstance(persisted_acc, dict) and isinstance(computed_acceptance, dict):
            if "diverges_from_authored" in persisted_acc:
                computed_acceptance["diverges_from_authored"] = (
                    persisted_acc.get("diverges_from_authored")
                )
    except Exception:  # noqa: BLE001
        pass

    return {
        "name":                spec.get("name", name),
        "title":               spec.get("title", spec.get("name", name)),
        "description":         spec.get("description", ""),
        "lead":                spec.get("lead", ""),
        "at_a_glance":         spec.get("at_a_glance") or [],
        "how_to_read":         spec.get("how_to_read") or [],
        "glossary":            spec.get("glossary") or [],
        "biological_story":    spec.get("biological_story", ""),
        "question":            spec.get("question", ""),
        "hypothesis":          spec.get("hypothesis", ""),
        "object_of_evaluation": spec.get("object_of_evaluation"),
        "status":              spec.get("status", "planning"),
        "effective_status":    effective_status,
        "expert_docs":         _coerce_list_field(spec, "expert_docs", source=str(spec_path)),
        "acceptance_criteria": _coerce_list_field(spec, "acceptance_criteria", source=str(spec_path)),
        "computed_acceptance": computed_acceptance,
        "executive":           spec.get("executive") or {},
        "scientific_argument": spec.get("scientific_argument") or {},
        "references":          (spec.get("inputs") or {}).get("references") or [],
        "proposed_inputs":     spec.get("proposed_inputs") or {},
        "studies":             studies_out,
        # Investigation-level run length hint (the longest member study's baseline
        # composite run) → the `vwb run investigation <slug> --steps N` chip.
        "default_n_steps":     (max(_study_step_values) if _study_step_values else None),
    }


# Register this module's cache-clear with the active-workspace registry so a
# workspace switch invalidates it via active_workspace.invalidate().
from . import active_workspace as _aw  # noqa: E402
_aw.register_clear_cb(clear_cache)
