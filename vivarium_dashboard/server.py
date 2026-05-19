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
        ws_data = yaml.safe_load(ws_yaml.read_text())
        slug = ws_data.get("name", "")
        # Support explicit package_path in workspace.yaml (most reliable).
        package_name = ws_data.get("package_path") or ("pbg_" + slug.replace("-", "_"))

        # Build the set of top-level package names that this workspace explicitly
        # owns or imports.  Used inside the subprocess to tag each discovered class.
        # imports is a dict keyed by catalog name; the Python package name lives
        # in imports[name].get("package") or falls back to name.replace("-", "_").
        imports_dict = ws_data.get("imports", {}) or {}
        _ws_import_pkgs: list[str] = []
        for cat_name, imp_val in imports_dict.items():
            if isinstance(imp_val, dict):
                pkg = imp_val.get("package") or cat_name.replace("-", "_")
            else:
                pkg = cat_name.replace("-", "_")
            _ws_import_pkgs.append(pkg.split(".")[0])
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
        'pbg_superpowers', 'vivarium_dashboard',
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
    except Exception as e:
        data = {"error": str(e), "processes": [], "types": []}

    _REGISTRY_CACHE["data"] = data
    _REGISTRY_CACHE["ts"] = now
    return data


def _save_upload(file_b64: str, target_path: Path) -> str:
    """Decode base64-encoded file content, write to target_path, return sha256."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(file_b64)
    target_path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


WORKSPACE: Path = Path("/")  # set by main()
LOCK = Lock()

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

    Studies created by /api/study-create-from-run live in ``studies/<name>/``.
    Pre-Phase-1 investigations live in ``investigations/<name>/``. The aliased
    /api/study-* handlers must find both.
    """
    studies_path = WORKSPACE / "studies" / name
    if studies_path.is_dir():
        return studies_path
    return WORKSPACE / "investigations" / name


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
    return spec


def _discover_viz_html_files(name: str) -> list[dict]:
    """Walk ``studies/<name>/viz/*.html`` and return embed_visualizations entries.

    Returns one dict per HTML file, with the shape the study-detail template
    expects: ``{name, url, description}``. The URL is workspace-relative so
    the dashboard's static-file fallback serves it.
    """
    viz_dir = WORKSPACE / "studies" / name / "viz"
    if not viz_dir.is_dir():
        return []
    out = []
    for html_file in sorted(viz_dir.glob("*.html")):
        size_kb = max(1, html_file.stat().st_size // 1024)
        rel = html_file.relative_to(WORKSPACE).as_posix()
        out.append({
            "name": f"{html_file.stem} (auto)",
            "url": f"/{rel}",
            "description": (
                f"Auto-discovered Plotly viz rendered at "
                f"{html_file.stat().st_mtime:.0f}s epoch "
                f"({size_kb} KB). Source: render_visualizations against the "
                f"latest runs.db history."
            ),
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
    viz_dir = WORKSPACE / "investigations" / inv_slug / "viz"
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
    runs_db = WORKSPACE / "studies" / name / "runs.db"
    if not runs_db.is_file():
        return []
    conn = sqlite3.connect(str(runs_db))
    conn.row_factory = sqlite3.Row
    try:
        # Discover available tables; both should exist for pbg_runner-wrapped
        # runs, but older backfilled DBs may only have runs_meta.
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        rows_by_id: dict[str, dict] = {}
        if "runs_meta" in tables:
            for r in conn.execute(
                "SELECT run_id, spec_id, label, params_json, started_at, "
                "completed_at, n_steps, status, sim_name "
                "FROM runs_meta ORDER BY started_at DESC"
            ):
                try:
                    params = _json.loads(r["params_json"] or "{}")
                except Exception:
                    params = {}
                rows_by_id[r["run_id"]] = {
                    "run_id":       r["run_id"],
                    "spec_id":      r["spec_id"],
                    "label":        r["label"] or r["sim_name"] or "",
                    "sim_name":     r["sim_name"] or r["label"] or "",
                    "variant":      params.get("variant"),
                    "composite":    params.get("composite") or r["spec_id"],
                    "params":       params,
                    "n_steps":      r["n_steps"],
                    "status":       r["status"],
                    "started_at":   r["started_at"],
                    "completed_at": r["completed_at"],
                    "source":       "runs_meta",
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
        conn.close()

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

    out = []
    for r in rows_by_id.values():
        r["started_at_iso"] = _iso(r.get("started_at"))
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
    """Yield every study directory across both studies/ and investigations/.

    A name present in both locations yields only the studies/ entry (the v3
    location wins, matching _study_dir's precedence).
    """
    seen = set()
    for root_name in ("studies", "investigations"):
        root = WORKSPACE / root_name
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name in seen:
                continue
            seen.add(d.name)
            yield d


def _iter_iset_dirs(ws_root: Path | None = None):
    """Yield investigations/<name>/ dirs that contain an investigation.yaml.

    'iset' = investigation-set (a named collection of studies with the v3
    'investigations as collections' semantics, distinct from the legacy
    investigations/<name>/spec.yaml study format).

    ``ws_root`` defaults to the module-level WORKSPACE constant; tests can
    pass an explicit path to walk an isolated tmp workspace.
    """
    root = (ws_root or WORKSPACE) / "investigations"
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

    # 3: anything in the "active" set OR runs accumulated → running.
    if any(s in _STUDY_STATUS_RUNNING for s in statuses):
        return "running"
    if any(has_runs):
        return "running"

    # 4: at least one done but not all → mixed-progress.
    if any(s in _STUDY_STATUS_COMPLETE for s in statuses):
        return "in_progress"

    # 5: default.
    return "planning"


def _read_study_status(ws_root: Path, slug: str) -> tuple[str, bool]:
    """Read (status, has_runs) for a member study referenced by an iset.

    Returns ``("planning", False)`` if the study can't be located or parsed —
    treat missing-children as benign for status derivation rather than
    poisoning the entire investigation.
    """
    candidates = [
        ws_root / "studies" / slug / "study.yaml",
        ws_root / "investigations" / slug / "spec.yaml",
    ]
    for sp in candidates:
        if not sp.is_file():
            continue
        try:
            spec = yaml.safe_load(sp.read_text()) or {}
        except Exception:
            return "planning", False
        status = spec.get("status") or "planning"
        # F2: count via _count_runs_for_study so we see runs that landed in
        # runs.db without a matching study.yaml entry (the new canonical
        # path). spec.runs still merged in via max() for legacy specs.
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
            spec = yaml.safe_load(sp.read_text()) or {}
        except Exception:
            return {axis: None for axis in _MULTIAXIS_STATUS_FIELDS}
        return {axis: spec.get(axis) for axis in _MULTIAXIS_STATUS_FIELDS}
    return {axis: None for axis in _MULTIAXIS_STATUS_FIELDS}


def _build_iset_summary_for_test(ws_root: Path) -> list[dict]:
    """Pure function backing ``GET /api/iset-list`` — emits the same list
    of summary dicts that the handler returns, but without HTTP plumbing.

    Each entry includes ``effective_status`` derived from the member
    studies' current statuses.
    """
    out: list[dict] = []
    for d in _iter_iset_dirs(ws_root):
        try:
            spec = yaml.safe_load((d / "investigation.yaml").read_text()) or {}
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
        })
    return out


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


def _build_investigation_registry_for_test(
    ws_root: Path,
    this_url: str,
    *,
    list_servers_fn=None,
    fetch_peer_fn=None,
) -> dict:
    """Pure function backing GET /api/investigation-registry.

    ``list_servers_fn`` and ``fetch_peer_fn`` are injectable to keep the
    helper testable without filesystem or HTTP I/O.

    Returns ``{current: {...}, running_others: [...]}`` where each
    ``running_others`` entry carries ``{slug, title?, worktree_path, url,
    effective_status, pid}``. Entries whose peer probe fails (None) are
    dropped from the list so the sidebar never renders empty rows.
    """
    if list_servers_fn is None:
        try:
            from pbg_superpowers import workspace_catalog
            list_servers_fn = workspace_catalog.list_servers
        except Exception:
            list_servers_fn = lambda: []
    if fetch_peer_fn is None:
        fetch_peer_fn = _peer_current_investigation

    # Current Investigation: pick from this workspace's iset list with the
    # same heuristic we use for peers (running > first > none).
    invs = _build_iset_summary_for_test(ws_root)
    if invs:
        running = next(
            (i for i in invs if i.get("effective_status") == "running"),
            None,
        )
        chosen = running or invs[0]
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

    # Running-others: every server record that does NOT point at this
    # worktree path AND has a live PID.
    this_path = str(ws_root.resolve())
    others: list[dict] = []
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

    return {"current": current, "running_others": others}


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
        spec = yaml.safe_load(spec_path.read_text()) or {}
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
        "expert_docs":      spec.get("expert_docs") or [],
        "acceptance_criteria": spec.get("acceptance_criteria") or [],
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

    spec: dict = {
        "schema_version": 1,
        "name": name,
        "title": name,
        "status": "planning",
        "description": overview,
        "studies": [],
    }
    if parent_studies:
        spec["parent_studies"] = list(parent_studies)

    inv_dir.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
        os.replace(tmp, target)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise

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
    spec = yaml.safe_load(sf.read_text()) or {}
    spec["objective"] = text
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200



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
    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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
    steps = int(body.get("steps") or params_n_steps or 5)
    generator_overrides = params

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text())
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

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
    emit_paths = _collect_study_observables(spec)
    response, code = _run_composite_subprocess(
        pkg=pkg, state=state, steps=steps, db_file=db_file,
        run_id=run_id, spec_id=spec_id, label=label, sim_name=label,
        overrides=generator_overrides, timeout=timeout_s,
        emit_paths=emit_paths,
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
    return response, code


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
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text()) or {}
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
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
        return [str(Path(p).relative_to(study_dir)) for p in paths], []
    except Exception as e:  # noqa: BLE001
        return [], [{"error": f"render_visualizations failed: "
                     f"{type(e).__name__}: {e}"}]


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

    spec = yaml.safe_load(sf.read_text()) or {}
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
    """
    from vivarium_dashboard.lib import composite_runs as cr

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

    spec = yaml.safe_load(sf.read_text()) or {}
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
    steps = int(body.get("steps") or params_n_steps or 5)
    generator_overrides = params

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text())
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

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
    emit_paths = _collect_study_observables(spec)
    response, code = _run_composite_subprocess(
        pkg=pkg, state=state, steps=steps, db_file=db_file,
        run_id=run_id, spec_id=spec_id, label=variant_name,
        sim_name=variant_name, overrides=generator_overrides,
        timeout=timeout_s, emit_paths=emit_paths,
    )
    # F2: no _append_study_run — the runs_meta row is the canonical record;
    # see the matching note in run-baseline above.
    return response, code


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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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

    spec = yaml.safe_load(sf.read_text()) or {}
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
    return tpl.render(study=spec, name=name)


def _jinja_fmt_ts(ts) -> str:
    """Format a unix timestamp as 'YYYY-MM-DD HH:MM' UTC, or '' if missing."""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return ""
    if not ts:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _jinja_fmt_duration(seconds) -> str:
    """Format a duration in seconds as '12s', '1m 30s', '2h 15m', or '' if missing."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
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
    for line in gm.read_text().splitlines():
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


def _run_composite_subprocess(*, pkg, state, steps, db_file, run_id, spec_id,
                              label, overrides=None, sim_name=None, timeout=1800,
                              emit_paths=None):
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
        }
        script = textwrap.dedent(f"""
            import json, sys, traceback
            try:
                from {pkg}.core import build_core
                from process_bigraph import Composite, gather_emitter_results
                from process_bigraph.emitter import SQLiteEmitter
                from pbg_superpowers.composite_generator import (
                    _REGISTRY, build_generator, discover_generators,
                )
                from vivarium_dashboard.lib import composite_runs as cr
                from bigraph_schema.json_codec import BigraphJSONEncoder as _BJE
                _payload = {payload!r}
                if not _REGISTRY: discover_generators()
                entry = _REGISTRY[_payload['spec_id']]
                core = build_core()
                core.register_link('SQLiteEmitter', SQLiteEmitter)
                doc = build_generator(entry, overrides=_payload['overrides'])
                state = doc.get('state', doc) if isinstance(doc, dict) else doc
                if _payload.get('emit_paths'):
                    state = cr.inject_emitter_for_paths(state, _payload['emit_paths'])
                state = cr.inject_sqlite_emitter(
                    state, run_id=_payload['run_id'], db_file=_payload['db_file'])
                composite = Composite({{'state': state}}, core=core)
                composite.run(_payload['steps'])
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
                core = build_core()
                core.register_link('SQLiteEmitter', SQLiteEmitter)
                with open({_state_path!r}) as _sf:
                    _state = json.load(_sf, object_hook=bigraph_json_hook)
                composite = Composite({{'state': _state}}, core=core)
                composite.run({steps})
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

    conn = cr.connect(db_file)
    try:
        try:
            cr.save_metadata(conn, spec_id=spec_id, run_id=run_id,
                             params=overrides, label=label,
                             started_at=time.time(), n_steps=steps)
            if sim_name is not None:
                conn.execute("UPDATE runs_meta SET sim_name=? WHERE run_id=?",
                             (sim_name, run_id))
                conn.commit()
        except sqlite3.IntegrityError:
            return ({"simulation_id": run_id,
                     "error": "duplicate run_id (rare timing collision) — retry"}, 500)

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


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):  # silence default request logging
        pass

    def do_GET(self):
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
            return self._serve_file(WORKSPACE / "reports" / "index.html", "text/html")
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
        if self.path.startswith("/api/iset-list"):
            return self._get_iset_list()
        if self.path.startswith("/api/iset/"):
            return self._get_iset_detail()
        if self.path.startswith("/api/investigation-run-unblocked-status"):
            return self._get_investigation_run_unblocked_status()
        if self.path.startswith("/api/investigation-registry"):
            return self._get_investigation_registry()
        if self.path.startswith("/api/study-charts/"):
            return self._get_study_charts()
        if self.path.startswith("/api/work-composite-diff"):
            return self._get_work_composite_diff()
        if self.path.startswith("/api/references-bib"):
            return self._get_references_bib()
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
        if self.path.startswith("/api/ui-config"):
            return self._get_ui_config()
        if self.path.startswith("/api/git-status"):
            return self._get_git_status()
        # Serve the bundled loom-explore viewer.
        if self.path.startswith("/loom-explore"):
            # Strip query string before resolving to the file on disk; popup
            # URLs include ?id=<ref> which would otherwise prevent the
            # static handler from finding index.html.
            loom_path = self.path.split("?", 1)[0]
            rel = loom_path[len("/loom-explore"):].lstrip("/") or "index.html"
            if ".." in rel.split("/"):
                self.send_response(403); self.end_headers(); return
            # Bundled inside the vivarium-dashboard package (was workspace-vendored).
            target = STATIC_DIR / "loom-explore" / rel
            return self._serve_file(target, self._guess_mime(rel))

        # Generic static file serving — also strip query strings so any
        # other route that the client appends params to still resolves.
        static_path = self.path.split("?", 1)[0]
        rel = static_path.lstrip("/")
        # Refuse path traversal and absolute paths.
        if ".." in rel.split("/") or rel.startswith("/"):
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
        fallback = WORKSPACE / "reports" / rel
        return self._serve_file(fallback, self._guess_mime(rel))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode()) if length else {}
        except json.JSONDecodeError as e:
            return self._json({"error": f"invalid JSON: {e}"}, 400)

        method_name = _POST_ROUTE_MAP.get(self.path)
        if method_name is None:
            return self._json({"error": "not found"}, 404)
        getattr(self, method_name)(body)

    def do_DELETE(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode()) if length else {}
        except json.JSONDecodeError as e:
            return self._json({"error": f"invalid JSON: {e}"}, 400)

        route_map = {
            "/api/simulation":    self._delete_simulation,
            "/api/simulation-run": self._delete_simulation_run,
            "/api/visualization": self._delete_visualization,
            "/api/investigation-composite": self._delete_investigation_composite,
            "/api/investigation-comparison": self._delete_investigation_comparison,
            "/api/investigation-group":      self._delete_investigation_group,
        }
        handler_fn = route_map.get(self.path)
        if handler_fn is None:
            return self._json({"error": "not found"}, 404)
        handler_fn(body)

    # ------------------------------------------------------------------
    # POST handlers
    # ------------------------------------------------------------------

    def _post_click(self, body: dict):
        with LOCK:
            events = WORKSPACE / ".pbg" / "server" / "state" / "events"
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

        if file_b64:
            if not filename:
                return self._json({"error": "filename is required when file_b64 is provided"}, 400)
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
            bib_file = WORKSPACE / "references" / "papers.bib"
            claims_file = WORKSPACE / "references" / "claims.yaml"
            pdf_dest_rel = f"references/papers/{bib_key}.pdf"
            pdf_dest = WORKSPACE / pdf_dest_rel

            if bib_file.exists():
                existing_text = bib_file.read_text()
                if re.search(rf"@\w+\{{{re.escape(bib_key)},", existing_text):
                    raise ValueError(f"BibTeX key '{bib_key}' already exists in papers.bib")

            sha = _save_upload(pdf_b64, pdf_dest)

            bibtex_entry = build_bibtex(bib_key, title, authors, year, journal, doi)
            bib_file.parent.mkdir(parents=True, exist_ok=True)
            existing_bib = bib_file.read_text() if bib_file.exists() else ""
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

            if claim_ids:
                import yaml as _yaml
                existing_claims: dict = {}
                if claims_file.exists():
                    try:
                        existing_claims = _yaml.safe_load(claims_file.read_text()) or {}
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
            bib_file = WORKSPACE / "references" / "papers.bib"
            claims_file = WORKSPACE / "references" / "claims.yaml"

            if bib_file.exists():
                existing_text = bib_file.read_text()
                if f"{{{bibkey}," in existing_text or f"{{{bibkey} " in existing_text:
                    raise ValueError(f"BibTeX key '{bibkey}' already exists in papers.bib")

            bib_file.parent.mkdir(parents=True, exist_ok=True)
            with bib_file.open("a") as f:
                f.write("\n" + bibtex_text + "\n")

            if claim_mappings:
                import yaml as _yaml
                existing_claims: dict = {}
                if claims_file.exists():
                    try:
                        existing_claims = _yaml.safe_load(claims_file.read_text()) or {}
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

        if file_b64:
            if not filename:
                return self._json({"error": "filename is required when file_b64 is provided"}, 400)
            ext = Path(filename).suffix if Path(filename).suffix else ".pdf"
            dest_rel = f"references/expert/{_safe_slug(name)}{ext}"
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
            dest_rel = f"references/expert/{_safe_slug(name)}{ext}"

        commit_msg = f"feat(5): add expert document '{name}'"

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
            entry: dict = {"name": name, "path": dest_rel, "sha256": sha}
            if description:
                entry["description"] = description
            if contributor:
                entry["contributor"] = contributor
            if claims_supported:
                entry["claims_supported"] = claims_supported
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
            known = {c["name"] for c in self._list_visualization_classes()}
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

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
        viz = next((v for v in (ws_data.get("visualizations") or []) if v.get("name") == name), None)
        if not viz:
            return self._json({"error": f"visualization '{name}' not registered (Add it first)"}, 404)

        description = viz.get("description") or ""
        if not description.strip():
            return self._json({"error": "visualization has no description — edit it first"}, 400)

        req_dir = WORKSPACE / ".pbg" / "viz-requests"
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

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
        viz = next((v for v in (ws_data.get("visualizations") or []) if v.get("name") == name), None)
        if not viz:
            return self._json({"status": "missing", "name": name}, 200)

        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        response_path = WORKSPACE / ".pbg" / "viz-responses" / f"{name}.py"
        staged_path = WORKSPACE / ".pbg" / "visualizations-staged" / f"{name}.py"
        committed_path = WORKSPACE / pkg / "visualizations" / f"{name}.py"
        request_path = WORKSPACE / ".pbg" / "viz-requests" / f"{name}.md"

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
        src = WORKSPACE / ".pbg" / "viz-responses" / f"{name}.py"
        if not src.exists():
            return self._json({"error": f"no skill response yet — run /pbg-viz {name} first"}, 404)
        dest_dir = WORKSPACE / ".pbg" / "visualizations-staged"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.py"
        shutil.copy2(src, dest)
        return self._json({"ok": True, "staged_path": str(dest.relative_to(WORKSPACE))}, 200)

    def _post_visualization_commit_batch(self, body: dict):
        """Move all staged visualizations to the workspace package + commit on active branch.

        Body: {names?: list[str]} — if omitted, commits all staged.
        """
        staged_dir = WORKSPACE / ".pbg" / "visualizations-staged"
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

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text()) or {}
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

        req_dir = WORKSPACE / ".pbg" / "viz-requests"
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
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text()) or {}
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
        test_dir = WORKSPACE / "tests"
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
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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

        reports_dir = WORKSPACE / "reports"
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
                ws_data = yaml.safe_load(ws_path.read_text()) or {}
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
        title = (body.get("title") or "").strip() or f"Workstream: {branch}"
        body_text = (body.get("body") or "").strip() or "Created via pbg-template dashboard."

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
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
            "unpushed": unpushed,
            "pushed": state.get("pushed", False),
            "has_origin": _has_origin_remote(),
            "gh_available": shutil.which("gh") is not None,
            "pr_number": state.get("pr_number"),
            "pr_url": state.get("pr_url"),
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

        Returns ``{simulations: [...]}`` aggregated from ``.pbg/composite-runs.db``
        and every ``studies/<name>/runs.db``, with Studies-association annotated
        from each ``study.yaml``'s ``runs[]``. Newest first.
        """
        _ws_add_to_sys_path()
        try:
            from vivarium_dashboard.lib.simulations_index import list_simulations
            sims = list_simulations(WORKSPACE)
        except Exception as e:  # noqa: BLE001 — never blank-page the user
            return self._json({"error": f"simulations index failed: {e}"}, 500)
        return self._json({"simulations": sims}, 200)

    def _get_composite_runs(self):
        """GET /api/composite-runs?spec_id=X — list runs for one composite spec."""
        from urllib.parse import urlparse, parse_qs
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib import composite_runs as cr

        qs = parse_qs(urlparse(self.path).query)
        spec_id = (qs.get("spec_id") or [""])[0]
        if not spec_id:
            return self._json({"runs": [], "error": "missing spec_id"}, 400)

        db_file = WORKSPACE / ".pbg" / "composite-runs.db"
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

        db_file = WORKSPACE / ".pbg" / "composite-runs.db"
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

        db_file = WORKSPACE / ".pbg" / "composite-runs.db"
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

        db_file = WORKSPACE / ".pbg" / "composite-runs.db"
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
                    resp["error"] = log_full.read_text()[-2000:]
        elif meta["status"] == "completed":
            viz_file = WORKSPACE / ".pbg" / "runs" / run_id / "viz.json"
            if viz_file.is_file():
                try:
                    resp["viz_html"] = json.loads(viz_file.read_text())
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
            doc = yaml.safe_load(composite_path.read_text()) or {}
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

        Two sources, merged in display order (live first, static after):

          live    — ``studies/<name>/runs.db`` (the per-step history
                    emitted by SQLiteEmitter). Picks the latest entry
                    from the ``simulations`` table (filtered to
                    ``baseline-steady-state`` when present) and renders
                    a small canonical set of line charts.
          static  — any pre-rendered ``studies/<name>/charts/*.svg``
                    files (with optional ``*.meta.json`` sidecars
                    providing title + caption). These are the domain-
                    specific charts the study authors checked in
                    directly (e.g. chromosome maps, DnaA-box positions).
        """
        import urllib.parse
        import yaml as _yaml
        from vivarium_dashboard.lib.study_charts import (
            render_study_charts, render_v4_test_charts,
            discover_static_study_charts,
        )
        from vivarium_dashboard.lib.simulations_index import (
            discover_default_baseline_db,
        )
        path = urllib.parse.urlparse(self.path).path
        name = path[len("/api/study-charts/"):].strip("/")
        if not name:
            return self._json({"error": "missing study name"}, 400)
        runs_db = WORKSPACE / "studies" / name / "runs.db"
        charts_dir = WORKSPACE / "studies" / name / "charts"
        spec_path = WORKSPACE / "studies" / name / "study.yaml"
        try:
            # Detect v4: study.yaml with schema_version: 4 → render charts
            # per-test from tests[].measure.path, with default-baseline
            # fallback when the per-study runs.db is empty.
            spec = None
            if spec_path.is_file():
                try:
                    spec = _yaml.safe_load(spec_path.read_text())
                except Exception:
                    spec = None
            is_v4 = isinstance(spec, dict) and spec.get("schema_version") == 4

            if is_v4:
                fallback_db = discover_default_baseline_db(WORKSPACE)
                live_charts = render_v4_test_charts(
                    spec, runs_db, fallback_db=fallback_db,
                )
            else:
                live_charts = render_study_charts(
                    runs_db, run_name="baseline-steady-state",
                )
                if not live_charts:
                    live_charts = render_study_charts(runs_db, run_name=None)
            for c in live_charts:
                c.setdefault("source", "live")
            static_charts = discover_static_study_charts(charts_dir)
        except Exception as e:
            return self._json({"error": str(e), "study": name}, 500)
        return self._json({
            "study": name,
            "schema_version": (spec or {}).get("schema_version"),
            "charts": live_charts + static_charts,
            "db_exists": runs_db.exists(),
            "static_count": len(static_charts),
            "live_count": len(live_charts),
        }, 200)

    def _get_iset_detail(self):
        """GET /api/iset/<name> — return one investigation + its resolved studies.

        Each constituent study is returned with its `parent_studies:`
        normalized (legacy strings become dicts) so the frontend DAG layout
        has consistent shape.
        """
        import urllib.parse
        path = urllib.parse.urlparse(self.path).path
        name = path.split("/api/iset/", 1)[-1].strip("/")
        if not name:
            return self._json({"error": "investigation name required"}, 400)

        spec_path = WORKSPACE / "investigations" / name / "investigation.yaml"
        if not spec_path.is_file():
            return self._json({"error": f"no investigation.yaml at {spec_path}"}, 404)
        try:
            spec = yaml.safe_load(spec_path.read_text()) or {}
        except Exception as e:
            return self._json({"error": f"parse failed: {e}"}, 500)

        # Resolve constituent studies.
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError

        from vivarium_dashboard.lib.investigations import normalize_dag_edges
        # Delegate to the canonical helper — reads pipeline_gate.prerequisites
        # first, falls back to parent_studies for back-compat (with a
        # DeprecationWarning when only the legacy field is set).
        def _normalize_parents(study_spec: dict) -> list[dict]:
            return normalize_dag_edges(study_spec)

        studies_out = []
        for slug in (spec.get("studies") or []):
            study_dir = WORKSPACE / "studies" / slug
            sp = study_dir / "study.yaml"
            if not sp.is_file():
                sp = WORKSPACE / "investigations" / slug / "spec.yaml"
            if not sp.is_file():
                studies_out.append({"name": slug, "status": "missing", "error": "study.yaml not found"})
                continue
            try:
                study_spec = load_spec(sp)
            except InvestigationSpecError as e:
                studies_out.append({"name": slug, "status": "invalid", "error": str(e)})
                continue
            # New-template aware: derive counts from new fields when present,
            # fall back to legacy fields. Purpose.question wins over top-level
            # question if both exist.
            sim_set = study_spec.get("simulation_set") or []
            beh_tests = study_spec.get("behavior_tests") or study_spec.get("expected_behavior") or []
            readouts = study_spec.get("readouts") or study_spec.get("observables") or []
            purpose = study_spec.get("purpose") or {}
            question = (purpose.get("question") if isinstance(purpose, dict) else None) or study_spec.get("question", "")
            follow_ups = study_spec.get("follow_up_studies") or []
            findings = study_spec.get("findings") or []
            studies_out.append({
                "name":            study_spec["name"],
                "status":          study_spec.get("status", "planned"),
                "phase":           study_spec.get("phase"),
                "question":        question,
                "n_variants":      len(sim_set) if sim_set else len(study_spec.get("variants") or []),
                "n_interventions": len(study_spec.get("interventions") or []),
                "n_runs":          _count_runs_for_study(study_spec["name"], study_spec),  # F2
                "baseline_source": _format_baseline_source(study_spec),
                "parent_studies":  _normalize_parents(study_spec),
                "n_behaviors":     len(beh_tests),
                "n_readouts":      len(readouts),
                "n_requirements":  len(study_spec.get("implementation_requirements") or study_spec.get("gaps") or []),
                "n_followups":     len(follow_ups),
                "follow_up_studies": follow_ups,
                "n_findings":      len(findings),
                "findings":        findings,
                # Pass A multi-axis status: pass through whichever of the six
                # axes are set on the study spec. All optional, all independent.
                "design_status":         study_spec.get("design_status"),
                "implementation_status": study_spec.get("implementation_status"),
                "simulation_status":     study_spec.get("simulation_status"),
                "evaluation_status":     study_spec.get("evaluation_status"),
                "gate_status":           study_spec.get("gate_status"),
                "expert_review_status":  study_spec.get("expert_review_status"),
            })

        # Compute effective_status from the member studies' current statuses.
        # The author-declared YAML 'status' represents intent; the dashboard
        # surfaces effective_status as the live signal.
        member_statuses = [s.get("status", "planning") for s in studies_out]
        member_has_runs = [(s.get("n_runs") or 0) > 0 for s in studies_out]
        effective_status = compute_investigation_status(
            member_statuses, has_runs=member_has_runs,
        )

        return self._json({
            "name":             spec.get("name", name),
            "title":            spec.get("title", spec.get("name", name)),
            "description":      spec.get("description", ""),
            "lead":             spec.get("lead", ""),
            "at_a_glance":      spec.get("at_a_glance") or [],
            "how_to_read":      spec.get("how_to_read") or [],
            "glossary":         spec.get("glossary") or [],
            "biological_story": spec.get("biological_story", ""),
            "question":         spec.get("question", ""),
            "hypothesis":       spec.get("hypothesis", ""),
            "status":           spec.get("status", "planning"),
            "effective_status": effective_status,
            "expert_docs":      spec.get("expert_docs") or [],
            "acceptance_criteria": spec.get("acceptance_criteria") or [],
            "studies":          studies_out,
        }, 200)

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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
                doc = json.loads(source_file.read_text())
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
        Used by the Composites tab's loom-explore iframe to fetch the
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
            doc = yaml.safe_load(path.read_text()) or {}
        except Exception as e:
            return self._json({"error": f"parse failed: {e}"}, 500)
        return self._json({"state": doc}, 200)

    def _get_investigations(self):
        """GET /api/investigations — return summaries of all investigations.

        Includes the study-dependency DAG: each row carries `parent_studies`
        (normalized to [{study, condition}]) and a computed `blocked` flag
        plus `blocked_by` list pointing at parents that don't yet satisfy
        their condition.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError

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

        from vivarium_dashboard.lib.investigations import normalize_dag_edges
        # Single read path — reads pipeline_gate.prerequisites (canonical)
        # with parent_studies fallback. Emits DeprecationWarning for the
        # legacy-only case so workspaces know to migrate.
        def _normalize_parents(spec: dict) -> list[dict]:
            return normalize_dag_edges(spec)

        def _condition_satisfied(parent: dict | None, condition: str) -> bool:
            """Does this parent currently satisfy `condition`?"""
            if parent is None:
                # Parent doesn't exist in workspace — treat as unsatisfiable,
                # so the child shows up as blocked with a useful diagnostic.
                return False
            status = parent.get("status", "planned")
            if condition == "ran":
                return status in ("ran", "complete")
            if condition == "complete":
                return status == "complete"
            if condition == "tests-passed":
                tests = parent.get("tests")
                # New v4 shape: tests is a list of {name, status, ...}.
                if isinstance(tests, list):
                    statuses = [
                        (t.get("status") or "").lower()
                        for t in tests if isinstance(t, dict)
                    ]
                    if not statuses:
                        return False
                    return all("pass" in s for s in statuses)
                # Legacy v3/v4-extras shape: tests is a mapping with last_results.
                last = (tests or {}).get("last_results") or {}
                summary = last.get("summary") or {}
                passed = summary.get("passed", 0) or 0
                failed = summary.get("failed", 0) or 0
                return failed == 0 and passed > 0
            return False

        out = []
        for d, spec in loaded:
            if spec.get("__invalid__"):
                out.append({"name": spec["name"], "status": "invalid", "error": spec["error"]})
                continue
            # Multi-composite (new) vs single-`composite:` (legacy) shape.
            composites = spec.get("composites") or []
            if composites:
                composite_summary = ", ".join(c.get("name", "") for c in composites)
                n_runs = _count_runs_for_study(spec["name"], spec)  # F2: runs.db canonical
            else:
                composite_summary = spec.get("composite", "")
                # Legacy v2 specs sometimes used `simulations:` instead of `runs:`.
                # _count_runs_for_study only checks `runs:` against runs.db, so
                # preserve the `simulations:` fallback for that shape.
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
                # DAG / dependency fields.
                "parent_studies":  parents,
                "blocked":         len(blocked_by) > 0,
                "blocked_by":      blocked_by,
            }
            out.append(row)
        return self._json({"investigations": out}, 200)

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

        inv_dir = WORKSPACE / "studies" / name
        if inv_dir.exists() or (WORKSPACE / "investigations" / name).exists():
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
                # v3-shape spec: dotted ref lives in `baseline:`; no sidecar.
                spec = {
                    "name": name,
                    "description": "",
                    "status": "planned",
                    "baseline": [
                        {
                            "name": baseline_name,
                            "composite": source,
                            "params": {},
                        }
                    ],
                    "variants": [],
                    "interventions": [],
                    "observables": [],
                    "visualizations": [],
                    "runs": [],
                }
                (inv_dir / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
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
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
                text = path.read_text()
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
        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
            spec = _y.safe_load(spec_path.read_text()) or {}
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
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
            out.append({"address": f"local:{name}", "name": name, "doc": doc})
        return out

    def _get_ui_config(self):
        """GET /api/ui-config — return UI feature flags from workspace.yaml."""
        try:
            ws = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text()) or {}
        except Exception:
            ws = {}
        ui = ws.get("ui") or {}
        return self._json({
            "composite_view": ui.get("composite_view", "loom-explore"),
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

    def _get_visualization_instances(self):
        """GET /api/visualization-instances — list class-backed configured viz
        instances from workspace.yaml.visualizations (entries with a ``class:`` key).

        Returns: [{name, class, address, config, description?}, ...]
        """
        try:
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text()) or {}
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
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
            resp_path = WORKSPACE / ".pbg" / "viz-responses" / f"{name}.py"
            req_path = WORKSPACE / ".pbg" / "viz-requests" / f"{name}.md"
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

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
            text = sidecar_path.read_text()
            composite_doc = (json.loads(text) if sidecar_path.suffix.lower() == ".json"
                              else yaml.safe_load(text)) or {}
        elif spec.get("composite"):
            # Legacy single-composite shape: resolve via workspace registry.
            composite_name = spec["composite"]
            path = find_composite_path(WORKSPACE, pkg, composite_name)
            if path is None:
                return self._json({"error": f"composite not found: {composite_name}"}, 404)
            text = path.read_text()
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
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text()) or {}
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

        inv_dir = WORKSPACE / "studies" / auto_name
        if inv_dir.exists() or (WORKSPACE / "investigations" / auto_name).exists():
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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
        parent_doc = yaml.safe_load(parent.read_text()) or {}
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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text()) or {}
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
            doc = yaml.safe_load(sidecar.read_text()) or {}
            doc['name'] = target_name
            if description is not None:
                doc['description'] = description
            target_path.write_text(yaml.safe_dump(doc, sort_keys=False))
            # Mark variant promoted in spec.yaml
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
        spec = yaml.safe_load(spec_path.read_text()) or {}
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
        parent_doc = yaml.safe_load(parent_path.read_text()) or {}
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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
            spec = yaml.safe_load(spec_path.read_text()) or {}
            for key in ("question", "hypothesis", "status", "topic"):
                if key in fields:
                    spec[key] = fields[key]
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

        try:
            return self._json(*_commit_or_run(commit_msg, do_action))
        except Exception as e:
            return self._json({"error": f"workstream error: {e}"}, 500)

    # ------------------------------------------------------------------
    # Study-specific POST handlers (thin wrappers around pure helpers)
    # ------------------------------------------------------------------

    def _post_study_set_objective(self, body: dict):
        """POST /api/study-set-objective {study, text}"""
        response, code = _post_study_set_objective_for_test(WORKSPACE, body)
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
            spec = _yaml.safe_load(spec_path.read_text()) or {}
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
        """POST /api/study-seed-followup {parent, followup_idx} → seed child study.

        Reads parent study.yaml, picks ``follow_up_studies[followup_idx]``,
        and writes a new ``studies/<new-name>/study.yaml`` whose Purpose +
        Pipeline gate inherit context from the follow-up entry. The new
        study comes up as ``phase: Design`` / ``status: planned`` and is
        immediately visible in the dashboard's Investigations tab.
        """
        from vivarium_dashboard.lib.study_seed import seed_followup_study
        try:
            new_name = seed_followup_study(
                WORKSPACE, body.get("parent"), int(body.get("followup_idx", -1))
            )
        except FileNotFoundError as e:
            return self._json({"error": str(e)}, 404)
        except (ValueError, KeyError, IndexError) as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            return self._json({"error": f"seed failed: {e}"}, 500)
        return self._json({"new_study_name": new_name}, 200)


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
        inv_yaml = WORKSPACE / "investigations" / inv_slug / "investigation.yaml"
        if not inv_yaml.is_file():
            return self._json({"error": f"investigation not found: {inv_slug}"}, 404)
        try:
            iset = _yaml.safe_load(inv_yaml.read_text()) or {}
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
            spec_path = WORKSPACE / "studies" / member_name / "study.yaml"
            if not spec_path.is_file():
                # legacy: investigations/<name>/spec.yaml
                spec_path = WORKSPACE / "investigations" / member_name / "spec.yaml"
            if not spec_path.is_file():
                skipped.append({"study": member_name, "variant": "?",
                                "status": "skipped",
                                "error": "study.yaml not found"})
                continue
            try:
                spec = _yaml.safe_load(spec_path.read_text()) or {}
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
            spec_path = WORKSPACE / "studies" / study_slug / "study.yaml"
            if not spec_path.is_file():
                continue
            try:
                study_spec = _yaml.safe_load(spec_path.read_text()) or {}
            except _yaml.YAMLError:
                continue
            specs = study_spec.get("comparative_visualizations") or []
            if not specs:
                continue
            viz_dir = WORKSPACE / "studies" / study_slug / "viz"
            viz_dir.mkdir(parents=True, exist_ok=True)
            study_db = WORKSPACE / "studies" / study_slug / "runs.db"
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
        spec_path = WORKSPACE / "studies" / slug / "study.yaml"
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
        src = WORKSPACE / "studies" / name
        if not src.is_dir():
            return self._json({"error": "study not found"}, 404)
        data = _study_export_zip(WORKSPACE, name)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{name}.zip"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, body: str, code: int = 200):
        """Send an HTML response with the given body and status code."""
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        encoded = body.encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

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
        spec = yaml.safe_load(spec_path.read_text()) or {}

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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
        spec = yaml.safe_load(spec_path.read_text()) or {}
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
            data = yaml.safe_load(spec_path.read_text()) or {}
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
        spec_peek = yaml.safe_load(spec_path.read_text()) or {}
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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
            spec_peek = yaml.safe_load(spec_path.read_text()) or {}
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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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
        spec_peek = yaml.safe_load(spec_path.read_text()) or {}
        if not any(g.get("name") == grp_name
                   for g in (spec_peek.get("groups") or [])):
            return self._json({"error": f"group {grp_name!r} not found"}, 404)

        commit_msg = f"feat(investigations/{inv_name}): delete group {grp_name}"

        def do_action():
            data = yaml.safe_load(spec_path.read_text()) or {}
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

        Each record includes ``kind`` (``"spec"`` | ``"generator"``) and
        ``module`` so dashboards can tell static specs from
        ``@composite_generator`` functions. Generator entries also include
        ``default_n_steps`` (int | None) for UI pre-fill.
        """
        import importlib as _importlib
        _ws_add_to_sys_path()
        try:
            from vivarium_dashboard.lib.composite_lookup import discover_all_composites
        except ImportError as e:
            return self._json({"composites": [], "error": str(e)}, 200)

        try:
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
            pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
            # Eagerly import the workspace package so any @composite_generator
            # decorators inside it fire and register into pbg-superpowers'
            # _REGISTRY before discover_all_composites calls discover_generators().
            # The workspace is already on sys.path via _ws_add_to_sys_path().
            try:
                _importlib.import_module(pkg)
            except Exception:
                pass
            specs = discover_all_composites(WORKSPACE, pkg)
            out = []
            for s in specs.values():
                rec = {k: v for k, v in s.items() if not k.startswith("_")}
                rec.setdefault("kind", "spec")
                rec.setdefault("module", "")
                # Ensure default_n_steps is always present in every entry so
                # the UI can rely on the key existing (None for spec entries
                # that don't declare one).
                if "default_n_steps" not in rec:
                    rec["default_n_steps"] = None
                out.append(rec)
            return self._json({"composites": out}, 200)
        except Exception as e:
            return self._json({"composites": [], "error": str(e)}, 200)

    def _get_composite_state(self):
        """GET /api/composite-state?ref=<id-or-path>
        Returns: {state: <parsed composite YAML/JSON document>}
        Accepts either a dotted spec ID (pkg.composites.foo) or a workspace-relative file path.

        For ``@composite_generator``-decorated entries (kind=generator), calls
        ``build_generator`` with no overrides and returns the resulting document.
        """
        import urllib.parse
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        ref = qs.get("ref", "").strip()
        if not ref:
            return self._json({"error": "ref required"}, 400)

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
                return self._json({"state": doc, "kind": "generator",
                                    "module": entry.module}, 200)
        except ImportError:
            pass

        path = None
        # Try to resolve as a dotted spec ID via composite_lookup.
        try:
            from vivarium_dashboard.lib.composite_lookup import find_composite_path
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
            return self._json({"error": f"composite not found: {ref}"}, 404)

        try:
            text = path.read_text()
            doc = json.loads(text) if path.suffix.lower() == ".json" else (yaml.safe_load(text) or {})
        except Exception as e:
            return self._json({"error": f"parse failed: {e}"}, 500)

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

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
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
            return self._json({"error": f"spec file not found for id {spec_id}"}, 404)

        text = path.read_text()
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

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
        pkg = ws_data.get("package_path") or (
            "pbg_" + ws_data.get("name", "").replace("-", "_"))
        db_file = str(WORKSPACE / ".pbg" / "composite-runs.db")

        if run_registry.count_running(db_file) >= run_registry.CONCURRENCY_CAP:
            return self._json(
                {"error": "too many runs in progress — wait for one to finish"},
                429)

        run_id = cr.generate_run_id(spec_id, overrides)
        run_dir = WORKSPACE / ".pbg" / "runs" / run_id
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
                ws = yaml.safe_load(ws_path.read_text()) or {}
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
                ws = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text()) or {}
            pkg = ws.get("package_path") or (
                "pbg_" + (ws.get("name") or "").replace("-", "_")
            )
            all_comps = discover_all_composites(WORKSPACE, pkg)
        except Exception:
            all_comps = {}
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
                    text = skill_md.read_text()
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
        info_file = WORKSPACE / ".pbg" / "server" / "server-info"
        if not info_file.is_file():
            return self._json(
                {"error": "server-info file not found - is the dashboard running?"},
                503,
            )
        try:
            info = json.loads(info_file.read_text())
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

        catalog_path = WORKSPACE / "scripts" / "_catalog" / "modules.json"
        if not catalog_path.is_file():
            return self._json({"error": "catalog not found"}, 404)
        try:
            catalog = json.loads(catalog_path.read_text())
        except Exception as e:
            return self._json({"error": f"catalog parse failed: {e}"}, 500)
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

        catalog_path = WORKSPACE / "scripts" / "_catalog" / "modules.json"
        if not catalog_path.is_file():
            return self._json({"error": "catalog not found"}, 404)
        try:
            catalog = json.loads(catalog_path.read_text())
        except Exception as e:
            return self._json({"error": f"catalog parse failed: {e}"}, 500)
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

    def _get_catalog(self):
        """GET /api/catalog — return the curated module catalog with installed annotations.

        Each installed module is additionally checked for venv-vs-workspace.yaml
        drift: if the declared Python package is not importable in the workspace
        venv, the module is flagged ``out_of_sync: true`` with a short reason.
        """
        catalog_path = WORKSPACE / "scripts" / "_catalog" / "modules.json"
        if not catalog_path.exists():
            return self._json({"modules": [], "error": "catalog not found"}, 200)
        try:
            modules = json.loads(catalog_path.read_text())
            # Annotate with installed status from workspace.yaml imports.
            ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
            imports = ws_data.get("imports", {}) or {}
            for m in modules:
                m["installed"] = m["name"] in imports
                if m["installed"]:
                    # Merge live workspace.yaml entry fields (source/ref/path/install_path/package)
                    # into the catalog item so the UI has authoritative install metadata.
                    imp = imports.get(m["name"], {}) or {}
                    for k in ("source", "ref", "path", "install_path", "package"):
                        v = imp.get(k)
                        if v is not None:
                            m[k] = v
                    # Sync check: is the Python package actually importable?
                    pkg_name = m.get("package") or m["name"].replace("-", "_")
                    sync_reason = _check_installed_module_sync(
                        pkg_name, m.get("install_path")
                    )
                    if sync_reason:
                        m["out_of_sync"] = True
                        m["out_of_sync_reason"] = sync_reason
            return self._json({"modules": modules}, 200)
        except Exception as e:
            return self._json({"modules": [], "error": str(e)}, 500)

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

        # Load catalog entry.
        catalog_path = WORKSPACE / "scripts" / "_catalog" / "modules.json"
        if not catalog_path.exists():
            return self._json({"error": "catalog not found"}, 404)
        try:
            modules = json.loads(catalog_path.read_text())
        except Exception as e:
            return self._json({"error": f"catalog parse failed: {e}"}, 500)
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
                    log_dir = WORKSPACE / ".pbg"
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
                    log_dir = WORKSPACE / ".pbg"
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
            return self._json({"ok": True, "already_uninstalled": True}, 200)

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
            data = yaml.safe_load((root / "workspace.yaml").read_text()) or {}
            return data.get("name") or root.name
        except Exception:
            return root.name

    def _serve_state(self):
        ws_file = WORKSPACE / "workspace.yaml"
        if not ws_file.exists():
            self.send_response(404)
            self.end_headers()
            return
        ws = yaml.safe_load(ws_file.read_text())
        body = json.dumps(ws).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_guidance(self):
        content_dir = WORKSPACE / ".pbg" / "server" / "content"
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
                    text = ws_file.read_text()
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
    _ws_add_to_sys_path()
    # Register the active workspace root for ``vivarium_dashboard.lib`` helpers
    # that used to walk up from __file__.
    from vivarium_dashboard.lib._root import set_workspace_root
    set_workspace_root(WORKSPACE)

    # Repair runs left 'running' by a previous crash/restart: a dead or
    # missing PID becomes 'orphaned'; a live PID is left to keep running.
    try:
        from vivarium_dashboard.lib.run_registry import reconcile_stale_runs
        n = reconcile_stale_runs(WORKSPACE / ".pbg" / "composite-runs.db")
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
    info_dir = WORKSPACE / ".pbg" / "server"
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
