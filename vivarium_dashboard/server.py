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
    "/api/work-create-github-repo": "_post_work_create_github_repo",
    "/api/work-create-pr":     "_post_work_create_pr",
    "/api/work-end":           "_post_work_end",
    "/api/dirty-commit-all":   "_post_dirty_commit_all",
    "/api/catalog-install":    "_post_catalog_install",
    "/api/catalog-uninstall":  "_post_catalog_uninstall",
    "/api/system-deps-install": "_post_system_deps_install",
    "/api/open-window":        "_post_open_window",
    "/api/suggest":            "_post_suggest",
    "/api/composite-test-run": "_post_composite_test_run",
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
    "/api/study-rename":                "_post_study_rename",
    "/api/study-create-from-run":       "_post_study_create_from_run",
    "/api/study-run-baseline":          "_post_study_run_baseline",
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
}
# Inject study-alias routes into the POST route map (same method name as old).
for _old, _new in _POST_STUDY_ALIASES.items():
    if _old in _POST_ROUTE_MAP:
        _POST_ROUTE_MAP[_new] = _POST_ROUTE_MAP[_old]
del _old, _new  # clean up loop variables from module scope


def _get_registry_data(bypass_cache: bool = False) -> dict:
    """Return registry data from build_core() subprocess, with 30s caching.

    Always returns {processes: [...], types: [...]} plus optional 'error' key.
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
        seen_classes[cls_id] = len(processes)
        processes.append({{
            "name": name,
            "address": addr,
            "kind": kind,
            "schema_preview": schema_preview,
            "aliases": [],
        }})
    # Re-sort by name so output is deterministic; promote short names.
    processes.sort(key=lambda p: ('.' in p['name'], p['name']))

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

    print(json.dumps({{"processes": processes, "types": types}}))
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


def _study_spec_path(name: str):
    """Resolve a study's spec file: ``study.yaml`` (v3) or ``spec.yaml`` (legacy)."""
    d = _study_dir(name)
    study_yaml = d / "study.yaml"
    if study_yaml.is_file():
        return study_yaml
    return d / "spec.yaml"


def _study_detail_spec(name: str):
    """Load a study's spec for the GET /studies/<name> detail page.

    Resolves studies/ or investigations/, study.yaml or spec.yaml (via
    _study_spec_path), then runs it through load_spec so legacy v2 specs are
    migrated to the v3 shape the detail template expects. Returns None when no
    spec file exists for the name.
    """
    from vivarium_dashboard.lib.investigations import load_spec
    spec_path = _study_spec_path(name)
    if not spec_path.is_file():
        return None
    return load_spec(spec_path)


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


def _append_study_run(study_dir, run_record: dict) -> None:
    """Append a run record to a Study's study.yaml `runs` list."""
    sf = study_dir / "study.yaml"
    spec = yaml.safe_load(sf.read_text()) or {}
    spec.setdefault("runs", []).append(run_record)
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))


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
        return None, {"error": f"composite {spec_id!r} not in generator registry"}
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
    sf = study_dir / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
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
    response, code = _run_composite_subprocess(
        pkg=pkg, state=state, steps=steps, db_file=db_file,
        run_id=run_id, spec_id=spec_id, label=label, sim_name=label,
        overrides=generator_overrides,
    )
    if code == 200:
        _append_study_run(study_dir, {
            "run_id": run_id, "variant": None, "label": label,
            "status": "completed", "n_steps": steps,
            "composite": entry.get("name"),
        })
    return response, code


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
    sf = study_dir / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return {"error": "study has no baseline composites"}, 400

    variant = next((v for v in (spec.get("variants") or [])
                    if isinstance(v, dict) and v.get("name") == variant_name), None)
    if variant is None:
        return {"error": f"variant {variant_name!r} not found"}, 404

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
    overrides = variant.get("parameter_overrides") or {}
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
    response, code = _run_composite_subprocess(
        pkg=pkg, state=state, steps=steps, db_file=db_file,
        run_id=run_id, spec_id=spec_id, label=variant_name,
        sim_name=variant_name, overrides=generator_overrides,
    )
    if code == 200:
        _append_study_run(study_dir, {
            "run_id": run_id, "variant": variant_name, "label": variant_name,
            "status": "completed", "n_steps": steps,
            "composite": entry.get("name"),
        })
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
    sf = study_dir / "study.yaml"
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
    sf = _study_dir(study) / "study.yaml"
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
    sf = study_dir / "study.yaml"
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
    sf = study_dir / "study.yaml"
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
    sf = study_dir / "study.yaml"
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
    sf = study_dir / "study.yaml"
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
    sf = study_dir / "study.yaml"
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
    sf = study_dir / "study.yaml"
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
    sf = study_dir / "study.yaml"
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
    sf = study_dir / "study.yaml"
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
    sf = _study_dir(study) / "study.yaml"
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


def _render_study_detail_html(name: str, spec: dict) -> str:
    """Render study-detail.html via Jinja2."""
    import jinja2
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    tpl = env.get_template("study-detail.html")
    return tpl.render(study=spec, name=name)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _is_generated_path(path: str) -> bool:
    """True if `path` is a generated report file (the dashboard rebuilds these
    on every page load, so they're chronically dirty and shouldn't block actions)
    or a large untracked artifact directory (out/ — the ~175 MB ParCa cache —
    which must never block actions and must never be committed).
    """
    return path.startswith("reports/") or path.startswith("out/") or path == "out/"


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
                              label, overrides=None, sim_name=None, timeout=120):
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

    state = cr.inject_sqlite_emitter(state, run_id=run_id, db_file=db_file)

    py = sys.executable
    script = textwrap.dedent(f"""
        import json, sys, traceback
        try:
            from {pkg}.core import build_core
            from process_bigraph import Composite, gather_emitter_results
            from process_bigraph.emitter import SQLiteEmitter
            core = build_core()
            core.register_link('SQLiteEmitter', SQLiteEmitter)
            composite = Composite({{'state': __import__('json').loads({json.dumps(json.dumps(state, default=_json_default))})}}, core=core)
            composite.run({steps})
            results = gather_emitter_results(composite)
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
            print('@@@RESULTS@@@')
            print(json.dumps({{'results': out, 'viz_html': viz_html}}, default=str))
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

        out = result.stdout
        if "@@@ERROR@@@" in out:
            cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
            tb = out.split("@@@ERROR@@@", 1)[1].strip()
            return ({"simulation_id": run_id, "error": "run failed",
                     "traceback": tb}, 502)

        try:
            payload = json.loads(out.split("@@@RESULTS@@@", 1)[1].strip())
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
    from vivarium_dashboard.lib.work_state import load_state, save_state
    state = load_state()
    branch = state.get("active_branch")
    if not branch:
        return {"error": "no active workstream — click Start workstream at the top of the dashboard"}, 409

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

        # Study Detail page: /studies/<name> → render study-detail.html
        if self.path.startswith("/studies/"):
            return self._get_study_detail_page()

        # Strip query string for route matching (self.path includes ?focus=...).
        path_only = self.path.split("?", 1)[0]
        if path_only in ("/", "/index.html"):
            return self._serve_file(WORKSPACE / "reports" / "index.html", "text/html")
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
        from vivarium_dashboard.lib.work_state import load_state, save_state
        state = load_state()
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

    def _post_work_create_github_repo(self, body: dict):
        """gh repo create + set origin + initial push, in one shot.

        Body: {visibility?: "public"|"private", name?: str, description?: str}.
        Defaults: visibility=private, name=<workspace_name>, description=workspace.yaml.description.
        """
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import load_state, save_state
        state = load_state()
        branch = state.get("active_branch")
        if not branch:
            return self._json({"error": "no active workstream — Start one first so the initial push has commits"}, 409)

        if not shutil.which("gh"):
            return self._json({
                "error": "gh CLI not installed",
                "diagnosis": {
                    "category": "gh_missing",
                    "summary": "GitHub CLI (`gh`) is not installed.",
                    "suggestion": "Install gh (`brew install gh` on macOS), then run `gh auth login`. After that, click Create GitHub repo again.",
                },
            }, 500)

        # Verify gh is authenticated
        auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
        if auth.returncode != 0:
            return self._json({
                "error": "gh not authenticated",
                "diagnosis": {
                    "category": "gh_auth",
                    "summary": "GitHub CLI isn't logged in.",
                    "suggestion": "Run `gh auth login` in your terminal, then click Create GitHub repo again.",
                },
            }, 500)

        if _has_origin_remote():
            return self._json({"error": "origin remote already configured — use Push instead"}, 409)

        ws_data = yaml.safe_load((WORKSPACE / "workspace.yaml").read_text())
        default_name = ws_data.get("name", WORKSPACE.name)
        repo_name = (body.get("name") or "").strip() or default_name
        if not re.match(r"^[A-Za-z0-9._-]+$", repo_name):
            return self._json({"error": "invalid repo name (must match [A-Za-z0-9._-]+)"}, 400)
        visibility = (body.get("visibility") or "private").strip().lower()
        if visibility not in ("public", "private", "internal"):
            return self._json({"error": "visibility must be one of: public, private, internal"}, 400)
        description = (body.get("description") or "").strip()
        if not description:
            description = ws_data.get("description") or f"Process-bigraph workspace: {repo_name}"

        # gh repo create <name> --<visibility> --source=. --remote=origin --push --description "..."
        # NOTE: --push pushes the current branch to the new remote.
        cmd = [
            "gh", "repo", "create", repo_name,
            "--" + visibility,
            "--source=.",
            "--remote=origin",
            "--push",
            "--description", description,
        ]
        r = subprocess.run(cmd, cwd=WORKSPACE, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return self._json({
                "error": "gh repo create failed",
                "log": (r.stderr or r.stdout).strip()[-500:],
            }, 500)

        # Successful: gh pushed the current branch. Mark workstream pushed.
        state["pushed"] = True
        save_state(state)

        url = r.stdout.strip().splitlines()[-1] if r.stdout else ""
        return self._json({
            "ok": True,
            "repo_url": url,
            "visibility": visibility,
            "branch": branch,
        }, 200)

    def _post_work_create_pr(self, body: dict):
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import load_state, save_state
        state = load_state()
        branch = state.get("active_branch")
        if not branch:
            return self._json({"error": "no active workstream"}, 409)
        if not state.get("pushed"):
            return self._json({"error": "push to origin first (Push button)"}, 409)
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

        r = subprocess.run(
            ["gh", "pr", "create", "--base", base, "--head", branch,
             "--title", title, "--body", body_text],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=30,
        )
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

    def _post_dirty_commit_all(self, body: dict):
        """Stage and commit all dirty files (minus reports/) under the active workstream."""
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.work_state import load_state
        state = load_state()
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
        """GET /api/investigations — return summaries of all investigations."""
        _ws_add_to_sys_path()
        from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError

        out = []
        for d in _iter_study_dirs():
            spec_path = d / "study.yaml" if (d / "study.yaml").is_file() else d / "spec.yaml"
            if not spec_path.is_file():
                continue
            try:
                spec = load_spec(spec_path)
                # Multi-composite (new) vs single-`composite:` (legacy) shape.
                composites = spec.get("composites") or []
                if composites:
                    composite_summary = ", ".join(c.get("name", "") for c in composites)
                    n_runs = len(spec.get("runs") or [])
                else:
                    composite_summary = spec.get("composite", "")
                    # v3 uses `runs:`; legacy uses `simulations:`.
                    n_runs = len(spec.get("runs") or spec.get("simulations") or [])
                row = {
                    "name":            spec["name"],
                    "composite":       composite_summary,
                    "composites":      composites,
                    "description":     spec.get("description", ""),
                    "topic":           spec.get("topic", ""),
                    "tags":            spec.get("tags") or [],
                    "status":          spec.get("status", "planned"),
                    "last_run":        spec.get("last_run"),
                    "n_simulations":   n_runs,
                    "baseline_names":  [b.get("name", "") for b in (spec.get("baseline") or [])
                                        if isinstance(b, dict)],
                    "n_baseline":      len(spec.get("baseline") or []),
                    "n_variants":      len(spec.get("variants") or []),
                    "n_groups":        len(spec.get("groups") or []),
                    "n_interventions": len(spec.get("interventions") or []),
                    "n_comparisons":   len(spec.get("comparisons") or []),
                    "n_runs":          n_runs,
                    "baseline_source": _format_baseline_source(spec),
                    "conclusions_excerpt": _conclusions_excerpt(spec),
                }
                out.append(row)
            except InvestigationSpecError as e:
                out.append({
                    "name": d.name, "status": "invalid", "error": str(e),
                })
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

        # Resolve source composite if provided
        source_path = None
        baseline_name = None
        if source:
            _ws_add_to_sys_path()
            from vivarium_dashboard.lib.investigation_migrate import _resolve_composite_source
            try:
                source_path, baseline_name = _resolve_composite_source(source, WORKSPACE)
            except (FileNotFoundError, ValueError) as e:
                return self._json({"error": f"source composite not found: {e}"}, 404)

        def action():
            import shutil as _shutil
            inv_dir.mkdir(parents=True, exist_ok=False)
            (inv_dir / "data").mkdir()
            (inv_dir / "data" / ".keep").write_text("")

            if source_path and baseline_name:
                # New-shape spec: seed with a baseline composite entry
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
        from vivarium_dashboard.lib.investigation_migrate import _resolve_composite_source
        try:
            source_path, _stem = _resolve_composite_source(source, WORKSPACE)
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

        commit_msg = f"feat(investigations/{inv_name}): add composite '{comp_name}'"

        def do_action():
            import shutil
            shutil.copy2(source_path, sidecar)
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

    def _post_study_run_variant(self, body: dict):
        """POST /api/study-run-variant {study, variant, steps?}"""
        response, code = _post_study_run_variant_for_test(WORKSPACE, body)
        return self._json(response, code)

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
        parts = self.path.strip("/").split("/")
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
            n_runs = len(spec.get("runs") or [])
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
                        cwd=WORKSPACE, capture_output=True, text=True, timeout=180,
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
                        cwd=WORKSPACE, capture_output=True, text=True, timeout=120,
                    )
                    if r.returncode != 0:
                        raise RuntimeError(
                            f"submodule add failed: {(r.stderr or r.stdout)[:300]}"
                        )

                # Step 2: pip install -e.
                try:
                    result = subprocess.run(
                        pip_cmd_base + [str(abs_target)],
                        cwd=WORKSPACE, capture_output=True, text=True, timeout=180,
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
        resp, code = _active_branch_action(commit_msg, action)
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
        resp, code = _active_branch_action(commit_msg, action)
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

def serve(workspace: Path, port: int) -> int:
    """Boot the dashboard HTTP server against ``workspace`` on ``port``.

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

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    # Write server-info so tests and other tools can detect the server is ready.
    info_dir = WORKSPACE / ".pbg" / "server"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "server-info").write_text(json.dumps({
        "port": port,
        "host": "127.0.0.1",
        "url": f"http://127.0.0.1:{port}",
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
    args = ap.parse_args()
    return serve(args.workspace, args.port)


if __name__ == "__main__":
    sys.exit(main() or 0)
