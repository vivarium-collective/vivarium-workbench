"""Investigation visualization POST mutation builders.

Pure builders for the two investigation-viz endpoints that the FastAPI app
calls directly:

    (ws_root: Path, body: dict) -> tuple[dict, int]

Routes covered:
  - POST /api/investigation-add-viz    → ``add_viz``:
      append a visualization entry to the study's ``spec.yaml``
      ``visualizations`` list. The git-committing legacy server keeps its
      ``_active_branch_action`` wrapper + post-wrapper augmentation VERBATIM,
      delegating ONLY the file write to the private ``_apply_add_viz`` helper
      (which raises ``RuntimeError`` on a duplicate name — so the live path
      collapses to its existing 500 — while the public ``add_viz`` pre-checks
      and returns a precise 409).
  - POST /api/investigation-render-viz → ``render_viz``:
      re-render the study's declared visualizations against its existing
      emitter data (NO simulation re-run). This route has NO commit wrapper —
      a plain no-commit extract: the WHOLE handler logic (workspace core build,
      pbg_superpowers viz-class registry augmentation, the ``build_and_run``
      ``process_bigraph.Composite`` runner and ``render_visualizations``) moves
      here, and the server shim becomes a thin lib delegate.

File / render side-effects only — no HTTP, no server imports, no git
operations.

Batch 28 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from vivarium_dashboard.lib import study_spec as _study_spec
from vivarium_dashboard.lib.upload_mutations import _ws_add_to_sys_path


# ---------------------------------------------------------------------------
# add_viz  (POST /api/investigation-add-viz)
# ---------------------------------------------------------------------------


def add_viz(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-add-viz {investigation, name, address, config}.

    Append a visualization entry to the study's ``spec.yaml``
    ``visualizations`` list.

    Returns (mirroring the live post-wrapper augmentation):
      200  {ok: True, investigation: <inv>, viz_name: <name>}
      400  validation (missing fields / viz-name regex)
      404  investigation not found
      409  a visualization with that name already exists
    """
    inv = (body.get("investigation") or "").strip()
    viz_name = (body.get("name") or "").strip()
    address = (body.get("address") or "").strip()
    viz_config = body.get("config") or {}

    if not inv or not viz_name or not address:
        return {"error": "investigation, name, address required"}, 400
    if not re.match(r"^[a-zA-Z0-9_-]+$", viz_name):
        return {"error": "viz name must match [a-zA-Z0-9_-]+"}, 400

    spec_path = _study_spec.study_spec_path(ws_root, inv)
    if not spec_path.is_file():
        return {"error": f"investigation '{inv}' not found"}, 404

    # Pre-check the duplicate so the FastAPI path returns the precise 409 (the
    # live path's bare RuntimeError in ``_apply_add_viz`` collapses to a 500 via
    # ``_active_branch_action`` — both carry the SAME message).
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    vizzes = spec.get("visualizations") or []
    if any(v.get("name") == viz_name for v in vizzes):
        return {"error": f"visualization '{viz_name}' already exists in spec"}, 409

    _apply_add_viz(
        ws_root,
        spec_path=spec_path,
        viz_name=viz_name,
        address=address,
        viz_config=viz_config,
    )
    return {"ok": True, "investigation": inv, "viz_name": viz_name}, 200


def _apply_add_viz(
    ws_root: Path,
    *,
    spec_path: Path,
    viz_name: str,
    address: str,
    viz_config: Any,
) -> None:
    """File write for the add-viz flow (formerly the action() closure).

    Raises ``RuntimeError`` if a visualization with ``viz_name`` already
    exists — byte-identical to the legacy ``action()`` body, so the live
    ``_active_branch_action`` path surfaces it as a 500 exactly as before.
    """
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    vizzes = spec.setdefault("visualizations", []) or []
    if any(v.get("name") == viz_name for v in vizzes):
        raise RuntimeError(f"visualization '{viz_name}' already exists in spec")
    vizzes.append({"name": viz_name, "address": address, "config": viz_config})
    spec["visualizations"] = vizzes
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# render_viz  (POST /api/investigation-render-viz)  — no commit wrapper
# ---------------------------------------------------------------------------


def render_viz(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-render-viz {name}.

    Re-render the study's declared visualizations against its existing emitter
    data. No simulation re-run. Builds the workspace ``<pkg>.core``, augments
    the link registry with pbg_superpowers viz classes, and runs each viz doc
    through a ``process_bigraph.Composite`` via ``build_and_run``.

    Returns:
      200  {ok: True, investigation, n_visualizations, viz_paths}
      400  validation (name required) / spec error
      404  investigation not found
      500  build-core failure / render failure
    """
    _ws_add_to_sys_path(ws_root)
    from vivarium_dashboard.lib.investigations import (
        load_spec, render_visualizations, InvestigationSpecError,
    )

    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    inv_dir = _study_spec.study_dir(ws_root, name)
    spec_path = _study_spec.study_spec_path(ws_root, name)
    if not spec_path.is_file():
        return {"error": f"investigation '{name}' not found"}, 404
    try:
        spec = load_spec(spec_path)
    except InvestigationSpecError as e:
        return {"error": f"spec error: {e}"}, 400

    # Discover workspace package + build core (mirror _post_investigation_run)
    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
    sys.path.insert(0, str(ws_root))
    try:
        core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
        core = core_module.build_core()
        registry = dict(core.link_registry)
    except Exception as e:  # noqa: BLE001
        return {"error": f"failed to build core: {e}"}, 500

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
    except Exception as e:  # noqa: BLE001
        return {"error": f"render failed: {type(e).__name__}: {e}"}, 500

    return {
        "ok": True, "investigation": name,
        "n_visualizations": len(viz_paths),
        "viz_paths": [str(p) for p in viz_paths],
    }, 200
