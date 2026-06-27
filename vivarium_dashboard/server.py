"""Local HTTP server: serves reports/, exposes /api/state, /api/events SSE, /api/guidance.

v0.1.7: adds mutating POST endpoints with auto-branch/commit, /api/branches, /api/run-tests,
and /api/render for post-action page reload.
v0.1.9: drag-drop file uploads (base64) + sha256 reproducibility for datasets, references PDFs,
and expert docs.
v0.1.10: PDF-first reference flow (/api/reference-pdf); legacy BibTeX paste renamed to
/api/reference-bibtex; BibTeX auto-generated from typed metadata via _lib.bibtex.
v0.1.12: /api/reference-pdf is now drop-and-go; pypdf extracts metadata from the PDF so no
typed fields are required. Auto-generates bib_key. Sets _metadata_pending flag when extraction
is incomplete.
v0.3.0: schema v2 — workspace IS the model. All endpoints drop model scoping.
  /api/observable, /api/visualization, /api/run-tests now operate on top-level workspace state directly. Pending-visibility helper
  added: unmerged stage/* branches surface entries with a "(pending review)" badge.
v0.3.7-A: /api/import-install — pip-install an import into the workspace venv; marks
  installed=True + install_path in workspace.yaml; invalidates registry cache.
v0.4.1: /api/catalog (GET) + /api/catalog-install (POST) — Registry as package manager.
  Catalog browsing + one-click submodule add + pip install + pyproject.toml edit.
v0.4.2: Visualization lifecycle — Create/Add/Commit.
  /api/visualization-create (POST), /api/visualization-status (GET),
  /api/visualization-add-to-project (POST), /api/visualization-commit-batch (POST).
  description becomes the only required field alongside name; structured fields optional.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import html as _html
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

import yaml

from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
from vivarium_dashboard.lib.atomic_io import atomic_write_text
from vivarium_dashboard.lib import git_status as _git_status_lib
from vivarium_dashboard.lib import composite_subprocess as _composite_subprocess
from vivarium_dashboard.lib import study_run_state as _study_run_state
from vivarium_dashboard.lib import study_run_post as _study_run_post
from vivarium_dashboard.lib import study_runs as _study_runs
from vivarium_dashboard.lib import comparative_runs as _comparative_runs
from vivarium_dashboard.lib import investigation_views as _inv_views
from vivarium_dashboard.lib import study_spec as _study_spec_lib
from vivarium_dashboard.lib import rigor_views as _rigor_views
from vivarium_dashboard.lib import investigation_status as _invstatus
from vivarium_dashboard.lib import data_sources as _data_sources_lib
from vivarium_dashboard.lib import saved_visualizations as _savedviz_lib
from vivarium_dashboard.lib import registry as _registry_lib
from vivarium_dashboard.lib import composite_state_views as _composite_state_views
from vivarium_dashboard.lib import observables_views as _obs_views
from vivarium_dashboard.lib import report_views as _report_views
from vivarium_dashboard.lib import active_workspace
from vivarium_dashboard.lib import study_viz_views as _study_viz
from vivarium_dashboard.lib import system_info as _system_info_lib
from vivarium_dashboard.lib import download_views as _download_views
from vivarium_dashboard.lib import events as _events_lib
from vivarium_dashboard.lib import metadata_mutations as _meta_mut
from vivarium_dashboard.lib import study_crud_mutations as _study_crud_lib
from vivarium_dashboard.lib import lifecycle_mutations as _lifecycle_mut
from vivarium_dashboard.lib import scaffold_mutations as _scaffold_mut
from vivarium_dashboard.lib import compare_group_mutations as _compare_grp_mut
from vivarium_dashboard.lib import viz_commit_mutations as _viz_commit_mut
from vivarium_dashboard.lib import upload_mutations as _upload_mut
from vivarium_dashboard.lib import reference_mutations as _reference_mut
from vivarium_dashboard.lib import composite_mutations as _composite_mut
from vivarium_dashboard.lib import investigation_viz_mutations as _inv_viz_mut
from vivarium_dashboard.lib.investigations_index import (
    _conclusions_excerpt,
    _format_baseline_source,
    _http_get_json,
)
from vivarium_dashboard.lib.registry import (
    clear_registry_cache,
    _dashboard_config,
    _registry_modules_override,
    _modules_override_pkgs,
    _registry_include_pkgs,
    _build_reexport_map,
    _apply_registry_include_filter,
    _mark_default_emitter,
    _registry_imports_meta,
)
from vivarium_dashboard.lib.composite_lookup import _dedupe_alias_composites
from vivarium_dashboard.lib.catalog import (
    build_catalog,
    _detect_workspace_venv_distributions,
    _read_workspace_pyproject_deps,
)
from vivarium_dashboard.lib.investigation_status import (
    compute_investigation_status,
    _STUDY_STATUS_FAILED,
    _STUDY_STATUS_COMPLETE,
    _STUDY_STATUS_DONE_ROLLUP,
    _STUDY_STATUS_RUNNING,
    _STUDY_STATUS_PLANNED,
)


def _strip_process_instances(state):
    return _composite_subprocess.strip_process_instances(state)


def _structured_array_to_json(o):
    """Serialize a NumPy structured array preserving its field names; else None.

    - With an ``id`` field (bulk molecules): an ``{id: count}`` map when a
      ``count`` field exists, otherwise ``{id: {other fields}}``.
    - Otherwise (unique molecules, etc.): a list of ``{field: value}`` records.

    Returns None for anything that isn't a 1-D+ structured array, so the caller
    falls through to its normal handling.
    """
    names = getattr(getattr(o, "dtype", None), "names", None)
    if not names or getattr(o, "ndim", 0) < 1:
        return None
    try:
        rows = o.tolist()  # list of per-row tuples
        records = [dict(zip(names, row)) for row in rows]
    except Exception:
        return None
    if "id" in names:
        if "count" in names:
            return {str(r["id"]): r["count"] for r in records}
        return {str(r["id"]): {k: v for k, v in r.items() if k != "id"} for r in records}
    return records


def _json_default(o):
    """JSON serialization fallback for objects json.dumps can't handle natively.

    Handles numpy arrays (which @composite_generator state docs often contain
    for spatial / field-based composites), numpy scalars, Path objects, sets,
    and anything with .tolist(). Falls back to repr() so a bad object still
    surfaces a string rather than killing the whole response.
    """
    # NumPy STRUCTURED array (a dtype with named fields, e.g. a bulk-molecule
    # array `(id, count, …submasses)` or a unique-molecule array `(unique_index,
    # domain_index, …)`). A plain `.tolist()` degrades each row to a positional
    # tuple, dropping the field names — which is why viewers render these stores
    # as meaningless 0,1,2,… indices. Preserve the field names so any consumer
    # shows real labels: an array with an `id` field becomes an {id: count} (or
    # {id: record}) map; otherwise a list of field-keyed records.
    structured = _structured_array_to_json(o)
    if structured is not None:
        return structured

    # numpy duck-typing without importing numpy (cheaper boot)
    tolist = getattr(o, "tolist", None)
    if callable(tolist):
        try:
            return tolist()
        except Exception:
            pass
    if hasattr(o, "item") and callable(o.item):
        try:
            return o.item()  # numpy scalar → python scalar
        except Exception:
            pass
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    if isinstance(o, Path):
        return str(o)
    return repr(o)


def _json_sanitize(obj):
    """Recursively replace non-finite floats (inf/-inf/nan) with None.

    json.dumps emits bare ``Infinity`` / ``NaN`` tokens for these — valid Python
    but invalid JSON, which a browser's JSON.parse rejects.

    Non-native objects (numpy arrays, etc.) are normalized once through
    _json_default so inf/nan buried inside them is caught too — but only when
    that conversion yields a JSON-native value. If _json_default returns yet
    another non-native object (e.g. a pint Quantity, whose .item() is itself a
    Quantity), the ORIGINAL object is left untouched for the final
    json.dumps(default=_json_default) pass to handle — recursing on it would
    loop forever.
    """
    if obj is None or isinstance(obj, (int, str)):  # int covers bool
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sanitize(v) for v in obj]
    converted = _json_default(obj)
    if isinstance(converted, (dict, list, tuple, float, int, str)) or converted is None:
        return _json_sanitize(converted)
    return obj


def _json_body(data) -> bytes:
    """Serialize ``data`` to spec-compliant JSON bytes.

    Fast path: dump with ``allow_nan=False`` (raises on non-finite floats). Only
    when that raises do we walk the structure and replace inf/nan with null —
    so all-finite payloads (the common case) pay nothing extra.
    """
    try:
        return json.dumps(data, default=_json_default, allow_nan=False).encode()
    except ValueError:
        return json.dumps(
            _json_sanitize(data), default=_json_default, allow_nan=False
        ).encode()


# ---------------------------------------------------------------------------
# Registry cache (module-level, shared across requests)
# ---------------------------------------------------------------------------
# The authoritative cache now lives in vivarium_dashboard.lib.registry.
# _REGISTRY_CACHE here is a reference alias kept for any residual direct
# reads inside this module; mutations MUST go through clear_registry_cache()
# or build_registry() (both imported from lib.registry above).
_REGISTRY_CACHE = _registry_lib._REGISTRY_CACHE   # type: ignore[attr-defined]
_REGISTRY_TTL = _registry_lib._REGISTRY_TTL       # type: ignore[attr-defined]

# SP4a linkage-index cache now lives in lib.report_views (TTL-cached pure
# derive over the workspace YAML). Cleared on workspace switch via
# _report_views.clear_cache() in _invalidate_workspace_caches.

# The /api/composite-state build cache now lives in lib.composite_state_views
# (TTL-cached subprocess composite build). Cleared on workspace switch via
# _composite_state_views.clear_cache() in _invalidate_workspace_caches.

# Cache of the /api/composites list per workspace path. Discovery runs in a
# SUBPROCESS (clean import — the long-running server's stale import state misses
# @composite_generator entries; SP2b), which is ~slow, so cache the result and
# invalidate on workspace switch. {ws_path: payload_dict}
_COMPOSITES_LIST_CACHE: dict = {}

# Serializes runtime workspace re-pointing (SP2). A switch must not interleave
# with another switch.
_SWITCH_LOCK = Lock()


def _invalidate_workspace_caches() -> None:
    """Clear every cache keyed to the active workspace. Called ONLY from
    _switch_active_workspace, so the invalidation surface is auditable."""
    # Clear every workspace-keyed LIB cache via the active-workspace registry.
    # Each lib cache module (registry, report_views, observables_views,
    # composite_state_views, data_sources) registers its clear_cache() at import,
    # so this fires the identical set the old inline clears did.
    # NB: registration happens at MODULE IMPORT — these 5 are imported at the top
    # of server.py, so the registry is complete before any switch fires. If a
    # cache module is ever made lazy-imported, register its clear_cache()
    # explicitly or it will silently stop being invalidated on workspace switch.
    active_workspace.invalidate()
    # Server-local caches stay inline (server-internal; move/retire at the flip).
    _COMPOSITES_LIST_CACHE.clear()
    _RUN_STORE_SUMMARY_CACHE.clear()
    _WP_CACHE.clear()


def _switch_active_workspace(new_root: Path) -> None:
    """Re-point the active workspace in-process: update the WORKSPACE global +
    lib._root, then invalidate all workspace-keyed caches. Serialized by lock."""
    from vivarium_dashboard.lib._root import set_workspace_root
    global WORKSPACE
    with _SWITCH_LOCK:
        WORKSPACE = Path(new_root).resolve()
        set_workspace_root(WORKSPACE)
        _invalidate_workspace_caches()

# Canonical investigation Overview status values (see Task A3.5 / set-overview).
_VALID_OVERVIEW_STATUSES = {"draft", "in-progress", "completed", "archived"}

# ---------------------------------------------------------------------------
# Study-alias route tables
# ---------------------------------------------------------------------------
# GET routes: do_GET rewrites self.path at entry using this table so the rest
# of the dispatch chain only sees /api/investigation-* paths.  Each entry is
# (existing_prefix, alias_prefix) — matching alias paths are rewritten in-place.
_GET_STUDY_ALIASES: list[tuple[str, str]] = [
    ("/api/investigations",            "/api/studies"),
    ("/api/investigation-viz-html",    "/api/study-viz-html"),
    ("/api/investigation-composites",  "/api/study-composites"),
    ("/api/investigation-state-tree",  "/api/study-state-tree"),
    # /api/investigation/<name>  →  /api/study/<name>
    ("/api/investigation/",            "/api/study/"),
]

# POST routes use a dict keyed on the exact path.  Each entry maps an
# existing investigation route to its study alias.
_POST_STUDY_ALIASES: dict[str, str] = {
    "/api/investigation-create":             "/api/study-create",
    "/api/investigation-delete":             "/api/study-delete",
    # /api/study-run-baseline is now a v3-native route (not an alias), so it
    # intentionally maps to _post_study_run_baseline, not _post_investigation_run.
    # /api/study-run-variant is now a v3-native route (not an alias), so it
    # intentionally maps to _post_study_run_variant, not _post_investigation_run_one.
    "/api/investigation-render-viz":         "/api/study-viz-render",
    "/api/investigation-add-viz":            "/api/study-viz-add",
    # /api/study-run-delete is now a v3-native route (not an alias).
    # /api/study-runs-clear is now a v3-native route (not an alias).
    # /api/study-variant-add is now a v3-native route (not an alias), so it
    # intentionally maps to _post_study_variant_add, not _post_investigation_composite_perturb.
    "/api/investigation-composite-rebuild":  "/api/study-variant-rebuild",
    "/api/investigation-set-observables":    "/api/study-set-observables",
    "/api/investigation-set-conclusions":    "/api/study-set-conclusion",
    "/api/investigation-set-overview":       "/api/study-set-description",
    # /api/study-comparison-add is now a v3-native route (not an alias).
    "/api/investigation-comparison-update":  "/api/study-comparison-update",
    "/api/investigation-group-add":          "/api/study-group-add",
    "/api/investigation-group-update":       "/api/study-group-update",
}

# Module-level POST route map (route → handler method name).  Used by do_POST
# and inspectable by tests without instantiating the handler.
_POST_ROUTE_MAP: dict[str, str] = {
    "/api/click":              "_post_click",
    "/api/feedback-import":    "_post_feedback_import",
    # GitHub auth (Phase B-bis, cherry-picked from #65). Lets users sign in
    # via the dashboard UI instead of a terminal — no AI agent required.
    "/api/auth/github/start":  "_post_auth_github_start",
    "/api/auth/github/logout": "_post_auth_github_logout",
    "/api/import":             "_post_import",
    "/api/import-install":     "_post_import_install",
    "/api/dataset":            "_post_dataset",
    "/api/reference-pdf":      "_post_reference_pdf",
    "/api/reference-bibtex":   "_post_reference",
    # Legacy alias kept for backward compat (v0.1.9 and earlier).
    "/api/reference":          "_post_reference",
    "/api/expert-doc":         "_post_expert_doc",
    "/api/observable":         "_post_observable",
    "/api/visualization":                "_post_visualization",
    "/api/visualization-create":         "_post_visualization_create",
    "/api/visualization-add-to-project": "_post_visualization_add_to_project",
    "/api/visualization-commit-batch":   "_post_visualization_commit_batch",
    "/api/visualization-preview":          "_post_visualization_preview",
    "/api/visualization-preview-instance": "_post_visualization_preview_instance",
    "/api/visualization-generate":         "_post_visualization_generate",
    "/api/visualization-accept":           "_post_visualization_accept",
    "/api/simulation":                   "_post_simulation",
    "/api/run-tests":          "_post_run_tests",
    "/api/render":             "_post_render",
    "/api/study-report-single": "_post_study_report_single",
    "/api/work-start":         "_post_work_start",
    "/api/work-push":          "_post_work_push",
    "/api/work-attach-report": "_post_work_attach_report",
    "/api/work-link-branch":   "_post_work_link_branch",
    "/api/work-create-pr":     "_post_work_create_pr",
    "/api/work-end":           "_post_work_end",
    "/api/dirty-commit-all":   "_post_dirty_commit_all",
    "/api/catalog-install":    "_post_catalog_install",
    "/api/catalog-uninstall":  "_post_catalog_uninstall",
    "/api/system-deps-install": "_post_system_deps_install",
    "/api/open-window":        "_post_open_window",
    "/api/suggest":            "_post_suggest",
    "/api/composite-test-run": "_post_composite_test_run",
    "/api/iset-create":         "_post_iset_create",
    "/api/iset-clone":          "_post_iset_clone",
    "/api/investigation-create":      "_post_investigation_create",
    "/api/investigation-delete":      "_post_investigation_delete",
    "/api/investigation-run":         "_post_investigation_run",
    "/api/investigation-render-viz":  "_post_investigation_render_viz",
    "/api/investigation-add-viz":     "_post_investigation_add_viz",
    "/api/investigation-run-delete":  "_post_investigation_run_delete",
    "/api/investigation-runs-clear":  "_post_investigation_runs_clear",
    "/api/investigation-run-one":     "_post_investigation_run_one",
    "/api/investigation-create-from-composite":  "_post_investigation_create_from_composite",
    "/api/investigation-composite-add":          "_post_investigation_composite_add",
    "/api/investigation-composite-perturb":      "_post_investigation_composite_perturb",
    "/api/investigation-composite-rebuild":      "_post_investigation_composite_rebuild",
    "/api/composite-promote-to-catalog":         "_post_composite_promote_to_catalog",
    "/api/investigation-set-observables":    "_post_investigation_set_observables",
    "/api/investigation-set-conclusions":    "_post_investigation_set_conclusions",
    "/api/investigation-set-overview":       "_post_investigation_set_overview",
    "/api/investigation-comparison-add":     "_post_investigation_comparison_add",
    "/api/investigation-comparison-update":  "_post_investigation_comparison_update",
    "/api/investigation-group-add":          "_post_investigation_group_add",
    "/api/investigation-group-update":       "_post_investigation_group_update",
    # Study-specific POST endpoints (no investigation alias).
    "/api/study-set-objective":         "_post_study_set_objective",
    "/api/study-narrative-set":         "_post_study_narrative_set",
    "/api/study-expert-input-set":      "_post_study_expert_input_set",
    "/api/study-rename":                "_post_study_rename",
    "/api/investigation-run-unblocked": "_post_investigation_run_unblocked",
    "/api/study-create-from-run":       "_post_study_create_from_run",
    "/api/study-run-baseline":          "_post_study_run_baseline",
    "/api/study-run-all-baselines":     "_post_study_run_all_baselines",
    "/api/study-run-variant":           "_post_study_run_variant",
    "/api/study-variant-add":           "_post_study_variant_add",
    "/api/study-variant-delete":        "_post_study_variant_delete",
    "/api/study-variant-set-params":    "_post_study_variant_set_params",
    "/api/study-baseline-add":          "_post_study_baseline_add",
    "/api/study-baseline-remove":       "_post_study_baseline_remove",
    "/api/study-intervention-add":      "_post_study_intervention_add",
    "/api/study-intervention-update":   "_post_study_intervention_update",
    "/api/study-intervention-delete":   "_post_study_intervention_delete",
    "/api/study-run-delete":            "_post_study_run_delete",
    "/api/study-runs-clear":            "_post_study_runs_clear",
    "/api/study-comparison-add":        "_post_study_comparison_add",
    "/api/study-tests-run":             "_post_study_tests_run",
    "/api/study-seed-followup":         "_post_study_seed_followup",
    "/api/feedback-apply-action":       "_post_feedback_apply_action",
    "/api/study-sync-runs":             "_post_study_sync_runs",
    "/api/investigation-set-status":    "_post_investigation_set_status",
    "/api/proposed-input-decision":     "_post_proposed_input_decision",
    # Workspace-switcher POST endpoints.
    "/api/workspaces/add":           "_post_workspaces_add",
    "/api/workspaces/forget":        "_post_workspaces_forget",
    "/api/workspaces/cleanup-stale": "_post_workspaces_cleanup_stale",
    "/api/workspaces/start":         "_post_workspaces_start",
    "/api/workspaces/stop":          "_post_workspaces_stop",
    "/api/source/switch":            "_post_source_switch",
    "/api/source/switch-build":      "_post_source_switch_build",
    "/api/branch/push":              "_post_branch_push",
    "/api/source/build-remote":      "_post_source_build_remote",
    # Remote-run endpoints (Phase 3b).
    "/api/remote-run-start":         "_post_remote_run_start",
}
# Inject study-alias routes into the POST route map (same method name as old).
for _old, _new in _POST_STUDY_ALIASES.items():
    if _old in _POST_ROUTE_MAP:
        _POST_ROUTE_MAP[_new] = _POST_ROUTE_MAP[_old]
del _old, _new  # clean up loop variables from module scope

_DELETE_ROUTE_MAP: dict[str, str] = {
    "/api/simulation":               "_delete_simulation",
    "/api/simulation-run":           "_delete_simulation_run",
    "/api/visualization":            "_delete_visualization",
    "/api/investigation-composite":  "_delete_investigation_composite",
    "/api/investigation-comparison": "_delete_investigation_comparison",
    "/api/investigation-group":      "_delete_investigation_group",
}


def _get_registry_data(bypass_cache: bool = False) -> dict:
    """Thin wrapper — delegates to ``lib.registry.build_registry`` with the
    module-level ``WORKSPACE`` global.

    All logic (subprocess discovery, caching, post-processing) now lives in
    ``vivarium_dashboard.lib.registry.build_registry`` so the FastAPI seam
    can call it without importing this module.
    """
    return _registry_lib.build_registry(WORKSPACE, bypass_cache=bypass_cache)


# _mark_default_emitter, _dashboard_config — moved to lib/registry.py (imported above).

# ---------------------------------------------------------------------------
# Repo-wide data sources (workspace.yaml dashboard.data_sources provider hook)
# ---------------------------------------------------------------------------
# A workspace may declare a data-source bundle provider:
#
#   dashboard:
#     data_sources:
#       provider: "pkg.module:func"   # importable module:callable
#       label: "ecoli-sources"
#
# The provider takes no args and returns a list of dicts, one per file:
#   {key, path (abs str), category, kind: "override"|"inherited", size_bytes}
#
# Optional — workspaces without it keep current behavior ({sources: []}).
# Data-source enumeration + caching moved to lib.data_sources; thin shims below
# delegate to it (names retained for the existing handler call-sites).
_data_sources_config = _data_sources_lib.data_sources_config


_import_provider = _data_sources_lib.import_provider


def _enumerate_data_sources(bypass_cache: bool = False) -> dict:
    """Back-compat shim — delegates to lib.data_sources.enumerate_data_sources
    for the module-level WORKSPACE."""
    return _data_sources_lib.enumerate_data_sources(WORKSPACE, bypass_cache)


# Map of file extension → (content-type, inline?) for serving a data-source
# file moved to lib.download_views (single source). Re-exported here for any
# back-compat reference.
_DATA_SOURCE_MIME = _download_views._DATA_SOURCE_MIME


# _registry_modules_override, _modules_override_pkgs, _registry_imports_meta,
# _registry_include_pkgs, _build_reexport_map, _apply_registry_include_filter
# — all moved to lib/registry.py (imported above).
# _filter_catalog_modules, _build_override_catalog, _build_reexport_origin_modules,
# _name_variants, _check_installed_module_sync (ws_root-parameterized),
# _CATALOG_VENV_PROBE_SCRIPT, _detect_workspace_venv_distributions,
# _read_workspace_pyproject_deps — all moved to lib/catalog.py (imported above).
# _dedupe_alias_composites — moved to lib/composite_lookup.py (imported above).


def _composite_top_pkg(rec: dict) -> str:
    """Derive a composite record's top-level package (normalized).

    A composite record carries ``module`` (its dotted Python path, e.g.
    ``v2ecoli.composites.foo`` or ``spatio_flux.composites.metabolism``) and
    sometimes ``source`` (a workspace-relative or absolute path, e.g.
    ``v2ecoli/composites/foo.composite.yaml``). The package is the first dotted
    segment of ``module``; when ``module`` is empty, fall back to the first
    path segment of ``source``. Dashes are normalized to underscores so
    ``pbg-bioreactordesign`` ↔ ``pbg_bioreactordesign`` matches the allow-list.

    Returns ``""`` when neither field yields a usable package root.
    """
    mod = str(rec.get("module") or "").strip()
    if mod:
        return mod.split(".")[0].replace("-", "_")
    src = str(rec.get("source") or "").strip()
    if src:
        segs = [s for s in src.replace("\\", "/").split("/") if s.strip()]
        # Installed-package sources are absolute paths whose package dir is the
        # segment immediately before ``composites/`` (e.g.
        # ``/…/site-packages/spatio_flux/composites/x.yaml`` → ``spatio_flux``).
        # Workspace-relative sources start at the package dir itself
        # (``v2ecoli/composites/foo.yaml`` → ``v2ecoli``). Prefer the
        # before-``composites`` segment; otherwise fall back to the first.
        for i, seg in enumerate(segs):
            if seg == "composites" and i > 0:
                return segs[i - 1].split(".")[0].replace("-", "_")
        return segs[0].split(".")[0].replace("-", "_") if segs else ""
    return ""


def _filter_composites(records: list, ws_data: dict | None) -> list:
    """Apply the per-workspace registry allow-list to a list of composite dicts.

    Keeps a record when EITHER it is flagged ``workspace_local: True`` (the
    workspace's own composites are always shown) OR its top-level package (see
    :func:`_composite_top_pkg`) is in the normalized
    ``dashboard.registry.{include,modules}`` allow-list. Reuses
    :func:`_registry_include_pkgs` so dash/underscore normalization matches the
    process-registry and catalog filters.

    No-op when no allow-list is configured (``None``) → returns ``records``
    unchanged, preserving the historical "show every installed package" view.
    """
    if not isinstance(records, list):
        return records
    include = _registry_include_pkgs(ws_data)
    if include is None:
        return records

    def _keep(rec: dict) -> bool:
        if not isinstance(rec, dict):
            return False
        if rec.get("workspace_local") is True:
            return True
        return _composite_top_pkg(rec) in include

    return [r for r in records if _keep(r)]


# _build_override_catalog, _build_reexport_origin_modules — moved to lib/catalog.py.


def _save_upload(file_b64: str, target_path: Path) -> str:
    """Decode base64-encoded file content, write to target_path, return sha256."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(file_b64)
    target_path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


WORKSPACE: Path = Path("/")  # set by main()
LOCK = Lock()

# Resolve the workspace directory layout from the active WORKSPACE (its optional
# `layout:` map; flat defaults otherwise). Keyed on WORKSPACE so tests that
# monkeypatch the global pick up the right layout.
_WP_CACHE: dict[str, WorkspacePaths] = {}


def workspace_paths() -> WorkspacePaths:
    """Directory layout for the active workspace (see lib.workspace_paths)."""
    key = str(Path(WORKSPACE).resolve())
    wp = _WP_CACHE.get(key)
    if wp is None:
        wp = WorkspacePaths.load(WORKSPACE)
        _WP_CACHE[key] = wp
    return wp


def _workspace_home_data(ws_root: "Path | None" = None) -> dict:
    """Return workspace narrative metadata for GET /api/workspace and publish.

    Thin shim: delegates to ``lib.system_info.build_workspace_home`` (which is
    the single implementation).  The shim is kept so publish.py's
    ``from vivarium_dashboard.server import _workspace_home_data`` still resolves.
    """
    _root = Path(ws_root) if ws_root is not None else Path(WORKSPACE)
    return _system_info_lib.build_workspace_home(_root)


# ---------------------------------------------------------------------------
# Study slug validation
# ---------------------------------------------------------------------------

# Single-sourced from lib.study_spec so api/app.py can import it without
# importing server.py.  The alias preserves every existing call-site in
# this module (they all use _SLUG_RE).
from vivarium_dashboard.lib.study_spec import SLUG_RE as _SLUG_RE  # noqa: E402

# ---------------------------------------------------------------------------
# Study / investigation directory resolution helpers
# ---------------------------------------------------------------------------


def _study_dir(name: str):
    """Resolve a study directory, preferring the v3 ``studies/`` location
    over the legacy ``investigations/`` location.

    Thin shim: delegates to ``lib.study_spec.study_dir`` injecting WORKSPACE.
    """
    return _study_spec_lib.study_dir(WORKSPACE, name)


def _study_spec_file(study_dir):
    """Path-based variant of :func:`_study_spec_path` for handlers that already
    have a ``study_dir`` (e.g. ``*_for_test`` callers that take ``ws_root``
    explicitly rather than using the WORKSPACE global).

    Thin shim: delegates to ``lib.study_spec.study_spec_file``.
    """
    return _study_spec_lib.study_spec_file(study_dir)


def _study_spec_path(name: str):
    """Resolve a study's spec file: ``study.yaml`` (v3) or ``spec.yaml`` (legacy).

    Thin shim: delegates to ``lib.study_spec.study_spec_path`` injecting WORKSPACE.
    """
    return _study_spec_lib.study_spec_path(WORKSPACE, name)


# ---------------------------------------------------------------------------
# Pathway Tools Omics Viewer launch helper
# ---------------------------------------------------------------------------

# DEFAULT TEMPLATE — the Pathway Tools Cellular Overview Omics Viewer auto-loads
# a tab-delimited data file via URL params (verified against sms-ptools 0.8.2;
# the param format is documented in the server's celOverviewHelp.shtml):
#   omics=t        enable the Omics Viewer overlay
#   url=<datafile> the data file to paint (reachable BY the PTools server)
# DEFAULT TEMPLATE — single-sourced from lib.system_info; imported here so all
# call-sites in this module (GET /api/ui-config, _get_ptools_launch, etc.) share
# the same value without duplicating it.
from vivarium_dashboard.lib.system_info import (  # noqa: E402
    _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
)


# ---------------------------------------------------------------------------
# PTools helpers — single-sourced from lib.study_viz_views; re-exported here
# so legacy call-sites (e.g. tests/test_ptools_launch.py) keep working.
# ---------------------------------------------------------------------------
_ptools_object_class = _study_viz.ptools_object_class
_build_ptools_launch_url = _study_viz.build_ptools_launch_url


_RUN_STORE_SUMMARY_CACHE: dict = {}


def _run_store_summary(store_abs):
    """Open a run store via RunReader and return what it actually contains:
    ``{generations, sim_minutes, n_observables}``. Cached by store path (run
    stores are immutable once written). Best-effort — returns {} on any failure.

    sim_minutes = max cumulative simulated time (RunReader's ``abs_time``, in
    seconds) / 60 — the real simulation time, not wall-clock.
    """
    key = str(store_abs)
    if key in _RUN_STORE_SUMMARY_CACHE:
        return _RUN_STORE_SUMMARY_CACHE[key]
    out: dict = {}
    try:
        # Canonical framework summary (pbg_emitters.RunReader.summary): the single
        # definition of a run's quantitative shape. Recorded runs persist these
        # fields; this read-time fallback covers legacy runs recorded before that.
        from pbg_emitters.run_reader import RunReader
        out = RunReader.open(str(store_abs)).summary() or {}
    except Exception:  # noqa: BLE001 — never break the study page
        out = {}
    _RUN_STORE_SUMMARY_CACHE[key] = out
    return out


def _reconcile_simset_with_runs(sim_set, runs, ws_root=None):
    """Thin shim — delegates to lib.study_enrichment.reconcile_simset_with_runs.

    Kept here (with identical name/signature) so every existing call-site in
    the SPA handlers that reaches this function directly continues to work.
    """
    from vivarium_dashboard.lib.study_enrichment import reconcile_simset_with_runs
    return reconcile_simset_with_runs(sim_set, runs, ws_root=ws_root)


def _enrich_findings_with_weight(study_spec: dict) -> list:
    """W8 — return the study's findings with a server-computed evidential
    weight attached as ``_evidential_weight`` (the report-data path so the SPA
    just renders the chip; no JS recompute, no drift).

    Each finding gets ``_evidential_weight = {"weight", "dims", "n_supporting"}``
    via the deterministic ``pbg_superpowers.rigor.finding_evidential_weight``.
    Defensive: if the function isn't importable (older pbg-superpowers) the
    findings pass through unchanged, so the chip simply doesn't render.
    """
    findings = study_spec.get("findings") or []
    try:
        from pbg_superpowers.rigor import finding_evidential_weight
    except Exception:
        return findings
    out = []
    for f in findings:
        if isinstance(f, dict):
            try:
                w = finding_evidential_weight(study_spec, f)
            except Exception:
                w = None
            if w:
                f = {**f, "_evidential_weight": w}
        out.append(f)
    return out


def _study_detail_spec(name: str):
    """Load a study's spec for the GET /studies/<name> detail page.

    Resolves studies/ or investigations/, study.yaml or spec.yaml (via
    _study_spec_path), then runs it through load_spec so legacy v2 specs are
    migrated to the v3 shape the detail template expects. Returns None when no
    spec file exists for the name.

    Merges runs from ``studies/<name>/runs.db`` (canonical source of truth
    for CLI- and dashboard-launched runs) on top of any ``spec.runs`` already
    persisted in study.yaml. Without this merge, programmatic runs via
    ``pbg_runner`` populate the db but never appear on the Runs tab.
    """
    return _study_spec_lib.load_study_detail_spec(WORKSPACE, name)


def _study_acceptance_criterion(name: str):
    """Thin shim — delegates to lib.study_enrichment.study_acceptance_criterion.

    Kept here (with identical name/signature) so every existing call-site in
    the SPA handlers that reaches this function directly continues to work.
    """
    from vivarium_dashboard.lib.study_enrichment import study_acceptance_criterion
    return study_acceptance_criterion(WORKSPACE, name)


def _collect_study_feedback(study_slug: str) -> list[dict]:
    """Thin shim — delegates to lib.study_enrichment.collect_study_feedback.

    Kept here (with identical name/signature) so every existing call-site in
    the SPA handlers that reaches this function directly continues to work.
    """
    from vivarium_dashboard.lib.study_enrichment import collect_study_feedback
    return collect_study_feedback(WORKSPACE, study_slug)


def _compute_param_enforcement(spec: dict) -> dict | None:
    """Thin shim — delegates to lib.study_enrichment.compute_param_enforcement.

    Kept here (with identical name/signature) so every existing call-site in
    the SPA handlers that reaches this function directly continues to work.
    """
    from vivarium_dashboard.lib.study_enrichment import compute_param_enforcement
    return compute_param_enforcement(spec)


def _latest_run_row(runs_db) -> dict | None:
    """Thin forwarder; the implementation lives in lib.study_charts (one source)."""
    from vivarium_dashboard.lib.study_charts import latest_run_row
    return latest_run_row(runs_db)


def _study_charts_payload(ws_root, name: str, *, hide_superseded: bool = False) -> dict:
    """Thin forwarder to lib.study_charts.build_study_charts_payload.

    The /api/study-charts/<slug> payload assembly now lives in lib so the FastAPI
    seam (api/app.py) and this stdlib handler share one implementation.
    """
    from vivarium_dashboard.lib.study_charts import build_study_charts_payload
    return build_study_charts_payload(ws_root, name, hide_superseded=hide_superseded)


def _study_refresh_viz(ws_root, name: str) -> dict:
    """Re-render every ``visualizations[]`` entry of study ``name`` against its
    latest run, stamping provenance (pure, unit-testable seam).

    Thin orchestration around the vendored :func:`refresh_study_viz`: resolves
    the study dir (layout-aware, like :func:`_study_charts_payload`), loads
    ``study.yaml``, finds the latest run via :func:`_latest_run_row`, and
    delegates. ``refresh_study_viz`` swallows per-chart render errors and
    returns ``status="error"`` entries, so this never raises on a bad render.

    Returns ``{"study": name, "results": [...]}`` or ``{"error": ...}`` when the
    study does not exist (the HTTP wrapper maps that to 404).
    """
    import yaml as _yaml
    from .lib.refresh_viz import refresh_study_viz

    study_dir = WorkspacePaths.load(ws_root).studies / name
    if not study_dir.is_dir():
        return {"error": f"study {name!r} not found", "not_found": True}
    spec_path = study_dir / "study.yaml"
    spec = {}
    if spec_path.is_file():
        try:
            loaded = _yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                spec = loaded
        except Exception:
            spec = {}
    latest = _latest_run_row(study_dir / "runs.db")
    results = refresh_study_viz(study_dir, spec, latest)
    return {"study": name, "results": results}


def _discover_viz_html_files(name: str) -> list[dict]:
    """Discover viz HTML files for a study from BOTH conventional locations.

    Sources:
      1. ``studies/<name>/viz/*.html`` — auto-rendered by render_visualizations
         from the study's runs.db. Gated on runs.db existing (no pre-data junk).
         Stale-flagged when mtime predates the latest recorded run.
      2. ``reports/figures/<name>/*.html`` — hand-authored cross-skill output
         (e.g. matplotlib figures generated by an investigation script). NOT
         gated on runs.db, because these aren't run-derived; the author owns
         the file's currency. No stale-flag.

    Returns one dict per HTML file with the shape the study-detail template
    expects: ``{name, url, description, stale}``. The URL is workspace-relative
    so the dashboard's static-file fallback serves it.

    v2ecoli friction #17 (2026-05-19): the original unconditional glob
    surfaced eagerly-rendered ``topology.html`` / ``workflow.html`` as
    "(auto)" tabs that persisted forever on un-run studies. The first fix
    added an mtime gate that *silently dropped* any viz older than
    ``runs.db``. mem3dg-readdy (2026-05-20): that gate dropped legitimate,
    freshly-rendered charts because a WAL checkpoint on the render's own
    read connection bumped ``runs.db`` mtime a few seconds *after* the HTML
    was written — every chart vanished with no error anywhere.

    v2ecoli-pdmp friction (2026-05-25): viz files generated by investigation
    scripts land under ``reports/figures/<name>/`` (not ``studies/<name>/viz/``);
    those were invisible to auto-discovery so investigations had to author
    ``embed_visualizations:`` entries by hand. Adding ``reports/figures/<name>``
    as a second source removes that workaround.

    Robustness rule (no silent drops): surface every viz file once a study
    has actually run OR has hand-authored figures. Past-run staleness on the
    auto-rendered side is *surfaced* (stale: True), not swallowed.
    """
    return _study_spec_lib.discover_viz_html_files(WORKSPACE, name)


def _discover_investigation_viz_html_files(inv_slug: str) -> list[dict]:
    """Walk ``investigations/<inv>/viz/*.html`` and return embed entries.

    Counterpart to ``_discover_viz_html_files`` for investigation-level
    comparative visualisations (rendered by
    ``_render_investigation_comparative_visualisations`` after a
    ``run-unblocked`` job completes). Same return shape so the
    investigation-detail endpoint can splice these into
    ``embed_visualizations``.
    """
    viz_dir = workspace_paths().investigations / inv_slug / "viz"
    if not viz_dir.is_dir():
        return []
    out = []
    for html_file in sorted(viz_dir.glob("*.html")):
        size_kb = max(1, html_file.stat().st_size // 1024)
        rel = html_file.relative_to(WORKSPACE).as_posix()
        out.append({
            "name": f"{html_file.stem} (comparative)",
            "url": f"/{rel}",
            "description": (
                f"Investigation-level comparative visualisation "
                f"({size_kb} KB). Rendered by the run-unblocked worker "
                f"from the investigation yaml's comparative_visualizations block."
            ),
        })
    return out


def _study_yaml_run_rows(name: str) -> list[dict]:
    """Map a study's ``study.yaml`` ``runs:`` list to run-row dicts (the shape
    :func:`_read_runs_db_for_study` returns).

    Emitter-less workspaces (e.g. numpy-based investigations like pbg-autopoiesis)
    record each run in the spec's ``runs:`` block rather than a per-step
    ``runs.db``. Surfacing those keeps the Simulations DB / Runs tab faithful to
    what actually ran, for ANY workspace — not only ones backed by a SQLite/
    Parquet emitter. Uses a light direct YAML read (``_study_spec_path``) to
    avoid recursing through ``_study_detail_spec`` (which itself reads runs).
    """
    import yaml as _yaml
    try:
        path = _study_spec_path(name)
        if not path or not Path(path).is_file():
            return []
        spec = _yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — never let a malformed spec break the view
        return []
    if not isinstance(spec, dict):
        return []
    rows: list[dict] = []
    for r in spec.get("runs", []) or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("run_id") or r.get("name") or "").strip()
        if not rid:
            continue
        rows.append({
            "run_id":        rid,
            "spec_id":       name,
            "label":         r.get("name") or rid,
            "sim_name":      r.get("name") or rid,
            "variant":       None,
            "composite":     r.get("composite"),
            "params":        {"seed": r.get("seed")} if r.get("seed") is not None else {},
            "n_steps":       r.get("n_steps"),
            "status":        r.get("status") or "completed",
            "started_at":    r.get("started_at"),
            "completed_at":  r.get("completed_at") or r.get("started_at"),
            "generation_id": r.get("generation_id"),
            "source":        "study.yaml",
        })
    return rows


def _read_runs_db_for_study(name: str) -> list[dict]:
    """Read all runs from ``studies/<name>/runs.db`` for the Runs tab.

    Merges the ``runs_meta`` and ``simulations`` tables on ``run_id`` /
    ``simulation_id`` (same string by convention). Returns one dict per
    run with the fields the template needs: run_id, sim_name, label,
    variant, composite, params (decoded), n_steps, status, started_at_iso.
    Sorted newest-first.

    Returns ``[]`` if the db doesn't exist or has neither table.
    """
    return _study_spec_lib.read_runs_db_for_study(WORKSPACE, name)


def _iter_study_dirs():
    """Yield every study directory across studies/ and investigations/.

    Thin wrapper: delegates to the lib implementation, injecting WORKSPACE.
    """
    from vivarium_dashboard.lib.investigations_index import _iter_study_dirs as _ii_iter
    return _ii_iter(WORKSPACE)


# Saved-visualizations discovery + the parsimony feature-detect moved to
# lib.saved_visualizations; these aliases keep the existing call-sites (the
# /parsimony-viewer/* static route + the /api/saved-visualizations handler).
_parsimony_viewer_dir = _savedviz_lib.parsimony_viewer_dir


def _build_saved_visualizations(ws_root) -> dict:
    """Discover saved, interactive visualizations in the workspace.

    Scans every study dir for packed 3D scenes under ``viz/3d/*.pack.json``
    (each optionally accompanied by a sibling ``.meta.json`` + ``meshes/``)
    and for PTools TSV exports under ``**/ptools/*.tsv``.

    Returns::

        {
          "parsimony_available": bool,   # the pbg_parsimony viewer is installed
          "saved": [ {study, name, pack_url, meta_url, n_placed, created}, ... ],
          "ptools": {"configured": bool, "studies": [ {study, n_tsvs}, ... ]},
        }

    ``pack_url`` / ``meta_url`` are rooted at the served workspace tree (the
    generic static handler maps ``/<rel>`` → ``WORKSPACE/<rel>``).

    Pure (no socket I/O) so tests can call it with an explicit ``ws_root``.
    """
    return _savedviz_lib.build_saved_visualizations(ws_root)


def _iter_iset_dirs(ws_root: Path | None = None):
    """Back-compat shim — delegates to lib.investigation_status.iter_iset_dirs,
    defaulting to the module-level WORKSPACE when ws_root is omitted."""
    yield from _invstatus.iter_iset_dirs(ws_root or WORKSPACE)


# ---------------------------------------------------------------------------
# Investigation status derivation
# ---------------------------------------------------------------------------

# Slug pattern used by /api/iset-create — kebab-case only (no underscores).
# Tighter than _SLUG_RE (which allows underscores for legacy auto-generated
# study names): investigations are user-named in the dashboard UI and we
# want them to look like URL-safe slugs.
_ISET_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# The _STUDY_STATUS_* sets are imported from lib.investigation_status (top of
# file); compute_study_effective_status below uses them.


# compute_investigation_status moved to lib.investigation_status (imported at
# top of file; same name, so existing call-sites are unchanged).


def compute_study_effective_status(
    status: str, has_runs: bool = False, has_active_run: bool = False
) -> str:
    """Derive a single study's effective status from its declared status
    plus whether a run is *actively executing right now*.

    ``"running"`` means a run is genuinely in flight — NOT merely that the
    study has accumulated run history. (The earlier rule mapped any
    ``has_runs == True`` to ``"running"``, which mislabelled every study
    with completed runs as actively running.) A study that ran and finished
    but hasn't cleared its gate reflects its *declared* status instead, so
    the badge reads honestly (e.g. ``in_progress`` / ``characterization-
    complete``) rather than a misleading ``running``.

    Rules in order (first match wins):

    1. ``status in {failed, invalid}`` → ``"failed"``.
    2. ``status in {complete, ran}`` → ``"complete"``.
    3. ``status in {running, implementing, runnable, analyzing}`` OR
       ``has_active_run == True`` → ``"running"``.
    4. ``status in {planned, planning}`` → ``"planned"`` (normalized).
    5. Anything else → reflect the declared status verbatim (e.g.
       ``in_progress``, ``characterization-complete``), or ``"planned"`` when
       empty. ``has_runs`` no longer forces ``"running"``.
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


def _iset_report_file(ws_root: Path, slug: str):
    """Per-investigation report index.html (investigations/<slug>/reports/), or None.

    Thin shim — delegates to ``lib.download_views.resolve_iset_report``."""
    return _download_views.resolve_iset_report(ws_root, slug)


def _read_study_status(ws_root: Path, slug: str) -> tuple[str, bool]:
    """Back-compat shim — delegates to lib.investigation_status.read_study_status,
    injecting the server's runs.db-backed runs-presence check."""
    return _invstatus.read_study_status(
        ws_root, slug,
        study_has_runs=lambda s, spec: _count_runs_for_study(s, spec) > 0,
    )


# Pass A multi-axis status: the six independent axes added to study.yaml in
# Pass A of the infrastructure-feedback roadmap. Each is optional; absence is
# represented as ``None`` in the iset passthrough.
_MULTIAXIS_STATUS_FIELDS = (
    "design_status",
    "implementation_status",
    "simulation_status",
    "evaluation_status",
    "gate_status",
    "expert_review_status",
)


def _read_study_multiaxis_status(ws_root: Path, slug: str) -> dict:
    """Return ``{axis: value or None}`` for the six Pass A status axes.

    Mirrors :func:`_read_study_status` for fallback behavior — returns all-None
    if the study spec is missing or unparseable.
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
    """Return the study's ``discovery_implications:`` block (or ``{}``).

    Mirrors :func:`_read_study_status` resolution + fallback behavior. The
    block holds alternate hypotheses, mechanism-update proposals, and the
    richer ``followup_study_proposals`` (successor to ``follow_up_studies``).
    """
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


def _iset_lifecycle(ws_root: Path, slug: str) -> str:
    """Back-compat shim — delegates to lib.investigation_status.iset_lifecycle."""
    return _invstatus.iset_lifecycle(ws_root, slug)


def _current_branch_slug(ws_root: Path) -> str | None:
    """Back-compat shim — delegates to lib.investigation_status.current_branch_slug."""
    return _invstatus.current_branch_slug(ws_root)


def _inputs_payload(ws_root: Path, slug: str | None = None) -> dict:
    """Pure seam backing ``GET /api/inputs``.

    Thin shim — delegates to ``lib.report_views.build_inputs``.  Keeps the
    name/signature so existing call-sites (``_get_inputs`` + tests) resolve.
    """
    from vivarium_dashboard.lib import report_views as _rv
    return _rv.build_inputs(ws_root, slug)


def _set_investigation_status(ws_root: Path, inv: str, status: str) -> dict:
    """Write the ``status`` field into investigations/<inv>/investigation.yaml.

    Pure helper backing ``POST /api/investigation-set-status``. Returns a
    ``{ok, status}`` dict on success, or ``{error, _code}`` on failure (the
    HTTP handler maps ``_code`` to the response status).
    """
    inv = (inv or "").strip()
    status = (status or "").strip()
    valid = {"active", "in-progress", "planning", "completed", "archived", "closed"}
    if not inv:
        return {"error": "investigation required", "_code": 400}
    if status not in valid:
        return {"error": f"status must be one of {sorted(valid)}", "_code": 400}
    target = None
    for d in _iter_iset_dirs(ws_root):
        if d.name == inv:
            target = d / "investigation.yaml"
            break
    if target is None or not target.is_file():
        return {"error": "investigation not found", "_code": 404}
    # Prefer ruamel (round-trip preserves comments) when available; fall back
    # to safe_dump (test .venv has no ruamel; the runtime venv does).
    try:
        from ruamel.yaml import YAML as _RYAML
        _ry = _RYAML(); _ry.preserve_quotes = True; _ry.width = 4096
        spec = _ry.load(target.read_text(encoding="utf-8")) or {}
        spec["status"] = status
        with target.open("w", encoding="utf-8") as _fh:
            _ry.dump(spec, _fh)
    except Exception:
        spec = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        spec["status"] = status
        target.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True, "status": status}


def _investigation_yaml_path(ws_root: Path, inv: str) -> Path | None:
    """Resolve investigations/<inv>/investigation.yaml, or None if missing."""
    for d in _iter_iset_dirs(ws_root):
        if d.name == inv:
            p = d / "investigation.yaml"
            return p if p.is_file() else None
    return None


def _append_investigation_input(ws_root: Path, inv: str, category: str, entry) -> bool:
    """Append ``entry`` to investigation.yaml's ``inputs.<category>`` list.

    ``category`` is one of ``datasets``, ``references``, ``expert_docs``.
    ``entry`` is a dict (datasets / expert_docs) or a bare bib-key string
    (references). Prefers ruamel for round-trip preservation, falling back to
    safe_dump. Returns True on success.
    """
    target = _investigation_yaml_path(ws_root, inv)
    if target is None:
        return False

    def _mutate(spec: dict) -> dict:
        block = spec.get("inputs")
        if not isinstance(block, dict):
            block = {}
            spec["inputs"] = block
        lst = block.get(category)
        if not isinstance(lst, list):
            lst = []
            block[category] = lst
        # De-dupe by name/path (dicts) or value (strings).
        if isinstance(entry, dict):
            ident = entry.get("path") or entry.get("name")
            for ex in lst:
                if isinstance(ex, dict) and (ex.get("path") == ident or ex.get("name") == entry.get("name")):
                    return spec
        else:
            if entry in lst:
                return spec
        lst.append(entry)
        return spec

    try:
        from ruamel.yaml import YAML as _RYAML
        _ry = _RYAML(); _ry.preserve_quotes = True; _ry.width = 4096
        spec = _ry.load(target.read_text(encoding="utf-8")) or {}
        spec = _mutate(spec)
        with target.open("w", encoding="utf-8") as _fh:
            _ry.dump(spec, _fh)
    except Exception:
        spec = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        spec = _mutate(spec)
        target.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return True


def _decide_proposed_input_for_test(
    ws_root: Path, inv: str, item_id: str, decision: str
) -> tuple[dict, int]:
    """Name-shim → lib.lifecycle_mutations.decide_proposed_input.

    Positional-arg signature kept for backward compatibility with test imports
    (tests/test_proposed_input_decision.py calls this as
    ``_decide_proposed_input_for_test(ws, inv, item_id, decision)``).
    Reconstructs the body dict and delegates to the lib builder.
    """
    return _lifecycle_mut.decide_proposed_input(
        ws_root,
        {"investigation": inv, "item_id": item_id, "decision": decision},
    )


def _build_iset_summary_for_test(ws_root: Path) -> list[dict]:
    """Back-compat shim — delegates to lib.investigation_status.build_iset_summary,
    injecting the server's runs.db-backed runs-presence check. (Name retained for
    the stdlib /api/iset-list handler + existing tests.)"""
    return _invstatus.build_iset_summary(
        ws_root,
        study_has_runs=lambda s, spec: _count_runs_for_study(s, spec) > 0,
    )


def _catalog_data(ws_root: "Path") -> dict:
    """Thin delegation to ``lib.catalog.build_catalog``.

    Kept here so ``Handler._get_catalog`` and ``publish.build_bundle``
    continue to call the same name unchanged.  The single implementation lives
    in ``vivarium_dashboard.lib.catalog.build_catalog`` (Task 6 extraction).
    """
    return build_catalog(ws_root)


# _dedupe_alias_composites — moved to lib/composite_lookup.py (imported above).


def _composites_data(ws_root: "Path") -> dict:
    """Pure data builder for GET /api/composites — returns ``{"composites": [...]}`` dict.

    Called by ``Handler._get_composites`` and ``publish.build_bundle``.
    Requires ``WORKSPACE`` to be set to *ws_root*.
    """
    import importlib as _importlib
    _ws_add_to_sys_path()
    try:
        from vivarium_dashboard.lib.composite_lookup import discover_all_composites
    except ImportError as e:
        return {"composites": [], "error": str(e)}

    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        try:
            _importlib.import_module(pkg)
        except Exception:
            pass
        specs = discover_all_composites(ws_root, pkg)
        ws_prefix_dot = pkg + "."
        out: list = []
        for s in specs.values():
            rec = {k: v for k, v in s.items() if not k.startswith("_")}
            rec.setdefault("kind", "spec")
            rec.setdefault("module", "")
            if "default_n_steps" not in rec:
                rec["default_n_steps"] = None
            mod = rec.get("module") or ""
            rec["workspace_local"] = bool(mod == pkg or mod.startswith(ws_prefix_dot))
            out.append(rec)
        out = _filter_composites(out, ws_data)
        out = _dedupe_alias_composites(out)
        return {"composites": out, "workspace_package": pkg}
    except Exception as e:
        return {"composites": [], "error": str(e)}


def _composites_data_subprocess(ws_root: "Path") -> "dict | None":
    """Run :func:`_composites_data` in a fresh subprocess (clean Python import).

    @composite_generator discovery imports package modules and scans decorators;
    that registration is unreliable in the long-running server's stale
    sys.modules state (it misses the generators), but works in a clean process
    (SP2b). Returns the discovered payload, or ``None`` if the subprocess fails
    (the caller then degrades to the in-process spec-only result).
    """
    # Discovery prints import warnings to stdout, so fence the JSON between
    # explicit start/end markers and extract exactly that (don't trust line order).
    script = (
        "import sys, json; from pathlib import Path;"
        "import vivarium_dashboard.server as s;"
        "s.WORKSPACE = Path(sys.argv[1]);"
        "_r = s._composites_data(s.WORKSPACE);"
        "sys.stdout.write('@@@C_START@@@' + json.dumps(_r) + '@@@C_END@@@')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(ws_root)],
            cwd=str(ws_root), capture_output=True, text=True, timeout=180,
        )
        out = result.stdout
        i, j = out.find("@@@C_START@@@"), out.find("@@@C_END@@@")
        if i != -1 and j != -1:
            return json.loads(out[i + len("@@@C_START@@@"):j])
    except Exception:
        pass
    return None


def _composite_state_via_subprocess(ref: str, ws_root: "Path") -> "dict | None":
    """Thin forwarder → ``lib.composite_state_views.composite_state_via_subprocess``.

    The implementation (and its embedded subprocess script — which no longer
    imports this server module) has moved to the lib seam so the FastAPI app
    can call it without importing ``vivarium_dashboard.server``. This wrapper
    preserves the original call-site name/signature (``ref`` first) used in
    ``_get_composite_state``.
    """
    from vivarium_dashboard.lib.composite_state_views import composite_state_via_subprocess
    return composite_state_via_subprocess(ws_root, ref)


def _composite_resolve_data(spec_id: str) -> "dict | None":
    """Thin forwarder → ``lib.composite_resolve.resolve_composite``.

    The implementation has moved to ``vivarium_dashboard.lib.composite_resolve``
    so the FastAPI seam can call it without importing this server module.
    This wrapper preserves the original call-site name used throughout server.py
    and by ``publish.build_bundle``.

    Returns ``None`` on any failure (not found, import errors, missing packages).
    Requires ``WORKSPACE`` to already be set.
    """
    from vivarium_dashboard.lib.composite_resolve import resolve_composite
    return resolve_composite(WORKSPACE, spec_id)


def _emitter_tag(emitter) -> str:
    """Normalise a row's ``emitter`` field to a lowercase string tag.

    The value may be a plain string ("parquet"), a structured dict
    ({"kind": "parquet", "store": ...}) declared in a study.yaml ``runs:``
    entry, or None. A dict reaching ``.lower()`` used to raise AttributeError
    inside the emitter_type loop — silently swallowed — which blanked every
    row's emitter_type and made the UI default the pill to "SQLite".
    """
    if isinstance(emitter, dict):
        emitter = emitter.get("kind")
    return emitter.lower() if isinstance(emitter, str) else ""


def _append_remote_simulations(sims: list, ws_root: Path) -> list:
    """Append the active remote build's server-side runs (scoped to the build's
    commit/repo) to the local Simulations-DB rows. No-op for local workspaces
    or when sms-api is unreachable — single source for the local+remote merge,
    shared by ``_simulations_data`` and the ``/api/simulations`` handler."""
    try:
        from vivarium_dashboard.lib.remote_simulations import list_remote_simulations
        remote = list_remote_simulations(ws_root)
    except Exception:
        remote = []
    return list(sims) + remote if remote else sims


def _simulations_data(ws_root: Path) -> dict:
    """Pure data builder for GET /api/simulations.

    Returns ``{"simulations": [...], "current": <slug|None>}`` with emitter_type
    labels applied.  Tolerates missing DB / import errors → returns empty list.
    Called by ``publish.build_bundle`` to export ``api/simulations.json``.
    """
    _ws_add_to_sys_path()
    try:
        from vivarium_dashboard.lib.simulations_index import list_simulations
        sims = list_simulations(ws_root)
    except Exception:
        return {"simulations": [], "current": None}
    try:
        from vivarium_dashboard.lib.runs_index import emitter_type_of
        _emitter_label = {"sqlite": "SQLite", "parquet": "Parquet", "xarray": "XArray",
                          "none": "—"}  # no step emitter (summary-only run)
        for s in sims:
            s["emitter_type"] = _emitter_label.get(
                _emitter_tag(s.get("emitter"))) or emitter_type_of(s.get("db_path"))
    except Exception:
        pass
    sims = _append_remote_simulations(sims, ws_root)
    return {"simulations": sims, "current": _current_branch_slug(ws_root)}


def _visualization_classes_data(ws_root: Path) -> dict:
    """Pure data builder for GET /api/visualization-classes.

    Thin wrapper: delegates to ``lib.visualization_classes.list_visualization_classes``
    so the FastAPI seam can call the same implementation without importing this
    stdlib server module.  Called by ``publish.build_bundle`` to export
    ``api/visualization-classes.json``.
    """
    from vivarium_dashboard.lib.visualization_classes import list_visualization_classes
    return list_visualization_classes(ws_root)


# Moved to lib/spec_norm.py (shared with Task 5 / lib.investigations_index).
# Re-import under the old private name so every existing call-site in this file
# continues to work without any other change.
from vivarium_dashboard.lib.spec_norm import normalize_requirements as _normalize_requirements  # noqa: E402


def _investigations_data(ws_root: Path) -> dict:
    """Thin wrapper: delegates to ``lib.investigations_index.build_investigations``.

    The single implementation lives in ``lib/investigations_index.py`` so both
    this stdlib handler and the FastAPI seam (``api/app.py``) share one code
    path without either importing the other.
    """
    from vivarium_dashboard.lib.investigations_index import build_investigations
    return build_investigations(ws_root)


# --- Pass C: cross-worktree investigation registry --------------------------
#
# Each running vivarium-dashboard registers itself in ~/.pbg/servers/*.json
# (path = worktree, pid, port, url). To support the cross-worktree
# investigation switcher in the left rail, we expose
# /api/investigation-registry which combines:
#
#   • "current"         — this server's active Investigation summary
#   • "running_others"  — every OTHER live server's current Investigation,
#                         queried over HTTP from each peer's /api/iset-list.
#
# Peers that don't respond within a short timeout are silently skipped.
# The HTTP probe results are cached for _REGISTRY_TTL_S to avoid hammering
# peers on every sidebar render.

_REGISTRY_TTL_S = 5.0
_registry_cache: dict[str, tuple[float, dict]] = {}



def _peer_current_investigation(url: str) -> dict | None:
    """Query a peer dashboard's /api/iset-list and pick a current Investigation.

    Heuristic: peer-side `/api/iset-list` returns every Investigation in the
    peer's workspace. We pick the one whose `effective_status` is "running"
    if present; otherwise the first entry. Returns a slim
    ``{slug, title, effective_status}`` dict, or None if the peer has no
    investigations or didn't respond.
    """
    cached = _registry_cache.get(url)
    now = time.time()
    if cached and now - cached[0] < _REGISTRY_TTL_S:
        return cached[1] or None
    data = _http_get_json(url.rstrip("/") + "/api/iset-list")
    out: dict | None
    if not data or not isinstance(data.get("investigations"), list):
        out = None
    else:
        invs = data["investigations"]
        running = next(
            (i for i in invs if i.get("effective_status") == "running"),
            None,
        )
        chosen = running or (invs[0] if invs else None)
        if chosen:
            out = {
                "slug":             chosen.get("name"),
                "title":            chosen.get("title", chosen.get("name")),
                "effective_status": chosen.get("effective_status"),
            }
        else:
            out = None
    _registry_cache[url] = (now, out or {})
    return out


# Investigation statuses that should NOT surface in the cross-worktree
# sidebar. Anything else (planning, running, planned, in_progress, blank…)
# is treated as "open" and listed. Aligns with /pbg-investigation close,
# which stamps `status: closed`.
_INVESTIGATION_STATUS_HIDDEN_FROM_SIDEBAR = frozenset({
    "closed", "archived", "complete",
})


def _list_other_worktrees(ws_root: Path) -> list[dict]:
    """Return ``[{path, branch}]`` for every git worktree of ``ws_root``'s
    repo EXCEPT ``ws_root`` itself.

    Uses ``git worktree list --porcelain`` from inside ``ws_root``. Returns
    an empty list if ``ws_root`` is not a git checkout, or git is missing,
    or the command fails. Never raises.
    """
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(ws_root),
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    out: list[dict] = []
    cur: dict = {}
    self_resolved = str(ws_root.resolve())
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            if cur and cur.get("path") and cur["path"] != self_resolved:
                out.append(cur)
            cur = {"path": line[len("worktree "):].strip(), "branch": None}
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            cur["branch"] = ref.split("/")[-1] if ref else None
        elif line.startswith("detached"):
            cur["branch"] = None
    if cur and cur.get("path") and cur["path"] != self_resolved:
        out.append(cur)
    return out


def _scan_worktree_investigations(worktree_path: str) -> list[dict]:
    """Walk ``<worktree>/investigations/*/investigation.yaml`` off disk
    and return slim summaries: ``[{slug, title, status}, ...]``.

    Used to surface dormant investigations whose dashboards are not
    running. Returns an empty list on any I/O error or invalid YAML.
    Never raises. Skips entries whose ``status`` matches
    ``_INVESTIGATION_STATUS_HIDDEN_FROM_SIDEBAR``.
    """
    import yaml as _yaml
    root = Path(worktree_path) / "investigations"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        spec_file = child / "investigation.yaml"
        if not spec_file.is_file():
            continue
        try:
            # Force utf-8 — Path.read_text(encoding="utf-8") defaults to locale encoding,
            # which crashed on ASCII locales when a sibling worktree's
            # investigation.yaml contained UTF-8 chars (e.g. → in titles).
            data = _yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, _yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        slug = data.get("name") or child.name
        status = (data.get("status") or "").strip().lower()
        if status in _INVESTIGATION_STATUS_HIDDEN_FROM_SIDEBAR:
            continue
        out.append({
            "slug":   slug,
            "title":  data.get("title") or slug,
            "status": data.get("status"),
        })
    return out


def _build_investigation_registry_for_test(
    ws_root: Path,
    this_url: str,
    *,
    list_servers_fn=None,
    fetch_peer_fn=None,
    list_worktrees_fn=None,
    scan_worktree_fn=None,
    current_branch_fn=None,
) -> dict:
    """Pure function backing GET /api/investigation-registry.

    Injectable hooks keep the helper testable without filesystem, HTTP,
    subprocess, or git I/O.

    Returns four buckets:

      - ``current``         — this dashboard's chosen Investigation.
                              Picked by (in priority order):
                                1. investigation whose ``name`` matches the
                                   current git branch (Investigation ≡ branch
                                   convention), then
                                2. any investigation with
                                   ``effective_status == "running"``, then
                                3. the first investigation alphabetically.
      - ``local_siblings``  — every OTHER investigation in THIS workspace
                              (same on-disk tree as ``current``). Lets the
                              sidebar list all investigations the user is
                              actively iterating on without forcing each
                              one onto its own worktree.
      - ``running_others``  — peer dashboards' chosen Investigations
                              (one per live peer), via HTTP probe of
                              each peer's ``/api/iset-list``.
      - ``dormant_others``  — open Investigations on OTHER worktrees
                              that do NOT have a running dashboard.
                              Read directly off disk via
                              ``git worktree list`` + filesystem scan.
                              **Deduplicated by slug** across worktrees:
                              each unique investigation appears once,
                              with the canonical worktree promoted to
                              the entry's ``worktree_path``/``branch``/
                              ``status`` fields and every other worktree
                              listed under ``variants``. Entries whose
                              ``status`` is closed/archived/complete are
                              filtered out so the sidebar never renders
                              stale rows.

    Contract: previously-existing keys retain their exact shape;
    ``local_siblings`` and ``dormant_others`` are additive buckets.
    """
    if list_servers_fn is None:
        try:
            from pbg_superpowers import workspace_catalog
            list_servers_fn = workspace_catalog.list_servers
        except Exception:
            list_servers_fn = lambda: []
    if fetch_peer_fn is None:
        fetch_peer_fn = _peer_current_investigation
    if list_worktrees_fn is None:
        list_worktrees_fn = lambda: _list_other_worktrees(ws_root)
    if scan_worktree_fn is None:
        scan_worktree_fn = _scan_worktree_investigations
    if current_branch_fn is None:
        def _default_branch_fn():
            try:
                from vivarium_dashboard.lib.work_state import _current_git_branch
                return _current_git_branch(ws_root)
            except Exception:
                return None
        current_branch_fn = _default_branch_fn

    # All local investigations in this workspace, picked apart into
    # current + siblings. Selection order: git-branch match > running >
    # alphabetical first.
    invs = _build_iset_summary_for_test(ws_root)
    chosen_idx: int | None = None
    if invs:
        cur_branch = current_branch_fn() or ""
        # Strip the canonical "investigation/" prefix so an investigation
        # slug ("dnaa-replication") matches its conventional branch
        # ("investigation/dnaa-replication"). Without this strip, the
        # heuristic falls through to "running" / alphabetical-first and
        # mislabels the workspace switcher.
        cur_branch_slug = cur_branch.removeprefix("investigation/") if cur_branch else ""
        if cur_branch_slug:
            for i, iv in enumerate(invs):
                if iv.get("name") == cur_branch_slug:
                    chosen_idx = i
                    break
        if chosen_idx is None:
            for i, iv in enumerate(invs):
                if iv.get("effective_status") == "running":
                    chosen_idx = i
                    break
        if chosen_idx is None:
            chosen_idx = 0

    if chosen_idx is not None:
        chosen = invs[chosen_idx]
        current = {
            "slug":             chosen.get("name"),
            "title":            chosen.get("title", chosen.get("name")),
            "worktree_path":    str(ws_root.resolve()),
            "url":              this_url,
            "effective_status": chosen.get("effective_status"),
        }
    else:
        current = {
            "slug":             None,
            "title":            None,
            "worktree_path":    str(ws_root.resolve()),
            "url":              this_url,
            "effective_status": None,
        }

    # local_siblings — everything else in this workspace.
    siblings: list[dict] = []
    if invs:
        for i, iv in enumerate(invs):
            if i == chosen_idx:
                continue
            siblings.append({
                "slug":             iv.get("name"),
                "title":            iv.get("title", iv.get("name")),
                "worktree_path":    str(ws_root.resolve()),
                "effective_status": iv.get("effective_status"),
            })

    # Running-others: every server record that does NOT point at this
    # worktree path AND has a live PID.
    this_path = str(ws_root.resolve())
    others: list[dict] = []
    running_paths: set[str] = set()
    for entry in list_servers_fn():
        if entry.get("path") == this_path:
            continue
        if not entry.get("_alive", False):
            continue
        url = entry.get("url") or ""
        if not url:
            continue
        peer = fetch_peer_fn(url)
        if peer is None:
            continue
        others.append({
            "slug":             peer.get("slug"),
            "title":            peer.get("title"),
            "worktree_path":    entry.get("path"),
            "url":              url,
            "effective_status": peer.get("effective_status"),
            "pid":              entry.get("pid"),
        })
        if entry.get("path"):
            running_paths.add(entry["path"])

    # Dormant-others: open investigations on OTHER worktrees that do NOT
    # have a live dashboard. Read directly off disk so closed/archived
    # ones can be filtered without a peer process.
    #
    # Worktrees of the same git repo typically share the same
    # `investigations/<slug>/investigation.yaml` files (the directory is
    # tracked in git), so the same slug appears once per worktree. The
    # sidebar previously rendered N near-identical rows; instead, dedupe
    # by slug and attach a `variants` list of every worktree+branch that
    # contains it. The primary entry's worktree_path/branch/status pick
    # the worktree whose branch matches the slug (Investigation ≡ branch
    # convention) when possible, otherwise the first alphabetical branch.
    dormant_by_slug: dict[str, dict] = {}
    for wt in list_worktrees_fn():
        wt_path = wt.get("path")
        if not wt_path or wt_path == this_path or wt_path in running_paths:
            continue
        for inv in scan_worktree_fn(wt_path):
            slug = inv.get("slug")
            if not slug:
                continue
            variant = {
                "worktree_path": wt_path,
                "branch":        wt.get("branch"),
                "status":        inv.get("status"),
            }
            bucket = dormant_by_slug.setdefault(slug, {
                "slug":     slug,
                "title":    inv.get("title"),
                "variants": [],
            })
            bucket["variants"].append(variant)
            # Keep title fresh if a later variant has one and the bucket lost
            # it (defensive — shouldn't happen, but the first scan might
            # have returned None).
            if not bucket.get("title") and inv.get("title"):
                bucket["title"] = inv.get("title")

    dormant: list[dict] = []
    for slug, bucket in sorted(dormant_by_slug.items()):
        variants = bucket["variants"]
        # Pick the canonical variant: branch == slug first, then first
        # alphabetical by branch name (None branches sort last).
        def _variant_sort_key(v: dict) -> tuple:
            br = v.get("branch") or ""
            return (br != slug, br or "￿")
        variants_sorted = sorted(variants, key=_variant_sort_key)
        primary = variants_sorted[0]
        dormant.append({
            "slug":          slug,
            "title":         bucket["title"] or slug,
            "worktree_path": primary["worktree_path"],
            "branch":        primary["branch"],
            "status":        primary["status"],
            "variants":      variants_sorted,
        })

    return {
        "current":         current,
        "local_siblings":  siblings,
        "running_others":  others,
        "dormant_others":  dormant,
    }


def _coerce_list_field(spec: dict, field: str, *, source: str = "<unknown>") -> list:
    """Read ``spec[field]`` and return it as a list.

    Workspace yamls evolve. A field documented as ``list[T]`` sometimes
    arrives as a dict (e.g. a grouped/nested shape an investigation author
    introduced before the renderer learned about it). Returning the dict
    untouched would crash the client renderer:

        TypeError: (iset.acceptance_criteria || []).map is not a function

    This helper coerces non-list values to ``[]`` and prints a one-line
    warning to stderr that names the field and the workspace file, so the
    operator knows where the schema drift is without having to bisect a
    JS stack trace.

    Use at every ``spec.get("...") or []`` site where the JS contract is a
    list (``.map`` / ``.forEach`` / ``.length`` on the client side).
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


def _build_iset_detail_for_test(ws_root: Path, name: str) -> tuple[dict, int]:
    """Pure function backing ``GET /api/iset/<name>`` — returns
    (response_dict, status_code). Used by the HTTP handler and unit tests.
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

    # Build a lean studies-out list (slug + status + has_runs) for status
    # derivation. The full handler does much more work to populate DAG
    # fields; we keep this helper minimal for testability.
    studies_out = []
    statuses = []
    has_runs = []
    for slug in (spec.get("studies") or []):
        status, runs = _read_study_status(ws_root, slug)
        statuses.append(status)
        has_runs.append(runs)
        # Pass A multi-axis status: surface the six optional axes per study.
        # Absent axes round-trip as None so callers can detect "not set".
        entry = {"name": slug, "status": status}
        entry.update(_read_study_multiaxis_status(ws_root, slug))
        # Discovery Implications passthrough — mirrors the full handler so the
        # study view + report can render alternate hypotheses / mechanism
        # updates / followup_study_proposals.
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


def _post_iset_create_for_test(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Name-shim for backward-compat test imports → lib.scaffold_mutations."""
    return _scaffold_mut.iset_create(ws_root, body)


def _post_iset_clone_for_test(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Name-shim for backward-compat test imports → lib.scaffold_mutations."""
    return _scaffold_mut.iset_clone(ws_root, body)


def _study_name_from_body(body: dict) -> str:
    """Extract the study/investigation identifier from a request body.

    The Study Detail UI sends 'study' (and 'investigation'); the legacy
    investigation UI sends 'name'. Accept any of them so the aliased
    /api/study-* handlers work for both callers.
    """
    return (
        (body.get("name") or body.get("study") or body.get("investigation") or "")
        .strip()
    )


# ---------------------------------------------------------------------------
# Study pure-function helpers (testable without HTTP handler)
# ---------------------------------------------------------------------------


def _post_study_set_objective_for_test(ws_root: Path, body: dict):
    """Name-shim for backward-compat test imports → lib.metadata_mutations."""
    return _meta_mut.set_study_objective(ws_root, body)


# ---------------------------------------------------------------------------
# v4 narrative-spine field writer — generic dotted-path setter.
# ---------------------------------------------------------------------------

# Top-level v4 narrative-spine fields the writer is allowed to touch. Restricts
# the dotted-path entry point so a malformed body can't rewrite arbitrary spec
# keys (e.g. `baseline` or `name`).
_NARRATIVE_ALLOWED_ROOTS = frozenset({
    "report",
    "study_card",
    "biological_summary",
    "conclusion_verdicts",
    "literature_anchors",
    "design_pivot_required",
})

# Enum constraints the schema declares — enforced server-side too so the form
# UI gets a clean 400 on a bad value instead of a silent write that later fails
# lint. The schema is permissive for free-form fields; only the truly-enum
# leaves are validated here.
_NARRATIVE_ENUM_LEAVES: dict[str, frozenset[str]] = {
    "report.confidence": frozenset({"high", "medium", "low"}),
    "conclusion_verdicts.regression_compatibility.result":
        frozenset({"PASS", "FAIL", "MIXED", "PENDING"}),
    "conclusion_verdicts.biological_validation.result":
        frozenset({"PASS", "FAIL", "MIXED", "PENDING"}),
    "conclusion_verdicts.explanatory_gain.result":
        frozenset({"POSITIVE", "NEUTRAL", "NEGATIVE", "PENDING"}),
}


def _post_study_narrative_set_for_test(ws_root: Path, body: dict):
    """Name-shim for backward-compat test imports → lib.metadata_mutations."""
    return _meta_mut.set_study_narrative(ws_root, body)


def _post_study_seed_followup_for_test(ws_root: Path, body: dict):
    """Name-shim → lib.lifecycle_mutations.study_seed_followup.

    Kept for backward compatibility with test imports
    (tests/test_study_seed_followup.py imports this symbol directly).
    """
    return _lifecycle_mut.study_seed_followup(ws_root, body)


def _post_feedback_apply_action_for_test(ws_root: Path, body: dict):
    """Name-shim → lib.lifecycle_mutations.feedback_apply_action.

    Kept for backward compatibility with test imports
    (tests/test_feedback_apply_action_api.py imports via Handler._feedback_apply_action_test).
    """
    return _lifecycle_mut.feedback_apply_action(ws_root, body)


def _post_study_rename_for_test(ws_root: Path, body: dict):
    """Name-shim → lib.lifecycle_mutations.study_rename.

    Kept for backward compatibility with test imports.
    """
    return _lifecycle_mut.study_rename(ws_root, body)


def _post_study_create_from_run_for_test(ws_root, body):
    """Name-shim → lib.lifecycle_mutations.study_create_from_run.

    Kept for backward compatibility with test imports
    (tests/test_study_create_from_run.py imports this symbol directly).
    """
    return _lifecycle_mut.study_create_from_run(ws_root, body)


def _count_runs_for_study(name: str, spec: dict | None = None) -> int:
    """Count runs for a study. Thin wrapper: delegates to lib, injecting WORKSPACE."""
    from vivarium_dashboard.lib.investigations_index import _count_runs_for_study as _ii_count
    return _ii_count(WORKSPACE, name, spec)


def _has_active_run_for_study(
    name: str, spec: dict | None = None, *, freshness_s: float = 300.0
) -> bool:
    """True only if a run for this study is *actively executing right now*.

    "Active" = a run whose status is ``running`` AND whose heartbeat is fresh
    (within ``freshness_s``). This deliberately excludes (a) completed runs —
    accumulated history is not active execution — and (b) stale ``running``
    rows left behind by a crashed/abandoned process (no recent heartbeat).
    Used by :func:`compute_study_effective_status` so a study that merely ran
    and finished does not badge as "running".
    """
    import time as _t
    now = _t.time()

    def _fresh(hb) -> bool:
        if hb is None:
            return False
        try:
            return (now - float(hb)) <= freshness_s
        except (TypeError, ValueError):
            return False

    # study.yaml runs[] (a backfilled/legacy run may carry status + heartbeat).
    for r in ((spec or {}).get("runs") or []):
        if not isinstance(r, dict):
            continue
        if str(r.get("status") or "").strip().lower() == "running" and _fresh(
            r.get("heartbeat_at")
        ):
            return True

    # studies/<name>/runs.db rows.
    try:
        for r in _read_runs_db_for_study(name):
            if str((r or {}).get("status") or "").strip().lower() == "running" and _fresh(
                (r or {}).get("heartbeat_at")
            ):
                return True
    except Exception:
        pass
    return False


def _investigation_emitter_for_study(study_name: str | None) -> str | None:
    return _study_run_state.investigation_emitter_for_study(WORKSPACE, study_name)


def _collect_study_observables(spec: dict) -> list[str]:
    """Return slash-joined observable store paths declared by the study spec.

    v2ecoli friction #14 (2026-05-19): study-run handlers historically did
    not pass `emit_paths` to `inject_emitter_for_paths`, so the
    SQLiteEmitter had an empty `emit:` schema and every `history.state` row
    was just `{"_tick": <global_time>}`. Comparative visualizations rendered
    as empty traces. This helper sweeps the spec for every observable-shaped
    path declaration so the run handler can wire `inject_emitter_for_paths`
    automatically — no study-yaml schema change required.

    Recognised sources (tolerant — drives off whatever the study author
    declared, in whatever shape):
      - readouts[*].store_path                (v2ecoli explicit per-readout
                                               paths, dot-joined)
      - behavior_tests[*].measure.path        (single-path measures)
      - behavior_tests[*].measure.{series_x,series_y,
                                    x,y,
                                    series_a,series_b}.path
      - simulation_set[*].observe             (per-sim observable list)

    Paths come in dot-joined ('agents.0.listeners.foo') or slash-joined
    ('agents/0/listeners/foo'); both are normalised to slash-joined so
    `inject_emitter_for_paths` accepts them uniformly. Duplicates are
    dropped while preserving declaration order.
    """
    def _norm(p: str) -> str | None:
        if not isinstance(p, str) or not p.strip():
            return None
        # Accept either separator; output is slash-joined.
        parts = [seg for seg in p.replace(".", "/").split("/") if seg]
        return "/".join(parts) if parts else None

    out: list[str] = []
    seen: set[str] = set()
    def _push(p):
        n = _norm(p) if isinstance(p, str) else None
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    for r in spec.get("readouts", []) or []:
        if isinstance(r, dict):
            _push(r.get("store_path"))

    for bt in spec.get("behavior_tests", []) or []:
        m = (bt or {}).get("measure") if isinstance(bt, dict) else None
        if not isinstance(m, dict):
            continue
        _push(m.get("path"))
        for nested_key in ("series_x", "series_y", "x", "y", "series_a", "series_b"):
            n = m.get(nested_key)
            if isinstance(n, dict):
                _push(n.get("path"))

    for sim in spec.get("simulation_set", []) or []:
        if not isinstance(sim, dict):
            continue
        obs = sim.get("observe")
        if isinstance(obs, list):
            for p in obs:
                _push(p)
        elif isinstance(obs, str):
            _push(obs)

    # v4 studies declare their tests under `tests:` (not `behavior_tests:`)
    # with the same {measure: {path, series_x, ...}} shape, and their overlay
    # observables under `comparative_visualizations[].observable_path`. Without
    # reading these, v4 studies (e.g. the dnaa investigation) collect zero
    # observables and every history row is just `{"_tick": <time>}`.
    for t in spec.get("tests", []) or []:
        m = (t or {}).get("measure") if isinstance(t, dict) else None
        if not isinstance(m, dict):
            continue
        _push(m.get("path"))
        for nested_key in ("series_x", "series_y", "x", "y", "series_a", "series_b"):
            n = m.get(nested_key)
            if isinstance(n, dict):
                _push(n.get("path"))

    for cv in spec.get("comparative_visualizations", []) or []:
        if isinstance(cv, dict):
            _push(cv.get("observable_path"))

    return out


def _resolve_study_baseline_state(pkg, spec_id, params):
    return _study_run_state.resolve_study_baseline_state(WORKSPACE, pkg, spec_id, params)


def _post_study_run_baseline_for_test(ws_root, body):
    return _study_runs.run_study_baseline(ws_root, body)


def _sync_parent_investigation(ws_root, study_dir) -> None:
    """Best-effort SP1 hook: after a study syncs, re-write its parent
    investigation's computed acceptance so the investigation verdict on disk
    tracks the member study's new outcome.

    No-op when the study has no owning investigation, or when the installed
    pbg_superpowers predates ``sync_investigation``. Never raises — a record
    error must not fail a successful run (mirrors the study-sync hook above).
    """
    try:
        from pbg_superpowers import study_outcomes
        from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
        sync_investigation = getattr(study_outcomes, "sync_investigation", None)
        if sync_investigation is None:
            return
        wp = WorkspacePaths.load(Path(ws_root))
        owner = wp.study_owner(Path(study_dir).name)
        if not owner:
            return
        inv_dir = wp.investigations / owner
        if (inv_dir / "investigation.yaml").is_file():
            sync_investigation(inv_dir, Path(ws_root))
    except Exception as exc:  # never fail a successful run on a record error
        print(f"[study_outcomes] sync_investigation failed: {exc}", file=sys.stderr)


def _run_post_run_scripts(spec: dict, ws_root: Path) -> tuple[list[str], list[dict]]:
    return _study_run_post.run_post_run_scripts(spec, ws_root)


def _build_analysis_options(entries: list[dict]) -> tuple[dict, list[dict]]:
    return _study_run_post.build_analysis_options(entries)


def _run_study_analyses(study_dir: Path, spec: dict, run_id: str,
                        ws_root: Path) -> tuple[list[str], list[dict]]:
    return _study_run_post.run_study_analyses(study_dir, spec, run_id, ws_root)


def _zarr_store_for_sim(study_db: Path, sim_name: str | None) -> Path | None:
    return _study_run_state.zarr_store_for_sim(study_db, sim_name)


def _render_study_visualizations(study_dir, spec, spec_id):
    return _study_run_post.render_study_visualizations(
        WORKSPACE, study_dir, spec, spec_id)


def _post_study_run_all_baselines_for_test(ws_root, body):
    return _study_runs.run_study_all_baselines(ws_root, body)


def _post_study_run_variant_for_test(ws_root, body):
    return _study_runs.run_study_variant(ws_root, body)


def _post_study_sync_runs_for_test(ws_root, body: dict):
    """Name-shim → lib.lifecycle_mutations.study_sync_runs.

    Kept for backward compatibility with test imports
    (tests/test_study_sync_runs_endpoint.py imports via server._post_study_sync_runs_for_test).
    """
    return _lifecycle_mut.study_sync_runs(ws_root, body)


def _post_study_variant_add_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_variant_add.

    Kept for backward compatibility with test imports
    (tests/test_study_baseline_handlers.py etc. import this symbol directly).
    """
    return _study_crud_lib.study_variant_add(ws_root, body)


def _post_study_variant_delete_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_variant_delete."""
    return _study_crud_lib.study_variant_delete(ws_root, body)


def _post_study_variant_set_params_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_variant_set_params."""
    return _study_crud_lib.study_variant_set_params(ws_root, body)


def _post_study_baseline_add_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_baseline_add."""
    return _study_crud_lib.study_baseline_add(ws_root, body)


def _post_study_baseline_remove_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_baseline_remove."""
    return _study_crud_lib.study_baseline_remove(ws_root, body)


def _post_study_intervention_add_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_intervention_add."""
    return _study_crud_lib.study_intervention_add(ws_root, body)


def _post_study_intervention_update_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_intervention_update."""
    return _study_crud_lib.study_intervention_update(ws_root, body)


def _post_study_intervention_delete_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_intervention_delete."""
    return _study_crud_lib.study_intervention_delete(ws_root, body)


def _post_study_run_delete_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_run_delete."""
    return _study_crud_lib.study_run_delete(ws_root, body)


def _post_study_runs_clear_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_runs_clear."""
    return _study_crud_lib.study_runs_clear(ws_root, body)


def _post_study_comparison_add_for_test(ws_root, body):
    """Name-shim: delegates to lib.study_crud_mutations.study_comparison_add."""
    return _study_crud_lib.study_comparison_add(ws_root, body)


def _study_export_zip(ws_root: Path, name: str) -> bytes:
    """Zip studies/<name>/ to bytes and return the zip content.

    Thin shim — delegates to ``lib.download_views.study_export_zip``."""
    return _download_views.study_export_zip(ws_root, name)


def _enrich_runs_with_meta(study_dir: Path, runs: list[dict]) -> list[dict]:
    """Merge per-run metadata from studies/<name>/runs.db into study.runs[].

    study.yaml's runs[] carries only the slim authoritative fields (run_id,
    variant, composite, label, status, n_steps). The runs_meta table in
    runs.db carries the rich per-run record (spec_id, params, started_at,
    completed_at, log_path). The Runs tab needs both. We copy the rich
    fields onto each entry under namespaced keys (`meta_*`) so the
    template doesn't have to know which DB they came from.

    Tolerant: if runs.db is absent, has no row for a run_id, or fails to
    open, the run entry is returned unchanged.
    """
    if not runs:
        return runs
    db = study_dir / "runs.db"
    rows: list = []
    if db.is_file():
        import sqlite3 as _sql
        try:
            conn = _sql.connect(str(db))
            conn.row_factory = _sql.Row
            rows = conn.execute(
                "SELECT run_id, spec_id, params_json, started_at, completed_at, "
                "n_steps, status, log_path FROM runs_meta"
            ).fetchall()
            conn.close()
        except _sql.Error:
            rows = []
    import json as _json
    by_id = {r["run_id"]: r for r in rows}
    enriched = []
    for r in runs:
        out = dict(r)
        # Always set meta_* keys so the Jinja template can call filters
        # against them unconditionally (None → empty cell).
        out.setdefault("meta_spec_id", None)
        out.setdefault("meta_started_at", None)
        out.setdefault("meta_completed_at", None)
        out.setdefault("meta_duration_sec", None)
        out.setdefault("meta_params", {})
        out.setdefault("meta_log_path", None)
        m = by_id.get(r.get("run_id"))
        if m is not None:
            try:
                params = _json.loads(m["params_json"] or "{}")
            except (ValueError, TypeError):
                params = {}
            started = m["started_at"]
            completed = m["completed_at"]
            duration = (completed - started) if (started and completed) else None
            out["meta_spec_id"] = m["spec_id"]
            out["meta_started_at"] = started
            out["meta_completed_at"] = completed
            out["meta_duration_sec"] = duration
            out["meta_params"] = params
            out["meta_log_path"] = m["log_path"]
        enriched.append(out)
    return enriched


def _render_study_detail_html(name: str, spec: dict) -> str:
    """Render study-detail.html via Jinja2.

    Thin shim: delegates to ``lib.study_page.render_study_detail_html``
    injecting the module-level WORKSPACE as ``ws_root``.

    CRITICAL call-site constraint: ``publish.py`` imports this function
    directly and calls it as ``_render_study_detail_html(slug, spec)``
    (2-arg). This shim MUST keep the ``(name, spec)`` signature so those
    call-sites keep working unchanged.
    """
    from vivarium_dashboard.lib import study_page as _study_page
    return _study_page.render_study_detail_html(WORKSPACE, name, spec)


def _humanize_study_name(slug: str) -> dict:
    """Mirror of JS _humanizeStudyName: peel a leading '<prefix>-NN[a-z]?-' into
    a chip and humanize the remainder. Keeps dashboard + report names identical."""
    import re
    m = re.match(r"^([a-z]+-\d+[a-z]*)-(.+)$", slug or "")
    if not m:
        return {"chip": "", "title": (slug or "").replace("-", " ")}
    rest = m.group(2).replace("-", " ")
    rest = rest[:1].upper() + rest[1:]
    if len(rest) > 60:
        rest = rest[:57] + "…"
    return {"chip": m.group(1), "title": rest}


def _jinja_fmt_ts(ts) -> str:
    """Format a unix timestamp as 'YYYY-MM-DD HH:MM' UTC, or '' if missing.

    Returns '' for None, empty values, AND undefined (Jinja's Undefined
    sentinel — e.g. when the template walks `r.meta_started_at or
    r.started_at` against a dict that has neither key). The previous
    `(TypeError, ValueError)` excludes Jinja's UndefinedError, which
    escaped here as a template-render failure for every <tr> in the
    Runs table whenever the merged run dict was missing both fields
    (the seven test_study_detail_page failures all triggered through
    this path).
    """
    try:
        ts = float(ts)
    except Exception:
        return ""
    if not ts:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _jinja_fmt_duration(seconds) -> str:
    """Format a duration in seconds as '12s', '1m 30s', '2h 15m', or '' if missing.

    Same Undefined-tolerance contract as _jinja_fmt_ts above.
    """
    try:
        seconds = float(seconds)
    except Exception:
        return ""
    if seconds < 0:
        return ""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if m else f"{h}h"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _is_generated_path(path: str) -> bool:
    """True if `path` is a generated report file (the dashboard rebuilds these
    on every page load, so they're chronically dirty and shouldn't block actions)
    or a large untracked artifact directory (out/ — the ~175 MB ParCa cache —
    which must never block actions and must never be committed) or dashboard
    runtime state under .pbg/ (the dashboard's own files; v2ecoli friction #15
    flagged the Install action being blocked by composite-runs.db-shm and
    .pbg/dashboard/ that older pbg-template revisions don't .gitignore yet).
    """
    return (
        path.startswith("reports/")
        or path.startswith("out/") or path == "out/"
        or path.startswith(".pbg/") or path == ".pbg/"
    )


def _submodule_paths() -> set[str]:
    """Read .gitmodules and return the set of registered submodule paths.

    Submodule pointer movements show up as `M <path>` in `git status --porcelain`
    even when the user has only updated the submodule's HEAD (e.g., `git submodule
    update --remote`). These shouldn't block workspace-level actions.
    """
    gm = WORKSPACE / ".gitmodules"
    if not gm.exists():
        return set()
    paths: set[str] = set()
    for line in gm.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("path"):
            _, _, val = line.partition("=")
            val = val.strip()
            if val:
                paths.add(val)
    return paths


def _has_origin_remote() -> bool:
    """True if a git remote named 'origin' is configured.

    Delegates to ``lib.git_status.has_origin_remote(WORKSPACE)`` — kept as a
    shim so existing call-sites in this module continue to work unchanged.
    """
    return _git_status_lib.has_origin_remote(WORKSPACE)


def _sms_api_base() -> str:
    """Base URL of the sms-api (the SSM tunnel by default)."""
    return os.environ.get("SMS_API_BASE", "http://localhost:8080")


def _remote_push_and_sha() -> str:
    """Push the workspace's current branch to origin with the GH token, return HEAD SHA."""
    from vivarium_dashboard.lib import github_auth

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=WORKSPACE,
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if not branch or branch == "HEAD":
        raise RuntimeError("workspace is not on a named branch")
    env = os.environ | github_auth.current_token_env()
    push = subprocess.run(
        ["git", "push", "-u", "origin", branch], cwd=WORKSPACE,
        capture_output=True, text=True, timeout=120, env=env,
    )
    if push.returncode != 0:
        raise RuntimeError(f"git push failed: {(push.stderr or push.stdout)[-300:]}")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=WORKSPACE, capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if not sha:
        raise RuntimeError("could not resolve HEAD commit")
    return sha


class _NotAGitRepo(RuntimeError):
    pass


def _remote_commit_and_push(message: str) -> dict:
    """Stage+commit WORKSPACE changes (skip if clean), push current branch, return result."""
    inside = subprocess.run(
        ["git", "-C", str(WORKSPACE), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise _NotAGitRepo("active source is not a git workspace (no commit/push)")
    subprocess.run(["git", "-C", str(WORKSPACE), "add", "-A"], capture_output=True, text=True, timeout=30)
    status = subprocess.run(
        ["git", "-C", str(WORKSPACE), "status", "--porcelain"], capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    if status:
        c = subprocess.run(
            ["git", "-C", str(WORKSPACE), "commit", "-m", message or "dashboard commit"],
            capture_output=True, text=True, timeout=30,
        )
        if c.returncode != 0:
            raise RuntimeError(f"git commit failed: {(c.stderr or c.stdout)[-300:]}")
    sha = _remote_push_and_sha()
    return {"ok": True, "pushed": bool(status), "commit": sha,
            "branch": subprocess.run(["git", "-C", str(WORKSPACE), "rev-parse", "--abbrev-ref", "HEAD"],
                                     capture_output=True, text=True).stdout.strip()}


def _normalize_repo_url(url: str) -> str:
    """Normalize a git remote URL for sms-api's simulator/upload.

    sms-api's ``/core/v1/simulator/upload`` 500s on a ``.git``-suffixed URL
    (it builds an image tag / repo path from the URL), so strip a trailing
    ``.git`` and surrounding whitespace.
    """
    url = url.strip()
    if url.endswith(".git"):
        url = url[: -len(".git")]
    return url


def _remote_repo_url() -> str | None:
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=WORKSPACE,
        capture_output=True, text=True, timeout=5,
    )
    if r.returncode != 0:
        return None
    raw = r.stdout.strip()
    return _normalize_repo_url(raw) if raw else None


def _stale_branch_threshold() -> int:
    """Commits-behind-main threshold above which a branch is flagged stale.

    Delegates to ``lib.git_status.stale_branch_threshold()`` — kept as a
    shim so existing call-sites in this module continue to work unchanged.
    """
    return _git_status_lib.stale_branch_threshold()


def _commits_behind(branch: str, base: str = "main") -> tuple[int, str]:
    """Return (commits_behind, ref_used).

    Delegates to ``lib.git_status.commits_behind(WORKSPACE, branch, base)``
    — kept as a shim so existing call-sites in this module continue to work.
    """
    return _git_status_lib.commits_behind(WORKSPACE, branch, base)


def _diagnose_push_error(err: str) -> dict | None:
    """Return a structured diagnosis for known push failure patterns, else None."""
    if not err:
        return None
    if "does not appear to be a git repository" in err or "Could not read from remote repository" in err:
        return {
            "category": "no_origin",
            "summary": "Push failed because no GitHub remote is configured.",
            "suggestion": "Click `Create GitHub repo` in the workstream strip to create one and push in one step.",
        }
    if "Permission to" in err and "denied" in err:
        return {
            "category": "auth",
            "summary": "Push denied — your git credential doesn't have write access.",
            "suggestion": "Run `gh auth login` (or check your SSH key / token) and try again.",
        }
    if "rejected" in err and ("non-fast-forward" in err or "behind" in err):
        return {
            "category": "behind",
            "summary": "Remote has commits your local branch doesn't.",
            "suggestion": "Pull/rebase first: `git pull --rebase origin <branch>`, then push.",
        }
    return None


def _invoke_v2ecoli_workflow(cfg_path, out_dir, ws_root, timeout_s):
    return _composite_subprocess.invoke_v2ecoli_workflow(cfg_path, out_dir, ws_root, timeout_s)


def _run_composite_subprocess(*, pkg, state, steps, db_file, run_id, spec_id,
                              label, overrides=None, sim_name=None, timeout=1800,
                              emit_paths=None, study_emitter=None,
                              study_max_generations=None,
                              study_single_daughters=None):
    return _composite_subprocess.run_composite_subprocess(
        WORKSPACE, pkg=pkg, state=state, steps=steps, db_file=db_file, run_id=run_id, spec_id=spec_id, label=label,
        overrides=overrides, sim_name=sim_name, timeout=timeout, emit_paths=emit_paths, study_emitter=study_emitter,
        study_max_generations=study_max_generations, study_single_daughters=study_single_daughters)


def _count_viz_steps_in_state(state: dict) -> int:
    """Best-effort count of Visualization-Step entries in a composite state.

    Heuristic: a Visualization Step is any ``_type: step`` entry whose
    address matches a known Visualization class. We don't have core access
    here, so we use a name-based heuristic: address contains ``Viz`` /
    ``Plot`` / ``Heatmap`` / ``Animation`` / ``Snapshots`` /
    ``Distribution``. Best-effort - undercounts are fine; this just powers
    the manifest dashboard glance.
    """
    if not isinstance(state, dict):
        return 0
    count = 0
    for v in state.values():
        if not isinstance(v, dict):
            continue
        if v.get("_type") != "step":
            continue
        addr = v.get("address") or ""
        if re.search(r"(Viz|Plot|Heatmap|Animation|Snapshots|Distribution)",
                     addr, re.I):
            count += 1
    return count


def _dirty_workspace() -> str:
    """Return the porcelain status excluding generated reports + submodule pointers.

    Delegates to ``lib.git_status.dirty_workspace(WORKSPACE)`` — kept as a
    shim so existing call-sites in this module continue to work unchanged.
    """
    return _git_status_lib.dirty_workspace(WORKSPACE)


def _suggest_dirty_commit_message(paths: list[str]) -> str:
    """Auto-generate a conventional commit message from a list of dirty paths.

    Uses the top-level directory of each path to pick a category prefix. When all
    dirty files share one top-level directory we map it to a conventional scope
    (chore(scripts), docs, chore(composites), ...). Otherwise falls back to a
    generic ``chore:`` prefix.
    """
    if not paths:
        return "chore: commit pending files"
    top_dirs = sorted(set(p.split('/')[0] for p in paths if p))
    n = len(paths)
    suffix = f"commit {n} pending file{'s' if n != 1 else ''}"
    if len(top_dirs) == 1:
        cat = top_dirs[0]
        # Map common top-level dirs to conventional categories
        known = {
            'scripts': 'chore(scripts)',
            'composites': 'chore(composites)',
            'investigations': 'chore(investigations)',
            'docs': 'docs',
            'tests': 'chore(tests)',
            'reports': 'chore(reports)',
            'pbg_chromosome_rep1': 'chore(pkg)',  # workspace package
        }
        # Generic fallback
        prefix = known.get(cat, f'chore({cat})')
        return f"{prefix}: {suffix}"
    return f"chore: {suffix}"


def _safe_slug(s: str) -> str:
    """Convert a string to a safe branch name component."""
    s = re.sub(r"[^a-zA-Z0-9_-]", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:40]


# _format_baseline_source and _conclusions_excerpt are imported from
# vivarium_dashboard.lib.investigations_index at the top of this module.


def _active_branch_action(commit_message: str, action_fn) -> tuple[dict, int]:
    """Run action_fn on the active workstream branch; commit; stay on it."""
    _ws_add_to_sys_path()
    from vivarium_dashboard.lib.work_state import (
        load_state, load_state_or_adopt_current, save_state,
    )
    state = load_state_or_adopt_current()
    branch = state.get("active_branch")
    if not branch:
        return {"error": "no active workstream — click Start workstream at the top of the dashboard, or check out a feature branch first"}, 409

    # Make sure we're on the active branch (auto-recover from drift)
    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=WORKSPACE, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if current != branch:
        r = subprocess.run(["git", "checkout", branch], cwd=WORKSPACE, capture_output=True, text=True)
        if r.returncode != 0:
            return {"error": f"could not check out workstream branch '{branch}': {r.stderr[:200]}"}, 500

    if _dirty_workspace().strip():
        return {"error": f"working tree dirty: {_dirty_workspace()[:300]}"}, 409

    try:
        action_fn()
        # Stage only the content the dashboard authors. A blanket `git add -A`
        # can sweep large untracked artifact dirs (out/, the ~175 MB ParCa
        # cache) into the commit; scoping the pathspec makes that impossible.
        # reports/ is intentionally excluded — it is generated, not authored.
        # Absent pathspecs are a fatal error for `git add`, so we filter the
        # list down to paths that actually exist in the workspace first.
        _STAGE_PATHS = [
            "studies/", "investigations/", "models/", "scripts/",
            "workspace.yaml", "pyproject.toml", ".gitmodules", ".gitignore",
            "external/",
        ]
        present = [p for p in _STAGE_PATHS if (WORKSPACE / p).exists()]
        if present:
            subprocess.run(
                ["git", "add", "-A", "--", *present],
                cwd=WORKSPACE, check=True, capture_output=True,
            )
        # Also stage any already-tracked top-level *.py / *.yaml the action
        # touched, without picking up untracked files.
        subprocess.run(
            ["git", "add", "--update"],
            cwd=WORKSPACE, check=True, capture_output=True,
        )
        diff = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            cwd=WORKSPACE, capture_output=True, text=True, check=True,
        ).stdout
        if not diff.strip():
            return {"error": "action made no changes (already at this state?)"}, 409
        subprocess.run([
            "git", "-c", "user.email=pbg-template@local",
                  "-c", "user.name=pbg-template",
                  "commit", "-m", commit_message,
        ], cwd=WORKSPACE, check=True, capture_output=True)
        commit_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=WORKSPACE, capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Reload state (action_fn may have side-effects) and keep file fresh
        state = load_state()
        if state.get("active_branch") == branch:
            save_state(state)

        return {"branch": branch, "commit": commit_sha[:7], "message": commit_message}, 200
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return {"error": f"git operation failed: {stderr[:300]}"}, 500
    except Exception as e:
        return {"error": str(e)}, 500


_NO_WORKSTREAM_MARKERS = (
    "no active workstream",
    "workspace.yaml not found",
)


def _commit_or_run(commit_message: str, action_fn) -> tuple[dict, int]:
    """Run ``action_fn`` under the active workstream, committing the result.

    The standard `check clean tree → run action → commit` flow is what
    mutation endpoints want: a dirty tree is surfaced as a clear 409 BEFORE
    any file is written, so the dashboard can ask the user to resolve it.

    Fallback: when no workstream is available (test fixtures, scaffolded
    workspaces without a workstream started, etc.), run ``action_fn`` directly
    so file side-effects still happen. Returns 200 with a ``note`` field
    distinguishing committed vs ran-only.
    """
    try:
        resp, code = _active_branch_action(commit_message, action_fn)
    except Exception as e:
        msg = str(e).lower()
        if any(m in msg for m in _NO_WORKSTREAM_MARKERS):
            try:
                action_fn()
            except Exception as inner:
                return {"error": f"action failed: {inner}"}, 500
            return {"ok": True, "note": f"no workstream; ran action without commit ({e})"}, 200
        raise
    if code == 409 and any(m in (resp.get("error") or "").lower() for m in _NO_WORKSTREAM_MARKERS):
        try:
            action_fn()
        except Exception as e:
            return {"error": f"action failed: {e}"}, 500
        return {"ok": True, "note": "no workstream; ran action without commit"}, 200
    return resp, code


# ---------------------------------------------------------------------------
# Pending visibility helper
# ---------------------------------------------------------------------------

def _pending_entries() -> dict:
    """Walk unmerged stage/* branches; diff against main's workspace.yaml.

    Returns a dict keyed by panel name, each value a list of
    {"entry": <dict>, "branch": <str>} objects for entries not on main.

    Panels: observables, visualizations, phases, datasets, references_pdfs,
            expert_docs, imports.
    """
    try:
        main_text = subprocess.run(
            ["git", "show", "main:workspace.yaml"],
            cwd=WORKSPACE, capture_output=True, text=True, check=True,
        ).stdout
        main_ws = yaml.safe_load(main_text) or {}
    except Exception:
        return {}

    # Build uniqueness-key sets for main.
    def _key_set(items, key):
        return {item.get(key) for item in (items or []) if isinstance(item, dict)}

    main_obs_names = _key_set(main_ws.get("observables"), "name")
    main_viz_names = _key_set(main_ws.get("visualizations"), "name")
    main_phase_ns = {p.get("n") for p in (main_ws.get("phases") or []) if isinstance(p, dict)}
    main_ds_names = _key_set(main_ws.get("datasets"), "name")
    main_pdf_keys = _key_set(main_ws.get("references_pdfs"), "bib_key")
    main_edoc_names = _key_set(main_ws.get("expert_docs"), "name")
    main_import_names = set((main_ws.get("imports") or {}).keys())

    # Get all stage/* branches.
    try:
        raw = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/stage/"],
            cwd=WORKSPACE, capture_output=True, text=True, check=True,
        ).stdout
        stage_branches = [b.strip() for b in raw.splitlines() if b.strip()]
    except Exception:
        return {}

    pending: dict = {
        "observables": [],
        "visualizations": [],
        "phases": [],
        "datasets": [],
        "references_pdfs": [],
        "expert_docs": [],
        "imports": [],
    }

    for branch in stage_branches:
        try:
            branch_text = subprocess.run(
                ["git", "show", f"{branch}:workspace.yaml"],
                cwd=WORKSPACE, capture_output=True, text=True, check=True,
            ).stdout
            branch_ws = yaml.safe_load(branch_text) or {}
        except Exception:
            continue

        # Find new observables.
        for item in (branch_ws.get("observables") or []):
            if isinstance(item, dict) and item.get("name") not in main_obs_names:
                pending["observables"].append({"entry": item, "branch": branch})

        # Find new visualizations.
        for item in (branch_ws.get("visualizations") or []):
            if isinstance(item, dict) and item.get("name") not in main_viz_names:
                pending["visualizations"].append({"entry": item, "branch": branch})

        # Find new phases.
        for item in (branch_ws.get("phases") or []):
            if isinstance(item, dict) and item.get("n") not in main_phase_ns:
                pending["phases"].append({"entry": item, "branch": branch})

        # Find new datasets.
        for item in (branch_ws.get("datasets") or []):
            if isinstance(item, dict) and item.get("name") not in main_ds_names:
                pending["datasets"].append({"entry": item, "branch": branch})

        # Find new reference PDFs.
        for item in (branch_ws.get("references_pdfs") or []):
            if isinstance(item, dict) and item.get("bib_key") not in main_pdf_keys:
                pending["references_pdfs"].append({"entry": item, "branch": branch})

        # Find new expert docs.
        for item in (branch_ws.get("expert_docs") or []):
            if isinstance(item, dict) and item.get("name") not in main_edoc_names:
                pending["expert_docs"].append({"entry": item, "branch": branch})

        # Find new imports.
        for imp_name, imp_val in (branch_ws.get("imports") or {}).items():
            if imp_name not in main_import_names:
                pending["imports"].append({"entry": {"name": imp_name, **imp_val}, "branch": branch})

    return pending


# ---------------------------------------------------------------------------
# Catalog sync check
# ---------------------------------------------------------------------------

def _platform_key() -> str:
    """Map sys.platform to the install-key used in catalog system_dependencies.

    Returns one of: 'darwin', 'linux', 'windows', or the raw lowercase
    platform.system() string as a last-resort fallback. Kept tiny on
    purpose — catalog entries key install commands by these strings.
    """
    import platform
    p = platform.system().lower()
    if p == "darwin":
        return "darwin"
    if p.startswith("linux"):
        return "linux"
    if p == "windows":
        return "windows"
    return p


def _check_system_dep(check: dict, venv_py: Path) -> tuple[bool, str | None]:
    """Run a single system-dep check defined in a catalog entry.

    A check is satisfied when its ``import_check`` Python snippet runs
    successfully inside the workspace venv. Empty/missing snippets are
    treated as satisfied (the catalog author signalled the dep has no
    programmatic check).

    Returns ``(satisfied, failure_reason)`` — reason is None on success
    and otherwise the most informative tail line of stderr.
    """
    snippet = check.get("import_check") or ""
    if not snippet:
        return True, None
    if not venv_py.is_file():
        return False, f"workspace venv python not found at {venv_py}"
    try:
        result = subprocess.run(
            [str(venv_py), "-c", snippet],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True, None
        # Last non-blank line of stderr is usually the most informative
        # (Python traceback final line / dlopen error / etc.).
        err_lines = [
            ln for ln in (result.stderr or "").strip().splitlines() if ln.strip()
        ]
        return False, (err_lines[-1] if err_lines else f"exit {result.returncode}")
    except subprocess.TimeoutExpired:
        return False, "check timed out"
    except Exception as e:
        return False, str(e)


def _check_installed_module_sync(pkg_name: str, install_path: str | None) -> str | None:
    """Thin wrapper — forwards to ``lib.catalog._check_installed_module_sync``.

    Supplies the active ``WORKSPACE`` global so Handler call-sites (which don't
    carry ``ws_root``) keep working unchanged.  The single implementation lives
    in ``vivarium_dashboard.lib.catalog`` (Task 6 extraction).
    """
    from vivarium_dashboard.lib.catalog import (
        _check_installed_module_sync as _f,
    )
    return _f(WORKSPACE, pkg_name, install_path)


# _CATALOG_VENV_PROBE_SCRIPT, _detect_workspace_venv_distributions,
# _read_workspace_pyproject_deps — moved to lib/catalog.py (imported above).


# ---------------------------------------------------------------------------
# SP2b-i — never-fabricate observable guard
#
# Wire the (otherwise orphaned) ``pbg_superpowers.readout_validation`` into a
# live path so the agent can answer "what can this composite actually emit?"
# and is stopped from authoring phantom observables. Pure/deterministic given
# a built composite: the dashboard renders the statuses, the /pbg-study skill
# guides re-authoring (dashboard stays AI-free).
# ---------------------------------------------------------------------------

def _build_composite_state_for_observables(ws_root: Path, ref: str):
    """Build a composite by ``ref`` → ``(core, state, schema)``.

    Thin shim → ``lib.observables_views.build_composite_state_for_observables``.
    Raises ``LookupError`` (unknown ref) / ``RuntimeError`` (build failure).
    """
    return _obs_views.build_composite_state_for_observables(ws_root, ref)


def _augment_lineage_aliases(available: dict) -> dict:
    """Augment an ``available_observables`` dict with lineage-prefix-stripped aliases.

    Thin shim → ``lib.observables_views.augment_lineage_aliases``.
    """
    return _obs_views.augment_lineage_aliases(available)


def _observables_for_ref(ws_root: Path, ref: str):
    """GET /api/observables?ref=<id> worker — returns ``(json_bytes, status)``.

    Thin shim → ``lib.observables_views.build_observables`` (encodes the payload
    dict via ``_json_body``). Unknown ref → 404; build failure → 400; validator
    absent → 501; introspection fail → 500.
    """
    body, status = _obs_views.build_observables(ws_root, ref)
    return _json_body(body), status


def _study_observable_check(ws_root: Path, slug: str):
    """GET /api/study-observable-check?study=<slug> worker — ``(json_bytes, status)``.

    Thin shim → ``lib.observables_views.build_study_observable_check`` (encodes
    the payload dict via ``_json_body``).
    """
    body, status = _obs_views.build_study_observable_check(ws_root, slug)
    return _json_body(body), status


def _report_lint(ws_root: Path):
    """GET /api/report-lint worker — ``(json_bytes, status)``.

    Thin shim — delegates to ``lib.report_views.build_report_lint``.
    """
    from vivarium_dashboard.lib import report_views as _rv
    body, status = _rv.build_report_lint(ws_root)
    return _json_body(body), status


def _composite_resolution_findings(ws_root: Path) -> list[dict]:
    """For every study in the workspace, return report-lint findings for any
    declared composite ref that does NOT resolve to a registered composite.

    Uses the dashboard's live registry (``known_composite_ids``) + the helper
    ``unresolved_study_composite_refs`` (which prefers pbg_superpowers'
    ``report_linter.unresolved_composite_refs`` when available). Tolerant:
    returns ``[]`` on any failure so the readiness panel never 500s.
    """
    ws_root = Path(ws_root)
    out: list[dict] = []
    try:
        from vivarium_dashboard.lib.composite_lookup import (
            known_composite_ids, unresolved_study_composite_refs,
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
            out.append({
                "study": d.name,
                "check": "unresolved_composite",
                "severity": "warning",
                "message": (f"composite not found in registry: {ref} — the study "
                            "references a composite that doesn't resolve (it may not "
                            "declare a real, registered composite)"),
                "field_path": "baseline[].composite",
            })
    return out


def _linkage_index(ws_root: Path, *, investigation=None, source=None, observable=None,
                   observable_registry=None, composite=None):
    """GET /api/linkage-index worker — ``(json_bytes, status)``.

    Thin shim — delegates to ``lib.report_views.build_linkage_index``.
    Injects ``_observables_for_ref`` (module-level, monkeypatchable by tests)
    for the SP4b observable_registry / composite paths.
    """
    from vivarium_dashboard.lib import report_views as _rv
    body, status = _rv.build_linkage_index(
        ws_root,
        investigation=investigation,
        source=source,
        observable=observable,
        observable_registry=observable_registry,
        composite=composite,
        observables_for_ref_fn=_observables_for_ref,
    )
    return _json_body(body), status


def _needs_attention(ws_root: Path, *, investigation=None):
    """GET /api/needs-attention worker — ``(json_bytes, status)``.

    Thin shim — delegates to ``lib.report_views.build_needs_attention``.
    """
    from vivarium_dashboard.lib import report_views as _rv
    body, status = _rv.build_needs_attention(ws_root, investigation=investigation)
    return _json_body(body), status


def _framework_metrics(ws_root: Path):
    """GET /api/framework-metrics worker — ``(json_bytes, status)``.

    Thin shim: delegates to ``lib.system_info.build_framework_metrics`` for the
    dict payload, then wraps it in ``(json_bytes, 200)`` for the legacy handler.
    Always returns HTTP 200 (best-effort, never raises).
    """
    return _json_body(_system_info_lib.build_framework_metrics(Path(ws_root))), 200


def _investigation_hypotheses(ws_root: Path, name: str):
    """GET /api/investigation-hypotheses worker — ``(json_bytes, status)``.

    Thin delegating shim → ``lib.investigation_views.build_investigation_hypotheses``.
    Always returns HTTP 200; result encoded as JSON bytes via ``_json_body``.
    """
    body = _inv_views.build_investigation_hypotheses(Path(ws_root), name)
    return _json_body(body), 200


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


def _git_branch_commit(path: str) -> tuple[str, str]:
    """(branch, short_commit) for a git workspace; ('', '') when unresolvable."""

    def _run(args: list[str]) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", path, *args], capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    return _run(["rev-parse", "--abbrev-ref", "HEAD"]), _run(["rev-parse", "--short", "HEAD"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):  # silence default request logging
        pass

    def do_GET(self):
        # DataSource seam endpoints — handled BEFORE the alias rewriting loop
        # so they are not shadowed by the /api/study/ → /api/investigation/
        # alias (sub-project #1, client-fetch seam).
        _path_only_pre = self.path.split("?", 1)[0]
        if _path_only_pre.startswith("/api/study/"):
            _slug = _path_only_pre.split("/api/study/", 1)[-1].strip("/")
            # Delegate entirely to the pure builder (slug validation + lookup
            # both live there so the live path and the tested builder are identical).
            return self._send_json_bytes(*Handler._build_api_study_response(_slug))
        if _path_only_pre == "/api/config":
            return self._send_json_bytes(*Handler._build_api_config_response())
        if _path_only_pre == "/api/workspace":
            return self._send_json_bytes(*Handler._build_api_workspace_response())
        # SP2b-i never-fabricate observable guard. Handled here (before the
        # /api/study-* alias rewriting) so /api/study-observable-check is not
        # shadowed. Both delegate to the pure module workers + WORKSPACE global.
        if _path_only_pre == "/api/observables":
            import urllib.parse as _up
            _ref = dict(_up.parse_qsl(_up.urlparse(self.path).query)).get("ref", "")
            return self._send_json_bytes(*_observables_for_ref(WORKSPACE, _ref))
        if _path_only_pre == "/api/study-observable-check":
            import urllib.parse as _up
            _q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
            _slug = (_q.get("study") or _q.get("investigation") or _q.get("name") or "").strip()
            return self._send_json_bytes(*_study_observable_check(WORKSPACE, _slug))
        # Spine A3: per-study readiness panel — runs the deterministic linter.
        if _path_only_pre == "/api/report-lint":
            return self._send_json_bytes(*_report_lint(WORKSPACE))
        # SP4a: linkage-index queries (AC→study gating matrix + gaps, source↔study,
        # finding-by-observable, study-DAG). Read-only deterministic derive.
        if _path_only_pre == "/api/linkage-index":
            import urllib.parse as _up
            _q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
            return self._send_json_bytes(*_linkage_index(
                WORKSPACE,
                investigation=(_q.get("investigation") or _q.get("inv") or "").strip() or None,
                source=(_q.get("source") or "").strip() or None,
                observable=(_q.get("observable") or "").strip() or None,
                observable_registry=(_q.get("observable_registry") or "").strip() or None,
                composite=(_q.get("composite") or "").strip() or None,
            ))
        # SP5: needs-attention scan — deterministic, build-free derive of the
        # items an investigation should triage (uncovered ACs, verdict
        # divergences, open feedback, param drift, stale findings, phantom
        # observables). Read-only; never 500.
        if _path_only_pre == "/api/needs-attention":
            import urllib.parse as _up
            _q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
            return self._send_json_bytes(*_needs_attention(
                WORKSPACE,
                investigation=(_q.get("investigation") or _q.get("inv") or "").strip() or None,
            ))

        # Resolve /api/study-* aliases to their /api/investigation-* originals so
        # the rest of the dispatch chain only needs to know one set of paths.
        for old_prefix, new_prefix in _GET_STUDY_ALIASES:
            if self.path.startswith(new_prefix):
                tail = self.path[len(new_prefix):]
                self.path = old_prefix + tail
                break

        # Study Detail page: /studies/<name> → render study-detail.html.
        # Only intercept the EXACT /studies/<slug> path (with optional query
        # string). Deeper paths like /studies/<slug>/viz/foo.html fall
        # through to the static-file handler below so workspace artefacts
        # under studies/<slug>/{viz,charts,...} are reachable. Closes
        # friction-log #16 (URL routing intercepts before static serving).
        if self.path.startswith("/studies/"):
            _path_only = self.path.split("?", 1)[0]
            _segs = _path_only.strip("/").split("/")
            if len(_segs) == 2:
                return self._get_study_detail_page()

        # Strip query string for route matching (self.path includes ?focus=...).
        path_only = self.path.split("?", 1)[0]
        if path_only in ("/", "/index.html"):
            # Render the SPA shell from index.html.j2 BEFORE serving so the
            # live dashboard is decoupled from the static reports/index.html
            # artifact other tools may overwrite for offline-viewing purposes
            # (e.g. pbg_superpowers /pbg-report writes a slim workspace-only
            # report there). If render fails, fall back to whatever's on disk.
            try:
                from vivarium_dashboard.lib.report import render_workspace_report
                render_workspace_report(WORKSPACE)
            except Exception as _render_exc:  # noqa: BLE001 — never block load
                import sys as _sys
                print(f"[dashboard] / re-render failed; serving on-disk file: "
                      f"{type(_render_exc).__name__}: {_render_exc}", file=_sys.stderr)
            return self._serve_file(workspace_paths().reports / "index.html", "text/html")
        # GitHub auth (cherry-picked from #65, Phase B-bis).
        if self.path.startswith("/api/auth/github/status"):
            return self._get_auth_github_status()
        if self.path.startswith("/api/auth/github/poll"):
            return self._get_auth_github_poll()
        if self.path.startswith("/api/auth/github/orgs"):
            return self._get_auth_github_orgs()
        if self.path.startswith("/api/workspaces"):
            return self._get_workspaces()
        if self.path.startswith("/api/source/builds"):
            return self._get_source_builds()
        if self.path.startswith("/api/state"):
            return self._serve_state()
        if self.path.startswith("/api/events"):
            return self._serve_events_sse()
        if self.path.startswith("/api/guidance"):
            return self._serve_guidance()
        if self.path.startswith("/api/branches"):
            return self._serve_branches()
        if self.path.startswith("/api/branch-diff"):
            return self._get_branch_diff()
        if self.path.startswith("/api/pending"):
            return self._serve_pending()
        if self.path.startswith("/api/registry"):
            return self._get_registry()
        if self.path.startswith("/api/composite-run/") and self.path.split("?", 1)[0].endswith("/state"):
            return self._get_composite_run_state()
        if self.path.startswith("/api/composite-run/") and self.path.split("?", 1)[0].endswith("/status"):
            return self._get_composite_run_status()
        if self.path.startswith("/api/composite-run/"):
            return self._get_composite_run()
        if self.path.startswith("/api/composite-runs"):
            return self._get_composite_runs()
        if self.path.startswith("/api/explorer/runs"):
            return self._get_explorer_runs()
        if self.path.startswith("/api/explorer/observables"):
            return self._get_explorer_observables()
        if self.path.startswith("/api/explorer/series"):
            return self._get_explorer_series()
        if self.path.startswith("/api/explorer/flux"):
            return self._get_explorer_flux()
        if self.path.startswith("/api/explorer/protein-breakdown"):
            return self._get_explorer_protein_breakdown()
        if self.path.startswith("/api/explorer/vector"):
            return self._get_explorer_vector()
        if self.path.startswith("/api/simulations"):
            return self._get_simulations()
        if self.path.startswith("/api/composite-state"):
            return self._get_composite_state()
        if self.path.startswith("/api/composite-resolve"):
            return self._get_composite_resolve()
        if self.path.startswith("/api/investigation-viz-html"):
            return self._get_investigation_viz_html()
        if self.path.startswith("/api/investigation-composites"):
            return self._get_investigation_composites()
        if self.path.startswith("/api/investigation-state-tree"):
            return self._get_investigation_state_tree()
        if self.path.startswith("/api/study-bigraph-paths"):
            return self._get_study_bigraph_paths()
        if path_only == "/api/inputs":
            return self._get_inputs()
        if path_only == "/api/data-sources":
            return self._get_data_sources()
        if path_only == "/api/data-source-file":
            return self._get_data_source_file()
        if self.path.startswith("/api/iset-list"):
            return self._get_iset_list()
        if self.path.startswith("/api/iset/") and self.path.split("?", 1)[0].rstrip("/").endswith("/report"):
            return self._get_iset_report()
        if self.path.startswith("/api/iset/"):
            return self._get_iset_detail()
        if self.path.startswith("/api/investigation-notebook/"):
            return self._get_investigation_notebook()
        if self.path.startswith("/api/investigation-run-unblocked-status"):
            return self._get_investigation_run_unblocked_status()
        if self.path.startswith("/api/investigation-registry"):
            return self._get_investigation_registry()
        if self.path.startswith("/api/study-charts/"):
            return self._get_study_charts()
        if self.path.startswith("/api/study-rigor"):
            return self._get_study_rigor()
        if self.path.startswith("/api/investigation-rigor"):
            return self._get_investigation_rigor()
        if self.path.startswith("/api/framework-metrics"):
            return self._send_json_bytes(*_framework_metrics(WORKSPACE))
        if _path_only_pre == "/api/investigation-hypotheses":
            import urllib.parse as _up
            _q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
            _slug = (_q.get("investigation") or _q.get("inv") or _q.get("name") or "").strip()
            return self._send_json_bytes(*_investigation_hypotheses(WORKSPACE, _slug))
        if self.path.startswith("/api/work-composite-diff"):
            return self._get_work_composite_diff()
        if self.path.startswith("/api/references-bib"):
            return self._get_references_bib()
        if self.path.startswith("/api/generation"):
            return self._get_generation()
        if self.path.startswith("/api/github-repo"):
            return self._get_github_repo()
        if self.path.startswith("/api/investigation-composite-doc"):
            return self._get_investigation_composite_doc()
        if self.path.startswith("/api/investigation/"):
            return self._get_investigation_detail()
        if self.path.startswith("/api/investigations"):
            return self._get_investigations()
        if self.path.startswith("/api/study-export"):
            return self._get_study_export()
        if self.path.startswith("/api/composites"):
            return self._get_composites()
        if self.path.startswith("/api/system-deps-check"):
            return self._get_system_deps_check()
        if self.path.startswith("/api/catalog"):
            return self._get_catalog()
        if self.path.startswith("/api/workspace-manifest"):
            return self._get_workspace_manifest()
        if self.path.startswith("/api/work-status"):
            return self._get_work_status()
        if self.path.startswith("/api/branch-staleness"):
            return self._get_branch_staleness()
        if self.path.startswith("/api/dirty-status"):
            return self._get_dirty_status()
        if self.path.startswith("/api/suggest-poll"):
            return self._get_suggest_poll()
        if self.path.startswith("/api/visualization-status"):
            return self._get_visualization_status()
        if self.path.startswith("/api/visualization-instances"):
            return self._get_visualization_instances()
        if self.path.startswith("/api/visualization-classes"):
            return self._get_visualization_classes()
        if self.path.startswith("/api/saved-visualizations"):
            return self._get_saved_visualizations()
        if self.path.startswith("/api/ui-config"):
            return self._get_ui_config()
        if self.path.startswith("/api/ptools-launch/"):
            study = self.path[len("/api/ptools-launch/"):].split("?", 1)[0]
            if not _SLUG_RE.match(study):
                return self._json({"error": "invalid study name"}, 400)
            return self._get_ptools_launch(study)
        if self.path.startswith("/api/git-status"):
            return self._get_git_status()
        if self.path.startswith("/api/remote-run-status"):
            return self._get_remote_run_status()
        # Serve the bigraph-loom viewer at /bigraph-loom. The bundle comes from
        # the standalone `bigraph-loom` package (a dependency), via
        # bigraph_loom.asset_dir(), rather than a vendored copy.
        if self.path.startswith("/bigraph-loom"):
            # Strip query string before resolving to the file on disk; popup
            # URLs include ?id=<ref> which would otherwise prevent the
            # static handler from finding index.html.
            loom_path = self.path.split("?", 1)[0]
            rel = loom_path[len("/bigraph-loom"):].lstrip("/") or "index.html"
            if ".." in rel.split("/"):
                self.send_response(403); self.end_headers(); return
            from bigraph_loom import asset_dir
            target = asset_dir() / rel
            return self._serve_file(target, self._guess_mime(rel))

        # Serve the parsimony 3D viewer at /parsimony-viewer/*. The bundle comes
        # from the optional `pbg_parsimony` package (pbg_parsimony/viewer/),
        # resolved at request time — like bigraph-loom above. Feature-detected:
        # if pbg_parsimony is not installed the route simply 404s and the
        # Analyses gallery hides the 3D cards.
        if self.path.startswith("/parsimony-viewer"):
            pv_dir = _parsimony_viewer_dir()
            if pv_dir is None:
                self.send_response(404); self.end_headers(); return
            pv_path = self.path.split("?", 1)[0]
            rel = pv_path[len("/parsimony-viewer"):].lstrip("/") or "index.html"
            if ".." in rel.split("/"):
                self.send_response(403); self.end_headers(); return
            return self._serve_file(pv_dir / rel, self._guess_mime(rel))

        # Generic static file serving — also strip query strings so any
        # other route that the client appends params to still resolves.
        static_path = self.path.split("?", 1)[0]
        rel = static_path.lstrip("/")
        # Refuse path traversal (rel is already lstrip-ed of leading "/").
        if ".." in rel.split("/"):
            self.send_response(403); self.end_headers(); return
        # Package-bundled static first (style.css, walkthrough.js, vivarium-logo.png,
        # render-helpers.js, client.js); then workspace tree (for files the
        # workspace might add); then workspace/reports/ (rendered output dir).
        bundled = STATIC_DIR / rel
        if bundled.is_file():
            return self._serve_file(bundled, self._guess_mime(rel))
        # Compatibility: the live dashboard HTML references bundled assets
        # under `/assets/<file>` (e.g. `<script src="assets/walkthrough.js">`),
        # but STATIC_DIR stores them at the package root with no `assets/`
        # prefix. Strip the prefix and retry the bundled lookup before
        # falling through to the workspace tree — otherwise a stale
        # `reports/assets/<file>` copy left by an earlier `pbg-report` run
        # would shadow the live source.
        if rel.startswith("assets/"):
            bundled_alt = STATIC_DIR / rel[len("assets/"):]
            if bundled_alt.is_file():
                return self._serve_file(bundled_alt, self._guess_mime(rel))
        primary = WORKSPACE / rel
        if primary.is_file():
            return self._serve_file(primary, self._guess_mime(rel))
        fallback = workspace_paths().reports / rel
        return self._serve_file(fallback, self._guess_mime(rel))

    def _csrf_ok(self) -> bool:
        """Same-origin guard for state-mutating (POST/DELETE) requests.

        Conservative allowlist designed NOT to break the same-origin SPA or
        local CLI tools while blocking cross-site forged requests to the
        loopback server (which can run git/gh/pip/shell):

          * ``Origin`` ABSENT  -> ALLOW. Covers curl, the user's local CLI
            tools, and same-origin navigations that omit the header.
          * ``Origin`` PRESENT -> its ``host:port`` must equal the request's
            ``Host`` header (same-origin). Match -> ALLOW; mismatch -> 403.

        The SPA is served same-origin by this same server, so its fetches send
        an ``Origin`` equal to ``Host`` (or none) and are always allowed.

        Set ``VIVARIUM_DASHBOARD_DISABLE_CSRF=1`` to bypass enforcement
        (escape hatch; enforcement is ON by default).

        Returns True if the request may proceed. On rejection, emits a 403
        JSON error and returns False.
        """
        from vivarium_dashboard.lib import csrf as _csrf
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if _csrf.is_request_allowed(
            origin, host, disabled=_csrf.is_disabled_via_env(os.environ)
        ):
            return True
        self._json({"error": "cross-origin request forbidden"}, 403)
        return False

    def do_POST(self):
        if not self._csrf_ok():
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode()) if length else {}
        except json.JSONDecodeError as e:
            return self._json({"error": f"invalid JSON: {e}"}, 400)

        # Match the route on the path WITHOUT its query string (mirrors do_GET).
        # self.path is left intact so handlers can still read query params
        # (e.g. _post_study_report_single honours ?skeptic=1). Without this,
        # any POST carrying a query string 404s — including handlers that
        # explicitly support query params.
        post_path_only = self.path.split("?", 1)[0]

        if post_path_only.startswith("/api/study-refresh-viz/"):
            return self._post_study_refresh_viz(body)

        method_name = _POST_ROUTE_MAP.get(post_path_only)
        if method_name is None:
            return self._json({"error": "not found"}, 404)
        getattr(self, method_name)(body)

    def do_DELETE(self):
        if not self._csrf_ok():
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode()) if length else {}
        except json.JSONDecodeError as e:
            return self._json({"error": f"invalid JSON: {e}"}, 400)

        method_name = _DELETE_ROUTE_MAP.get(self.path)
        if method_name is None:
            return self._json({"error": "not found"}, 404)
        getattr(self, method_name)(body)

    # ------------------------------------------------------------------
    # POST handlers
    # ------------------------------------------------------------------

    def _post_study_refresh_viz(self, body: dict):
        """POST /api/study-refresh-viz/<name> — re-render the study's declared
        visualizations against its latest run, stamping provenance.

        Thin HTTP wrapper around :func:`_study_refresh_viz` (the pure seam).
        Tolerant by design: per-chart render failures come back as
        ``status="error"`` entries (never a 500). Only a missing study 404s.
        """
        import urllib.parse
        path = urllib.parse.urlparse(self.path).path
        name = path[len("/api/study-refresh-viz/"):].strip("/")
        if not name:
            return self._json({"error": "missing study name"}, 400)
        try:
            payload = _study_refresh_viz(WORKSPACE, name)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e), "study": name}, 500)
        if payload.get("not_found"):
            return self._json({"error": payload["error"], "study": name}, 404)
        return self._json(payload, 200)

    def _post_feedback_import(self, body: dict):
        """POST /api/feedback-import — ingest feedback submitted directly from
        the report widget (expert-feedback B.2).

        Body is the same ``{meta, annotations}`` payload the widget builds for
        its YAML download. Writes it to investigations/<inv>/feedback/<ts>.yaml
        via the shared pbg_superpowers writer, so direct submit and the
        pbg-feedback-import CLI land identically. Eliminates the
        download→email→CLI round-trip when the report is viewed live.
        """
        try:
            from pbg_superpowers.feedback_import import (
                write_feedback_payload, FeedbackImportError,
            )
        except ImportError:
            return self._json(
                {"error": "pbg-superpowers not available for feedback import"}, 500)
        try:
            target = write_feedback_payload(WORKSPACE, body)
        except FeedbackImportError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": f"feedback import failed: {e}"}, 500)
        anns = body.get("annotations") or {}
        n_entries = sum(len(v or []) for v in anns.values() if isinstance(v, list))
        return self._json({
            "ok": True,
            "path": str(target.relative_to(WORKSPACE)),
            "n_entries": n_entries,
        }, 200)

    # ------------------------------------------------------------------
    # GitHub auth (cherry-picked from #65, Phase B-bis).
    # The lib is vivarium_dashboard/lib/github_auth.py; the JS widget is
    # static/github-login.js. UI placement (the sign-in chip) is left for a
    # follow-up PR — the widget is defensive and no-ops without its target.
    # ------------------------------------------------------------------

    def _post_auth_github_start(self, body: dict):
        """POST /api/auth/github/start — initiate the Device Flow.

        Returns the user_code + verification_uri the client must display, plus
        a server-issued flow_id the client passes to ``/poll``. Never returns
        the device_code (held server-side).
        """
        from vivarium_dashboard.lib.github_auth import start_device_flow
        result = start_device_flow()
        if "error" in result:
            # 503 for missing client_id (deployment not configured); 502 for
            # GitHub-side failures.
            code = 503 if result["error"] == "no_client_id" else 502
            return self._json(result, code)
        return self._json(result, 200)

    def _get_auth_github_poll(self):
        """GET /api/auth/github/poll?flow_id=<uuid> — poll the token endpoint."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        flow_id = (qs.get("flow_id") or [""])[0].strip()
        if not flow_id:
            return self._json({"status": "error", "detail": "missing_flow_id"}, 400)

        from vivarium_dashboard.lib.github_auth import poll_device_flow
        result = poll_device_flow(flow_id)
        # Map outcomes to HTTP codes the client can use without parsing JSON:
        #   pending → 202, ok → 200, expired → 410, denied → 403, error → 400.
        status = result.get("status")
        code = {
            "ok": 200, "pending": 202, "expired": 410, "denied": 403,
        }.get(status, 400)
        return self._json(result, code)

    def _get_auth_github_status(self):
        """GET /api/auth/github/status — current session, or {authenticated:false}.

        Never includes the token itself."""
        from vivarium_dashboard.lib.github_auth import status_payload
        return self._json(status_payload(), 200)

    def _post_auth_github_logout(self, body: dict):
        """POST /api/auth/github/logout — clear in-memory session + keyring entry."""
        from vivarium_dashboard.lib.github_auth import logout
        logout()
        return self._json({"ok": True}, 200)

    def _get_auth_github_orgs(self):
        """GET /api/auth/github/orgs — user's personal namespace + orgs."""
        from vivarium_dashboard.lib.github_auth import list_orgs
        result = list_orgs()
        if "error" in result:
            code = 401 if result["error"] == "unauthenticated" else 502
            return self._json(result, code)
        return self._json(result, 200)

    def _post_click(self, body: dict):
        with LOCK:
            events = workspace_paths().pbg / "server" / "state" / "events"
            events.parent.mkdir(parents=True, exist_ok=True)
            with events.open("a") as f:
                f.write(json.dumps(body) + "\n")
        self.send_response(204)
        self.end_headers()

    def _post_import(self, body: dict):
        """Register an import in the catalog (workspace.yaml.imports).

        NOTE: git submodule add is NOT performed here. Submodule operations
        require terminal access for network/auth reasons. After this call,
        run from your terminal:
          git submodule add <source> external/<name>   # for reference / in-place
        The response includes the exact command to run.
        """
        name = (body.get("name") or "").strip()
        source = (body.get("source") or "").strip()
        ref = (body.get("ref") or "").strip()
        mode = (body.get("mode") or "").strip()
        description = (body.get("description") or "").strip() or None

        if not all([name, source, ref, mode]):
            return self._json({"error": "name, source, ref, mode are required"}, 400)
        if mode not in ("reference", "fork-source", "in-place"):
            return self._json({"error": "mode must be one of: reference, fork-source, in-place"}, 400)
        if re.search(r'[^\w\-.]', name):
            return self._json({"error": "name must contain only word chars, hyphens, dots"}, 400)

        commit_msg = f"feat(0.5): register import '{name}' (mode={mode})"

        def action():
            resp_lib, code_lib = _upload_mut.register_import_entry(WORKSPACE, body)
            if code_lib != 200:
                raise RuntimeError(resp_lib.get("error") or "register_import_entry failed")

        resp, code = _active_branch_action(commit_msg, action)
        if code == 200:
            # Add guidance about submodule step.
            if mode in ("reference",):
                resp["next_terminal_step"] = f"git submodule add {source} external/{name}"
            elif mode == "in-place":
                resp["next_terminal_step"] = f"git submodule add {source} external/{name}"
            else:
                resp["next_terminal_step"] = "(fork-source: no submodule needed)"
            resp["note"] = (
                "git submodule add is NOT performed by the server (requires terminal for network/auth). "
                "Run 'next_terminal_step' from your workspace root to complete the import."
            )
        return self._json(resp, code)

    def _post_dataset(self, body: dict):
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "name is required"}, 400)
        claims_raw = body.get("claims", "")
        if isinstance(claims_raw, str):
            claims = [c.strip() for c in claims_raw.split(",") if c.strip()]
        elif isinstance(claims_raw, list):
            claims = list(claims_raw)
        else:
            claims = []

        entry: dict = {"name": name, "claims": claims}

        file_b64 = body.get("file_b64", "").strip()
        filename = (body.get("filename") or "").strip()
        path = (body.get("path") or "").strip()
        url = (body.get("url") or "").strip()
        investigation = (body.get("investigation") or "").strip()
        if investigation and not _SLUG_RE.match(investigation):
            return self._json({"error": f"invalid investigation slug: '{investigation}'"}, 400)

        if file_b64:
            if not filename:
                return self._json({"error": "filename is required when file_b64 is provided"}, 400)
            if investigation:
                dest_rel = f"investigations/{investigation}/inputs/datasets/{_safe_slug(name)}/{filename}"
            else:
                dest_rel = f"datasets/{_safe_slug(name)}/{filename}"
            entry["path"] = dest_rel
        elif path:
            entry["path"] = path
        elif url:
            entry["url"] = url
            sha256 = body.get("sha256", "").strip()
            if sha256:
                entry["sha256"] = sha256
        else:
            return self._json({"error": "either file_b64, path, or url is required"}, 400)

        commit_msg = f"feat(4): register dataset '{name}'"

        def action():
            resp_lib, code_lib = _upload_mut.register_dataset(WORKSPACE, body)
            if code_lib != 200:
                raise RuntimeError(resp_lib.get("error") or "register_dataset failed")

        if investigation:
            commit_msg = f"feat(4): register dataset '{name}' for investigation '{investigation}'"
        return self._json(*_active_branch_action(commit_msg, action))

    def _post_reference_pdf(self, body: dict):
        """Drop-and-go PDF reference flow (v0.1.12)."""
        pdf_b64 = body.get("pdf_b64", "").strip()
        if not pdf_b64:
            return self._json({"error": "pdf_b64 is required"}, 400)

        import base64 as _base64
        raw_pdf = _base64.b64decode(pdf_b64)
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.pdf_metadata import extract_pdf_metadata, auto_bib_key, build_bibtex
        extracted = extract_pdf_metadata(raw_pdf)

        investigation = (body.get("investigation") or "").strip()
        if investigation and not _SLUG_RE.match(investigation):
            return self._json({"error": f"invalid investigation slug: '{investigation}'"}, 400)

        title = (body.get("title") or "").strip() or extracted.get("title", "")
        authors_input = (body.get("authors") or "").strip()
        if authors_input:
            authors = [a.strip() for a in re.split(r"[;|]| and ", authors_input) if a.strip()]
        else:
            authors = extracted.get("authors", [])
        year_raw = body.get("year")
        if year_raw is not None:
            try:
                year: int | None = int(year_raw)
            except (ValueError, TypeError):
                year = extracted.get("year")
        else:
            year = extracted.get("year")
        journal = (body.get("journal") or "").strip() or None
        doi = (body.get("doi") or "").strip() or None

        bib_key = (body.get("bib_key") or "").strip()
        if not bib_key:
            bib_key = auto_bib_key(authors, year)
        if not re.match(r"^[A-Za-z0-9_:\-]+$", bib_key):
            return self._json({"error": f"invalid bib_key: '{bib_key}'"}, 400)

        metadata_pending = (
            not title or not authors or not year or bib_key.startswith("_pending")
        )

        claim_mappings_raw = body.get("claim_mappings", [])
        if isinstance(claim_mappings_raw, str):
            claim_ids: list[str] = [c.strip() for c in claim_mappings_raw.split(",") if c.strip()]
        elif isinstance(claim_mappings_raw, list):
            claim_ids = [str(c).strip() for c in claim_mappings_raw if str(c).strip()]
        else:
            claim_ids = []

        commit_msg = f"feat(5): add reference '{bib_key}'"
        if metadata_pending:
            commit_msg += " (metadata pending)"

        def action():
            _reference_mut._apply_reference_pdf(
                WORKSPACE,
                bib_key=bib_key,
                title=title,
                authors=authors,
                year=year,
                journal=journal,
                doi=doi,
                investigation=investigation,
                claim_ids=claim_ids,
                metadata_pending=metadata_pending,
                pdf_b64=pdf_b64,
            )

        response, status = _active_branch_action(commit_msg, action)
        response["bib_key"] = bib_key
        response["metadata_pending"] = metadata_pending
        response["extracted"] = {k: v for k, v in extracted.items() if k != "raw"}
        return self._json(response, status)

    def _post_reference(self, body: dict):
        """Legacy BibTeX-paste reference flow (now also served as /api/reference-bibtex)."""
        bibtex_text = (body.get("bibtex_text") or "").strip()
        claim_mappings_raw = body.get("claim_mappings", {})
        pdf_b64 = body.get("pdf_b64", "").strip()

        if not bibtex_text:
            return self._json({"error": "bibtex_text is required"}, 400)

        m = re.search(r"@\w+\{([^,\s]+)", bibtex_text)
        if not m:
            return self._json({"error": "could not parse BibTeX key from bibtex_text"}, 400)
        bibkey = m.group(1).strip()

        investigation = (body.get("investigation") or "").strip()
        if investigation and not _SLUG_RE.match(investigation):
            return self._json({"error": f"invalid investigation slug: '{investigation}'"}, 400)

        if isinstance(claim_mappings_raw, str):
            claim_mappings: dict = {}
            for pair in claim_mappings_raw.split(","):
                pair = pair.strip()
                if ":" in pair:
                    cid, bkey = pair.split(":", 1)
                    claim_mappings[cid.strip()] = bkey.strip()
        else:
            claim_mappings = dict(claim_mappings_raw) if claim_mappings_raw else {}

        commit_msg = f"feat(5): add reference '{bibkey}'"

        def action():
            _reference_mut._apply_reference(
                WORKSPACE,
                bibkey=bibkey,
                bibtex_text=bibtex_text,
                investigation=investigation,
                claim_mappings=claim_mappings,
                pdf_b64=pdf_b64,
            )

        return self._json(*_active_branch_action(commit_msg, action))

    def _post_expert_doc(self, body: dict):
        """Register an expert document in workspace.yaml."""
        import shutil as _shutil

        name = (body.get("name") or "").strip()
        file_b64 = body.get("file_b64", "").strip()
        filename = (body.get("filename") or "").strip()
        source_path_raw = (body.get("source_path") or "").strip()
        description = (body.get("description") or "").strip() or None
        contributor = (body.get("contributor") or "").strip() or None
        claims_raw = body.get("claims_supported", [])

        investigation = (body.get("investigation") or "").strip()
        if investigation and not _SLUG_RE.match(investigation):
            return self._json({"error": f"invalid investigation slug: '{investigation}'"}, 400)

        if not name:
            return self._json({"error": "name is required"}, 400)
        if not file_b64 and not source_path_raw:
            return self._json({"error": "either file_b64+filename or source_path is required"}, 400)

        if isinstance(claims_raw, str):
            claims_supported = [c.strip() for c in claims_raw.split(",") if c.strip()]
        elif isinstance(claims_raw, list):
            claims_supported = list(claims_raw)
        else:
            claims_supported = []

        expert_dir = (f"investigations/{investigation}/inputs/expert"
                      if investigation else "references/expert")

        if file_b64:
            if not filename:
                return self._json({"error": "filename is required when file_b64 is provided"}, 400)
            ext = Path(filename).suffix if Path(filename).suffix else ".pdf"
            dest_rel = f"{expert_dir}/{_safe_slug(name)}{ext}"
            source_path = None
        else:
            source_path = Path(source_path_raw)
            if not source_path.is_absolute():
                source_path = WORKSPACE / source_path
            if not source_path.exists():
                return self._json({"error": f"source_path does not exist: {source_path}"}, 400)
            if not source_path.is_file():
                return self._json({"error": f"source_path is not a regular file: {source_path}"}, 400)
            ext = source_path.suffix if source_path.suffix else ".pdf"
            dest_rel = f"{expert_dir}/{_safe_slug(name)}{ext}"

        commit_msg = (f"feat(5): add expert document '{name}' for investigation '{investigation}'"
                      if investigation else f"feat(5): add expert document '{name}'")

        def action():
            resp_lib, code_lib = _upload_mut.register_expert_doc(WORKSPACE, body)
            if code_lib != 200:
                raise RuntimeError(resp_lib.get("error") or "register_expert_doc failed")

        return self._json(*_active_branch_action(commit_msg, action))

    def _post_observable(self, body: dict):
        """Register an observable in workspace.yaml (v0.3.0: top-level, no model).

        Body: {name, store_path, units?, description?}
        """
        name = (body.get("name") or "").strip()
        store_path = (body.get("store_path") or "").strip()
        units = (body.get("units") or "").strip() or None
        description = (body.get("description") or "").strip() or None

        if not all([name, store_path]):
            return self._json({"error": "name and store_path are required"}, 400)

        commit_msg = f"feat(setup): add observable '{name}'"

        def action():
            resp_lib, code_lib = _viz_commit_mut.observable_add(WORKSPACE, body)
            if code_lib != 200:
                raise RuntimeError(resp_lib.get("error") or "observable_add failed")

        return self._json(*_active_branch_action(commit_msg, action))

    def _post_visualization(self, body: dict):
        """Register a visualization in workspace.yaml.

        Three entry modes (mutually compatible — combine fields as needed):
            description-first: {name, description}  → Create → /pbg-viz skill
            class-backed:      {name, class, config}  → configured instance of
                               a registered Visualization v2 class
            structured legacy: {name, type, observables, config?, simulation?}

        Only `name` is required.
        """
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "name is required"}, 400)
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return self._json({"error": "name must match ^[a-zA-Z0-9_-]+$"}, 400)

        description = (body.get("description") or "").strip() or None
        viz_class = (body.get("class") or "").strip() or None
        viz_type = (body.get("type") or "").strip() or None
        obs_list = body.get("observables") or []
        config = body.get("config") or {}
        simulation_name = (body.get("simulation") or "").strip() or None

        if viz_class:
            known = {c["name"] for c in self._list_visualization_classes()
                     if c.get("kind") != "analysis"}
            if viz_class not in known:
                return self._json(
                    {"error": f"class '{viz_class}' is not a registered Visualization. "
                              f"Available: {sorted(known)}"},
                    400,
                )

        # Structured path: if type or observables are provided, validate them fully.
        if viz_type or obs_list:
            if not viz_type:
                return self._json({"error": "type is required when observables are specified"}, 400)
            if viz_type not in ("time-series", "phase-space", "heatmap", "histogram"):
                return self._json({"error": "type must be one of: time-series, phase-space, heatmap, histogram"}, 400)
            if not isinstance(obs_list, list) or not obs_list:
                return self._json({"error": "observables must be a non-empty list"}, 400)

        commit_msg = f"feat(setup): add visualization '{name}'"

        def action():
            resp_lib, code_lib = _viz_commit_mut.visualization_add(WORKSPACE, body)
            if code_lib != 200:
                raise RuntimeError(resp_lib.get("error") or "visualization_add failed")

        return self._json(*_active_branch_action(commit_msg, action))

    def _post_visualization_create(self, body: dict):
        """Write a .pbg/viz-requests/<name>.md file with the description and workspace context.

        Body: {name: str}
        Returns: {ok, request_path, skill_command, instructions}

        Thin shim — delegates to :func:`lib.viz_write_mutations.visualization_create`.
        """
        from vivarium_dashboard.lib import viz_write_mutations as _viz_write
        resp, code = _viz_write.visualization_create(WORKSPACE, body)
        return self._json(resp, code)

    def _get_visualization_status(self):
        """Return lifecycle status for a viz: described | requested | created | added | committed.

        Thin shim — delegates to :func:`lib.study_viz_views.build_visualization_status`.
        """
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        name = (qs.get("name") or [""])[0]
        body, status = _study_viz.build_visualization_status(WORKSPACE, name)
        return self._json(body, status)

    def _post_visualization_add_to_project(self, body: dict):
        """Copy .pbg/viz-responses/<name>.py to .pbg/visualizations-staged/<name>.py.

        Does NOT commit (Commit is a separate action). Working tree stays clean
        because both source and dest are gitignored.

        Thin shim — delegates to :func:`lib.viz_write_mutations.visualization_add_to_project`.
        """
        from vivarium_dashboard.lib import viz_write_mutations as _viz_write
        resp, code = _viz_write.visualization_add_to_project(WORKSPACE, body)
        return self._json(resp, code)

    def _post_visualization_commit_batch(self, body: dict):
        """Move all staged visualizations to the workspace package + commit on active branch.

        Body: {names?: list[str]} — if omitted, commits all staged.
        """
        staged_dir = workspace_paths().pbg / "visualizations-staged"
        if not staged_dir.is_dir():
            return self._json({"error": "no staged visualizations"}, 404)

        requested = body.get("names")
        available = sorted(p.stem for p in staged_dir.glob("*.py"))
        if requested:
            names = [n for n in requested if n in available]
        else:
            names = available
        if not names:
            return self._json({"error": "no staged visualizations match"}, 404)

        moved_names = list(names)  # captured for closure

        def action():
            resp_lib, code_lib = _viz_commit_mut.visualization_commit_batch(WORKSPACE, body)
            if code_lib != 200:
                raise RuntimeError(resp_lib.get("error") or "visualization_commit_batch failed")

        commit_msg = (
            f"feat(viz): commit {len(moved_names)} visualization(s): {', '.join(moved_names)}"
            if len(moved_names) > 1
            else f"feat(viz): commit {moved_names[0]}"
        )
        resp, code = _active_branch_action(commit_msg, action)

        if code == 200:
            resp["ok"] = True
            resp["committed"] = moved_names
        return self._json(resp, code)

    def _post_visualization_generate(self, body: dict):
        """POST /api/visualization-generate {name, description} — write a
        new-contract viz-request file at .pbg/viz-requests/<name>.md. The
        /pbg-viz skill consumes the request and writes a decorated function
        to <workspace_pkg>/visualizations/<snake>.py.

        Thin shim — delegates to :func:`lib.viz_write_mutations.visualization_generate`.
        """
        from vivarium_dashboard.lib import viz_write_mutations as _viz_write
        resp, code = _viz_write.visualization_generate(WORKSPACE, body)
        return self._json(resp, code)

    def _post_visualization_accept(self, body: dict):
        """POST /api/visualization-accept {name, class_name?} — finalize a
        generated viz: invalidate the registry cache, verify the file imports
        cleanly, confirm the class is visible, then commit on the active branch.
        """
        name = (body.get("name") or "").strip()
        class_name = (body.get("class_name") or "").strip()
        if not name:
            return self._json({"error": "name is required"}, 400)

        snake = name.lower().replace("-", "_")
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        target_rel = f"{pkg}/visualizations/{snake}.py"
        target_abs = WORKSPACE / target_rel
        if not target_abs.is_file():
            return self._json({"error": f"generated file not found at {target_rel}"}, 404)

        # Invalidate the module-level registry cache so the next registry
        # fetch will rebuild from disk.
        clear_registry_cache()

        # Attempt a fresh in-process import to verify the file loads cleanly.
        try:
            _ws_add_to_sys_path()
            sys.path.insert(0, str(WORKSPACE))
            import importlib
            mod_name = f"{pkg}.visualizations.{snake}"
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
            else:
                __import__(mod_name)
            # Also reload the visualizations package itself so the new module
            # is picked up by subsequent _list_visualization_classes calls.
            pkg_viz_mod = f"{pkg}.visualizations"
            if pkg_viz_mod in sys.modules:
                importlib.reload(sys.modules[pkg_viz_mod])
        except Exception as e:
            return self._json({
                "error": f"generated file failed to import: {type(e).__name__}: {e}"
            }, 500)

        # Smoke-test the workspace's build_core() so a generated class that
        # breaks bigraph-schema discovery (e.g. malformed inputs type strings,
        # circular imports, type registration errors) surfaces here rather
        # than at first investigation run. Invalidate the cached base core so
        # the rebuild walks the new module too.
        try:
            import bigraph_schema.core as _bsc
            _bsc._cached_base_core = None
        except Exception:
            pass
        try:
            core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
            core_module.build_core()
        except Exception as e:
            return self._json({
                "error": (
                    f"workspace build_core() failed after importing the generated file: "
                    f"{type(e).__name__}: {e}"
                )
            }, 500)

        # Verify the class is discoverable when class_name is supplied.
        # We walk the imported module's attributes directly (using the
        # is_visualization() marker) rather than relying on core.link_registry,
        # because non-installed workspace packages are not discovered by
        # discover_packages() / importlib.metadata.
        if class_name:
            found = False
            mod = sys.modules.get(f"{pkg}.visualizations.{snake}")
            if mod is not None:
                for attr_val in vars(mod).values():
                    if not isinstance(attr_val, type):
                        continue
                    if getattr(attr_val, "__name__", None) != class_name:
                        continue
                    marker = getattr(attr_val, "is_visualization", None)
                    if callable(marker):
                        try:
                            if marker() is True:
                                found = True
                                break
                        except Exception:
                            pass
                    # Fallback: check subclass of Visualization base
                    if not found:
                        try:
                            from pbg_superpowers.visualization import Visualization as _VizBase
                            if issubclass(attr_val, _VizBase) and attr_val is not _VizBase:
                                found = True
                                break
                        except ImportError:
                            pass
            if not found:
                return self._json({
                    "error": (
                        f"class {class_name!r} not found in generated file after import; "
                        f"check the @as_visualization name= argument matches"
                    )
                }, 500)

        commit_msg = f"feat(viz): generate {class_name or name} via /pbg-viz"

        def action():
            pass  # file was already written by the skill; git add -A picks it up

        try:
            resp, code = _active_branch_action(commit_msg, action)
        except Exception as e:
            resp, code = {"error": f"workstream error: {e}"}, 500
        return self._json(resp, code)

    def _post_simulation(self, body: dict):
        """Register a simulation in workspace.yaml.

        Body: {name, description?, t_start, t_end, initial_state?, parameter_overrides?,
               emitter_config?, processes?}
        """
        import re as _re
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip() or None
        t_start = body.get("t_start")
        t_end = body.get("t_end")
        initial_state = body.get("initial_state") or None
        parameter_overrides = body.get("parameter_overrides") or None
        emitter_config = body.get("emitter_config") or None
        composite = (body.get("composite") or "").strip() or None
        processes_raw = body.get("processes", [])

        if not name:
            return self._json({"error": "name is required"}, 400)
        if not _re.match(r"^[a-zA-Z0-9_-]+$", name):
            return self._json({"error": "name must match ^[a-zA-Z0-9_-]+$"}, 400)
        if t_start is None or t_end is None:
            return self._json({"error": "t_start and t_end are required"}, 400)
        try:
            t_start = float(t_start)
            t_end = float(t_end)
        except (TypeError, ValueError):
            return self._json({"error": "t_start and t_end must be numbers"}, 400)
        if t_start < 0:
            return self._json({"error": "t_start must be >= 0"}, 400)
        if t_end <= t_start:
            return self._json({"error": "t_end must be > t_start"}, 400)

        # Validate processes list.
        if not isinstance(processes_raw, list):
            return self._json({"error": "processes must be a list of strings"}, 400)
        processes_list = [str(p).strip() for p in processes_raw if str(p).strip()]

        # Validate process names against registry (best-effort; skip if registry unavailable).
        if processes_list:
            try:
                reg = _get_registry_data()
                if not reg.get("error"):
                    registered_proc_names = {p["name"] for p in (reg.get("processes") or [])}
                    for proc_name in processes_list:
                        if proc_name not in registered_proc_names:
                            return self._json(
                                {"error": f"process '{proc_name}' not in registry"}, 400
                            )
            except Exception as reg_err:
                # Registry call failed — warn but don't block.
                import logging
                logging.warning("Registry validation skipped: %s", reg_err)

        commit_msg = f"feat(setup): add simulation '{name}'"

        def action():
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
            ws_file = WORKSPACE / "workspace.yaml"
            ws = load_workspace(ws_file)

            simulations = ws.setdefault("simulations", [])
            if simulations is None:
                simulations = []
                ws["simulations"] = simulations
            for existing in simulations:
                if isinstance(existing, dict) and existing.get("name") == name:
                    raise ValueError(f"simulation '{name}' already registered")
            entry: dict = {"name": name, "t_start": t_start, "t_end": t_end}
            if description:
                entry["description"] = description
            if composite:
                entry["composite"] = composite
            if initial_state is not None:
                entry["initial_state"] = initial_state
            if parameter_overrides is not None:
                entry["parameter_overrides"] = parameter_overrides
            if emitter_config is not None:
                entry["emitter_config"] = emitter_config
            if processes_list:
                entry["processes"] = processes_list
            simulations.append(entry)
            save_workspace(ws_file, ws)

        return self._json(*_active_branch_action(commit_msg, action))

    def _delete_simulation(self, body: dict):
        """Remove a simulation from workspace.yaml.

        Body: {name}
        """
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "name is required"}, 400)

        commit_msg = f"feat(setup): remove simulation '{name}'"

        def action():
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
            ws_file = WORKSPACE / "workspace.yaml"
            ws = load_workspace(ws_file)
            simulations = ws.get("simulations") or []
            new_sims = [s for s in simulations if not (isinstance(s, dict) and s.get("name") == name)]
            if len(new_sims) == len(simulations):
                raise ValueError(f"simulation '{name}' not found")
            if new_sims:
                ws["simulations"] = new_sims
            else:
                ws.pop("simulations", None)
            save_workspace(ws_file, ws)

        return self._json(*_active_branch_action(commit_msg, action))

    def _delete_simulation_run(self, body: dict):
        """DELETE /api/simulation-run — full delete of one persisted run.

        Body: ``{run_id}``. Removes the runs_meta row, all history rows for
        that simulation_id, the ``.pbg/runs/<run_id>/`` directory if any,
        and the run_id from any ``study.yaml`` ``runs[]`` that references
        it. Returns the summary dict from
        ``simulations_index.delete_simulation``.

        Does NOT go through ``_active_branch_action``. Run DBs and run dirs
        are gitignored; ``study.yaml`` edits are left in the working tree
        (same UX as a Studies-tab edit before commit).
        """
        run_id = (body.get("run_id") or "").strip()
        if not run_id:
            return self._json({"error": "run_id is required"}, 400)

        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.simulations_index import (
            delete_simulation, RunNotFound,
        )
        try:
            summary = delete_simulation(WORKSPACE, run_id)
        except RunNotFound:
            return self._json({"error": "run not found"}, 404)
        except Exception as e:  # noqa: BLE001 — surface the failure, don't crash
            return self._json({"error": f"delete failed: {e}"}, 500)
        return self._json(summary, 200)

    def _delete_visualization(self, body: dict):
        """Remove a visualization from workspace.yaml.

        Body: {name}
        """
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "name is required"}, 400)

        commit_msg = f"feat(setup): remove visualization '{name}'"

        def action():
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
            ws_file = WORKSPACE / "workspace.yaml"
            ws = load_workspace(ws_file)
            visualizations = ws.get("visualizations") or []
            new_vizs = [v for v in visualizations if not (isinstance(v, dict) and v.get("name") == name)]
            if len(new_vizs) == len(visualizations):
                raise ValueError(f"visualization '{name}' not found")
            if new_vizs:
                ws["visualizations"] = new_vizs
            else:
                ws.pop("visualizations", None)
            save_workspace(ws_file, ws)

        return self._json(*_active_branch_action(commit_msg, action))

    def _post_run_tests(self, body: dict):
        """Run pytest for the workspace (v0.3.0: no model param).

        Returns JSON with returncode, stdout, stderr.
        """
        test_dir = workspace_paths().tests
        cmd = [sys.executable, "-m", "pytest", "-v", str(test_dir)]
        try:
            result = subprocess.run(
                cmd, cwd=WORKSPACE,
                capture_output=True, text=True, timeout=120,
            )
            return self._json({
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }, 200)
        except subprocess.TimeoutExpired:
            return self._json({"error": "pytest timed out after 120s"}, 500)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def _post_import_install(self, body: dict):
        """Pip-install an import into the workspace venv.

        Body: {name: str, target?: str}.
        `target` overrides the default install path (workspace.yaml.imports[name].path).
        """
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "missing name"}, 400)
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        imports = ws_data.get("imports", {})
        if name not in imports:
            return self._json({"error": f"import '{name}' not registered"}, 404)

        entry = imports[name]
        target = (body.get("target") or "").strip() or entry.get("path") or ""
        if not target:
            return self._json({"error": "no install target — set 'path' in import or pass 'target' in body"}, 400)

        # Resolve path relative to workspace (unless it's a URL/VCS spec).
        if not target.startswith(("http://", "https://", "git+")):
            abs_target = (WORKSPACE / target).resolve()
            if not abs_target.exists():
                return self._json({"error": f"path does not exist: {abs_target}"}, 404)
            target = str(abs_target)

        # Pick installer: prefer pip in the venv; fall back to system `uv` when
        # the venv has no pip (created via `uv venv`). Both produce the same
        # editable install in the venv's site-packages.
        venv_pip = WORKSPACE / ".venv" / "bin" / "pip"
        venv_py = WORKSPACE / ".venv" / "bin" / "python3"
        if venv_pip.exists():
            cmd = [str(venv_pip), "install", "-e", target]
        else:
            uv_path = shutil.which("uv")
            if uv_path and venv_py.exists():
                cmd = [uv_path, "pip", "install", "--python", str(venv_py), "-e", target]
            else:
                hint = (
                    "neither .venv/bin/pip nor `uv` found. "
                    "Create a venv with pip (`python -m venv .venv && .venv/bin/pip install --upgrade pip`) "
                    "or install uv (`brew install uv`)."
                )
                return self._json({"error": hint}, 500)

        # Run install (outside the branch action so errors surface before git work).
        try:
            result = subprocess.run(
                cmd,
                cwd=WORKSPACE, capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            return self._json({"error": f"{cmd[0]} install timed out after 120s"}, 500)
        except Exception as pip_err:
            return self._json({"error": f"install error: {pip_err}"}, 500)

        log_excerpt = (result.stdout + "\n" + result.stderr).strip()[-3000:]
        if result.returncode != 0:
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.install_errors import diagnose as _diagnose_install
            diag = _diagnose_install(log_excerpt)
            resp = {
                "error": "install failed",
                "log": log_excerpt[-1000:],
            }
            if diag:
                resp["diagnosis"] = diag.as_dict()
            return self._json(resp, 500)

        # Mark installed in workspace.yaml on a stage branch.
        install_target = target  # captured for closure

        def action():
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
            ws_file = WORKSPACE / "workspace.yaml"
            ws = load_workspace(ws_file)
            ws.setdefault("imports", {}).setdefault(name, {})["installed"] = True
            ws["imports"][name]["install_path"] = install_target
            save_workspace(ws_file, ws)

        commit_msg = f"chore(import): pip install {name} into venv"

        resp, code = _active_branch_action(commit_msg, action)

        # Invalidate registry cache so next /api/registry call sees fresh data.
        clear_registry_cache()

        # The pip install itself succeeded; if the metadata mutation was a
        # no-op (workspace.yaml already has installed=True on main), that's
        # not an error — surface it as a clean re-install acknowledgment.
        if code == 409 and "no changes" in (resp.get("error") or ""):
            return self._json({
                "ok": True,
                "already_installed": True,
                "message": "Package re-installed; workspace.yaml already marks it installed.",
                "log": log_excerpt[-500:],
            }, 200)

        if code == 200:
            resp["ok"] = True
            resp["log"] = log_excerpt[-500:]

        return self._json(resp, code)

    # ------------------------------------------------------------------
    # Work-stream endpoints (v0.4.0b)
    # ------------------------------------------------------------------

    def _post_work_start(self, body: dict):
        """Create a new working branch from base; set active in state."""
        branch = (body.get("branch") or "").strip()
        base = (body.get("base") or "main").strip()
        if not branch or not re.match(r"^[A-Za-z0-9._/-]+$", branch) or len(branch) > 100:
            return self._json({"error": "invalid branch name"}, 400)

        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import load_state, save_state
        state = load_state()
        if state.get("active_branch"):
            return self._json({"error": f"already on workstream '{state['active_branch']}'. End it first."}, 409)
        if _dirty_workspace().strip():
            return self._json({"error": "working tree dirty — commit or stash first"}, 409)

        # Verify base exists
        r = subprocess.run(["git", "rev-parse", "--verify", base], cwd=WORKSPACE, capture_output=True, text=True)
        if r.returncode != 0:
            return self._json({"error": f"base branch '{base}' not found"}, 404)

        # Verify branch doesn't already exist locally
        r = subprocess.run(["git", "rev-parse", "--verify", branch], cwd=WORKSPACE, capture_output=True, text=True)
        if r.returncode == 0:
            return self._json({"error": f"branch '{branch}' already exists. Pick a different name or delete the old one."}, 409)

        subprocess.run(["git", "checkout", base], cwd=WORKSPACE, check=True, capture_output=True)
        r = subprocess.run(["git", "checkout", "-b", branch], cwd=WORKSPACE, capture_output=True, text=True)
        if r.returncode != 0:
            return self._json({"error": f"branch create failed: {r.stderr[:300]}"}, 500)

        save_state({"active_branch": branch, "base": base, "pushed": False, "pr_number": None, "pr_url": None})
        return self._json({"ok": True, "branch": branch, "base": base}, 200)

    def _post_work_push(self, body: dict):
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import (
            load_state_or_adopt_current, save_state,
        )
        state = load_state_or_adopt_current()
        branch = state.get("active_branch")
        if not branch:
            return self._json({"error": "no active workstream"}, 409)

        # Pre-flight: refuse cleanly when no origin remote exists (the common
        # confusion on fresh workspaces). Surface a structured diagnosis the
        # JS layer can render as a clickable Create-GitHub-repo prompt.
        if not _has_origin_remote():
            return self._json({
                "error": "no GitHub remote configured",
                "diagnosis": {
                    "category": "no_origin",
                    "summary": "This workspace has no `origin` remote yet.",
                    "suggestion": "Click `Create GitHub repo` in the workstream strip to create one in your account and push in a single step.",
                },
            }, 409)

        r = subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout).strip()
            diag = _diagnose_push_error(err)
            resp = {"error": f"push failed: {err[:300]}"}
            if diag:
                resp["diagnosis"] = diag
            return self._json(resp, 500)
        state["pushed"] = True
        save_state(state)
        return self._json({"ok": True, "branch": branch, "log": r.stdout[-300:]}, 200)

    def _post_work_attach_report(self, body: dict):
        """POST /api/work-attach-report {filename, html, commit_message?}

        Writes ``html`` to ``reports/<filename>`` and creates a single commit
        on the current branch. Used by the Open-PR flow so the generated
        investigation report ships with the PR as a checked-in artifact
        (reviewers can read it inline on GitHub instead of downloading).

        Idempotent: re-runs with the same filename overwrite + amend nothing
        — they create a new commit each time (so reviewers see the report
        evolve alongside the code).
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import load_state
        state = load_state()
        branch = state.get("active_branch")
        if not branch:
            return self._json({"error": "no active investigation branch"}, 409)

        filename = (body.get("filename") or "").strip()
        html = body.get("html")
        if not filename or not isinstance(html, str) or not html:
            return self._json({"error": "filename + html required"}, 400)
        if "/" in filename or filename.startswith("."):
            return self._json({"error": "filename must be a bare name (no path / no leading .)"}, 400)
        commit_message = (body.get("commit_message") or
                          f"docs(report): attach {filename}").strip()

        reports_dir = workspace_paths().reports
        reports_dir.mkdir(parents=True, exist_ok=True)
        out_path = reports_dir / filename
        out_path.write_text(html)

        # Stage + commit. Allow the commit to fail cleanly when the file
        # hasn't actually changed (caller still gets a success response with
        # an `unchanged: true` flag).
        rel = str(out_path.relative_to(WORKSPACE))
        add = subprocess.run(["git", "add", "--", rel],
                             cwd=WORKSPACE, capture_output=True, text=True, timeout=10)
        if add.returncode != 0:
            return self._json({"error": f"git add failed: {(add.stderr or add.stdout)[:300]}"}, 500)
        commit = subprocess.run(
            ["git", "commit", "-m", commit_message, "--", rel],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=15,
        )
        if commit.returncode != 0:
            stderr = (commit.stderr or commit.stdout)
            # git returns non-zero when there's nothing to commit — treat as a soft success.
            if "nothing to commit" in stderr or "nothing added" in stderr:
                return self._json({"ok": True, "unchanged": True, "path": rel,
                                   "branch": branch}, 200)
            return self._json({"error": f"git commit failed: {stderr[:300]}"}, 500)
        sha = subprocess.run(["git", "rev-parse", "HEAD"],
                             cwd=WORKSPACE, capture_output=True, text=True, timeout=5)
        return self._json({"ok": True, "path": rel, "branch": branch,
                           "commit_sha": sha.stdout.strip()}, 200)

    def _post_work_link_branch(self, body: dict):
        """Link the workspace to an upstream branch.

        Body: {upstream_repo?: "owner/name", branch_name?: str, push?: bool=True,
               mode?: "branch" | "fork"}.

        mode="branch" (default):
        - Sets git origin to the upstream (https://github.com/<repo>.git) if absent.
        - Pushes the current branch (or `branch_name`, after renaming if provided).
        - Marks workstream as pushed.

        mode="fork":
        - Forks the upstream repo under the authenticated gh user (gh repo fork).
        - Sets origin to the fork URL; adds upstream remote pointing to the original.
        - Pushes the current branch to origin (the fork).
        - Returns fork and upstream full names in the response.

        Any other mode value returns 400.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import (
            load_state_or_adopt_current, save_state,
        )
        state = load_state_or_adopt_current()
        current_branch = state.get("active_branch")
        if not current_branch:
            return self._json({"error": "no active workstream — Start one first so the push has a target"}, 409)

        if not shutil.which("gh"):
            return self._json({"error": "gh CLI not installed. Install via `brew install gh` then `gh auth login`."}, 500)
        auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
        if auth.returncode != 0:
            return self._json({"error": "gh not authenticated. Run `gh auth login`."}, 500)

        mode = (body.get("mode") or "branch").strip().lower()
        if mode not in ("branch", "fork"):
            return self._json({"error": f"mode must be 'branch' or 'fork'; got {mode!r}"}, 400)

        upstream_repo = (body.get("upstream_repo") or "").strip() or self._default_upstream_repo()
        if not re.match(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", upstream_repo):
            return self._json({"error": f"upstream_repo must look like owner/name; got {upstream_repo!r}"}, 400)

        # Optional rename of the current branch before pushing.
        target_branch = (body.get("branch_name") or "").strip() or current_branch
        if not re.match(r"^[A-Za-z0-9._/-]+$", target_branch):
            return self._json({"error": "invalid branch name"}, 400)
        if target_branch != current_branch:
            r = subprocess.run(["git", "branch", "-m", current_branch, target_branch],
                               cwd=WORKSPACE, capture_output=True, text=True)
            if r.returncode != 0:
                return self._json({"error": f"branch rename failed: {(r.stderr or r.stdout)[:300]}"}, 500)

        if mode == "fork":
            # --- Fork mode ---
            # 1. Fork the upstream repo (no local clone, no remote change yet).
            repo_name = upstream_repo.split("/")[1]
            r = subprocess.run(
                ["gh", "repo", "fork", upstream_repo, "--remote=false", "--clone=false"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                return self._json({"error": f"gh repo fork failed: {(r.stderr or r.stdout)[:500]}"}, 500)

            # 2. Resolve the fork's full name via gh api user.
            login_r = subprocess.run(
                ["gh", "api", "user", "--jq", ".login"],
                capture_output=True, text=True,
            )
            if login_r.returncode != 0:
                return self._json({"error": f"could not resolve gh login: {(login_r.stderr or login_r.stdout)[:300]}"}, 500)
            gh_login = login_r.stdout.strip()
            fork_repo = f"{gh_login}/{repo_name}"
            fork_url = f"https://github.com/{fork_repo}.git"
            upstream_url = f"https://github.com/{upstream_repo}.git"

            # 3. Set origin to fork; add upstream remote.
            existing = subprocess.run(["git", "remote", "get-url", "origin"],
                                      cwd=WORKSPACE, capture_output=True, text=True)
            if existing.returncode != 0:
                r = subprocess.run(["git", "remote", "add", "origin", fork_url],
                                   cwd=WORKSPACE, capture_output=True, text=True)
                if r.returncode != 0:
                    return self._json({"error": f"git remote add origin failed: {(r.stderr or r.stdout)[:300]}"}, 500)
            else:
                r = subprocess.run(["git", "remote", "set-url", "origin", fork_url],
                                   cwd=WORKSPACE, capture_output=True, text=True)
                if r.returncode != 0:
                    return self._json({"error": f"git remote set-url origin failed: {(r.stderr or r.stdout)[:300]}"}, 500)

            # Add or update upstream remote.
            up_existing = subprocess.run(["git", "remote", "get-url", "upstream"],
                                         cwd=WORKSPACE, capture_output=True, text=True)
            if up_existing.returncode != 0:
                subprocess.run(["git", "remote", "add", "upstream", upstream_url],
                               cwd=WORKSPACE, capture_output=True, text=True)
            else:
                subprocess.run(["git", "remote", "set-url", "upstream", upstream_url],
                               cwd=WORKSPACE, capture_output=True, text=True)

            # 4. Push to fork.
            if body.get("push", True):
                r = subprocess.run(["git", "push", "-u", "origin", target_branch],
                                   cwd=WORKSPACE, capture_output=True, text=True, timeout=120)
                if r.returncode != 0:
                    return self._json({"error": f"git push to fork failed: {(r.stderr or r.stdout)[:500]}"}, 500)

            state["pushed"] = True
            save_state(state)

            return self._json({
                "ok": True,
                "fork": fork_repo,
                "upstream": upstream_repo,
                "branch": target_branch,
                "branch_url": f"https://github.com/{fork_repo}/tree/{target_branch}",
            }, 200)

        # --- Branch mode (default) ---
        # Set origin if not present (or replace if it points elsewhere).
        upstream_url = f"https://github.com/{upstream_repo}.git"
        existing = subprocess.run(["git", "remote", "get-url", "origin"],
                                  cwd=WORKSPACE, capture_output=True, text=True)
        if existing.returncode != 0:
            r = subprocess.run(["git", "remote", "add", "origin", upstream_url],
                               cwd=WORKSPACE, capture_output=True, text=True)
            if r.returncode != 0:
                return self._json({"error": f"git remote add origin failed: {(r.stderr or r.stdout)[:300]}"}, 500)
        else:
            # If origin already points somewhere else, refuse rather than silently overwriting.
            current_url = (existing.stdout or "").strip()
            if current_url and current_url != upstream_url and current_url != upstream_url.replace("https://github.com/", "git@github.com:"):
                return self._json({
                    "error": f"origin already configured to {current_url}; refusing to overwrite",
                    "current_origin": current_url,
                }, 409)

        # Push the current branch to origin.
        if body.get("push", True):
            r = subprocess.run(["git", "push", "-u", "origin", target_branch],
                               cwd=WORKSPACE, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                return self._json({"error": f"git push failed: {(r.stderr or r.stdout)[:500]}"}, 500)

        state["pushed"] = True
        save_state(state)

        return self._json({
            "ok": True,
            "upstream_repo": upstream_repo,
            "branch": target_branch,
            "branch_url": f"https://github.com/{upstream_repo}/tree/{target_branch}",
        }, 200)

    def _default_upstream_repo(self) -> str:
        """Auto-detect upstream repo from workspace.yaml or external/v2ecoli/.git/config.

        Falls back to ``vivarium-collective/v2ecoli`` if nothing else is configured.
        """
        ws_path = WORKSPACE / "workspace.yaml"
        if ws_path.exists():
            try:
                ws_data = yaml.safe_load(ws_path.read_text(encoding="utf-8")) or {}
                ur = (ws_data.get("upstream_repo") or "").strip()
                if ur:
                    return ur
            except yaml.YAMLError:
                pass
        # Try external/v2ecoli's origin.
        external = WORKSPACE / "external" / "v2ecoli"
        if external.is_dir():
            r = subprocess.run(["git", "remote", "get-url", "origin"],
                               cwd=external, capture_output=True, text=True)
            if r.returncode == 0:
                url = r.stdout.strip()
                # https://github.com/owner/name.git or git@github.com:owner/name.git
                m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", url)
                if m:
                    return m.group(1)
        return "vivarium-collective/v2ecoli"

    def _post_work_create_pr(self, body: dict):
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import (
            load_state_or_adopt_current, save_state,
        )
        state = load_state_or_adopt_current()
        branch = state.get("active_branch")
        if not branch:
            return self._json({"error": "no active workstream"}, 409)
        # Opportunistic: if local matches origin/<branch>, mark pushed automatically.
        if not state.get("pushed"):
            check = subprocess.run(
                ["git", "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD"],
                cwd=WORKSPACE, capture_output=True, text=True,
            )
            if check.returncode == 0:
                parts = (check.stdout or "").strip().split()
                if len(parts) == 2 and parts[1] == "0":
                    state["pushed"] = True
                    save_state(state)
        if not state.get("pushed"):
            # mem3dg-readdy friction #35: the old error said "click the Push
            # button" but that button only renders when the branch has an
            # upstream AND is ahead of it. For a never-pushed branch the
            # workstream strip shows "Link branch to upstream" instead, and
            # the user ended up stuck. Spell out BOTH UI paths plus the
            # terminal fallback so the user has an actionable next step
            # regardless of branch state.
            return self._json({
                "error": (
                    "branch not yet pushed. Use the workstream strip at "
                    "the top of the dashboard — click `Link branch to "
                    "upstream` (if shown) to create the remote and push, "
                    "or `Push` (if the branch already has an upstream). "
                    "Terminal fallback: `git push -u origin <branch>`; "
                    "the dashboard picks it up on the next refresh."
                ),
            }, 409)
        if state.get("pr_url"):
            return self._json({"error": f"PR already exists: {state['pr_url']}", "pr_url": state["pr_url"]}, 409)

        base = state.get("base") or "main"
        # PR title default: prefer the matching investigation's `title:`
        # field (from investigations/<branch>/investigation.yaml) so the
        # PR reads like "PDMP whole-cell model reformulation" rather than
        # the technical "Workstream: <branch>". Branch and investigation
        # slug are kept in 1:1 correspondence by the Investigation ≡
        # branch convention, so we look up by branch name. Falls back
        # to the legacy "Workstream: <branch>" when no matching
        # investigation.yaml is present (e.g., generic feature branches).
        def _default_pr_title(branch_name: str) -> str:
            inv_yaml = workspace_paths().investigations / branch_name / "investigation.yaml"
            if inv_yaml.is_file():
                try:
                    inv_spec = yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
                    inv_title = (inv_spec.get("title") or "").strip()
                    if inv_title:
                        return inv_title
                except Exception:
                    pass
            return f"Workstream: {branch_name}"

        title = (body.get("title") or "").strip() or _default_pr_title(branch)
        body_text = (body.get("body") or "").strip() or "Created via pbg-template dashboard."

        # Investigation PR convention: if the branch touches anything under
        # investigations/ AND the title isn't already prefixed, prepend
        # `investigation: `. Investigation PRs are living integration
        # branches — not merge targets — so they need to be visually
        # distinguishable in the PR list. Combined with the `draft=True`
        # default below, this enforces the convention end-to-end without
        # asking the user to remember it.
        if not title.lower().startswith("investigation:"):
            try:
                _diff = subprocess.run(
                    ["git", "diff", "--name-only", f"{base}...{branch}"],
                    cwd=WORKSPACE, capture_output=True, text=True, timeout=10,
                )
                if _diff.returncode == 0 and any(
                    line.startswith("investigations/") for line in _diff.stdout.splitlines()
                ):
                    title = f"investigation: {title}"
            except Exception:  # noqa: BLE001 — heuristic is best-effort
                pass

        if not shutil.which("gh"):
            try:
                from vivarium_dashboard.lib.report import _detect_github_repo
            except ImportError:
                _detect_github_repo = lambda *a: None
            repo = _detect_github_repo(WORKSPACE)
            manual = f"https://github.com/{repo}/compare/{base}...{branch}?expand=1" if repo else None
            return self._json({
                "error": "gh CLI not installed. Open manually:",
                "manual_url": manual,
            }, 500)

        draft = bool(body.get("draft", True))
        cmd = ["gh", "pr", "create", "--base", base, "--head", branch,
               "--title", title, "--body", body_text]
        if draft:
            cmd.append("--draft")
        r = subprocess.run(cmd, cwd=WORKSPACE, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return self._json({"error": f"gh pr create failed: {(r.stderr or r.stdout)[:300]}"}, 500)
        pr_url = r.stdout.strip().splitlines()[-1] if r.stdout else ""
        m = re.search(r"/pull/(\d+)", pr_url)
        if m:
            state["pr_url"] = pr_url
            state["pr_number"] = int(m.group(1))
            save_state(state)
        return self._json({"ok": True, "pr_url": pr_url, "pr_number": state.get("pr_number")}, 200)

    def _post_suggest(self, body: dict):
        """Write a Claude-suggestion request file. Body: {kind, context_extras?}."""
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.suggest_requests import write_request, VALID_KINDS

        kind = (body.get("kind") or "").strip()
        if kind not in VALID_KINDS:
            return self._json({"error": f"invalid kind (must be one of {VALID_KINDS})"}, 400)

        # Build context: workspace name + description, workstream info, recent commits.
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        from vivarium_dashboard.lib.work_state import load_state
        state = load_state() or {}
        branch = state.get("active_branch")
        commits = []
        if branch:
            r = subprocess.run(
                ["git", "log", "--format=%h %s", f"main..{branch}"],
                cwd=WORKSPACE, capture_output=True, text=True,
            )
            if r.returncode == 0:
                commits = [line for line in (r.stdout or "").splitlines() if line.strip()]

        context = {
            "workspace_name": ws_data.get("name", ""),
            "workspace_description": ws_data.get("description", ""),
            "active_branch": branch,
            "commits": commits[:30],
            "extras": body.get("context_extras") or {},
        }

        req_id = write_request(WORKSPACE, kind, context)
        return self._json({
            "ok": True,
            "id": req_id,
            "skill_command": f"/pbg-suggest {req_id}",
            "instructions": (
                f"Open Claude Code in this workspace and run `/pbg-suggest {req_id}`. "
                f"The dashboard will pick up the response automatically."
            ),
        }, 200)

    def _get_suggest_poll(self):
        """GET /api/suggest-poll?id=<id> → returns {ready: bool, suggestion?, rationale?}."""
        from urllib.parse import urlparse, parse_qs
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.suggest_requests import read_response

        qs = parse_qs(urlparse(self.path).query)
        req_id = (qs.get("id") or [""])[0]
        if not req_id:
            return self._json({"error": "missing id"}, 400)
        resp = read_response(WORKSPACE, req_id)
        if not resp:
            return self._json({"ready": False}, 200)
        return self._json({
            "ready": True,
            "suggestion": resp.get("suggestion", ""),
            "rationale": resp.get("rationale", ""),
        }, 200)

    def _get_work_status(self):
        """GET /api/work-status — delegates to lib.git_status.build_work_status.

        Single-source shim: the payload (and the ``{active: False}`` short-circuit)
        live in the lib so the FastAPI seam and this stdlib handler stay identical.
        """
        return self._json(_git_status_lib.build_work_status(WORKSPACE), 200)

    def _get_branch_staleness(self):
        """Generic helper: how many commits is <branch> behind <base>?

        Query string: ?branch=<name>&base=<name>. Both optional —
        branch defaults to the workspace's current HEAD; base defaults
        to 'main'. Probes origin/<base> first (so the answer matches
        what a merge from upstream would have to fast-forward over),
        falls back to local <base>.

        Surfaces friction note 2026-05-27 #5: long-running investigation
        branches drift, and when framework migrations land on main, the
        eventual merge produces "trivial but tedious" conflicts. A skill
        or UI calls this endpoint to warn the user before the drift gets
        painful.
        """
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        branch = (qs.get("branch") or [None])[0]
        base = (qs.get("base") or ["main"])[0]

        # Single-source shim: query parsing + status-code mapping stay here;
        # the staleness computation lives in lib.git_status.build_branch_staleness.
        try:
            body = _git_status_lib.build_branch_staleness(WORKSPACE, branch, base)
        except _git_status_lib.NoBranchError as e:
            return self._json({"error": str(e)}, 400)
        return self._json(body, 200)

    def _post_work_end(self, body: dict):
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import load_state, clear_state
        state = load_state()
        if not state.get("active_branch"):
            return self._json({"error": "no active workstream"}, 409)
        if _dirty_workspace().strip():
            return self._json({"error": "uncommitted changes — commit or stash before ending"}, 409)
        base = state.get("base", "main")
        subprocess.run(["git", "checkout", base], cwd=WORKSPACE, check=True, capture_output=True)
        clear_state()
        return self._json({"ok": True}, 200)

    def _get_dirty_status(self):
        """Return the filtered porcelain list of uncommitted files.

        Single-source shim over lib.git_status.build_dirty_status; the 500
        status-code mapping on a ``git status`` failure stays here.
        """
        try:
            body = _git_status_lib.build_dirty_status(WORKSPACE)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            return self._json({"error": f"git status failed: {stderr[:200]}"}, 500)
        return self._json(body, 200)

    def _get_git_status(self):
        """GET /api/git-status — live sync state for the workspace's git.

        Returns:
            {
              upstream_repo: "owner/name" | null,
              branch: str | null,
              push_state: "pushed" | "ahead" | "no_origin" | "diverged" | "behind",
              ahead: int,
              behind: int,
              branch_url: str | null,
              repo_url: str | null,
              pr_number: int | null,
              pr_url: str | null,
              base: str,
              ahead_of_base: int,
              dirty_count: int,
              compare_url: str | null,
              pr_state: str | null,
            }

        Single-source shim over lib.git_status.build_git_status (always 200).
        """
        return self._json(_git_status_lib.build_git_status(WORKSPACE), 200)

    def _post_dirty_commit_all(self, body: dict):
        """Stage and commit all dirty files (minus reports/) under the active workstream."""
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import load_state_or_adopt_current
        state = load_state_or_adopt_current()
        branch = state.get("active_branch")
        if not branch:
            return self._json({"error": "no active workstream"}, 409)
        # Ensure we're on the active branch
        try:
            current = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=WORKSPACE, capture_output=True, text=True, check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            return self._json({"error": f"git rev-parse failed: {stderr[:200]}"}, 500)
        if current != branch:
            r = subprocess.run(["git", "checkout", branch], cwd=WORKSPACE, capture_output=True, text=True)
            if r.returncode != 0:
                return self._json({"error": f"could not check out '{branch}': {r.stderr[:200]}"}, 500)
        dirty = _dirty_workspace().strip()
        if not dirty:
            return self._json({"error": "working tree is already clean"}, 409)
        paths = [line[3:] for line in dirty.splitlines() if len(line) >= 4]
        message = _suggest_dirty_commit_message(paths)
        try:
            subprocess.run(["git", "add", "-A"], cwd=WORKSPACE, check=True, capture_output=True)
            subprocess.run(["git", "reset", "HEAD", "--", "reports/"], cwd=WORKSPACE, check=False, capture_output=True)
            subprocess.run([
                "git", "-c", "user.email=pbg-template@local",
                      "-c", "user.name=pbg-template",
                      "commit", "-m", message,
            ], cwd=WORKSPACE, check=True, capture_output=True)
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=WORKSPACE, capture_output=True, text=True, check=True,
            ).stdout.strip()
            return self._json({"commit_sha": sha[:7], "message": message, "paths": paths}, 200)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            return self._json({"error": f"git operation failed: {stderr[:300]}"}, 500)

    def _post_render(self, body: dict):
        """Re-render workspace dashboard."""
        try:
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.report import render_workspace_report
            render_workspace_report(WORKSPACE)
            return self._json({"ok": True}, 200)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def _post_study_report_single(self, body: dict):
        """POST /api/study-report-single — render a standalone HTML report
        for ONE study (the investigation's ``focus_study`` or an explicit
        ``study`` override).

        Body (accepts either):
            {"investigation": "<slug>"}   resolves focus_study from yaml
            {"study": "<slug>"}           explicit override (wins if both set)

        Response: ``{html_path, size_bytes, study, investigation?}``.

        Why this exists: the full investigation report walks the reviewer
        through every study in the DAG. The domain expert reviews one
        study at a time and approves it before unblocking the next — this
        endpoint emits ONLY the focus study's content, no overview /
        comparative / cross-study sections.
        """
        try:
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.single_study_report import (
                build_single_study_report_for_test,
            )
            # W24 — honor ?skeptic=1 in the URL as an alternative to the body
            # flag, so a "View as skeptic" link can request the reordered view.
            body = dict(body or {})
            try:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                if "skeptic" in q and "skeptic" not in body:
                    body["skeptic"] = q["skeptic"][0] not in ("0", "false", "")
            except Exception:
                pass
            resp, code = build_single_study_report_for_test(WORKSPACE, body)
            return self._json(resp, code)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)

    # ------------------------------------------------------------------
    # GET handlers
    # ------------------------------------------------------------------

    def _serve_branches(self):
        """Return list of stage/* branches with last-commit info.

        Single-source shim over lib.git_status.list_branches; the builder
        returns ``{"error": ...}`` on a top-level git failure, which this
        handler maps to HTTP 500 (matching the legacy behaviour).
        """
        body = _git_status_lib.list_branches(WORKSPACE)
        if "error" in body:
            return self._json(body, 500)
        return self._json(body, 200)

    def _serve_pending(self):
        """Return pending entries from unmerged stage/* branches.

        Single-source shim over lib.work_views.build_pending; WORKSPACE passes
        the ws_root so the builder is git-cwd-agnostic.
        """
        from vivarium_dashboard.lib.work_views import build_pending
        body, status = build_pending(WORKSPACE)
        return self._json(body, status)

    def _get_branch_diff(self):
        """Return a short diff summary for ?branch=<name>.

        Single-source shim over lib.git_status.build_branch_diff; an invalid /
        missing branch name maps to HTTP 400 (matching the legacy behaviour).
        """
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        branch = (qs.get("branch") or [""])[0]
        try:
            body = _git_status_lib.build_branch_diff(WORKSPACE, branch)
        except ValueError:
            return self._json({"error": "invalid branch name"}, 400)
        return self._json(body, 200)

    def _get_registry(self):
        """GET /api/registry — live introspection of build_core(); cached 30s.

        Query param: ?refresh=1 to bypass cache.
        Never returns 500 — always returns {processes, types} (with optional 'error').
        """
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        bypass = qs.get("refresh", ["0"])[0] == "1"
        try:
            data = _get_registry_data(bypass_cache=bypass)
        except Exception as e:
            data = {"error": str(e), "processes": [], "types": []}
        return self._json(data, 200)

    def _get_explorer_runs(self):
        """GET /api/explorer/runs — runs for the Data Explorer run-picker."""
        from vivarium_dashboard.lib import explorer_data
        try:
            return self._json({"runs": explorer_data.list_runs(WORKSPACE)}, 200)
        except Exception as e:  # never sink the page
            return self._json({"error": str(e), "runs": []}, 200)

    def _get_explorer_observables(self):
        """GET /api/explorer/observables?db=<path>&run=<id>"""
        import urllib.parse as _up
        from vivarium_dashboard.lib import explorer_data
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        db = q.get("db")
        if not db:
            return self._json({"error": "missing db", "categories": {}}, 200)
        try:
            return self._json(
                explorer_data.list_observables(db, q.get("run"), workspace=WORKSPACE), 200)
        except Exception as e:
            return self._json({"error": str(e), "categories": {}}, 200)

    def _get_explorer_series(self):
        """GET /api/explorer/series?db=<path>&paths=a,b#2&subsample=N&run=<id>"""
        import urllib.parse as _up
        from vivarium_dashboard.lib import explorer_data
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        db = q.get("db")
        if not db:
            return self._json({"error": "missing db", "time": [], "series": {}}, 200)
        specs = []
        for tok in (q.get("paths") or "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            if "#" in tok:
                p, _, i = tok.partition("#")
                specs.append((p, int(i) if i.isdigit() else None))
            else:
                specs.append((tok, None))
        try:
            sub = int(q.get("subsample", "400"))
        except ValueError:
            sub = 400
        try:
            return self._json(
                explorer_data.get_series(db, specs, sub, q.get("run"),
                                         workspace=WORKSPACE), 200)
        except Exception as e:
            return self._json({"error": str(e), "time": [], "series": {}}, 200)

    def _get_explorer_flux(self):
        """GET /api/explorer/flux?db=<path>&step=<int>&run=<id>"""
        import urllib.parse as _up
        from vivarium_dashboard.lib import explorer_data
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        db = q.get("db")
        if not db:
            return self._json({"error": "missing db", "fluxes": {}}, 200)
        try:
            step = int(q.get("step", "0"))
        except ValueError:
            step = 0
        try:
            _, id_map = explorer_data.load_flux_assets()
            return self._json(
                explorer_data.get_flux_auto(db, step, id_map, q.get("run"),
                                            workspace=WORKSPACE), 200)
        except Exception as e:
            return self._json({"error": str(e), "fluxes": {}}, 200)

    def _get_explorer_vector(self):
        """GET /api/explorer/vector?db=&run=&path=&step="""
        import urllib.parse as _up
        from vivarium_dashboard.lib import explorer_data
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        db = q.get("db"); path = q.get("path")
        step = 0
        if not db or not path:
            return self._json({"error": "missing db/path", "ids": [], "values": [], "step": 0, "time": None}, 200)
        try:
            step = int(q.get("step", "0"))
        except ValueError:
            step = 0
        try:
            return self._json(
                explorer_data.get_vector(db, path, step, q.get("run"), WORKSPACE), 200)
        except Exception as e:
            return self._json({"error": str(e), "ids": [], "values": [], "step": step, "time": None}, 200)

    def _get_explorer_protein_breakdown(self):
        """GET /api/explorer/protein-breakdown?db=&run=&path=&step= — protein mass
        grouped by functional category at one timepoint (count x MW by category)."""
        import urllib.parse as _up
        from vivarium_dashboard.lib import explorer_data
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        db = q.get("db"); path = q.get("path")
        if not db or not path:
            return self._json({"error": "missing db/path", "breakdown": {}, "step": 0, "time": None}, 200)
        try:
            step = int(q.get("step", "0"))
        except ValueError:
            step = 0
        try:
            return self._json(
                explorer_data.get_protein_breakdown(db, path, step, q.get("run"), WORKSPACE), 200)
        except Exception as e:
            return self._json({"error": str(e), "breakdown": {}, "step": step, "time": None}, 200)

    def _get_simulations(self):
        """GET /api/simulations — all persisted runs across the workspace.

        Returns ``{simulations: [...], current: <slug|None>}`` aggregated from
        ``.pbg/composite-runs.db`` and every ``studies/<name>/runs.db``, with
        Studies-association annotated from each ``study.yaml``'s ``runs[]``.
        Newest first. ``current`` is the investigation slug matching the
        workspace's current git branch, so the SimulationsDB UI can default to
        the loaded investigation.

        Each sim carries an ``emitter_type`` in {"SQLite","Parquet","XArray"}
        (capitalized; the canonical labels from
        :mod:`vivarium_dashboard.lib.runs_index`), derived from the index's
        lowercase ``emitter`` tag (sqlite/parquet/xarray) so the UI can render
        an emitter pill uniformly across SQLite/Parquet/XArray runs.
        """
        _ws_add_to_sys_path()
        try:
            from vivarium_dashboard.lib.simulations_index import list_simulations
            sims = list_simulations(WORKSPACE)
        except Exception as e:  # noqa: BLE001 — never blank-page the user
            return self._json({"error": f"simulations index failed: {e}"}, 500)
        # Map the index's lowercase emitter tag onto the canonical capitalized
        # emitter_type label the UI/pills key on (and runs_index.emitter_type_of
        # produces). db_path-based detection is the fallback for any row whose
        # source tag didn't resolve.
        from vivarium_dashboard.lib.runs_index import emitter_type_of
        _emitter_label = {"sqlite": "SQLite", "parquet": "Parquet", "xarray": "XArray",
                          "none": "—"}  # no step emitter (summary-only run)
        for s in sims:
            s["emitter_type"] = _emitter_label.get(
                _emitter_tag(s.get("emitter"))) or emitter_type_of(s.get("db_path"))
        sims = _append_remote_simulations(sims, WORKSPACE)
        return self._json(
            {"simulations": sims, "current": _current_branch_slug(WORKSPACE)}, 200)

    def _get_composite_runs(self):
        """GET /api/composite-runs?spec_id=X — list runs for one composite spec.

        Thin shim → ``lib.composite_run_views.build_composite_runs``.
        """
        from urllib.parse import urlparse, parse_qs
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.composite_run_views import build_composite_runs

        qs = parse_qs(urlparse(self.path).query)
        spec_id = (qs.get("spec_id") or [""])[0]
        body, status = build_composite_runs(WORKSPACE, spec_id or None)
        return self._json(body, status)

    def _get_composite_run(self):
        """GET /api/composite-run/<run_id> — return trajectory list.

        Thin shim → ``lib.composite_run_views.build_composite_run``.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.composite_run_views import build_composite_run

        path_only = self.path.split("?", 1)[0]
        rest = path_only[len("/api/composite-run/"):]
        # Guard: this handler matches the bare /api/composite-run/<id> form;
        # /state and /status are dispatched before this handler reaches here.
        if "/" in rest:
            return self._json({"error": "use /state subpath"}, 400)
        run_id = rest
        body, status = build_composite_run(WORKSPACE, run_id)
        return self._json(body, status)

    def _get_composite_run_state(self):
        """GET /api/composite-run/<run_id>/state?step=N — single state snapshot.

        Thin shim → ``lib.composite_run_views.build_composite_run_state``.
        """
        from urllib.parse import urlparse, parse_qs
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.composite_run_views import build_composite_run_state

        u = urlparse(self.path)
        path_only = u.path
        prefix = "/api/composite-run/"
        rest = path_only[len(prefix):]
        if not rest.endswith("/state"):
            return self._json({"error": "bad route"}, 400)
        run_id = rest[: -len("/state")]
        qs = parse_qs(u.query)
        step_raw = (qs.get("step") or ["0"])[0]
        try:
            step = int(step_raw)
        except ValueError:
            return self._json({"error": "step must be int"}, 400)

        body, status = build_composite_run_state(WORKSPACE, run_id, step)
        return self._json(body, status)

    def _get_composite_run_status(self):
        """GET /api/composite-run/<run_id>/status — lightweight run status.

        Returns {status, progress_step, n_steps, heartbeat_at}. For terminal
        states it also returns an `error` excerpt (failed/orphaned, from the
        run log) or `viz_html` (completed, from the run's viz.json).

        Thin shim → ``lib.composite_run_views.build_composite_run_status``.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.composite_run_views import build_composite_run_status

        path_only = self.path.split("?", 1)[0]
        prefix = "/api/composite-run/"
        rest = path_only[len(prefix):]
        if not rest.endswith("/status"):
            return self._json({"error": "bad route"}, 400)
        run_id = rest[: -len("/status")]

        body, status = build_composite_run_status(WORKSPACE, run_id)
        return self._json(body, status)

    def _get_investigation_detail(self):
        """GET /api/investigation/<name> — full spec + viz file paths + runs summary."""
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError
        from vivarium_dashboard.lib import composite_runs as cr

        path_only = self.path.split("?", 1)[0]
        rest = path_only[len("/api/investigation/"):]
        if "/" in rest or not rest:
            return self._json({"error": "bad route"}, 400)
        name = rest

        inv_dir = _study_dir(name)
        spec_path = _study_spec_path(name)
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)

        # Auto-migrate legacy single-composite investigations on first open.
        try:
            from vivarium_dashboard.lib.investigation_migrate import needs_migration, migrate_investigation
            if needs_migration(spec_path):
                migrate_investigation(spec_path, workspace_root=WORKSPACE)
        except Exception:
            # Migration failure must not block the viewer; the dashboard
            # surfaces the issue via the normal spec-load error path.
            pass

        try:
            spec = load_spec(spec_path)
        except InvestigationSpecError as e:
            return self._json({"error": str(e), "name": name, "status": "invalid"}, 200)

        # Merge auto-discovered viz/*.html into spec.embed_visualizations so
        # the downloadable investigation report (walkthrough.js'
        # _buildInvestigationReportHtml) — which iterates spec.embed_visualizations
        # to inline iframes — picks up CLI-rendered Plotly charts without a
        # manual study.yaml edit. Mirror logic of _study_detail_spec.
        if isinstance(spec, dict):
            try:
                auto_embeds = _discover_viz_html_files(name)
            except Exception:
                auto_embeds = []
            if auto_embeds:
                existing_urls = {
                    (e or {}).get("url")
                    for e in (spec.get("embed_visualizations") or [])
                }
                merged_embeds = list(spec.get("embed_visualizations") or [])
                for e in auto_embeds:
                    if e.get("url") not in existing_urls:
                        merged_embeds.append(e)
                spec["embed_visualizations"] = merged_embeds
            # Also merge runs.db rows so spec.runs reflects all CLI-launched
            # runs (same logic as _study_detail_spec).
            try:
                db_runs = _read_runs_db_for_study(name)
            except Exception:
                db_runs = []
            if db_runs:
                existing_ids = {(r or {}).get("run_id") for r in (spec.get("runs") or [])}
                merged_runs = list(spec.get("runs") or [])
                for r in db_runs:
                    if r.get("run_id") not in existing_ids:
                        merged_runs.append(r)
                spec["runs"] = merged_runs

        viz_dir = inv_dir / "viz"
        viz_files = []
        if viz_dir.is_dir():
            for v in sorted(viz_dir.glob("*.html")):
                viz_files.append({"name": v.stem, "path": str(v.relative_to(WORKSPACE))})

        runs_summary = []
        db = inv_dir / "runs.db"
        if db.is_file():
            conn = cr.connect(db)
            try:
                rows = conn.execute(
                    "SELECT run_id, sim_name, label, params_json, status, n_steps "
                    "FROM runs_meta ORDER BY started_at DESC"
                ).fetchall()
                for r in rows:
                    import json as _j
                    try:
                        params = _j.loads(r["params_json"] or "{}")
                    except _j.JSONDecodeError:
                        params = {}
                    runs_summary.append({
                        "run_id": r["run_id"], "sim_name": r["sim_name"] or "",
                        "label": r["label"] or "", "params": params,
                        "status": r["status"], "n_steps": r["n_steps"] or 0,
                    })
            finally:
                conn.close()

        # Single-sourced reviewer-facing run/test/verdict summary for the
        # downloadable report's per-study clarity strip (see study_status).
        try:
            from pbg_superpowers import study_status as _ss
            if isinstance(spec, dict):
                spec["clarity_summary"] = _ss.study_clarity_summary(
                    spec, spec.get("runs") or [])
        except Exception:  # noqa: BLE001
            pass

        return self._json({
            "name": name,
            "spec": spec,
            "viz_files": viz_files,
            "runs_summary": runs_summary,
        }, 200)

    def _get_investigation_viz_html(self):
        """GET /api/investigation-viz-html?investigation=<inv>&run_id=<run_id>
        — list the persisted viz HTML files for one run.

        Returns ``{viz_files: [{name, html_path}]}`` where ``html_path`` is the
        workspace-relative path the static-file handler can serve. Symmetric
        with the inline ``viz_html`` payload returned from
        ``/api/investigation-run-one``; the run handler writes one HTML file
        per inlined ``Visualization`` step under
        ``investigations/<inv>/viz/<run_id>/<name>.html``.

        Thin delegating shim → ``lib.investigation_views.build_investigation_viz_html``.
        """
        from urllib.parse import urlparse, parse_qs

        qs = parse_qs(urlparse(self.path).query)
        inv = (qs.get("investigation") or [""])[0].strip()
        run_id = (qs.get("run_id") or [""])[0].strip()
        try:
            body = _inv_views.build_investigation_viz_html(WORKSPACE, inv, run_id)
            return self._json(body, 200)
        except _inv_views.InvViewError as exc:
            return self._json(exc.body, exc.status)

    def _get_investigation_composites(self):
        """GET /api/investigation-composites?investigation=<n>
        Returns: {composites: [{name, source, params}]}
        Reads the v3 ``baseline`` list; each entry is projected to
        {name, source (was composite), params}.

        Thin delegating shim → ``lib.investigation_views.build_investigation_composites``.
        """
        import urllib.parse
        qs = urllib.parse.urlparse(self.path).query
        name = urllib.parse.parse_qs(qs).get('investigation', [''])[0].strip()
        try:
            body = _inv_views.build_investigation_composites(WORKSPACE, name)
            return self._json(body, 200)
        except _inv_views.InvViewError as exc:
            return self._json(exc.body, exc.status)

    def _get_investigation_state_tree(self):
        """GET /api/investigation-state-tree?investigation=<n>&composite=<c>
        Returns: {nodes: [{path, kind, type?, default?, address?, config?}]}

        Thin shim — delegates to
        :func:`lib.investigation_views.build_investigation_state_tree`.
        """
        import urllib.parse
        _ws_add_to_sys_path()
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        inv = qs.get('investigation', '').strip()
        comp = qs.get('composite', '').strip()
        try:
            body = _inv_views.build_investigation_state_tree(WORKSPACE, inv, comp)
        except _inv_views.InvViewError as exc:
            return self._json(exc.body, exc.status)
        return self._json(body, 200)

    # --- /api/study-bigraph-paths: walk a saved composite state snapshot --
    #
    # Returns the legal store paths a user can pick when authoring observables
    # for a study. Reads the composite's serialized .pbg / .json state file
    # under <workspace>/models/, walks it, and emits a flat list of leaf
    # entries with type hints.
    _bigraph_path_cache = {}  # {(path, mtime, max_depth): [nodes]}

    # --- /api/iset-list, /api/iset/<name>: investigation-set endpoints ---
    #
    # An investigation-set (iset) is a named collection of studies with
    # explicit ordering + cross-study dependencies. UI surface: the
    # Investigations tab. Storage: investigations/<name>/investigation.yaml.
    # The legacy investigations/<name>/spec.yaml (per-study v1/v2 format) is
    # distinct and walked separately by _iter_study_dirs.

    def _get_iset_list(self):
        """GET /api/iset-list — return summaries of every investigation.

        Each item includes ``status`` (author-declared, from YAML) and
        ``effective_status`` (computed from the member studies). See
        :func:`compute_investigation_status` for the derivation rules.
        """
        out = _build_iset_summary_for_test(WORKSPACE)
        return self._json({"investigations": out}, 200)

    def _get_study_rigor(self):
        """GET /api/study-rigor?study=<slug> — evidence & rigor scorecard.

        Deterministic feedback (replication, negative controls, alternative
        hypotheses, claim discipline, falsifiability, engineered-vs-emergent)
        computed by pbg_superpowers.rigor from the study's declared fields.
        """
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        slug = (q.get("study") or q.get("investigation") or [None])[0]
        try:
            body = _rigor_views.build_study_rigor(WORKSPACE, slug)
        except _rigor_views.RigorViewError as e:
            return self._json(e.body, e.status)
        return self._json(body, 200)

    def _get_investigation_rigor(self):
        """GET /api/investigation-rigor?investigation=<slug> — rigor roll-up
        across the investigation's member studies + investigation-level
        dimensions (adversarial coverage, traceable methodology)."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        slug = (q.get("investigation") or [None])[0]
        try:
            body = _rigor_views.build_investigation_rigor(WORKSPACE, slug)
        except _rigor_views.RigorViewError as e:
            return self._json(e.body, e.status)
        return self._json(body, 200)

    def _get_investigation_registry(self):
        """GET /api/investigation-registry — Pass C cross-worktree view.

        Returns the current worktree's active Investigation plus every
        OTHER live dashboard's current Investigation, queried over HTTP
        from each peer's /api/iset-list and cached for ~5s.

        Shape::

            {
              "current": {
                "slug": "...",
                "title": "...",
                "worktree_path": "...",
                "url": "http://127.0.0.1:<port>",
                "effective_status": "..."
              },
              "running_others": [
                {
                  "slug": "...",
                  "title": "...",
                  "worktree_path": "...",
                  "url": "http://127.0.0.1:<port>",
                  "effective_status": "...",
                  "pid": <int>
                }, ...
              ]
            }
        """
        # The server doesn't know its own URL up front; derive from the
        # ~/.pbg/servers record (which we wrote on boot in cli.py). Fall
        # back to a best-effort URL constructed from this request's Host.
        this_url = ""
        try:
            from pbg_superpowers import workspace_catalog
            rec = workspace_catalog.find_running(WORKSPACE)
            if rec:
                this_url = rec.get("url") or ""
        except Exception:
            pass
        if not this_url:
            host = self.headers.get("Host") or "127.0.0.1"
            this_url = f"http://{host}"
        out = _build_investigation_registry_for_test(WORKSPACE, this_url)
        return self._json(out, 200)

    def _post_iset_create(self, body: dict):
        """POST /api/iset-create — scaffold a new investigation.yaml.

        Body: ``{name: str, overview?: str, parent_studies?: list[str]}``.
        Slug must match ``^[a-z0-9][a-z0-9-]*$``. Atomic write (tmp+rename).
        Returns the new investigation in the same shape as GET /api/iset/<name>.
        """
        resp, code = _post_iset_create_for_test(WORKSPACE, body)
        return self._json(resp, code)

    def _post_iset_clone(self, body: dict):
        """POST /api/iset-clone — clone an investigation into a fresh planning state.

        Body: ``{source, target, source_prefix?, target_prefix?}``.
        Delegates to the workspace's ``scripts/clone_investigation.py``; returns
        the new investigation in the same shape as GET /api/iset/<target>,
        with an extra ``clone_summary`` field describing the study remap.
        """
        resp, code = _post_iset_clone_for_test(WORKSPACE, body)
        return self._json(resp, code)

    def _get_generation(self):
        """GET /api/generation — the workspace's current coordinated generation.

        Single-source shim over lib.work_views.build_generation. Returns
        ``{generation: {generation_id, git_sha, param_set_hash, created_at,
        label, n_runs}}`` or ``{generation: null}`` when no generation is
        active. Best-effort: any error → null, never 500.
        """
        from vivarium_dashboard.lib.work_views import build_generation
        return self._json(build_generation(WORKSPACE), 200)

    def _get_github_repo(self):
        """GET /api/github-repo — the workspace's GitHub repo as ``owner/name``.

        Thin shim: delegates to ``lib.system_info.build_github_repo``.
        Best-effort: never 500s.
        """
        return self._json(_system_info_lib.build_github_repo(WORKSPACE), 200)

    def _get_references_bib(self):
        """GET /api/references-bib — parsed contents of references/papers.bib.

        Returns {entries: [{key, type, title, author, journal, year, doi, url, note, ...}]}.
        Each entry is enriched in place with cached fetch results from
        references/.cache.json: ``enriched_doi``, ``publisher_url``,
        ``oa_pdf_url``, ``oa_status``, ``enrichment_fetched_at``,
        ``enrichment_pending``.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.report import _parse_bib_entries
        from vivarium_dashboard.lib.references_fetch import load_cache, enrich_entries
        try:
            entries = _parse_bib_entries(WORKSPACE)
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        try:
            cache = load_cache(WORKSPACE)
            entries = enrich_entries(entries, cache)
        except Exception:
            # Cache failures must never break the references view.
            pass
        return self._json({"entries": entries}, 200)

        """GET /api/work-composite-diff — files changed on the active branch
        that look like model code (composites + processes + steps + library
        helpers). Powers a "Model changes" section in the PR body Suggest.

        Single-source shim over lib.work_views.build_work_composite_diff.
        Always HTTP 200 (errors in body). Capped at 500 entries.
        """
        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        return self._json(build_work_composite_diff(WORKSPACE), 200)

    def _get_study_charts(self):
        """GET /api/study-charts/<name> — inline-SVG charts for the study.

        Thin HTTP wrapper around :func:`_study_charts_payload` (the pure,
        unit-testable seam). See that function for the source semantics.
        """
        import urllib.parse
        path = urllib.parse.urlparse(self.path).path
        name = path[len("/api/study-charts/"):].strip("/")
        if not name:
            return self._json({"error": "missing study name"}, 400)
        try:
            payload = _study_charts_payload(WORKSPACE, name)
        except Exception as e:
            return self._json({"error": str(e), "study": name}, 500)
        return self._json(payload, 200)

    def _get_inputs(self):
        """GET /api/inputs — loaded investigation's inputs (top) + repo-wide
        global inputs + current investigation slug. Honors ?investigation=<slug>
        so the tab follows the SPA-loaded investigation, not just the git branch."""
        import urllib.parse as _up
        _q = _up.parse_qs(_up.urlparse(self.path).query)
        _slug = (_q.get("investigation") or [None])[0]
        return self._json(_inputs_payload(WORKSPACE, _slug), 200)

    def _get_data_sources(self):
        """GET /api/data-sources — repo-wide data-source bundle.

        Reads ``workspace.yaml dashboard.data_sources``, imports the declared
        ``provider`` (module:func) in-process, and returns
        ``{label, sources: [{key, path, category, kind, size_bytes}, ...]}``.
        Returns ``{sources: []}`` when no provider is configured. Cached ~30s.
        """
        return self._json(_enumerate_data_sources(), 200)

    def _get_data_source_file(self):
        """GET /api/data-source-file?key=... — serve one bundle file.

        Re-runs the provider enumeration and serves the bytes of the entry
        whose ``key`` matches. The path comes ONLY from the enumeration (never
        a client-supplied path), so there is no traversal surface. Text kinds
        (tsv/csv/json/txt/fasta/yaml/md) are served inline; anything else is
        offered as a download. 404 if the key is not in the enumeration.
        """
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        key = (q.get("key") or [None])[0]
        try:
            data, mime, inline, filename = _download_views.resolve_data_source_file(
                WORKSPACE, key
            )
        except _download_views.DownloadError as exc:
            return self._json(exc.body, exc.status)

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if not inline:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"',
            )
        self.end_headers()
        self.wfile.write(data)

    def _get_iset_report(self):
        """GET /api/iset/<slug>/report — serve the per-investigation report."""
        import urllib.parse as _up
        _path = _up.urlparse(self.path).path
        _slug = _path[len("/api/iset/"):].rsplit("/report", 1)[0].strip("/")
        _f = _iset_report_file(WORKSPACE, _slug)
        if _f is None:
            return self._json({"error": f"no report for investigation {_slug!r}"}, 404)
        return self._serve_file(_f, "text/html")

    def _get_iset_detail(self):
        """GET /api/iset/<name> — return one investigation + its resolved studies.

        Delegates to the pure builder ``_iset_detail_data`` so the export CLI
        (publish.py) and the live handler share identical logic.
        """
        import urllib.parse
        path = urllib.parse.urlparse(self.path).path
        name = path.split("/api/iset/", 1)[-1].strip("/")
        if not name:
            return self._json({"error": "investigation name required"}, 400)
        result = Handler._iset_detail_data(name)
        if result is None:
            return self._json({"error": f"no investigation.yaml for {name!r}"}, 404)
        return self._json(result, 200)

    def _get_investigation_notebook(self):
        """GET /api/investigation-notebook/<slug>[?format=py] — generate and
        download the investigation's runnable Jupyter notebook (.ipynb) or the
        matching Python script (.py).

        Deterministic export (no AI), identical to what publish.py ships
        statically; the coder-facing complement to the HTML report.
        """
        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)
        slug = parsed.path.split("/api/investigation-notebook/", 1)[-1].strip("/")
        fmt = (urllib.parse.parse_qs(parsed.query).get("format") or ["ipynb"])[0]
        try:
            data, mime, filename = _download_views.build_investigation_notebook(
                WORKSPACE, slug, fmt
            )
        except _download_views.DownloadError as exc:
            return self._json(exc.body, exc.status)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def _get_study_bigraph_paths(self):
        """GET /api/study-bigraph-paths?study=<slug>[&baseline=<name>][&max_depth=<n>]

        Returns: {composite, source_file, max_depth, node_count, nodes:[{path,kind,...}]}

        Thin shim — delegates to :func:`lib.study_viz_views.build_study_bigraph_paths`.
        """
        import urllib.parse
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        slug = qs.get("study", "").strip()
        baseline_name = qs.get("baseline", "").strip()
        try:
            max_depth = int(qs.get("max_depth", "8"))
        except ValueError:
            max_depth = 8
        body, status = _study_viz.build_study_bigraph_paths(
            WORKSPACE, slug, baseline_name=baseline_name, max_depth=max_depth,
        )
        return self._json(body, status)

    def _get_investigation_composite_doc(self):
        """GET /api/investigation-composite-doc?investigation=<n>&composite=<c>
        Returns: {state: <parsed composite YAML>}
        Used by the Composites tab's bigraph-loom iframe to fetch the
        composite document as JSON (the iframe can't parse YAML in-browser
        without bundling a parser).

        Thin delegating shim → ``lib.investigation_views.build_investigation_composite_doc``.
        """
        import urllib.parse
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        inv = qs.get('investigation', '').strip()
        comp = qs.get('composite', '').strip()
        try:
            body = _inv_views.build_investigation_composite_doc(WORKSPACE, inv, comp)
            return self._json(body, 200)
        except _inv_views.InvViewError as exc:
            return self._json(exc.body, exc.status)

    def _get_investigations(self):
        """GET /api/investigations — return summaries of all investigations.

        Delegates to the pure builder :func:`_investigations_data` so the same
        data is available for ``publish.build_bundle`` without HTTP plumbing.
        """
        return self._json(_investigations_data(WORKSPACE), 200)

    def _post_investigation_create(self, body: dict):
        """POST /api/investigation-create {name, source?} — scaffold a new investigation.

        ``source`` is an optional composite ref (e.g. ``pkg.composites.foo``) that seeds the
        investigation with a baseline composite.  If omitted an empty study is created.
        The legacy ``composite`` field is accepted but ignored when ``source`` is provided.
        """
        name = (body.get("name") or "").strip()
        source = (body.get("source") or "").strip()
        if not name:
            return self._json({"error": "name is required"}, 400)
        import re
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return self._json({"error": "name must match [a-zA-Z0-9_-]+"}, 400)

        inv_dir = workspace_paths().studies / name
        if inv_dir.exists() or (workspace_paths().investigations / name).exists():
            return self._json({"error": f"investigation '{name}' already exists"}, 409)

        # Resolve source composite if provided. YAML refs land in the
        # legacy v2-shape with a copied sidecar; @composite_generator refs
        # land in the v3 shape (no sidecar — just store the dotted ref in
        # `baseline:`), which sidesteps the "can't serialize live Process
        # instances" problem for v2ecoli-style composites.
        source_path = None
        is_generator = False
        baseline_name = None
        if source:
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.investigation_migrate import (
                _resolve_composite_source_or_generate,
            )
            try:
                source_path, is_generator, baseline_name = (
                    _resolve_composite_source_or_generate(source, WORKSPACE)
                )
            except (FileNotFoundError, ValueError) as e:
                return self._json({"error": f"source composite not found: {e}"}, 404)

        def action():
            import shutil as _shutil
            inv_dir.mkdir(parents=True, exist_ok=False)
            (inv_dir / "data").mkdir()
            (inv_dir / "data" / ".keep").write_text("")

            if is_generator and baseline_name:
                # v4-shape scaffold: dotted ref lives in `baseline:` (no
                # sidecar — sidesteps the "can't serialize live Process
                # instances" problem for @composite_generator refs). The 14-
                # section narrative spine is emitted as commented placeholders
                # so the user sees the target shape — the same shape the
                # dnaa-replication investigation evolved through use — without
                # having to read docs first.
                from vivarium_dashboard.lib.scaffold_yaml import (
                    v4_study_scaffold,
                )
                body_yaml = v4_study_scaffold(
                    name,
                    composite=source,
                    baseline_name=baseline_name,
                )
                (inv_dir / "study.yaml").write_text(body_yaml)
            elif source_path and baseline_name:
                # Legacy v2-shape spec: seed with a baseline composite entry.
                composites_dir = inv_dir / "composites"
                composites_dir.mkdir(parents=True, exist_ok=True)
                sidecar = composites_dir / f"{baseline_name}.yaml"
                _shutil.copy2(source_path, sidecar)
                spec = {
                    "name": name,
                    "description": "",
                    "composites": [
                        {
                            "name": baseline_name,
                            "source": source,
                            "document": f"./composites/{baseline_name}.yaml",
                        }
                    ],
                    "simulations": [
                        {
                            "name": "baseline",
                            "composite": baseline_name,
                            "kind": "single",
                            "overrides": {},
                            "steps": 10,
                        }
                    ],
                    "observables": [],
                    "visualizations": [],
                    "status": "planned",
                }
                (inv_dir / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
            else:
                # Blank study — no composite yet
                stub = (
                    f"name: {name}\n"
                    f"description: \"\"\n"
                    f"\n"
                    f"composites: []\n"
                    f"\n"
                    f"simulations: []\n"
                    f"\n"
                    f"observables: []\n"
                    f"\n"
                    f"visualizations: []\n"
                    f"\n"
                    f"status: planned\n"
                )
                (inv_dir / "spec.yaml").write_text(stub)

        commit_msg = f"feat(investigations): scaffold {name}"
        resp, code = _active_branch_action(commit_msg, action)
        if code == 200:
            resp.update({"ok": True, "name": name})
        return self._json(resp, code)

    def _post_investigation_delete(self, body: dict):
        """POST /api/investigation-delete {name} — remove investigation directory."""
        name = _study_name_from_body(body)
        if not name:
            return self._json({"error": "name is required"}, 400)
        inv_dir = _study_dir(name)
        if not inv_dir.is_dir():
            return self._json({"error": f"investigation '{name}' not found"}, 404)

        def action():
            resp_lib, code_lib = _scaffold_mut.delete_investigation(WORKSPACE, body)
            if code_lib != 200:
                raise RuntimeError(resp_lib.get("error", "delete_investigation failed"))

        commit_msg = f"feat(investigations): delete {name}"
        resp, code = _active_branch_action(commit_msg, action)
        if code == 200:
            resp.update({"ok": True, "name": name})
        return self._json(resp, code)

    def _post_investigation_run(self, body: dict):
        """POST /api/investigation-run {name} — run all simulations + render visualizations."""
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigations import (
            run_investigation, InvestigationSpecError,
        )
        from vivarium_dashboard.lib.composite_lookup import substitute_parameters, find_composite_path
        from vivarium_dashboard.lib import composite_runs as cr

        name = _study_name_from_body(body)
        if not name:
            return self._json({"error": "name is required"}, 400)

        # Resolve workspace package
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

        def run_one_composite(*, spec_id, overrides, steps, sim_name, run_id, db_file,
                              state_doc=None):
            """Run one composite via subprocess. Matches _post_composite_test_run shape.

            When ``state_doc`` is provided (multi-composite path), the pre-built
            composite document is used directly; the emitter step has already been
            injected by ``inject_emitter_step``.  The SQLiteEmitter is then wired
            in by replacing the emitter address/config so the SQLite run_id/db_file
            are set correctly.

            When ``state_doc`` is None (legacy single-composite path), the composite
            is resolved from the registry by spec_id as before.
            """
            if state_doc is not None:
                # Multi-composite: state_doc already has the emitter step injected.
                # Wire the SQLiteEmitter run_id + db_file into the emitter config.
                import copy
                state_doc = copy.deepcopy(state_doc)
                state = state_doc.get("state") or {}
                emitter = state.get("emitter") or {}
                if emitter.get("_type") == "step":
                    cfg = dict(emitter.get("config") or {})
                    cfg["run_id"] = run_id
                    cfg["db_file"] = db_file
                    emitter["config"] = cfg
                    emitter["address"] = "local:SQLiteEmitter"
                    state["emitter"] = emitter
                state_doc["state"] = state
            else:
                # Legacy path: resolve composite from registry by spec_id.
                path = find_composite_path(WORKSPACE, pkg, spec_id)
                if path is None:
                    return {"status": "failed", "error": f"composite not found: {spec_id}"}
                text = path.read_text(encoding="utf-8")
                spec = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
                state = substitute_parameters(spec.get("state") or {},
                                              spec.get("parameters") or {},
                                              overrides)
                state = cr.inject_sqlite_emitter(state, run_id=run_id, db_file=db_file)
                state_doc = {"state": state}

            py = sys.executable
            _state_to_run = state_doc.get("state") or {}
            script = textwrap.dedent(f"""
                import json, sys, traceback
                try:
                    from {pkg}.core import build_core
                    from process_bigraph import Composite
                    from process_bigraph.emitter import SQLiteEmitter
                    core = build_core()
                    core.register_link('SQLiteEmitter', SQLiteEmitter)
                    composite = Composite({{'state': __import__('json').loads({json.dumps(json.dumps(_state_to_run, default=_json_default))})}}, core=core)
                    composite.run({steps})
                    print('@@@OK@@@')
                except Exception as e:
                    print('@@@ERROR@@@')
                    print(traceback.format_exc())
            """)
            try:
                result = subprocess.run([py, "-c", script], cwd=WORKSPACE,
                                         capture_output=True, text=True, timeout=300)
            except subprocess.TimeoutExpired as exc:
                try:
                    if exc.process:
                        exc.process.kill()
                        exc.process.communicate(timeout=2)
                except Exception:
                    pass
                return {"status": "failed", "error": "timeout"}
            if "@@@ERROR@@@" in result.stdout:
                return {"status": "failed",
                         "error": result.stdout.split("@@@ERROR@@@", 1)[1].strip()[-500:]}
            if "@@@OK@@@" not in result.stdout:
                return {"status": "failed",
                         "error": "runner returned unexpected output"}
            return {"status": "completed"}

        # Build the visualization registry. We need the workspace's core for
        # the Visualization class lookup; import the workspace package and
        # build a fresh core here (in-process, no subprocess needed).
        sys.path.insert(0, str(WORKSPACE))
        try:
            core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
            core = core_module.build_core()
            registry = dict(core.link_registry)
        except Exception as e:
            return self._json({"error": f"failed to build core: {e}"}, 500)

        # Also register the default Visualization classes from pbg_superpowers
        try:
            from pbg_superpowers.visualizations import (
                TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap,
            )
            registry["TimeSeriesPlot"] = TimeSeriesPlot
            registry["ParamVsObservable"] = ParamVsObservable
            registry["Distribution"] = Distribution
            registry["PhaseSpace"] = PhaseSpace
            registry["Heatmap"] = Heatmap
        except ImportError:
            pass

        def build_and_run(viz_doc, registry_arg):
            """Production hook: build a Composite from viz_doc, run 1 step,
            return the output_store's html string.
            """
            from process_bigraph import Composite
            composite = Composite({'state': viz_doc}, core=core)
            composite.run(1)
            state = composite.state
            html = state.get('output_store')
            if isinstance(html, dict):
                html = html.get('value') or html.get('_value') or ''
            return html if isinstance(html, str) else ''

        summary_holder: list = []

        def action():
            try:
                summary = run_investigation(
                    WORKSPACE, name,
                    run_one_composite=run_one_composite,
                    core_registry=registry,
                    build_and_run=build_and_run,
                )
                summary_holder.append(summary)
            except InvestigationSpecError as e:
                summary_holder.append({"error": f"spec error: {e}"})
            except FileNotFoundError as e:
                summary_holder.append({"error": str(e)})

        commit_msg = f"run(investigations): {name}"
        resp, code = _active_branch_action(commit_msg, action)
        if summary_holder and "error" in summary_holder[0]:
            err = summary_holder[0]["error"]
            return self._json({"error": err}, 400 if "spec error" in err else 404)
        if code == 200 and summary_holder:
            return self._json(summary_holder[0], 200)
        if code == 409 and summary_holder and "error" not in summary_holder[0]:
            # No changes to commit (e.g., re-run with identical spec where viz
            # files happen to be byte-identical) — still return success.
            return self._json(summary_holder[0], 200)
        return self._json(resp, code)

    def _post_investigation_render_viz(self, body: dict):
        """POST /api/investigation-render-viz {name} — re-render visualizations
        against the investigation's existing emitter data. No simulation re-run.

        No commit wrapper — a plain no-commit render. The whole handler logic
        lives in ``lib.investigation_viz_mutations.render_viz``; this shim is a
        thin lib delegate (FastAPI calls the lib builder directly).
        """
        return self._json(*_inv_viz_mut.render_viz(WORKSPACE, body))

    def _post_investigation_add_viz(self, body: dict):
        """POST /api/investigation-add-viz {investigation, name, address, config}
        — append a visualization entry to spec.yaml."""
        _ws_add_to_sys_path()
        import yaml as _y
        import re as _re

        inv = (body.get("investigation") or "").strip()
        viz_name = (body.get("name") or "").strip()
        address = (body.get("address") or "").strip()
        viz_config = body.get("config") or {}

        if not inv or not viz_name or not address:
            return self._json({"error": "investigation, name, address required"}, 400)
        if not _re.match(r"^[a-zA-Z0-9_-]+$", viz_name):
            return self._json({"error": "viz name must match [a-zA-Z0-9_-]+"}, 400)

        spec_path = _study_spec_path(inv)
        if not spec_path.is_file():
            return self._json({"error": f"investigation '{inv}' not found"}, 404)

        def action():
            _inv_viz_mut._apply_add_viz(
                WORKSPACE,
                spec_path=spec_path,
                viz_name=viz_name,
                address=address,
                viz_config=viz_config,
            )

        commit_msg = f"feat(investigations/{inv}): add viz {viz_name} ({address})"
        resp, code = _active_branch_action(commit_msg, action)
        if code == 200:
            resp["ok"] = True
            resp["investigation"] = inv
            resp["viz_name"] = viz_name
        return self._json(resp, code)

    def _list_visualization_classes(self) -> list:
        """Shared lookup: returns the deduped list of v2 Visualization classes
        currently registered in this workspace, in the same shape as
        ``_get_visualization_classes`` returns over HTTP. Used by both that
        endpoint and the workspace-level Add-Viz validator.
        """
        _ws_add_to_sys_path()
        try:
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
            pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
            sys.path.insert(0, str(WORKSPACE))
            core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
            core = core_module.build_core()
            registry = dict(core.link_registry)
        except Exception:
            registry = {}
        try:
            from pbg_superpowers.visualizations import (
                TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap,
            )
            for cls in [TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap]:
                registry[cls.__name__] = cls
        except ImportError:
            pass

        try:
            from pbg_superpowers.visualization import Visualization as _VizBase
        except ImportError:
            _VizBase = None

        def _is_viz(cls):
            if cls is _VizBase:
                return False
            marker = getattr(cls, "is_visualization", None)
            if callable(marker):
                try:
                    if marker() is True:
                        return True
                except Exception:
                    pass
            if _VizBase is not None:
                try:
                    if isinstance(cls, type) and issubclass(cls, _VizBase):
                        return True
                except TypeError:
                    pass
            return False

        # Inject workspace-local viz classes (non-pip-installed) so the
        # catalog shows them alongside discovered pbg-* package classes.
        self._add_workspace_viz_classes(registry)

        per_cls: dict = {}
        for name, cls in registry.items():
            if not _is_viz(cls) or name == "Visualization":
                continue
            existing = per_cls.get(id(cls))
            if existing is None or len(name) < len(existing[0]):
                per_cls[id(cls)] = (name, cls)
        out = []
        for name, cls in sorted(per_cls.values(), key=lambda kv: kv[0]):
            try:
                doc = (cls.__doc__ or "").strip().split("\n", 1)[0] if cls.__doc__ else ""
            except Exception:
                doc = ""
            out.append({"address": f"local:{name}", "name": name, "doc": doc, "kind": "visualization"})

        # Append Analysis classes from v2ecoli (process-bigraph Steps).
        # Guarded import — dashboard is workspace-agnostic; if v2ecoli is not
        # installed the analysis section is simply absent.
        try:
            import v2ecoli.workflow.analyses  # noqa: F401  (import-time registration)
            from v2ecoli.workflow.analysis import ANALYSIS_REGISTRY, Analysis
            for _name, _cls in sorted(ANALYSIS_REGISTRY.items()):
                if isinstance(_cls, type) and issubclass(_cls, Analysis):
                    try:
                        _doc = (_cls.__doc__ or "").strip().split("\n")[0]
                    except Exception:
                        _doc = ""
                    out.append({
                        "address": f"local:{_cls.__module__}.{_cls.__qualname__}",
                        "name": _name,
                        "doc": _doc,
                        "kind": "analysis",
                    })
        except Exception:
            pass  # no analysis registry importable in this workspace — fine

        return out

    def _get_ui_config(self):
        """GET /api/ui-config — return UI feature flags from workspace.yaml.

        Thin shim: delegates to ``lib.system_info.build_ui_config``.
        """
        return self._json(_system_info_lib.build_ui_config(WORKSPACE), 200)

    def _get_visualization_classes(self):
        """GET /api/visualization-classes — list registered Visualization v2 classes.

        Detection: a class is a Visualization if it (a) declares the marker via
        ``is_visualization()`` returning True, or (b) is a subclass of
        ``pbg_superpowers.visualization.Visualization``. The v2 base class itself
        is filtered out.
        Returns: [{address, name, doc}, ...]
        """
        return self._json({"classes": self._list_visualization_classes()}, 200)

    def _get_saved_visualizations(self):
        """GET /api/saved-visualizations — list saved interactive visualizations.

        Returns the workspace's packed 3D scenes (parsimony packs under
        ``studies/*/viz/3d/*.pack.json``) plus PTools TSV exports, for the
        Analyses gallery. See ``_build_saved_visualizations`` for the shape.
        """
        return self._json(_build_saved_visualizations(WORKSPACE), 200)

    def _get_visualization_instances(self):
        """GET /api/visualization-instances — list class-backed configured viz
        instances from workspace.yaml.visualizations (entries with a ``class:`` key).

        Thin shim — delegates to :func:`lib.study_viz_views.build_visualization_instances`.
        """
        return self._json(_study_viz.build_visualization_instances(WORKSPACE), 200)

    # Synthetic demo states for the 5 built-in pbg-superpowers Visualization
    # classes. Each key is the class's short name; value is a state dict that
    # matches the class's declared inputs(). Used when previewing a viz without
    # real run data, or as a fallback when investigation data is incompatible.
    _BUILTIN_VIZ_DEMOS = {
        "TimeSeriesPlot": {
            # Three runs in a sweep — list-of-lists triggers the multi-run
            # branch in TimeSeriesPlot.update(), and Plotly auto-shows the
            # legend once there's more than one named trace.
            "observable": [
                [1.0, 1.4, 2.1, 3.0, 4.2, 5.7, 7.1, 8.0, 8.3, 8.4],
                [2.0, 2.6, 3.5, 4.6, 5.9, 7.3, 8.5, 9.1, 9.3, 9.3],
                [0.5, 0.7, 1.1, 1.7, 2.5, 3.5, 4.6, 5.5, 6.1, 6.4],
            ],
            "time": [
                [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
                [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
                [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
            ],
            "_run_labels": ["rate=1.0", "rate=2.0", "rate=0.5"],
        },
        "ParamVsObservable": {
            "sweep_param_values": [0.1, 0.5, 1.0, 2.0, 5.0],
            "reduced_observable": [3.0, 7.5, 12.0, 17.5, 21.0],
        },
        "Distribution": {
            "samples": [
                10.0, 10.3, 10.1, 10.6, 10.4, 10.2, 10.5, 10.9, 10.7, 10.4,
                10.8, 10.3, 10.5, 11.0, 10.6, 10.2, 10.4, 10.7, 10.5, 10.8,
            ],
        },
        "PhaseSpace": {
            "x": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0],
            "y": [0.0, 0.8, 1.5, 1.8, 1.5, 0.8, 0.0, -0.8, -1.5, -0.8],
        },
        "Heatmap": {
            "x_params": [0.1, 0.5, 1.0, 2.0, 5.0],
            "y_params": [10.0, 20.0, 30.0],
            "z_values": [
                [1.0, 2.0, 3.0, 4.0, 5.0],
                [2.0, 4.0, 6.0, 8.0, 10.0],
                [3.0, 6.0, 9.0, 12.0, 15.0],
            ],
        },
    }

    def _build_workspace_core(self):
        """Build the workspace's process-bigraph core and return (core, registry_dict).
        On failure, returns (None, {})."""
        _ws_add_to_sys_path()
        try:
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
            pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
            sys.path.insert(0, str(WORKSPACE))
            core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
            core = core_module.build_core()
            return core, dict(core.link_registry)
        except Exception:
            return None, {}

    def _add_workspace_viz_classes(self, registry: dict) -> dict:
        """Walk <workspace_pkg>.visualizations.* and inject local Visualization
        subclasses into ``registry`` (so non-pip-installed workspace classes
        are reachable). Returns the mutated registry."""
        try:
            from pbg_superpowers.visualization import Visualization as _VizBase
        except ImportError:
            return registry
        try:
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
            pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
            import pkgutil, importlib
            viz_pkg = importlib.import_module(f"{pkg}.visualizations")
            for _, modname, _ in pkgutil.iter_modules(viz_pkg.__path__):
                try:
                    mod = importlib.import_module(f"{pkg}.visualizations.{modname}")
                    for attr_val in vars(mod).values():
                        if not isinstance(attr_val, type):
                            continue
                        if attr_val is _VizBase:
                            continue
                        if issubclass(attr_val, _VizBase):
                            registry[attr_val.__name__] = attr_val
                except Exception:
                    continue
        except Exception:
            pass
        return registry

    def _resolve_viz_class(self, address: str):
        """Resolve a 'local:<Name>' address (or bare class name) to the class
        object. Accepts both short names (e.g. ``TimeSeriesPlot``) and the
        fully-qualified module path that ``bigraph_schema.discover_packages``
        emits. Returns (class_obj, short_name) or (None, None) if not found.
        """
        class_key = address.split(":", 1)[1] if ":" in address else address
        core, registry = self._build_workspace_core()
        try:
            from pbg_superpowers.visualizations import (
                TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap,
            )
            for cls in [TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap]:
                registry[cls.__name__] = cls
        except ImportError:
            pass
        self._add_workspace_viz_classes(registry)

        short = class_key.rsplit(".", 1)[-1]
        for key in (class_key, short):
            cls = registry.get(key)
            if cls is not None:
                return cls, short
        return None, None

    def _demo_state_for(self, cls, class_key: str) -> dict:
        """Return a synthetic state dict for previewing a class.

        Priority: cls.demo() classmethod (user-provided) → built-in demo map →
        empty dict.
        """
        if hasattr(cls, "demo") and callable(getattr(cls, "demo")):
            try:
                state = cls.demo()
                if isinstance(state, dict):
                    return state
            except Exception:
                pass
        return dict(self._BUILTIN_VIZ_DEMOS.get(class_key, {}))

    def _post_visualization_preview(self, body: dict):
        """POST /api/visualization-preview — render a viz against demo data or
        an existing investigation's emitter outputs.

        Body:
            address: 'local:<Class>' (required) — the Visualization class to render
            config:  {} — config dict (used for both demo and investigation paths)
            source:  'demo' | 'investigation:<name>' (default 'demo')

        Returns:
            {ok, html, source_used, notes}
        """
        address = (body.get("address") or "").strip()
        if not address:
            return self._json({"error": "address is required"}, 400)
        config = body.get("config") or {}
        source = (body.get("source") or "demo").strip()

        cls, class_key = self._resolve_viz_class(address)
        if cls is None:
            return self._json({"error": f"class not registered: {address}"}, 404)

        notes = []
        # Try investigation source first if requested.
        if source.startswith("investigation:"):
            inv_name = source.split(":", 1)[1].strip()
            inv_dir = _study_dir(inv_name)
            runs_db = inv_dir / "runs.db"
            if not runs_db.is_file():
                notes.append(f"investigation '{inv_name}' has no runs.db; falling back to demo")
            else:
                try:
                    from vivarium_dashboard.lib.investigations import (
                        gather_emitter_outputs, build_viz_composite,
                    )
                    gathered = gather_emitter_outputs(runs_db)
                    viz_spec = {
                        "name": "preview", "address": address,
                        "config": dict(config),
                    }
                    registry = {class_key: cls}
                    doc = build_viz_composite(viz_spec, gathered, registry)
                    inst = cls.__new__(cls)
                    inst.config = config or {}
                    html = inst.update(dict(doc.get("inputs_store") or {})).get("html", "")
                    if html:
                        return self._json({
                            "ok": True, "html": html,
                            "source_used": f"investigation:{inv_name}",
                            "notes": "; ".join(notes),
                        }, 200)
                    notes.append("investigation render produced empty html; falling back to demo")
                except Exception as e:
                    notes.append(f"investigation render failed ({type(e).__name__}: {e}); falling back to demo")

        # Demo path (default or fallback).
        try:
            state = self._demo_state_for(cls, class_key)

            # Detect streaming-style viz (all inputs are scalar types). For
            # these, feed N synthetic timesteps so the accumulator builds up a
            # meaningful trajectory. The 5 default v2 classes use list[float]
            # inputs and render in a single call; wrapper classes like
            # ReaDDyPlots/BioreactorPlots use scalar inputs and accumulate.
            scalar_types = {"float", "integer", "string", "boolean"}
            # Probe inputs without full init (bare instance is enough for inputs()).
            probe = cls.__new__(cls)
            try:
                probe.config = config or {}
            except Exception:
                pass
            declared = {}
            try:
                declared = probe.inputs() or {}
            except Exception:
                pass
            is_streaming = (
                bool(declared)
                and all(t in scalar_types for t in declared.values())
                and not state
            )

            # Construct the real instance. Streaming viz typically need their
            # __init__ to run (to set up accumulator buffers), so try a proper
            # constructor with a fresh core; fall back to object.__new__ if
            # the class's signature doesn't accept (config, core).
            inst = None
            if is_streaming:
                core, _ = self._build_workspace_core()
                if core is None:
                    try:
                        from bigraph_schema import allocate_core
                        core = allocate_core()
                    except Exception:
                        core = None
                for ctor_args in (
                    {"config": config or {}, "core": core},
                    {"config": config or {}},
                ):
                    try:
                        inst = cls(**ctor_args)
                        break
                    except Exception:
                        continue
            if inst is None:
                inst = cls.__new__(cls)
                try:
                    inst.config = config or {}
                except Exception:
                    pass

            if is_streaming:
                # Synthesize 12 timesteps with smoothly-varying scalar values
                # so the accumulator has enough data to render a trajectory.
                import math
                html = ""
                for step in range(12):
                    synth = {}
                    for port, port_type in declared.items():
                        if port_type == "float":
                            if port in ("time", "t"):
                                synth[port] = float(step) * 0.5
                            else:
                                # Smooth wave; offset per-port via hash to avoid collinear demos.
                                phase = (hash(port) & 0xff) / 40.0
                                synth[port] = 1.0 + 0.5 * math.sin(step * 0.6 + phase) + step * 0.1
                        elif port_type == "integer":
                            synth[port] = int(50 + step * 7)
                        elif port_type == "boolean":
                            synth[port] = step % 2 == 0
                        else:
                            synth[port] = f"step-{step}"
                    result = inst.update(synth) or {}
                    html = result.get("html", "") or html
            else:
                html = inst.update(state).get("html", "")

            if not html:
                html = (
                    f'<div style="padding:20px;font-family:system-ui">'
                    f'<p><strong>{class_key}</strong>: no demo state available.</p>'
                    f'<p style="color:#666">Add a <code>demo()</code> classmethod to '
                    f'the viz class, or register an instance in workspace.yaml and '
                    f'use the Preview button on the instance row to render against '
                    f'real emitter data.</p></div>'
                )
            return self._json({
                "ok": True, "html": html, "source_used": "demo",
                "notes": "; ".join(notes),
            }, 200)
        except Exception as e:
            return self._json({
                "ok": False,
                "html": f'<p style="color:#991b1b">demo render failed: {type(e).__name__}: {e}</p>',
                "source_used": "demo",
                "notes": "; ".join(notes),
            }, 200)

    def _post_visualization_preview_instance(self, body: dict):
        """POST /api/visualization-preview-instance — preview a registered
        workspace.yaml instance by name. Looks up the instance's class + config
        and delegates to _post_visualization_preview.

        Body: {name, source?}
        """
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "name is required"}, 400)
        try:
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        except Exception:
            ws_data = {}
        entry = next(
            (v for v in (ws_data.get("visualizations") or [])
             if isinstance(v, dict) and v.get("name") == name),
            None,
        )
        if not entry:
            return self._json({"error": f"visualization '{name}' not registered"}, 404)
        cls = (entry.get("class") or "").strip()
        if not cls:
            # Description-only entry — there's no class to render against demo
            # data. Show a friendly stub instead of erroring out, so the user
            # sees what's there and what to do next.
            desc = entry.get("description") or "(no description)"
            resp_path = workspace_paths().pbg / "viz-responses" / f"{name}.py"
            req_path = workspace_paths().pbg / "viz-requests" / f"{name}.md"
            if resp_path.is_file():
                status_block = (
                    '<p style="margin:8px 0;color:#1f7a3a">'
                    '<strong>Code generated</strong> at <code>.pbg/viz-responses/'
                    + name + '.py</code>. '
                    'It hasn\'t been added to the project yet — use the '
                    '<strong>Add to project</strong> button on this row to stage it.'
                    '</p>'
                )
            elif req_path.is_file():
                status_block = (
                    '<p style="margin:8px 0;color:#b45309">'
                    '<strong>Request pending</strong>. A <code>/pbg-viz</code> request '
                    'has been written to <code>.pbg/viz-requests/' + name + '.md</code> '
                    'but no response file exists yet.<br>'
                    'In your Claude Code session, run <code>/pbg-viz ' + name + '</code> '
                    'and wait for it to write <code>.pbg/viz-responses/' + name + '.py</code>.'
                    '</p>'
                )
            else:
                status_block = (
                    '<p style="margin:8px 0;color:#555">'
                    'This is a <strong>description-only</strong> visualization — '
                    'no class is configured and no code has been generated yet. '
                    'To make it renderable:'
                    '<ol style="margin:6px 0 0 18px">'
                    '<li>Click <strong>Create</strong> on this row to write a '
                    '<code>/pbg-viz</code> request.</li>'
                    '<li>In your Claude Code session, run <code>/pbg-viz '
                    + name + '</code>.</li>'
                    '<li>When the skill writes <code>.pbg/viz-responses/'
                    + name + '.py</code>, click <strong>Add to project</strong>, '
                    'then <strong>Commit</strong>.</li>'
                    '<li>Or — easier — re-register this entry with a '
                    '<strong>Class</strong> picked from the catalog and a '
                    'Config dict; that path doesn\'t need code generation.</li>'
                    '</ol>'
                    '</p>'
                )
            stub_html = (
                '<div style="font-family:system-ui,sans-serif;padding:8px;color:#222">'
                '<h3 style="margin:0 0 8px">' + name + '</h3>'
                '<p style="margin:0 0 8px;color:#444"><em>' + _html.escape(desc) + '</em></p>'
                + status_block +
                '</div>'
            )
            return self._json({
                "ok": True, "html": stub_html, "source_used": "stub",
                "notes": "description-only entry; nothing to render against demo data",
            }, 200)
        return self._post_visualization_preview({
            "address": f"local:{cls}",
            "config": entry.get("config") or {},
            "source": (body.get("source") or "demo"),
        })

    def _post_investigation_run_delete(self, body: dict):
        """POST /api/investigation-run-delete {investigation, run_id} — delete one run from runs.db."""
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib import composite_runs as cr

        inv = _study_name_from_body(body)
        run_id = (body.get("run_id") or "").strip()
        if not inv or not run_id:
            return self._json({"error": "investigation and run_id required"}, 400)
        db = _study_dir(inv) / "runs.db"
        if not db.is_file():
            return self._json({"error": "runs.db not found"}, 404)
        conn = cr.connect(db)
        try:
            conn.execute("DELETE FROM history WHERE simulation_id=?", (run_id,))
            conn.execute("DELETE FROM runs_meta WHERE run_id=?", (run_id,))
            conn.commit()
        finally:
            conn.close()
        return self._json({"ok": True, "run_id": run_id}, 200)

    def _post_investigation_runs_clear(self, body: dict):
        """POST /api/investigation-runs-clear {investigation} — wipe runs.db."""
        inv = _study_name_from_body(body)
        if not inv:
            return self._json({"error": "investigation required"}, 400)
        db = _study_dir(inv) / "runs.db"
        if db.is_file():
            db.unlink()
        return self._json({"ok": True, "investigation": inv}, 200)

    def _post_investigation_run_one(self, body: dict):
        """POST /api/investigation-run-one {investigation, sim_name, overrides, steps}
        — run a single ad-hoc composite execution and append to the investigation's runs.db.

        Used by the 'Duplicate run' flow: user takes an existing run's params,
        tweaks them in a modal, submits as a one-off addition.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError
        from vivarium_dashboard.lib.composite_lookup import substitute_parameters, find_composite_path
        from vivarium_dashboard.lib import composite_runs as cr

        inv = _study_name_from_body(body)
        sim_name = (body.get("sim_name") or "").strip() or "ad-hoc"
        overrides = body.get("overrides") or {}
        steps = int(body.get("steps") or 10)
        if not inv:
            return self._json({"error": "investigation required"}, 400)

        spec_path = _study_spec_path(inv)
        if not spec_path.is_file():
            return self._json({"error": "spec.yaml not found"}, 404)
        try:
            spec = load_spec(spec_path)
        except InvestigationSpecError as e:
            return self._json({"error": str(e)}, 400)

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

        # Resolve which composite to run. v2 studies have `baseline` + `variants[]`
        # with each variant carrying a `document: ./composites/<name>.yaml`
        # sidecar that is the single source of truth (already merged + frozen
        # at create time). Legacy specs use a single top-level `composite` key
        # and resolve via the workspace registry.
        composite_name = None
        composite_doc = None  # raw {state, parameters, ...} dict OR a flat state dict
        inv_dir = _study_dir(inv)
        if "variants" in spec:
            # v2 study shape: prefer baseline; if absent, the first declared variant.
            variants = spec.get("variants") or []
            baseline_name = spec.get("baseline") or (variants[0].get("name") if variants else None)
            variant_entry = None
            for v in variants:
                if v.get("name") == baseline_name:
                    variant_entry = v
                    break
            if variant_entry is None:
                return self._json({"error": f"baseline variant not found: {baseline_name!r}"}, 404)
            composite_name = variant_entry.get("name") or baseline_name
            sidecar_rel = variant_entry.get("document") or f"./composites/{composite_name}.yaml"
            sidecar_path = (inv_dir / sidecar_rel).resolve()
            if not sidecar_path.is_file():
                return self._json({"error": f"composite sidecar not found: {sidecar_path}"}, 404)
            text = sidecar_path.read_text(encoding="utf-8")
            composite_doc = (json.loads(text) if sidecar_path.suffix.lower() == ".json"
                              else yaml.safe_load(text)) or {}
        elif spec.get("composite"):
            # Legacy single-composite shape: resolve via workspace registry.
            composite_name = spec["composite"]
            path = find_composite_path(WORKSPACE, pkg, composite_name)
            if path is None:
                return self._json({"error": f"composite not found: {composite_name}"}, 404)
            text = path.read_text(encoding="utf-8")
            composite_doc = (json.loads(text) if path.suffix.lower() == ".json"
                              else yaml.safe_load(text)) or {}
        else:
            return self._json(
                {"error": "spec has neither 'variants' (v2) nor 'composite' (legacy)"},
                400,
            )

        # Two sidecar shapes coexist in the wild:
        #   1. `{state: {...}, parameters: {...}}`  — file-spec composites
        #   2. `{...}`  — flat state dict from @composite_generator outputs
        # composite-test-run handles both (see line ~4775); mirror that here.
        if isinstance(composite_doc, dict) and "state" in composite_doc \
                and isinstance(composite_doc["state"], dict):
            state = substitute_parameters(composite_doc.get("state") or {},
                                           composite_doc.get("parameters") or {},
                                           overrides)
        else:
            # Flat state dict: no parameter substitution layer to apply,
            # overrides are best-effort applied at the top level only.
            state = dict(composite_doc) if isinstance(composite_doc, dict) else {}
            for k, v in (overrides or {}).items():
                if k in state:
                    state[k] = v
        db_file = str(_study_dir(inv) / "runs.db")
        run_id = cr.generate_run_id(composite_name, overrides)
        state = cr.inject_sqlite_emitter(state, run_id=run_id, db_file=db_file)

        # Ensure the DB exists + the runs_meta table has sim_name column
        import sqlite3 as _sql
        conn = cr.connect(db_file)
        try:
            conn.execute("ALTER TABLE runs_meta ADD COLUMN sim_name TEXT")
            conn.commit()
        except _sql.OperationalError:
            pass

        label = body.get("label") or f"ad-hoc {sim_name}"
        import time as _time
        cr.save_metadata(conn, spec_id=composite_name, run_id=run_id,
                          params=overrides, label=label, started_at=_time.time(),
                          n_steps=steps)
        conn.execute("UPDATE runs_meta SET sim_name=? WHERE run_id=?", (sim_name, run_id))
        conn.commit()
        conn.close()

        py = sys.executable
        script = textwrap.dedent(f"""
            import json, sys, traceback
            try:
                from {pkg}.core import build_core
                from process_bigraph import Composite
                from process_bigraph.emitter import SQLiteEmitter
                core = build_core()
                core.register_link('SQLiteEmitter', SQLiteEmitter)
                composite = Composite({{'state': __import__('json').loads({json.dumps(json.dumps(state, default=_json_default))})}}, core=core)
                composite.run({steps})
                # Gather rendered viz HTML, if pbg_superpowers is importable.
                viz_html = {{}}
                try:
                    from pbg_superpowers.visualization import render_results
                    rendered = render_results(composite)
                    for path_tuple, payload in rendered.items():
                        key = '.'.join(str(p) for p in path_tuple)
                        viz_html[key] = payload
                except Exception:
                    viz_html = {{}}
                print('@@@RESULTS@@@')
                print(json.dumps({{'viz_html': viz_html}}, default=str))
            except Exception:
                print('@@@ERROR@@@')
                print(traceback.format_exc())
        """)
        result = subprocess.run([py, "-c", script], cwd=WORKSPACE,
                                 capture_output=True, text=True, timeout=300)
        conn = cr.connect(db_file)
        try:
            if "@@@RESULTS@@@" in result.stdout:
                # Parse the viz_html block. Persist each viz's html to disk at
                # investigations/<inv>/viz/<run_id>/<viz_path_safe>.html so the
                # dashboard's static-file handler can serve it.
                viz_html_resp = {}
                try:
                    payload = json.loads(
                        result.stdout.split("@@@RESULTS@@@", 1)[1].strip()
                    )
                    viz_html = payload.get("viz_html") or {}
                except (IndexError, json.JSONDecodeError):
                    viz_html = {}

                if viz_html:
                    viz_dir = inv_dir / "viz" / run_id
                    viz_dir.mkdir(parents=True, exist_ok=True)
                    for viz_key, viz_payload in viz_html.items():
                        # Sanitise key for filesystem use: replace any '.', '/', or
                        # other separators with '_'.
                        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", viz_key).strip("_") or "viz"
                        html_str = ""
                        if isinstance(viz_payload, dict):
                            html_str = viz_payload.get("html") or ""
                        elif isinstance(viz_payload, str):
                            html_str = viz_payload
                        out_path = viz_dir / f"{safe}.html"
                        try:
                            out_path.write_text(html_str if isinstance(html_str, str) else str(html_str))
                            rel_path = out_path.relative_to(WORKSPACE)
                            viz_html_resp[safe] = {
                                "html": html_str if isinstance(html_str, str) else "",
                                "path": str(rel_path),
                            }
                        except OSError:
                            # Best-effort persistence; still include the HTML inline.
                            viz_html_resp[safe] = {
                                "html": html_str if isinstance(html_str, str) else "",
                                "path": "",
                            }

                cr.complete_metadata(conn, run_id=run_id, n_steps=steps, status="completed")
                return self._json({"ok": True, "run_id": run_id,
                                   "investigation": inv, "sim_name": sim_name,
                                   "viz_html": viz_html_resp}, 200)
            else:
                cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
                err = result.stdout.split("@@@ERROR@@@", 1)[-1].strip()[-500:] \
                      if "@@@ERROR@@@" in result.stdout else "unknown error"
                return self._json({"ok": False, "run_id": run_id, "error": err}, 200)
        finally:
            conn.close()

    def _post_investigation_create_from_composite(self, body: dict):
        """POST /api/investigation-create-from-composite {composite_name}

        Clone a workspace-catalog composite into a fresh investigation. The
        catalog is the union of the workspace's own ``pbg_<slug>/composites/``
        and every installed ``pbg-*`` package's ``composites/`` directory
        (see :func:`vivarium_dashboard.lib.composite_lookup.discover_all_composites`).

        ``composite_name`` is the friendly identifier surfaced by the
        Composite Explorer — matched against the catalog record's ``name``
        field first (the value in the composite YAML's top-level ``name:``),
        and falling back to the dotted-id stem (the bit after
        ``...composites.``) so URLs and slugs work too.

        On success, creates ``investigations/<auto-name>/`` with a v2-shape
        ``spec.yaml`` (name/baseline/variants/comparisons/conclusions/question/
        hypothesis/status) and copies the resolved source YAML to
        ``./composites/<composite_name>.yaml``. Returns ``{name: <auto-name>}``.
        """
        composite_name = (body.get("composite_name") or "").strip()
        if not composite_name:
            return self._json({"error": "composite_name required"}, 400)

        _ws_add_to_sys_path()
        try:
            from vivarium_dashboard.lib.composite_lookup import discover_all_composites
            from vivarium_dashboard.lib.investigation_migrate import _resolve_composite_source
        except ImportError as e:
            return self._json({"error": f"composite lookup unavailable: {e}"}, 500)

        # Resolve composite_name → dotted source ref via the workspace catalog.
        try:
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
        except Exception as e:
            return self._json({"error": f"failed to read workspace.yaml: {e}"}, 500)
        pkg = ws_data.get("package_path") or (
            "pbg_" + (ws_data.get("name") or "").replace("-", "_")
        )
        try:
            catalog = discover_all_composites(WORKSPACE, pkg)
        except Exception as e:
            return self._json({"error": f"catalog scan failed: {e}"}, 500)

        # Match by YAML name first, then by id-stem (the bit after `.composites.`).
        match_rec = None
        for rec in catalog.values():
            if rec.get("name") == composite_name:
                match_rec = rec
                break
        if match_rec is None:
            for rec_id, rec in catalog.items():
                stem = rec_id.rsplit(".composites.", 1)[-1]
                if stem == composite_name:
                    match_rec = rec
                    break
        if match_rec is None:
            return self._json(
                {"error": f"composite {composite_name!r} not in workspace catalog"},
                404,
            )

        source_ref = match_rec["id"]  # e.g. pbg_testws.composites.foo
        is_generator = match_rec.get("kind") == "generator"
        generator_doc = None
        source_path = None
        if is_generator:
            # Generator-kind: build the doc now and write it as a frozen YAML
            # snapshot. The variant's `source` field still references the
            # generator id (provenance); from the study's POV the sidecar is
            # an ordinary spec.
            try:
                from pbg_superpowers.composite_generator import _REGISTRY, build_generator, discover_generators
            except ImportError as e:
                return self._json(
                    {"error": f"pbg-superpowers unavailable for generator resolution: {e}"},
                    500,
                )
            if not _REGISTRY:
                discover_generators()
            entry = _REGISTRY.get(source_ref)
            if entry is None:
                return self._json(
                    {"error": f"generator {source_ref!r} not in registry — was it imported?"},
                    404,
                )
            try:
                generator_doc = build_generator(entry)
            except Exception as e:  # noqa: BLE001
                return self._json(
                    {"error": f"generator build failed: {e}"}, 400,
                )
        else:
            try:
                source_path, _stem = _resolve_composite_source(source_ref, WORKSPACE)
            except (FileNotFoundError, ValueError) as e:
                # Catalog has it but source file is gone (or installed package outside
                # the workspace tree). Fall back to the catalog's recorded path.
                recorded = match_rec.get("_path")
                if recorded and Path(recorded).is_file():
                    source_path = Path(recorded)
                else:
                    return self._json({"error": str(e)}, 404)

        # Auto-name: study-<slug>-<6-char-hex>. Slugify: lowercase, replace
        # any non-[a-z0-9_-] with '-', collapse repeats.
        slug = re.sub(r"[^a-z0-9_-]+", "-", composite_name.lower()).strip("-") or "composite"
        slug = re.sub(r"-+", "-", slug)
        auto_name = f"study-{slug}-{uuid.uuid4().hex[:6]}"

        inv_dir = workspace_paths().studies / auto_name
        if inv_dir.exists() or (workspace_paths().investigations / auto_name).exists():
            # Collision is astronomically unlikely with 24 bits of entropy, but
            # if it happens (e.g. a test seeds uuid), surface it rather than
            # silently overwriting.
            return self._json(
                {"error": f"investigation {auto_name!r} already exists"}, 409
            )

        commit_msg = (
            f"feat(investigations/{auto_name}): create from composite "
            f"'{composite_name}'"
        )

        def do_action():
            _composite_mut._apply_create_from_composite(
                WORKSPACE,
                inv_dir=inv_dir,
                composite_name=composite_name,
                is_generator=is_generator,
                generator_doc=generator_doc,
                source_path=source_path,
                source_ref=source_ref,
                auto_name=auto_name,
            )

        try:
            resp, code = _commit_or_run(commit_msg, do_action)
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

        if code == 200:
            return self._json({"name": auto_name}, 200)
        return self._json(resp, code)

    def _post_investigation_composite_add(self, body: dict):
        """POST /api/investigation-composite-add {investigation, name, source}
        Clone a registered workspace composite into the study.
        """
        inv_name = (body.get("investigation") or "").strip()
        comp_name = (body.get("name") or "").strip()
        source = (body.get("source") or "").strip()
        if not (inv_name and comp_name and source):
            return self._json({"error": "investigation, name, source required"}, 400)

        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigation_migrate import (
            _resolve_composite_source_or_generate,
            materialize_generator_doc,
        )
        try:
            source_path, is_generator, _stem = (
                _resolve_composite_source_or_generate(source, WORKSPACE)
            )
        except (FileNotFoundError, ValueError) as e:
            return self._json({"error": str(e)}, 404)

        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)
        composites_dir = inv_dir / "composites"
        composites_dir.mkdir(parents=True, exist_ok=True)
        sidecar = composites_dir / f"{comp_name}.yaml"
        if sidecar.is_file():
            return self._json({"error": f"composite {comp_name!r} already exists"}, 409)

        # For generator refs we materialize the doc now so the YAML sidecar
        # write below has something concrete to dump. Composites whose
        # state contains non-serializable objects (e.g. live Process
        # instances) will surface a clear error here.
        if is_generator:
            try:
                generator_doc = materialize_generator_doc(source)
            except Exception as e:  # noqa: BLE001
                return self._json(
                    {"error": (
                        f"composite {source!r} can't be serialized as a "
                        f"YAML sidecar: {e}"
                    )},
                    400,
                )
        else:
            generator_doc = None

        commit_msg = f"feat(investigations/{inv_name}): add composite '{comp_name}'"

        def do_action():
            _composite_mut._apply_add_investigation_composite(
                WORKSPACE,
                sidecar=sidecar,
                source_path=source_path,
                generator_doc=generator_doc,
                spec_path=spec_path,
                comp_name=comp_name,
                source=source,
            )

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_composite_perturb(self, body: dict):
        """POST /api/investigation-composite-perturb {investigation, name, extends,
        description?, parameter_overrides?, process_overrides?}

        Derive a new composite from an existing one by applying overrides, and
        register it as a variant in the study's ``spec.yaml``.

        Writes v2 shape: a ``variants:`` list with the intervention recipe
        nested under ``intervention: {description, parameter_overrides?,
        process_overrides?}``. If a variant with ``name`` already exists, it is
        REPLACED in-place (the sidecar composite document and the variant
        entry are both overwritten) — this supports the Interventions tab's
        Save-edit flow without a separate endpoint.
        """
        inv_name = (body.get("investigation") or body.get("study") or "").strip()
        comp_name = (body.get("name") or "").strip()
        extends = (body.get("extends") or "").strip()
        if not (inv_name and comp_name and extends):
            return self._json({"error": "investigation, name, extends required"}, 400)

        _ws_add_to_sys_path()
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)

        parent = inv_dir / "composites" / f"{extends}.yaml"
        if not parent.is_file():
            return self._json({"error": f"parent composite {extends!r} not found"}, 404)

        composites_dir = inv_dir / "composites"
        derived = composites_dir / f"{comp_name}.yaml"
        # NB: do NOT 409 on existing — perturb of an existing variant means
        # "edit this intervention", which overwrites the sidecar in-place.

        from vivarium_dashboard.lib.composite_recipes import (
            apply_parameter_overrides, apply_process_overrides,
        )
        import copy
        parent_doc = yaml.safe_load(parent.read_text(encoding="utf-8")) or {}
        derived_doc = copy.deepcopy(parent_doc)
        try:
            if body.get('parameter_overrides'):
                apply_parameter_overrides(derived_doc, body['parameter_overrides'])
            if body.get('process_overrides'):
                apply_process_overrides(derived_doc, body['process_overrides'])
        except KeyError as e:
            return self._json({"error": f"override failed: {e}"}, 400)
        except Exception as e:
            return self._json({"error": f"override failed: {type(e).__name__}: {e}"}, 500)

        commit_msg = f"feat(investigations/{inv_name}): derive composite '{comp_name}' from '{extends}'"

        def do_action():
            _composite_mut._apply_perturb_investigation_composite(
                WORKSPACE,
                derived=derived,
                derived_doc=derived_doc,
                spec_path=spec_path,
                comp_name=comp_name,
                extends=extends,
                body=body,
            )

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_composite_promote_to_catalog(self, body: dict):
        """POST /api/composite-promote-to-catalog
        Body: {investigation, variant, target_name?, description?}

        Promote an investigation variant's sidecar composite into the
        workspace-level composite catalog as a new
        ``<pkg>/composites/<target_name>.composite.yaml`` file. The catalog is
        YAML-based (see :mod:`vivarium_dashboard.lib.composite_lookup`), so the promoted
        entry is a YAML file — **not** a Python module.

        Steps:
          1. Read sidecar at ``investigations/<inv>/composites/<variant>.yaml``.
          2. Write it as ``<pkg>/composites/<target_name>.composite.yaml`` with
             the document's ``name`` set to ``target_name`` and, if provided,
             ``description`` set.
          3. Mark the variant entry as ``promoted: true`` in spec.yaml.

        Refuses with 409 if the catalog already contains
        ``<target_name>.composite.yaml`` — promotion is non-destructive.
        Returns ``{"name": "<target_name>", "path": "<relative path>"}``.
        """
        inv_name = (body.get("investigation") or "").strip()
        variant_name = (body.get("variant") or "").strip()
        target_name = (body.get("target_name") or variant_name).strip()
        description = body.get("description")
        if not (inv_name and variant_name):
            return self._json(
                {"error": "investigation, variant required"}, 400,
            )

        # Resolve workspace package path using the same pattern as other
        # handlers (e.g. _post_investigation_create_from_composite).
        try:
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
        except Exception as e:
            return self._json({"error": f"failed to read workspace.yaml: {e}"}, 500)
        pkg = ws_data.get("package_path") or (
            "pbg_" + (ws_data.get("name") or "").replace("-", "_")
        )
        catalog_dir = WORKSPACE / pkg / "composites"

        # Source paths
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": f"investigation {inv_name!r} not found"}, 404)
        sidecar = inv_dir / "composites" / f"{variant_name}.yaml"
        if not sidecar.is_file():
            return self._json(
                {"error": f"variant {variant_name!r} sidecar not found"}, 404,
            )

        # Refuse if catalog already has this target
        target_path = catalog_dir / f"{target_name}.composite.yaml"
        if target_path.exists():
            return self._json(
                {"error": f"catalog entry {target_name!r} already exists"}, 409,
            )

        rel_path = str(target_path.relative_to(WORKSPACE))
        commit_msg = (
            f"feat(catalog): promote {variant_name!r} from "
            f"investigations/{inv_name} as {target_name!r}"
        )

        def do_action():
            _composite_mut._apply_promote_composite_to_catalog(
                WORKSPACE,
                catalog_dir=catalog_dir,
                sidecar=sidecar,
                target_path=target_path,
                target_name=target_name,
                description=description,
                spec_path=spec_path,
                variant_name=variant_name,
            )

        try:
            resp, code = _commit_or_run(commit_msg, do_action)
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

        if code == 200:
            resp = dict(resp)
            resp["name"] = target_name
            resp["path"] = rel_path
            return self._json(resp, 200)
        return self._json(resp, code)

    def _post_investigation_composite_rebuild(self, body: dict):
        """POST /api/investigation-composite-rebuild {investigation, name}
        Re-render a derived composite from its recipe (re-applies overrides on
        the current parent document).
        """
        inv_name = (body.get("investigation") or "").strip()
        comp_name = (body.get("name") or "").strip()
        if not (inv_name and comp_name):
            return self._json({"error": "investigation, name required"}, 400)

        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        entry = next((c for c in (spec.get('composites') or [])
                      if c.get('name') == comp_name), None)
        if entry is None:
            return self._json({"error": f"composite {comp_name!r} not found"}, 404)
        extends = entry.get('extends')
        if not extends:
            return self._json({"error": f"composite {comp_name!r} is not derived (no extends)"}, 400)
        parent_path = inv_dir / "composites" / f"{extends}.yaml"
        if not parent_path.is_file():
            return self._json({"error": f"parent {extends!r} document missing"}, 404)

        from vivarium_dashboard.lib.composite_recipes import (
            apply_parameter_overrides, apply_process_overrides,
        )
        import copy
        parent_doc = yaml.safe_load(parent_path.read_text(encoding="utf-8")) or {}
        derived_doc = copy.deepcopy(parent_doc)
        try:
            if entry.get('parameter_overrides'):
                apply_parameter_overrides(derived_doc, entry['parameter_overrides'])
            if entry.get('process_overrides'):
                apply_process_overrides(derived_doc, entry['process_overrides'])
        except KeyError as e:
            return self._json({"error": f"rebuild failed: {e}"}, 400)
        except Exception as e:
            return self._json({"error": f"rebuild failed: {type(e).__name__}: {e}"}, 500)

        commit_msg = f"chore(investigations/{inv_name}): rebuild composite '{comp_name}'"

        def do_action():
            _composite_mut._apply_rebuild_investigation_composite(
                WORKSPACE,
                inv_dir=inv_dir,
                derived_doc=derived_doc,
                comp_name=comp_name,
            )

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_set_observables(self, body: dict):
        """POST /api/investigation-set-observables {investigation, paths, emit_all}
        Rewrites spec.yaml.observables. The orchestrator builds the emitter
        step at run time.

        Validation (400/404) stays in this LIVE shim so the dirty-tree check
        never fires before a bad-body rejection; the inner mutation is
        delegated to lib.metadata_mutations and run under the active
        workstream via ``_commit_or_run`` (commit / 409-on-dirty / note),
        byte-identical to the legacy handler. The FastAPI route calls the lib
        builder directly (commit/workstream deferred to the flip batch).
        """
        inv_name = (body.get("investigation") or "").strip()
        paths = body.get("paths")
        if not inv_name:
            return self._json({"error": "investigation required"}, 400)
        if paths is None or not isinstance(paths, list):
            return self._json({"error": "paths must be a list of arrays"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)

        commit_msg = f"feat(investigations/{inv_name}): set observables"

        def do_action():
            # The lib builder catches its own errors and RETURNS (dict, code)
            # rather than raising; re-raise on a post-validation runtime failure
            # so _commit_or_run surfaces it as a 500 (matching the legacy inline
            # mutation, which raised) instead of swallowing it into a 200+note.
            _resp, _code = _meta_mut.set_investigation_observables(WORKSPACE, body)
            if _code != 200:
                raise RuntimeError(_resp.get("error") or "mutation failed")

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_set_conclusions(self, body: dict):
        """POST /api/investigation-set-conclusions {investigation, markdown}
        Writes spec.yaml.conclusions. Rejects bodies over 256KB.

        Validation stays in this LIVE shim; inner mutation delegated to
        lib.metadata_mutations and committed via ``_commit_or_run`` (see
        ``_post_investigation_set_observables`` for the full rationale).
        """
        inv_name = _study_name_from_body(body)
        markdown = body.get("markdown", "")
        if not inv_name:
            return self._json({"error": "investigation required"}, 400)
        if not isinstance(markdown, str):
            return self._json({"error": "markdown must be a string"}, 400)
        if len(markdown.encode("utf-8")) > 256 * 1024:
            return self._json({"error": "conclusions exceed 256KB limit"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)

        commit_msg = f"feat(investigations/{inv_name}): set conclusions"

        def do_action():
            _resp, _code = _meta_mut.set_investigation_conclusions(WORKSPACE, body)
            if _code != 200:
                raise RuntimeError(_resp.get("error") or "mutation failed")

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_set_overview(self, body: dict):
        """POST /api/investigation-set-overview {investigation, fields: {question?, hypothesis?, status?}}
        Selectively updates the three Overview metadata fields on spec.yaml.

        Validation stays in this LIVE shim; inner mutation delegated to
        lib.metadata_mutations and committed via ``_commit_or_run`` (see
        ``_post_investigation_set_observables`` for the full rationale).
        """
        inv_name = (body.get("investigation") or "").strip()
        fields = body.get("fields") or {}
        if not inv_name:
            return self._json({"error": "investigation required"}, 400)
        if not isinstance(fields, dict):
            return self._json({"error": "fields must be a mapping"}, 400)
        if "status" in fields and fields["status"] not in _VALID_OVERVIEW_STATUSES:
            return self._json(
                {"error": f"status must be one of {sorted(_VALID_OVERVIEW_STATUSES)}"},
                400,
            )
        for key in ("question", "hypothesis", "topic"):
            if key in fields and not isinstance(fields[key], str):
                return self._json({"error": f"{key} must be a string"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)

        commit_msg = f"feat(investigations/{inv_name}): set overview metadata"

        def do_action():
            _resp, _code = _meta_mut.set_investigation_overview(WORKSPACE, body)
            if _code != 200:
                raise RuntimeError(_resp.get("error") or "mutation failed")

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_set_status(self, body: dict):
        """POST /api/investigation-set-status — shim → lib.metadata_mutations.

        Legacy never wrapped this in ``_commit_or_run`` (it delegated to the
        pure ``_set_investigation_status`` helper and returned its result
        directly), so the shim stays a direct lib delegation.
        """
        return self._json(*_meta_mut.set_investigation_status(WORKSPACE, body))

    def _post_proposed_input_decision(self, body: dict):
        """POST /api/proposed-input-decision {investigation, item_id, decision}.

        Accept or decline an agent-proposed reference/mechanism. On accept,
        kind=reference items are promoted into ``inputs.references``. Persists
        the new ``status`` back to investigation.yaml. See
        ``_decide_proposed_input_for_test``.
        """
        response, code = _decide_proposed_input_for_test(
            WORKSPACE,
            body.get("investigation") or "",
            str(body.get("item_id") or ""),
            (body.get("decision") or "").strip().lower(),
        )
        return self._json(response, code)

    # ------------------------------------------------------------------
    # Study-specific POST handlers — shims → lib.metadata_mutations
    # ------------------------------------------------------------------

    def _post_study_set_objective(self, body: dict):
        """POST /api/study-set-objective — shim → lib.metadata_mutations."""
        return self._json(*_meta_mut.set_study_objective(WORKSPACE, body))

    def _post_study_narrative_set(self, body: dict):
        """POST /api/study-narrative-set — shim → lib.metadata_mutations."""
        return self._json(*_meta_mut.set_study_narrative(WORKSPACE, body))

    def _post_study_expert_input_set(self, body: dict):
        """POST /api/study-expert-input-set — shim → lib.metadata_mutations."""
        return self._json(*_meta_mut.set_study_expert_input(WORKSPACE, body))

    def _post_study_seed_followup(self, body: dict):
        """POST /api/study-seed-followup → seed a child study.

        Source forms (the four unified followup field families):

        - **Finding** ``{parent, finding_id}`` — seeds from a
          ``finding.next_action`` by delegating to the shared
          pbg-superpowers seed mechanism (standalone; no pre-existing
          ``followup_proposals[]`` row needed). Wins over the others.
        - Legacy ``{parent, followup_idx}`` — seeds from
          ``follow_up_studies[followup_idx]``.
        - Richer ``{parent, proposal_id}`` or ``{parent, proposal_idx}`` —
          seeds from ``discovery_implications.followup_study_proposals``,
          inheriting the proposal's title / study_type /
          target_mechanism_elements / required_inputs and a
          ``parent_studies`` edge back with ``relation: leads-to``.

        A proposal selector wins over ``followup_idx``. The new study comes
        up as ``phase: Design`` / ``status: planned`` and is immediately
        visible in the dashboard's Investigations tab.
        """
        response, code = _post_study_seed_followup_for_test(WORKSPACE, body)
        return self._json(response, code)


    def _post_feedback_apply_action(self, body: dict):
        """POST /api/feedback-apply-action {item_id} → apply a tracked action."""
        response, code = _post_feedback_apply_action_for_test(WORKSPACE, body)
        return self._json(response, code)

    @staticmethod
    def _feedback_apply_action_test(body: dict):
        """Test seam: apply a feedback action against ``body['workspace']``.

        Returns ``(json_bytes, status_code)`` so a test can assert on the
        serialized response without standing up the HTTP server.
        """
        ws_root = body.get("workspace") or WORKSPACE
        response, code = _post_feedback_apply_action_for_test(ws_root, body)
        return _json_body(response), code

    def _post_study_sync_runs(self, body: dict):
        """POST /api/study-sync-runs {study}"""
        response, code = _post_study_sync_runs_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_rename(self, body: dict):
        """POST /api/study-rename {study, new_name}"""
        response, code = _post_study_rename_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_create_from_run(self, body: dict):
        """POST /api/study-create-from-run {name, objective, description?, source_run_id}"""
        response, code = _post_study_create_from_run_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_run_baseline(self, body: dict):
        """POST /api/study-run-baseline {study, steps?}"""
        response, code = _post_study_run_baseline_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_run_all_baselines(self, body: dict):
        """POST /api/study-run-all-baselines {study, steps?}

        Runs every entry in the study's `baseline:` list sequentially. The
        UI uses this for multi-baseline Studies (architecture comparisons)
        where firing one button is friendlier than clicking N per-entry
        Run buttons in order.
        """
        response, code = _post_study_run_all_baselines_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_run_variant(self, body: dict):
        """POST /api/study-run-variant {study, variant, steps?}"""
        response, code = _post_study_run_variant_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_investigation_run_unblocked(self, body: dict):
        """POST /api/investigation-run-unblocked {investigation}

        Enumerates every study in the investigation, finds variants whose
        ``conditions.model_settings`` don't have any unset
        ``required-before-run`` gates, and runs them sequentially on a
        background thread. Returns a ``job_id`` immediately; the client
        polls ``/api/investigation-run-unblocked-status?job_id=...``.

        Each variant's run delegates to the existing
        ``_post_study_run_baseline_for_test`` / ``_post_study_run_variant_for_test``
        handlers — same composite resolution, same emit pipeline, same
        viz-rendering side effect.

        After the last variant lands, the worker also fires comparative
        visualisations declared under the investigation yaml's
        ``comparative_visualizations:`` block (if present).
        """
        import threading
        import yaml as _yaml
        from vivarium_dashboard.lib.run_jobs import (
            manager, enumerate_unblocked,
        )

        inv_slug = ((body or {}).get("investigation") or "").strip()
        if not inv_slug:
            return self._json({"error": "investigation is required"}, 400)
        inv_yaml = workspace_paths().investigations / inv_slug / "investigation.yaml"
        if not inv_yaml.is_file():
            return self._json({"error": f"investigation not found: {inv_slug}"}, 404)
        try:
            iset = _yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
        except _yaml.YAMLError as e:
            return self._json({"error": f"yaml parse failed: {e}"}, 500)

        # Optional studies filter: ``{"investigation": "...", "studies":
        # ["dnaa-05-itv2-comparison", ...]}`` runs only those member
        # studies. Default (no filter) is "all studies in the investigation".
        studies_filter_raw = (body or {}).get("studies")
        studies_filter: set[str] | None = None
        if studies_filter_raw:
            if isinstance(studies_filter_raw, str):
                studies_filter = {studies_filter_raw}
            elif isinstance(studies_filter_raw, list):
                studies_filter = {str(s) for s in studies_filter_raw if s}

        # Collect runnable items across every member study (or just the
        # requested subset).
        items: list[dict] = []
        skipped: list[dict] = []
        for member in (iset.get("studies") or []):
            member_name = member if isinstance(member, str) else member.get("study")
            if not member_name:
                continue
            if studies_filter and member_name not in studies_filter:
                continue
            spec_path = workspace_paths().studies / member_name / "study.yaml"
            if not spec_path.is_file():
                # legacy: investigations/<name>/spec.yaml
                spec_path = workspace_paths().investigations / member_name / "spec.yaml"
            if not spec_path.is_file():
                skipped.append({"study": member_name, "variant": "?",
                                "status": "skipped",
                                "error": "study.yaml not found"})
                continue
            try:
                spec = _yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            except _yaml.YAMLError as e:
                skipped.append({"study": member_name, "variant": "?",
                                "status": "skipped", "error": f"yaml: {e}"})
                continue
            runnable, blocked = enumerate_unblocked(spec)
            items.extend(runnable)
            items.extend(blocked)
        items.extend(skipped)

        if not any(it.get("status") == "queued" for it in items):
            # mem3dg-readdy friction #34: a bare "no unblocked variants"
            # error was unactionable. Compute a per-status breakdown
            # *and* surface the items[] in the response body so the UI
            # can render per-item reasons.
            from collections import Counter as _Counter
            status_counts = _Counter(it.get("status") or "?" for it in items)
            parts = []
            for label, key in (
                ("blocked",   "blocked"),
                ("skipped",   "skipped"),
                ("completed", "done"),
            ):
                if status_counts.get(key):
                    parts.append(f"{status_counts[key]} {label}")
            breakdown = ", ".join(parts) if parts else "no items enumerated"
            return self._json({
                "error": (
                    f"no variants to queue ({breakdown}). Each item's reason "
                    "is in `items[].error` — see the per-item panel."
                ),
                "items": items,
            }, 400)

        # Worker: walk through queued items in order, fire each via the
        # existing run-variant / run-baseline path.
        def _worker(job):
            for idx, item in enumerate(list(job.items)):
                if item.get("status") != "queued":
                    continue
                job.update_item(idx, status="running")
                study_slug = item["study"]
                variant_name = item["variant"]
                try:
                    if item["kind"] == "baseline":
                        resp, code = _post_study_run_baseline_for_test(
                            WORKSPACE, {"study": study_slug}
                        )
                    else:
                        resp, code = _post_study_run_variant_for_test(
                            WORKSPACE, {"study": study_slug, "variant": variant_name}
                        )
                    if code == 200:
                        job.update_item(idx, status="done",
                                        run_id=resp.get("run_id", ""))
                    else:
                        job.update_item(idx, status="failed",
                                        error=resp.get("error", f"HTTP {code}"))
                except BaseException as e:  # noqa: BLE001
                    job.update_item(idx, status="failed", error=str(e))
            # Optional: render investigation-level comparative visualisations.
            self._render_investigation_comparative_visualisations(
                inv_slug, iset, job,
            )

        job = manager.submit(inv_slug, items, _worker)
        return self._json({"job_id": job.job_id, "items": items}, 202)

    def _get_investigation_run_unblocked_status(self):
        """GET /api/investigation-run-unblocked-status?job_id=<id>"""
        import urllib.parse
        from vivarium_dashboard.lib.run_jobs import manager
        from vivarium_dashboard.lib import job_status_views as _job_status_views
        q = urllib.parse.parse_qs(self.path.split("?", 1)[-1] if "?" in self.path else "")
        job_id = (q.get("job_id") or [""])[0]
        return self._json(*_job_status_views.job_status(manager, job_id))

    def _render_investigation_comparative_visualisations(
        self, inv_slug: str, iset: dict, job
    ) -> None:
        return _comparative_runs.render_investigation_comparative_visualisations(
            WORKSPACE, inv_slug, iset, job)

    def _post_study_variant_add(self, body: dict):
        """POST /api/study-variant-add {study, name, description?,
        parameter_overrides?, process_overrides?}"""
        response, code = _post_study_variant_add_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_variant_delete(self, body: dict):
        """POST /api/study-variant-delete {study, variant}"""
        response, code = _post_study_variant_delete_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_variant_set_params(self, body: dict):
        response, code = _post_study_variant_set_params_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_baseline_add(self, body: dict):
        response, code = _post_study_baseline_add_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_baseline_remove(self, body: dict):
        response, code = _post_study_baseline_remove_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_intervention_add(self, body: dict):
        response, code = _post_study_intervention_add_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_intervention_update(self, body: dict):
        response, code = _post_study_intervention_update_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_intervention_delete(self, body: dict):
        response, code = _post_study_intervention_delete_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_run_delete(self, body: dict):
        """POST /api/study-run-delete {study, run_id}"""
        response, code = _post_study_run_delete_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_runs_clear(self, body: dict):
        """POST /api/study-runs-clear {study}"""
        response, code = _post_study_runs_clear_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_comparison_add(self, body: dict):
        """POST /api/study-comparison-add {study, run_ids, name?}"""
        response, code = _post_study_comparison_add_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_tests_run(self, body: dict):
        """POST /api/study-tests-run {study} — run pytest against
        studies/<study>/tests/. Returns {summary, tests, note?}.
        """
        from .lib.study_tests import run_study_tests, StudyTestsConcurrentError
        slug = (body or {}).get("study")
        if not slug:
            return self._json({"error": "missing 'study' in body"}, 400)
        spec_path = workspace_paths().studies / slug / "study.yaml"
        if not spec_path.exists():
            return self._json({"error": f"study not found: {slug}"}, 404)
        try:
            result = run_study_tests(WORKSPACE, slug)
        except StudyTestsConcurrentError as e:
            return self._json({"error": str(e)}, 409)
        return self._json({
            "summary": result.summary,
            "tests": result.tests,
            "note": result.note,
        }, 200)

    def _get_study_export(self):
        """GET /api/study-export?study=<name>"""
        from urllib.parse import urlparse, parse_qs
        qs = urlparse(self.path).query
        params = parse_qs(qs)
        name = (params.get("study", [""])[0] or "").strip()
        try:
            data, mime, filename = _download_views.build_study_export(WORKSPACE, name)
        except _download_views.DownloadError as exc:
            return self._json(exc.body, exc.status)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _get_ptools_launch(self, study: str):
        """GET /api/ptools-launch/<study>?run=<run_id>&analysis=<name>

        Discovers per-run ptools TSV files and returns a Pathway Tools Omics
        Viewer launch URL.  Requires ``ui.ptools_server_url`` in workspace.yaml.

        Thin shim — delegates to :func:`lib.study_viz_views.build_ptools_launch`,
        supplying ``public_base`` from (in priority order):
          1. ``ui.dashboard_public_base_url`` in workspace.yaml (read inside lib)
          2. The HTTP ``Host`` header sent by the browser (passed here)
        """
        from urllib.parse import urlparse, parse_qs
        qs = urlparse(self.path).query
        params = parse_qs(qs)
        run_id = (params.get("run", [""])[0] or "").strip() or None
        analysis = (params.get("analysis", [""])[0] or "").strip() or None

        host = self.headers.get("Host", "localhost")
        public_base = f"http://{host}"

        body, status = _study_viz.build_ptools_launch(
            WORKSPACE, study, run=run_id, analysis=analysis, public_base=public_base,
        )
        return self._json(body, status)

    def _send_html(self, body: str, code: int = 200):
        """Send an HTML response with the given body and status code."""
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        encoded = body.encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    @staticmethod
    def _build_api_study_response(slug: str):
        """Pure builder for GET /api/study/<slug>.

        Returns (json_bytes, http_status).  Pure (no socket I/O) so tests can
        call it without a live server.  The do_GET branch calls this and emits
        the bytes via self._send_json_bytes().

        Validates the slug with _SLUG_RE first so the live path and builder
        are identical — callers that skip do_GET (e.g. tests) still get the
        400 on traversal/invalid slugs.
        """
        if not _SLUG_RE.match(slug):
            return _json_body({"error": "invalid slug"}), 400
        # Defense-in-depth: never let a single bad/oversized study drop the
        # connection. An unhandled exception here propagates out of do_GET (this
        # route has no try/except wrapper) → the client sees a network-level
        # "Failed to fetch" rather than an HTTP status, which aborts report
        # generation. Convert any failure into a structured 500 so the SPA gets
        # a real response it can surface, and the publish run keeps going.
        try:
            spec = _study_detail_spec(slug)
        except Exception as exc:  # noqa: BLE001
            import traceback as _tb
            return _json_body({
                "error": f"failed to build study {slug!r}: {type(exc).__name__}: {exc}",
                "traceback": _tb.format_exc(),
            }), 500
        if spec is None:
            return _json_body({"error": f"study not found: {slug}"}), 404
        try:
            return _json_body(spec), 200
        except Exception as exc:  # noqa: BLE001
            import traceback as _tb
            return _json_body({
                "error": f"failed to serialize study {slug!r}: {type(exc).__name__}: {exc}",
                "traceback": _tb.format_exc(),
            }), 500

    @staticmethod
    def _build_api_config_response():
        """Pure builder for GET /api/config — returns the source-config object.

        Returns (json_bytes, http_status).  Default: local-server mode.
        """
        return _json_body({"mode": "local-server"}), 200

    @staticmethod
    def _observables_for_ref_test(ws_root, ref):
        """Test seam for GET /api/observables — calls the module worker with an
        explicit ws_root so unit tests don't need the WORKSPACE global patched.
        Returns (json_bytes, http_status)."""
        return _observables_for_ref(ws_root, ref)

    @staticmethod
    def _study_observable_check_test(ws_root, slug):
        """Test seam for GET /api/study-observable-check — calls the module
        worker with an explicit ws_root. Returns (json_bytes, http_status)."""
        return _study_observable_check(ws_root, slug)

    @staticmethod
    def _report_lint_test(ws_root):
        """Test seam for GET /api/report-lint — runs the deterministic linter
        over an explicit ws_root. Returns (json_bytes, http_status)."""
        return _report_lint(ws_root)

    @staticmethod
    def _linkage_index_test(ws_root, *, investigation=None, source=None, observable=None,
                            observable_registry=None, composite=None):
        """Test seam for GET /api/linkage-index — runs the deterministic linkage
        queries over an explicit ws_root. Returns (json_bytes, http_status)."""
        return _linkage_index(ws_root, investigation=investigation,
                              source=source, observable=observable,
                              observable_registry=observable_registry,
                              composite=composite)

    @staticmethod
    def _needs_attention_test(ws_root, *, investigation=None):
        """Test seam for GET /api/needs-attention — runs the deterministic
        needs-attention scan over an explicit ws_root. Returns
        (json_bytes, http_status)."""
        return _needs_attention(ws_root, investigation=investigation)

    @staticmethod
    def _framework_metrics_test(ws_root):
        """Test seam for GET /api/framework-metrics — aggregates framework-self
        metrics over an explicit ws_root. Returns (json_bytes, http_status)."""
        return _framework_metrics(ws_root)

    @staticmethod
    def _investigation_hypotheses_test(ws_root, name):
        """Test seam for GET /api/investigation-hypotheses — returns the
        investigation's competing hypotheses with computed support_log folded in
        (Wave 3b #6/#16). Returns (json_bytes, http_status)."""
        return _investigation_hypotheses(ws_root, name)

    @staticmethod
    def _iset_detail_data(name: str) -> "dict | None":
        """Pure builder for investigation (iset) detail — no socket I/O.

        Thin shim — delegates to ``lib.report_views.build_iset_detail``.
        Returns the dict that GET /api/iset/<name> sends, or ``None`` when
        the investigation.yaml does not exist.
        """
        from vivarium_dashboard.lib import report_views as _rv
        return _rv.build_iset_detail(WORKSPACE, name)

    @staticmethod
    def _build_api_workspace_response():
        """Pure builder for GET /api/workspace — returns workspace home data.

        Returns (json_bytes, http_status).  Mirrors _build_api_config_response.
        """
        return _json_body(_workspace_home_data(WORKSPACE)), 200

    def _get_study_detail_page(self):
        """GET /studies/<name> — render the Study Detail page."""
        # Strip query-string before slicing the slug — otherwise a URL like
        # /studies/<slug>?focus=tests trips the SLUG regex on the trailing
        # "?focus=...". Defense-in-depth alongside the do_GET dispatcher's
        # exact-segment-count check.
        _path_only = self.path.split("?", 1)[0]
        parts = _path_only.strip("/").split("/")
        if len(parts) < 2 or parts[0] != "studies":
            return self._send_html("<h1>Not found</h1>", code=404)
        name = parts[1]
        # Reject path-traversal attempts and anything that's not a valid slug.
        if not _SLUG_RE.match(name):
            return self._send_html("<h1>Not found</h1>", code=404)
        spec = _study_detail_spec(name)
        if spec is None:
            return self._send_html(
                f"<h1>Study not found</h1><p><code>{name}</code> does not exist.</p>",
                code=404,
            )
        body = _render_study_detail_html(name, spec)
        return self._send_html(body, code=200)

    def _delete_investigation_composite(self, body: dict):
        """DELETE /api/investigation-composite {investigation, name}
        Refuse if any runs, visualizations, or other composites reference this composite.
        """
        inv_name = (body.get("investigation") or "").strip()
        comp_name = (body.get("name") or "").strip()
        if not (inv_name and comp_name):
            return self._json({"error": "investigation, name required"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}

        # Dependents: runs[].composite, visualizations[].config.sources, composites[].extends
        dependents = []
        for r in (spec.get('runs') or []):
            if r.get('composite') == comp_name:
                dependents.append(f"run({r})")
        for v in (spec.get('visualizations') or []):
            sources = (v.get('config') or {}).get('sources') or []
            if comp_name in sources:
                dependents.append(f"visualization({v.get('name')})")
        for c in (spec.get('composites') or []):
            if c.get('extends') == comp_name:
                dependents.append(f"composite({c.get('name')})")
        if dependents:
            return self._json({
                "error": f"composite {comp_name!r} has dependents",
                "dependents": dependents,
            }, 409)

        commit_msg = f"chore(investigations/{inv_name}): remove composite '{comp_name}'"

        def do_action():
            doc_path = inv_dir / "composites" / f"{comp_name}.yaml"
            if doc_path.is_file():
                doc_path.unlink()
            spec['composites'] = [c for c in (spec.get('composites') or [])
                                   if c.get('name') != comp_name]
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_comparison_add(self, body: dict):
        """POST /api/investigation-comparison-add {investigation, name, description?, variants[], observables[]}
        Appends a comparison entry to spec.yaml.comparisons.
        """
        inv_name = (body.get("investigation") or body.get("study") or "").strip()
        cmp_name = (body.get("name") or "").strip()
        variants = body.get("variants") or []
        observables = body.get("observables") or []
        description = body.get("description", "")
        if not inv_name:
            return self._json({"error": "investigation required"}, 400)
        if not cmp_name:
            return self._json({"error": "name required"}, 400)
        if not isinstance(variants, list) or not variants:
            return self._json({"error": "variants must be a non-empty list"}, 400)
        if not isinstance(observables, list) or not observables:
            return self._json({"error": "observables must be a non-empty list"}, 400)
        if not isinstance(description, str):
            return self._json({"error": "description must be a string"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)

        commit_msg = f"feat(investigations/{inv_name}): add comparison {cmp_name}"

        def do_action():
            # Delegate to the pure lib builder; re-raise on non-200 so
            # _commit_or_run surfaces it as the correct error (409→ValueError
            # for duplicate; other failures→RuntimeError→500).
            _resp, _code = _compare_grp_mut.comparison_add(WORKSPACE, body)
            if _code == 409:
                raise ValueError(_resp.get("error") or "conflict")
            if _code != 200:
                raise RuntimeError(_resp.get("error") or "mutation failed")

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except ValueError as e:
            return self._json({"error": str(e)}, 409)
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_comparison_update(self, body: dict):
        """POST /api/investigation-comparison-update {investigation, name, fields_to_update}
        Replaces fields on a comparison entry. `name` is immutable; only
        description/variants/observables can be updated.
        """
        inv_name = (body.get("investigation") or "").strip()
        cmp_name = (body.get("name") or "").strip()
        fields = body.get("fields_to_update") or {}
        if not inv_name:
            return self._json({"error": "investigation required"}, 400)
        if not cmp_name:
            return self._json({"error": "name required"}, 400)
        if not isinstance(fields, dict):
            return self._json({"error": "fields_to_update must be a mapping"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)

        commit_msg = f"feat(investigations/{inv_name}): update comparison {cmp_name}"

        def do_action():
            # Delegate to the pure lib builder; re-raise on non-200 so
            # _commit_or_run surfaces it as the correct error (404→KeyError
            # for missing comparison; other failures→RuntimeError→500).
            _resp, _code = _compare_grp_mut.comparison_update(WORKSPACE, body)
            if _code == 404:
                raise KeyError(_resp.get("error") or "not found")
            if _code != 200:
                raise RuntimeError(_resp.get("error") or "mutation failed")

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except KeyError as e:
            return self._json({"error": str(e)}, 404)
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _delete_investigation_comparison(self, body: dict):
        """DELETE /api/investigation-comparison {investigation, name}
        Refuses with 409 if any visualization's config.comparison references
        this comparison name.
        """
        inv_name = (body.get("investigation") or "").strip()
        cmp_name = (body.get("name") or "").strip()
        if not inv_name:
            return self._json({"error": "investigation required"}, 400)
        if not cmp_name:
            return self._json({"error": "name required"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        dependents = [
            v.get("name", "<unnamed>")
            for v in (spec.get("visualizations") or [])
            if ((v.get("config") or {}).get("comparison") == cmp_name)
        ]
        if dependents:
            return self._json(
                {
                    "error": f"comparison {cmp_name!r} still referenced by visualization(s): {dependents}",
                    "dependents": dependents,
                },
                409,
            )

        commit_msg = f"feat(investigations/{inv_name}): delete comparison {cmp_name}"

        def do_action():
            data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            data["comparisons"] = [
                c for c in (data.get("comparisons") or []) if c.get("name") != cmp_name
            ]
            spec_path.write_text(yaml.safe_dump(data, sort_keys=False))

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    # ------------------------------------------------------------------
    # Investigation Groups (B7)
    # ------------------------------------------------------------------

    def _post_investigation_group_add(self, body: dict):
        """POST /api/investigation-group-add {investigation, name, description?, variants[]}
        Appends a group entry to spec.yaml.groups. 409 on duplicate name.
        400 if any variants[] entry is not in spec.variants[].
        """
        inv_name = (body.get("investigation") or "").strip()
        grp_name = (body.get("name") or "").strip()
        variants = body.get("variants") or []
        description = body.get("description", "")
        if not inv_name:
            return self._json({"error": "investigation required"}, 400)
        if not grp_name:
            return self._json({"error": "name required"}, 400)
        if not isinstance(variants, list) or not variants:
            return self._json({"error": "variants must be a non-empty list"}, 400)
        if not isinstance(description, str):
            return self._json({"error": "description must be a string"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)

        # Validate variant refs against declared variants up-front so the
        # error code is 400 (bad input) rather than 500 from do_action.
        spec_peek = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        declared = {v.get("name") for v in (spec_peek.get("variants") or [])
                    if isinstance(v, dict)}
        unknown = [v for v in variants if v not in declared]
        if unknown:
            return self._json(
                {"error": f"unknown variant(s): {unknown}; declared: {sorted(declared)}"},
                400,
            )

        commit_msg = f"feat(investigations/{inv_name}): add group {grp_name}"

        def do_action():
            # Delegate to the pure lib builder; re-raise on non-200 so
            # _commit_or_run surfaces it as the correct error (409→ValueError
            # for duplicate; other failures→RuntimeError→500).
            _resp, _code = _compare_grp_mut.group_add(WORKSPACE, body)
            if _code == 409:
                raise ValueError(_resp.get("error") or "conflict")
            if _code != 200:
                raise RuntimeError(_resp.get("error") or "mutation failed")

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except ValueError as e:
            return self._json({"error": str(e)}, 409)
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_group_update(self, body: dict):
        """POST /api/investigation-group-update {investigation, name, fields_to_update}
        Replaces description/variants on a group entry. name is immutable.
        """
        inv_name = (body.get("investigation") or "").strip()
        grp_name = (body.get("name") or "").strip()
        fields = body.get("fields_to_update") or {}
        if not inv_name:
            return self._json({"error": "investigation required"}, 400)
        if not grp_name:
            return self._json({"error": "name required"}, 400)
        if not isinstance(fields, dict):
            return self._json({"error": "fields_to_update must be a mapping"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)

        # If variants are being replaced, ensure each one references a declared
        # variant so we can return 400 rather than 500 on bad input.
        if "variants" in fields:
            new_vars = fields["variants"]
            if not isinstance(new_vars, list) or not new_vars:
                return self._json(
                    {"error": "variants must be a non-empty list"}, 400,
                )
            spec_peek = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            declared = {v.get("name") for v in (spec_peek.get("variants") or [])
                        if isinstance(v, dict)}
            unknown = [v for v in new_vars if v not in declared]
            if unknown:
                return self._json(
                    {"error": f"unknown variant(s): {unknown}; declared: {sorted(declared)}"},
                    400,
                )

        commit_msg = f"feat(investigations/{inv_name}): update group {grp_name}"

        def do_action():
            # Delegate to the pure lib builder; re-raise on non-200 so
            # _commit_or_run surfaces it as the correct error (404→KeyError
            # for missing group; other failures→RuntimeError→500).
            _resp, _code = _compare_grp_mut.group_update(WORKSPACE, body)
            if _code == 404:
                raise KeyError(_resp.get("error") or "not found")
            if _code != 200:
                raise RuntimeError(_resp.get("error") or "mutation failed")

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except KeyError as e:
            return self._json({"error": str(e)}, 404)
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _delete_investigation_group(self, body: dict):
        """DELETE /api/investigation-group {investigation, name}
        Removes a group from spec.yaml.groups. 404 if not found.
        """
        inv_name = (body.get("investigation") or "").strip()
        grp_name = (body.get("name") or "").strip()
        if not inv_name:
            return self._json({"error": "investigation required"}, 400)
        if not grp_name:
            return self._json({"error": "name required"}, 400)
        inv_dir = _study_dir(inv_name)
        spec_path = (inv_dir / "study.yaml") if (inv_dir / "study.yaml").is_file() else (inv_dir / "spec.yaml")
        if not spec_path.is_file():
            return self._json({"error": "investigation not found"}, 404)
        spec_peek = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        if not any(g.get("name") == grp_name
                   for g in (spec_peek.get("groups") or [])):
            return self._json({"error": f"group {grp_name!r} not found"}, 404)

        commit_msg = f"feat(investigations/{inv_name}): delete group {grp_name}"

        def do_action():
            data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            data["groups"] = [
                g for g in (data.get("groups") or []) if g.get("name") != grp_name
            ]
            spec_path.write_text(yaml.safe_dump(data, sort_keys=False))

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _get_composites(self):
        """GET /api/composites — composite specs from the workspace AND installed pbg-* packages.

        Discovery runs in a SUBPROCESS for a clean Python import: @composite_generator
        scanning is unreliable in the long-running server's stale sys.modules state
        (a fresh process finds the generators, the in-process call misses them — SP2b).
        Result is cached per workspace and invalidated on switch. Falls back to the
        in-process (spec-only) builder if the subprocess fails.
        """
        ws_key = str(WORKSPACE)
        cached = _COMPOSITES_LIST_CACHE.get(ws_key)
        if cached is not None:
            return self._json(cached, 200)
        payload = _composites_data_subprocess(WORKSPACE)
        if payload is None:
            payload = _composites_data(WORKSPACE)  # degraded: spec-only in-process
        else:
            _COMPOSITES_LIST_CACHE[ws_key] = payload
        return self._json(payload, 200)

    def _get_composite_state(self):
        """GET /api/composite-state?ref=<id-or-path>
        Returns: {state: <parsed composite YAML/JSON document>}
        Accepts either a dotted spec ID (pkg.composites.foo) or a workspace-relative file path.

        For ``@composite_generator``-decorated entries (kind=generator), calls
        ``build_generator`` with no overrides and returns the resulting document.
        """
        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)
        qs = dict(urllib.parse.parse_qsl(parsed.query))
        ref = qs.get("ref", "").strip()
        if not ref:
            # Static/snapshot form used by the loom's read-only ?stateUrl= mode:
            # /api/composite-state/<ref>.json (see walkthrough.js _renderComposite).
            # The handler historically only read ?ref=, so the static path form
            # returned "ref required" and the explorer degraded to an
            # error/unresolved stub. Parse the ref out of the path instead.
            _p = urllib.parse.unquote(parsed.path)
            _prefix = "/api/composite-state/"
            if _p.startswith(_prefix):
                ref = _p[len(_prefix):].strip()
                if ref.endswith(".json"):
                    ref = ref[: -len(".json")]
        if not ref:
            return self._json({"error": "ref required"}, 400)

        # All branch logic (TTL cache, subprocess generator build, static
        # fallback, spec/path resolution, 404) lives in the lib seam.
        _ws_add_to_sys_path()
        fresh = qs.get("fresh") in ("1", "true", "yes")
        body, status = _composite_state_views.build_composite_state(
            WORKSPACE, ref, fresh=fresh)
        return self._json(body, status)

    def _get_composite_resolve(self):
        """GET /api/composite-resolve — resolve a composite spec with param overrides, return state + SVG.

        Also resolves ``@composite_generator`` entries (kind=generator) by
        calling ``build_generator`` with overrides — the returned payload
        carries ``kind`` and ``module`` so the Composite Explorer page can
        surface the source-module info.
        """
        from urllib.parse import urlparse, parse_qs
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.composite_lookup import substitute_parameters, find_composite_path

        qs = parse_qs(urlparse(self.path).query)
        spec_id = (qs.get("id") or [""])[0]
        overrides_raw = (qs.get("overrides") or ["{}"])[0]
        try:
            overrides = json.loads(overrides_raw) if overrides_raw else {}
        except json.JSONDecodeError:
            return self._json({"error": "invalid overrides JSON"}, 400)

        if not spec_id:
            return self._json({"error": "missing id"}, 400)

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

        # Generator-kind branch: resolve via pbg-superpowers' live registry.
        try:
            from pbg_superpowers.composite_generator import _REGISTRY, build_generator, discover_generators
            if not _REGISTRY:
                discover_generators()
            entry = _REGISTRY.get(spec_id)
        except ImportError:
            entry = None
        if entry is not None:
            try:
                doc = build_generator(entry, overrides=overrides)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                return self._json({"error": f"generator build failed: {e}"}, 400)
            # build_generator may return {state: ..., schema: ...} or a bare
            # state dict. Normalize to a state dict for the iframe / JSON view.
            if isinstance(doc, dict) and "state" in doc and isinstance(doc["state"], dict):
                state = doc["state"]
            else:
                state = doc
            svg = _render_composite_svg(state, pkg)
            # Attach per-process docstrings so the explorer inspector's
            # Description section is populated on the composite-resolve path
            # too (not just /api/composite-state). Done after the SVG render
            # so the bigraph-viz subprocess sees clean state.
            from vivarium_dashboard.lib.process_docs import attach_process_docs
            attach_process_docs(state)
            return self._json({
                "id": spec_id,
                "name": entry.name,
                "description": entry.description,
                "parameters": entry.parameters,
                "state": state,
                "svg": svg,
                "kind": "generator",
                "module": entry.module,
                # GeneratorEntry has no default_n_steps field; guard like
                # _get_composites does rather than crashing the resolve.
                "default_n_steps": getattr(entry, "default_n_steps", None),
            }, 200)

        path = find_composite_path(WORKSPACE, pkg, spec_id)
        if path is None:
            # Honest degrade payload (see _get_composite_state): the explorer
            # renders "composite not found / not a registered composite" rather
            # than a bare error node, keying on ``unresolved``.
            return self._json({
                "error": (f"composite not found: {spec_id} — not a registered "
                          "composite (this study may not declare a real composite)"),
                "unresolved": True,
                "ref": spec_id,
            }, 404)

        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            spec = json.loads(text)
        else:
            spec = yaml.safe_load(text)
        state = substitute_parameters(spec.get("state") or {},
                                       spec.get("parameters") or {},
                                       overrides)

        # Render the wiring diagram via bigraph-viz subprocess.
        svg = _render_composite_svg(state, pkg)

        from vivarium_dashboard.lib.composite_lookup import _derive_module_from_spec_id
        from vivarium_dashboard.lib.process_docs import attach_process_docs
        attach_process_docs(state)  # per-process docstrings for the inspector
        return self._json({
            "id": spec_id,
            "name": spec.get("name", spec_id.rsplit(".composites.", 1)[-1]),
            "description": spec.get("description", ""),
            "parameters": spec.get("parameters") or {},
            "state": state,
            "svg": svg,
            "kind": "spec",
            "module": _derive_module_from_spec_id(spec_id),
            "default_n_steps": None,
        }, 200)

    def _post_composite_test_run(self, body: dict):
        """POST /api/composite-test-run — start a detached composite run.

        Writes a run-request file, inserts the runs_meta row, spawns the
        run-composite CLI detached, and returns 202 {run_id} immediately.
        The run itself executes in a separate process; the browser polls
        /api/composite-run/<id>/status to follow it.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib import composite_runs as cr
        from vivarium_dashboard.lib import run_registry
        from vivarium_dashboard.lib.composite_runs import auto_label

        spec_id = (body.get("id") or "").strip()
        overrides = body.get("overrides") or {}
        steps = int(body.get("steps") or 5)
        label = (body.get("label") or "").strip() or auto_label(overrides)
        emit_paths = body.get("emit_paths") or []
        if not isinstance(emit_paths, list):
            emit_paths = []
        if not spec_id:
            return self._json({"error": "missing id"}, 400)

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or (
            "pbg_" + ws_data.get("name", "").replace("-", "_"))
        db_file = str(workspace_paths().pbg / "composite-runs.db")

        if run_registry.count_running(db_file) >= run_registry.CONCURRENCY_CAP:
            return self._json(
                {"error": "too many runs in progress — wait for one to finish"},
                429)

        run_id = cr.generate_run_id(spec_id, overrides)
        run_dir = workspace_paths().pbg / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log_rel = str((run_dir / "run.log").relative_to(WORKSPACE))
        request_path = run_dir / "request.json"
        request_path.write_text(json.dumps({
            "run_id": run_id,
            "spec_id": spec_id,
            "pkg": pkg,
            "workspace": str(WORKSPACE),
            "overrides": overrides,
            "steps": steps,
            "emit_paths": emit_paths,
            "db_file": db_file,
            "log_path": log_rel,
        }))

        conn = cr.connect(db_file)
        try:
            cr.prune_runs(conn, spec_id=spec_id, keep=cr.PRUNE_KEEP)
            cr.save_metadata(conn, spec_id=spec_id, run_id=run_id,
                             params=overrides, label=label,
                             started_at=time.time(), n_steps=steps,
                             log_path=log_rel)
            try:
                pid = run_registry.spawn_detached(
                    request_path, workspace=WORKSPACE,
                    log_path=run_dir / "run.log")
            except Exception as e:  # noqa: BLE001 — surface the spawn failure
                cr.complete_metadata(conn, run_id=run_id, n_steps=0,
                                     status="failed")
                return self._json(
                    {"error": f"spawn failed: {e}", "run_id": run_id}, 500)
            cr.set_pid(conn, run_id=run_id, pid=pid)
        finally:
            conn.close()

        return self._json({"run_id": run_id, "status": "running"}, 202)

    # ------------------------------------------------------------------
    # Workspace manifest — one-call situational awareness for agents
    # ------------------------------------------------------------------

    def _get_workspace_manifest(self):
        """GET /api/workspace-manifest — one-call situational awareness for agents.

        Returns a structured JSON snapshot of the workspace state without
        making the agent stitch together 10 separate API calls. Aggregates:
        workspace identity + git state, composites (kind/module), studies
        (status/runs/variants), registry summary, dirty-tree count, and
        available pbg-* skills.
        """
        out = {
            "workspace":  self._manifest_workspace_section(),
            "composites": self._manifest_composites_section(),
            "studies":    self._manifest_studies_section(),
            "registry":   self._manifest_registry_section(),
            "health":     self._manifest_health_section(),
            "skills":     self._manifest_skills_section(),
        }
        return self._json(out, 200)

    def _manifest_workspace_section(self):
        """name, branch, commits ahead, package_path, has_origin."""
        _ws_add_to_sys_path()
        ws = {}
        ws_path = WORKSPACE / "workspace.yaml"
        try:
            from vivarium_dashboard.lib.workspace_yaml import load_workspace
            ws = load_workspace(ws_path)
        except Exception:
            # Fall back to raw yaml.safe_load when validation fails so the
            # manifest still surfaces basic identity for partially-formed
            # workspaces (test fixtures, migrations in progress, ...).
            try:
                ws = yaml.safe_load(ws_path.read_text(encoding="utf-8")) or {}
            except Exception:
                ws = {}
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=WORKSPACE, capture_output=True, text=True,
            ).stdout.strip() or "unknown"
        except Exception:
            branch = "unknown"
        try:
            has_origin = _has_origin_remote()
        except Exception:
            has_origin = False
        return {
            "name":         ws.get("name", ""),
            "description":  ws.get("description", ""),
            "package_path": ws.get("package_path", ""),
            "branch":       branch,
            "has_origin":   has_origin,
        }

    def _manifest_composites_section(self):
        """One-line summary per composite: id, name, kind, module, description, viz_step_count."""
        _ws_add_to_sys_path()
        all_comps = {}
        try:
            from vivarium_dashboard.lib.composite_lookup import discover_all_composites
            try:
                from vivarium_dashboard.lib.workspace_yaml import load_workspace
                ws = load_workspace(WORKSPACE / "workspace.yaml")
            except Exception:
                ws = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
            pkg = ws.get("package_path") or (
                "pbg_" + (ws.get("name") or "").replace("-", "_")
            )
            all_comps = discover_all_composites(WORKSPACE, pkg)
        except Exception:
            all_comps = {}
        # Per-workspace registry allow-list (same as /api/composites): hide
        # composites from non-allow-listed packages from the manifest summary.
        # The workspace's own package is always in the include set, so its
        # composites survive the package-root check. No-op when unset.
        ws_for_filter = None
        try:
            ws_for_filter = yaml.safe_load(
                (WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")
            ) or {}
        except Exception:
            ws_for_filter = None
        if ws_for_filter is not None:
            kept = _filter_composites(list(all_comps.values()), ws_for_filter)
            kept_ids = {c.get("id") for c in kept if isinstance(c, dict)}
            all_comps = {cid: c for cid, c in all_comps.items() if cid in kept_ids}
        out = []
        for cid, c in sorted(all_comps.items()):
            viz_count = _count_viz_steps_in_state(c.get("state") or {})
            out.append({
                "id":              cid,
                "name":            c.get("name", ""),
                "kind":            c.get("kind", "spec"),
                "module":          c.get("module", ""),
                "description":     (c.get("description") or "")[:200],
                "viz_step_count":  viz_count,
            })
        return out

    def _manifest_studies_section(self):
        """List of studies (v3) with name, topic, status, baseline_names, n_baseline,
        n_variants, n_groups, n_interventions, n_runs, n_comparisons, conclusions_len."""
        _ws_add_to_sys_path()
        try:
            from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError
        except Exception:
            return []
        out = []
        for d in _iter_study_dirs():
            spec_path = d / "study.yaml" if (d / "study.yaml").is_file() else d / "spec.yaml"
            if not spec_path.is_file():
                continue
            try:
                spec = load_spec(spec_path)
            except (InvestigationSpecError, Exception):
                continue
            n_runs = _count_runs_for_study(spec.get("name", d.name), spec)  # F2
            entry = {
                "name":             spec.get("name", d.name),
                "topic":            spec.get("topic", ""),
                "status":           spec.get("status", "draft"),
                "baseline_names":   [b.get("name", "")
                                     for b in (spec.get("baseline") or [])
                                     if isinstance(b, dict)],
                "n_baseline":       len(spec.get("baseline") or []),
                "n_variants":       len(spec.get("variants") or []),
                "n_groups":         len(spec.get("groups") or []),
                "n_interventions":  len(spec.get("interventions") or []),
                "n_runs":           n_runs,
                "n_comparisons":    len(spec.get("comparisons") or []),
                "conclusions_len":  len(spec.get("conclusions") or ""),
            }
            out.append(entry)
        return out

    def _manifest_registry_section(self):
        """Summary of registered kinds: count per (process|step|emitter|visualization|type)."""
        try:
            data = _get_registry_data()
            processes = data.get("processes") or []
            by_kind = {"process": 0, "step": 0, "emitter": 0,
                       "visualization": 0, "other": 0}
            for p in processes:
                k = p.get("kind") or "other"
                by_kind[k] = by_kind.get(k, 0) + 1
            return {
                "process_count":       by_kind["process"],
                "step_count":          by_kind["step"],
                "emitter_count":       by_kind["emitter"],
                "visualization_count": by_kind["visualization"],
                "type_count":          len(data.get("types") or []),
            }
        except Exception:
            return {"process_count": 0, "step_count": 0, "emitter_count": 0,
                    "visualization_count": 0, "type_count": 0}

    def _manifest_health_section(self):
        """dirty_count + dirty file list + venv presence + python version."""
        try:
            dirty = _dirty_workspace()
        except Exception:
            dirty = ""
        dirty_files = [line[3:] for line in dirty.splitlines() if len(line) >= 4]
        venv_py = WORKSPACE / ".venv" / "bin" / "python3"
        return {
            "dirty_count":      len(dirty_files),
            "dirty_files":      dirty_files[:10],  # cap
            "venv_present":     venv_py.is_file(),
            "python_version":   sys.version.split()[0],
        }

    def _manifest_skills_section(self):
        """List installed pbg-* skills the agent can invoke. Reads ~/.claude/skills/."""
        skills_dir = Path.home() / ".claude" / "skills"
        if not skills_dir.is_dir():
            return []
        out = []
        for d in sorted(skills_dir.iterdir()):
            if not d.is_dir() or not d.name.startswith("pbg-"):
                continue
            skill_md = d / "SKILL.md"
            description = ""
            if skill_md.is_file():
                try:
                    text = skill_md.read_text(encoding="utf-8")
                except Exception:
                    text = ""
                m = re.search(r"^description:\s*(.+?)$", text, re.MULTILINE)
                if m:
                    description = m.group(1).strip()
            out.append({"name": d.name, "description": description})
        return out

    # ------------------------------------------------------------------
    # Open-window — let agents demonstrate work visually via the browser
    # ------------------------------------------------------------------

    def _post_open_window(self, body: dict):
        """POST /api/open-window {route} — open a dashboard URL in the user's browser.

        Lets agents demonstrate visually what they've just done (e.g. open
        the Composite Explorer for a composite they just created/ran). The
        ``route`` is appended to the workspace dashboard's base URL read
        from ``.pbg/server/server-info``.
        """
        route = (body.get("route") or "/").strip()
        if not route.startswith("/"):
            route = "/" + route
        info_file = workspace_paths().pbg / "server" / "server-info"
        if not info_file.is_file():
            return self._json(
                {"error": "server-info file not found - is the dashboard running?"},
                503,
            )
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
        except Exception as e:
            return self._json({"error": f"server-info parse failed: {e}"}, 500)
        url = (info.get("url") or "").rstrip("/") + route
        import platform
        plat = platform.system().lower()
        if plat == "darwin":
            cmd = ["open", url]
        elif plat.startswith("linux"):
            cmd = ["xdg-open", url]
        elif plat == "windows":
            cmd = ["cmd", "/c", "start", url]
        else:
            return self._json({"error": f"unsupported platform: {plat}"}, 501)
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
        except Exception as e:
            return self._json({"error": f"open failed: {e}"}, 500)
        return self._json({"ok": True, "url": url}, 200)

    def _get_system_deps_check(self):
        """GET /api/system-deps-check?name=<module> — check whether a catalog
        module's ``system_dependencies`` are satisfied in the workspace venv.

        Returns: ``{name, platform, ok, checks: [{name, description, ok,
        reason, install: {manager, commands, notes}|null, notes}]}``.

        Delegates to ``lib.workspace_deps_views.build_system_deps_check``.
        """
        import urllib.parse
        from vivarium_dashboard.lib.workspace_deps_views import build_system_deps_check
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (qs.get("name", [""])[0]).strip()
        body, status = build_system_deps_check(WORKSPACE, name)
        return self._json(body, status)

    def _post_system_deps_install(self, body: dict):
        """POST /api/system-deps-install ``{name, check_names}`` — run install
        commands for the named checks of a catalog module.

        Caller is expected to have surfaced the commands to the user and
        gotten explicit consent before invoking this endpoint; install
        commands are run via ``shell=True`` (catalog is workspace-local
        and editable only by trusted users).

        Returns: ``{ok, log: [...], recheck: [{name, ok, reason}]}``.
        """
        name = (body.get("name") or "").strip()
        check_names = body.get("check_names") or []
        if not name or not check_names:
            return self._json({"error": "name + check_names required"}, 400)

        catalog = self._module_registry()
        entry = next((m for m in catalog if m.get("name") == name), None)
        if entry is None:
            return self._json({"error": f"unknown module: {name}"}, 404)

        sys_deps = (entry.get("system_dependencies") or {}).get("checks") or []
        plat = _platform_key()
        by_name = {c.get("name"): c for c in sys_deps if c.get("name")}

        log: list[dict] = []
        overall_ok = True
        for cn in check_names:
            check = by_name.get(cn)
            if check is None:
                log.append({"check_name": cn, "returncode": -1, "error": "unknown check"})
                overall_ok = False
                continue
            install_block = check.get("install") if isinstance(check.get("install"), dict) else None
            install_spec = install_block.get(plat) if install_block else None
            if not install_spec:
                log.append({
                    "check_name": cn, "returncode": -1,
                    "error": f"no install spec for platform {plat}",
                })
                overall_ok = False
                continue
            commands = install_spec.get("commands") or []
            for cmd in commands:
                # WARNING: shell=True so catalog-supplied commands execute
                # verbatim. Catalog is workspace-local; only trusted users
                # should be allowed to edit it. The UI is expected to have
                # shown each command to the user before this endpoint is
                # called.
                try:
                    result = subprocess.run(
                        cmd, shell=True, capture_output=True, text=True,
                        timeout=600,  # brew installs can be slow
                    )
                except subprocess.TimeoutExpired:
                    log.append({
                        "check_name": cn, "command": cmd,
                        "returncode": -1, "error": "timeout (600s)",
                    })
                    overall_ok = False
                    break
                log.append({
                    "check_name": cn,
                    "command": cmd,
                    "returncode": result.returncode,
                    "stdout_tail": (result.stdout or "")[-500:],
                    "stderr_tail": (result.stderr or "")[-500:],
                })
                if result.returncode != 0:
                    overall_ok = False
                    break

        # Re-check each requested dep after install attempts.
        venv_py = WORKSPACE / ".venv" / "bin" / "python3"
        recheck = []
        for cn in check_names:
            check = by_name.get(cn)
            if check is None:
                continue
            ok, reason = _check_system_dep(check, venv_py)
            recheck.append({"name": cn, "ok": ok, "reason": reason})

        return self._json({
            "ok": overall_ok,
            "log": log,
            "recheck": recheck,
        }, 200)

    def _module_registry(self) -> list[dict]:
        """The available-modules registry (single source of truth).

        The canonical curated list ships with pbg-superpowers
        (``pbg_superpowers.catalog.load_registry``), merged with this
        workspace's optional ``scripts/_catalog/overlay.json`` for local-only
        modules. Falls back to a legacy per-workspace
        ``scripts/_catalog/modules.json`` only when the installed
        pbg-superpowers predates the canonical registry.
        """
        try:
            from pbg_superpowers.catalog import load_registry
            return load_registry(WORKSPACE)
        except Exception:
            legacy = workspace_paths().scripts / "_catalog" / "modules.json"
            if legacy.is_file():
                try:
                    return json.loads(legacy.read_text(encoding="utf-8"))
                except Exception:
                    return []
            return []

    def _get_catalog(self):
        """GET /api/catalog — return the curated module catalog with installed annotations.

        Delegates data assembly to the module-level ``_catalog_data(ws_root)``
        pure builder; wraps the result in the HTTP JSON response.
        """
        data = _catalog_data(WORKSPACE)
        status = 500 if "error" in data and not data.get("modules") else 200
        return self._json(data, status)

    def _workspace_self_module(self, ws_data: dict) -> dict | None:
        """Synthesize a catalog-style entry for the workspace's own package.

        ``workspace.yaml.package_path`` is the first-party Python package that
        ``build_core()`` imports — its Processes/Steps/Composites/Types are
        what the workspace actually runs with. Treating it as just-another
        installed module in the Installed Modules panel makes that visible.

        Returns None when no package_path is declared OR when the directory
        doesn't exist on disk (degenerate workspace; nothing to show).
        """
        slug = (ws_data or {}).get("name", "") or ""
        pkg = (ws_data or {}).get("package_path")
        if not pkg:
            # Fall back to the same heuristic used elsewhere in the server.
            pkg = "pbg_" + slug.replace("-", "_") if slug else None
        if not pkg:
            return None
        pkg_dir = WORKSPACE / pkg
        if not pkg_dir.is_dir():
            return None

        sync_reason = _check_installed_module_sync(pkg, pkg)
        # Best-effort current branch — purely cosmetic for the Source column.
        try:
            ref = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=WORKSPACE, capture_output=True, text=True, timeout=2,
            ).stdout.strip() or "—"
        except (subprocess.TimeoutExpired, OSError):
            ref = "—"
        entry: dict = {
            "kind": "workspace",
            "name": slug or pkg,
            "package": pkg,
            "install_path": pkg,
            "description": "Workspace's own first-party package — provides the "
                           "Processes, Steps, Composites, and Types that "
                           "build_core() registers for this workspace.",
            "source": "workspace",
            "ref": ref,
            "tags": ["workspace"],
            "installed": True,
        }
        if sync_reason:
            entry["out_of_sync"] = True
            entry["out_of_sync_reason"] = sync_reason
        return entry

    def _post_catalog_install(self, body: dict):
        """POST /api/catalog-install — install a catalog module.

        If the catalog entry has a ``pypi_name`` field, the package is
        installed directly from PyPI (no submodule, no uv.sources entry).
        Otherwise the legacy git-submodule path is used.

        Requires an active workstream (uses _active_branch_action).
        """
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "missing name"}, 400)

        # Load catalog entry from the canonical registry (+ workspace overlay).
        modules = self._module_registry()
        entry = next((m for m in modules if m["name"] == name), None)
        if not entry:
            return self._json({"error": f"module '{name}' not in catalog"}, 404)

        # System-dependency gate: if the catalog declares native checks and
        # any are unsatisfied, refuse the install with a 409 containing
        # structured info. UI then prompts the user to install the system
        # deps (or POST again with skip_system_deps_check=true).
        sys_deps_block = entry.get("system_dependencies") or {}
        sys_deps_checks = sys_deps_block.get("checks") or []
        if sys_deps_checks and not bool(body.get("skip_system_deps_check")):
            venv_py_for_check = WORKSPACE / ".venv" / "bin" / "python3"
            plat = _platform_key()
            missing = []
            for check in sys_deps_checks:
                ok, reason = _check_system_dep(check, venv_py_for_check)
                if ok:
                    continue
                install_block = check.get("install") if isinstance(check.get("install"), dict) else None
                install_spec = install_block.get(plat) if install_block else None
                missing.append({
                    "name": check.get("name"),
                    "description": check.get("description", ""),
                    "reason": reason,
                    "install": install_spec,
                    "notes": check.get("notes"),
                })
            if missing:
                return self._json({
                    "error": "unmet system dependencies",
                    "name": name,
                    "platform": plat,
                    "missing": missing,
                    "hint": "POST again with skip_system_deps_check=true to proceed anyway, or call /api/system-deps-install first.",
                }, 409)

        pypi_name = entry.get("pypi_name")  # optional; if set, install from PyPI

        target_path = f"external/{name}"
        abs_target = (WORKSPACE / target_path).resolve()

        # Resolve uv / pip command upfront (before the action closure).
        venv_pip = WORKSPACE / ".venv" / "bin" / "pip"
        venv_py = WORKSPACE / ".venv" / "bin" / "python3"
        uv_path = shutil.which("uv")

        if pypi_name:
            # PyPI path: use uv exclusively (faster, no submodule needed).
            if uv_path and venv_py.exists():
                pypi_install_cmd = [uv_path, "pip", "install", "--python", str(venv_py), pypi_name]
            elif venv_pip.exists():
                pypi_install_cmd = [str(venv_pip), "install", pypi_name]
            else:
                return self._json({"error": "neither pip nor uv available"}, 500)
        else:
            # Git-submodule fallback: editable local install.
            if venv_pip.exists():
                pip_cmd_base = [str(venv_pip), "install", "-e"]
            elif uv_path and venv_py.exists():
                pip_cmd_base = [uv_path, "pip", "install", "--python", str(venv_py), "-e"]
            else:
                return self._json({"error": "neither pip nor uv available"}, 500)

        package_name = entry.get("package", name)
        catalog_entry = entry  # captured for closure
        log_holder: list[str] = []
        install_mode_holder: list[str] = []

        def action():
            if pypi_name:
                # ---- PyPI install path ----
                install_mode_holder.append("pypi")

                try:
                    result = subprocess.run(
                        pypi_install_cmd,
                        cwd=WORKSPACE, capture_output=True,
                        encoding="utf-8", errors="replace", timeout=180,
                    )
                except subprocess.TimeoutExpired:
                    raise RuntimeError("pip install from PyPI timed out after 180s")

                excerpt = (result.stdout + "\n" + result.stderr).strip()[-2000:]
                log_holder.append(excerpt)
                if result.returncode != 0:
                    raise RuntimeError(f"pip install from PyPI failed:\n{excerpt[-500:]}")

                # workspace.yaml
                _ws_add_to_sys_path()
                from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
                from vivarium_dashboard.lib.pyproject_edit import add_dependency

                ws_file = WORKSPACE / "workspace.yaml"
                ws = load_workspace(ws_file)
                ws.setdefault("imports", {})[name] = {
                    "source": catalog_entry["source"],
                    "ref": catalog_entry["ref"],
                    "mode": "pypi",
                    "pypi_name": pypi_name,
                    "description": catalog_entry.get("description", ""),
                    "installed": True,
                    "package": package_name,
                }
                save_workspace(ws_file, ws)

                # pyproject.toml — only [project.dependencies]; NO uv.sources entry
                # because the package is on PyPI and resolves without local path mapping.
                try:
                    add_dependency(WORKSPACE / "pyproject.toml", pypi_name)
                except Exception as e:
                    log_dir = workspace_paths().pbg
                    log_dir.mkdir(parents=True, exist_ok=True)
                    (log_dir / "catalog-install.log").write_text(
                        f"pyproject edit failed for {name}: {e}\n"
                    )

            else:
                # ---- Git-submodule fallback path ----
                install_mode_holder.append("git")

                # Step 1: submodule add if directory not already present.
                if not abs_target.exists():
                    # Clean up any stale `.git/modules/<path>` left behind by a
                    # previous uninstall — git refuses `submodule add` when one
                    # exists. The matching working-tree dir was already
                    # verified absent above, and the module is not in
                    # .gitmodules, so the leftover is safe to remove.
                    stale = WORKSPACE / ".git" / "modules" / target_path
                    if stale.is_dir():
                        shutil.rmtree(stale, ignore_errors=True)

                    r = subprocess.run(
                        ["git", "submodule", "add", "-b", catalog_entry["ref"],
                         catalog_entry["source"], target_path],
                        cwd=WORKSPACE, capture_output=True,
                        encoding="utf-8", errors="replace", timeout=120,
                    )
                    if r.returncode != 0:
                        raise RuntimeError(
                            f"submodule add failed: {(r.stderr or r.stdout)[:300]}"
                        )

                # Step 2: pip install -e.
                try:
                    result = subprocess.run(
                        pip_cmd_base + [str(abs_target)],
                        cwd=WORKSPACE, capture_output=True,
                        encoding="utf-8", errors="replace", timeout=180,
                    )
                except subprocess.TimeoutExpired:
                    raise RuntimeError("pip install timed out after 180s")

                excerpt = (result.stdout + "\n" + result.stderr).strip()[-2000:]
                log_holder.append(excerpt)
                if result.returncode != 0:
                    raise RuntimeError(f"pip install failed:\n{excerpt[-500:]}")

                # Step 3: workspace.yaml.
                _ws_add_to_sys_path()
                from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
                from vivarium_dashboard.lib.pyproject_edit import add_dependency, add_uv_source

                ws_file = WORKSPACE / "workspace.yaml"
                ws = load_workspace(ws_file)
                ws.setdefault("imports", {})[name] = {
                    "source": catalog_entry["source"],
                    "ref": catalog_entry["ref"],
                    "mode": "reference",
                    "path": f"external/{name}",
                    "description": catalog_entry.get("description", ""),
                    "installed": True,
                    "install_path": str(abs_target),
                    "package": package_name,
                }
                save_workspace(ws_file, ws)

                # Step 4: pyproject.toml — both [project.dependencies] and
                # [tool.uv.sources]. The dep line declares the requirement;
                # the uv-source maps it to the local submodule path so uv can
                # resolve a git-only pbg-* package in CI without going to PyPI.
                try:
                    add_dependency(WORKSPACE / "pyproject.toml", package_name)
                    add_uv_source(
                        WORKSPACE / "pyproject.toml",
                        package_name,
                        path=f"external/{name}",
                        editable=True,
                    )
                except Exception as e:
                    # Don't fail the whole install if pyproject edit fails — log it.
                    log_dir = workspace_paths().pbg
                    log_dir.mkdir(parents=True, exist_ok=True)
                    (log_dir / "catalog-install.log").write_text(
                        f"pyproject edit failed for {name}: {e}\n"
                    )

        commit_msg = f"feat(catalog): install {name}"
        # _commit_or_run falls back to running the install action directly
        # when there's no active workstream — so users can install composites
        # / run things without first creating a workstream branch.
        resp, code = _commit_or_run(commit_msg, action)
        log_excerpt = log_holder[0] if log_holder else ""
        install_mode = install_mode_holder[0] if install_mode_holder else ("pypi" if pypi_name else "git")

        # Invalidate registry cache.
        clear_registry_cache()

        if code == 200:
            resp["ok"] = True
            resp["module"] = name
            resp["install_mode"] = install_mode
            resp["log"] = log_excerpt[-500:]
        elif code == 409 and "no changes" in (resp.get("error") or ""):
            # The pip install ran; metadata might already be in workspace.yaml.
            return self._json({
                "ok": True,
                "already_installed": True,
                "module": name,
                "install_mode": install_mode,
                "log": log_excerpt[-500:],
            }, 200)
        elif code == 500 and log_excerpt:
            # pip install failed inside action() — add structured diagnosis if available.
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.install_errors import diagnose as _diagnose_install
            diag = _diagnose_install(log_excerpt)
            resp["log"] = log_excerpt[-1000:]
            resp["install_mode"] = install_mode
            if diag:
                resp["diagnosis"] = diag.as_dict()

        return self._json(resp, code)

    def _uninstall_unmanaged_or_404(self, name: str):
        """Handle uninstall for catalog modules NOT in workspace.yaml.imports.

        Three cases:
        1. Not in venv either → genuinely already uninstalled (200).
        2. In venv but other installed packages require it (transitive with
           real parents) → 409, tell the user to uninstall the parent(s) first.
        3. In venv with no parent (unmanaged orphan) → pip uninstall +
           best-effort remove of untracked external/<name>/ checkout. No
           pyproject/workspace.yaml edits to make (nothing claims it).

        Skips the _commit_or_run wrapper because case 3 has no tracked-file
        changes to commit (external/<name>/ is removed only if untracked).
        """
        venv_dists = _detect_workspace_venv_distributions(WORKSPACE)

        # Resolve catalog metadata so we know the actual python pkg / pypi name.
        catalog_pkg = name.replace("-", "_")
        catalog_pypi = name
        try:
            for cat_m in self._module_registry():
                if cat_m.get("name") == name:
                    catalog_pkg = cat_m.get("package") or catalog_pkg
                    catalog_pypi = cat_m.get("pypi_name") or name
                    break
        except Exception:
            pass

        variants = {name.lower(), catalog_pkg.lower(), catalog_pypi.lower()}
        dist_info = None
        matched_dist = None
        for v in variants:
            if v in venv_dists:
                dist_info = venv_dists[v]
                matched_dist = v
                break

        if dist_info is None:
            return self._json({"ok": True, "already_uninstalled": True}, 200)

        parents = dist_info.get("requires_by") or []
        if parents:
            return self._json({
                "error": f"{name} is required by {', '.join(parents)} — uninstall the parent(s) first",
                "transitive_via": parents,
                "module": name,
            }, 409)

        # Orphaned venv install — safe to remove directly.
        venv_py = WORKSPACE / ".venv" / "bin" / "python3"
        uv_path = shutil.which("uv")
        target = catalog_pypi or matched_dist
        if uv_path and venv_py.exists():
            uninstall_cmd = [uv_path, "pip", "uninstall", "--python", str(venv_py), target]
        else:
            venv_pip = WORKSPACE / ".venv" / "bin" / "pip"
            if venv_pip.exists():
                uninstall_cmd = [str(venv_pip), "uninstall", "-y", target]
            else:
                return self._json({"error": "no venv pip/uv available to uninstall"}, 500)

        log: list[str] = []
        try:
            r = subprocess.run(
                uninstall_cmd, cwd=WORKSPACE, capture_output=True, text=True, timeout=60,
            )
            log.append((r.stdout + "\n" + r.stderr).strip()[-2000:])
        except Exception as e:
            log.append(f"pip uninstall failed: {e}")

        # Remove untracked external/<name>/ checkout if present and NOT a
        # registered submodule (deinit/git-rm flow is reserved for the
        # imports-declared path).
        ext_path = WORKSPACE / "external" / name
        if ext_path.exists():
            gm = WORKSPACE / ".gitmodules"
            is_submodule = False
            if gm.exists():
                try:
                    is_submodule = f"external/{name}" in gm.read_text(encoding="utf-8")
                except Exception:
                    pass
            if is_submodule:
                log.append(f"external/{name} is a tracked submodule; left in place")
            else:
                try:
                    shutil.rmtree(ext_path)
                    log.append(f"removed external/{name}/")
                except Exception as e:
                    log.append(f"rm external/{name} failed: {e}")

        clear_registry_cache()

        return self._json({
            "ok": True,
            "module": name,
            "install_mode": "unmanaged",
            "log": "\n".join(log)[-500:],
        }, 200)

    def _post_catalog_uninstall(self, body: dict):
        """POST /api/catalog-uninstall — remove a catalog module from this workspace.

        Reverses _post_catalog_install:
        - PyPI mode: uv pip uninstall <pypi_name>, remove from [project.dependencies].
        - Git mode: git submodule deinit + git rm external/<name>, remove dep +
          [tool.uv.sources] entry from pyproject.toml.
        - Both: remove workspace.yaml imports.<name>.

        Wrapped in _active_branch_action so the change is committed on the active
        stage/* branch.
        """
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "missing name"}, 400)

        # Read workspace.yaml to check if it's installed.
        ws_file = WORKSPACE / "workspace.yaml"
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace

        ws = load_workspace(ws_file)
        imports = ws.get("imports") or {}
        if name not in imports:
            # Either truly not installed, OR an "unmanaged" venv install
            # (editable/hand-installed without a workspace.yaml.imports
            # declaration — common when a previous workspace flow added the
            # package as an editable submodule and the user later wants it
            # gone). Detect the latter and run a minimal uninstall:
            # pip uninstall + best-effort remove of untracked external/<name>/.
            return self._uninstall_unmanaged_or_404(name)

        entry = imports[name]
        mode = entry.get("mode", "reference")  # "pypi" or "reference"
        pypi_name = entry.get("pypi_name")
        package_name = entry.get("package", name)

        venv_py = WORKSPACE / ".venv" / "bin" / "python3"
        uv_path = shutil.which("uv")

        # Build uninstall command (best-effort; don't fail if pip uninstall errors).
        if uv_path and venv_py.exists():
            uninstall_cmd_base = [uv_path, "pip", "uninstall", "--python", str(venv_py)]
        else:
            venv_pip = WORKSPACE / ".venv" / "bin" / "pip"
            if venv_pip.exists():
                uninstall_cmd_base = [str(venv_pip), "uninstall", "-y"]
            else:
                uninstall_cmd_base = None

        log_holder: list[str] = []
        uninstall_mode_holder: list[str] = []

        def action():
            from vivarium_dashboard.lib.pyproject_edit import remove_dependency, remove_uv_source

            if mode == "pypi":
                uninstall_mode_holder.append("pypi")
                pkg_to_uninstall = pypi_name or package_name

                # Remove from pyproject.toml [project.dependencies].
                try:
                    remove_dependency(WORKSPACE / "pyproject.toml", pkg_to_uninstall)
                except Exception as e:
                    log_holder.append(f"pyproject dep remove failed: {e}")

                # Pip uninstall — best effort.
                if uninstall_cmd_base:
                    try:
                        result = subprocess.run(
                            uninstall_cmd_base + [pkg_to_uninstall],
                            cwd=WORKSPACE, capture_output=True, text=True, timeout=60,
                        )
                        excerpt = (result.stdout + "\n" + result.stderr).strip()[-2000:]
                        log_holder.append(excerpt)
                    except Exception as e:
                        log_holder.append(f"pip uninstall failed (best-effort): {e}")

            else:
                # Reference / git-submodule mode.
                uninstall_mode_holder.append("reference")

                # Remove dep + uv source from pyproject.toml.
                try:
                    remove_dependency(WORKSPACE / "pyproject.toml", package_name)
                    remove_uv_source(WORKSPACE / "pyproject.toml", package_name)
                except Exception as e:
                    log_holder.append(f"pyproject edit failed: {e}")

                # Remove git submodule.
                target_path = f"external/{name}"
                abs_target = (WORKSPACE / target_path).resolve()
                if abs_target.exists() or (WORKSPACE / ".gitmodules").exists():
                    try:
                        subprocess.run(
                            ["git", "submodule", "deinit", "-f", target_path],
                            cwd=WORKSPACE, capture_output=True, text=True, timeout=30,
                        )
                    except Exception as e:
                        log_holder.append(f"submodule deinit failed (best-effort): {e}")

                    try:
                        r = subprocess.run(
                            ["git", "rm", "-f", target_path],
                            cwd=WORKSPACE, capture_output=True, text=True, timeout=30,
                        )
                        log_holder.append((r.stdout + "\n" + r.stderr).strip()[-500:])
                    except Exception as e:
                        log_holder.append(f"git rm failed (best-effort): {e}")

                # Pip uninstall — best effort.
                if uninstall_cmd_base:
                    try:
                        result = subprocess.run(
                            uninstall_cmd_base + [package_name],
                            cwd=WORKSPACE, capture_output=True, text=True, timeout=60,
                        )
                        excerpt = (result.stdout + "\n" + result.stderr).strip()[-2000:]
                        log_holder.append(excerpt)
                    except Exception as e:
                        log_holder.append(f"pip uninstall failed (best-effort): {e}")

            # Remove workspace.yaml imports entry.
            ws2 = load_workspace(ws_file)
            ws2.get("imports", {}).pop(name, None)
            save_workspace(ws_file, ws2)

        commit_msg = f"feat(catalog): uninstall {name}"
        # Fall back to direct run when there's no active workstream.
        resp, code = _commit_or_run(commit_msg, action)
        log_excerpt = "\n".join(log_holder)[-500:]
        uninstall_mode = uninstall_mode_holder[0] if uninstall_mode_holder else mode

        # Invalidate registry cache.
        clear_registry_cache()

        if code == 200:
            resp["ok"] = True
            resp["module"] = name
            resp["install_mode"] = uninstall_mode
            resp["log"] = log_excerpt
        elif code == 409 and "no changes" in (resp.get("error") or ""):
            return self._json({
                "ok": True,
                "already_uninstalled": True,
                "module": name,
                "install_mode": uninstall_mode,
                "log": log_excerpt,
            }, 200)

        return self._json(resp, code)

    def _serve_file(self, path: Path, mime: str):
        if not path.exists() or not path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _get_workspaces(self):
        """GET /api/workspaces — dropdown payload for the workspace switcher.

        Reads ~/.pbg/workspaces.json (catalog) and joins each entry with
        ~/.pbg/servers/<name>.json to determine status. No HTTP probes.
        Falls back to current-workspace-only on missing/corrupt catalog.

        Delegates to ``lib.workspace_deps_views.build_workspaces``.
        """
        from vivarium_dashboard.lib.workspace_deps_views import build_workspaces
        self._json(build_workspaces(WORKSPACE), 200)

    def _post_workspaces_add(self, body: dict):
        """POST /api/workspaces/add — register an existing workspace in the catalog."""
        path = body.get("path") if isinstance(body, dict) else None
        if not path or not isinstance(path, str) or not path.startswith("/"):
            self._json({"error": "path must be an absolute string"}, 400)
            return
        from pbg_superpowers import workspace_catalog
        try:
            entry = workspace_catalog.add(path)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
        self._json(entry, 200)

    def _post_workspaces_forget(self, body: dict):
        """POST /api/workspaces/forget — remove the catalog entry. Refuses
        to forget a running workspace; caller must stop it first."""
        path = body.get("path") if isinstance(body, dict) else None
        if not path or not isinstance(path, str):
            self._json({"error": "path required"}, 400)
            return
        from pbg_superpowers import workspace_catalog
        if workspace_catalog.find_running(path) is not None:
            self._json({"error": "stop the server before forgetting"}, 409)
            return
        workspace_catalog.forget(path)
        self._json({"ok": True}, 200)

    def _post_workspaces_cleanup_stale(self, body: dict):
        """POST /api/workspaces/cleanup-stale — remove a stale running-registry
        entry plus orphan workspace-local files. Refuses if the PID is in
        fact alive."""
        path = body.get("path") if isinstance(body, dict) else None
        if not path or not isinstance(path, str):
            self._json({"error": "path required"}, 400)
            return
        from pbg_superpowers import workspace_catalog
        if workspace_catalog.find_running(path) is not None:
            self._json({"error": "server is still running"}, 409)
            return
        workspace_catalog.unregister_server(path)
        # Best-effort removal of the orphan workspace-local files.
        sdir = Path(path).expanduser().resolve() / ".pbg" / "server"
        for fname in ("server-info", "server.pid"):
            try:
                (sdir / fname).unlink()
            except FileNotFoundError:
                pass
        self._json({"ok": True}, 200)

    def _post_workspaces_start(self, body: dict):
        """POST /api/workspaces/start — spawn `vivarium-dashboard serve` for a
        stopped workspace and poll until it registers. Idempotent: returns
        the existing URL if a live entry already exists. Returns 504 with
        log_path if the child doesn't register within 8 s."""
        path = body.get("path") if isinstance(body, dict) else None
        if not path or not isinstance(path, str) or not path.startswith("/"):
            self._json({"error": "path must be an absolute string"}, 400)
            return

        target = Path(path).expanduser().resolve()
        if not (target / "workspace.yaml").is_file():
            self._json({"error": "not a workspace (no workspace.yaml)"}, 400)
            return

        from pbg_superpowers import workspace_catalog

        # Safety: only catalog paths can be spawned. Prevents the dashboard
        # from being used to launch processes against arbitrary directories.
        if not any(Path(e.get("path") or "").resolve() == target
                   for e in workspace_catalog.list_workspaces()):
            self._json({"error": "workspace not in catalog — Add it first"}, 400)
            return

        # Idempotent: if a live entry exists, return it.
        live = workspace_catalog.find_running(target)
        if live is not None:
            self._json({"url": live["url"], "pid": live["pid"]}, 200)
            return

        log_path = target / ".pbg" / "server" / "start.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as logf:
            subprocess.Popen(
                [sys.executable, "-m", "vivarium_dashboard.cli",
                 "serve", "--workspace", str(target)],
                stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                cwd=str(target),
            )

        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            entry = workspace_catalog.find_running(target)
            if entry is not None:
                self._json({"url": entry["url"], "pid": entry["pid"]}, 200)
                return
            time.sleep(0.1)

        self._json({
            "error": "start_timeout",
            "log_path": str(log_path),
            "hint": f"tail {log_path}",
        }, 504)

    def _post_workspaces_stop(self, body: dict):
        """POST /api/workspaces/stop — SIGTERM a running workspace's dashboard
        and poll for the child's atexit hook to remove the global registry
        entry. Refuses self-stop and uncatalogued paths. Does NOT escalate
        to SIGKILL on timeout — returns 504 with the PID instead."""
        path = body.get("path") if isinstance(body, dict) else None
        if not path or not isinstance(path, str) or not path.startswith("/"):
            self._json({"error": "path must be an absolute string"}, 400)
            return

        target = Path(path).expanduser().resolve()

        from pbg_superpowers import workspace_catalog

        # Catalog membership guard (same as /start).
        if not any(Path(e.get("path") or "").resolve() == target
                   for e in workspace_catalog.list_workspaces()):
            self._json({"error": "workspace not in catalog"}, 400)
            return

        # Refuse self-stop: WORKSPACE is the dashboard's own bound workspace,
        # already resolved by serve(). Stopping it would kill the dashboard
        # the user is currently using.
        if target == WORKSPACE:
            entry_self = workspace_catalog.find_running(target)
            pid_self = entry_self["pid"] if entry_self else os.getpid()
            self._json({
                "error": f"refusing to stop self \u2014 use the terminal: kill {pid_self}"
            }, 400)
            return

        entry = workspace_catalog.find_running(target)
        if entry is None:
            self._json({"error": "not running"}, 400)
            return

        pid = int(entry["pid"])
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            # Already dead between find_running and os.kill \u2014 treat as success.
            self._json({"ok": True}, 200)
            return

        # Poll for the child's atexit to remove the global entry.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if workspace_catalog.find_entry(target) is None:
                self._json({"ok": True}, 200)
                return
            time.sleep(0.1)

        self._json({
            "error": "stop_timeout",
            "hint": f"PID {pid} still alive; SIGKILL it manually if stuck",
        }, 504)

    def _post_source_switch(self, body: dict):
        """POST /api/source/switch — re-point the active workspace in-process.

        Body: {"path": <workspace dir>}. The path MUST be a registered catalog
        entry (validated against workspace_catalog.list_workspaces(); no arbitrary
        paths). Returns {ok, source}; the client reloads.
        """
        from pbg_superpowers import workspace_catalog
        path = str(body.get("path") or "").strip()
        if not path:
            return self._json({"error": "missing 'path'"}, 400)
        target = str(Path(path).resolve())
        entry = next(
            (w for w in workspace_catalog.list_workspaces()
             if str(Path(w["path"]).resolve()) == target),
            None,
        )
        if entry is None:
            return self._json(
                {"error": f"{path!r} is not a registered workspace"}, 400)
        _switch_active_workspace(Path(entry["path"]))
        return self._json(
            {"ok": True,
             "source": {"path": str(entry["path"]), "name": entry.get("name")}},
            200,
        )

    def _post_branch_push(self, body: dict):
        """POST /api/branch/push — commit WORKSPACE changes + push current branch."""
        message = (body or {}).get("message") or "dashboard commit"
        try:
            return self._json(_remote_commit_and_push(message), 200)
        except _NotAGitRepo as e:
            return self._json({"error": str(e)}, 409)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def _post_source_build_remote(self, body: dict):
        """POST /api/source/build-remote — register a repo+branch's HEAD as an sms-api build."""
        from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError
        repo = (body or {}).get("repo") or ""
        branch = (body or {}).get("branch") or ""
        if not repo or not branch:
            return self._json({"error": "repo and branch are required"}, 400)
        repo = _normalize_repo_url(repo)
        client = SmsApiClient(_sms_api_base())
        try:
            latest = client.latest_simulator(repo, branch)
            commit = latest.get("git_commit_hash") or ""
            if not commit:
                return self._json({"error": "could not resolve branch HEAD via sms-api"}, 502)
            reg = client.register_simulator(repo, branch, commit)
        except SmsApiError as e:
            return self._json({"error": f"sms-api: {e}"}, 502)
        return self._json({"ok": True, "simulator_id": reg.get("database_id"),
                           "repo": repo, "branch": branch, "commit": commit}, 200)

    def _get_source_builds(self):
        """GET /api/source/builds — remote sms-api simulator builds for the
        source dropdown. Best-effort; empty list + reason if sms-api is down.

        Delegates to ``lib.workspace_deps_views.build_source_builds``.
        """
        from vivarium_dashboard.lib.workspace_deps_views import build_source_builds
        return self._json(build_source_builds(), 200)

    def _post_source_switch_build(self, body: dict):
        """POST /api/source/switch-build — materialize a build's workspace (once,
        cached) and re-point to it in-process (SP2). Body: {simulator_id}."""
        from vivarium_dashboard.lib import remote_build_source
        from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError
        sim_id = body.get("simulator_id")
        if sim_id is None:
            return self._json({"error": "missing 'simulator_id'"}, 400)
        client = SmsApiClient(_sms_api_base())
        listing = remote_build_source.list_build_sources(client)
        entry = next((b for b in listing["builds"] if b["simulator_id"] == sim_id), None)
        if entry is None:
            if listing.get("error"):
                return self._json({"error": f"sms-api unavailable: {listing['error']}"}, 502)
            return self._json({"error": f"build {sim_id} not found"}, 404)
        try:
            cache_dir = remote_build_source.materialize_build(client, sim_id, entry["commit"])
        except SmsApiError as e:
            return self._json({"error": f"materialize failed: {e}"}, 502)
        # Stamp build provenance into the cache dir so the rail chip can show
        # "<branch> @ <commit> · remote build #<id>" (a materialized build is not
        # a git repo, so the chip can't derive branch/commit from git).
        try:
            (Path(cache_dir) / ".viv-build.json").write_text(json.dumps({
                "simulator_id": sim_id, "repo": entry.get("repo", ""),
                "branch": entry.get("branch", ""), "commit": entry.get("commit", ""),
                "repo_url": entry.get("repo_url", ""),
            }))
        except Exception:
            pass  # provenance stamp is best-effort, never block the switch
        _switch_active_workspace(cache_dir)
        return self._json({"ok": True, "source": {"path": str(cache_dir), "name": entry["label"]}}, 200)

    def _read_workspace_name(self, root: Path) -> str:
        """Read `name` from <root>/workspace.yaml; fall back to dir basename."""
        try:
            data = yaml.safe_load((root / "workspace.yaml").read_text(encoding="utf-8")) or {}
            return data.get("name") or root.name
        except Exception:
            return root.name

    def _serve_state(self):
        ws_file = WORKSPACE / "workspace.yaml"
        if not ws_file.exists():
            self.send_response(404)
            self.end_headers()
            return
        ws = yaml.safe_load(ws_file.read_text(encoding="utf-8"))
        body = json.dumps(ws).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_guidance(self):
        latest = _download_views.resolve_guidance(WORKSPACE)
        if latest is None:
            self.send_response(204)
            self.end_headers()
            return
        return self._serve_file(latest, "text/html")

    def _serve_events_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last_state = None
        ws_file = WORKSPACE / "workspace.yaml"
        try:
            while True:
                if ws_file.exists():
                    text = ws_file.read_text(encoding="utf-8")
                    if text != last_state:
                        # Derive payload from the SAME text we deduped on (single read).
                        payload = _events_lib.payload_from_text(text)
                        self.wfile.write(b"event: state\ndata: ")
                        self.wfile.write(payload.encode())
                        self.wfile.write(b"\n\n")
                        self.wfile.flush()
                        last_state = text
                time.sleep(1.0)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_json_bytes(self, body: bytes, code: int):
        """Send pre-encoded JSON bytes with the standard JSON response headers.

        Used by do_GET branches that call a ``_build_*_response`` builder (which
        already encodes to bytes) so the encoding step is not duplicated.
        """
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, code: int):
        body = _json_body(data)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    # Remote-run endpoints (Phase 3b)
    # ------------------------------------------------------------------

    def _post_remote_run_start(self, body: dict):
        """POST /api/remote-run-start {study, num_generations?, num_seeds?, run_parca?}"""
        from vivarium_dashboard.lib import github_auth
        from vivarium_dashboard.lib.investigations import load_spec
        from vivarium_dashboard.lib.remote_run_jobs import PipelineCtx, manager, run_remote_pipeline
        from vivarium_dashboard.lib.remote_run_landing import land_remote_run
        from vivarium_dashboard.lib.sms_api_client import SmsApiClient

        if github_auth.current_session() is None:
            return self._json({"error": "not authenticated"}, 401)
        study = (body.get("study") or "").strip()
        if not study:
            return self._json({"error": "study is required"}, 400)
        if not _has_origin_remote():
            return self._json({"error": "no GitHub remote configured"}, 409)
        repo_url = _remote_repo_url()
        if not repo_url:
            return self._json({"error": "could not resolve origin remote url"}, 409)

        spec_path = _study_spec_path(study)
        if spec_path is None or not spec_path.is_file():
            return self._json({"error": f"study {study!r} not found"}, 404)
        spec = load_spec(spec_path)
        observables = _collect_study_observables(spec)

        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=WORKSPACE,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()

        client = SmsApiClient(_sms_api_base())
        # spec_id = the study's baseline COMPOSITE ref (what local runs use:
        # _post_study_run_baseline_for_test -> entry.get("composite")), NOT the
        # baseline entry's `name` (which is the study slug). Falls back to the
        # study slug only when no baseline composite is declared.
        _baseline = spec.get("baseline") or []
        _spec_id = (_baseline[0].get("composite") if _baseline else None) or study
        ctx = PipelineCtx(
            study=study,
            study_dir=_study_dir(study),
            spec_id=_spec_id,
            repo_url=repo_url,
            branch=branch,
            observables=observables,
            num_generations=int(body.get("num_generations") or 1),
            num_seeds=int(body.get("num_seeds") or 1),
            run_parca=bool(body.get("run_parca", True)),
            client=client,
            push_and_sha=_remote_push_and_sha,
            land=land_remote_run,
        )
        job = manager.submit(study, lambda j: run_remote_pipeline(j, ctx))
        return self._json({"job_id": job.job_id}, 202)

    def _get_remote_run_status(self):
        """GET /api/remote-run-status?job_id=<id>"""
        from urllib.parse import parse_qs, urlparse

        from vivarium_dashboard.lib.remote_run_jobs import manager
        from vivarium_dashboard.lib import job_status_views as _job_status_views

        qs = parse_qs(urlparse(self.path).query)
        job_id = (qs.get("job_id") or [""])[0]
        return self._json(*_job_status_views.job_status(manager, job_id))

    @staticmethod
    def _guess_mime(rel: str) -> str:
        # Single-sourced through lib.static_serving.guess_mime (the FastAPI seam
        # uses the same table); the body lives once, in lib.
        from vivarium_dashboard.lib.static_serving import guess_mime
        return guess_mime(rel)


# ---------------------------------------------------------------------------
# Composite diagram rendering helper
# ---------------------------------------------------------------------------

def _render_composite_svg(state: dict, package_name: str) -> str:
    """Run bigraph-viz to render the composite state. Return SVG string or error placeholder."""
    py = sys.executable
    # The state can be multi-megabyte (v2ecoli composites embed initial_state),
    # which overflows the OS ARG_MAX if passed inside the ``-c`` script. Keep the
    # script constant-size and feed the state JSON through stdin instead.
    script = textwrap.dedent(f"""
        import json, sys, traceback
        try:
            from {package_name}.core import build_core
            from process_bigraph import Composite
            try:
                from bigraph_viz import plot_bigraph
            except ImportError:
                print("@@@NO_BIGRAPH_VIZ@@@")
                sys.exit(0)

            core = build_core()
            state = json.load(sys.stdin)
            # bigraph-viz's plot_bigraph expects the state dict directly, NOT
            # composite.composition (which is a string in this version). Pass
            # the resolved state with core so node types resolve properly.
            #
            # bigraph-viz >=2.0.3 returns a ResponsiveGraph whose
            # _make_responsive_svg() handles the responsive width + the
            # graphviz viewBox/transform mismatch that previously clipped
            # the right/bottom edges. Fall back to raw .pipe('svg') if a
            # downgrade ever happens, but the pin in pyproject.toml is >=2.0.3.
            try:
                fig = plot_bigraph(state=state, core=core, rankdir='LR')
                if hasattr(fig, '_make_responsive_svg'):
                    svg = fig._make_responsive_svg()
                else:
                    svg = fig.pipe(format='svg').decode('utf-8')
                print('@@@SVG@@@')
                print(svg)
            except Exception as e:
                print('@@@ERROR@@@')
                print(f'render failed: {{e}}')
        except Exception as e:
            print('@@@ERROR@@@')
            print(traceback.format_exc())
    """)
    try:
        result = subprocess.run(
            [py, "-c", script],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=30,
            input=json.dumps(state, default=_json_default),
        )
    except subprocess.TimeoutExpired:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='400' height='50'><text x='10' y='30'>diagram render timed out</text></svg>"

    out = result.stdout
    if "@@@SVG@@@" in out:
        return out.split("@@@SVG@@@", 1)[1].strip()
    if "@@@NO_BIGRAPH_VIZ@@@" in out:
        return ("<svg xmlns='http://www.w3.org/2000/svg' width='600' height='50'>"
                "<text x='10' y='30'>bigraph-viz not installed. "
                "Add bigraph-viz to pyproject.toml dependencies and run: uv pip install bigraph-viz. "
                "Falling back to JSON state below.</text></svg>")
    if "@@@ERROR@@@" in out:
        err = out.split("@@@ERROR@@@", 1)[1].strip()[:500]
        return f"<svg xmlns='http://www.w3.org/2000/svg' width='600' height='50'><text x='10' y='30'>diagram render failed: {err}</text></svg>"
    return "<svg xmlns='http://www.w3.org/2000/svg' width='400' height='50'><text x='10' y='30'>diagram render returned nothing</text></svg>"


# ---------------------------------------------------------------------------
# Sys-path injection helper
# ---------------------------------------------------------------------------

def _ws_add_to_sys_path() -> None:
    """Make the workspace's own Python package(s) importable.

    After extraction from pbg-template, the lib helpers live in
    ``vivarium_dashboard.lib`` (installed in the venv). The workspace's
    own package — e.g. ``pbg_chromosome_rep1`` — still lives at the
    workspace root, so we add WORKSPACE to sys.path so it resolves as
    a top-level package.
    """
    ws = str(WORKSPACE)
    if ws not in sys.path:
        sys.path.insert(0, ws)


# ---------------------------------------------------------------------------
# Bundled-asset paths
# ---------------------------------------------------------------------------

import vivarium_dashboard as _vd_pkg  # noqa: E402
PACKAGE_ROOT: Path = Path(_vd_pkg.__file__).parent
TEMPLATES_DIR: Path = PACKAGE_ROOT / "templates"
STATIC_DIR: Path = PACKAGE_ROOT / "static"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def serve(workspace: Path, port: int, host: str = "127.0.0.1") -> int:
    """Boot the dashboard HTTP server against ``workspace`` on ``host:port``.

    ``host`` defaults to ``127.0.0.1`` (loopback only — appropriate for local
    development). Pass ``0.0.0.0`` to bind every interface, which is required
    when running inside a container whose published port must be reachable
    from the host.

    Blocks until the server stops. Returns 0 on clean shutdown.
    """
    global WORKSPACE
    WORKSPACE = Path(workspace).resolve()
    # Run with CWD = workspace root. In-process composite/generator builds use
    # workspace-relative paths (e.g. out/cache/initial_state.json) and would
    # otherwise resolve against wherever the server was launched from, failing
    # with "No such file or directory". Subprocess calls already pass cwd=WORKSPACE.
    os.chdir(WORKSPACE)
    _ws_add_to_sys_path()
    # Register the active workspace root for ``vivarium_dashboard.lib`` helpers
    # that used to walk up from __file__.
    from vivarium_dashboard.lib._root import set_workspace_root
    set_workspace_root(WORKSPACE)

    # Repair runs left 'running' by a previous crash/restart: a dead or
    # missing PID becomes 'orphaned'; a live PID is left to keep running.
    try:
        from vivarium_dashboard.lib.run_registry import reconcile_stale_runs
        n = reconcile_stale_runs(workspace_paths().pbg / "composite-runs.db")
        if n:
            print(f"reconciled {n} stale composite run(s) on startup")
    except Exception as e:  # noqa: BLE001 — never block server boot on this
        print(f"warning: run reconcile failed: {e}", file=sys.stderr)

    srv = ThreadingHTTPServer((host, port), Handler)
    # Write server-info so tests and other tools can detect the server is ready.
    # When binding 0.0.0.0, advertise the loopback URL since that's what
    # in-container tooling reaches; the host machine reaches via the published
    # port mapping.
    advertise_host = "127.0.0.1" if host == "0.0.0.0" else host
    info_dir = workspace_paths().pbg / "server"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "server-info").write_text(json.dumps({
        "port": port,
        "host": advertise_host,
        "bind_host": host,
        "url": f"http://{advertise_host}:{port}",
        "pid": os.getpid(),
        "screen_dir": str(info_dir / "content"),
        "state_dir": str(info_dir / "state"),
    }))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1",
                    help="Bind host (default 127.0.0.1; use 0.0.0.0 in containers)")
    args = ap.parse_args()
    return serve(args.workspace, args.port, host=args.host)


if __name__ == "__main__":
    sys.exit(main() or 0)
