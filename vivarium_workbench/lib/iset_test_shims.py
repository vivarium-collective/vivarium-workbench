"""Investigation summary/detail builders retained for external consumers.

These three functions (``_build_iset_summary_for_test``,
``_build_iset_detail_for_test``, and the ``_read_study_*`` helpers they use)
were relocated verbatim from the retired ``vivarium_workbench.server`` so that
external repos which still ``from vivarium_workbench.server import
_build_iset_summary_for_test`` / ``_build_iset_detail_for_test`` (e.g.
pbg-superpowers) keep working via the server deprecation shim. The dashboard's
own FastAPI seam uses ``report_views.build_iset_detail`` /
``investigation_status.build_iset_summary`` instead; do not add new dependencies
on these ``*_for_test`` builders.

All functions are parameterised on ``ws_root`` (no WORKSPACE global).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib.investigation_status import (
    build_iset_summary,
    compute_investigation_status,
    read_study_status,
)
from vivarium_workbench.lib.investigations_index import _count_runs_for_study
from vivarium_workbench.lib.report_views import _coerce_list_field
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


_MULTIAXIS_STATUS_FIELDS = (
    "design_status",
    "implementation_status",
    "simulation_status",
    "evaluation_status",
    "gate_status",
    "expert_review_status",
)


def _study_has_runs(ws_root: Path):
    """Return a ``(slug, spec) -> bool`` runs-presence check for *ws_root*.

    Mirrors the retired server injection ``_count_runs_for_study(s, spec) > 0``.
    """
    return lambda slug, spec: _count_runs_for_study(ws_root, slug, spec) > 0


def _read_study_multiaxis_status(ws_root: Path, slug: str) -> dict:
    """Return ``{axis: value or None}`` for the six Pass A status axes.

    Returns all-None if the study spec is missing or unparseable.
    """
    candidates = [
        ws_root / "studies" / slug / "study.yaml",
        ws_root / "investigations" / slug / "spec.yaml",
    ]
    for sp in candidates:
        if not sp.is_file():
            continue
        try:
            spec = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
        except Exception:
            return {axis: None for axis in _MULTIAXIS_STATUS_FIELDS}
        return {axis: spec.get(axis) for axis in _MULTIAXIS_STATUS_FIELDS}
    return {axis: None for axis in _MULTIAXIS_STATUS_FIELDS}


def _read_study_discovery_implications(ws_root: Path, slug: str) -> dict:
    """Return the study's ``discovery_implications:`` block (or ``{}``)."""
    try:
        sp = WorkspacePaths.load(ws_root).study_dir(slug) / "study.yaml"
    except FileNotFoundError:
        sp = ws_root / "investigations" / slug / "spec.yaml"
    if sp.is_file():
        try:
            spec = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        di = spec.get("discovery_implications")
        return di if isinstance(di, dict) else {}
    return {}


def _build_iset_summary_for_test(ws_root: Path) -> list[dict]:
    """Build the ``/api/investigation-summaries`` payload for *ws_root*.

    Delegates to ``investigation_status.build_iset_summary`` with the runs.db-backed
    runs-presence check injected (parity with the retired server shim).
    """
    return build_iset_summary(ws_root, study_has_runs=_study_has_runs(ws_root))


def _build_iset_detail_for_test(ws_root: Path, name: str) -> tuple[dict, int]:
    """Pure builder backing ``GET /api/investigation/<name>`` — returns
    ``(response_dict, status_code)``. Minimal (testable) variant of the full
    ``report_views.build_iset_detail``; retained for external consumers.
    """
    if not name:
        return {"error": "investigation name required"}, 400
    spec_path = ws_root / "investigations" / name / "investigation.yaml"
    if not spec_path.is_file():
        return {"error": f"no investigation.yaml at {spec_path}"}, 404
    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return {"error": f"parse failed: {e}"}, 500

    has_runs_fn = _study_has_runs(ws_root)
    studies_out = []
    statuses = []
    has_runs = []
    for slug in (spec.get("studies") or []):
        status, runs = read_study_status(ws_root, slug, study_has_runs=has_runs_fn)
        statuses.append(status)
        has_runs.append(runs)
        entry = {"name": slug, "status": status}
        entry.update(_read_study_multiaxis_status(ws_root, slug))
        entry["discovery_implications"] = _read_study_discovery_implications(
            ws_root, slug)
        studies_out.append(entry)

    author_status = spec.get("status", "planning")
    effective_status = compute_investigation_status(statuses, has_runs=has_runs)
    return {
        "name":             spec.get("name", name),
        "title":            spec.get("title", spec.get("name", name)),
        "description":      spec.get("description", ""),
        "biological_story": spec.get("biological_story", ""),
        "question":         spec.get("question", ""),
        "hypothesis":       spec.get("hypothesis", ""),
        "status":           author_status,
        "effective_status": effective_status,
        "expert_docs":      _coerce_list_field(spec, "expert_docs", source=str(spec_path)),
        "acceptance_criteria": _coerce_list_field(spec, "acceptance_criteria", source=str(spec_path)),
        "references":          (spec.get("inputs") or {}).get("references") or [],
        "proposed_inputs":     spec.get("proposed_inputs") or {},
        "studies":          studies_out,
    }, 200
