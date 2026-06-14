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


def _strip_process_instances(state):
    """Strip live Process/Step instances from a state tree before JSON encoding.

    Composite generators (e.g. v2ecoli's ``make_edge``) attach the live
    Python ``instance`` to the state dict alongside the serialisable
    ``address`` + ``config``. The instance can't cross a JSON boundary;
    address+config is sufficient for the child subprocess to rebuild the
    composite via ``Composite()`` + ``core.register_link``. The
    ``_inputs``/``_outputs`` schema sidecars are also dropped here — they
    come from ``instance.inputs()``/``outputs()`` and will be rederived by
    the child when it instantiates the class.

    Walks dicts and lists; leaves non-container leaves untouched. Returns a
    new tree (does not mutate the input).
    """
    if isinstance(state, dict):
        out = {}
        is_edge = state.get('_type') in ('step', 'process')
        for k, v in state.items():
            if is_edge and k in ('instance', '_inputs', '_outputs'):
                continue
            out[k] = _strip_process_instances(v)
        return out
    if isinstance(state, list):
        return [_strip_process_instances(v) for v in state]
    return state


def _json_default(o):
    """JSON serialization fallback for objects json.dumps can't handle natively.

    Handles numpy arrays (which @composite_generator state docs often contain
    for spatial / field-based composites), numpy scalars, Path objects, sets,
    and anything with .tolist(). Falls back to repr() so a bad object still
    surfaces a string rather than killing the whole response.
    """
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

_REGISTRY_CACHE: dict = {"data": None, "ts": 0.0}
_REGISTRY_TTL = 30.0  # seconds

# SP4a linkage-index cache — keyed ("linkage", ws_root) → (built_at, index dict).
# The index is a pure derive over the workspace YAML; a short TTL keeps it cheap
# on repeat queries while still picking up YAML edits.
_LINKAGE_CACHE: dict = {}
_LINKAGE_TTL = 30.0  # seconds

# Cache of built composite-state payloads for /api/composite-state, keyed by ref:
# {ref: (built_at_epoch, payload_dict)}. Building a whole-cell composite is ~1s+;
# this makes repeat opens + pop-outs instant. Short TTL so code edits are picked up.
_COMPOSITE_STATE_CACHE: dict = {}
_COMPOSITE_STATE_TTL_S = 300.0

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
    "/api/references-fetch":    "_post_references_fetch",
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
    """Return registry data from build_core() subprocess, with 30s caching.

    Always returns {processes: [...], types: [...]} plus optional 'error' key.
    Each process entry includes a ``source`` field:
      - ``"in_workspace"`` — class belongs to the workspace's own package or a
        declared import (workspace.yaml.imports).
      - ``"framework"`` — class is from the process-bigraph framework infrastructure
        (process_bigraph, bigraph_schema, bigraph_viz, pbg_superpowers,
        vivarium_dashboard).
      - ``"environment_only"`` — discovered via allocate_core() entry-point scan
        but not declared in workspace.yaml.  Installed in the Python env but not
        explicitly imported by this workspace.
    Never raises.
    """
    global _REGISTRY_CACHE
    now = time.time()
    if not bypass_cache and _REGISTRY_CACHE["data"] is not None:
        if now - _REGISTRY_CACHE["ts"] < _REGISTRY_TTL:
            return _REGISTRY_CACHE["data"]

    try:
        ws_yaml = WORKSPACE / "workspace.yaml"
        ws_data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8"))
        slug = ws_data.get("name", "")
        # Support explicit package_path in workspace.yaml (most reliable).
        package_name = ws_data.get("package_path") or ("pbg_" + slug.replace("-", "_"))

        # Build the set of top-level package names that this workspace
        # explicitly owns or imports. Used inside the subprocess to tag
        # each discovered class.
        #
        # ``workspace.yaml.imports`` ships in two shapes across the
        # ecosystem:
        #   * dict (older convention, keyed by catalog name):
        #       imports:
        #         pbg-oxidizeme:
        #           package: pbg_oxidizeme
        #           source:  https://github.com/.../pbg-oxidizeme
        #   * list of dicts (v2ecoli + newer pbg-template workspaces):
        #       imports:
        #         - name:    pbg_oxidizeme
        #           source:  https://github.com/.../pbg-oxidizeme
        #
        # Normalize both into the loop so the registry endpoint doesn't
        # crash with "'list' object has no attribute 'items'" when the
        # workspace uses the list form.
        imports_raw = ws_data.get("imports") or []
        _ws_import_pkgs: list[str] = []
        if isinstance(imports_raw, dict):
            for cat_name, imp_val in imports_raw.items():
                if isinstance(imp_val, dict):
                    pkg = imp_val.get("package") or cat_name.replace("-", "_")
                else:
                    pkg = cat_name.replace("-", "_")
                _ws_import_pkgs.append(pkg.split(".")[0])
        elif isinstance(imports_raw, list):
            for entry in imports_raw:
                if isinstance(entry, dict):
                    # name is the catalog identity; package is the
                    # importable Python package name (defaults to name
                    # with dashes → underscores).
                    cat_name = entry.get("name") or ""
                    pkg = entry.get("package") or cat_name.replace("-", "_")
                elif isinstance(entry, str):
                    pkg = entry.replace("-", "_")
                else:
                    continue
                if pkg:
                    _ws_import_pkgs.append(pkg.split(".")[0])
        # Any other shape (e.g. None) yields no imports — registry just
        # shows the workspace's own package + framework classes.
        # The workspace's own package is always "in_workspace".
        _ws_import_pkgs.append(package_name.split(".")[0])
        # Dedupe while preserving insertion order.
        _workspace_pkgs_repr = repr(list(dict.fromkeys(_ws_import_pkgs)))

        py = sys.executable
        script = textwrap.dedent(f"""
import json, sys
try:
    from {package_name}.core import build_core
    core = build_core()

    import inspect as _inspect
    import process_bigraph as _pb
    EMITTER_CLS = getattr(_pb, 'Emitter', None)
    try:
        from pbg_superpowers.visualization import Visualization as VISUALIZATION_CLS
    except ImportError:
        VISUALIZATION_CLS = None

    # Packages declared in this workspace (own package + workspace.yaml imports).
    _WORKSPACE_PKGS = set({_workspace_pkgs_repr})
    # Framework infrastructure packages — always shown, never "environment_only".
    _FRAMEWORK_PKGS = {{
        'process_bigraph', 'bigraph_schema', 'bigraph_viz',
        'pbg_superpowers', 'vivarium_dashboard', 'pbg_emitters',
    }}

    def _classify_source(cls):
        try:
            top_pkg = cls.__module__.split('.')[0]
        except Exception:
            return 'environment_only'
        if top_pkg in _WORKSPACE_PKGS:
            return 'in_workspace'
        if top_pkg in _FRAMEWORK_PKGS:
            return 'framework'
        return 'environment_only'

    # Processes (and other linkable components) live in core.link_registry,
    # a dict keyed by both short names ('Composite') and fully-qualified
    # names ('process_bigraph.composite.Composite'). Dedupe by class identity
    # and prefer the short name.
    processes = []
    seen_classes = {{}}
    link_reg = getattr(core, 'link_registry', {{}}) or {{}}
    for name, cls in link_reg.items():
        cls_id = id(cls)
        is_qualified = '.' in name
        if cls_id in seen_classes:
            # already saw this class; only update if current name is shorter (preferred)
            existing = seen_classes[cls_id]
            if not is_qualified and '.' in processes[existing]['name']:
                processes[existing]['aliases'].append(processes[existing]['name'])
                processes[existing]['name'] = name
            else:
                processes[existing]['aliases'].append(name)
            continue
        try:
            addr = f"{{cls.__module__}}.{{cls.__qualname__}}"
        except Exception:
            addr = str(cls)
        # Categorize by ancestry
        kind = "other"
        if isinstance(cls, type):
            if EMITTER_CLS is not None and issubclass(cls, EMITTER_CLS) and cls is not EMITTER_CLS:
                kind = "emitter"
            elif VISUALIZATION_CLS is not None and issubclass(cls, VISUALIZATION_CLS) and cls is not VISUALIZATION_CLS:
                kind = "visualization"
            elif hasattr(cls, '__mro__'):
                for ancestor in cls.__mro__:
                    if ancestor.__name__ in ('Process', 'ProcessEnsemble'):
                        kind = "process"
                        break
                    if ancestor.__name__ == 'Step':
                        kind = "step"
                        break
        schema_preview = ""
        if hasattr(cls, 'config_schema'):
            try:
                schema_preview = json.dumps(cls.config_schema, default=str)[:400]
            except Exception:
                schema_preview = "<unserializable>"
        source = _classify_source(cls)
        # Framework hygiene: hide process_bigraph's OWN built-in toy/base/protocol
        # processes (examples, parameter_scan, math_expression, growth_division,
        # minimal_gillespie, the composite base classes, ray/parallel/rest
        # protocols) from every workspace's registry — they are framework
        # infrastructure, not workspace content. Emitters + visualizations are
        # kept (useful framework components a workspace wires in).
        _topmod = (getattr(cls, '__module__', '') or '').split('.')[0]
        if _topmod == 'process_bigraph' and kind in ('process', 'step', 'other'):
            continue
        # Hide abstract base classes (e.g. pbg_emitters' BufferedEmitter) — they
        # are intermediate bases not meant to be used directly, not registry
        # content.
        try:
            if isinstance(cls, type) and _inspect.isabstract(cls):
                continue
        except Exception:
            pass
        seen_classes[cls_id] = len(processes)
        processes.append({{
            "name": name,
            "address": addr,
            "kind": kind,
            "schema_preview": schema_preview,
            "aliases": [],
            "source": source,
        }})
    # Re-sort by name so output is deterministic; promote short names.
    # Within each source group: in_workspace first, then framework, then environment_only.
    _source_order = {{"in_workspace": 0, "framework": 1, "environment_only": 2}}
    processes.sort(key=lambda p: (_source_order.get(p.get('source', 'environment_only'), 2), '.' in p['name'], p['name']))

    # Types: core.registry is a dict of registered type schemas.
    types = []
    type_reg = getattr(core, 'registry', {{}}) or {{}}
    for name in sorted(type_reg.keys()):
        try:
            td = core.access(name)
            preview = str(td)[:200] if td is not None else ""
        except Exception as e:
            preview = f"<error: {{e}}>"
        types.append({{"name": name, "schema_preview": preview}})

    print(json.dumps({{"processes": processes, "types": types, "workspace_pkgs": list(_WORKSPACE_PKGS)}}))
except ImportError as e:
    print(json.dumps({{"error": f"could not import {package_name}.core: {{e}}", "processes": [], "types": []}}))
except Exception as e:
    print(json.dumps({{"error": f"build_core() failed: {{e}}", "processes": [], "types": []}}))
""")
        result = subprocess.run(
            [py, "-c", script],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            data: dict = {
                "error": f"subprocess failed: {(result.stderr or '').strip()[:300]}",
                "processes": [],
                "types": [],
            }
        else:
            try:
                last_line = result.stdout.strip().split("\n")[-1]
                data = json.loads(last_line)
            except (json.JSONDecodeError, IndexError):
                data = {
                    "error": f"invalid output: {result.stdout[:300]}",
                    "processes": [],
                    "types": [],
                }

        # Annotate emitter entries with is_workspace_default per
        # workspace.yaml::runtime.default_emitter. ws_data was loaded above;
        # treat the emitter-name match permissively (case-insensitive substring
        # against the class name, e.g. 'parquet' → ParquetEmitter).
        _mark_default_emitter(data, ws_data)
        # Optional display-only allow-list: workspace.yaml::dashboard.registry.include.
        # When set, the Registry tab shows ONLY classes whose originating package
        # is in the list (discovery is unchanged). No-op when unset → current
        # behavior (show everything).
        _apply_registry_include_filter(data, ws_data)
    except Exception as e:
        data = {"error": str(e), "processes": [], "types": []}

    _REGISTRY_CACHE["data"] = data
    _REGISTRY_CACHE["ts"] = now
    return data


def _mark_default_emitter(data: dict, ws_data: dict | None) -> None:
    """Set ``is_workspace_default: True`` on emitter entries that match
    ``ws_data['runtime']['default_emitter']``.

    The match is a case-insensitive substring check against the entry's
    ``name`` (e.g. ``'parquet'`` matches ``ParquetEmitter``). All emitter
    entries get the field set explicitly (True or False) so the frontend
    can render the badge consistently. No-op when ``ws_data`` is missing
    or has no runtime block.
    """
    if not isinstance(data, dict):
        return
    processes = data.get("processes") or []
    default_emitter = ""
    if isinstance(ws_data, dict):
        rt = ws_data.get("runtime") or {}
        if isinstance(rt, dict):
            default_emitter = str(rt.get("default_emitter") or "").strip().lower()
    needle = default_emitter
    for p in processes:
        if not isinstance(p, dict):
            continue
        if p.get("kind") != "emitter":
            continue
        name = str(p.get("name") or "")
        p["is_workspace_default"] = bool(needle) and (needle in name.lower())
    # Expose the resolved value at the top level for convenience / debugging.
    data["default_emitter"] = default_emitter or None


def _dashboard_config(ws_data: dict | None) -> dict:
    """Return the ``dashboard:`` block from workspace.yaml as a dict (or {}).

    The block is the single source for per-workspace dashboard customization::

        dashboard:
          name: "sms-ecoli dashboard"        # header/brand + <title>
          logo: assets/sms-ecoli-logo.png    # workspace-relative logo file
          registry:
            include: [pkg-a, pkg-b]           # display allow-list (by package)

    All keys optional; missing block → {} → current default behavior.
    """
    if not isinstance(ws_data, dict):
        return {}
    dash = ws_data.get("dashboard")
    return dash if isinstance(dash, dict) else {}


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
# Enumeration is cached (same 30s TTL as the registry) because it can stat
# 100s of files. The server already runs inside the workspace venv (it spawns
# build_core() via sys.executable), so the provider imports in-process.
_DATA_SOURCES_CACHE: dict = {"data": None, "ts": 0.0}
_DATA_SOURCES_TTL = 30.0  # seconds


def _data_sources_config(ws_data: dict | None) -> dict:
    """Return the ``dashboard.data_sources`` block (or {})."""
    dash = _dashboard_config(ws_data)
    ds = dash.get("data_sources")
    return ds if isinstance(ds, dict) else {}


def _import_provider(spec: str):
    """Import a ``module:func`` spec and return the callable.

    Raises ImportError/AttributeError/ValueError on a malformed or unresolvable
    spec; the caller is expected to catch and surface as an error payload.
    """
    import importlib
    if ":" not in spec:
        raise ValueError(f"provider must be 'module:func', got {spec!r}")
    mod_name, _, func_name = spec.partition(":")
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, func_name)
    if not callable(fn):
        raise TypeError(f"provider {spec!r} is not callable")
    return fn


def _enumerate_data_sources(bypass_cache: bool = False) -> dict:
    """Resolve + invoke the workspace data-source provider, with 30s caching.

    Always returns ``{"label": <str|None>, "sources": [...]}`` (never raises).
    On a missing provider returns ``{"sources": []}``. On a provider error
    returns ``{"label", "sources": [], "error": <str>}`` so the UI can degrade.
    """
    global _DATA_SOURCES_CACHE
    now = time.time()
    if not bypass_cache and _DATA_SOURCES_CACHE["data"] is not None:
        if now - _DATA_SOURCES_CACHE["ts"] < _DATA_SOURCES_TTL:
            return _DATA_SOURCES_CACHE["data"]

    data: dict
    try:
        ws_yaml = WORKSPACE / "workspace.yaml"
        ws_data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) or {}
        cfg = _data_sources_config(ws_data)
        provider = str(cfg.get("provider") or "").strip()
        label = cfg.get("label")
        if not provider:
            data = {"sources": []}
        else:
            fn = _import_provider(provider)
            raw = fn() or []
            sources = []
            for entry in raw:
                if not isinstance(entry, dict) or "key" not in entry:
                    continue
                sources.append({
                    "key": str(entry.get("key")),
                    "path": str(entry.get("path") or ""),
                    "category": str(entry.get("category") or "uncategorized"),
                    "kind": str(entry.get("kind") or "inherited"),
                    "size_bytes": int(entry.get("size_bytes") or 0),
                    # Optional external link (e.g. a GitHub raw URL for an
                    # inherited source). When present the SPA renders a
                    # hyperlink — the ONLY working access path in the published
                    # static snapshot (the /api/data-source-file "Open" button
                    # is server-only).
                    "url": str(entry.get("url") or ""),
                })
            data = {"label": label, "sources": sources}
    except Exception as e:  # noqa: BLE001 — never break the dashboard
        data = {"label": None, "sources": [], "error": f"{type(e).__name__}: {e}"}

    _DATA_SOURCES_CACHE["data"] = data
    _DATA_SOURCES_CACHE["ts"] = now
    return data


# Map of file extension → (content-type, inline?) for serving a data-source
# file. Anything not listed is offered as a binary download.
_DATA_SOURCE_MIME: dict[str, tuple[str, bool]] = {
    ".tsv": ("text/tab-separated-values; charset=utf-8", True),
    ".csv": ("text/csv; charset=utf-8", True),
    ".json": ("application/json; charset=utf-8", True),
    ".txt": ("text/plain; charset=utf-8", True),
    ".text": ("text/plain; charset=utf-8", True),
    ".md": ("text/markdown; charset=utf-8", True),
    ".fasta": ("text/plain; charset=utf-8", True),
    ".fa": ("text/plain; charset=utf-8", True),
    ".fna": ("text/plain; charset=utf-8", True),
    ".faa": ("text/plain; charset=utf-8", True),
    ".yaml": ("text/yaml; charset=utf-8", True),
    ".yml": ("text/yaml; charset=utf-8", True),
}


def _registry_modules_override(ws_data: dict | None) -> list | None:
    """Resolve ``dashboard.registry.modules`` to a list of entries, or ``None``.

    The ``modules`` block is the per-workspace catalog OVERRIDE: when present
    and non-empty it REPLACES pbg's default catalog (unlike ``include``, which
    only filters the default). Each entry is either:

      - a bare string  → the name of an entry in pbg's default catalog whose
        full metadata should be inherited (or a minimal stub if pbg doesn't
        ship it); or
      - a dict         → a custom catalog module that pbg doesn't ship
        (e.g. ``viva-munk``), used verbatim with missing display fields filled.

    Returns ``None`` when unset/not-a-list/empty → caller falls back to the
    default catalog + ``include`` filter (unchanged behavior).
    """
    dash = _dashboard_config(ws_data)
    reg = dash.get("registry")
    if not isinstance(reg, dict):
        return None
    modules = reg.get("modules")
    if not isinstance(modules, list) or not modules:
        return None
    return modules


def _modules_override_pkgs(ws_data: dict | None) -> set[str] | None:
    """Normalized top-level package names named by ``dashboard.registry.modules``.

    Used so the process-registry (``/api/registry``) filter shows the SAME set
    as the override catalog even when no explicit ``include`` is present. For a
    string entry the package is the name itself; for a dict entry the ``package``
    field (falling back to the snake_case ``name``). Returns ``None`` when no
    override is configured.
    """
    modules = _registry_modules_override(ws_data)
    if modules is None:
        return None

    def _norm(s) -> str:
        return str(s or "").strip().replace("-", "_").split(".")[0]

    pkgs: set[str] = set()
    for entry in modules:
        if isinstance(entry, str):
            n = _norm(entry)
            if n:
                pkgs.add(n)
        elif isinstance(entry, dict):
            pkg = entry.get("package") or entry.get("name")
            n = _norm(pkg)
            if n:
                pkgs.add(n)
    return pkgs or None


def _registry_include_pkgs(ws_data: dict | None) -> set[str] | None:
    """Resolve ``dashboard.registry.include`` to a set of normalized top-level
    package names (dashes → underscores), or ``None`` when unset.

    ``None`` means "no filter" (show everything — current behavior); an empty
    list also means no filter (treated as unset, to avoid an accidental
    blank registry).

    When ``dashboard.registry.modules`` (the catalog override) is present but
    no explicit ``include`` is given, the allow-list is DERIVED from the module
    names — so the process-registry class grid stays in sync with the override
    catalog (same set: workspace-self + each declared module).
    """
    dash = _dashboard_config(ws_data)
    reg = dash.get("registry")
    if not isinstance(reg, dict):
        return None
    include = reg.get("include")
    if not isinstance(include, list) or not include:
        # No explicit include: derive from the modules override (if any) so the
        # process registry matches the override catalog. The workspace's own
        # package is always allowed alongside the declared modules.
        derived = _modules_override_pkgs(ws_data)
        if derived is None:
            return None
        slug = str((ws_data or {}).get("name", "") or "").strip().replace("-", "_")
        pkg_path = str((ws_data or {}).get("package_path", "") or "").strip().replace("-", "_")
        for s in (slug, pkg_path):
            if s:
                derived.add(s)
        return derived or None
    pkgs = {
        str(p).strip().replace("-", "_").split(".")[0]
        for p in include
        if str(p).strip()
    }
    return pkgs or None


def _build_reexport_map(include: set[str]) -> dict[str, str]:
    """Map re-exported classes → the allow-listed package that re-exports them.

    For each allow-listed package, import it and scan its top-level namespace
    (``dir(mod)``) for classes whose ``__module__`` top-level segment is a
    DIFFERENT package. Those are re-exports: a class defined elsewhere (e.g.
    ``spatio_flux.visualizations.field_heatmap.FieldHeatmap``) that the
    allow-listed package surfaces as part of its own API (e.g. exposed as
    ``viva_munk.FieldHeatmap``).

    The returned map is keyed by the class's full definition address
    (``def_module + '.' + qualname``, e.g.
    ``spatio_flux.visualizations.field_heatmap.FieldHeatmap``) AND, as a
    looser fallback, by ``(def_top_pkg, class_name)`` joined as
    ``"<def_top_pkg>::<name>"``. The value is the re-exporting package's
    normalized name (e.g. ``viva_munk``).

    Imports are guarded with try/except — a single bad import never blanks the
    registry; the worst case is a class is not surfaced. The allow-listed set is
    small (a handful of packages) so importing them here is cheap.
    """
    import importlib
    import inspect

    # Framework infrastructure packages are intentionally hidden from the
    # filtered registry; do NOT resurrect them as re-exports just because an
    # allow-listed package re-imports e.g. process_bigraph.Composite into its
    # namespace. Mirrors _FRAMEWORK_PKGS in the registry subprocess.
    _FRAMEWORK_PKGS = {
        "process_bigraph", "bigraph_schema", "bigraph_viz",
        "pbg_superpowers", "vivarium_dashboard",
    }

    reexports: dict[str, str] = {}
    for pkg in sorted(include):
        try:
            mod = importlib.import_module(pkg)
        except Exception:
            continue
        for attr in dir(mod):
            try:
                obj = getattr(mod, attr)
            except Exception:
                continue
            if not inspect.isclass(obj):
                continue
            def_mod = getattr(obj, "__module__", "") or ""
            def_top = def_mod.split(".")[0].replace("-", "_")
            if not def_top or def_top == pkg:
                continue  # defined in the re-exporting package itself → not a re-export
            if def_top in include:
                continue  # already surfaced by its own allow-listed package
            if def_top in _FRAMEWORK_PKGS:
                continue  # framework infra stays hidden; not a workspace re-export
            qualname = getattr(obj, "__qualname__", attr) or attr
            full_addr = f"{def_mod}.{qualname}"
            reexports[full_addr] = pkg
            reexports[f"{def_top}::{qualname}"] = pkg
    return reexports


def _apply_registry_include_filter(data: dict, ws_data: dict | None) -> None:
    """Filter ``data['processes']`` to only classes from allow-listed packages.

    Display-only: matches each entry's originating top-level package (derived
    from its ``address`` = ``module.qualname``, falling back to the entry
    ``name`` if it is dotted) against the normalized
    ``dashboard.registry.include`` set. Dashes/underscores are normalized on
    both sides (``pbg-bioreactordesign`` ↔ ``pbg_bioreactordesign``).

    Re-exports are honored: a class DEFINED in a non-allow-listed package but
    RE-EXPORTED in an allow-listed package's top-level namespace (e.g.
    ``viva_munk.FieldHeatmap``, defined in ``spatio_flux``) survives the filter
    and is re-attributed to the re-exporting package — its ``source`` becomes
    ``in_workspace`` and its top-level package tag flips to the re-exporter, so
    the UI groups it under (e.g.) viva_munk rather than spatio_flux. The true
    definition module is preserved in ``aliases`` so the attribution is not
    misleading. Classes from a non-allow-listed package that are NOT re-exported
    stay filtered out.

    No-op when no include list is configured (current behavior: show all).
    Allow-listed packages surface regardless of in_workspace/framework/
    environment_only classification.
    """
    if not isinstance(data, dict):
        return
    include = _registry_include_pkgs(ws_data)
    if include is None:
        return

    def _top_pkg(entry: dict) -> str:
        addr = str(entry.get("address") or "")
        mod = addr
        # address is "module.path.ClassName"; the module is everything we have,
        # but the qualname tail is the class. The top-level package is just the
        # first dotted segment, so we can take it directly from the address.
        if not mod:
            mod = str(entry.get("name") or "")
        return mod.split(".")[0].replace("-", "_")

    # Build the re-export map (guarded so a bad import never blanks the grid).
    try:
        reexports = _build_reexport_map(include)
    except Exception:
        reexports = {}

    def _reexporter(entry: dict) -> str | None:
        """Return the allow-listed pkg that re-exports this entry, else None."""
        if not reexports:
            return None
        addr = str(entry.get("address") or "").strip()
        if addr and addr in reexports:
            return reexports[addr]
        # Looser match: definition top-level package + class name. The class
        # name is the last segment of the address (or the entry name).
        def_top = _top_pkg(entry)
        cls_name = addr.split(".")[-1] if addr else str(entry.get("name") or "")
        key = f"{def_top}::{cls_name}"
        return reexports.get(key)

    procs = data.get("processes") or []
    kept: list[dict] = []
    for p in procs:
        if not isinstance(p, dict):
            continue
        own_pkg = _top_pkg(p)
        if own_pkg in include:
            kept.append(p)
            continue
        # Always surface emitters regardless of the include allow-list. They are
        # the workspace's I/O backends (the configured runtime.default_emitter is
        # one of them) and live in framework/env packages (process_bigraph,
        # pbg_emitters) outside the include list — so a repo-scoped include like
        # [v2ecoli] would otherwise leave the Registry's Emitters section empty.
        if p.get("kind") == "emitter":
            kept.append(p)
            continue
        reexporter = _reexporter(p)
        if reexporter is not None:
            # Re-attribute to the re-exporting package: keep the true definition
            # module in aliases (so it is not misleading), flip the address's
            # top-level segment and source classification to the re-exporter.
            true_addr = str(p.get("address") or "")
            aliases = list(p.get("aliases") or [])
            if true_addr and true_addr not in aliases:
                aliases.append(true_addr)
            p["aliases"] = aliases
            p["reexported_from"] = own_pkg
            p["source"] = "in_workspace"
            # Re-tag the address's top-level package so _top_pkg / the UI group
            # it under the re-exporter. The class is re-exported as
            # ``<reexporter>.<ClassName>``.
            cls_name = true_addr.split(".")[-1] if true_addr else str(p.get("name") or "")
            p["address"] = f"{reexporter}.{cls_name}"
            kept.append(p)
    data["processes"] = kept
    # Record what was applied for debugging / frontend awareness.
    data["registry_include"] = sorted(include)


def _filter_catalog_modules(modules: list, ws_data: dict | None) -> list:
    """Apply ``dashboard.registry.include`` to the package catalog (/api/catalog).

    Same allow-list, same normalization as the registry filter
    (``_apply_registry_include_filter``): dashes ↔ underscores, top-level
    package segment only. A catalog module's package identity is matched
    against any of its name variants — ``name`` (e.g. ``pbg-bioreactordesign``,
    ``spatio-flux``), ``pypi_name``, and ``package`` (the snake_case import
    name) — so e.g. ``viva-munk`` ↔ ``viva_munk`` and the workspace's own
    first-party module (``kind: "workspace"``, ``name`` = slug = ``v2ecoli``)
    all resolve correctly.

    No-op when no include list is configured (returns ``modules`` unchanged →
    current behavior: show the full catalog).
    """
    if not isinstance(modules, list):
        return modules
    include = _registry_include_pkgs(ws_data)
    if include is None:
        return modules

    def _norm(s) -> str:
        return str(s or "").strip().replace("-", "_").split(".")[0]

    def _allowed(m: dict) -> bool:
        if not isinstance(m, dict):
            return False
        variants = {_norm(m.get("name"))}
        if m.get("pypi_name"):
            variants.add(_norm(m.get("pypi_name")))
        # `package` may be absent; fall back to name→snake_case like elsewhere.
        pkg = m.get("package") or str(m.get("name") or "").replace("-", "_")
        variants.add(_norm(pkg))
        variants.discard("")
        return bool(variants & include)

    # Always keep modules that are actually INSTALLED in this workspace — the
    # catalog's job is to show what's active here, and "modules active in this
    # workspace appear at the top" is its stated contract. The include allow-list
    # only governs which *non-installed* (available-to-install) modules also
    # surface. (Without this, `registry.include: [v2ecoli]` hid the workspace's
    # own installed deps — pbg-emitters, viva-munk, … — leaving only v2ecoli.)
    return [m for m in modules if _allowed(m) or m.get("installed")]


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


def _build_override_catalog(override: list, default_modules: list) -> list:
    """Build a catalog from ``dashboard.registry.modules`` (the override).

    The override REPLACES pbg's default catalog. ``default_modules`` is pbg's
    default catalog (``load_registry`` + workspace overlay) used only to resolve
    bare-string entries by inheriting their full metadata.

    Resolution per entry:

      - **string** → look the name up in ``default_modules``; if found, deep-copy
        its full metadata dict; if NOT found, emit a minimal stub
        (``name`` + a short ``description`` note) so the row still renders.
      - **dict** → a custom module pbg doesn't ship; used verbatim with missing
        display fields filled with sensible defaults so the row renders and the
        Install/Uninstall button works (needs at least ``name``; ``package``
        defaults to the snake_case name; ``source``/``description``/``tags`` get
        placeholder fallbacks).

    Install-state is NOT set here — the caller's existing install-detection loop
    (imports / pyproject / venv probe) annotates each entry, so a custom entry
    whose ``package`` is importable in the venv (e.g. ``viva_munk``) is marked
    installed exactly like a default-catalog entry.

    Order is preserved from the override list. Unrecognized entry types are
    skipped.
    """
    by_name = {
        str(m.get("name")): m
        for m in (default_modules or [])
        if isinstance(m, dict) and m.get("name")
    }
    out: list[dict] = []
    seen: set[str] = set()

    for entry in override:
        if isinstance(entry, str):
            name = entry.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            found = by_name.get(name)
            if found is not None:
                out.append(copy.deepcopy(found))
            else:
                # pbg doesn't ship this name — minimal stub so it still renders.
                out.append({
                    "name": name,
                    "package": name.replace("-", "_"),
                    "description": (
                        f"{name} — declared in this workspace's "
                        "dashboard.registry.modules but not found in the default "
                        "pbg catalog (no inherited metadata)."
                    ),
                    "tags": [],
                    "override_stub": True,
                })
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            m = copy.deepcopy(entry)
            m.setdefault("package", name.replace("-", "_"))
            m.setdefault(
                "description",
                f"{name} — custom workspace catalog entry.",
            )
            m.setdefault("source", "")
            m.setdefault("tags", [])
            # Surface a single-tag category as a tag too (display convenience).
            cat = m.get("category")
            if cat and isinstance(m.get("tags"), list) and cat not in m["tags"]:
                m["tags"] = list(m["tags"]) + [cat]
            m["override_custom"] = True
            out.append(m)
        # else: unknown entry type → skip silently.

    return out


def _build_reexport_origin_modules(
    ws_data: dict | None, existing_modules: list
) -> list[dict]:
    """Synthesize catalog entries for re-export-ORIGIN packages.

    A re-export origin is a package that is (a) NOT in the registry allow-list
    itself, but (b) has ≥1 class re-exported by an allow-listed package (per
    :func:`_build_reexport_map`). The canonical example: ``spatio_flux`` is not
    allow-listed, but ``viva_munk`` re-exports 7 of its classes into its own
    top-level namespace — so spatio-flux is a genuine dependency of an
    allow-listed package and should be SHOWN in the catalog (tagged
    ``📦 via viva-munk``) rather than fully hidden.

    For each such origin package we emit one catalog entry stamped with
    ``install_source: "venv"`` + ``installed_via: [<allow-listed re-exporters>]``
    so the install-source badge renders ``📦 via <parents>`` and the UI shows
    "(remove parent to uninstall)" instead of an Install button. This
    install_source attribution is DELIBERATELY forced to the re-exporter(s)
    even when the package is also a direct pyproject dependency of the
    workspace (e.g. v2ecoli pins spatio-flux): the meaningful reason it appears
    in this filtered catalog is the re-export, per v2ecoli's own pyproject
    comment.

    Guarded: returns ``[]`` unless a registry allow-list is configured AND the
    re-export map yields at least one origin package. Origin packages already
    present in ``existing_modules`` (by name/package variant) are skipped so we
    never duplicate or shadow a primary catalog entry.
    """
    include = _registry_include_pkgs(ws_data)
    if include is None:
        return []
    try:
        reexports = _build_reexport_map(include)
    except Exception:
        return []
    if not reexports:
        return []

    def _norm(s) -> str:
        return str(s or "").strip().replace("-", "_").split(".")[0]

    # Collect, per origin package, the set of allow-listed re-exporters.
    # Map keys are either ``def_module.qualname`` (full address) or
    # ``"<def_top_pkg>::<name>"``; the value is the re-exporting package. Only
    # the ``::`` keys cleanly expose the origin top-level package, so derive
    # origins from those.
    # Stdlib / builtin module names are NOT installable workspace packages —
    # an allow-listed package re-importing e.g. ``typing.TypeVar`` or
    # ``dataclasses.dataclass`` into its namespace must not surface a bogus
    # "typing" catalog row. Mirror the framework-pkg guard in _build_reexport_map
    # for the standard library.
    import sys as _sys
    _stdlib = set(getattr(_sys, "stdlib_module_names", ()))
    _builtins = set(_sys.builtin_module_names)

    origins: dict[str, set[str]] = {}
    for key, reexporter in reexports.items():
        if "::" not in key:
            continue
        def_top = _norm(key.split("::", 1)[0])
        if not def_top or def_top in include:
            continue
        if def_top in _stdlib or def_top in _builtins:
            continue
        origins.setdefault(def_top, set()).add(reexporter)
    if not origins:
        return []

    # Don't duplicate a package that the override catalog already lists.
    existing: set[str] = set()
    for m in existing_modules or []:
        if not isinstance(m, dict):
            continue
        existing.add(_norm(m.get("name")))
        if m.get("pypi_name"):
            existing.add(_norm(m.get("pypi_name")))
        existing.add(_norm(m.get("package") or m.get("name")))
    existing.discard("")

    out: list[dict] = []
    for origin_pkg in sorted(origins):
        if origin_pkg in existing:
            continue
        # Re-exporters as their catalog display names (dash form for the badge).
        parents = sorted(p.replace("_", "-") for p in origins[origin_pkg])
        display_name = origin_pkg.replace("_", "-")
        out.append({
            "name": display_name,
            "package": origin_pkg,
            "description": (
                f"Re-exported by {', '.join(parents)} "
                "(particles + visualizations)."
            ),
            "tags": ["dependency", "re-export"],
            "category": "dependency",
            "installed": True,
            # Force the venv/via-parent attribution even if this package is also
            # a direct pyproject dep — the reason it's surfaced here is the
            # re-export, not the direct pin.
            "install_source": "venv",
            "installed_via": parents,
            "reexport_origin": True,
        })
    return out


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

    Reads workspace.yaml + enumerates investigation dirs.  Pure (no socket I/O).
    Returned dict shape: {name, description, imports, investigations:[...]}.
    """
    ws_root = Path(ws_root) if ws_root is not None else Path(WORKSPACE)
    wp = WorkspacePaths.load(ws_root)
    ws: dict = {}
    wf = ws_root / "workspace.yaml"
    if wf.exists():
        try:
            ws = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        except Exception:
            ws = {}

    investigations: list[dict] = []
    inv_root = wp.investigations
    if inv_root.is_dir():
        for inv_dir in sorted(
            d for d in inv_root.iterdir()
            if d.is_dir() and (d / "investigation.yaml").is_file()
        ):
            try:
                inv_spec = yaml.safe_load(
                    (inv_dir / "investigation.yaml").read_text(encoding="utf-8")
                ) or {}
                investigations.append({
                    "name":        inv_spec.get("name", inv_dir.name),
                    "title":       inv_spec.get("title") or inv_spec.get("name") or inv_dir.name,
                    "status":      inv_spec.get("status", "planning"),
                    "description": inv_spec.get("description", ""),
                })
            except Exception:
                investigations.append({"name": inv_dir.name, "status": "error"})

    return {
        "name":           ws.get("name", ws_root.name),
        "description":    ws.get("description", ""),
        "imports":        ws.get("imports") or {},
        "investigations": investigations,
    }


# ---------------------------------------------------------------------------
# Study slug validation
# ---------------------------------------------------------------------------

# Study/investigation names are generated with underscores (e.g. derived from
# composite names like ``monod_kinetics``), so the slug pattern allows ``_``
# alongside ``-``. Still anchored to alphanumerics at both ends, which keeps
# out path traversal (``..``, ``/``, leading dots).
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")

# ---------------------------------------------------------------------------
# Study / investigation directory resolution helpers
# ---------------------------------------------------------------------------


def _study_dir(name: str):
    """Resolve a study directory, preferring the v3 ``studies/`` location
    over the legacy ``investigations/`` location.

    Uses ``WorkspacePaths.study_dir`` as the primary lookup (handles nested
    ``investigations/<inv>/studies/<slug>/`` layouts used by workspaces with a
    custom ``layout:`` map in ``workspace.yaml``, e.g. v2e-invest).  Falls back
    to the flat ``investigations/<name>/`` path for callers that reference a
    pre-Phase-1 spec.yaml that is not discovered by ``iter_study_dirs``.
    """
    try:
        return workspace_paths().study_dir(name)
    except FileNotFoundError:
        pass
    # Guard: flat studies/<name>/ exists but has only spec.yaml (no study.yaml),
    # so iter_study_dirs() skipped it.  Return it rather than falling back to
    # the legacy investigations/<name> location.
    flat_candidate = workspace_paths().studies / name
    if flat_candidate.is_dir():
        return flat_candidate
    return workspace_paths().investigations / name


def _study_spec_file(study_dir):
    """Path-based variant of :func:`_study_spec_path` for handlers that already
    have a ``study_dir`` (e.g. ``*_for_test`` callers that take ``ws_root``
    explicitly rather than using the WORKSPACE global).

    Prefers ``study.yaml`` (v3 convention) when present, falls back to legacy
    ``spec.yaml``. Returns ``study_dir / "study.yaml"`` as the not-found
    default so callers' ``is_file()`` checks behave the same as before.
    """
    study_yaml = study_dir / "study.yaml"
    if study_yaml.is_file():
        return study_yaml
    spec_yaml = study_dir / "spec.yaml"
    if spec_yaml.is_file():
        return spec_yaml
    return study_yaml


def _study_spec_path(name: str):
    """Resolve a study's spec file: ``study.yaml`` (v3) or ``spec.yaml`` (legacy)."""
    return _study_spec_file(_study_dir(name))


# ---------------------------------------------------------------------------
# Pathway Tools Omics Viewer launch helper
# ---------------------------------------------------------------------------

# DEFAULT TEMPLATE — the Pathway Tools Cellular Overview Omics Viewer auto-loads
# a tab-delimited data file via URL params (verified against sms-ptools 0.8.2;
# the param format is documented in the server's celOverviewHelp.shtml):
#   omics=t        enable the Omics Viewer overlay
#   url=<datafile> the data file to paint (reachable BY the PTools server)
#   class=<cls>    object type of the rows: gene | reaction | protein | compound
#   column1=<N|a-b> data column(s); a range (e.g. 1-6) animates across timepoints
# Placeholders: {server}, {orgid}, {tsv_url}, {cls}, {columns}.  Override with
# ``ui.ptools_omics_url_template`` in workspace.yaml if your PTools build differs.
_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE = (
    "{server}/overviewsWeb/celOv.shtml"
    "?omics=t&url={tsv_url}&orgid={orgid}&class={cls}&column1={columns}"
)


def _ptools_object_class(name: str) -> str:
    """Infer the Pathway Tools object class from an analysis/TSV name.

    The Omics Viewer needs to know whether the row IDs are genes, reactions,
    proteins, or compounds.  v2ecoli's ptools analyses are named accordingly
    (ptools_rna → genes, ptools_rxns → reactions, ptools_proteins → proteins).
    """
    n = name.lower()
    if "rxn" in n or "reaction" in n:
        return "reaction"
    if "protein" in n:
        return "protein"
    if "metabolite" in n or "compound" in n:
        return "compound"
    return "gene"  # rna / default


def _build_ptools_launch_url(
    study_dir,
    ws_root,
    ptools_server_url: str,
    ptools_omics_url_template: str,
    public_base: str,
    run_id: str | None = None,
    analysis: str | None = None,
) -> dict:
    """Pure helper: discover ptools TSVs and build a Pathway Tools Omics Viewer URL.

    Returns a dict with keys:
      - ``url`` + ``tsv_url`` + ``available`` on success
      - ``error`` + optional ``available`` on failure

    The Pathway Tools server must be able to reach ``tsv_url`` over HTTP —
    it must be an absolute URL on the dashboard's externally-reachable host,
    not a localhost/relative path.
    """
    study_dir = Path(study_dir)
    ws_root = Path(ws_root)

    # Discover all ptools TSVs under the study directory.
    all_tsvs = sorted(study_dir.glob("**/ptools/*.tsv"))

    # Filter by analysis prefix when requested.
    if analysis:
        prefix = f"{analysis}__"
        all_tsvs = [p for p in all_tsvs if p.name.startswith(prefix)]

    # Build workspace-relative paths for the static handler + available list.
    def _relpath(p):
        try:
            return p.relative_to(ws_root).as_posix()
        except ValueError:
            return p.as_posix()

    available = [_relpath(p) for p in all_tsvs]

    if not available:
        return {"error": "no ptools TSVs found for this run", "available": []}

    # Use the first available TSV (most useful when analysis is filtered).
    chosen = all_tsvs[0]
    rel = available[0]
    tsv_url = f"{public_base.rstrip('/')}/{rel}"

    # Object class for the overlay (gene/reaction/protein/compound).
    cls = _ptools_object_class(analysis or chosen.name)

    # Animate across every data column: count the timepoint columns from the
    # first non-comment line (the ptools TSVs carry a ``$``-prefixed header row
    # whose remaining fields are the timepoints).  ``column1=1-N`` animates.
    columns = "1"
    try:
        for line in chosen.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith(("#", ";")):
                continue
            ncol = len(line.split("\t")) - 1  # minus the name/ID column
            columns = f"1-{ncol}" if ncol > 1 else "1"
            break
    except Exception:
        pass

    launch_url = ptools_omics_url_template.format(
        server=ptools_server_url.rstrip("/"),
        tsv_url=tsv_url,
        orgid="ECOLI",
        cls=cls,
        columns=columns,
    )
    return {"url": launch_url, "tsv_url": tsv_url, "available": available}


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
                from pathlib import Path as _P
                for r in mruns:
                    store = (r.get("emitter") or {}).get("store") or r.get("store")
                    if not store:
                        continue
                    summ = _run_store_summary(_P(ws_root) / store)
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
    from vivarium_dashboard.lib.investigations import load_spec
    spec_path = _study_spec_path(name)
    if not spec_path.is_file():
        return None
    spec = load_spec(spec_path)
    if isinstance(spec, dict):
        try:
            db_runs = _read_runs_db_for_study(name)
        except Exception:
            db_runs = []
        if db_runs:
            existing_ids = {(r or {}).get("run_id") for r in (spec.get("runs") or [])}
            merged = list(spec.get("runs") or [])
            for r in db_runs:
                if r.get("run_id") not in existing_ids:
                    merged.append(r)
            spec["runs"] = merged

        # Reconcile the simulation_set with the actual runs so the Simulations
        # tab reflects current status (seeds / duration / run-count / ran) rather
        # than the authored-or-synthesized plan's "? min / not set / ready".
        try:
            spec["simulation_set"] = _reconcile_simset_with_runs(
                spec.get("simulation_set"), spec.get("runs"), ws_root=WORKSPACE)
            # Fill the rest of each entry's promise: condition + tests applied.
            _cond = (spec.get("condition") or spec.get("media")
                     or (spec.get("model_change") or {}).get("condition"))
            _ntests = len(spec.get("tests") or spec.get("behavior_tests") or [])
            for _e in (spec.get("simulation_set") or []):
                if not isinstance(_e, dict):
                    continue
                if _cond and not _e.get("condition"):
                    _e["condition"] = _cond
                if _ntests and not _e.get("n_tests_applied"):
                    _e["n_tests_applied"] = _ntests
        except Exception:  # noqa: BLE001
            pass

        # Auto-discover any pre-rendered Plotly HTML files at
        # studies/<name>/viz/*.html (produced by render_visualizations
        # after a CLI- or dashboard-launched run). They get surfaced on
        # the Visualizations tab as embed_visualizations entries — no
        # manual study.yaml edit required.
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

        # Param-enforcement gate (expert-feedback D.2): if the study declares
        # `enforced_params`, verify the latest run actually applied them.
        # Surfaces "declared but not applied" as structured violations the
        # report renders as a banner, instead of the silent default-use the
        # reviewer caught. Best-effort — never breaks the study response.
        try:
            spec["param_enforcement"] = _compute_param_enforcement(spec)
        except Exception:  # noqa: BLE001
            pass

        # Imported expert feedback (expert-feedback B.1): attach any
        # annotations a reviewer left on this study's sections so the report
        # shows them back in-context, closing the loop. Best-effort.
        try:
            fb = _collect_study_feedback(name)
            if fb:
                spec["expert_feedback"] = fb
        except Exception:  # noqa: BLE001
            pass

        # Stage-3c: tracked feedback index with per-item status
        # (open / addressed / dismissed).  Pure Python in pbg-superpowers —
        # no AI dependency.  Best-effort; empty result on any error.
        try:
            from pbg_superpowers.feedback_tracking import study_feedback_tracked
            ft = study_feedback_tracked(WORKSPACE, name)
            # Always attach so the SPA can render the panel (empty → no items).
            spec["feedback_tracked"] = ft
        except Exception:  # noqa: BLE001
            pass

        # SP3b: tracked feedback ACTIONS — each open feedback item joined with
        # its proposed action (kind + proposed_text) and open/applied status.
        # Pure Python in pbg-superpowers (the dashboard never computes the
        # action — it renders this + applies via /api/feedback-apply-action).
        try:
            from pbg_superpowers.feedback_actions import study_feedback_actions
            spec["feedback_actions"] = study_feedback_actions(WORKSPACE, name)
        except Exception:  # noqa: BLE001
            pass

        # Derive-on-read status (round-2 friction #2): compute the observable
        # status axes from runs.db so the report shows what actually ran, and
        # flag any stored axis (or legacy planning headline) that contradicts
        # execution state. Stops the "planning status after execution" drift.
        try:
            from pbg_superpowers import study_status as _ss
            runs = spec.get("runs") or []
            spec["derived_status"] = _ss.derive_status(spec, runs)
            diss = _ss.status_disagreements(spec, runs)
            if diss:
                spec["status_disagreements"] = diss
            # Single-sourced reviewer-facing run/test/verdict summary — the
            # downloadable report's per-study clarity strip renders from this so
            # the markers are derived once (here) and shown consistently.
            spec["clarity_summary"] = _ss.study_clarity_summary(spec, runs)
        except Exception:  # noqa: BLE001
            pass

        # Coded gate verdict (spine stage #2): surface the study verdict
        # alongside the authored gate_status so the SPA can render both and flag
        # divergence. PREFER the PERSISTED pipeline_gate.gate_evaluator written
        # by study_verdict.write_gate_evaluator — it carries result,
        # evaluated_by AND diverges_from_authored (the code-vs-authored signal).
        # Only fall back to roll_up_verdict (a render-only recompute that DROPS
        # diverges_from_authored) when no persisted slot exists. Does NOT modify
        # study.yaml; this is render-only.
        try:
            persisted_ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator")
            if isinstance(persisted_ge, dict) and persisted_ge.get("result"):
                spec["computed_gate_verdict"] = dict(persisted_ge)
            else:
                from pbg_superpowers.study_verdict import roll_up_verdict
                spec["computed_gate_verdict"] = roll_up_verdict(spec)
        except Exception:  # noqa: BLE001
            pass

        # Wave 3a #18: pre-registration status — compare the study's declared
        # `preregistered` block (registered_at vs the canonical run's start;
        # thresholds vs behavior_tests[].pass_if) so the SPA / report can render
        # a "pre-registered ✓ / post-hoc ⚠" chip in the verdict area. Pure
        # function in pbg-superpowers; render-only, never modifies study.yaml.
        # Defensive: degrade silently if pbg-superpowers isn't importable.
        try:
            from pbg_superpowers.study_verdict import preregistration_status
            ps = preregistration_status(spec)
            if isinstance(ps, dict) and ps.get("preregistered"):
                spec["preregistration_status"] = ps
        except Exception:  # noqa: BLE001
            pass

        # Spine C1a: surface the owning investigation's PERSISTED acceptance
        # criterion(s) covering THIS study so the "Spine at a glance" panel can
        # show the acceptance roll-up + link to the investigation. Pure disk
        # read of executive.computed_acceptance — NO recompute (the live
        # roll-up still happens in the investigation builder). Best-effort.
        try:
            sa = _study_acceptance_criterion(name)
            if sa:
                spec["spine_acceptance"] = sa
        except Exception:  # noqa: BLE001
            pass

        # Wave 3b #25 — attach the derived lifecycle floor to each finding (the
        # report-data path so the SPA renders the chip without a JS recompute).
        # Defensive: a missing pbg_superpowers.study_verdict.lifecycle_floor
        # leaves findings untouched (the chip then shows only the authored state).
        try:
            from pbg_superpowers.study_verdict import lifecycle_floor as _lf
            for _f in (spec.get("findings") or []):
                if not isinstance(_f, dict) or "_lifecycle_floor" in _f:
                    continue
                try:
                    _v = _lf(_f, spec)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(_v, str) and _v.strip():
                    _f["_lifecycle_floor"] = _v.strip()
        except Exception:  # noqa: BLE001
            pass
    return spec


def _study_acceptance_criterion(name: str):
    """The owning investigation's PERSISTED acceptance criterion(s) for a study.

    Reads ``investigations/<owner>/investigation.yaml``'s
    ``executive.computed_acceptance`` (written by the spine's investigation
    acceptance evaluator) and filters its ``criteria`` to those covering this
    study. Returns ``{investigation, verdict_status, criteria}`` or ``None``
    when the study has no owning investigation / no persisted acceptance.
    Pure disk read — never recomputes.
    """
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
    wp = WorkspacePaths.load(WORKSPACE)
    owner = wp.study_owner(name)
    if not owner:
        return None
    inv_file = wp.investigations / owner / "investigation.yaml"
    if not inv_file.is_file():
        return None
    import yaml as _yaml
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


def _collect_study_feedback(study_slug: str) -> list[dict]:
    """Gather imported feedback annotations targeting one study.

    Scans every ``investigations/<inv>/`` for stored feedback (via
    pbg_superpowers' shared reader) and returns the annotations whose section
    id matches ``study-<slug>``, newest-first. Cross-investigation because a
    study's feedback is keyed by the study slug embedded in the section id,
    not by which investigation exported the report.
    """
    from pbg_superpowers.feedback_import import (
        load_investigation_feedback, feedback_for_study,
    )
    inv_root = workspace_paths().investigations
    if not inv_root.is_dir():
        return []
    out: list[dict] = []
    seen: set[tuple] = set()
    for inv_dir in sorted(inv_root.iterdir()):
        if not inv_dir.is_dir():
            continue
        by_section = load_investigation_feedback(WORKSPACE, inv_dir.name)
        for ann in feedback_for_study(by_section, study_slug):
            key = (ann.get("section"), ann.get("ts"), ann.get("text"))
            if key in seen:
                continue
            seen.add(key)
            out.append(ann)
    out.sort(key=lambda a: a.get("ts") or "", reverse=True)
    return out


def _compute_param_enforcement(spec: dict) -> dict | None:
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
    from pbg_superpowers.param_enforcement import (
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


def _latest_run_timestamp(runs_db: Path) -> float | None:
    """Return the most recent run's wall-clock time from ``runs_meta``.

    Prefers ``completed_at`` (when the run finished, hence when its viz
    could have been rendered), falling back to ``started_at``. Returns
    ``None`` if the table is unreadable or empty.

    Why not ``runs.db`` file mtime: the db is opened in WAL mode, and any
    *read* connection (including the one render_visualizations uses to draw
    the charts) can trigger a checkpoint that bumps the file mtime AFTER the
    viz HTML was written. That made freshly-rendered viz look "older" than
    the db and get silently dropped. The recorded run timestamps are real
    data and immune to that race.
    """
    try:
        conn = sqlite3.connect(f"file:{runs_db}?mode=ro", uri=True, timeout=1.0)
        try:
            row = conn.execute(
                "SELECT MAX(COALESCE(completed_at, started_at)) FROM runs_meta"
            ).fetchone()
        finally:
            conn.close()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:  # noqa: BLE001 — best-effort freshness probe
        return None


def _latest_run_row(runs_db) -> dict | None:
    """Newest runs_meta row as {run_id, completed_at, generation_id, emitter_path},
    tolerating older DBs missing the optional columns. Mirrors
    pbg_superpowers.run_registry.latest_run (not importable here)."""
    from pathlib import Path
    import sqlite3
    runs_db = Path(runs_db)
    if not runs_db.is_file():
        return None
    want = ("run_id", "completed_at", "generation_id", "emitter_path")
    try:
        conn = sqlite3.connect(f"file:{runs_db}?mode=ro", uri=True, timeout=1.0)
        try:
            have = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
            cols = [c for c in want if c in have]
            if "run_id" not in cols:
                return None
            row = conn.execute(
                f"SELECT {', '.join(cols)} FROM runs_meta "
                "ORDER BY COALESCE(completed_at, started_at) DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return dict(zip(cols, row)) if row else None
    except sqlite3.Error:
        return None


def _study_charts_payload(ws_root, name: str) -> dict:
    """Build the /api/study-charts/<name> payload (pure, unit-testable).

    Two chart sources, merged in display order (live first, static after):

      live    — ``studies/<name>/runs.db`` (the per-step history
                emitted by SQLiteEmitter). Picks the latest entry
                from the ``simulations`` table (filtered to
                ``baseline-steady-state`` when present) and renders
                a small canonical set of line charts.
      static  — any pre-rendered ``studies/<name>/charts/*.svg`` files
                (with optional ``*.meta.json`` sidecars providing title +
                caption). These are the domain-specific charts the study
                authors checked in directly (e.g. chromosome maps).

    Each STATIC chart additionally carries a ``freshness`` badge —
    ``fresh`` / ``stale`` / ``unrendered`` for charts declared in the spec's
    ``visualizations[]`` (computed against the study's latest run via the
    vendored :func:`chart_freshness`), and ``untracked`` for on-disk chart
    files with no matching ``visualizations[]`` entry.
    """
    import yaml as _yaml
    from vivarium_dashboard.lib.study_charts import (
        render_study_charts, render_v4_test_charts,
        discover_static_study_charts,
    )
    from vivarium_dashboard.lib.simulations_index import (
        discover_default_baseline_db,
    )
    from .lib.viz_freshness import chart_freshness, manifest_diff

    study_dir = WorkspacePaths.load(ws_root).studies / name
    runs_db = study_dir / "runs.db"
    charts_dir = study_dir / "charts"
    spec_path = study_dir / "study.yaml"

    # Detect v4: study.yaml with schema_version: 4 → render charts per-test
    # from tests[].measure.path, with default-baseline fallback when the
    # per-study runs.db is empty.
    spec = None
    if spec_path.is_file():
        try:
            spec = _yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        except Exception:
            spec = None
    is_v4 = isinstance(spec, dict) and spec.get("schema_version") == 4

    if is_v4:
        fallback_db = discover_default_baseline_db(ws_root)
        live_charts = render_v4_test_charts(spec, runs_db, fallback_db=fallback_db)
    else:
        live_charts = render_study_charts(runs_db, run_name="baseline-steady-state")
        if not live_charts:
            live_charts = render_study_charts(runs_db, run_name=None)
    for c in live_charts:
        c.setdefault("source", "live")
    static_charts = discover_static_study_charts(charts_dir)

    # Per-chart freshness for static charts. Match each on-disk chart
    # (``charts/<key>.<media>``) against the spec's visualizations[] entries
    # by their ``chart:`` field; declared entries get fresh/stale/unrendered,
    # everything else is untracked.
    visualizations = (spec or {}).get("visualizations") or []
    entry_by_chart = {
        e.get("chart"): e for e in visualizations if isinstance(e, dict) and e.get("chart")
    }
    # manifest_diff ensures untracked on-disk charts are accounted for; the
    # static-chart list already includes them, so we just use it to confirm.
    manifest_diff(study_dir, visualizations)
    latest = _latest_run_row(runs_db)
    for c in static_charts:
        rel = f"charts/{c.get('key')}.{c.get('media')}"
        entry = entry_by_chart.get(rel)
        if entry is None:
            c["freshness"] = "untracked"
        else:
            c["freshness"] = chart_freshness(study_dir, entry, latest)

    return {
        "study": name,
        "schema_version": (spec or {}).get("schema_version"),
        "charts": live_charts + static_charts,
        "db_exists": runs_db.exists(),
        "static_count": len(static_charts),
        "live_count": len(live_charts),
    }


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
    out: list[dict] = []

    # Source 1: studies/<name>/viz/*.html (auto-rendered from runs.db).
    viz_dir = workspace_paths().studies / name / "viz"
    runs_db = workspace_paths().studies / name / "runs.db"
    if viz_dir.is_dir() and runs_db.is_file():
        # Freshness reference: the latest recorded run time (WAL-immune), not the
        # db file mtime. A small grace absorbs sub-second render/commit ordering.
        fresh_ref = _latest_run_timestamp(runs_db)
        grace_s = 5.0
        for html_file in sorted(viz_dir.glob("*.html")):
            mtime = html_file.stat().st_mtime
            size_kb = max(1, html_file.stat().st_size // 1024)
            rel = html_file.relative_to(WORKSPACE).as_posix()
            stale = fresh_ref is not None and mtime + grace_s < fresh_ref
            desc = (
                f"Auto-discovered Plotly viz ({size_kb} KB) rendered by "
                f"render_visualizations against the study's runs.db history."
            )
            if stale:
                desc = (
                    "⚠ May predate the latest run — this chart was "
                    "rendered before the most recent simulation completed; re-run "
                    "the study to refresh it. " + desc
                )
            out.append({
                "name": f"{html_file.stem} (auto)",
                "url": f"/{rel}",
                "description": desc,
                "stale": stale,
            })

    # Source 2: reports/figures/<name>/*.html (hand-authored cross-skill output).
    # No runs.db gate — these aren't auto-rendered.
    figures_dir = workspace_paths().reports / "figures" / name
    if figures_dir.is_dir():
        for html_file in sorted(figures_dir.glob("*.html")):
            size_kb = max(1, html_file.stat().st_size // 1024)
            rel = html_file.relative_to(WORKSPACE).as_posix()
            out.append({
                "name": f"{html_file.stem}",
                "url": f"/{rel}",
                "description": (
                    f"Hand-authored figure ({size_kb} KB) from reports/figures/{name}/."
                ),
                "stale": False,
            })

    return out


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
    import sqlite3, json as _json, datetime as _dt
    runs_db = workspace_paths().studies / name / "runs.db"
    # A runs.db is the canonical per-step source, but it's optional: emitter-less
    # workspaces record runs only in study.yaml (merged in below). So don't bail
    # when it's absent — fall through with an empty db result.
    conn = sqlite3.connect(str(runs_db)) if runs_db.is_file() else None
    if conn is not None:
        conn.row_factory = sqlite3.Row
    try:
        # Discover available tables; both should exist for pbg_runner-wrapped
        # runs, but older backfilled DBs may only have runs_meta.
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")} if conn is not None else set()
        # runs_meta.generation_id is a recently-added nullable column; older
        # DBs won't have it, so probe before selecting it.
        meta_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")} \
            if "runs_meta" in tables else set()
        _gen_col = "generation_id" if "generation_id" in meta_cols else "NULL AS generation_id"
        rows_by_id: dict[str, dict] = {}
        if "runs_meta" in tables:
            for r in conn.execute(
                "SELECT run_id, spec_id, label, params_json, started_at, "
                f"completed_at, n_steps, status, sim_name, {_gen_col} "
                "FROM runs_meta ORDER BY started_at DESC"
            ):
                try:
                    params = _json.loads(r["params_json"] or "{}")
                except Exception:
                    params = {}
                rows_by_id[r["run_id"]] = {
                    "run_id":        r["run_id"],
                    "spec_id":       r["spec_id"],
                    "label":         r["label"] or r["sim_name"] or "",
                    "sim_name":      r["sim_name"] or r["label"] or "",
                    "variant":       params.get("variant"),
                    "composite":     params.get("composite") or r["spec_id"],
                    "params":        params,
                    "n_steps":       r["n_steps"],
                    "status":        r["status"],
                    "started_at":    r["started_at"],
                    "completed_at":  r["completed_at"],
                    "generation_id": r["generation_id"],
                    "source":        "runs_meta",
                }
        if "simulations" in tables:
            for r in conn.execute(
                "SELECT simulation_id, name, started_at, completed_at "
                "FROM simulations ORDER BY started_at DESC"
            ):
                sid = r["simulation_id"]
                existing = rows_by_id.get(sid)
                if existing:
                    # Fall back to SQLiteEmitter values when runs_meta lacks
                    # a name / timestamp.
                    if not existing.get("sim_name"):
                        existing["sim_name"] = r["name"] or ""
                else:
                    rows_by_id[sid] = {
                        "run_id":       sid,
                        "spec_id":      name,
                        "label":        r["name"] or "",
                        "sim_name":     r["name"] or "",
                        "variant":      None,
                        "composite":    None,
                        "params":       {},
                        "n_steps":      None,
                        "status":       "ran",
                        "started_at":   r["started_at"],
                        "completed_at": r["completed_at"],
                        "source":       "simulations",
                    }
    finally:
        if conn is not None:
            conn.close()

    # Merge runs recorded in study.yaml `runs:` (emitter-less workspaces) — the
    # db is authoritative where present, so only add spec runs not already seen.
    for _r in _study_yaml_run_rows(name):
        rows_by_id.setdefault(_r["run_id"], _r)

    def _iso(v):
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            try:
                return _dt.datetime.fromtimestamp(
                    float(v), tz=_dt.timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                return str(v)
        return str(v)

    # Coordinated-generation staleness (expert-feedback A.2): a run from an
    # older generation than the workspace's current one is flagged so the
    # report/Runs tab can mark it instead of silently mixing it in.
    try:
        from pbg_superpowers import generation as _gen
        _cur_gen = _gen.current_generation_id(WORKSPACE)
    except Exception:  # noqa: BLE001
        _cur_gen = None

    out = []
    for r in rows_by_id.values():
        r["started_at_iso"] = _iso(r.get("started_at"))
        try:
            r["stale"] = _gen.is_stale(r.get("generation_id"), _cur_gen)
        except Exception:  # noqa: BLE001
            r["stale"] = False
        # Compact params summary for the table cell (e.g., "seed=0, rida_rate=4.6").
        params = r.get("params") or {}
        if params:
            shown = {k: v for k, v in params.items() if not k.startswith("_")}
            r["params_summary"] = ", ".join(
                f"{k}={v}" for k, v in sorted(shown.items())
            )[:80]
        else:
            r["params_summary"] = ""
        out.append(r)

    def _sort_key(r):
        v = r.get("started_at")
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return _dt.datetime.fromisoformat(
                    v.replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0
        return 0.0
    out.sort(key=_sort_key, reverse=True)
    return out


def _iter_study_dirs():
    """Yield every study directory across studies/ and investigations/.

    Delegates to ``WorkspacePaths.iter_study_dirs``, which descends into the
    nested ``investigations/<inv>/studies/<slug>/`` layout (a study dir is one
    that holds ``study.yaml``) AND the flat ``studies/<slug>/`` layout, with the
    nested location winning on slug collision.

    The previous implementation iterated only the DIRECT children of
    ``studies/`` and ``investigations/`` — so for a nested-layout workspace it
    saw the investigation dirs (which hold ``investigation.yaml``, not
    ``study.yaml``) instead of their studies, and every investigation-nested
    study (e.g. ketchup-exchange-comparison, pdmp-*, colonies-*) was silently
    dropped from /api/investigations -> "No investigations declared".

    Legacy compatibility: a study authored directly under
    ``investigations/<name>/`` as a pre-Phase-1 ``spec.yaml`` (no nested
    ``studies/`` subdir, no ``investigation.yaml``) is still a study and is
    yielded too — but a real investigation COLLECTION (one carrying
    ``investigation.yaml``) is not, since its studies live one level down.
    """
    wp = WorkspacePaths.load(WORKSPACE)
    seen: set[str] = set()
    for d in wp.iter_study_dirs():
        seen.add(d.name)
        yield d
    # Legacy: studies stored directly under investigations/<name>/spec.yaml.
    inv_root = wp.dir("investigations")
    if inv_root.is_dir():
        for d in sorted(inv_root.iterdir()):
            if not d.is_dir() or d.name in seen:
                continue
            if (d / "investigation.yaml").is_file():
                continue  # an investigation collection, not a study
            if (d / "spec.yaml").is_file() or (d / "study.yaml").is_file():
                seen.add(d.name)
                yield d


def _parsimony_viewer_dir():
    """Return the bundled ``pbg_parsimony`` viewer asset dir, or None when the
    optional ``pbg_parsimony`` package is not installed.

    Feature-detect seam for the ``/parsimony-viewer/*`` static route and the
    Analyses 3D gallery — the parsimony cards/routes only appear when this
    returns a real directory, mirroring how other optional integrations
    (e.g. bigraph-loom) are gated.
    """
    try:
        import importlib.util
        spec = importlib.util.find_spec("pbg_parsimony")
        if spec is None or not spec.origin:
            return None
        d = Path(spec.origin).parent / "viewer"
        return d if d.is_dir() else None
    except Exception:
        return None


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
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    saved: list[dict] = []
    ptools_studies: list[dict] = []
    for study_dir in wp.iter_study_dirs():
        study = study_dir.name
        viz3d = study_dir / "viz" / "3d"
        if viz3d.is_dir():
            for pack in sorted(viz3d.glob("*.pack.json")):
                try:
                    rel = pack.relative_to(ws_root).as_posix()
                except ValueError:
                    continue
                meta = pack.with_name(pack.name.replace(".pack.json", ".meta.json"))
                meta_url = None
                n_placed = None
                if meta.is_file():
                    try:
                        meta_url = "/" + meta.relative_to(ws_root).as_posix()
                    except ValueError:
                        meta_url = None
                    try:
                        md = json.loads(meta.read_text(encoding="utf-8"))
                        ing = md.get("ingredients") or {}
                        total = sum(
                            int(v.get("count", 0))
                            for v in ing.values() if isinstance(v, dict)
                        )
                        n_placed = total or None
                    except Exception:
                        n_placed = None
                try:
                    created = int(pack.stat().st_mtime)
                except Exception:
                    created = None
                saved.append({
                    "study": study,
                    "name": pack.name[: -len(".pack.json")],
                    "pack_url": "/" + rel,
                    "meta_url": meta_url,
                    "n_placed": n_placed,
                    "created": created,
                })
        if sorted(study_dir.glob("**/ptools/*.tsv")):
            ptools_studies.append({
                "study": study,
                "n_tsvs": len(sorted(study_dir.glob("**/ptools/*.tsv"))),
            })

    try:
        ws = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        ws = {}
    ui = ws.get("ui") or {}
    ptools_configured = bool(str(ui.get("ptools_server_url", "")).strip())

    return {
        "parsimony_available": _parsimony_viewer_dir() is not None,
        "saved": saved,
        "ptools": {"configured": ptools_configured, "studies": ptools_studies},
    }


def _iter_iset_dirs(ws_root: Path | None = None):
    """Yield investigations/<name>/ dirs that contain an investigation.yaml.

    'iset' = investigation-set (a named collection of studies with the v3
    'investigations as collections' semantics, distinct from the legacy
    investigations/<name>/spec.yaml study format).

    ``ws_root`` defaults to the module-level WORKSPACE constant; tests can
    pass an explicit path to walk an isolated tmp workspace.
    """
    root = WorkspacePaths.load(ws_root or WORKSPACE).dir("investigations")
    if not root.is_dir():
        return
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if (d / "investigation.yaml").is_file():
            yield d


# ---------------------------------------------------------------------------
# Investigation status derivation
# ---------------------------------------------------------------------------

# Slug pattern used by /api/iset-create — kebab-case only (no underscores).
# Tighter than _SLUG_RE (which allows underscores for legacy auto-generated
# study names): investigations are user-named in the dashboard UI and we
# want them to look like URL-safe slugs.
_ISET_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# Sets used by compute_investigation_status. Defined at module scope so the
# derivation rules are inspectable / overridable from tests.
_STUDY_STATUS_FAILED = frozenset({"failed", "invalid"})
_STUDY_STATUS_COMPLETE = frozenset({"complete", "ran"})
_STUDY_STATUS_RUNNING = frozenset({"running", "implementing", "runnable", "analyzing"})
_STUDY_STATUS_PLANNED = frozenset({"planned", "planning"})


def compute_investigation_status(
    study_statuses: list[str],
    has_runs: list[bool] | None = None,
) -> str:
    """Derive an investigation's effective status from its member studies.

    Rules, applied in order (first match wins):

    1. Any child in ``{failed, invalid}`` → ``"failed"``.
    2. All children in ``{complete, ran}`` (non-empty) → ``"complete"``.
    3. Any child in ``{running, implementing, runnable, analyzing}`` OR with
       accumulated runs (via ``has_runs[i] == True``) → ``"running"``.
    4. At least one child in ``{complete, ran}`` but not all → ``"in_progress"``.
    5. Otherwise (empty, or all planned/planning/unknown) → ``"planning"``.

    ``has_runs`` is an optional parallel list of bools (one per study)
    indicating whether each study has accumulated at least one run. When
    omitted, rule 3 considers only the status set.
    """
    statuses = list(study_statuses or [])
    has_runs = list(has_runs or [False] * len(statuses))

    # 1: any failed/invalid child poisons the whole investigation.
    if any(s in _STUDY_STATUS_FAILED for s in statuses):
        return "failed"

    # 2: non-empty list, every child complete/ran.
    if statuses and all(s in _STUDY_STATUS_COMPLETE for s in statuses):
        return "complete"

    # 3: anything in the "active" set → running. (Mere accumulated runs no
    # longer count as running — completed history is not active execution.)
    if any(s in _STUDY_STATUS_RUNNING for s in statuses):
        return "running"

    # 4: at least one done OR any accumulated runs, but not all → mixed-progress.
    if any(s in _STUDY_STATUS_COMPLETE for s in statuses) or any(has_runs):
        return "in_progress"

    # 5: default.
    return "planning"


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
    """Per-investigation report index.html (investigations/<slug>/reports/), or None."""
    f = WorkspacePaths.load(ws_root).report_dir(slug) / "index.html"
    return f if f.is_file() else None


def _read_study_status(ws_root: Path, slug: str) -> tuple[str, bool]:
    """Read (status, has_runs) for a member study referenced by an iset.

    Returns ``("planning", False)`` if the study can't be located or parsed —
    treat missing-children as benign for status derivation rather than
    poisoning the entire investigation.
    """
    # Resolve the study dir nested-aware (investigations/<inv>/studies/<slug>/),
    # falling back to the legacy v2 spec.yaml. study_dir() handles flat back-compat.
    try:
        sp = WorkspacePaths.load(ws_root).study_dir(slug) / "study.yaml"
    except FileNotFoundError:
        sp = ws_root / "investigations" / slug / "spec.yaml"
    if sp.is_file():
        try:
            spec = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
        except Exception:
            return "planning", False
        status = spec.get("status") or "planning"
        # F2: count via _count_runs_for_study so we see runs that landed in
        # runs.db without a matching study.yaml entry. spec.runs merged via max().
        return status, _count_runs_for_study(slug, spec) > 0
    return "planning", False


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
    """Git lifecycle of an investigation: 'merged' if its dir exists in the
    merge-base with main (i.e. already on main), else 'wip'. Any git error or
    non-repo -> 'wip'."""
    import subprocess
    rel = WorkspacePaths.load(ws_root).rel("investigations") + f"/{slug}/investigation.yaml"
    try:
        base = subprocess.run(["git", "merge-base", "HEAD", "main"], cwd=str(ws_root),
                              capture_output=True, text=True)
        ref = base.stdout.strip() if base.returncode == 0 else "main"
        r = subprocess.run(["git", "cat-file", "-e", f"{ref}:{rel}"], cwd=str(ws_root),
                          capture_output=True, text=True)
        return "merged" if r.returncode == 0 else "wip"
    except Exception:
        return "wip"


def _current_branch_slug(ws_root: Path) -> str | None:
    """The investigation slug matching the workspace's current git branch, or None."""
    import re, subprocess
    try:
        br = subprocess.run(["git", "-C", str(ws_root), "branch", "--show-current"],
                            capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:
        return None
    if not br:
        return None
    slugs = [d.name for d in _iter_iset_dirs(ws_root)]
    if br in slugs:
        return br
    for s in slugs:
        if br == f"investigation/{s}" or br.endswith("/" + s):
            return s
    brtok = set(t for t in re.split(r"[/_\-.]+", br.lower()) if t)
    best, best_n = None, 0
    for s in slugs:
        stok = set(t for t in re.split(r"[/_\-.]+", s.lower()) if t)
        n = len(brtok & stok)
        if n > best_n:
            best, best_n = s, n
    return best if best_n > 0 else None


def _inputs_payload(ws_root: Path, slug: str | None = None) -> dict:
    """Pure seam backing ``GET /api/inputs``.

    Returns the loaded investigation's owned inputs (the investigation whose
    slug matches the current git branch), the repo-wide global inputs
    (workspace.yaml ``datasets`` + parsed BibTeX references), and that current
    slug. Mirrors the SimulationsDB current-investigation-first layout.
    """
    from vivarium_dashboard.lib.investigation_inputs import investigation_inputs
    from vivarium_dashboard.lib.report import _parse_bib_entries, _enrich_with_file_info

    current = slug or _current_branch_slug(ws_root)
    if current:
        investigation = investigation_inputs(ws_root, current, repo_fallback=False)
    else:
        investigation = {"datasets": [], "references": [],
                         "expert_docs": [], "_repo_fallback": False}

    # Repo-level (global) inputs: reuse the same data sources the global Inputs
    # page builds from — workspace.yaml `datasets` (file-enriched) and the
    # parsed BibTeX references.
    try:
        ws = yaml.safe_load((Path(ws_root) / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        ws = {}
    try:
        global_datasets = _enrich_with_file_info(ws.get("datasets") or [], ws_root)
    except Exception:
        global_datasets = list(ws.get("datasets") or [])
    try:
        bib_entries = _parse_bib_entries(ws_root)
    except Exception:
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
    pdf_by_key = {}
    for rp in (ws.get("references_pdfs") or []):
        if isinstance(rp, dict) and rp.get("bib_key") and rp.get("path"):
            pdf_by_key[rp["bib_key"]] = rp["path"]

    def _enrich_ref(ref):
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

    def _norm_input(item):
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
    """Pure function backing ``POST /api/proposed-input-decision``.

    Resolves the ``proposed_inputs.items[]`` entry with ``id == item_id`` in
    investigations/<inv>/investigation.yaml and applies the expert decision:

      * ``accept``  → set ``status: accepted``. For ``kind: reference`` also
        append the citation to ``inputs.references`` so it becomes a real
        provided reference. For ``kind: mechanism`` only mark accepted (a
        human integrates the mechanism).
      * ``decline`` → set ``status: declined``.

    Persists with ruamel (round-trip preserves comments/formatting), falling
    back to safe_dump where ruamel is unavailable (e.g. the test venv).
    Returns (response_dict, status_code).
    """
    if not inv:
        return {"error": "investigation name required"}, 400
    if not item_id:
        return {"error": "item_id required"}, 400
    if decision not in ("accept", "decline"):
        return {"error": "decision must be 'accept' or 'decline'"}, 400

    target = _investigation_yaml_path(ws_root, inv)
    if target is None:
        return {"error": f"no investigation.yaml for {inv!r}"}, 404

    new_status = "accepted" if decision == "accept" else "declined"
    result: dict = {}

    def _mutate(spec: dict):
        block = spec.get("proposed_inputs")
        if not isinstance(block, dict):
            return None, ("proposed_inputs block missing", 404)
        items = block.get("items")
        if not isinstance(items, list):
            return None, ("proposed_inputs.items missing", 404)
        match = None
        for it in items:
            if isinstance(it, dict) and str(it.get("id")) == str(item_id):
                match = it
                break
        if match is None:
            return None, (f"no proposed input with id {item_id!r}", 404)
        match["status"] = new_status
        kind = match.get("kind") or "reference"
        result["kind"] = kind
        result["status"] = new_status
        # On accept, promote a reference into the real provided-references list.
        if decision == "accept" and kind == "reference":
            inputs = spec.get("inputs")
            if not isinstance(inputs, dict):
                inputs = {}
                spec["inputs"] = inputs
            refs = inputs.get("references")
            if not isinstance(refs, list):
                refs = []
                inputs["references"] = refs
            # Prefer a bib-key style id; fall back to the citation text.
            ref_value = match.get("id") or match.get("citation")
            if ref_value and ref_value not in refs:
                refs.append(ref_value)
                result["added_reference"] = ref_value
        return spec, None

    try:
        from ruamel.yaml import YAML as _RYAML
        _ry = _RYAML(); _ry.preserve_quotes = True; _ry.width = 4096
        spec = _ry.load(target.read_text(encoding="utf-8")) or {}
        mutated, err = _mutate(spec)
        if err is not None:
            return {"error": err[0]}, err[1]
        with target.open("w", encoding="utf-8") as _fh:
            _ry.dump(mutated, _fh)
    except ImportError:
        spec = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        mutated, err = _mutate(spec)
        if err is not None:
            return {"error": err[0]}, err[1]
        target.write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")

    return {"ok": True, "item_id": item_id, **result}, 200


def _build_iset_summary_for_test(ws_root: Path) -> list[dict]:
    """Pure function backing ``GET /api/iset-list`` — emits the same list
    of summary dicts that the handler returns, but without HTTP plumbing.

    Each entry includes ``effective_status`` derived from the member
    studies' current statuses.
    """
    out: list[dict] = []
    current_slug = _current_branch_slug(ws_root)
    for d in _iter_iset_dirs(ws_root):
        try:
            spec = yaml.safe_load((d / "investigation.yaml").read_text(encoding="utf-8")) or {}
        except Exception as e:
            out.append({"name": d.name, "error": f"parse failed: {e}"})
            continue
        study_slugs = list(spec.get("studies") or [])
        statuses_and_runs = [_read_study_status(ws_root, s) for s in study_slugs]
        statuses = [s for s, _ in statuses_and_runs]
        has_runs = [r for _, r in statuses_and_runs]
        author_status = spec.get("status", "planning")
        effective_status = compute_investigation_status(statuses, has_runs=has_runs)
        out.append({
            "name":             spec.get("name", d.name),
            "title":            spec.get("title", spec.get("name", d.name)),
            "status":           author_status,
            "effective_status": effective_status,
            "description":      spec.get("description", ""),
            "question":         spec.get("question", ""),
            "hypothesis":       spec.get("hypothesis", ""),
            "n_studies":        len(study_slugs),
            "studies":          study_slugs,
            "lifecycle":        _iset_lifecycle(ws_root, spec.get("name", d.name)),
            "current":          (d.name == current_slug),
        })
    return out


def _catalog_data(ws_root: "Path") -> dict:
    """Pure data builder for GET /api/catalog — returns ``{"modules": [...]}`` dict.

    Called by ``Handler._get_catalog`` (which wraps it in the HTTP response)
    and by ``publish.build_bundle`` to export ``api/catalog.json``.

    Requires the ``WORKSPACE`` global to already be set to *ws_root*
    (``build_bundle`` ensures this before calling).
    """
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths as _WP

    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    except Exception as e:
        return {"modules": [], "error": f"workspace.yaml: {e}"}

    # Module registry (canonical pbg-superpowers list + per-workspace overlay)
    try:
        from pbg_superpowers.catalog import load_registry as _lr
        default_modules: list = _lr(ws_root)
    except Exception:
        _wp = _WP.load(ws_root)
        legacy = _wp.scripts / "_catalog" / "modules.json"
        if legacy.is_file():
            try:
                default_modules = json.loads(legacy.read_text(encoding="utf-8"))
            except Exception:
                default_modules = []
        else:
            default_modules = []

    override = _registry_modules_override(ws_data)
    if override is not None:
        modules = _build_override_catalog(override, default_modules)
    else:
        modules = default_modules

    imports = (ws_data or {}).get("imports", {}) or {}
    pyproject_deps = _read_workspace_pyproject_deps(ws_root)
    venv_dists = _detect_workspace_venv_distributions(ws_root)

    def _name_variants(m: dict) -> list:
        out: list = [m["name"].lower()]
        pn = m.get("pypi_name")
        if pn:
            out.append(pn.lower())
        pkg = m.get("package") or m["name"].replace("-", "_")
        out.append(pkg.lower())
        return out

    for m in modules:
        variants = _name_variants(m)
        declared = m["name"] in imports
        in_pyproject = any(v in pyproject_deps for v in variants)
        in_venv = any(v in venv_dists for v in variants)
        if declared:
            m["installed"] = True
            m["install_source"] = "imports"
            imp = imports.get(m["name"], {}) or {}
            for k in ("source", "ref", "path", "install_path", "package"):
                v = imp.get(k)
                if v is not None:
                    m[k] = v
        elif in_pyproject:
            m["installed"] = True
            m["install_source"] = "pyproject"
        elif in_venv:
            m["installed"] = True
            m["install_source"] = "venv"
            parents: list = []
            for v in variants:
                info = venv_dists.get(v)
                if info:
                    parents.extend(info.get("requires_by") or [])
                    break
            m["installed_via"] = sorted(set(parents))
        else:
            m["installed"] = False
        if m["installed"]:
            if m.get("install_source") in ("imports", "pyproject"):
                pkg_name = m.get("package") or m["name"].replace("-", "_")
                sync_reason = _check_installed_module_sync(pkg_name, m.get("install_path"))
                if sync_reason:
                    m["out_of_sync"] = True
                    m["out_of_sync_reason"] = sync_reason

    reexport_origins = _build_reexport_origin_modules(ws_data, modules)
    if reexport_origins:
        modules = modules + reexport_origins

    # Workspace self-module (mirrors Handler._workspace_self_module)
    slug = (ws_data or {}).get("name", "") or ""
    ws_pkg = (ws_data or {}).get("package_path")
    if not ws_pkg:
        ws_pkg = "pbg_" + slug.replace("-", "_") if slug else None
    if ws_pkg:
        pkg_dir = ws_root / ws_pkg
        if pkg_dir.is_dir():
            sync_reason = _check_installed_module_sync(ws_pkg, ws_pkg)
            try:
                ref = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=ws_root, capture_output=True, text=True, timeout=2,
                ).stdout.strip() or "—"
            except (subprocess.TimeoutExpired, OSError):
                ref = "—"
            ws_self: dict = {
                "kind":         "workspace",
                "name":         slug or ws_pkg,
                "package":      ws_pkg,
                "install_path": ws_pkg,
                "description":  "Workspace's own first-party package — provides the "
                                "Processes, Steps, Composites, and Types that "
                                "build_core() registers for this workspace.",
                "source":       "workspace",
                "ref":          ref,
                "tags":         ["workspace"],
                "installed":    True,
            }
            if sync_reason:
                ws_self["out_of_sync"] = True
                ws_self["out_of_sync_reason"] = sync_reason
            modules = [ws_self] + modules

    if override is None:
        kept_origins = [m for m in modules if isinstance(m, dict) and m.get("reexport_origin")]
        modules = _filter_catalog_modules(modules, ws_data) + kept_origins

    return {"modules": modules}


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
        return {"composites": out, "workspace_package": pkg}
    except Exception as e:
        return {"composites": [], "error": str(e)}


def _composite_resolve_data(spec_id: str) -> "dict | None":
    """Pure data builder for a single composite — returns the resolve payload dict.

    Mirrors the data returned by ``GET /api/composite-resolve`` (minus the
    expensive SVG render, which is set to ``None``).  Used by ``publish.build_bundle``
    to pre-build ``api/composite-state/<id>.json`` files consumed by the
    bigraph-loom ``?static=1&stateUrl=`` read-only mode.

    Returns ``None`` on any failure (not found, import errors, missing packages).
    Requires ``WORKSPACE`` to already be set.
    """
    _ws_add_to_sys_path()
    try:
        from vivarium_dashboard.lib.composite_lookup import (
            substitute_parameters,
            find_composite_path,
        )
        ws_data = yaml.safe_load(
            (WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")
        )
        pkg = ws_data.get("package_path") or (
            "pbg_" + ws_data.get("name", "").replace("-", "_")
        )

        # Generator-kind branch (pbg-superpowers @composite_generator)
        try:
            from pbg_superpowers.composite_generator import (
                _REGISTRY, build_generator, discover_generators,
            )
            if not _REGISTRY:
                discover_generators()
            entry = _REGISTRY.get(spec_id)
        except ImportError:
            entry = None

        if entry is not None:
            try:
                doc = build_generator(entry, overrides={})
            except Exception:
                return None
            if isinstance(doc, dict) and "state" in doc and isinstance(doc["state"], dict):
                state = doc["state"]
            else:
                state = doc
            try:
                from vivarium_dashboard.lib.process_docs import attach_process_docs
                attach_process_docs(state)
            except Exception:
                pass
            return {
                "id": spec_id,
                "name": entry.name,
                "description": entry.description,
                "parameters": entry.parameters,
                "state": state,
                "svg": None,
                "kind": "generator",
                "module": entry.module,
                "default_n_steps": getattr(entry, "default_n_steps", None),
            }

        # Spec-file branch
        path = find_composite_path(WORKSPACE, pkg, spec_id)
        if path is None:
            return None

        text = path.read_text(encoding="utf-8")
        spec = (
            json.loads(text) if path.suffix.lower() == ".json"
            else yaml.safe_load(text)
        )
        state = substitute_parameters(
            spec.get("state") or {},
            spec.get("parameters") or {},
            {},
        )
        try:
            from vivarium_dashboard.lib.composite_lookup import _derive_module_from_spec_id
            module = _derive_module_from_spec_id(spec_id)
        except Exception:
            module = ""
        try:
            from vivarium_dashboard.lib.process_docs import attach_process_docs
            attach_process_docs(state)
        except Exception:
            pass
        return {
            "id": spec_id,
            "name": spec.get("name", spec_id.rsplit(".composites.", 1)[-1]),
            "description": spec.get("description", ""),
            "parameters": spec.get("parameters") or {},
            "state": state,
            "svg": None,
            "kind": "spec",
            "module": module,
            "default_n_steps": None,
        }
    except Exception:
        return None


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
            tag = (s.get("emitter") or "").lower()
            s["emitter_type"] = _emitter_label.get(tag) or emitter_type_of(s.get("db_path"))
    except Exception:
        pass
    return {"simulations": sims, "current": _current_branch_slug(ws_root)}


def _visualization_classes_data(ws_root: Path) -> dict:
    """Pure data builder for GET /api/visualization-classes.

    Returns ``{"classes": [...]}`` with the same shape as
    ``Handler._list_visualization_classes()``.  Tolerates missing packages /
    build_core failures → returns empty list.
    Called by ``publish.build_bundle`` to export ``api/visualization-classes.json``.
    """
    _ws_add_to_sys_path()
    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        sys.path.insert(0, str(ws_root))
        core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
        core = core_module.build_core()
        registry: dict = dict(core.link_registry)
    except Exception:
        registry = {}

    # Inject standard pbg-superpowers visualization classes
    try:
        from pbg_superpowers.visualizations import (
            TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap,
        )
        for cls in [TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap]:
            registry[cls.__name__] = cls
    except ImportError:
        pass

    # Inject workspace-local viz classes
    try:
        from pbg_superpowers.visualization import Visualization as _VizBase
        import pkgutil as _pkgutil, importlib as _importlib
        viz_pkg = _importlib.import_module(f"{ws_data.get('package_path') or ('pbg_' + ws_data.get('name','').replace('-','_'))}.visualizations")
        for _, modname, _ in _pkgutil.iter_modules(viz_pkg.__path__):
            try:
                mod = _importlib.import_module(f"{pkg}.visualizations.{modname}")
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
        _VizBase = None

    # Filter to Visualization subclasses
    try:
        from pbg_superpowers.visualization import Visualization as _VB
    except ImportError:
        _VB = None

    def _is_viz(cls):
        if _VB is not None and cls is _VB:
            return False
        marker = getattr(cls, "is_visualization", None)
        if callable(marker):
            try:
                if marker() is True:
                    return True
            except Exception:
                pass
        if _VB is not None:
            try:
                if isinstance(cls, type) and issubclass(cls, _VB):
                    return True
            except TypeError:
                pass
        return False

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

    # Append Analysis classes from v2ecoli
    try:
        import v2ecoli.workflow.analyses  # noqa: F401
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
        pass

    return {"classes": out}


def _investigations_data(ws_root: Path) -> dict:
    """Pure data builder for GET /api/investigations — returns ``{"investigations": [...]}`` dict.

    Includes the study-dependency DAG: each row carries ``parent_studies``
    (normalized to [{study, condition}]) and a computed ``blocked`` flag
    plus ``blocked_by`` list pointing at parents that don't yet satisfy
    their condition.

    Called by ``Handler._get_investigations`` (which wraps it in the HTTP
    response) and by ``publish.build_bundle`` to export
    ``api/investigations.json``.  Requires ``WORKSPACE`` to be set to
    *ws_root*.
    """
    _ws_add_to_sys_path()
    from vivarium_dashboard.lib.investigations import (
        load_spec,
        InvestigationSpecError,
        normalize_dag_edges,
    )

    # First pass: load every spec so we can resolve cross-study conditions.
    loaded: list[tuple[Path, dict]] = []   # (dir, spec)
    for d in _iter_study_dirs():
        spec_path = d / "study.yaml" if (d / "study.yaml").is_file() else d / "spec.yaml"
        if not spec_path.is_file():
            continue
        try:
            loaded.append((d, load_spec(spec_path)))
        except InvestigationSpecError as e:
            loaded.append((d, {"__invalid__": True, "name": d.name, "error": str(e)}))

    by_name: dict[str, dict] = {s["name"]: s for _, s in loaded if not s.get("__invalid__")}

    def _normalize_parents(spec: dict) -> list[dict]:
        return normalize_dag_edges(spec)

    def _condition_satisfied(parent: dict | None, condition: str) -> bool:
        if parent is None:
            return False
        status = parent.get("status", "planned")
        if condition == "ran":
            return status in ("ran", "complete")
        if condition == "complete":
            return status == "complete"
        if condition == "tests-passed":
            from pbg_superpowers import study_status
            counts = study_status.count_test_outcomes(parent, parent.get("runs"))
            return counts["fail"] == 0 and counts["pass"] > 0
        return False

    out = []
    for d, spec in loaded:
        if spec.get("__invalid__"):
            out.append({"name": spec["name"], "status": "invalid", "error": spec["error"]})
            continue
        composites = spec.get("composites") or []
        if composites:
            composite_summary = ", ".join(c.get("name", "") for c in composites)
            n_runs = _count_runs_for_study(spec["name"], spec)
        else:
            composite_summary = spec.get("composite", "")
            n_runs = _count_runs_for_study(spec["name"], spec)
            if n_runs == 0:
                n_runs = len(spec.get("simulations") or [])

        parents = _normalize_parents(spec)
        blocked_by = []
        for p in parents:
            parent_spec = by_name.get(p["study"])
            if not _condition_satisfied(parent_spec, p["condition"]):
                blocked_by.append({
                    "study":     p["study"],
                    "condition": p["condition"],
                    "missing":   "parent-not-found" if parent_spec is None else
                                 f"parent.status={parent_spec.get('status', 'planned')}",
                })

        sim_set_top = spec.get("simulation_set") or []
        beh_tests_top = spec.get("behavior_tests") or spec.get("expected_behavior") or []
        readouts_top = spec.get("readouts") or spec.get("observables") or []
        reqs_top = spec.get("implementation_requirements") or spec.get("gaps") or []
        n_variants_top = (len(sim_set_top) if sim_set_top
                          else len(spec.get("variants") or []))
        row = {
            "name":            spec["name"],
            "composite":       composite_summary,
            "composites":      composites,
            "description":     spec.get("description", ""),
            "topic":           spec.get("topic", ""),
            "tags":            spec.get("tags") or [],
            "status":          spec.get("status", "planned"),
            "phase":           spec.get("phase"),
            "last_run":        spec.get("last_run"),
            "n_simulations":   n_runs,
            "baseline_names":  [b.get("name", "") for b in (spec.get("baseline") or [])
                                if isinstance(b, dict)],
            "n_baseline":      len(spec.get("baseline") or []),
            "n_variants":      n_variants_top,
            "n_groups":        len(spec.get("groups") or []),
            "n_interventions": len(spec.get("interventions") or []),
            "n_behaviors":     len(beh_tests_top),
            "n_readouts":      len(readouts_top),
            "n_requirements":  len(reqs_top),
            "n_comparisons":   len(spec.get("comparisons") or []),
            "n_runs":          n_runs,
            "baseline_source": _format_baseline_source(spec),
            "conclusions_excerpt": _conclusions_excerpt(spec),
            "parent_studies":  parents,
            "blocked":         len(blocked_by) > 0,
            "blocked_by":      blocked_by,
        }
        out.append(row)
    return {"investigations": out}


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


def _http_get_json(url: str, timeout: float = 1.5) -> dict | None:
    """Best-effort GET → JSON. Returns None on any failure (timeout, non-2xx,
    invalid JSON). Never raises — callers treat None as 'peer unreachable'.
    """
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
            return json.loads(data.decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError,
            json.JSONDecodeError, ValueError):
        return None


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
    """Create a new investigation.yaml. Returns (response_dict, status_code).

    Body:
        name:           required, kebab-case slug (^[a-z0-9][a-z0-9-]*$).
        overview:       optional, becomes the ``description:`` field.
        parent_studies: optional list of study slugs.
    """
    import os
    name = (body.get("name") or "").strip()
    overview = body.get("overview") or ""
    parent_studies = body.get("parent_studies") or []

    if not name:
        return {"error": "name is required"}, 400
    if not _ISET_SLUG_RE.match(name):
        return {"error": "name must be kebab-case (^[a-z0-9][a-z0-9-]*$)"}, 400

    inv_dir = ws_root / "investigations" / name
    target = inv_dir / "investigation.yaml"
    if target.exists():
        return {"error": f"investigation '{name}' already exists"}, 409

    # Emit a v2-shape investigation.yaml with the narrative spine commented
    # in as TODOs (executive / scientific_argument / biological_story /
    # at_a_glance / glossary / guidelines). The user sees the target shape
    # — the same shape dnaa-replication evolved through use — without having
    # to read docs first. All v2 fields are optional, so the spec validates
    # on day one and the user opts in by uncommenting sections.
    from vivarium_dashboard.lib.scaffold_yaml import v2_investigation_scaffold
    body_yaml = v2_investigation_scaffold(
        name,
        title=name,
        overview=overview or None,
        parent_studies=list(parent_studies) if parent_studies else None,
    )

    inv_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, body_yaml)

    detail, code = _build_iset_detail_for_test(ws_root, name)
    return detail, code


def _post_iset_clone_for_test(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Clone an existing investigation into a fresh planning state.

    Shells out to the workspace's ``scripts/clone_investigation.py`` so the
    dashboard and the standalone CLI share a single source of truth. The
    script lives in the workspace (not in this package) because clone rules
    are workspace-specific (which subdirectories to strip, which planning
    docs to keep, study-name conventions, etc.).

    Body:
        source:         required, slug of the source investigation.
        target:         required, slug of the target investigation.
        source_prefix:  optional, defaults to first dash-segment of source.
        target_prefix:  optional, defaults to first dash-segment of target.
    """
    source = (body.get("source") or "").strip()
    target = (body.get("target") or "").strip()
    if not source or not target:
        return {"error": "source and target are required"}, 400
    if not _ISET_SLUG_RE.match(source) or not _ISET_SLUG_RE.match(target):
        return {"error": "source and target must be kebab-case (^[a-z0-9][a-z0-9-]*$)"}, 400
    if source == target:
        return {"error": "source and target must differ"}, 400

    src_dir = ws_root / "investigations" / source
    if not src_dir.is_dir():
        return {"error": f"source investigation '{source}' not found"}, 404
    dst_dir = ws_root / "investigations" / target
    if dst_dir.exists():
        return {"error": f"target investigation '{target}' already exists"}, 409

    script = ws_root / "scripts" / "clone_investigation.py"
    if not script.is_file():
        return {"error": "workspace is missing scripts/clone_investigation.py"}, 501

    argv = [
        sys.executable, str(script),
        "--source", source,
        "--target", target,
        "--source-root", str(ws_root),
        "--target-root", str(ws_root),
        "--json",
    ]
    if body.get("source_prefix"):
        argv += ["--source-prefix", str(body["source_prefix"])]
    if body.get("target_prefix"):
        argv += ["--target-prefix", str(body["target_prefix"])]

    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return {
            "error": "clone script failed",
            "stderr": (proc.stderr or proc.stdout)[-2000:],
        }, 500
    try:
        summary = json.loads(proc.stdout.strip().split("\n")[-1])
    except (json.JSONDecodeError, IndexError):
        summary = {"stdout_tail": proc.stdout[-500:]}

    detail, code = _build_iset_detail_for_test(ws_root, target)
    if code == 200:
        detail["clone_summary"] = summary
    return detail, code


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
    """Set study.yaml objective field. Returns (response_dict, status_code)."""
    name = (body.get("study") or "").strip()
    text = body.get("text") or ""
    if not name:
        return {"error": "missing study"}, 400
    sf = ws_root / "studies" / name / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404
    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    spec["objective"] = text
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


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
    """Set one v4 narrative-spine field on study.yaml at a dotted path.

    Body shape:
        {study: "<slug>", path: "<dotted-path>", value: <any>}

    The dotted path's first segment must be one of the allowlisted v4
    narrative-spine roots (report, study_card, biological_summary,
    conclusion_verdicts, literature_anchors, design_pivot_required). Intermediate
    dicts are created on demand. An empty-string or null value REMOVES the
    leaf (and prunes empty parent dicts up the chain) so the YAML stays tidy
    after a user clears a field.

    Examples:
        path="biological_summary"                                 value="DnaA cycles..."
        path="study_card.goal"                                    value="Split DnaA species"
        path="report.confidence"                                  value="high"
        path="conclusion_verdicts.biological_validation.result"   value="MIXED"
        path="conclusion_verdicts.biological_validation.basis"    value="atp_fraction = 0.997..."

    The handler is intentionally generic — one route serves every scalar
    narrative-spine field rather than 10+ /api/study-set-<field> endpoints.
    Returns (response_dict, status_code).
    """
    import os

    name = (body.get("study") or "").strip()
    path = (body.get("path") or "").strip()
    if not name:
        return {"error": "missing study"}, 400
    if not path:
        return {"error": "missing path"}, 400
    if "value" not in body:
        return {"error": "missing value"}, 400
    value = body["value"]

    parts = path.split(".")
    if not parts or not parts[0]:
        return {"error": "empty path"}, 400
    if parts[0] not in _NARRATIVE_ALLOWED_ROOTS:
        return {
            "error": f"path must start with one of "
                     f"{sorted(_NARRATIVE_ALLOWED_ROOTS)}, got {parts[0]!r}",
        }, 400

    # Enum guard on the few leaves the schema strictly enums. Empty-string and
    # null are allowed through (they trigger the remove-leaf branch below).
    if value not in (None, "") and path in _NARRATIVE_ENUM_LEAVES:
        allowed = _NARRATIVE_ENUM_LEAVES[path]
        if value not in allowed:
            return {
                "error": f"{path}: value {value!r} not in allowed enum "
                         f"{sorted(allowed)}",
            }, 400

    sf = ws_root / "studies" / name / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    if not isinstance(spec, dict):
        return {"error": "study.yaml is not a mapping"}, 500

    # Walk parents, creating dicts as needed.
    cur = spec
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    leaf = parts[-1]

    if value in (None, ""):
        # Clear-out path: pop the leaf, then prune empty parent dicts walking
        # back up so a fully-cleared section disappears from the YAML.
        cur.pop(leaf, None)
        # Re-walk to prune empty parents (top-down detection of empties).
        for i in range(len(parts) - 1, 0, -1):
            ancestor_path = parts[:i]
            ancestor = spec
            for p in ancestor_path[:-1]:
                ancestor = ancestor.get(p, {})
                if not isinstance(ancestor, dict):
                    break
            else:
                last = ancestor_path[-1]
                if last in ancestor and ancestor[last] == {}:
                    ancestor.pop(last, None)
                    continue
            break
    else:
        cur[leaf] = value

    atomic_write_text(sf, yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
    return {"ok": True}, 200


def _post_study_seed_followup_for_test(ws_root: Path, body: dict):
    """Seed a child study from a parent. Returns (response_dict, status_code).

    Routes the four unified followup field families through one entry:

    - ``finding_id`` → delegates to the shared pbg-superpowers seed mechanism
      (``resolve_seed_source`` + ``write_child_study``) via
      ``seed_followup_study``; seeds STANDALONE from a ``finding.next_action``.
    - ``followup_idx`` / ``proposal_id`` / ``proposal_idx`` → the existing
      legacy / discovery_implications paths.

    The pbg import is lazy + tolerant: if pbg-superpowers isn't installed the
    finding path returns a 500 with a clear message rather than crashing the
    server.
    """
    from vivarium_dashboard.lib.study_seed import seed_followup_study

    parent = body.get("parent")
    finding_id = body.get("finding_id")
    proposal_id = body.get("proposal_id")
    proposal_idx = body.get("proposal_idx")
    # Wave 3a #19 — optional study_type (e.g. 'diagnostic' when the parent
    # failed) threaded to the pbg writer so the seeded child is typed.
    study_type = body.get("study_type") or None
    if proposal_idx is not None:
        try:
            proposal_idx = int(proposal_idx)
        except (TypeError, ValueError):
            return {"error": "proposal_idx must be an integer"}, 400
    try:
        if finding_id is not None and str(finding_id) != "":
            # Finding family — delegate to the shared pbg seed mechanism.
            new_name = seed_followup_study(
                ws_root, parent, finding_id=finding_id, proposal_id=proposal_id,
                study_type=study_type)
        else:
            new_name = seed_followup_study(
                ws_root, parent,
                int(body.get("followup_idx", -1)),
                proposal_id=proposal_id,
                proposal_idx=proposal_idx,
                study_type=study_type,
            )
    except ImportError as e:
        return {"error": f"finding-seed requires pbg-superpowers: {e}"}, 500
    except FileNotFoundError as e:
        return {"error": str(e)}, 404
    except (ValueError, KeyError, IndexError) as e:
        return {"error": str(e)}, 400
    except Exception as e:
        return {"error": f"seed failed: {e}"}, 500
    return {"new_study_name": new_name, "new_slug": new_name}, 200


def _post_feedback_apply_action_for_test(ws_root: Path, body: dict):
    """Apply a tracked feedback action via the pbg-superpowers primitive.

    SP3b: the dashboard NEVER computes the action — it renders the
    ``study_feedback_actions`` data + applies via this primitive (AI-free).
    Lazy + tolerant pbg import: if pbg-superpowers isn't installed, return a
    clear 500 rather than crashing the server. Body: ``{item_id}``.
    Returns ``(response_dict, status_code)``.
    """
    item_id = body.get("item_id")
    if not item_id:
        return {"error": "item_id required"}, 400
    try:
        from pbg_superpowers.feedback_actions import apply_feedback_action
    except ImportError as e:
        return {"error": f"feedback-apply requires pbg-superpowers: {e}"}, 500
    try:
        result = apply_feedback_action(ws_root, item_id)
    except FileNotFoundError as e:
        return {"error": str(e)}, 404
    except Exception as e:  # noqa: BLE001
        return {"error": f"apply failed: {e}"}, 500
    # apply_feedback_action is best-effort: a not-found / bad-target case comes
    # back as {"error": ...} without applied=True. Surface that as a 400.
    if result.get("error") and not result.get("applied"):
        return result, 400
    return result, 200


def _post_study_rename_for_test(ws_root: Path, body: dict):
    """Rename a study directory and update name in study.yaml. Returns (response_dict, status_code)."""
    name = (body.get("study") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    if not name or not new_name:
        return {"error": "missing study or new_name"}, 400
    if not _SLUG_RE.match(new_name):
        return {"error": "new_name must be lowercase + dashes"}, 400
    src = ws_root / "studies" / name
    dst = ws_root / "studies" / new_name
    if not src.is_dir():
        return {"error": "study not found"}, 404
    if dst.exists():
        return {"error": f"study {new_name!r} already exists"}, 409
    src.rename(dst)
    sf = dst / "study.yaml"
    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    spec["name"] = new_name
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True, "name": new_name}, 200


def _post_study_create_from_run_for_test(ws_root, body):
    """Create a new Study from a scratchpad run. Returns (response_dict, status_code)."""
    import datetime
    import json as _json
    import tempfile
    from vivarium_dashboard.lib.composite_runs import copy_run_to_new_db

    name = (body.get("name") or "").strip()
    objective = body.get("objective") or ""
    description = body.get("description") or ""
    source_run_id = (body.get("source_run_id") or "").strip()

    if not name or not source_run_id:
        return {"error": "missing name or source_run_id"}, 400
    if not _SLUG_RE.match(name):
        return {"error": "name must be lowercase + dashes"}, 400

    studies_root = Path(ws_root) / "studies"
    studies_root.mkdir(parents=True, exist_ok=True)
    dst = studies_root / name
    if dst.exists():
        return {"error": f"study {name!r} already exists"}, 409

    scratch = Path(ws_root) / ".pbg" / "composite-runs.db"
    if not scratch.is_file():
        return {"error": "no scratchpad DB"}, 404

    # Read the source run's metadata once to populate baseline.
    import sqlite3 as _sqlite3
    src = _sqlite3.connect(str(scratch))
    src.row_factory = _sqlite3.Row
    meta = src.execute(
        "SELECT spec_id, params_json, n_steps FROM runs_meta WHERE run_id = ?",
        (source_run_id,),
    ).fetchone()
    src.close()
    if meta is None:
        return {"error": "source_run_id not in scratchpad"}, 404

    spec_id = meta["spec_id"]
    try:
        params = _json.loads(meta["params_json"] or "{}")
    except (TypeError, ValueError):
        params = {}
    n_steps = int(meta["n_steps"] or 0)
    if n_steps and "n_steps" not in params:
        params["n_steps"] = n_steps

    # Build the study atomically: write to a temp dir inside studies_root,
    # then rename. Using studies_root as the temp parent ensures same filesystem.
    tmp_dir = tempfile.mkdtemp(dir=str(studies_root))
    tmp_path = Path(tmp_dir) / "build"
    try:
        tmp_path.mkdir()
        (tmp_path / "composites").mkdir()
        (tmp_path / "viz").mkdir()

        # Copy the run history into the new DB.
        copy_run_to_new_db(scratch, tmp_path / "runs.db", source_run_id)

        spec = {
            "schema_version": 3,
            "name": name,
            "created": datetime.date.today().isoformat(),
            "status": "ran",
            "objective": objective,
            "description": description,
            "baseline": {"composite": spec_id, "params": params},
            "variants": [],
            "runs": [{
                "run_id": source_run_id,
                "variant": None,
                "label": "promoted from scratchpad",
                "status": "completed",
            }],
            "visualizations": [],
            "conclusion": None,
            "parent_studies": [],
        }
        (tmp_path / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))

        # Atomic rename: tmp/build → studies/<name>.
        tmp_path.rename(dst)
    except Exception:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    else:
        # Clean up the now-empty temp dir (build/ was renamed out).
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"study": name, "url": f"/studies/{name}"}, 200


def _count_runs_for_study(name: str, spec: dict | None = None) -> int:
    """Count runs in studies/<name>/runs.db, falling back to len(spec.runs).

    F2: ``runs.db`` is the canonical source of truth. We still merge with
    the count from ``spec.get("runs")`` to surface legacy v3 specs that
    have historical ``runs:`` entries which never made it into the DB
    (e.g. workspaces predating pbg_runner). Returns the larger of the two
    so the dashboard never undercounts.
    """
    try:
        db_runs = _read_runs_db_for_study(name)
    except Exception:
        db_runs = []
    db_count = len(db_runs)
    spec_count = 0
    if spec is not None:
        spec_count = len(spec.get("runs") or [])
    # Most workspaces will have db_count >= spec_count after F2 lands.
    # If they ever diverge below, max() preserves the union — better to
    # over-report than under-report when both sources should agree.
    return max(db_count, spec_count)


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
    """Preferred emitter declared by the investigation that owns this study.

    Reads ``investigations/<slug>/investigation.yaml`` → ``runtime.default_emitter``
    for whichever investigation lists ``study_name`` in its ``studies[]``.
    Sits in the emitter-precedence chain BETWEEN the per-study
    ``runtime.emitter`` override (higher) and the workspace default (lower),
    so an investigation can standardise its emitter once — e.g. the PDMP
    investigation declares ``xarray`` and every member study runs XArray
    without per-study config. Returns the emitter name or None.
    """
    if not study_name:
        return None
    inv_dir = WORKSPACE / "investigations"
    if not inv_dir.is_dir():
        return None
    try:
        for invf in sorted(inv_dir.glob("*/investigation.yaml")):
            try:
                inv = yaml.safe_load(invf.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            studies = inv.get("studies") or []
            names = [s if isinstance(s, str) else (s or {}).get("study")
                     for s in studies]
            if study_name in names:
                rt = inv.get("runtime") or {}
                em = str((rt or {}).get("default_emitter") or "").strip().lower()
                return em or None
    except Exception:
        return None
    return None


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
    """Resolve a generator composite spec_id + params → a state dict.

    Returns (state, error_dict_or_None). Studies always reference generator
    composites (mirrors the generator branch of _post_composite_test_run).
    """
    import importlib
    import sys as _sys

    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, build_generator, discover_generators,
        )
    except ImportError:
        return None, {"error": "pbg_superpowers not importable"}
    if not _REGISTRY:
        discover_generators()
    entry = _REGISTRY.get(spec_id)
    # Allow `local:<name>` shorthand: look up by entry.name when no exact id match.
    if entry is None and spec_id.startswith("local:"):
        short_name = spec_id[len("local:"):]
        entry = next((e for e in _REGISTRY.values() if e.name == short_name), None)
    if entry is None:
        # The registry may be stale (cleared by test teardown or a registry
        # reset). Force-reload the module that defines this composite so its
        # @composite_generator decorators re-fire, then retry.
        # spec_id is like "pkg.composites.name"; the defining module is
        # typically "pkg.composites" or "pkg.composites.name".
        candidate_mods = []
        if ".composites." in spec_id:
            # "pkg.composites.name" → try "pkg.composites.name", "pkg.composites", "pkg"
            parts = spec_id.split(".")
            for i in range(len(parts), 0, -1):
                candidate_mods.append(".".join(parts[:i]))
        for mod_name in candidate_mods:
            if mod_name in _sys.modules:
                try:
                    importlib.reload(_sys.modules[mod_name])
                except Exception:  # noqa: BLE001
                    pass
        if candidate_mods:
            discover_generators()
        entry = _REGISTRY.get(spec_id)
    if entry is None:
        # mem3dg-readdy friction #21: fall back to file-discovered composites
        # (the OTHER registry — pbg_superpowers.composite_discovery walks
        # *.composite.{yaml,json} on disk). A workspace that ships YAML
        # specs without @composite_generator decorators is still runnable
        # via this path, removing the "Composites tab lists it but Run
        # rejects it" foot-gun.
        try:
            from pbg_superpowers.composite_discovery import discover_composites
            specs = discover_composites()
        except Exception:  # noqa: BLE001
            specs = {}
        yaml_spec = specs.get(spec_id)
        # Allow the same `local:<name>` shorthand on the YAML side.
        if yaml_spec is None and spec_id.startswith("local:"):
            short_name = spec_id[len("local:"):]
            yaml_spec = next(
                (s for sid, s in specs.items() if sid.endswith("." + short_name)
                 or s.get("name") == short_name),
                None,
            )
        if yaml_spec is not None:
            state = yaml_spec.get("state") if isinstance(yaml_spec, dict) else None
            if isinstance(state, dict):
                # YAML composites don't support `params` overrides yet —
                # generators are the path for parametrized runs. Surface
                # this clearly rather than silently dropping the kwargs.
                if params:
                    return None, {"error": (
                        f"YAML composite {spec_id!r} resolved but `params:` "
                        "overrides aren't supported on file-discovered specs. "
                        "Promote to @composite_generator to use param overrides."
                    )}
                return state, None
            return None, {"error": (
                f"YAML composite {spec_id!r} has no `state:` block "
                "(check the spec shape)"
            )}
        return None, {"error": (
            f"composite {spec_id!r} not found in either the "
            "@composite_generator registry OR the file-discovery index "
            "(*.composite.{yaml,json}). Add an @composite_generator "
            "function or ship a composite YAML."
        )}
    try:
        doc = build_generator(entry, overrides=params)
    except Exception as e:  # noqa: BLE001
        return None, {"error": f"generator build failed: {e}"}
    if isinstance(doc, dict) and "state" in doc and isinstance(doc["state"], dict):
        return doc["state"], None
    return doc, None


def _post_study_run_baseline_for_test(ws_root, body):
    """Run a Study's baseline composite. Returns (response_dict, status_code).

    Body:
      study:     <name>  (or `name`/`investigation`)
      composite: <baseline-entry name>  (optional; default = baseline[0].name)
      steps:     <int>   (optional; overrides params.n_steps; default 5)
    """
    from vivarium_dashboard.lib import composite_runs as cr

    name = _study_name_from_body(body)
    if not name:
        return {"error": "missing study"}, 400
    # Resolve study dir from ws_root so _for_test callers don't need WORKSPACE patched.
    studies_path = ws_root / "studies" / name
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / name
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    # Auto-migrate legacy v2-shape specs (baseline: <str>, variants: [...]) to
    # the v3 list shape this handler expects. In-memory only; doesn't rewrite
    # the file. Keeps legacy investigations/spec.yaml usable.
    from vivarium_dashboard.lib.spec_migration import migrate_v2_to_v3
    spec = migrate_v2_to_v3(spec)
    # v4-redesign projection: synthesises legacy fields (baseline list,
    # variants list, behavior_tests, simulation_set) from a v4 conditions
    # block. Idempotent on v3 (no-op when conditions is absent).
    if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
        from vivarium_dashboard.lib.investigations import _project_v4_redesign_to_legacy_view
        spec = _project_v4_redesign_to_legacy_view(spec)
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return {"error": "study has no baseline composites"}, 400

    requested = (body.get("composite") or "").strip()
    if requested:
        entry = next((b for b in baseline if isinstance(b, dict) and b.get("name") == requested),
                     None)
        if entry is None:
            return {"error": f"baseline composite {requested!r} not found"}, 404
    else:
        entry = baseline[0]
    spec_id = entry.get("composite")
    if not spec_id:
        return {"error": f"baseline entry {entry.get('name')!r} has no composite"}, 400

    params = dict(entry.get("params") or {})
    params_n_steps = params.pop("n_steps", None)
    generator_overrides = params

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
    # XArrayEmitter buffers ~hundreds of ticks before flushing, so the legacy
    # 5-tick default produces empty zarr stores. Workspaces declare a sensible
    # baseline run length via runtime.default_n_steps; we fall back to 5 only
    # if neither the body, the study yaml, nor the workspace specifies one
    # (preserves the legacy quick-smoke behaviour for SQLite workspaces).
    _runtime = (ws_data.get("runtime") or {}) if isinstance(ws_data, dict) else {}
    ws_default_n_steps = _runtime.get("default_n_steps")
    steps = int(body.get("steps") or params_n_steps or ws_default_n_steps or 5)

    state, err = _resolve_study_baseline_state(pkg, spec_id, generator_overrides)
    if err is not None:
        return err, 400

    full_params = dict(generator_overrides)
    if params_n_steps is not None:
        full_params["n_steps"] = params_n_steps

    db_file = str(study_dir / "runs.db")
    run_id = cr.generate_run_id(spec_id, full_params)
    label = entry.get("name") or "baseline"
    # v2ecoli friction #6: subprocess timeout from study yaml so a 3600-step
    # baseline isn't killed by the 120s default. Per-study override.
    runtime_cfg = (spec.get("runtime") or {}) if isinstance(spec.get("runtime"), dict) else {}
    timeout_s = int(runtime_cfg.get("subprocess_timeout_s") or 1800)
    # v2ecoli friction #14: derive emit_paths from spec observables so the
    # injected SQLiteEmitter captures real biology, not just ticks.
    emit_paths = cr.collect_emit_paths_from_spec(spec)
    # Per-study overrides — all win over workspace defaults. Emitter precedence:
    # study runtime.emitter > investigation runtime.default_emitter > workspace.
    study_emitter = runtime_cfg.get("emitter") or _investigation_emitter_for_study(spec.get("name"))
    study_max_generations = runtime_cfg.get("max_generations")
    study_single_daughters = runtime_cfg.get("single_daughters")
    response, code = _run_composite_subprocess(
        pkg=pkg, state=state, steps=steps, db_file=db_file,
        run_id=run_id, spec_id=spec_id, label=label, sim_name=label,
        overrides=generator_overrides, timeout=timeout_s,
        emit_paths=emit_paths, study_emitter=study_emitter,
        study_max_generations=study_max_generations,
        study_single_daughters=study_single_daughters,
    )
    if code == 200:
        # F2: do NOT append to study.yaml.runs[] — the runs_meta row
        # written by _run_composite_subprocess (via composite_runs.save_metadata)
        # IS the canonical record. The Runs tab reads runs.db directly via
        # _read_runs_db_for_study + _enrich_runs_with_meta; appending here
        # would duplicate the same fact in two places and let them drift.
        #
        # Render canonical viz: composite defaults from
        # @composite_generator(visualizations=...) merged with Study-declared
        # ones (Study wins on name collision). Writes HTML under
        # <study_dir>/viz/. Per-viz errors absorbed; others still render.
        viz_files, viz_errors = _render_study_visualizations(
            study_dir, spec, spec_id,
        )
        if viz_files:
            response.setdefault("viz_files", []).extend(viz_files)
        if viz_errors:
            response.setdefault("viz_errors", []).extend(viz_errors)
        # post_run_scripts: study-yaml-declared scripts to invoke after the
        # auto-render dispatch. Pattern for hand-rolled render scripts that
        # don't fit the @Visualization class registry (e.g. chromosome-state
        # snapshotters that run their own sim and write HTML directly).
        # Schema:
        #   post_run_scripts:
        #   - path: scripts/render_chromosome_timeline.py
        #     args: ["--study", "dnaa-02", "--spec", "...", "--steps", "600"]
        #     timeout_s: 1800
        script_files, script_errors = _run_post_run_scripts(spec, ws_root)
        if script_files:
            response.setdefault("post_run_script_files", []).extend(script_files)
        if script_errors:
            response.setdefault("post_run_script_errors", []).extend(script_errors)
        # Post-run analysis hook: run spec.analyses[] steps over the parquet emitter
        # output.  Synchronous (runs before this HTTP response returns) so the
        # analysis outputs are on disk by the time the client refreshes.
        analysis_files, analysis_errors = _run_study_analyses(
            study_dir, spec, run_id, ws_root)
        if analysis_files:
            response.setdefault("analysis_files", []).extend(analysis_files)
        if analysis_errors:
            response.setdefault("analysis_errors", []).extend(analysis_errors)
        try:
            from pbg_superpowers import study_outcomes
            study_outcomes.sync(study_dir)  # record runs + compute outcomes
        except Exception as exc:  # never fail a successful run on a record error
            print(f"[study_outcomes] sync failed: {exc}", file=sys.stderr)
        _sync_parent_investigation(ws_root, study_dir)  # SP1: roll up to investigation
    return response, code


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
    """Invoke each ``spec.post_run_scripts[]`` entry as a subprocess.

    Each entry: ``{path: <rel-to-ws>, args: [...], timeout_s: 1800}``.
    Scripts run with cwd=ws_root using the same Python interpreter as the
    dashboard. Stdout/stderr are captured but discarded unless the script
    fails (script's own viz writes go straight to disk). Returns
    ``(written_files, errors)`` — written_files lists newly-mtime'd HTML
    files under studies/<slug>/viz/ (for response surfacing).
    """
    entries = spec.get("post_run_scripts") or []
    if not entries:
        return [], []
    import sys as _sys
    import subprocess as _subprocess
    import time as _time

    written: list[str] = []
    errors: list[dict] = []
    t_start = _time.time()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rel_path = entry.get("path")
        if not rel_path:
            continue
        script_path = ws_root / rel_path
        if not script_path.is_file():
            errors.append({"script": rel_path, "error": "script not found"})
            continue
        args = [str(a) for a in (entry.get("args") or [])]
        timeout_s = int(entry.get("timeout_s") or 1800)
        try:
            result = _subprocess.run(
                [_sys.executable, str(script_path), *args],
                cwd=str(ws_root), timeout=timeout_s,
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                # surface stderr tail for debugging; full stdout/stderr stay
                # in the script's own logs if it wrote any.
                tail = (result.stderr or result.stdout or "")[-500:]
                errors.append({
                    "script": rel_path, "args": args,
                    "returncode": result.returncode, "stderr_tail": tail,
                })
        except _subprocess.TimeoutExpired:
            errors.append({"script": rel_path, "error": f"timed out after {timeout_s}s"})
        except Exception as e:  # noqa: BLE001 — keep other scripts running
            errors.append({"script": rel_path, "error": f"{type(e).__name__}: {e}"})
    # collect HTML files written during this batch (mtime newer than start)
    for study_dir in (ws_root / "studies").iterdir() if (ws_root / "studies").is_dir() else []:
        viz_dir = study_dir / "viz"
        if not viz_dir.is_dir():
            continue
        for html in viz_dir.glob("*.html"):
            if html.stat().st_mtime >= t_start:
                written.append(str(html.relative_to(ws_root)))
    return written, errors


def _build_analysis_options(entries: list[dict]) -> tuple[dict, list[dict]]:
    """Translate ``spec.analyses`` entries into v2ecoli ``analysis_options``.

    Looks up each entry's ``name`` in ``v2ecoli.workflow.analysis.ANALYSIS_REGISTRY``
    to discover its ``scale``, then groups it into
    ``{scale: {name: params}}``.

    Returns ``(analysis_options, errors)`` where ``errors`` lists dicts for
    unknown analysis names.  Importable as a pure helper so it is unit-testable
    without a workspace.
    """
    try:
        from v2ecoli.workflow.analysis import ANALYSIS_REGISTRY  # type: ignore[import]
    except ImportError:
        return {}, [{"error": "v2ecoli not installed; cannot resolve analysis scales"}]

    analysis_options: dict[str, dict] = {}
    errors: list[dict] = []
    for entry in entries:
        name = entry.get("name")
        if not name:
            continue
        step_cls = ANALYSIS_REGISTRY.get(name)
        if step_cls is None:
            errors.append({"analysis": name, "error": f"unknown analysis {name!r} (not in ANALYSIS_REGISTRY)"})
            continue
        scale = getattr(step_cls, "scale", None)
        if not scale:
            errors.append({"analysis": name, "error": f"analysis {name!r} has no scale attribute"})
            continue
        analysis_options.setdefault(scale, {})[name] = entry.get("params") or {}
    return analysis_options, errors


def _run_study_analyses(study_dir: Path, spec: dict, run_id: str,
                        ws_root: Path) -> tuple[list[str], list[dict]]:
    """Run the study's configured ``analyses:`` steps over the run's parquet output.

    Mirrors ``_run_post_run_scripts`` in structure.  Collects the written
    ``ptools/*.tsv``, ``viz/*.html``, and ``analysis.json`` into
    ``written_files``, and per-analysis errors into ``errors``.
    Returns ``(written_files, errors)`` — never raises.

    Requires:
      - v2ecoli installed in the same venv (guarded import).
      - A parquet emitter run under ``study_dir/parquet-runs/``.
      - A sim_data pickle somewhere in the workspace (searched under ws_root).
        Analyses that don't need sim_data will still run even if none is found.
    """
    try:
        entries = list(spec.get("analyses") or [])
        if not entries:
            return [], []

        # 1. Build analysis_options from spec.analyses entries.
        analysis_options, build_errors = _build_analysis_options(entries)
        if not analysis_options:
            return [], build_errors

        # 2. Locate the most-recent parquet sweep dir.
        from vivarium_dashboard.lib.study_charts import _latest_parquet_for_study
        hive_root = _latest_parquet_for_study(study_dir)
        if hive_root is None:
            return [], [{"error": "no parquet run found under study dir; analyses need parquet emitter output"}]
        # run_analyses globs history parquet under sweep_dir; the hive root is
        # <exp>/history so its parent <exp> is the sweep_dir.
        sweep_dir = hive_root.parent

        # 3. Resolve workspace sim_data (optional — analyses that don't need it still run).
        sim_data_path: str | None = None
        for pat in ("out/**/simData*.cPickle", "out/**/sim_data*.cPickle",
                    "simData*.cPickle", "sim_data*.cPickle",
                    "**/simData*.cPickle", "**/sim_data*.cPickle"):
            import glob as _glob
            hits = _glob.glob(str(ws_root / pat), recursive=True)
            if hits:
                sim_data_path = hits[0]
                break

        # 4. Run analyses.
        from v2ecoli.workflow.analysis_runner import run_analyses  # type: ignore[import]
        import v2ecoli.workflow.analyses  # noqa: F401 — register analysis ports  # type: ignore[import]

        t_start = __import__("time").time()
        results = run_analyses(str(sweep_dir), analysis_options, sim_data_path=sim_data_path)

        # 5. Collect written files (mtime newer than call start).
        written: list[str] = []
        for sub in ("ptools", "viz"):
            sub_dir = sweep_dir / sub
            if not sub_dir.is_dir():
                continue
            for f in sub_dir.iterdir():
                if f.is_file() and f.stat().st_mtime >= t_start:
                    written.append(str(f))
        analysis_json = sweep_dir / "analysis.json"
        if analysis_json.is_file() and analysis_json.stat().st_mtime >= t_start:
            written.append(str(analysis_json))

        # 6. Collect per-group errors from the results dict.
        errors: list[dict] = list(build_errors)
        for scale_results in results.values():
            for name, groups in (scale_results or {}).items():
                for gstr, val in (groups or {}).items():
                    if isinstance(val, dict) and "error" in val:
                        errors.append({"analysis": name, "group": gstr, "error": val["error"]})
        return written, errors

    except Exception as exc:  # noqa: BLE001 — never crash the run handler
        import traceback
        return [], [{"error": f"_run_study_analyses failed: {type(exc).__name__}: {exc}",
                     "traceback": traceback.format_exc()}]


def _zarr_store_for_sim(study_db: Path, sim_name: str | None) -> Path | None:
    """Find the most-recent XArrayEmitter zarr store for a sim_name in a study.

    XArrayEmitter runs (via the subprocess template's xarray branch) write a
    per-run zarr directory next to the SQLite db at
    ``<study>/runs.<run_id>.zarr``. To map a sim_name → zarr path:

      1. Read runs_meta from the study's SQLite db to find the latest
         completed run_id for that sim_name (runs_meta is written for both
         SQLite-backed AND xarray-backed runs).
      2. Check whether the corresponding zarr dir exists on disk.

    Returns the zarr path if it exists, else None (caller falls back to SQLite).
    """
    if not sim_name or not study_db or not study_db.exists():
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(str(study_db))
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "runs_meta" not in tables:
                return None
            row = conn.execute(
                "SELECT run_id FROM runs_meta WHERE sim_name=? "
                "AND status='completed' ORDER BY started_at DESC LIMIT 1",
                (sim_name,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        run_id = row[0]
    except Exception:
        return None
    # Construct the zarr path: <db_stem>.<run_id>.zarr next to the SQLite db.
    zarr_dir = study_db.parent / f"{study_db.stem}.{run_id}.zarr"
    return zarr_dir if zarr_dir.is_dir() else None


def _render_study_visualizations(study_dir, spec, spec_id):
    """Render canonical + Study-declared visualizations after a completed run.

    Merges the composite's ``@composite_generator(visualizations=...)``
    defaults (from ``pbg_superpowers._REGISTRY``) with
    ``spec.visualizations`` (Study entries win on name collision), then
    delegates to ``vivarium_dashboard.lib.investigations.render_visualizations``
    to render against ``study_dir/runs.db``.

    Returns ``(viz_files, viz_errors)`` — viz_files lists paths relative
    to ``study_dir`` of HTML files written; viz_errors is a list of
    ``{error: <msg>}`` for global failures (per-viz failures are handled
    inside ``render_visualizations`` and surface as error-stub HTML).
    """
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, discover_generators,
        )
        from vivarium_dashboard.lib.investigations import render_visualizations
    except ImportError as e:
        return [], [{"error": f"viz render deps missing: {e}"}]

    if not _REGISTRY:
        discover_generators()
    entry = _REGISTRY.get(spec_id)
    default_viz = list(getattr(entry, "visualizations", []) or []) if entry else []
    study_viz = list(spec.get("visualizations") or [])
    by_name: dict[str, dict] = {}
    for v in default_viz + study_viz:
        if isinstance(v, dict) and v.get("name"):
            by_name[v["name"]] = v
    merged = list(by_name.values())
    if not merged:
        return [], []

    # mem3dg-readdy friction #29: study.yaml needed `address:` (caught by
    # the report linter in pbg-superpowers / friction #26), but the same
    # gap existed on the @composite_generator(visualizations=[...]) side
    # and silently won on name collisions. Single source of truth fix:
    # default any unaddressed entry from workspace.yaml.visualizations[].class
    # by name, before render_visualizations gets a chance to KeyError.
    name_to_class: dict[str, str] = {}
    try:
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
        for ws_viz in ws_data.get("visualizations", []) or []:
            if isinstance(ws_viz, dict) and ws_viz.get("name") and ws_viz.get("class"):
                name_to_class[ws_viz["name"]] = ws_viz["class"]
    except Exception:  # noqa: BLE001 — defaulting is best-effort
        pass
    for v in merged:
        if not v.get("address"):
            cls = name_to_class.get(v.get("name", ""))
            if cls:
                v["address"] = f"local:{cls}"

    effective_spec = dict(spec)
    effective_spec["visualizations"] = merged

    # Build core + viz registry + build_and_run hook — render_visualizations
    # refuses to operate without a build_and_run callable, and bigraph-schema's
    # `local:<name>` address resolution goes through `core.link_registry`, so
    # viz classes must be registered onto the core itself (not just our local
    # dict). Mirrors the legacy /api/investigation-run wiring.
    _ws_add_to_sys_path()
    try:
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        pkg_name = ws_data.get("package_path") or (
            "pbg_" + ws_data.get("name", "").replace("-", "_"))
        core_module = __import__(f"{pkg_name}.core", fromlist=["build_core"])
        core = core_module.build_core()
        registry = dict(core.link_registry)
    except Exception as e:  # noqa: BLE001
        return [], [{"error": f"failed to build core for viz: {e}"}]

    # pbg-superpowers default Visualization classes.
    try:
        from pbg_superpowers.visualizations import (
            TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap,
        )
        for cls in (TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap):
            core.register_link(cls.__name__, cls)
            registry[cls.__name__] = cls
    except ImportError:
        pass

    # Discover every Visualization subclass loaded in the process (e.g.
    # v2ecoli's WorkflowVisualization / NetworkVisualization / ColonyVisualization,
    # plus any future wrapper-shipped viz). Walks the __subclasses__ tree
    # so classes only loaded transitively still register.
    try:
        from pbg_superpowers.visualization import Visualization
        import importlib
        # Force-load workspace + every installed bigraph-schema-dependent
        # package so their @Visualization classes appear in the subclass tree.
        from pbg_superpowers.composite_generator import discover_generators
        discover_generators()  # imports the same packages composite discovery walks

        def _walk(cls):
            for sub in cls.__subclasses__():
                yield sub
                yield from _walk(sub)
        for sub in _walk(Visualization):
            if sub.__name__ in registry:
                continue
            try:
                core.register_link(sub.__name__, sub)
                registry[sub.__name__] = sub
            except Exception:
                pass
    except Exception:  # noqa: BLE001 — discovery is best-effort
        pass

    def build_and_run(viz_doc, registry_arg):
        """Hook for render_visualizations: build composite, run 1 step,
        return the output_store html string."""
        from process_bigraph import Composite
        composite = Composite({'state': viz_doc}, core=core)
        composite.run(1)
        state = composite.state
        html = state.get('output_store')
        if isinstance(html, dict):
            html = html.get('value') or html.get('_value') or ''
        return html if isinstance(html, str) else ''

    try:
        paths = render_visualizations(
            effective_spec,
            study_dir,
            spec.get("name", ""),
            core_registry=registry,
            build_and_run=build_and_run,
        )
        written = [str(Path(p).relative_to(study_dir)) for p in paths]
        # Auto-purge stale viz: after rendering, delete any *.html in
        # studies/<slug>/viz/ whose mtime is older than the latest run's
        # started_at AND not in the just-written set. Keeps the report
        # showing only current-run output without manual cleanup.
        # `comparative_*` viz are excluded — those are owned by the
        # investigation-end hook (_render_investigation_comparative_visualisations)
        # which fires on a different schedule; purging them on a per-study
        # run would delete legitimately-current cross-run overlays.
        _purge_stale_viz(study_dir, written)
        return written, []
    except Exception as e:  # noqa: BLE001
        return [], [{"error": f"render_visualizations failed: "
                     f"{type(e).__name__}: {e}"}]


def _purge_stale_viz(study_dir: Path, just_written: list[str]) -> None:
    """Delete *.html in study_dir/viz/ whose mtime is older than the
    latest run's started_at AND not in the just-written set AND not
    a comparative_ viz (those are owned by a separate dispatch).

    No-op on any error — viz cleanup is best-effort, not load-bearing.
    """
    try:
        viz_dir = study_dir / "viz"
        runs_db = study_dir / "runs.db"
        if not viz_dir.is_dir() or not runs_db.is_file():
            return
        cutoff = _latest_run_timestamp(runs_db)
        if cutoff is None:
            return
        kept_names = {Path(p).name for p in just_written}
        for html in viz_dir.glob("*.html"):
            if html.name in kept_names:
                continue
            if html.name.startswith("comparative_"):
                continue
            try:
                if html.stat().st_mtime < cutoff:
                    html.unlink()
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass


def _post_study_run_all_baselines_for_test(ws_root, body):
    """Run every entry in spec.baseline[] sequentially. Returns
    ``({results: [...], errors: [...]}, status_code)``.

    Sugar for multi-baseline Studies (e.g. architecture comparisons) where
    the UI wants a single "run all" affordance instead of clicking through
    N per-baseline buttons. Each per-entry run is dispatched through the
    existing :func:`_post_study_run_baseline_for_test` so the persistence,
    canonical-viz rendering, and run-record bookkeeping all stay identical
    — this function only sequences the calls and aggregates the responses.

    Body:
      study: <name>             # required
      steps: <int>              # optional; passed through to each run

    Response (status 200 when every baseline succeeds; 207 multi-status
    when at least one fails but others succeeded; 4xx/5xx propagated when
    none can be run, e.g. the study itself doesn't exist):

      {
        "results": [
          {"composite": <entry-name>, "status": "completed", "run_id": ..., "viz_files": [...]},
          ...
        ],
        "errors": [
          {"composite": <entry-name>, "status": <http-code>, "error": "..."},
          ...
        ],
      }
    """
    name = _study_name_from_body(body)
    if not name:
        return {"error": "missing study"}, 400
    studies_path = ws_root / "studies" / name
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / name
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    from vivarium_dashboard.lib.spec_migration import migrate_v2_to_v3
    spec = migrate_v2_to_v3(spec)
    # v4-redesign projection: synthesises legacy fields (baseline list,
    # variants list, behavior_tests, simulation_set) from a v4 conditions
    # block. Idempotent on v3 (no-op when conditions is absent).
    if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
        from vivarium_dashboard.lib.investigations import _project_v4_redesign_to_legacy_view
        spec = _project_v4_redesign_to_legacy_view(spec)
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return {"error": "study has no baseline composites"}, 400

    steps = body.get("steps")
    results: list = []
    errors: list = []
    for entry in baseline:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        sub_body = {"study": name, "composite": entry["name"]}
        if steps is not None:
            sub_body["steps"] = steps
        sub_response, sub_code = _post_study_run_baseline_for_test(ws_root, sub_body)
        if sub_code == 200:
            results.append({
                "composite": entry["name"],
                **sub_response,
            })
        else:
            errors.append({
                "composite": entry["name"],
                "status": sub_code,
                "error": sub_response.get("error") if isinstance(sub_response, dict) else str(sub_response),
            })

    if not results and errors:
        # Nothing ran — propagate the first error's status as the overall code.
        return {"results": results, "errors": errors}, errors[0]["status"]
    code = 207 if errors else 200
    return {"results": results, "errors": errors}, code


def _post_study_run_variant_for_test(ws_root, body):
    """Run a Study variant (baseline + param overrides). Returns (response_dict, status_code).

    Body:
      study:   <name>
      variant: <variant name>
    Resolves the variant's `base_composite` against the study's `baseline[]`,
    layers `parameter_overrides` on top of that entry's `params`, and runs.

    SP2a: a variant declaring `kind: sweep` / `kind: seeds` is an ENSEMBLE — it
    is DELEGATED to v2ecoli-workflow (which packs every grid point into ONE
    parquet hive store), not executed as N independent dashboard subprocesses.
    """
    from vivarium_dashboard.lib import composite_runs as cr
    from vivarium_dashboard.lib.ensemble_config import (
        build_workflow_config, delegation_available, is_delegatable_sweep,
    )

    name = _study_name_from_body(body)
    variant_name = (body.get("variant") or "").strip()
    if not name or not variant_name:
        return {"error": "missing study or variant"}, 400
    # Resolve study dir from ws_root (matches Task 5 pattern; supports
    # standalone tests without monkeypatching WORKSPACE).
    studies_path = ws_root / "studies" / name
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / name
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    # Auto-migrate legacy v2-shape specs to v3 list shape (see run-baseline).
    from vivarium_dashboard.lib.spec_migration import migrate_v2_to_v3
    spec = migrate_v2_to_v3(spec)
    # v4-redesign projection: synthesises legacy fields (baseline list,
    # variants list, behavior_tests, simulation_set) from a v4 conditions
    # block. Idempotent on v3 (no-op when conditions is absent).
    if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
        from vivarium_dashboard.lib.investigations import _project_v4_redesign_to_legacy_view
        spec = _project_v4_redesign_to_legacy_view(spec)
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return {"error": "study has no baseline composites"}, 400

    variant = next((v for v in (spec.get("variants") or [])
                    if isinstance(v, dict) and v.get("name") == variant_name), None)
    if variant is None:
        return {"error": f"variant {variant_name!r} not found"}, 404

    # Variant resolution: a variant may either
    #   (a) point at its own ``composite`` directly (v4 redesign — a
    #       variant can use a different generator than the baseline), or
    #   (b) reference a baseline entry by name via ``base_composite``,
    #       inheriting its composite + params (legacy v3 shape).
    # Direct composite wins when present.
    direct_composite = (variant.get("composite") or "").strip()
    if direct_composite:
        spec_id = direct_composite
        params: dict = {}  # no baseline params inheritance — variant is standalone
    else:
        base_name = (variant.get("base_composite") or "").strip()
        if base_name:
            entry = next((b for b in baseline
                          if isinstance(b, dict) and b.get("name") == base_name), None)
            if entry is None:
                return {"error": f"variant base_composite {base_name!r} not in baseline"}, 404
        else:
            entry = baseline[0]
        spec_id = entry.get("composite")
        if not spec_id:
            return {"error": f"baseline entry {entry.get('name')!r} has no composite"}, 400
        params = dict(entry.get("params") or {})

    overrides = variant.get("parameter_overrides") or variant.get("params") or {}
    params.update(overrides)

    params_n_steps = params.pop("n_steps", None)
    generator_overrides = params

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
    # Same workspace-level default as the baseline path — see comment there.
    _runtime = (ws_data.get("runtime") or {}) if isinstance(ws_data, dict) else {}
    ws_default_n_steps = _runtime.get("default_n_steps")
    steps = int(body.get("steps") or params_n_steps or ws_default_n_steps or 5)

    kind = variant.get("kind")
    if kind in ("sweep", "seeds"):
        # Review FIX 1: branch on the variant being an ENSEMBLE first. A
        # `kind: sweep`/`kind: seeds` variant is NEVER silently single-run as a
        # baseline — if it is not delegatable (bare-key sweep, missing/zero
        # n_seeds) it must error CLEARLY rather than ignore the declared sweep.
        if not is_delegatable_sweep(variant):
            if kind == "seeds":
                return ({"error": "kind: seeds requires n_seeds >= 1"}, 422)
            # kind == "sweep" — empty or bare-key (non-"<proc>.<key>") targets.
            sweep_over = variant.get("sweep_over") or {}
            if not sweep_over:
                return ({"error": "kind: sweep requires a non-empty sweep_over "
                         "of '<process>.<key>' targets"}, 422)
            bad = [k for k in sweep_over if "." not in str(k)]
            return ({"error": "sweep targets must be '<process>.<key>' "
                     f"(got bare keys: {bad})"}, 422)
        # SP2a delegation: hand the whole ensemble to v2ecoli-workflow once. It
        # packs all sweep/seed points into ONE parquet hive store under
        # out/<run_id>/, which the post-run sync records as a single run. We do
        # NOT resolve/build the composite here (no _resolve_study_baseline_state)
        # — the workflow engine builds every branch itself.
        if not delegation_available(ws_root):
            return ({"error": "ensemble sweep/seeds runs require a v2ecoli "
                     "workspace (v2ecoli-workflow) with `<proc>.<key>` sweep "
                     "targets; this workspace cannot delegate"}, 422)
        full_params = dict(generator_overrides)
        if params_n_steps is not None:
            full_params["n_steps"] = params_n_steps
        run_id = cr.generate_run_id(spec_id, full_params)
        runtime_cfg = (spec.get("runtime") or {}) if isinstance(spec.get("runtime"), dict) else {}
        timeout_s = int(runtime_cfg.get("subprocess_timeout_s") or 1800)
        out_dir = study_dir / "out" / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        # experiment_id == run_id so the packed store + the recorded run align.
        cfg = build_workflow_config(variant, run_id, str(out_dir))
        cfg_path = out_dir / "config.json"
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        response, code = _invoke_v2ecoli_workflow(
            str(cfg_path), out_dir, ws_root, timeout_s)
    else:
        state, err = _resolve_study_baseline_state(pkg, spec_id, generator_overrides)
        if err is not None:
            return err, 400

        full_params = dict(generator_overrides)
        if params_n_steps is not None:
            full_params["n_steps"] = params_n_steps

        db_file = str(study_dir / "runs.db")
        run_id = cr.generate_run_id(spec_id, full_params)
        # v2ecoli friction #6: per-study subprocess timeout.
        runtime_cfg = (spec.get("runtime") or {}) if isinstance(spec.get("runtime"), dict) else {}
        timeout_s = int(runtime_cfg.get("subprocess_timeout_s") or 1800)
        # v2ecoli friction #14: thread observables to the subprocess (same as
        # baseline path) so variant runs also capture biology in history.state.
        emit_paths = cr.collect_emit_paths_from_spec(spec)
        # Per-study overrides — see baseline path for rationale. Emitter precedence:
        # study runtime.emitter > investigation runtime.default_emitter > workspace.
        study_emitter = runtime_cfg.get("emitter") or _investigation_emitter_for_study(spec.get("name"))
        study_max_generations = runtime_cfg.get("max_generations")
        study_single_daughters = runtime_cfg.get("single_daughters")
        response, code = _run_composite_subprocess(
            pkg=pkg, state=state, steps=steps, db_file=db_file,
            run_id=run_id, spec_id=spec_id, label=variant_name,
            sim_name=variant_name, overrides=generator_overrides,
            timeout=timeout_s, emit_paths=emit_paths,
            study_emitter=study_emitter,
            study_max_generations=study_max_generations,
            study_single_daughters=study_single_daughters,
        )
    # F2: no _append_study_run — the runs_meta row is the canonical record;
    # see the matching note in run-baseline above.
    if code == 200:
        # Same canonical-viz + post-run-scripts dispatch as the baseline path
        # so variants also refresh chromosome viz etc.
        viz_files, viz_errors = _render_study_visualizations(
            study_dir, spec, spec_id,
        )
        if viz_files:
            response.setdefault("viz_files", []).extend(viz_files)
        if viz_errors:
            response.setdefault("viz_errors", []).extend(viz_errors)
        script_files, script_errors = _run_post_run_scripts(spec, ws_root)
        if script_files:
            response.setdefault("post_run_script_files", []).extend(script_files)
        if script_errors:
            response.setdefault("post_run_script_errors", []).extend(script_errors)
        # Post-run analysis hook: mirrors baseline path — run spec.analyses[] steps.
        analysis_files, analysis_errors = _run_study_analyses(
            study_dir, spec, run_id, ws_root)
        if analysis_files:
            response.setdefault("analysis_files", []).extend(analysis_files)
        if analysis_errors:
            response.setdefault("analysis_errors", []).extend(analysis_errors)
        try:
            from pbg_superpowers import study_outcomes
            study_outcomes.sync(study_dir)  # record runs + compute outcomes
        except Exception as exc:  # never fail a successful run on a record error
            print(f"[study_outcomes] sync failed: {exc}", file=sys.stderr)
        _sync_parent_investigation(ws_root, study_dir)  # SP1: roll up to investigation
    return response, code


def _post_study_sync_runs_for_test(ws_root, body: dict):
    """Reconcile a study's runs.db into study.yaml runs[]. Returns (response_dict, status_code).

    Body:
      study: <slug>
    """
    from pbg_superpowers import study_outcomes
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
    slug = (body or {}).get("study")
    if not slug:
        return {"error": "study slug required"}, 400
    try:
        study_dir = WorkspacePaths.load(Path(ws_root)).study_dir(slug)
    except FileNotFoundError:
        return {"error": f"study not found: {slug}"}, 404
    summary = study_outcomes.sync(study_dir)  # record runs + compute outcomes
    _sync_parent_investigation(ws_root, study_dir)  # SP1: roll up to investigation
    return {"ok": True, "summary": summary}, 200


def _post_study_variant_add_for_test(ws_root, body):
    """Add a variant entry to study.yaml. Returns (response_dict, status_code).

    Body:
      study or investigation:  <study name>
      name:                    <variant name>
      base_composite:          <baseline entry name> (required)
      parameter_overrides:     <dict>  (optional; defaults to {})
    """
    study = (body.get("study") or body.get("investigation") or "").strip()
    variant_name = (body.get("name") or "").strip()
    base_composite = (body.get("base_composite") or "").strip()
    if not study or not variant_name:
        return {"error": "missing study or variant name"}, 400
    if not base_composite:
        return {"error": "missing base_composite"}, 400
    overrides = body.get("parameter_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        return {"error": "parameter_overrides must be an object"}, 400

    # Inline ws_root-based path resolution (matches Task 5/6 pattern).
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    baseline = spec.get("baseline") or []
    baseline_names = {b.get("name") for b in baseline if isinstance(b, dict)}
    if base_composite not in baseline_names:
        return {"error": f"base_composite {base_composite!r} not in baseline"}, 404

    variants = spec.setdefault("variants", [])
    if any(v.get("name") == variant_name for v in variants if isinstance(v, dict)):
        return {"error": f"variant {variant_name!r} already exists"}, 409

    variants.append({
        "name": variant_name,
        "base_composite": base_composite,
        "parameter_overrides": overrides or {},
    })
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True, "name": variant_name}, 200


def _post_study_variant_delete_for_test(ws_root, body):
    """Remove a variant entry from study.yaml. Returns (response_dict, status_code)."""
    study = _study_name_from_body(body)
    variant_name = (body.get("variant") or "").strip()
    if not study or not variant_name:
        return {"error": "missing study or variant"}, 400
    sf = _study_spec_file(_study_dir(study))
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    variants = spec.get("variants") or []
    remaining = [v for v in variants if v.get("name") != variant_name]
    if len(remaining) == len(variants):
        return {"error": f"variant {variant_name!r} not found"}, 404
    spec["variants"] = remaining
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


def _post_study_variant_set_params_for_test(ws_root, body):
    """Replace a variant's parameter_overrides. Returns (response_dict, status_code).

    Body:
      study:                <name>
      variant:              <variant name>
      parameter_overrides:  <dict>  (replaces; does not merge)
    """
    study = _study_name_from_body(body)
    variant_name = (body.get("variant") or "").strip()
    overrides = body.get("parameter_overrides")
    if not study or not variant_name:
        return {"error": "missing study or variant"}, 400
    if not isinstance(overrides, dict):
        return {"error": "parameter_overrides must be an object"}, 400

    # Inline ws_root-based path resolution (matches Task 5/6/7 pattern).
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    variants = spec.get("variants") or []
    for v in variants:
        if isinstance(v, dict) and v.get("name") == variant_name:
            v["parameter_overrides"] = dict(overrides)
            spec["variants"] = variants
            sf.write_text(yaml.safe_dump(spec, sort_keys=False))
            return {"ok": True}, 200
    return {"error": f"variant {variant_name!r} not found"}, 404


def _post_study_baseline_add_for_test(ws_root, body):
    """Append a composite to study.yaml.baseline[]. Returns (response_dict, status_code).

    Body:
      study:     <name>
      name:      <baseline entry name>  (unique within baseline)
      composite: <pkg.composites.x>
      params:    <dict>  (optional; defaults to {})
    """
    # Use body.get("study") directly — _study_name_from_body would pick up "name"
    # (the baseline entry name field) and misidentify it as the study name.
    study = (body.get("study") or body.get("investigation") or "").strip()
    entry_name = (body.get("name") or "").strip()
    composite = (body.get("composite") or "").strip()
    params = body.get("params")
    if not study:
        return {"error": "missing study"}, 400
    if not entry_name:
        return {"error": "missing baseline entry name"}, 400
    if not composite:
        return {"error": "missing composite"}, 400
    if params is not None and not isinstance(params, dict):
        return {"error": "params must be an object"}, 400

    # Inline ws_root-based path resolution (matches Task 5/6/7/8 pattern).
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    baseline = spec.setdefault("baseline", [])
    if any(b.get("name") == entry_name for b in baseline if isinstance(b, dict)):
        return {"error": f"baseline entry {entry_name!r} already exists"}, 409
    baseline.append({"name": entry_name, "composite": composite, "params": params or {}})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True, "name": entry_name}, 200


def _post_study_baseline_remove_for_test(ws_root, body):
    """Remove a baseline entry by name. Returns (response_dict, status_code).

    Body:
      study: <name>
      name:  <baseline entry name>

    409 if any variant has base_composite == name.
    400 if removal would leave baseline empty.
    """
    study = (body.get("study") or body.get("investigation") or "").strip()
    entry_name = (body.get("name") or "").strip()
    if not study or not entry_name:
        return {"error": "missing study or baseline entry name"}, 400

    # Inline ws_root-based path resolution.
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    baseline = spec.get("baseline") or []
    remaining = [b for b in baseline
                 if not (isinstance(b, dict) and b.get("name") == entry_name)]
    if len(remaining) == len(baseline):
        return {"error": f"baseline entry {entry_name!r} not found"}, 404

    # Check variant dependencies BEFORE checking empty — so a sole entry that is
    # referenced by a variant returns 409 (dependency) rather than 400 (empty).
    dependents = [v.get("name") for v in (spec.get("variants") or [])
                  if isinstance(v, dict) and v.get("base_composite") == entry_name]
    if dependents:
        return {
            "error": f"variants reference {entry_name!r}: {', '.join(dependents)}",
            "dependents": dependents,
        }, 409

    if not remaining:
        return {"error": "cannot leave baseline empty"}, 400

    spec["baseline"] = remaining
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


def _post_study_intervention_add_for_test(ws_root, body):
    """Append an intervention to study.yaml.interventions[]. Returns (response, code).

    Body:
      study:       <name>
      name:        <intervention name>  (unique within interventions)
      description: <freeform text>  (optional; defaults to "")
    """
    study = (body.get("study") or body.get("investigation") or "").strip()
    name = (body.get("name") or "").strip()
    description = body.get("description") or ""
    if not study or not name:
        return {"error": "missing study or intervention name"}, 400

    # Inline ws_root-based path resolution.
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    interventions = spec.setdefault("interventions", [])
    if any(i.get("name") == name for i in interventions if isinstance(i, dict)):
        return {"error": f"intervention {name!r} already exists"}, 409
    interventions.append({"name": name, "description": description})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True, "name": name}, 200


def _post_study_intervention_update_for_test(ws_root, body):
    """Update an intervention's description. Returns (response, code)."""
    study = (body.get("study") or body.get("investigation") or "").strip()
    name = (body.get("name") or "").strip()
    description = body.get("description") or ""
    if not study or not name:
        return {"error": "missing study or intervention name"}, 400

    # Inline ws_root-based path resolution.
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    for i in spec.get("interventions") or []:
        if isinstance(i, dict) and i.get("name") == name:
            i["description"] = description
            sf.write_text(yaml.safe_dump(spec, sort_keys=False))
            return {"ok": True}, 200
    return {"error": f"intervention {name!r} not found"}, 404


def _post_study_intervention_delete_for_test(ws_root, body):
    """Remove an intervention by name. Returns (response, code)."""
    study = (body.get("study") or body.get("investigation") or "").strip()
    name = (body.get("name") or "").strip()
    if not study or not name:
        return {"error": "missing study or intervention name"}, 400

    # Inline ws_root-based path resolution.
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    interventions = spec.get("interventions") or []
    remaining = [i for i in interventions
                 if not (isinstance(i, dict) and i.get("name") == name)]
    if len(remaining) == len(interventions):
        return {"error": f"intervention {name!r} not found"}, 404
    spec["interventions"] = remaining
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


def _post_study_run_delete_for_test(ws_root, body):
    """Remove one run from runs.db + study.yaml. Returns (response_dict, status_code)."""
    study = _study_name_from_body(body)
    run_id = (body.get("run_id") or "").strip()
    if not study or not run_id:
        return {"error": "missing study or run_id"}, 400
    study_dir = _study_dir(study)
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    db = study_dir / "runs.db"
    if db.is_file():
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DELETE FROM runs_meta WHERE run_id = ?", (run_id,))
            has_history = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
            ).fetchone()
            if has_history:
                conn.execute("DELETE FROM history WHERE simulation_id = ?", (run_id,))
            conn.commit()
        finally:
            conn.close()

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    spec["runs"] = [r for r in (spec.get("runs") or []) if r.get("run_id") != run_id]
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


def _post_study_runs_clear_for_test(ws_root, body):
    """Remove all runs from runs.db + study.yaml. Returns (response_dict, status_code)."""
    study = _study_name_from_body(body)
    if not study:
        return {"error": "missing study"}, 400
    study_dir = _study_dir(study)
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    db = study_dir / "runs.db"
    if db.is_file():
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DELETE FROM runs_meta")
            has_history = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
            ).fetchone()
            if has_history:
                conn.execute("DELETE FROM history")
            conn.commit()
        finally:
            conn.close()

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    spec["runs"] = []
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


def _post_study_comparison_add_for_test(ws_root, body):
    """Add a named comparison (set of run_ids) to study.yaml['comparisons'].
    Returns (response_dict, status_code)."""
    study = _study_name_from_body(body)
    run_ids = body.get("run_ids") or []
    if not study:
        return {"error": "missing study"}, 400
    if not isinstance(run_ids, list) or len(run_ids) < 2:
        return {"error": "run_ids must be a list of at least 2 run ids"}, 400
    sf = _study_spec_file(_study_dir(study))
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    comparisons = spec.setdefault("comparisons", [])
    name = (body.get("name") or "").strip() or f"comparison-{len(comparisons) + 1}"
    comparisons.append({"name": name, "run_ids": list(run_ids)})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True, "name": name}, 200


def _study_export_zip(ws_root: Path, name: str) -> bytes:
    """Zip studies/<name>/ to bytes and return the zip content."""
    import io
    import zipfile
    src = ws_root / "studies" / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(src.parent))
    return buf.getvalue()


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
    """Render study-detail.html via Jinja2."""
    import jinja2
    from vivarium_dashboard.lib.investigations import effective_status
    spec = dict(spec)
    spec["runs"] = _enrich_runs_with_meta(_study_dir(name), spec.get("runs") or [])
    # F1: compute a single headline status from the multi-axis fields (with
    # legacy `status` as fallback) so the template doesn't have to encode
    # the precedence rules itself.
    spec["_effective_status"] = effective_status(spec)
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    env.filters["fmt_ts"] = _jinja_fmt_ts
    env.filters["fmt_duration"] = _jinja_fmt_duration
    tpl = env.get_template("study-detail.html")
    # Single display name everywhere (mirrors JS _humanizeStudyName): authored
    # title:, else the slug with the ordering prefix peeled off into a chip.
    _hn = _humanize_study_name(name)
    # PTools (Pathway Tools Omics Viewer) is a v2ecoli-style feature; only offer
    # the "Launch ptools" action when the workspace configures ui.ptools_server_url.
    _ptools_enabled = False
    try:
        _ws = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
        _ptools_enabled = bool((_ws.get("ui") or {}).get("ptools_server_url"))
    except Exception:
        _ptools_enabled = False
    # W15 — open epistemic debts, computed server-side via the deterministic
    # pbg_superpowers collector (derives from rigor + freshness so it can't
    # drift). Defensive: degrade to no panel if the collector isn't importable.
    epistemic_debts = []
    try:
        from pbg_superpowers.needs_attention import open_epistemic_debts
        epistemic_debts = open_epistemic_debts(spec) or []
    except Exception:
        epistemic_debts = []
    # Composite-resolution lint: flag declared composite refs that don't resolve
    # against the live registry (would have caught autopoiesis studies 2–4).
    unresolved_composites = []
    try:
        from vivarium_dashboard.lib.composite_lookup import (
            known_composite_ids, unresolved_study_composite_refs,
        )
        unresolved_composites = unresolved_study_composite_refs(
            spec, known_composite_ids(WORKSPACE)) or []
    except Exception:
        unresolved_composites = []
    return tpl.render(study=spec, name=name,
                      display_name=spec.get("title") or _hn["title"],
                      name_chip=_hn["chip"], ptools_enabled=_ptools_enabled,
                      epistemic_debts=epistemic_debts,
                      unresolved_composites=unresolved_composites)


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
    """True if a git remote named 'origin' is configured."""
    r = subprocess.run(
        ["git", "remote"],
        cwd=WORKSPACE, capture_output=True, text=True, check=False,
    )
    return "origin" in (r.stdout or "").split()


def _stale_branch_threshold() -> int:
    """Commits-behind-main threshold above which a branch is flagged stale.

    Default 20 (matches the dnaa-biology friction report's "24 commits
    behind, two trivial conflicts" anchor). Override per-server with
    PBG_STALE_BRANCH_THRESHOLD=<int>."""
    raw = os.environ.get("PBG_STALE_BRANCH_THRESHOLD")
    if raw:
        try:
            n = int(raw)
            return max(n, 1)
        except ValueError:
            pass
    return 20


def _commits_behind(branch: str, base: str = "main") -> tuple[int, str]:
    """Return (commits_behind, ref_used). Probes origin/<base> first
    (matches what `git merge origin/<base>` would have to fast-forward
    over — the actual integration cost). Falls back to local <base>.
    Returns (0, "") on any git failure so callers don't have to
    branch on the error case."""
    for ref in (f"origin/{base}", base):
        r = subprocess.run(
            ["git", "rev-list", "--count", f"{branch}..{ref}"],
            cwd=WORKSPACE, capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            try:
                return int(r.stdout.strip() or 0), ref
            except ValueError:
                pass
    return 0, ""


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
    """Run ``v2ecoli-workflow`` once for a delegated ensemble (SP2a).

    Mirrors :func:`_run_composite_subprocess`'s timeout/return contract: runs
    ``<ws>/.venv/bin/v2ecoli-workflow --config <cfg> --out <out_dir>`` in a
    subprocess and returns ``(response_dict, status_code)``. The workflow packs
    every sweep/seed point into ONE parquet hive store under
    ``<out_dir>/parquet/…``; the caller's existing post-run ``study_outcomes.sync``
    records that one dir as a single ensemble run (no dashboard change needed).

    Does NOT touch ``_run_composite_subprocess`` — this is the ensemble sibling.
    """
    ws = Path(ws_root)
    out_dir = Path(out_dir)
    run_id = out_dir.name
    exe = ws / ".venv" / "bin" / "v2ecoli-workflow"
    cmd = [str(exe), "--config", str(cfg_path), "--out", str(out_dir)]
    try:
        result = subprocess.run(cmd, cwd=str(ws), capture_output=True,
                                text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return ({"simulation_id": run_id, "error": "ensemble run timed out"}, 504)
    except FileNotFoundError:
        # Defensive: delegation_available() should have gated this, but a venv
        # missing the console script must never raise uncaught (review FIX 2).
        return ({"simulation_id": run_id,
                 "error": "v2ecoli-workflow not found in the workspace venv"}, 502)
    if result.returncode != 0:
        return ({"simulation_id": run_id, "error": "ensemble run failed",
                 "stdout": result.stdout, "stderr": result.stderr}, 502)
    return ({"simulation_id": run_id, "ensemble": True,
             "out_dir": str(out_dir), "steps": 0}, 200)


def _run_composite_subprocess(*, pkg, state, steps, db_file, run_id, spec_id,
                              label, overrides=None, sim_name=None, timeout=1800,
                              emit_paths=None, study_emitter=None,
                              study_max_generations=None,
                              study_single_daughters=None):
    """Run a resolved composite ``state`` for ``steps`` steps in a subprocess,
    persisting runs_meta + history (via an injected SQLiteEmitter) to
    ``db_file``.

    Shared by ``_post_composite_test_run`` (scratchpad db) and the study-run
    handlers (per-Study db). Does NOT clear prior rows — callers decide.

    Returns ``(response_dict, status_code)``.  ``response_dict`` always has
    ``"simulation_id"``; on success also ``"results"``, ``"viz_html"``,
    ``"steps"``.
    """
    from vivarium_dashboard.lib import composite_runs as cr

    # Are we running a registered @composite_generator? If so, the child can
    # rebuild the composite in its own process from (spec_id, overrides) —
    # no state serialization needed. This avoids the live-Process-instance
    # problem in shared partitioned-process pools (v2ecoli) and the pint
    # Quantity infinite-recursion problem in repr() that JSON-encoding the
    # parent-built state used to hit. Non-generator callers (file-based
    # composites) keep the old state-serialization path below.
    use_generator_path = False
    try:
        from pbg_superpowers.composite_generator import _REGISTRY, discover_generators
        if not _REGISTRY:
            discover_generators()
        use_generator_path = spec_id in _REGISTRY
    except ImportError:
        pass

    py = sys.executable
    import tempfile as _tempfile
    from bigraph_schema.json_codec import BigraphJSONEncoder

    if use_generator_path:
        # Pass (spec_id, overrides) as small JSON; the child builds + injects
        # the SQLiteEmitter + runs entirely in-process.
        _state_path = None
        # Read workspace-level runtime defaults: emitter selection + multi-gen cap.
        # Workspaces (e.g. v2ecoli) can opt into XArrayEmitter via
        # `runtime: { default_emitter: xarray, max_generations: N }`. Default
        # is SQLite (the dashboard's historical single-generation behaviour).
        # Per-study override (``study_emitter``) wins over the workspace
        # default — set it from the study yaml's ``runtime.emitter`` so a
        # single workspace can mix emitters by study (e.g. xarray for
        # many-sims aggregation studies, sqlite for ones needing unstructured
        # state like unique-molecule snapshots for chromosome viz).
        try:
            _ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
            _runtime = (_ws_data.get("runtime") or {}) if isinstance(_ws_data, dict) else {}
            _default_emitter = str(_runtime.get("default_emitter") or "sqlite").lower()
            _max_generations = int(_runtime.get("max_generations") or 3)
            _single_daughters = bool(_runtime.get("single_daughters") or False)
        except Exception:
            _default_emitter = "sqlite"
            _max_generations = 3
            _single_daughters = False
        if study_emitter:
            _default_emitter = str(study_emitter).lower()
        # Per-study overrides win over workspace defaults.
        if study_max_generations is not None:
            _max_generations = int(study_max_generations)
        if study_single_daughters is not None:
            _single_daughters = bool(study_single_daughters)
        # Derive a zarr store path alongside the SQLite db_file (one per run).
        _zarr_store = str(Path(db_file).with_suffix("")) + f".{run_id}.zarr"
        payload = {
            "spec_id": spec_id,
            "overrides": overrides or {},
            "run_id": run_id,
            "db_file": db_file,
            "steps": steps,
            # v2ecoli friction #14: thread the study's declared observables
            # to the child so it can populate the user_emitter schema BEFORE
            # injecting SQLiteEmitter. Without this, history.state rows are
            # just `{"_tick": <global_time>}` and every comparative viz
            # renders empty. Empty list = legacy "no observables" behavior.
            "emit_paths": list(emit_paths or []),
            # XArray opt-in (per workspace.yaml runtime.default_emitter).
            "default_emitter": _default_emitter,
            "max_generations": _max_generations,
            "single_daughters": _single_daughters,
            "zarr_store": _zarr_store,
        }
        script = textwrap.dedent(f"""
            import json, sys, traceback
            try:
                from {pkg}.core import build_core
                from process_bigraph import Composite, gather_emitter_results
                from process_bigraph.emitter import SQLiteEmitter
                from pbg_superpowers.composite_generator import (
                    _REGISTRY, build_generator, discover_generators,
                    apply_core_extensions,
                )
                from vivarium_dashboard.lib import composite_runs as cr
                from bigraph_schema.json_codec import BigraphJSONEncoder as _BJE
                _payload = {payload!r}
                if not _REGISTRY: discover_generators()
                entry = _REGISTRY[_payload['spec_id']]
                core = build_core()
                core.register_link('SQLiteEmitter', SQLiteEmitter)
                # v2ecoli friction #16: register types/processes the composite
                # needs from packages build_core() doesn't know about (declared
                # via @composite_generator(core_extensions=[...])).
                core = apply_core_extensions(entry, core)
                doc = build_generator(entry, overrides=_payload['overrides'])
                state = doc.get('state', doc) if isinstance(doc, dict) else doc
                if _payload.get('emit_paths'):
                    state = cr.inject_emitter_for_declared_paths(state, _payload['emit_paths'])
                _use_xarray = _payload.get('default_emitter') == 'xarray'
                _view = []
                if _use_xarray:
                    # Auto-view from the study's declared observables. v0 of
                    # view_from_emit_paths is scalar-only — vector observables
                    # (monomer_counts, fork_coordinates, RNAP_coordinates, …)
                    # are skipped. If a study declares ONLY vector observables
                    # (e.g. dnaa-01 emits only listeners.monomer_counts), the
                    # auto-view is empty and the XArrayEmitter constructor
                    # would crash. In that case, fall back to SQLite for this
                    # run so the study isn't blocked.
                    from v2ecoli.library.xarray_run import (
                        run_multigen_xarray, view_from_emit_paths,
                    )
                    _view = view_from_emit_paths(_payload.get('emit_paths') or [])
                    if not _view:
                        print('[xarray-run] auto-view is empty (all declared '
                              'observables are vector / non-listeners-rooted); '
                              'falling back to SQLite emitter for this run.',
                              file=sys.stderr)
                        _use_xarray = False
                if _use_xarray:
                    # XArray multi-gen path: drive the composite externally past
                    # divisions, per-generation emitter swap; results land in a
                    # partitioned zarr store. See v2ecoli plan
                    # 2026-05-12-migrate-emitters.md task 7.x.
                    composite = Composite({{'state': state}}, core=core)
                    _md = {{
                        'experiment_id': _payload['run_id'],
                        'variant': 0,
                        'lineage_seed': 0,
                        'time_step': 1.0,
                        'max_duration': float(_payload['steps']),
                    }}
                    _xarr = run_multigen_xarray(
                        composite,
                        store_path=_payload['zarr_store'],
                        view=_view,
                        metadata_base=_md,
                        max_steps=_payload['steps'],
                        max_generations=_payload['max_generations'],
                    )
                    results = {{'zarr_store': _xarr['store'],
                               'generations': _xarr['generations'],
                               'steps': _xarr['steps']}}
                else:
                    _mg = int(_payload.get('max_generations') or 1)
                    if _mg > 1:
                        # Multi-gen: workspace-side runner drives the
                        # SQLiteEmitter externally (mirrors how the
                        # xarray branch drives XArrayEmitter). The
                        # composite does NOT get an injected emitter —
                        # the static `agents/0/...` wiring would write
                        # empty rows after division. The runner extracts
                        # the followed agent's state each chunk and
                        # calls `emitter.update` with it; on division it
                        # switches to the daughter agent_id.
                        composite = Composite({{'state': state}}, core=core)
                        from v2ecoli.library.sqlite_run import run_multigen_sqlite
                        _sq = run_multigen_sqlite(
                            composite,
                            run_id=_payload['run_id'],
                            db_file=_payload['db_file'],
                            emit_paths=_payload.get('emit_paths') or [],
                            max_steps=_payload['steps'],
                            max_generations=_mg,
                            single_daughters=bool(_payload.get('single_daughters')),
                            core=core,
                        )
                        results = {{'steps': _sq['steps'],
                                   'generations': _sq['generations']}}
                    else:
                        state = cr.inject_sqlite_emitter(
                            state, run_id=_payload['run_id'], db_file=_payload['db_file'])
                        composite = Composite({{'state': state}}, core=core)
                        cr.run_with_division(composite, _payload['steps'])
                        results = gather_emitter_results(composite)
        """).lstrip("\n")
    else:
        # Legacy path: serialize the pre-built state into a tempfile.
        # v2ecoli friction #14: parent-side injection works here because
        # the serialized state IS what the subprocess reconstructs.
        if emit_paths:
            state = cr.inject_emitter_for_paths(state, list(emit_paths))
        state = cr.inject_sqlite_emitter(state, run_id=run_id, db_file=db_file)
        state = _strip_process_instances(state)
        _state_fd, _state_path = _tempfile.mkstemp(suffix=".state.json", prefix="vivarium-run-")
        try:
            with os.fdopen(_state_fd, "w") as _f:
                json.dump(state, _f, cls=BigraphJSONEncoder)
        except Exception:
            try: os.unlink(_state_path)
            except OSError: pass
            raise

        script = textwrap.dedent(f"""
            import json, sys, traceback
            try:
                from {pkg}.core import build_core
                from process_bigraph import Composite, gather_emitter_results
                from process_bigraph.emitter import SQLiteEmitter
                from bigraph_schema.json_codec import bigraph_json_hook
                from vivarium_dashboard.lib import composite_runs as cr
                core = build_core()
                core.register_link('SQLiteEmitter', SQLiteEmitter)
                with open({_state_path!r}) as _sf:
                    _state = json.load(_sf, object_hook=bigraph_json_hook)
                composite = Composite({{'state': _state}}, core=core)
                cr.run_with_division(composite, {steps})
                results = gather_emitter_results(composite)
        """).lstrip("\n")

    # Shared tail: gather results + viz HTML + emit @@@RESULTS@@@ block.
    script += textwrap.dedent(f"""
            # Flatten tuple keys to JSON-friendly dotted strings
            out = {{}}
            for path_tuple, entries in results.items():
                key = '.'.join(str(p) for p in path_tuple)
                out[key] = entries
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
            from bigraph_schema.json_codec import BigraphJSONEncoder as _BJE
            print('@@@RESULTS@@@')
            print(json.dumps({{'results': out, 'viz_html': viz_html}}, cls=_BJE))
        except Exception as e:
            print('@@@ERROR@@@')
            print(traceback.format_exc())
    """)

    # Coordinated-generation stamp (expert-feedback A.2): tag this run with the
    # workspace's current generation so the report can flag panels from an
    # older generation as stale. No-op (None) when no generation is active.
    _generation_id = None
    try:
        from pbg_superpowers import generation as _gen
        _generation_id = _gen.current_generation_id(WORKSPACE)
    except Exception:  # noqa: BLE001 — generation is advisory, never fatal
        _generation_id = None

    conn = cr.connect(db_file)
    try:
        try:
            cr.save_metadata(conn, spec_id=spec_id, run_id=run_id,
                             params=overrides, label=label,
                             started_at=time.time(), n_steps=steps,
                             generation_id=_generation_id)
            if sim_name is not None:
                conn.execute("UPDATE runs_meta SET sim_name=? WHERE run_id=?",
                             (sim_name, run_id))
                conn.commit()
        except sqlite3.IntegrityError:
            return ({"simulation_id": run_id,
                     "error": "duplicate run_id (rare timing collision) — retry"}, 500)
        if _generation_id is not None:
            try:
                _gen.record_run(WORKSPACE, _generation_id,
                                study=(sim_name or label or spec_id),
                                run_id=run_id, sim_name=sim_name)
            except Exception:  # noqa: BLE001 — manifest index is best-effort
                pass

        # v2ecoli friction #10: persist the rendered script alongside runs.db
        # so "what did the dashboard actually run for this run_id?" is one cat.
        # Best-effort; never fail a run because the sidecar couldn't be written.
        try:
            _db_dir = os.path.dirname(os.path.abspath(db_file))
            _sims_dir = os.path.join(_db_dir, "sims")
            os.makedirs(_sims_dir, exist_ok=True)
            with open(os.path.join(_sims_dir, f"{run_id}.subprocess.py"), "w") as _f:
                _f.write(script)
        except OSError:
            pass

        try:
            try:
                result = subprocess.run([py, "-c", script], cwd=WORKSPACE,
                                        capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                try:
                    if exc.process is not None:
                        exc.process.kill()
                        exc.process.communicate(timeout=2)
                except Exception:
                    pass
                cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
                return ({"simulation_id": run_id, "error": "run timed out"}, 504)
        finally:
            if _state_path is not None:
                try: os.unlink(_state_path)
                except OSError: pass

        out = result.stdout
        if "@@@ERROR@@@" in out:
            cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
            tb = out.split("@@@ERROR@@@", 1)[1].strip()
            return ({"simulation_id": run_id, "error": "run failed",
                     "traceback": tb}, 502)

        try:
            from bigraph_schema.json_codec import bigraph_json_hook
            payload = json.loads(
                out.split("@@@RESULTS@@@", 1)[1].strip(),
                object_hook=bigraph_json_hook,
            )
        except (IndexError, json.JSONDecodeError):
            cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
            return ({"simulation_id": run_id,
                     "error": "could not parse run output",
                     "stdout": out, "stderr": result.stderr}, 502)

        # Subprocess emits {results, viz_html}; older versions emitted the
        # results dict directly. Handle both for forward/backward compat.
        if isinstance(payload, dict) and "results" in payload:
            results = payload.get("results") or {}
            viz_html = payload.get("viz_html") or {}
        else:
            results = payload
            viz_html = {}

        cr.complete_metadata(conn, run_id=run_id, n_steps=steps, status="completed")
        return ({"simulation_id": run_id, "results": results,
                 "viz_html": viz_html, "steps": steps}, 200)
    finally:
        conn.close()


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
    """Return the porcelain status excluding generated reports + submodule pointers."""
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=WORKSPACE, capture_output=True, text=True, check=True,
    ).stdout
    submodules = _submodule_paths()
    kept = []
    for raw in status.splitlines():
        if len(raw) < 4:
            continue
        path = raw[3:]
        if _is_generated_path(path):
            continue
        if path in submodules:
            continue
        kept.append(raw)
    return "\n".join(kept)


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


def _format_baseline_source(spec: dict) -> str:
    """Summarise a v3 study's baseline as a short label.

    - 1 entry: pkg_short:name if the composite contains '.composites.';
      otherwise the composite verbatim.
    - N entries: format the first as above, then append ' (+N-1 more)'.
    - 0 entries / missing / not a list: ''.
    """
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return ""
    first = baseline[0] if isinstance(baseline[0], dict) else None
    if first is None:
        return ""
    composite = (first.get("composite") or "").strip()
    if not composite:
        return ""
    if ".composites." in composite:
        pkg, _, rest = composite.partition(".composites.")
        label = f"{pkg}:{rest}"
    else:
        label = composite
    if len(baseline) > 1:
        return f"{label} (+{len(baseline) - 1} more)"
    return label


def _conclusions_excerpt(spec: dict, limit: int = 240) -> str:
    """Return a single-line preview of ``spec.conclusions`` for the index cards.

    The Conclusions tab (B6) stores a structured markdown document with H2
    headers (``## Claims``, ``## Evidence``, ``## Limitations``, ``## Next
    steps``). For an at-a-glance preview we drop those headers, collapse
    whitespace, and truncate to ``limit`` characters.
    """
    text = (spec.get("conclusions") or "").strip()
    if not text:
        return ""
    # Drop the structured H2 headers so the excerpt is just the prose.
    text = re.sub(
        r"^##\s+(Claims|Evidence|Limitations|Next steps)\s*$",
        "",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    # Collapse whitespace + truncate.
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


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
    """Return None if the module is consistently installed; else a one-line reason.

    Best-effort, fast: verifies the Python package is importable from the
    workspace venv. Surfaces drift between workspace.yaml.imports and the
    actual venv state (e.g., user pip-uninstalled a package without touching
    workspace.yaml).
    """
    venv_py = WORKSPACE / ".venv" / "bin" / "python3"
    if not venv_py.is_file():
        return None  # no venv to introspect; treat as consistent
    try:
        result = subprocess.run(
            [str(venv_py), "-c", f"import {pkg_name}"],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return f"Python import of '{pkg_name}' failed (was the venv updated?)"
    except subprocess.TimeoutExpired:
        return f"Python import of '{pkg_name}' timed out"
    except Exception as e:
        return f"Python import check errored: {e}"
    return None


# Cached for the lifetime of a request. The bulk-venv-probe takes ~200ms;
# cache invalidation is fine because each Handler instance is per-request.
_CATALOG_VENV_PROBE_SCRIPT = r'''
import importlib.metadata as md, json, re, sys
out = {}
for d in md.distributions():
    name = (d.metadata.get("Name") or "").lower()
    if not name:
        continue
    requires_raw = list(d.requires or [])
    requires_names = []
    for r in requires_raw:
        # Bare-name extract: strip version markers, extras, environment markers.
        bare = re.split(r"[\s;<>=!~\[]", r, 1)[0].strip().lower()
        if bare:
            requires_names.append(bare)
    out[name] = {"version": d.version, "requires": requires_names}
json.dump(out, sys.stdout)
'''


def _detect_workspace_venv_distributions(ws_root: Path) -> dict[str, dict]:
    """Single bulk venv probe — returns {package_name_lower: {version, requires, requires_by}}.

    Used by `/api/catalog` to detect packages that are installed in the
    workspace venv but NOT declared in workspace.yaml.imports — the
    transitive-dependency case (e.g., v2ecoli depends on viva-munk via
    pyproject.toml, viva-munk shows up in the venv but workspace.yaml has
    no entry for it).

    `requires_by` is the reverse index: for each package, which OTHER
    installed packages declared it as a dependency. Lets the UI show
    "transitive: brought in by X, Y" for venv-only-installed catalog
    entries.

    Returns {} if the venv is missing, probe times out, or JSON parse
    fails — caller should degrade gracefully (no transitive detection).
    """
    venv_py = ws_root / ".venv" / "bin" / "python3"
    if not venv_py.is_file():
        return {}
    try:
        result = subprocess.run(
            [str(venv_py), "-c", _CATALOG_VENV_PROBE_SCRIPT],
            cwd=ws_root, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return {}
    # Build reverse index: requires_by[child] = [parent_pkgs]
    rev: dict[str, list[str]] = {}
    for name, info in data.items():
        for req in info.get("requires", []):
            rev.setdefault(req, []).append(name)
    for name, info in data.items():
        info["requires_by"] = sorted(rev.get(name, []))
    return data


def _read_workspace_pyproject_deps(ws_root: Path) -> set[str]:
    """Return the set of declared dependencies (bare package names, lowercased)
    from the workspace's pyproject.toml `[project.dependencies]`.

    Used by `/api/catalog` to mark a catalog module as installed when the
    workspace's pyproject.toml declares it directly — even if
    workspace.yaml.imports has no entry. This is the SECOND of three
    install-source layers the dashboard now checks (after
    workspace.yaml.imports, before raw venv presence).

    Returns empty set on parse failure or missing file — degrades gracefully.
    """
    pyp = ws_root / "pyproject.toml"
    if not pyp.is_file():
        return set()
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib   # type: ignore
        except ImportError:
            return set()
    try:
        data = tomllib.loads(pyp.read_text(encoding="utf-8"))
    except Exception:
        return set()
    deps = ((data.get("project") or {}).get("dependencies") or [])
    out: set[str] = set()
    for d in deps:
        if not isinstance(d, str):
            continue
        # Strip version markers / extras / env markers — same regex as the
        # venv probe so the two sources can be compared directly.
        bare = re.split(r"[\s;<>=!~\[]", d, 1)[0].strip().lower()
        if bare:
            out.add(bare)
    return out


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
    """Build a composite by ``ref`` and return ``(core, state, schema)``.

    Reuses the SAME build path the Composite Explorer uses
    (``_get_composite_state`` / ``_get_composite_resolve``): a
    ``@composite_generator`` entry via ``build_generator``, else a spec file
    parsed + ``substitute_parameters``-resolved. A best-effort workspace
    ``build_core()`` is threaded through so registered ``LabeledArray`` types
    resolve their ``_labels`` catalogs (tolerated if it fails — ``core`` may be
    ``None``, in which case only inline ``_labels`` are recoverable).

    Raises ``LookupError`` for an unknown ref and ``RuntimeError`` for a build
    failure; the caller maps those to clear 4xx statuses.
    """
    ws_root = Path(ws_root)
    ws_str = str(ws_root)
    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    pkg = ws_data.get("package_path") or ("pbg_" + str(ws_data.get("name", "")).replace("-", "_"))

    # Best-effort core for labeled-array catalog resolution. Absence is fine —
    # leaves come from the state tree alone; only static catalogs degrade.
    core = None
    try:
        core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
        core = core_module.build_core()
    except Exception:
        core = None

    # Generator branch (mirrors _get_composite_state): resolve via the live
    # pbg-superpowers registry.
    entry = None
    apply_core_extensions = None
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, build_generator, discover_generators, apply_core_extensions,
        )
        if not _REGISTRY:
            try:
                discover_generators()
            except Exception:
                pass
        entry = _REGISTRY.get(ref)
    except ImportError:
        entry = None

    if entry is not None:
        if core is not None and apply_core_extensions is not None:
            try:
                core = apply_core_extensions(entry, core)
            except Exception:
                pass
        try:
            doc = build_generator(entry, core=core)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"generator build failed: {e}") from e
        if isinstance(doc, dict) and isinstance(doc.get("state"), dict):
            return core, doc["state"], doc.get("schema")
        return core, doc, None

    # Spec-parse branch (mirrors _get_composite_resolve): read the file +
    # substitute parameter defaults to get the live state tree.
    from vivarium_dashboard.lib.composite_lookup import find_composite_path, substitute_parameters
    path = find_composite_path(ws_root, pkg, ref)
    if path is None or not path.is_file():
        raise LookupError(f"composite not found: {ref}")
    try:
        text = path.read_text(encoding="utf-8")
        spec = json.loads(text) if path.suffix.lower() == ".json" else (yaml.safe_load(text) or {})
        state = substitute_parameters(spec.get("state") or {}, spec.get("parameters") or {}, {})
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"spec parse failed: {e}") from e
    return core, state, spec.get("schema") or spec.get("composition")


import re as _re
_LINEAGE_AGENT_RE = _re.compile(r"^agents\.\d+\.(.+)$")


def _augment_lineage_aliases(available: dict) -> dict:
    """Augment an ``available_observables`` dict with lineage-prefix-stripped aliases.

    The whole-cell composite runs as a LINEAGE: the cell is nested under
    ``agents.<n>.*`` (nearly every leaf is ``agents.0.<rest>``).  Studies,
    however, author *bare* single-cell readout paths (``listeners.mass.cell_mass``,
    ``unique.active_replisome``).  Without normalization the never-fabricate
    guard flags those real readouts as ``not_in_structure`` purely on a prefix
    mismatch (confirmed across all v2e-invest studies: 4/4 such flags, 0 genuine
    phantoms).

    For the ``available`` set used in VALIDATION only, this strips a leading
    ``agents.<n>.`` from every leaf (and catalog key) and adds the captured
    ``<rest>`` as an alias.  The raw emitted paths are preserved.  Crucially it
    strips ONLY a leading ``agents.<n>.`` — never an arbitrary suffix — so a
    genuinely-absent observable (``listeners.totally_fabricated``) still fails
    to match and is correctly flagged ``not_in_structure``.

    This lineage/``agents.<n>.`` convention lives in the dashboard worker; the
    general ``readout_validation`` validator stays free of agent-structure
    knowledge.
    """
    leaves = list(available.get("leaves", []) or [])
    catalogs = dict(available.get("catalogs", {}) or {})

    seen = set(leaves)
    extra_leaves = []
    for leaf in leaves:
        m = _LINEAGE_AGENT_RE.match(leaf)
        if m:
            rest = m.group(1)
            if rest not in seen:
                extra_leaves.append(rest)
                seen.add(rest)

    for key, val in list(catalogs.items()):
        m = _LINEAGE_AGENT_RE.match(key)
        if m:
            catalogs.setdefault(m.group(1), val)

    return {"leaves": leaves + extra_leaves, "catalogs": catalogs}


def _observables_for_ref(ws_root: Path, ref: str):
    """GET /api/observables?ref=<id> worker — returns ``(json_bytes, status)``.

    Builds the composite (shared TTL cache, since a whole-cell build is ~3s)
    and reports its emittable observables via ``available_observables``:
    ``{"ref", "leaves": [dotted paths], "catalogs": {observable: [labels]}}``.
    Unknown ref → 404; build failure → 400; validator absent → 501.
    """
    ref = (ref or "").strip()
    if not ref:
        return _json_body({"error": "ref required"}), 400

    import time as _time
    cache = _COMPOSITE_STATE_CACHE
    ckey = ("observables", str(ws_root), ref)
    hit = cache.get(ckey)
    if hit is not None and (_time.time() - hit[0]) < _COMPOSITE_STATE_TTL_S:
        return _json_body({**hit[1], "cached": True}), 200

    # Lazy import — tolerant if pbg_superpowers predates readout_validation.
    try:
        from pbg_superpowers.readout_validation import available_observables
    except Exception as e:  # noqa: BLE001
        return _json_body({"error": f"readout_validation unavailable: {e}"}), 501

    try:
        core, state, schema = _build_composite_state_for_observables(ws_root, ref)
    except LookupError as e:
        return _json_body({"error": str(e)}), 404
    except Exception as e:  # noqa: BLE001
        return _json_body({"error": f"composite build failed: {e}"}), 400

    try:
        available = available_observables(core, state, schema)
    except Exception as e:  # noqa: BLE001
        return _json_body({"error": f"observable introspection failed: {e}"}), 500

    payload = {
        "ref": ref,
        "leaves": available.get("leaves", []),
        "catalogs": available.get("catalogs", {}),
    }
    cache[ckey] = (_time.time(), payload)
    if len(cache) > 32:  # cap memory; drop the oldest entry
        cache.pop(next(iter(cache)))
    return _json_body(payload), 200


def _study_observable_check(ws_root: Path, slug: str):
    """GET /api/study-observable-check?study=<slug> worker — ``(json_bytes, status)``.

    Validates every readout in a study against its baseline composite's real
    structure (the never-fabricate guard): ``{"composite": ref, "readouts":
    [{name, status, detail}]}`` with ``status`` ∈
    ``ok|unresolved|not_in_structure|aspirational``. ``not_in_structure`` is the
    never-fabricate flag — a selector pointing at an observable the composite
    does not expose. If the composite can't build, returns a clear non-500
    (422 + all readouts marked aspirational with a note), never a crash.
    """
    ws_root = Path(ws_root)
    if not _SLUG_RE.match(slug or ""):
        return _json_body({"error": "invalid slug"}), 400

    study_dir = ws_root / "studies" / slug
    if not study_dir.is_dir():
        study_dir = ws_root / "investigations" / slug
    sf = _study_spec_file(study_dir)
    if not sf.is_file():
        return _json_body({"error": f"study not found: {slug}"}), 404

    try:
        spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return _json_body({"error": f"study spec parse failed: {e}"}), 400

    # Project legacy v2 shape (baseline: <str>) into the v3 baseline list.
    from vivarium_dashboard.lib.spec_migration import migrate_v2_to_v3
    spec = migrate_v2_to_v3(spec)

    baseline = spec.get("baseline") or []
    if not (isinstance(baseline, list) and baseline and isinstance(baseline[0], dict)):
        return _json_body({"error": "study has no baseline composite", "readouts": []}), 422
    ref = baseline[0].get("composite")
    if not ref:
        return _json_body({"error": "baseline entry has no composite ref", "readouts": []}), 422

    try:
        from pbg_superpowers.readout_validation import available_observables, validate_readouts
    except Exception as e:  # noqa: BLE001
        return _json_body({"error": f"readout_validation unavailable: {e}"}), 501

    readouts = spec.get("readouts") or []
    try:
        core, state, schema = _build_composite_state_for_observables(ws_root, ref)
    except Exception as e:  # noqa: BLE001 (LookupError / RuntimeError both land here)
        # Composite can't build → clear non-500: surface every readout as
        # aspirational (unverifiable) with a note, rather than crashing.
        out = [
            {"name": r.get("name", f"readout_{i}"), "status": "aspirational",
             "detail": f"composite {ref!r} could not be built — readout unverified"}
            for i, r in enumerate(readouts)
        ]
        return _json_body({
            "composite": ref,
            "readouts": out,
            "note": f"composite {ref!r} could not be built: {e}",
        }), 422

    try:
        # Normalize the lineage prefix: the whole-cell composite nests the cell
        # under ``agents.<n>.`` but studies author bare single-cell paths, so
        # augment the VALIDATION set with prefix-stripped aliases (never-fabricate
        # preserved — only a leading ``agents.<n>.`` is stripped).
        available = _augment_lineage_aliases(available_observables(core, state, schema))
        results = validate_readouts(spec, available=available)
    except Exception as e:  # noqa: BLE001
        return _json_body({"error": f"readout validation failed: {e}", "composite": ref}), 500

    return _json_body({"composite": ref, "readouts": results}), 200


def _report_lint(ws_root: Path):
    """GET /api/report-lint worker — ``(json_bytes, status)``.

    Spine A3: runs the EXISTING deterministic linter
    (``pbg_superpowers.report_linter.lint_workspace_report``) over the
    workspace and returns its findings keyed by study so the dashboard can
    render a per-study readiness panel. This wires three computed artifacts at
    once: the SP2b-ii readout-migration findings (info/warning) and the SP2c
    band-citation-gap warnings already emitted by the linter.

    The dashboard adds NO AI — it only runs the deterministic linter and
    renders the result. Tolerant: if the linter is unavailable (older
    pbg_superpowers) or the workspace can't be scanned, returns 200 with an
    empty findings list rather than a 500.

    Shape: ``{"findings": [{study, check, severity, message, field_path}]}``,
    in the linter's own stable order (error→warning→info).
    """
    ws_root = Path(ws_root)
    try:
        from pbg_superpowers.report_linter import lint_workspace_report
    except Exception:  # noqa: BLE001 — older pbg_superpowers lacks the linter
        return _json_body({"findings": []}), 200
    try:
        raw = lint_workspace_report(ws_root)
    except Exception as e:  # noqa: BLE001 — never 500 the readiness panel
        return _json_body({"findings": [], "error": str(e)}), 200

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

    # Composite-resolution lint — the linter works on specs and has no registry,
    # but the dashboard DOES. For each study, flag declared composite refs that
    # don't resolve against the live registry. This is what would have caught the
    # autopoiesis studies 2–4 (numpy-only, no registered composite).
    findings.extend(_composite_resolution_findings(ws_root))
    return _json_body({"findings": findings}), 200


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


def _linkage_cached_index(ws_root: Path):
    """Return the cached linkage index for ``ws_root`` (TTL-cached like the
    registry cache), or build + cache it. Returns ``None`` when the index
    module is unavailable or the build fails — callers stay tolerant."""
    import time as _time

    key = ("linkage", str(Path(ws_root)))
    now = _time.time()
    hit = _LINKAGE_CACHE.get(key)
    if hit is not None and now - hit[0] < _LINKAGE_TTL:
        return hit[1]
    try:
        from pbg_superpowers.linkage_index import build_index
        index = build_index(ws_root)
    except Exception:  # noqa: BLE001 — older pbg_superpowers / unscannable ws
        return None
    _LINKAGE_CACHE[key] = (now, index)
    return index


def _linkage_index(ws_root: Path, *, investigation=None, source=None, observable=None,
                   observable_registry=None, composite=None):
    """GET /api/linkage-index worker — ``(json_bytes, status)``.

    SP4a: runs the deterministic linkage index/queries
    (``pbg_superpowers.linkage_index``) over the workspace. Param-dispatch:

    - ``source``               → ``{studies: [...]}`` (studies citing the bib_key)
    - ``observable``           → ``{findings: [...]}`` (findings measuring the token)
    - ``observable_registry``  → ``{studies, composites}`` emitting the token (SP4b)
    - ``composite``            → ``{emits, used_by_studies}`` for that composite (SP4b)
    - ``investigation``        → ``{ac_matrix, dag, nodes, edges}`` for that inv
    - (none)                   → the full ``{nodes, edges}`` graph

    The dashboard adds NO AI — it only runs the deterministic derive and returns
    JSON. Tolerant: an older/absent pbg_superpowers, or an unscannable
    workspace, returns 200 with an empty payload rather than a 500.

    SP4b note: ``observable_registry``/``composite`` are the ONLY paths that
    trigger a (cached) composite build, via the injected
    ``_observables_for_ref`` callable. The other paths stay build-free.
    """
    ws_root = Path(ws_root)
    try:
        from pbg_superpowers import linkage_index as _li
    except Exception:  # noqa: BLE001 — older pbg_superpowers lacks the module
        return _json_body({"nodes": [], "edges": []}), 200

    # SP4b: adapter over the (cached, real) composite build. ``_observables_for_ref``
    # returns ``(json_bytes, status)`` for the HTTP path; the enrich callable wants
    # the ``{"leaves", "catalogs"}`` dict — normalize both that and a direct dict
    # (test-injected) shape. Looked up as a module global so tests can monkeypatch it.
    def _obs_for_ref(ref):
        res = _observables_for_ref(ws_root, ref)
        if isinstance(res, dict):
            return res
        if isinstance(res, tuple) and res:
            try:
                return json.loads(res[0])
            except Exception:  # noqa: BLE001
                return {}
        return {}

    if observable_registry:
        try:
            return _json_body(_li.studies_for_observable(
                ws_root, observable_registry, observables_for_ref=_obs_for_ref)), 200
        except Exception:  # noqa: BLE001 — build/derive can fail; stay typed + 200
            return _json_body({"studies": [], "composites": []}), 200
    if composite:
        try:
            return _json_body(_li.composite_emits(
                ws_root, composite, observables_for_ref=_obs_for_ref)), 200
        except Exception:  # noqa: BLE001 — build/derive can fail; stay typed + 200
            return _json_body({"emits": [], "used_by_studies": []}), 200

    try:
        if source:
            return _json_body({"studies": _li.studies_for_source(ws_root, source)}), 200
        if observable:
            return _json_body({"findings": _li.findings_for_observable(ws_root, observable)}), 200
        if investigation:
            return _json_body({
                "investigation": investigation,
                "ac_matrix": _li.ac_gating_matrix(ws_root, investigation),
                "dag": _li.study_dag(ws_root, investigation),
            }), 200
        index = _linkage_cached_index(ws_root) or {"nodes": [], "edges": []}
        return _json_body(index), 200
    except Exception as e:  # noqa: BLE001 — never 500 the navigate surface
        return _json_body({"nodes": [], "edges": [], "error": str(e)}), 200


def _needs_attention(ws_root: Path, *, investigation=None):
    """GET /api/needs-attention worker — ``(json_bytes, status)``.

    SP5: runs the deterministic ``pbg_superpowers.needs_attention.
    scan_investigation`` over the workspace and returns its
    ``{"investigation", "items": [...], "summary": {...}}`` payload so the
    dashboard can render a "Needs attention" panel on the investigation-detail
    page. ``items`` arrive pre-sorted high→medium→low.

    Build-free by default: we do NOT pass ``observables_for_ref`` (the opt-in
    that would trigger a composite build). The dashboard adds NO AI — it only
    runs the deterministic scan and renders. Tolerant: an older/absent
    pbg_superpowers, or an unscannable workspace, returns 200 with the
    empty-typed payload rather than a 500.
    """
    ws_root = Path(ws_root)
    _empty = {
        "investigation": investigation,
        "items": [],
        "summary": {"by_severity": {"high": 0, "medium": 0, "low": 0},
                    "by_kind": {}, "total": 0},
    }
    try:
        from pbg_superpowers import needs_attention as _na
    except Exception:  # noqa: BLE001 — older pbg_superpowers lacks the module
        return _json_body(_empty), 200
    try:
        return _json_body(_na.scan_investigation(ws_root, investigation)), 200
    except Exception:  # noqa: BLE001 — scan/derive can fail; stay typed + 200
        return _json_body(_empty), 200


def _framework_metrics(ws_root: Path):
    """GET /api/framework-metrics worker — ``(json_bytes, status)``.

    Wave 3a #26: aggregate framework-self metrics across EVERY study + every
    investigation in the workspace via the deterministic
    ``pbg_superpowers.rigor.framework_metrics`` (each metric is
    ``{fraction, count, total}``). The dashboard renders this as a
    "Framework scorecard" section labelled "framework-self metrics (n=N
    investigations)" — the label is the dashboard's job, the math is pbg's.

    AI-free + tolerant: an absent/old pbg_superpowers, or an unreadable
    workspace, returns 200 with ``{metrics: {}, n_investigations, n_studies}``
    rather than a 500 so the report degrades gracefully (section omitted).
    """
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)

    study_specs = []
    studies_root = wp.studies
    if studies_root.is_dir():
        for d in sorted(studies_root.iterdir()):
            f = d / "study.yaml"
            if not f.is_file():
                continue
            try:
                sp = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001 — skip unreadable studies
                continue
            if isinstance(sp, dict):
                study_specs.append(sp)

    inv_specs = []
    inv_root = wp.investigations
    if inv_root.is_dir():
        for d in sorted(inv_root.iterdir()):
            f = d / "investigation.yaml"
            if not f.is_file():
                continue
            try:
                isp = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                continue
            if isinstance(isp, dict):
                inv_specs.append(isp)

    base = {"metrics": {}, "n_investigations": len(inv_specs),
            "n_studies": len(study_specs)}
    try:
        from pbg_superpowers.rigor import framework_metrics
    except Exception:  # noqa: BLE001 — older pbg_superpowers lacks the function
        return _json_body(base), 200
    try:
        metrics = framework_metrics(study_specs, inv_specs) or {}
        base["metrics"] = metrics
        return _json_body(base), 200
    except Exception:  # noqa: BLE001 — compute can fail; stay typed + 200
        return _json_body(base), 200


def _investigation_hypotheses(ws_root: Path, name: str):
    """GET /api/investigation-hypotheses worker — ``(json_bytes, status)``.

    Wave 3b #6/#16: return the investigation's competing ``hypotheses[]`` with a
    COMPUTED ``support_log`` folded in via the deterministic
    ``pbg_superpowers.hypotheses.rollup_support`` (falling back to
    ``score_support`` per hypothesis). The authored ``statement`` / ``predictions``
    pass through; the SPA just renders the trajectory (no JS recompute, no drift).

    AI-free + tolerant: an absent/old pbg_superpowers, a missing investigation,
    or a compute failure returns 200 with the authored hypotheses (un-enriched)
    rather than a 500 so the report's "Competing hypotheses" panel degrades.
    """
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    base = {"hypotheses": [], "investigation": name}

    inv_path = wp.investigations / name / "investigation.yaml"
    if not inv_path.is_file():
        return _json_body(base), 200
    try:
        inv_spec = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return _json_body(base), 200
    if not isinstance(inv_spec, dict):
        return _json_body(base), 200

    authored = inv_spec.get("hypotheses")
    authored = authored if isinstance(authored, list) else []
    base["hypotheses"] = authored
    if not authored:
        return _json_body(base), 200

    # Member study specs (slug strings or {name: slug}).
    study_specs = []
    for s in (inv_spec.get("studies") or []):
        slug = s.get("name") if isinstance(s, dict) else s
        if not slug:
            continue
        f = wp.studies / str(slug) / "study.yaml"
        if not f.is_file():
            continue
        try:
            sp = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if isinstance(sp, dict):
            study_specs.append(sp)

    # 1) Preferred: rollup_support returns the enriched inv_spec (or list).
    try:
        from pbg_superpowers.hypotheses import rollup_support
    except Exception:  # noqa: BLE001 — older/absent pbg_superpowers
        rollup_support = None
    if rollup_support is not None:
        try:
            enriched = rollup_support(inv_spec, study_specs)
            if isinstance(enriched, dict):
                hyps = enriched.get("hypotheses")
                if isinstance(hyps, list):
                    base["hypotheses"] = hyps
                    return _json_body(base), 200
            elif isinstance(enriched, list):
                base["hypotheses"] = enriched
                return _json_body(base), 200
        except Exception:  # noqa: BLE001
            pass

    # 2) Fallback: score_support per hypothesis.
    try:
        from pbg_superpowers.hypotheses import score_support
    except Exception:  # noqa: BLE001
        return _json_body(base), 200
    out = []
    for h in authored:
        if not isinstance(h, dict):
            continue
        h2 = dict(h)
        try:
            log = score_support(h, study_specs)
            if isinstance(log, list):
                h2["support_log"] = log
        except Exception:  # noqa: BLE001
            pass
        out.append(h2)
    base["hypotheses"] = out
    return _json_body(base), 200


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

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
        if os.environ.get("VIVARIUM_DASHBOARD_DISABLE_CSRF") == "1":
            return True
        origin = self.headers.get("Origin")
        if not origin:
            return True
        from urllib.parse import urlsplit
        origin_netloc = urlsplit(origin).netloc
        host = self.headers.get("Host", "")
        if origin_netloc and origin_netloc == host:
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
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.imports import register_import
            register_import(
                WORKSPACE, name=name, source=source, ref=ref, mode=mode,
                description=description,
            )

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
            nonlocal entry
            if file_b64:
                dest = WORKSPACE / entry["path"]
                sha = _save_upload(file_b64, dest)
                entry["sha256"] = sha
            elif path and not file_b64:
                src = Path(path)
                if not src.is_absolute():
                    src = WORKSPACE / path
                if src.exists() and src.is_file():
                    import hashlib as _hashlib
                    h = _hashlib.sha256()
                    with src.open("rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            h.update(chunk)
                    entry["sha256"] = h.hexdigest()

            _ws_add_to_sys_path()
            if investigation:
                # Investigation-scoped: append to investigations/<slug>/investigation.yaml.
                if not _append_investigation_input(WORKSPACE, investigation, "datasets", entry):
                    raise ValueError(f"investigation '{investigation}' not found")
                return
            from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
            ws_file = WORKSPACE / "workspace.yaml"
            ws = load_workspace(ws_file)
            datasets = ws.setdefault("datasets", [])
            if datasets is None:
                datasets = []
                ws["datasets"] = datasets
            for existing in datasets:
                if isinstance(existing, dict) and existing.get("name") == name:
                    raise ValueError(f"dataset '{name}' already registered")
            datasets.append(entry)
            save_workspace(ws_file, ws)

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
            bib_file = workspace_paths().references / "papers.bib"
            claims_file = workspace_paths().references / "claims.yaml"
            if investigation:
                pdf_dest_rel = f"investigations/{investigation}/inputs/references/{bib_key}.pdf"
            else:
                pdf_dest_rel = f"references/papers/{bib_key}.pdf"
            pdf_dest = WORKSPACE / pdf_dest_rel

            if bib_file.exists():
                existing_text = bib_file.read_text(encoding="utf-8")
                if re.search(rf"@\w+\{{{re.escape(bib_key)},", existing_text):
                    raise ValueError(f"BibTeX key '{bib_key}' already exists in papers.bib")

            sha = _save_upload(pdf_b64, pdf_dest)

            bibtex_entry = build_bibtex(bib_key, title, authors, year, journal, doi)
            bib_file.parent.mkdir(parents=True, exist_ok=True)
            existing_bib = bib_file.read_text(encoding="utf-8") if bib_file.exists() else ""
            with bib_file.open("a") as f:
                if existing_bib and not existing_bib.endswith("\n"):
                    f.write("\n")
                f.write(bibtex_entry + "\n")

            from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
            ws_file = WORKSPACE / "workspace.yaml"
            ws = load_workspace(ws_file)
            refs_pdfs = ws.setdefault("references_pdfs", [])
            if refs_pdfs is None:
                refs_pdfs = []
                ws["references_pdfs"] = refs_pdfs
            if not any(e.get("bib_key") == bib_key for e in refs_pdfs):
                entry = {"bib_key": bib_key, "path": pdf_dest_rel, "sha256": sha}
                if metadata_pending:
                    entry["_metadata_pending"] = True
                refs_pdfs.append(entry)
            save_workspace(ws_file, ws)

            if investigation:
                if not _append_investigation_input(WORKSPACE, investigation, "references", bib_key):
                    raise ValueError(f"investigation '{investigation}' not found")

            if claim_ids:
                import yaml as _yaml
                existing_claims: dict = {}
                if claims_file.exists():
                    try:
                        existing_claims = _yaml.safe_load(claims_file.read_text(encoding="utf-8")) or {}
                    except Exception:
                        existing_claims = {}
                for claim_id in claim_ids:
                    existing_claims.setdefault(claim_id, [])
                    if bib_key not in existing_claims[claim_id]:
                        existing_claims[claim_id].append(bib_key)
                claims_file.parent.mkdir(parents=True, exist_ok=True)
                claims_file.write_text(_yaml.safe_dump(existing_claims, sort_keys=False))

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
            bib_file = workspace_paths().references / "papers.bib"
            claims_file = workspace_paths().references / "claims.yaml"

            already_in_bib = False
            if bib_file.exists():
                existing_text = bib_file.read_text(encoding="utf-8")
                if f"{{{bibkey}," in existing_text or f"{{{bibkey} " in existing_text:
                    already_in_bib = True
                    # Investigation-scoped references may reuse an existing key
                    # (just add the bare key to the investigation block); the
                    # global flow still treats a duplicate as an error.
                    if not investigation:
                        raise ValueError(f"BibTeX key '{bibkey}' already exists in papers.bib")

            if not already_in_bib:
                bib_file.parent.mkdir(parents=True, exist_ok=True)
                with bib_file.open("a") as f:
                    f.write("\n" + bibtex_text + "\n")

            if investigation:
                if not _append_investigation_input(WORKSPACE, investigation, "references", bibkey):
                    raise ValueError(f"investigation '{investigation}' not found")

            if claim_mappings:
                import yaml as _yaml
                existing_claims: dict = {}
                if claims_file.exists():
                    try:
                        existing_claims = _yaml.safe_load(claims_file.read_text(encoding="utf-8")) or {}
                    except Exception:
                        existing_claims = {}
                for claim_id, bkey in claim_mappings.items():
                    existing_claims.setdefault(claim_id, [])
                    if bkey not in existing_claims[claim_id]:
                        existing_claims[claim_id].append(bkey)
                claims_file.parent.mkdir(parents=True, exist_ok=True)
                claims_file.write_text(_yaml.safe_dump(existing_claims, sort_keys=False))

            if pdf_b64:
                pdf_dest_rel = f"references/papers/{bibkey}.pdf"
                pdf_dest = WORKSPACE / pdf_dest_rel
                sha = _save_upload(pdf_b64, pdf_dest)

                _ws_add_to_sys_path()
                from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
                ws_file = WORKSPACE / "workspace.yaml"
                ws = load_workspace(ws_file)
                refs_pdfs = ws.setdefault("references_pdfs", [])
                if refs_pdfs is None:
                    refs_pdfs = []
                    ws["references_pdfs"] = refs_pdfs
                if not any(e.get("bib_key") == bibkey for e in refs_pdfs):
                    refs_pdfs.append({"bib_key": bibkey, "path": pdf_dest_rel, "sha256": sha})
                save_workspace(ws_file, ws)

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
            dest = WORKSPACE / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)

            if file_b64:
                sha = _save_upload(file_b64, dest)
            else:
                _shutil.copy2(str(source_path), str(dest))
                import hashlib as _hashlib
                h = _hashlib.sha256()
                with dest.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
                sha = h.hexdigest()

            _ws_add_to_sys_path()
            entry: dict = {"name": name, "path": dest_rel, "sha256": sha}
            if description:
                entry["description"] = description
            if contributor:
                entry["contributor"] = contributor
            if claims_supported:
                entry["claims_supported"] = claims_supported

            if investigation:
                if not _append_investigation_input(WORKSPACE, investigation, "expert_docs", entry):
                    raise ValueError(f"investigation '{investigation}' not found")
                return
            from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
            ws_file = WORKSPACE / "workspace.yaml"
            ws = load_workspace(ws_file)
            expert_docs = ws.setdefault("expert_docs", [])
            if expert_docs is None:
                expert_docs = []
                ws["expert_docs"] = expert_docs
            for existing in expert_docs:
                if isinstance(existing, dict) and existing.get("name") == name:
                    raise ValueError(f"expert doc '{name}' already registered")
            expert_docs.append(entry)
            save_workspace(ws_file, ws)

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
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
            ws_file = WORKSPACE / "workspace.yaml"
            ws = load_workspace(ws_file)
            observables = ws.setdefault("observables", [])
            if observables is None:
                observables = []
                ws["observables"] = observables
            for existing in observables:
                if isinstance(existing, dict) and existing.get("name") == name:
                    raise ValueError(f"observable '{name}' already registered")
            entry: dict = {"name": name, "store_path": store_path}
            if units:
                entry["units"] = units
            if description:
                entry["description"] = description
            observables.append(entry)
            save_workspace(ws_file, ws)

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
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.workspace_yaml import load_workspace, save_workspace
            ws_file = WORKSPACE / "workspace.yaml"
            ws = load_workspace(ws_file)

            # Only validate observable references when structured fields are provided.
            if obs_list:
                registered_obs = {
                    o.get("name") for o in (ws.get("observables") or [])
                    if isinstance(o, dict)
                }
                missing = [o for o in obs_list if o not in registered_obs]
                if missing:
                    raise ValueError(
                        f"observables not registered: {missing}. "
                        "Register them first via /api/observable."
                    )

            # Validate simulation reference if provided.
            if simulation_name:
                registered_sims = {
                    s.get("name") for s in (ws.get("simulations") or [])
                    if isinstance(s, dict)
                }
                if simulation_name not in registered_sims:
                    raise ValueError(
                        f"simulation '{simulation_name}' not registered. "
                        "Register it first via /api/simulation."
                    )

            visualizations = ws.setdefault("visualizations", [])
            if visualizations is None:
                visualizations = []
                ws["visualizations"] = visualizations
            for existing in visualizations:
                if isinstance(existing, dict) and existing.get("name") == name:
                    raise ValueError(f"visualization '{name}' already registered")
            entry: dict = {"name": name}
            if viz_class:
                entry["class"] = viz_class
            if description:
                entry["description"] = description
            if viz_type:
                entry["type"] = viz_type
            if obs_list:
                entry["observables"] = list(obs_list)
            if config:
                entry["config"] = config
            if simulation_name:
                entry["simulation"] = simulation_name
            visualizations.append(entry)
            save_workspace(ws_file, ws)

        return self._json(*_active_branch_action(commit_msg, action))

    def _post_visualization_create(self, body: dict):
        """Write a .pbg/viz-requests/<name>.md file with the description and workspace context.

        Body: {name: str}
        Returns: {ok, request_path, skill_command, instructions}
        """
        name = (body.get("name") or "").strip()
        if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return self._json({"error": "invalid name"}, 400)

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        viz = next((v for v in (ws_data.get("visualizations") or []) if v.get("name") == name), None)
        if not viz:
            return self._json({"error": f"visualization '{name}' not registered (Add it first)"}, 404)

        description = viz.get("description") or ""
        if not description.strip():
            return self._json({"error": "visualization has no description — edit it first"}, 400)

        req_dir = workspace_paths().pbg / "viz-requests"
        req_dir.mkdir(parents=True, exist_ok=True)
        req_path = req_dir / f"{name}.md"

        # Build context for the skill
        observables = ws_data.get("observables", []) or []
        simulations = ws_data.get("simulations", []) or []
        phases = ws_data.get("phases", []) or []
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

        obs_lines = "\n".join(
            f'  - `{o["name"]}` (path: `{o["store_path"]}`'
            + (f', units: {o["units"]}' if o.get("units") else "")
            + ")"
            for o in observables
        ) or "  (none)"
        sim_lines = "\n".join(
            f'  - `{s["name"]}`: t={s["t_start"]}→{s["t_end"]}'
            for s in simulations
        ) or "  (none)"
        phase_lines = "\n".join(
            f'  - {p["n"]}: {p["name"]} ({p.get("status","planned")})'
            for p in phases
        ) or "  (none)"

        content = f"""# Visualization request: {name}

## Description (from user)

{description}

## Workspace context

- Workspace package: `{pkg}`
- Available observables:
{obs_lines}
- Available simulations:
{sim_lines}
- Phases:
{phase_lines}

## Instructions for the agent

Write a Python function and save it to `.pbg/viz-responses/{name}.py`. The function:

- Should be named `visualize` (no name suffix — the file path identifies it)
- Takes one argument: `results: dict` — emitter output keyed by emitter path tuple, with values being lists of dicts `{{observable_name: value, ...}}`
- Returns: HTML string (Plotly preferred) OR a base64 PNG (matplotlib fallback)
- Must include a `_demo()` helper that returns the visualization run on synthetic data, so the dashboard preview can call it without real simulation results
- Should pick the visualization library that best fits the description (Plotly for interactive, matplotlib for static)

Output file structure:

```python
\"\"\"Generated visualization: {name}\"\"\"
import plotly.graph_objects as go  # or matplotlib.pyplot, etc.

def visualize(results: dict) -> str:
    # ... build figure from results ...
    return fig.to_html(full_html=False, include_plotlyjs='cdn')

def _demo() -> str:
    # Synthetic data matching the observable shape
    fake_results = {{('emitter',): [{{...}}, ...]}}
    return visualize(fake_results)

if __name__ == "__main__":
    import sys
    sys.stdout.write(_demo())
```
"""
        req_path.write_text(content)

        return self._json({
            "ok": True,
            "request_path": str(req_path.relative_to(WORKSPACE)),
            "skill_command": f"/pbg-viz {name}",
            "instructions": (
                f"Open Claude Code in this workspace and run `/pbg-viz {name}`. "
                f"The skill will read {req_path.relative_to(WORKSPACE)}, generate a function, "
                f"and save it to .pbg/viz-responses/{name}.py. "
                f"Click Refresh below when ready."
            ),
        }, 200)

    def _get_visualization_status(self):
        """Return lifecycle status for a viz: described | requested | created | added | committed."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        name = (qs.get("name") or [""])[0]
        if not name:
            return self._json({"error": "missing name"}, 400)

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        viz = next((v for v in (ws_data.get("visualizations") or []) if v.get("name") == name), None)
        if not viz:
            return self._json({"status": "missing", "name": name}, 200)

        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        response_path = workspace_paths().pbg / "viz-responses" / f"{name}.py"
        staged_path = workspace_paths().pbg / "visualizations-staged" / f"{name}.py"
        committed_path = WORKSPACE / pkg / "visualizations" / f"{name}.py"
        request_path = workspace_paths().pbg / "viz-requests" / f"{name}.md"

        if committed_path.exists():
            status = "committed"
        elif staged_path.exists():
            status = "added"
        elif response_path.exists():
            status = "created"
        elif request_path.exists():
            status = "requested"
        else:
            status = "described"

        return self._json({
            "status": status,
            "name": name,
            "has_request": request_path.exists(),
            "has_response": response_path.exists(),
            "has_staged": staged_path.exists(),
            "has_committed": committed_path.exists(),
        }, 200)

    def _post_visualization_add_to_project(self, body: dict):
        """Copy .pbg/viz-responses/<name>.py to .pbg/visualizations-staged/<name>.py.

        Does NOT commit (Commit is a separate action). Working tree stays clean
        because both source and dest are gitignored.
        """
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "missing name"}, 400)
        src = workspace_paths().pbg / "viz-responses" / f"{name}.py"
        if not src.exists():
            return self._json({"error": f"no skill response yet — run /pbg-viz {name} first"}, 404)
        dest_dir = workspace_paths().pbg / "visualizations-staged"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.py"
        shutil.copy2(src, dest)
        return self._json({"ok": True, "staged_path": str(dest.relative_to(WORKSPACE))}, 200)

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

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        target_dir = WORKSPACE / pkg / "visualizations"

        moved_names = list(names)  # captured for closure

        def action():
            target_dir.mkdir(parents=True, exist_ok=True)
            # Ensure __init__.py exists
            init = target_dir / "__init__.py"
            if not init.exists():
                init.write_text("")
            for n in moved_names:
                src = staged_dir / f"{n}.py"
                dest = target_dir / f"{n}.py"
                shutil.copy2(src, dest)
                src.unlink()  # remove staged copy

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
        """
        name = (body.get("name") or "").strip()
        if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return self._json({"error": "name must match ^[a-zA-Z0-9_-]+$"}, 400)
        description = (body.get("description") or "").strip()
        if not description:
            return self._json({"error": "description is required"}, 400)

        snake = name.lower().replace("-", "_")
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        target = f"{pkg}/visualizations/{snake}.py"

        observables = ws_data.get("observables") or []
        simulations = ws_data.get("simulations") or []
        obs_lines = "\n".join(
            f'  - `{o.get("name")}` (path: `{o.get("store_path")}`'
            + (f', units: {o["units"]}' if o.get("units") else "")
            + ")"
            for o in observables if isinstance(o, dict)
        ) or "  (none)"
        sim_lines = "\n".join(
            f'  - `{s.get("name")}`: t={s.get("t_start")}->{s.get("t_end")}'
            for s in simulations if isinstance(s, dict)
        ) or "  (none)"

        body_md = (
            f"# Visualization request: {name}\n\n"
            f"## Description (from user)\n\n"
            f"{description}\n\n"
            f"## Workspace context\n\n"
            f"- Workspace package: `{pkg}`\n"
            f"- Available observables:\n{obs_lines}\n"
            f"- Available simulations:\n{sim_lines}\n\n"
            f"## Instructions for the agent\n\n"
            f"Write a single function decorated with `@as_visualization` and save it to "
            f"`{target}`.\n\n"
            f"Output file structure (the only thing this file should contain):\n\n"
            f"```python\n"
            f'"""<class-name> — one-line description.\n\n'
            f"Generated by /pbg-viz from request '{name}'.\n"
            f'"""\n'
            f"from __future__ import annotations\n"
            f"import html as _html, json\n"
            f"from pbg_superpowers.visualization import as_visualization\n\n\n"
            f"@as_visualization(\n"
            f"    inputs={{'<port>': '<bigraph-type>', ...}},  # typed input ports\n"
            f"    name='<ClassName>',\n"
            f"    demo={{...}},                                  # synthetic state for dashboard preview\n"
            f")\n"
            f"def update_{snake}(state):\n"
            f"    # ... build the Plotly figure from state ...\n"
            f"    return {{'html': '<...Plotly HTML...>'}}\n"
            f"```\n\n"
            f"Constraints:\n\n"
            f"- The function MUST be named `update_{snake}` (snake_case).\n"
            f"- `inputs` MUST use bigraph-schema type strings: `'list[float]'`, `'float'`, "
            f"`'list[list[float]]'`, `'string'`. For trajectory ports prefer `'list[float]'`.\n"
            f"- `demo` MUST be realistic synthetic state matching `inputs` so the dashboard "
            f"preview is meaningful.\n"
            f"- Do NOT define a class manually; the decorator synthesizes the Visualization "
            f"subclass.\n"
            f"- Do NOT edit `__init__.py` — `bigraph_schema.discover_packages()` walks the "
            f"package automatically.\n"
            f"- The file must be self-contained (only `pbg_superpowers`, `process_bigraph`, "
            f"`html`, `json`, and standard `plotly`/`matplotlib` imports allowed).\n"
        )

        req_dir = workspace_paths().pbg / "viz-requests"
        req_dir.mkdir(parents=True, exist_ok=True)
        req_path = req_dir / f"{name}.md"
        req_path.write_text(body_md)
        return self._json({
            "ok": True,
            "request_path": str(req_path),
            "target_file": target,
            "skill_command": f"/pbg-viz {name}",
            "instructions": (
                "In your active Claude Code session, run `/pbg-viz "
                f"{name}`. The skill will read this request and write the "
                "decorated function to the target file. Click Accept here "
                "when it's done."
            ),
        }, 200)

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
        global _REGISTRY_CACHE
        _REGISTRY_CACHE["data"] = None

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
        global _REGISTRY_CACHE
        _REGISTRY_CACHE["data"] = None

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
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import load_state
        state = load_state()
        if not state.get("active_branch"):
            return self._json({"active": False}, 200)
        branch = state["active_branch"]
        base = state.get("base", "main")

        # commits ahead of base
        r = subprocess.run(["git", "rev-list", "--count", f"{base}..{branch}"],
                           cwd=WORKSPACE, capture_output=True, text=True)
        commits_ahead = int(r.stdout.strip() or 0) if r.returncode == 0 else 0

        # commits behind base — surfaces the friction-#5 case where a long-running
        # investigation branch drifts so far that framework migrations need
        # manual conflict-resolution. Computed against origin/<base> when present
        # (matches what a `git merge origin/main` would have to fast-forward
        # over) and falls back to local <base>.
        commits_behind, behind_ref = _commits_behind(branch, base)
        stale_threshold = _stale_branch_threshold()

        # unpushed commits
        if state.get("pushed"):
            r2 = subprocess.run(["git", "rev-list", "--count", f"origin/{branch}..{branch}"],
                                cwd=WORKSPACE, capture_output=True, text=True)
            unpushed = int(r2.stdout.strip() or 0) if r2.returncode == 0 else commits_ahead
        else:
            unpushed = commits_ahead

        return self._json({
            "active": True,
            "branch": branch,
            "base": base,
            "commits_ahead": commits_ahead,
            "commits_behind": commits_behind,
            "behind_ref": behind_ref,
            "stale": commits_behind >= stale_threshold,
            "stale_threshold": stale_threshold,
            "unpushed": unpushed,
            "pushed": state.get("pushed", False),
            "has_origin": _has_origin_remote(),
            "gh_available": shutil.which("gh") is not None,
            "pr_number": state.get("pr_number"),
            "pr_url": state.get("pr_url"),
        }, 200)

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

        if not branch:
            r = subprocess.run(["git", "branch", "--show-current"],
                               cwd=WORKSPACE, capture_output=True, text=True)
            branch = r.stdout.strip() if r.returncode == 0 else ""
        if not branch:
            return self._json({"error": "could not determine current branch + no ?branch= given"}, 400)

        commits_behind, behind_ref = _commits_behind(branch, base)
        threshold = _stale_branch_threshold()
        return self._json({
            "branch": branch,
            "base": base,
            "behind_ref": behind_ref,
            "commits_behind": commits_behind,
            "stale_threshold": threshold,
            "stale": commits_behind >= threshold,
        }, 200)

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
        """Return the filtered porcelain list of uncommitted files."""
        try:
            dirty = _dirty_workspace()
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            return self._json({"error": f"git status failed: {stderr[:200]}"}, 500)
        files = []
        for raw in dirty.splitlines():
            if len(raw) < 4:
                continue
            files.append({"status": raw[:2].strip(), "path": raw[3:]})
        return self._json({"count": len(files), "files": files}, 200)

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
        """
        result = {
            "upstream_repo": None, "branch": None, "push_state": "no_origin",
            "ahead": 0, "behind": 0,
            "branch_url": None, "repo_url": None,
            "pr_number": None, "pr_url": None,
            "base": "main", "ahead_of_base": 0,
            "dirty_count": 0, "compare_url": None, "pr_state": None,
            "gh_available": bool(shutil.which("gh")),
            "has_active_workstream": False,
        }
        # current branch
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           cwd=WORKSPACE, capture_output=True, text=True)
        if r.returncode != 0:
            return self._json(result, 200)
        result["branch"] = (r.stdout or "").strip()
        # upstream repo (from origin remote)
        r = subprocess.run(["git", "remote", "get-url", "origin"],
                           cwd=WORKSPACE, capture_output=True, text=True)
        if r.returncode != 0:
            return self._json(result, 200)
        origin_url = (r.stdout or "").strip()
        m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", origin_url)
        if m:
            result["upstream_repo"] = m.group(1)
            result["repo_url"] = f"https://github.com/{m.group(1)}"
            result["branch_url"] = f"https://github.com/{m.group(1)}/tree/{result['branch']}"
        # ahead/behind vs origin/<branch>
        ref = f"origin/{result['branch']}"
        r = subprocess.run(["git", "rev-list", "--left-right", "--count", f"{ref}...HEAD"],
                           cwd=WORKSPACE, capture_output=True, text=True)
        if r.returncode != 0:
            # origin/<branch> probably doesn't exist yet
            result["push_state"] = "no_origin"
        else:
            parts = (r.stdout or "").strip().split()
            if len(parts) == 2:
                behind = int(parts[0]); ahead = int(parts[1])
                result["ahead"] = ahead; result["behind"] = behind
                if ahead == 0 and behind == 0:
                    result["push_state"] = "pushed"
                elif ahead > 0 and behind == 0:
                    result["push_state"] = "ahead"
                elif ahead == 0 and behind > 0:
                    result["push_state"] = "behind"
                else:
                    result["push_state"] = "diverged"
        # PR info + base — read from .pbg/state.json (cheaper than gh API)
        try:
            from vivarium_dashboard.lib.work_state import load_state
            state = load_state()
            result["pr_url"] = state.get("pr_url")
            result["pr_number"] = state.get("pr_number")
            result["base"] = state.get("base") or "main"
            result["has_active_workstream"] = bool(state.get("active_branch"))
        except Exception:
            pass
        # ahead_of_base: commits on branch not yet merged into base
        base = result["base"]
        branch = result["branch"]
        if branch:
            for base_ref in (base, f"origin/{base}"):
                r_aob = subprocess.run(
                    ["git", "rev-list", "--count", f"{base_ref}..HEAD"],
                    cwd=WORKSPACE, capture_output=True, text=True,
                )
                if r_aob.returncode == 0:
                    try:
                        result["ahead_of_base"] = int(r_aob.stdout.strip())
                    except ValueError:
                        pass
                    break
            if result["upstream_repo"]:
                result["compare_url"] = (
                    f"https://github.com/{result['upstream_repo']}"
                    f"/compare/{base}...{branch}"
                )
        # dirty_count: number of uncommitted files (filtered, same as dirty-status)
        try:
            dirty_output = _dirty_workspace()
            result["dirty_count"] = len([
                l for l in dirty_output.splitlines() if len(l) >= 4
            ])
        except Exception:
            pass
        # pr_state: query gh if a PR number is known
        if result.get("pr_number"):
            try:
                r_pr = subprocess.run(
                    ["gh", "pr", "view", str(result["pr_number"]),
                     "--json", "state", "--jq", ".state"],
                    cwd=WORKSPACE, capture_output=True, text=True, timeout=5,
                )
                if r_pr.returncode == 0:
                    result["pr_state"] = r_pr.stdout.strip() or None
            except Exception:
                pass
        return self._json(result, 200)

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
        """Return list of stage/* branches with last-commit info."""
        try:
            raw = subprocess.run(
                ["git", "branch", "--list", "stage/*"],
                cwd=WORKSPACE, capture_output=True, text=True, check=True,
            ).stdout
            stage_branches = [b.strip().lstrip("* ") for b in raw.splitlines() if b.strip()]

            current = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=WORKSPACE, capture_output=True, text=True, check=True,
            ).stdout.strip()

            branches = []
            for bname in stage_branches:
                try:
                    log = subprocess.run(
                        ["git", "log", "-1", "--format=%H|%s|%ci", bname],
                        cwd=WORKSPACE, capture_output=True, text=True, check=True,
                    ).stdout.strip()
                    parts = log.split("|", 2)
                    sha = parts[0] if parts else ""
                    subject = parts[1] if len(parts) > 1 else ""
                    date_str = parts[2] if len(parts) > 2 else ""

                    ahead_raw = subprocess.run(
                        ["git", "rev-list", "--count", f"main..{bname}"],
                        cwd=WORKSPACE, capture_output=True, text=True,
                    ).stdout.strip()
                    ahead = int(ahead_raw) if ahead_raw.isdigit() else 0

                    branches.append({
                        "name": bname,
                        "last_commit": {
                            "sha": sha[:7],
                            "subject": subject,
                            "date": date_str,
                        },
                        "ahead_of_main": ahead,
                    })
                except Exception:
                    branches.append({"name": bname, "last_commit": {}, "ahead_of_main": 0})

            return self._json({"branches": branches, "current": current}, 200)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def _serve_pending(self):
        """Return pending entries from unmerged stage/* branches."""
        try:
            return self._json(_pending_entries(), 200)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def _get_branch_diff(self):
        """Return a short diff summary for ?branch=<name>."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        branch = (qs.get("branch") or [""])[0]
        if not branch or not re.match(r"^[A-Za-z0-9./_-]+$", branch) or ".." in branch:
            return self._json({"error": "invalid branch name"}, 400)
        log = subprocess.run(
            ["git", "log", "--oneline", f"main..{branch}"],
            cwd=WORKSPACE, capture_output=True, text=True, check=False,
        )
        diff_stat = subprocess.run(
            ["git", "diff", "--stat", f"main...{branch}"],
            cwd=WORKSPACE, capture_output=True, text=True, check=False,
        )
        return self._json({
            "branch": branch,
            "log": log.stdout,
            "diff_stat": diff_stat.stdout,
        }, 200)

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
            tag = (s.get("emitter") or "").lower()
            s["emitter_type"] = _emitter_label.get(tag) or emitter_type_of(s.get("db_path"))
        return self._json(
            {"simulations": sims, "current": _current_branch_slug(WORKSPACE)}, 200)

    def _get_composite_runs(self):
        """GET /api/composite-runs?spec_id=X — list runs for one composite spec."""
        from urllib.parse import urlparse, parse_qs
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib import composite_runs as cr

        qs = parse_qs(urlparse(self.path).query)
        spec_id = (qs.get("spec_id") or [""])[0]
        if not spec_id:
            return self._json({"runs": [], "error": "missing spec_id"}, 400)

        db_file = workspace_paths().pbg / "composite-runs.db"
        if not db_file.is_file():
            return self._json({"runs": []}, 200)
        conn = cr.connect(db_file)
        try:
            runs = cr.query_runs(conn, spec_id=spec_id)
        finally:
            conn.close()
        return self._json({"runs": runs}, 200)

    def _get_composite_run(self):
        """GET /api/composite-run/<run_id> — return trajectory list."""
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib import composite_runs as cr

        path_only = self.path.split("?", 1)[0]
        rest = path_only[len("/api/composite-run/"):]
        # Strip a trailing '/state' if a more specific route should handle it;
        # this handler matches the bare /api/composite-run/<id> form.
        if "/" in rest:
            return self._json({"error": "use /state subpath"}, 400)
        run_id = rest

        db_file = workspace_paths().pbg / "composite-runs.db"
        if not db_file.is_file():
            return self._json({"error": "no run database"}, 404)
        conn = cr.connect(db_file)
        try:
            trajectory = cr.query_run(conn, run_id=run_id)
        finally:
            conn.close()
        if not trajectory:
            return self._json({"error": "run not found"}, 404)
        return self._json({"run_id": run_id, "trajectory": trajectory}, 200)

    def _get_composite_run_state(self):
        """GET /api/composite-run/<run_id>/state?step=N — single state snapshot."""
        from urllib.parse import urlparse, parse_qs
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib import composite_runs as cr

        u = urlparse(self.path)
        # path: /api/composite-run/<run_id>/state
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

        db_file = workspace_paths().pbg / "composite-runs.db"
        if not db_file.is_file():
            return self._json({"error": "no run database"}, 404)
        conn = cr.connect(db_file)
        try:
            state = cr.query_run_state(conn, run_id=run_id, step=step)
        finally:
            conn.close()
        if state is None:
            return self._json({"error": "state not found for run+step"}, 404)
        return self._json({"run_id": run_id, "step": step,
                            "state": state}, 200)

    def _get_composite_run_status(self):
        """GET /api/composite-run/<run_id>/status — lightweight run status.

        Returns {status, progress_step, n_steps, heartbeat_at}. For terminal
        states it also returns an `error` excerpt (failed/orphaned, from the
        run log) or `viz_html` (completed, from the run's viz.json).
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib import composite_runs as cr

        path_only = self.path.split("?", 1)[0]
        prefix = "/api/composite-run/"
        rest = path_only[len(prefix):]
        if not rest.endswith("/status"):
            return self._json({"error": "bad route"}, 400)
        run_id = rest[: -len("/status")]

        db_file = workspace_paths().pbg / "composite-runs.db"
        if not db_file.is_file():
            return self._json({"error": "no run database"}, 404)
        conn = cr.connect(db_file)
        try:
            meta = cr.query_run_meta(conn, run_id=run_id)
        finally:
            conn.close()
        if meta is None:
            return self._json({"error": "run not found"}, 404)

        resp = {
            "run_id": run_id,
            "status": meta["status"],
            "progress_step": meta.get("progress_step") or 0,
            "n_steps": meta.get("n_steps"),
            "heartbeat_at": meta.get("heartbeat_at"),
        }
        if meta["status"] in ("failed", "orphaned"):
            log_rel = meta.get("log_path")
            if log_rel:
                resp["log_path"] = log_rel
                log_full = WORKSPACE / log_rel
                if log_full.is_file():
                    resp["error"] = log_full.read_text(encoding="utf-8")[-2000:]
        elif meta["status"] == "completed":
            viz_file = workspace_paths().pbg / "runs" / run_id / "viz.json"
            if viz_file.is_file():
                try:
                    resp["viz_html"] = json.loads(viz_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
        return self._json(resp, 200)

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
        """
        from urllib.parse import urlparse, parse_qs

        qs = parse_qs(urlparse(self.path).query)
        inv = (qs.get("investigation") or [""])[0].strip()
        run_id = (qs.get("run_id") or [""])[0].strip()
        if not inv or not run_id:
            return self._json(
                {"error": "investigation and run_id are required",
                 "viz_files": []}, 400,
            )
        viz_dir = _study_dir(inv) / "viz" / run_id
        if not viz_dir.is_dir():
            return self._json({"viz_files": []}, 200)
        out = []
        for html_file in sorted(viz_dir.glob("*.html")):
            out.append({
                "name": html_file.stem,
                "html_path": str(html_file.relative_to(WORKSPACE)),
            })
        return self._json({"viz_files": out}, 200)

    def _get_investigation_composites(self):
        """GET /api/investigation-composites?investigation=<n>
        Returns: {composites: [{name, source, params}]}
        Reads the v3 ``baseline`` list; each entry is projected to
        {name, source (was composite), params}.
        """
        import urllib.parse
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError
        qs = urllib.parse.urlparse(self.path).query
        name = urllib.parse.parse_qs(qs).get('investigation', [''])[0].strip()
        if not name:
            return self._json({"error": "investigation is required"}, 400)
        spec_path = _study_spec_path(name)
        if not spec_path.is_file():
            return self._json({"error": f"investigation '{name}' not found"}, 404)
        try:
            spec = load_spec(spec_path)
        except InvestigationSpecError as e:
            return self._json({"error": f"spec error: {e}"}, 400)
        items = [
            {
                "name":   b.get("name", ""),
                "source": b.get("composite", ""),
                "params": b.get("params") or {},
            }
            for b in (spec.get("baseline") or [])
            if isinstance(b, dict)
        ]
        return self._json({"composites": items}, 200)

    def _get_investigation_state_tree(self):
        """GET /api/investigation-state-tree?investigation=<n>&composite=<c>
        Returns: {nodes: [{path, kind, type?, default?, address?, config?}]}
        """
        import urllib.parse
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.composite_recipes import walk_state_tree
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        inv = qs.get('investigation', '').strip()
        comp = qs.get('composite', '').strip()
        if not inv or not comp:
            return self._json({"error": "investigation + composite required"}, 400)
        composite_path = _study_dir(inv) / "composites" / f"{comp}.yaml"
        if not composite_path.is_file():
            return self._json({"error": f"composite document not found: {composite_path}"}, 404)
        try:
            doc = yaml.safe_load(composite_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            return self._json({"error": f"failed to parse composite: {e}"}, 500)
        return self._json({"nodes": walk_state_tree(doc)}, 200)

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
        if not slug:
            return self._json({"error": "missing ?study="}, 400)
        spec = _study_detail_spec(slug)
        if spec is None:
            return self._json({"error": "study not found"}, 404)
        try:
            from pbg_superpowers.rigor import study_rigor
            return self._json(study_rigor(spec), 200)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}",
                               "dimensions": [], "score": {}, "summary": ""}, 200)

    def _get_investigation_rigor(self):
        """GET /api/investigation-rigor?investigation=<slug> — rigor roll-up
        across the investigation's member studies + investigation-level
        dimensions (adversarial coverage, traceable methodology)."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        slug = (q.get("investigation") or [None])[0]
        if not slug:
            return self._json({"error": "missing ?investigation="}, 400)
        inv_path = workspace_paths().investigations / slug / "investigation.yaml"
        if not inv_path.is_file():
            return self._json({"error": "investigation not found"}, 404)
        try:
            inv_spec = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            return self._json({"error": f"unreadable investigation.yaml: {e}"}, 200)
        member_specs = []
        for s in (inv_spec.get("studies") or []):
            slug_s = s if isinstance(s, str) else (
                (s.get("slug") or s.get("study")) if isinstance(s, dict) else None)
            if not slug_s:
                continue
            sp = _study_detail_spec(slug_s)
            if sp:
                member_specs.append(sp)
        try:
            from pbg_superpowers.rigor import investigation_rigor
            return self._json(investigation_rigor(inv_spec, member_specs), 200)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}",
                               "dimensions": [], "per_study": {}, "score": {}, "summary": ""}, 200)

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

        Returns ``{generation: {generation_id, git_sha, param_set_hash,
        created_at, label, n_runs}}`` or ``{generation: null}`` when no
        generation is active. Backs the report's generation banner so the
        live dashboard and the exported HTML stamp the same provenance
        (expert-feedback A.3). Best-effort: any error reports null rather
        than 500, so a missing generation never breaks the report.
        """
        try:
            from pbg_superpowers import generation as _gen
            g = _gen.current_generation(WORKSPACE)
        except Exception:  # noqa: BLE001
            g = None
        if g is None:
            return self._json({"generation": None}, 200)
        return self._json({"generation": {
            "generation_id": g.generation_id,
            "git_sha": g.git_sha,
            "param_set_hash": g.param_set_hash,
            "created_at": g.created_at,
            "label": g.label,
            "n_runs": len(g.runs),
        }}, 200)

    def _get_github_repo(self):
        """GET /api/github-repo — the workspace's GitHub repo as ``owner/name``.

        Resolution order (first hit wins):
          1. ``git remote get-url origin`` parsed for github.com (the live
             checkout's actual remote — authoritative for v2ecoli =
             ``vivarium-collective/v2ecoli``).
          2. workspace.yaml ``dashboard.github_repo`` / ``dashboard.repository``.

        Returns ``{repo: "owner/name"}`` or ``{repo: null}`` when neither
        resolves. Backs the report's inline-feedback "Open GitHub issue"
        button so the exported HTML can pre-fill issues against the right
        repo without prompting the reviewer. Best-effort: never 500s.
        """
        repo = None
        try:
            from vivarium_dashboard.lib.report import _detect_github_repo
            repo = _detect_github_repo(WORKSPACE)
        except Exception:  # noqa: BLE001
            repo = None
        if not repo:
            try:
                ws_data = yaml.safe_load(
                    (WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")
                ) or {}
                dash = _dashboard_config(ws_data)
                cand = dash.get("github_repo") or dash.get("repository")
                if isinstance(cand, str) and cand.strip():
                    # Normalize a full URL down to owner/name.
                    import re as _re
                    cand = cand.strip()
                    m = _re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", cand)
                    repo = m.group(1) if m else cand.replace(".git", "").strip("/")
            except Exception:  # noqa: BLE001
                repo = None
        return self._json({"repo": repo or None}, 200)

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

    def _post_references_fetch(self, body: dict):
        """POST /api/references-fetch — fetch DOI + Unpaywall enrichment.

        Body:
            key:    optional bib key. If set, fetch only that entry.
            force:  optional bool, default false. If true, re-fetch entries
                    that already have a cached record.

        With neither set, fetches all entries that lack a cached record.
        Returns ``{updated: [<key>, ...], entries: [...refreshed entries...]}``.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.report import _parse_bib_entries
        from vivarium_dashboard.lib.references_fetch import (
            fetch_missing, load_cache, enrich_entries, resolve_contact_email,
        )
        try:
            entries = _parse_bib_entries(WORKSPACE)
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        key = (body.get("key") or "").strip() or None
        force = bool(body.get("force"))
        if key and not any(e.get("key") == key for e in entries):
            return self._json({"error": f"unknown bib key: {key}"}, 404)

        cache_before = load_cache(WORKSPACE)
        try:
            cache_after = fetch_missing(
                entries, WORKSPACE,
                only_key=key, email=resolve_contact_email(WORKSPACE), force=force,
            )
        except Exception as e:
            return self._json({"error": f"fetch failed: {e}"}, 500)

        if force:
            updated = sorted(cache_after)
        else:
            updated = sorted(set(cache_after) - set(cache_before))
        enriched = enrich_entries(entries, cache_after)
        return self._json({"updated": updated, "entries": enriched}, 200)

    def _get_work_composite_diff(self):
        """GET /api/work-composite-diff — files changed on the active branch
        that look like model code (composites + processes + steps + library
        helpers). Powers a "Model changes" section in the PR body Suggest.

        Returns ``{base, branch, changes: [{path, lines_added, lines_removed,
        category}, ...]}``. Empty list when the branch is at base, or when
        the diff is huge (capped at 500 entries).
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import load_state
        state = load_state()
        branch = state.get("active_branch") or ""
        if not branch:
            head = subprocess.run(["git", "branch", "--show-current"],
                                  cwd=WORKSPACE, capture_output=True, text=True, timeout=5)
            if head.returncode == 0:
                branch = head.stdout.strip()
        base = state.get("base") or "main"

        # Get numstat (per-file lines added/removed) vs the merge-base with base.
        mb = subprocess.run(
            ["git", "merge-base", base, "HEAD"],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=10,
        )
        if mb.returncode != 0:
            return self._json({"base": base, "branch": branch, "changes": [],
                               "error": f"merge-base failed: {(mb.stderr or mb.stdout)[:200]}"}, 200)
        ref = mb.stdout.strip() or base
        diff = subprocess.run(
            ["git", "diff", "--numstat", f"{ref}...HEAD"],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=15,
        )
        if diff.returncode != 0:
            return self._json({"base": base, "branch": branch, "changes": [],
                               "error": f"diff failed: {(diff.stderr or diff.stdout)[:200]}"}, 200)

        # Category mapping: a file is included only if it matches one of these
        # path patterns (model code in the v2ecoli layout). Other repos can
        # extend the pattern list; for now we hardcode the canonical roots.
        CATEGORIES = [
            ("composites/",      "composite"),
            ("/composites/",     "composite"),
            ("processes/",       "process"),
            ("/processes/",      "process"),
            ("steps/",           "step"),
            ("/steps/",          "step"),
            ("library/",         "library helper"),
            ("/library/",        "library helper"),
            ("types/",           "type definition"),
            ("/types/",          "type definition"),
        ]

        changes = []
        for line in diff.stdout.splitlines()[:500]:
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            added, removed, path = parts
            try:
                a = int(added) if added != "-" else 0
                r = int(removed) if removed != "-" else 0
            except ValueError:
                continue
            cat = None
            for sub, label in CATEGORIES:
                if sub in "/" + path:
                    cat = label
                    break
            if cat is None:
                continue
            changes.append({
                "path": path,
                "lines_added": a,
                "lines_removed": r,
                "category": cat,
            })

        # Sort by largest diff first (lines_added + lines_removed).
        changes.sort(key=lambda c: -(c["lines_added"] + c["lines_removed"]))
        return self._json({"base": base, "branch": branch, "changes": changes}, 200)

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
        if not key:
            return self._json({"error": "missing ?key="}, 400)

        payload = _enumerate_data_sources()
        entry = next(
            (s for s in payload.get("sources", []) if s.get("key") == key),
            None,
        )
        if entry is None:
            return self._json(
                {"error": f"key not in data-source bundle: {key!r}"}, 404
            )

        path = Path(entry.get("path") or "")
        if not path.is_file():
            return self._json(
                {"error": f"file for key {key!r} not found: {path}"}, 404
            )

        ext = path.suffix.lower()
        mime, inline = _DATA_SOURCE_MIME.get(
            ext, ("application/octet-stream", False)
        )
        try:
            data = path.read_bytes()
        except OSError as e:
            return self._json({"error": f"read failed: {e}"}, 500)

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if not inline:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{path.name}"',
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

    def _get_study_bigraph_paths(self):
        """GET /api/study-bigraph-paths?study=<slug>[&baseline=<name>][&max_depth=<n>]

        Returns: {composite, source_file, max_depth, node_count, nodes:[{path,kind,...}]}
        """
        import urllib.parse
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        slug = qs.get("study", "").strip()
        baseline_name = qs.get("baseline", "").strip()
        try:
            max_depth = int(qs.get("max_depth", "8"))
        except ValueError:
            max_depth = 8
        if not slug:
            return self._json({"error": "study slug required (?study=<slug>)"}, 400)

        spec_path = _study_dir(slug) / "study.yaml"
        if not spec_path.is_file():
            spec_path = _study_dir(slug) / "spec.yaml"
        if not spec_path.is_file():
            return self._json({"error": f"no study.yaml or spec.yaml at {_study_dir(slug)}"}, 404)
        try:
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            return self._json({"error": f"failed to parse study spec: {e}"}, 500)

        baselines = spec.get("baseline") or []
        if not baselines:
            return self._json({"error": "study has no baseline entries"}, 400)
        if baseline_name:
            chosen = next((b for b in baselines if b.get("name") == baseline_name), None)
            if chosen is None:
                return self._json(
                    {"error": f"baseline {baseline_name!r} not found in study {slug!r}"}, 404,
                )
        else:
            chosen = baselines[0]

        composite_ref = chosen.get("composite") or ""
        basename = composite_ref.rsplit(".", 1)[-1] if composite_ref else ""

        candidates = [
            WORKSPACE / "models" / f"{basename}.pbg",
            WORKSPACE / "models" / f"{basename}.json",
        ]
        # v2ecoli legacy: the "baseline" composite is serialized as "partitioned".
        if basename == "baseline":
            candidates.append(WORKSPACE / "models" / "partitioned.pbg")
        source_file = next((p for p in candidates if p.is_file()), None)
        if source_file is None:
            return self._json({
                "error":     "no serialized composite state found",
                "composite": composite_ref,
                "looked_in": [str(p) for p in candidates],
                "hint":      "run the baseline to populate <workspace>/models/<composite>.pbg, or commit a snapshot.",
            }, 404)

        mtime = source_file.stat().st_mtime
        cache_key = (str(source_file), mtime, max_depth)
        nodes = self._bigraph_path_cache.get(cache_key)
        if nodes is None:
            from vivarium_dashboard.lib.composite_recipes import walk_state_snapshot
            try:
                doc = json.loads(source_file.read_text(encoding="utf-8"))
            except Exception as e:
                return self._json({"error": f"failed to parse {source_file.name}: {e}"}, 500)
            nodes = walk_state_snapshot(doc, max_depth=max_depth)
            if len(self._bigraph_path_cache) > 8:
                self._bigraph_path_cache.clear()
            self._bigraph_path_cache[cache_key] = nodes

        source_display = (
            str(source_file.relative_to(WORKSPACE))
            if str(source_file).startswith(str(WORKSPACE))
            else str(source_file)
        )
        return self._json({
            "composite":   composite_ref,
            "source_file": source_display,
            "max_depth":   max_depth,
            "node_count":  len(nodes),
            "nodes":       nodes,
        }, 200)

    def _get_investigation_composite_doc(self):
        """GET /api/investigation-composite-doc?investigation=<n>&composite=<c>
        Returns: {state: <parsed composite YAML>}
        Used by the Composites tab's bigraph-loom iframe to fetch the
        composite document as JSON (the iframe can't parse YAML in-browser
        without bundling a parser).
        """
        import urllib.parse
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        inv = qs.get('investigation', '').strip()
        comp = qs.get('composite', '').strip()
        if not (inv and comp):
            return self._json({"error": "investigation + composite required"}, 400)
        path = _study_dir(inv) / "composites" / f"{comp}.yaml"
        if not path.is_file():
            return self._json({"error": "composite document not found"}, 404)
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            return self._json({"error": f"parse failed: {e}"}, 500)
        return self._json({"state": doc}, 200)

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
        import shutil
        name = _study_name_from_body(body)
        if not name:
            return self._json({"error": "name is required"}, 400)
        inv_dir = _study_dir(name)
        if not inv_dir.is_dir():
            return self._json({"error": f"investigation '{name}' not found"}, 404)

        def action():
            shutil.rmtree(inv_dir)

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
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigations import (
            load_spec, render_visualizations, InvestigationSpecError,
        )

        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "name is required"}, 400)
        inv_dir = _study_dir(name)
        spec_path = _study_spec_path(name)
        if not spec_path.is_file():
            return self._json({"error": f"investigation '{name}' not found"}, 404)
        try:
            spec = load_spec(spec_path)
        except InvestigationSpecError as e:
            return self._json({"error": f"spec error: {e}"}, 400)

        # Discover workspace package + build core (mirror _post_investigation_run)
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        sys.path.insert(0, str(WORKSPACE))
        try:
            core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
            core = core_module.build_core()
            registry = dict(core.link_registry)
        except Exception as e:
            return self._json({"error": f"failed to build core: {e}"}, 500)

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

        from process_bigraph import Composite

        def build_and_run(viz_doc, registry_arg):
            composite = Composite({'state': viz_doc}, core=core)
            composite.run(1)
            state = composite.state
            html = state.get('output_store')
            if isinstance(html, dict):
                html = html.get('value') or html.get('_value') or ''
            return html if isinstance(html, str) else ''

        try:
            viz_paths = render_visualizations(
                spec, inv_dir, name,
                core_registry=registry, build_and_run=build_and_run,
            )
        except Exception as e:
            return self._json({"error": f"render failed: {type(e).__name__}: {e}"}, 500)

        return self._json({
            "ok": True, "investigation": name,
            "n_visualizations": len(viz_paths),
            "viz_paths": [str(p) for p in viz_paths],
        }, 200)

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
            spec = _y.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            vizzes = spec.setdefault("visualizations", []) or []
            if any(v.get("name") == viz_name for v in vizzes):
                raise RuntimeError(f"visualization '{viz_name}' already exists in spec")
            vizzes.append({"name": viz_name, "address": address, "config": viz_config})
            spec["visualizations"] = vizzes
            spec_path.write_text(_y.safe_dump(spec, sort_keys=False))

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
        """GET /api/ui-config — return UI feature flags from workspace.yaml."""
        try:
            ws = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
        except Exception:
            ws = {}
        ui = ws.get("ui") or {}
        # NOTE (ptools_omics_url_template): the default targets the Omics Viewer
        # auto-load endpoint (omics=t&url=…&class=…&column1=…), verified against
        # sms-ptools 0.8.2. Override via ui.ptools_omics_url_template if your
        # PTools build differs. Placeholders: {server},{orgid},{tsv_url},{cls},{columns}.
        return self._json({
            "composite_view": ui.get("composite_view", "bigraph-loom"),
            "ptools_server_url": ui.get("ptools_server_url", ""),
            "ptools_omics_url_template": ui.get(
                "ptools_omics_url_template",
                _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
            ),
        }, 200)

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

        Returns: [{name, class, address, config, description?}, ...]
        """
        try:
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
        except Exception:
            ws_data = {}
        out = []
        for entry in (ws_data.get("visualizations") or []):
            if not isinstance(entry, dict):
                continue
            cls = (entry.get("class") or "").strip()
            if not cls:
                continue
            out.append({
                "name": entry.get("name"),
                "class": cls,
                "address": f"local:{cls}",
                "config": entry.get("config") or {},
                "description": entry.get("description") or "",
            })
        return self._json({"instances": out}, 200)

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
            composites_dir = inv_dir / "composites"
            composites_dir.mkdir(parents=True, exist_ok=True)
            sidecar = composites_dir / f"{composite_name}.yaml"
            if is_generator:
                sidecar.write_text(yaml.safe_dump(generator_doc, sort_keys=False))
            else:
                shutil.copy2(source_path, sidecar)

            spec = {
                "name": auto_name,
                "baseline": composite_name,
                "variants": [{
                    "name": composite_name,
                    "source": source_ref,
                    "document": f"./composites/{composite_name}.yaml",
                }],
                "comparisons": [],
                "conclusions": "",
                "question": "",
                "hypothesis": "",
                "status": "draft",
            }
            (inv_dir / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))

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
            import shutil
            if source_path is not None:
                shutil.copy2(source_path, sidecar)
            else:
                sidecar.write_text(yaml.safe_dump(generator_doc, sort_keys=False))
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            composites = spec.setdefault('composites', [])
            composites.append({
                'name': comp_name,
                'source': source,
                'document': f'./composites/{comp_name}.yaml',
            })
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

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
            derived.write_text(yaml.safe_dump(derived_doc, sort_keys=False))
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            variants = spec.setdefault('variants', [])
            entry = {'name': comp_name, 'extends': extends,
                     'document': f'./composites/{comp_name}.yaml'}
            intervention = {
                'description': body.get('description') if body.get('description') is not None else '',
            }
            if body.get('parameter_overrides'):
                intervention['parameter_overrides'] = body['parameter_overrides']
            if body.get('process_overrides'):
                intervention['process_overrides'] = body['process_overrides']
            # Only attach the intervention block if at least one override was
            # supplied; description-only on a derived variant would otherwise
            # carry an empty recipe.
            if intervention.get('parameter_overrides') or intervention.get('process_overrides'):
                entry['intervention'] = intervention
            existing_idx = next(
                (i for i, v in enumerate(variants) if v.get('name') == comp_name),
                None,
            )
            if existing_idx is not None:
                variants[existing_idx] = entry  # full replace
            else:
                variants.append(entry)
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

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
            catalog_dir.mkdir(parents=True, exist_ok=True)
            doc = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
            doc['name'] = target_name
            if description is not None:
                doc['description'] = description
            target_path.write_text(yaml.safe_dump(doc, sort_keys=False))
            # Mark variant promoted in spec.yaml
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            for v in (spec.get('variants') or []):
                if v.get('name') == variant_name:
                    v['promoted'] = True
                    break
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

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
            derived_path = inv_dir / "composites" / f"{comp_name}.yaml"
            derived_path.write_text(yaml.safe_dump(derived_doc, sort_keys=False))

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_set_observables(self, body: dict):
        """POST /api/investigation-set-observables {investigation, paths, emit_all}
        Rewrites spec.yaml.observables. The orchestrator builds the emitter
        step at run time.
        """
        inv_name = (body.get("investigation") or "").strip()
        paths = body.get("paths")
        emit_all = bool(body.get("emit_all"))
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
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            if emit_all:
                spec['observables'] = [{'path': []}]
            else:
                spec['observables'] = [{'path': list(p)} for p in paths if p]
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_set_conclusions(self, body: dict):
        """POST /api/investigation-set-conclusions {investigation, markdown}
        Writes spec.yaml.conclusions. Rejects bodies over 256KB.
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
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            spec['conclusions'] = markdown
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_set_overview(self, body: dict):
        """POST /api/investigation-set-overview {investigation, fields: {question?, hypothesis?, status?}}
        Selectively updates the three Overview metadata fields on spec.yaml.
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
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            for key in ("question", "hypothesis", "status", "topic"):
                if key in fields:
                    spec[key] = fields[key]
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    def _post_investigation_set_status(self, body: dict):
        """POST /api/investigation-set-status {investigation, status} — write the
        `status` field into investigations/<slug>/investigation.yaml."""
        result = _set_investigation_status(
            WORKSPACE,
            body.get("investigation") or "",
            body.get("status") or "",
        )
        code = result.pop("_code", 200)
        return self._json(result, code)

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
    # Study-specific POST handlers (thin wrappers around pure helpers)
    # ------------------------------------------------------------------

    def _post_study_set_objective(self, body: dict):
        """POST /api/study-set-objective {study, text}"""
        response, code = _post_study_set_objective_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_narrative_set(self, body: dict):
        """POST /api/study-narrative-set {study, path, value}

        Generic writer for v4 narrative-spine fields (report / study_card /
        biological_summary / conclusion_verdicts / literature_anchors /
        design_pivot_required). See ``_post_study_narrative_set_for_test``.
        """
        response, code = _post_study_narrative_set_for_test(WORKSPACE, body)
        return self._json(response, code)

    def _post_study_expert_input_set(self, body: dict):
        """POST /api/study-expert-input-set {study, name, current}

        Patches one ``conditions.model_settings[i].current`` value in the
        target study's yaml (legacy alias ``conditions.expert_inputs`` is
        still accepted on read). The next ``pbg_runner`` invocation reads
        the updated value. Round-trip preserves yaml comments via the
        standard yaml.safe_dump output (comments not preserved by design —
        the file is canonical, not a hand-edited doc).

        Body: ``{"study": "<slug>", "name": "<setting-name>", "current": <value>}``
        Where ``current`` can be a number, string, bool, or null (to reset
        to "awaiting expert").

        URL kept as ``/api/study-expert-input-set`` for back-compat; rename
        the field internally without breaking deployed clients.
        """
        import yaml as _yaml
        slug = (body or {}).get("study", "").strip()
        name = (body or {}).get("name", "").strip()
        if not slug or not name:
            return self._json({"error": "study and name are required"}, 400)
        if "current" not in (body or {}):
            return self._json({"error": "current is required (may be null)"}, 400)
        new_current = body["current"]

        spec_path = _study_spec_path(slug)
        if not spec_path or not spec_path.is_file():
            return self._json({"error": f"study not found: {slug}"}, 404)
        try:
            spec = _yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        except _yaml.YAMLError as e:
            return self._json({"error": f"yaml parse failed: {e}"}, 500)

        cond = spec.get("conditions")
        if not isinstance(cond, dict):
            return self._json(
                {"error": "study has no v4 conditions block; cannot set model setting"},
                400,
            )
        # Prefer the new key; fall back to the legacy alias.
        eis_key = "model_settings" if "model_settings" in cond else "expert_inputs"
        eis = cond.get(eis_key)
        if not isinstance(eis, list):
            return self._json(
                {"error": f"conditions.{eis_key} is missing or not a list"},
                400,
            )

        target = None
        for ei in eis:
            if isinstance(ei, dict) and ei.get("name") == name:
                target = ei
                break
        if target is None:
            return self._json(
                {"error": f"model setting not found: {name}"},
                404,
            )

        # Optional bounds check when range is declared.
        rng = target.get("range")
        if (
            isinstance(rng, list) and len(rng) == 2
            and isinstance(new_current, (int, float))
            and not isinstance(new_current, bool)
        ):
            lo, hi = rng[0], rng[1]
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                if new_current < lo or new_current > hi:
                    return self._json(
                        {"error": f"value {new_current} is outside declared range [{lo}, {hi}]"},
                        400,
                    )

        target["current"] = new_current
        try:
            spec_path.write_text(
                _yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)
            )
        except OSError as e:
            return self._json({"error": f"write failed: {e}"}, 500)

        return self._json({
            "study": slug,
            "name": name,
            "current": new_current,
        }, 200)

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
        q = urllib.parse.parse_qs(self.path.split("?", 1)[-1] if "?" in self.path else "")
        job_id = (q.get("job_id") or [""])[0]
        if not job_id:
            return self._json({"jobs": manager.list_recent(10)}, 200)
        job = manager.get(job_id)
        if job is None:
            return self._json({"error": "job not found"}, 404)
        return self._json(job.to_dict(), 200)

    def _render_investigation_comparative_visualisations(
        self, inv_slug: str, iset: dict, job
    ) -> None:
        """Walk each member study + render its ``comparative_visualizations``.

        Comparative viz now lives in the **study** yaml, not the
        investigation yaml — each comparison is between the study's own
        baseline + variants. Single ``studies/<slug>/runs.db`` is queried
        once per trace (filtered by simulation name), and output lands
        in ``studies/<slug>/viz/comparative_<name>.html`` so the
        per-study viz auto-discovery + the downloadable report's
        per-study section pick it up.

        Schema (optional, in each study.yaml):

            comparative_visualizations:
              - name: dnaa-atp-count-vs-time
                title: DnaA-ATP count over time (baseline vs variants)
                observable_path: listeners.itv2.dnaa_atp_count
                y_label: DnaA-ATP count
                runs:
                  - {sim_name: dnaa-05-itv2-comparison-baseline, label: Baseline (ITv2)}
                  - {sim_name: v2ecoli-baseline-default,         label: v2ecoli default}
                  - {sim_name: v2ecoli-with-fxj-params,          label: v2ecoli + FXJ}

        ``sim_name`` matches the ``simulations.name`` column in the
        study's runs.db — the value pbg_runner writes as the run's
        label. For baselines this is typically ``<study-slug>-baseline``;
        for variants it's the variant's own name.
        """
        import yaml as _yaml
        from vivarium_dashboard.lib.comparative_viz import (
            render_comparative_time_series,
        )
        for member in (iset.get("studies") or []):
            study_slug = member if isinstance(member, str) else (member or {}).get("study")
            if not study_slug:
                continue
            spec_path = workspace_paths().studies / study_slug / "study.yaml"
            if not spec_path.is_file():
                continue
            try:
                study_spec = _yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            except _yaml.YAMLError:
                continue
            specs = study_spec.get("comparative_visualizations") or []
            if not specs:
                continue
            viz_dir = workspace_paths().studies / study_slug / "viz"
            viz_dir.mkdir(parents=True, exist_ok=True)
            study_db = workspace_paths().studies / study_slug / "runs.db"
            if not study_db.is_file():
                continue
            for cv in specs:
                if not isinstance(cv, dict) or not cv.get("name"):
                    continue
                runs = []
                for r in cv.get("runs") or []:
                    if not isinstance(r, dict):
                        continue
                    sim_name = r.get("sim_name") or r.get("variant") or r.get("name")
                    label = r.get("label") or sim_name or "?"
                    # XArrayEmitter runs write per-run zarr stores alongside
                    # the SQLite db (one zarr dir per run_id). When the sim's
                    # most-recent completed run has a zarr store, point
                    # comparative_viz at it via zarr_path; the zarr-read
                    # adapter (PR #87) extracts the observable across
                    # generations. Falls back to SQLite db_path otherwise
                    # (legacy single-generation runs).
                    zarr_path = _zarr_store_for_sim(study_db, sim_name)
                    if zarr_path is not None:
                        runs.append({
                            "label": label,
                            "zarr_path": zarr_path,
                            "sim_name": sim_name,
                        })
                    else:
                        runs.append({
                            "label": label,
                            "db_path": study_db,
                            "sim_name": sim_name,
                        })
                if not runs:
                    continue
                out_path = viz_dir / f"comparative_{cv['name']}.html"
                try:
                    render_comparative_time_series(
                        runs=runs,
                        observable_path=cv.get("observable_path", ""),
                        title=cv.get("title", cv["name"]),
                        y_label=cv.get("y_label", ""),
                        output_path=out_path,
                        observable_index=cv.get("observable_index"),
                        target_band=cv.get("target_band"),
                        target_band_label=cv.get("target_band_label"),
                    )
                except Exception as e:  # noqa: BLE001
                    job.update_item(
                        len(job.items) - 1,
                        comparative_viz_warning=f"{study_slug}/{cv['name']}: {e}",
                    )

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
        if not name:
            return self._json({"error": "missing study"}, 400)
        src = workspace_paths().studies / name
        if not src.is_dir():
            return self._json({"error": "study not found"}, 404)
        data = _study_export_zip(WORKSPACE, name)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{name}.zip"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _get_ptools_launch(self, study: str):
        """GET /api/ptools-launch/<study>?run=<run_id>&analysis=<name>

        Discovers per-run ptools TSV files and returns a Pathway Tools Omics
        Viewer launch URL.  Requires ``ui.ptools_server_url`` in workspace.yaml.

        The Pathway Tools server fetches the data file over HTTP, so the TSV URL
        must be reachable from the PTools server, not just the browser.  The
        dashboard resolves its public base from (in priority order):
          1. ``ui.dashboard_public_base_url`` in workspace.yaml
          2. The HTTP ``Host`` header sent by the browser
        """
        from urllib.parse import urlparse, parse_qs
        qs = urlparse(self.path).query
        params = parse_qs(qs)
        run_id = (params.get("run", [""])[0] or "").strip() or None
        analysis = (params.get("analysis", [""])[0] or "").strip() or None

        try:
            ws = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8")) or {}
        except Exception:
            ws = {}
        ui = ws.get("ui") or {}

        ptools_server_url = ui.get("ptools_server_url", "").strip()
        if not ptools_server_url:
            return self._json({"error": "ptools_server_url not configured"}, 400)

        # Default template auto-loads the Omics Viewer; override via
        # ui.ptools_omics_url_template if your PTools build differs.
        ptools_omics_url_template = ui.get(
            "ptools_omics_url_template",
            _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        )

        # Resolve the dashboard's public base URL so the PTools server can fetch
        # the TSV over HTTP.  Priority: explicit config > Host header.
        public_base = (ui.get("dashboard_public_base_url") or "").strip()
        if not public_base:
            host = self.headers.get("Host", "localhost")
            public_base = f"http://{host}"

        study_dir = _study_dir(study)
        if not study_dir.is_dir():
            return self._json({"error": f"study not found: {study}"}, 404)

        result = _build_ptools_launch_url(
            study_dir=study_dir,
            ws_root=WORKSPACE,
            ptools_server_url=ptools_server_url,
            ptools_omics_url_template=ptools_omics_url_template,
            public_base=public_base,
            run_id=run_id,
            analysis=analysis,
        )
        if "error" in result:
            return self._json(result, 404)
        return self._json(result, 200)

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
        spec = _study_detail_spec(slug)
        if spec is None:
            return _json_body({"error": f"study not found: {slug}"}), 404
        return _json_body(spec), 200

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

        Returns the dict that GET /api/iset/<name> sends, or ``None`` when
        the investigation.yaml does not exist.  Extracted from
        ``_get_iset_detail`` so publish.py can call it without a live server.
        """
        spec_path = workspace_paths().investigations / name / "investigation.yaml"
        if not spec_path.is_file():
            return None
        try:
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return None

        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError
        from vivarium_dashboard.lib.investigations import normalize_dag_edges

        def _normalize_parents(study_spec: dict) -> list:
            return normalize_dag_edges(study_spec)

        studies_out: list[dict] = []
        for slug in (spec.get("studies") or []):
            try:
                sp = workspace_paths().study_dir(slug) / "study.yaml"
            except FileNotFoundError:
                sp = workspace_paths().investigations / slug / "spec.yaml"
            if not sp.is_file():
                studies_out.append({"name": slug, "status": "missing", "error": "study.yaml not found"})
                continue
            try:
                study_spec = load_spec(sp)
            except InvestigationSpecError as e:
                studies_out.append({"name": slug, "status": "invalid", "error": str(e)})
                continue
            sim_set = study_spec.get("simulation_set") or []
            beh_tests = study_spec.get("behavior_tests") or study_spec.get("expected_behavior") or []
            readouts = study_spec.get("readouts") or study_spec.get("observables") or []
            purpose = study_spec.get("purpose") or {}
            question = (purpose.get("question") if isinstance(purpose, dict) else None) or study_spec.get("question", "")
            follow_ups = study_spec.get("follow_up_studies") or []
            disc_impl = study_spec.get("discovery_implications") or {}
            disc_followups = (disc_impl.get("followup_study_proposals")
                              if isinstance(disc_impl, dict) else None) or []
            findings = _enrich_findings_with_weight(study_spec)
            n_runs_for_study = _count_runs_for_study(study_spec["name"], study_spec)
            raw_status = study_spec.get("status", "planned")
            studies_out.append({
                "name":                  study_spec["name"],
                "status":                raw_status,
                "effective_status":      compute_study_effective_status(
                    raw_status,
                    has_runs=n_runs_for_study > 0,
                    has_active_run=_has_active_run_for_study(study_spec["name"], study_spec)),
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
                "n_requirements":        len(study_spec.get("implementation_requirements") or study_spec.get("gaps") or []),
                "n_followups":           len(disc_followups) or len(follow_ups),
                "follow_up_studies":     follow_ups,
                "discovery_implications": disc_impl,
                "n_findings":            len(findings),
                "findings":              findings,
                "claim":                 study_spec.get("claim"),
                "confidence":            study_spec.get("confidence"),
                "design_status":         study_spec.get("design_status"),
                "implementation_status": study_spec.get("implementation_status"),
                "simulation_status":     study_spec.get("simulation_status"),
                "evaluation_status":     study_spec.get("evaluation_status"),
                "gate_status":           study_spec.get("gate_status"),
                "expert_review_status":  study_spec.get("expert_review_status"),
                # Spine A2: surface the PERSISTED coded gate_evaluator (carries
                # result + diverges_from_authored) so the report's per-study
                # verdict pill can render a code-vs-authored divergence chip.
                # Read-only passthrough; no recompute here.
                "computed_gate_verdict": (
                    (study_spec.get("pipeline_gate") or {}).get("gate_evaluator")
                    if isinstance((study_spec.get("pipeline_gate") or {}).get("gate_evaluator"), dict)
                    else None
                ),
            })

        member_statuses = [s.get("status", "planning") for s in studies_out]
        member_has_runs = [(s.get("n_runs") or 0) > 0 for s in studies_out]
        effective_status = compute_investigation_status(
            member_statuses, has_runs=member_has_runs,
        )

        computed_acceptance: "dict | None" = None
        try:
            from pbg_superpowers.investigation_status import roll_up_acceptance
            from pbg_superpowers import study_io as _sio
            wp = workspace_paths()
            studies_by_name: dict = {}
            for _sd in wp.iter_study_dirs():
                _syp = _sd / "study.yaml"
                if _syp.exists():
                    try:
                        studies_by_name[_sd.name] = _sio.load_yaml_mapping(_syp)
                    except Exception:
                        pass
            computed_acceptance = roll_up_acceptance(spec, studies_by_name)
            # Spine A1: surface the PERSISTED divergence flag (written by the
            # investigation acceptance evaluator) so the executive fold can
            # render a code-vs-authored badge. The recompute above gives the
            # per-criterion table + computed verdict_status; we do NOT recompute
            # diverges_from_authored here — we read the spine-written flag.
            persisted_acc = (spec.get("executive") or {}).get("computed_acceptance")
            if isinstance(persisted_acc, dict) and isinstance(computed_acceptance, dict):
                if "diverges_from_authored" in persisted_acc:
                    computed_acceptance["diverges_from_authored"] = (
                        persisted_acc.get("diverges_from_authored")
                    )
        except Exception:
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
            # Wave 3a #1 — what the investigation primarily evaluates
            # (method | model | hypothesis | composition-protocol). Renders as a
            # chip in the report header. Absent → no chip.
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
        }

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
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            cmps = list(spec.get("comparisons") or [])
            if any(c.get("name") == cmp_name for c in cmps):
                raise ValueError(f"comparison {cmp_name!r} already exists")
            cmps.append({
                "name": cmp_name,
                "description": description,
                "variants": list(variants),
                "observables": list(observables),
            })
            spec["comparisons"] = cmps
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

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
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            cmps = spec.get("comparisons") or []
            idx = next((i for i, c in enumerate(cmps) if c.get("name") == cmp_name), None)
            if idx is None:
                raise KeyError(f"comparison {cmp_name!r} not found")
            for key in ("description", "variants", "observables"):
                if key in fields:
                    cmps[idx][key] = fields[key]
            spec["comparisons"] = cmps
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

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
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            grps = list(spec.get("groups") or [])
            if any(g.get("name") == grp_name for g in grps):
                raise ValueError(f"group {grp_name!r} already exists")
            grps.append({
                "name": grp_name,
                "description": description,
                "variants": list(variants),
            })
            spec["groups"] = grps
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

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
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            grps = spec.get("groups") or []
            idx = next((i for i, g in enumerate(grps) if g.get("name") == grp_name), None)
            if idx is None:
                raise KeyError(f"group {grp_name!r} not found")
            for key in ("description", "variants"):
                if key in fields:
                    grps[idx][key] = fields[key]
            spec["groups"] = grps
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

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
        """GET /api/composites — return composite specs from the workspace AND every installed pbg-* package.

        Delegates data assembly to the module-level ``_composites_data(ws_root)``
        pure builder; wraps the result in the HTTP JSON response.
        """
        return self._json(_composites_data(WORKSPACE), 200)

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

        # Building a whole-cell composite (build_generator) takes ~3s and is
        # re-run on every explorer open / pop-out. Cache the built (and value-
        # summarized) doc per ref with a short TTL so repeat opens are instant
        # but code edits are still picked up. Checked FIRST so a hit skips the
        # per-request sys.path + registry setup entirely. Bypass with ?fresh=1.
        import time as _time
        fresh = qs.get("fresh") in ("1", "true", "yes")
        cache = _COMPOSITE_STATE_CACHE
        if not fresh:
            hit = cache.get(ref)
            if hit is not None and (_time.time() - hit[0]) < _COMPOSITE_STATE_TTL_S:
                return self._json({**hit[1], "cached": True}, 200)

        _ws_add_to_sys_path()

        # Generator-kind branch: resolve via pbg-superpowers' live registry.
        try:
            from pbg_superpowers.composite_generator import _REGISTRY, build_generator, discover_generators
            if not _REGISTRY:
                # Registry not primed yet — trigger discovery so @composite_generator
                # imports fire. Self-heals when /api/composites hasn't been hit yet.
                discover_generators()
            entry = _REGISTRY.get(ref)
            if entry is not None:
                try:
                    doc = build_generator(entry)
                except Exception as e:  # noqa: BLE001
                    return self._json({"error": f"generator build failed: {e}"}, 400)
                from vivarium_dashboard.lib.process_docs import (
                    attach_process_docs, summarize_large_values,
                )
                doc = summarize_large_values(doc)  # shrink ~5MB bulk values → tiny
                attach_process_docs(doc)  # per-process docstrings for the inspector
                payload = {"state": doc, "kind": "generator", "module": entry.module}
                cache[ref] = (_time.time(), payload)
                if len(cache) > 16:  # cap memory; drop the oldest entry
                    cache.pop(next(iter(cache)))
                return self._json(payload, 200)
        except ImportError:
            pass

        path = None
        # Try to resolve as a dotted spec ID via composite_lookup.
        try:
            from vivarium_dashboard.lib.composite_lookup import find_composite_path
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text(encoding="utf-8"))
            pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
            found = find_composite_path(WORKSPACE, pkg, ref)
            if found is not None:
                path = found
        except Exception:
            pass

        # Fall back to workspace-relative path.
        if path is None:
            candidate = WORKSPACE / ref
            if candidate.is_file():
                path = candidate

        if path is None or not path.is_file():
            # Honest, structured degrade payload so the loom / Composites view can
            # render "composite not found / not a registered composite — this study
            # may not declare a real composite" instead of a bare "error composite"
            # node. ``unresolved: true`` is the machine-readable flag the client keys on.
            return self._json({
                "error": (f"composite not found: {ref} — not a registered composite "
                          "(this study may not declare a real composite)"),
                "unresolved": True,
                "ref": ref,
            }, 404)

        try:
            text = path.read_text(encoding="utf-8")
            doc = json.loads(text) if path.suffix.lower() == ".json" else (yaml.safe_load(text) or {})
        except Exception as e:
            return self._json({"error": f"parse failed: {e}"}, 500)

        from vivarium_dashboard.lib.process_docs import attach_process_docs
        attach_process_docs(doc)  # per-process docstrings for the inspector
        return self._json({"state": doc, "kind": "spec"}, 200)

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
        """
        import urllib.parse
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (qs.get("name", [""])[0]).strip()
        if not name:
            return self._json({"error": "name required"}, 400)

        catalog = self._module_registry()
        entry = next((m for m in catalog if m.get("name") == name), None)
        if entry is None:
            return self._json({"error": f"unknown module: {name}"}, 404)

        sys_deps = (entry.get("system_dependencies") or {}).get("checks") or []
        venv_py = WORKSPACE / ".venv" / "bin" / "python3"
        plat = _platform_key()

        results = []
        all_ok = True
        for check in sys_deps:
            ok, reason = _check_system_dep(check, venv_py)
            if not ok:
                all_ok = False
            install_block = check.get("install") if isinstance(check.get("install"), dict) else None
            install_spec = install_block.get(plat) if install_block else None
            results.append({
                "name": check.get("name"),
                "description": check.get("description", ""),
                "ok": ok,
                "reason": reason,
                "install": install_spec,
                "notes": check.get("notes"),
            })
        return self._json({
            "name": name,
            "platform": plat,
            "ok": all_ok,
            "checks": results,
        }, 200)

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
        global _REGISTRY_CACHE
        _REGISTRY_CACHE["data"] = None

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

        global _REGISTRY_CACHE
        _REGISTRY_CACHE["data"] = None

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
        global _REGISTRY_CACHE
        _REGISTRY_CACHE["data"] = None

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
        """
        from pbg_superpowers import workspace_catalog

        current_root = WORKSPACE
        current_resolved = str(current_root.resolve())

        current_name = self._read_workspace_name(current_root)
        result = {
            "current": {"name": current_name, "path": current_resolved},
            "workspaces": [],
        }

        try:
            catalog = workspace_catalog.list_workspaces()
        except Exception:
            catalog = []

        if not any(e.get("path") == current_resolved for e in catalog):
            catalog = [{
                "name": current_name,
                "path": current_resolved,
                "package": None,
                "added_at": None,
            }] + list(catalog)

        for entry in catalog:
            path = entry.get("path", "")
            row = {"name": entry.get("name") or Path(path).name, "path": path}
            if not Path(path).is_dir():
                row["status"] = "missing"
            elif path == current_resolved:
                row["status"] = "current"
                entry = workspace_catalog.find_entry(path)
                if entry is not None:
                    pid_val = int(entry.get("pid") or 0)
                    if pid_val <= 0:
                        alive = False
                    else:
                        try:
                            os.kill(pid_val, 0)
                            alive = True
                        except ProcessLookupError:
                            alive = False
                        except PermissionError:
                            alive = True  # PID exists but owned by another user
                        except (OSError, ValueError):
                            alive = False
                    if alive:
                        row["url"] = entry["url"]
                        row["pid"] = entry["pid"]
            else:
                entry = workspace_catalog.find_entry(path)
                if entry is None:
                    row["status"] = "stopped"
                else:
                    pid_val = int(entry.get("pid") or 0)
                    if pid_val <= 0:
                        alive = False
                    else:
                        try:
                            os.kill(pid_val, 0)
                            alive = True
                        except ProcessLookupError:
                            alive = False
                        except PermissionError:
                            alive = True  # PID exists but owned by another user
                        except (OSError, ValueError):
                            alive = False
                    if alive:
                        row["status"] = "running"
                        row["url"] = entry["url"]
                        row["pid"] = entry["pid"]
                    else:
                        row["status"] = "stale"
                        row["pid"] = entry.get("pid")
            result["workspaces"].append(row)

        order = {"current": 0, "running": 1, "stopped": 2, "stale": 3, "missing": 4}
        result["workspaces"].sort(key=lambda r: (order.get(r["status"], 99), r["name"]))

        self._json(result, 200)

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
        content_dir = workspace_paths().pbg / "server" / "content"
        if not content_dir.exists():
            self.send_response(204)
            self.end_headers()
            return
        files = sorted(content_dir.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            self.send_response(204)
            self.end_headers()
            return
        return self._serve_file(files[0], "text/html")

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
                        try:
                            payload = json.dumps(yaml.safe_load(text))
                        except Exception:
                            payload = json.dumps({"_error": "yaml parse"})
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

    @staticmethod
    def _guess_mime(rel: str) -> str:
        if rel.endswith(".css"): return "text/css"
        if rel.endswith(".js"): return "application/javascript"
        if rel.endswith(".json"): return "application/json"
        if rel.endswith(".png"): return "image/png"
        if rel.endswith(".svg"): return "image/svg+xml"
        if rel.endswith(".html"): return "text/html"
        if rel.endswith(".tsv"): return "text/tab-separated-values"
        return "text/plain"


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
