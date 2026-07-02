"""Workspace/system-info read-only builders extracted from server.py.

These are ``ws_root``-parameterised pure functions that back the 4 "best-effort,
never-500" GET routes:

  build_framework_metrics  → GET /api/framework-metrics
  build_github_repo        → GET /api/github-repo
  build_ui_config          → GET /api/ui-config
  build_workspace_home     → GET /api/workspace

Each function returns a plain ``dict`` (the FastAPI route returns it via a typed
model; the legacy server.py shim wraps it into ``(json_bytes, status)`` via
``_json_body``).  All errors are swallowed and degrade to a typed empty-default
body — callers must never 500 on these routes.

The default PTools Omics Viewer URL template is single-sourced here so both the
FastAPI route and the legacy server.py handler share the same constant.
"""

from __future__ import annotations

import re
import yaml
from pathlib import Path

from vivarium_workbench.lib.workspace_paths import WorkspacePaths
from vivarium_workbench.lib.registry import _dashboard_config

# ---------------------------------------------------------------------------
# Single-sourced constant: default PTools Omics Viewer URL template
# ---------------------------------------------------------------------------

# NOTE: the default targets the Omics Viewer auto-load endpoint
# (omics=t&url=…&class=…&column1=…), verified against sms-ptools 0.8.2.
# Override via ui.ptools_omics_url_template in workspace.yaml if your PTools
# build differs.  Placeholders: {server},{orgid},{tsv_url},{cls},{columns}.
_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE = (
    "{server}/overviewsWeb/celOv.shtml"
    "?omics=t&url={tsv_url}&orgid={orgid}&class={cls}&column1={columns}"
)


# ---------------------------------------------------------------------------
# build_framework_metrics
# ---------------------------------------------------------------------------

def build_framework_metrics(ws_root: Path) -> dict:
    """Return the framework-metrics payload dict for GET /api/framework-metrics.

    Aggregates framework-self metrics across EVERY study + every investigation
    in the workspace via the deterministic
    ``pbg_superpowers.rigor.framework_metrics`` (each metric is
    ``{fraction, count, total}``).

    AI-free + tolerant: an absent/old pbg_superpowers, or an unreadable
    workspace, returns ``{metrics: {}, n_investigations: int, n_studies: int}``
    rather than raising, so the dashboard degrades gracefully.
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

    base: dict = {
        "metrics": {},
        "n_investigations": len(inv_specs),
        "n_studies": len(study_specs),
    }
    try:
        from pbg_superpowers.rigor import framework_metrics
    except Exception:  # noqa: BLE001 — older pbg_superpowers lacks the function
        return base
    try:
        metrics = framework_metrics(study_specs, inv_specs) or {}
        base["metrics"] = metrics
    except Exception:  # noqa: BLE001 — compute can fail; stay typed + return defaults
        pass
    return base


# ---------------------------------------------------------------------------
# build_github_repo
# ---------------------------------------------------------------------------

def build_github_repo(ws_root: Path) -> dict:
    """Return ``{repo: "owner/name"}`` or ``{repo: null}`` for GET /api/github-repo.

    Resolution order (first hit wins):
      1. ``git remote get-url origin`` parsed for github.com.
      2. workspace.yaml ``dashboard.github_repo`` / ``dashboard.repository``.

    Best-effort: never raises; returns ``{repo: null}`` on any failure.
    """
    ws_root = Path(ws_root)
    repo = None

    # 1. Try git remote (authoritative live checkout).
    try:
        from vivarium_workbench.lib.report import _detect_github_repo
        repo = _detect_github_repo(ws_root)
    except Exception:  # noqa: BLE001
        repo = None

    # 2. Fall back to workspace.yaml dashboard.* config.
    if not repo:
        try:
            ws_data = yaml.safe_load(
                (ws_root / "workspace.yaml").read_text(encoding="utf-8")
            ) or {}
            dash = _dashboard_config(ws_data)
            cand = dash.get("github_repo") or dash.get("repository")
            if isinstance(cand, str) and cand.strip():
                cand = cand.strip()
                m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", cand)
                repo = m.group(1) if m else cand.replace(".git", "").strip("/")
        except Exception:  # noqa: BLE001
            repo = None

    return {"repo": repo or None}


# ---------------------------------------------------------------------------
# build_ui_config
# ---------------------------------------------------------------------------

def build_ui_config(ws_root: Path) -> dict:
    """Return the UI feature-flags dict for GET /api/ui-config.

    Reads workspace.yaml's ``ui:`` block.  Missing or unreadable workspace →
    all-default values (never raises).

    Keys returned:
      composite_view          — default "bigraph-loom"
      ptools_server_url       — default ""
      ptools_omics_url_template — default ``_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE``
    """
    ws_root = Path(ws_root)
    try:
        ws = yaml.safe_load(
            (ws_root / "workspace.yaml").read_text(encoding="utf-8")
        ) or {}
    except Exception:  # noqa: BLE001
        ws = {}
    ui = ws.get("ui") or {}
    import os
    readonly = os.environ.get("VIVARIUM_DASHBOARD_READONLY", "").strip().lower() \
        not in ("", "0", "false", "no")
    return {
        "readonly": readonly,
        "composite_view": ui.get("composite_view", "bigraph-loom"),
        "ptools_server_url": ui.get("ptools_server_url", ""),
        "ptools_omics_url_template": ui.get(
            "ptools_omics_url_template",
            _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        ),
    }


# ---------------------------------------------------------------------------
# build_workspace_home
# ---------------------------------------------------------------------------

def build_workspace_home(ws_root: Path) -> dict:
    """Return workspace narrative metadata for GET /api/workspace and publish.

    Reads workspace.yaml + enumerates investigation dirs.  Pure (no socket I/O).

    Returned dict shape: ``{name, description, imports, investigations: [...]}``.
    """
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    ws: dict = {}
    wf = ws_root / "workspace.yaml"
    if wf.exists():
        try:
            ws = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001
                investigations.append({"name": inv_dir.name, "status": "error"})

    return {
        "name":           ws.get("name", ws_root.name),
        "description":    ws.get("description", ""),
        "imports":        ws.get("imports") or {},
        "investigations": investigations,
    }
