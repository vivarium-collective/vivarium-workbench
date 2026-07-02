"""Visualization commit POST mutation builders.

Pure builders for the three visualization commit endpoints:

    (ws_root: Path, body: dict) -> tuple[dict, int]

File side-effects only — no HTTP, no server imports, no git operations.

Routes covered:
  - POST /api/observable                   → add observable entry to workspace.yaml
  - POST /api/visualization                → add visualization entry to workspace.yaml
  - POST /api/visualization-commit-batch   → move staged viz files to workspace pkg

These are ``_active_branch_action``-wrapped routes; the lib builder performs
validation + mutation (no git).  The server keeps ``_active_branch_action`` and
delegates the mutation to these builders from inside the ``action()`` closure.

Batch 24 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Ensure the workspace root is on ``sys.path`` so its package is importable."""
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


# ---------------------------------------------------------------------------
# observable_add
# ---------------------------------------------------------------------------


def observable_add(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/observable — register an observable in workspace.yaml.

    Body: ``{name, store_path, units?, description?}``

    Returns:
      200  ``{ok: True}``
      400  name and/or store_path missing
      409  observable name already registered
    """
    name = (body.get("name") or "").strip()
    store_path = (body.get("store_path") or "").strip()
    units = (body.get("units") or "").strip() or None
    description = (body.get("description") or "").strip() or None

    if not all([name, store_path]):
        return {"error": "name and store_path are required"}, 400

    _ws_add_to_sys_path(ws_root)
    ws_file = ws_root / "workspace.yaml"
    ws: dict = yaml.safe_load(ws_file.read_text(encoding="utf-8")) or {}
    observables = ws.setdefault("observables", [])
    if observables is None:
        observables = []
        ws["observables"] = observables
    for existing in observables:
        if isinstance(existing, dict) and existing.get("name") == name:
            return {"error": f"observable '{name}' already registered"}, 409
    entry: dict[str, Any] = {"name": name, "store_path": store_path}
    if units:
        entry["units"] = units
    if description:
        entry["description"] = description
    observables.append(entry)
    ws_file.write_text(yaml.safe_dump(ws, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True}, 200


# ---------------------------------------------------------------------------
# visualization_add
# ---------------------------------------------------------------------------


def visualization_add(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/visualization — register a visualization in workspace.yaml.

    Three entry modes (mutually compatible — combine fields as needed):
        description-first: {name, description}  → Create → /pbg-viz skill
        class-backed:      {name, class, config}  → configured instance of
                           a registered Visualization v2 class
        structured legacy: {name, type, observables, config?, simulation?}

    Only ``name`` is required.

    Returns:
      200  ``{ok: True}``
      400  validation failures (name required, regex, type/observables, unknown class)
      409  visualization name already registered
    """
    from vivarium_dashboard.lib.visualization_classes import (
        list_visualization_classes as _list_viz_classes,
    )

    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return {"error": "name must match ^[a-zA-Z0-9_-]+$"}, 400

    description = (body.get("description") or "").strip() or None
    viz_class = (body.get("class") or "").strip() or None
    viz_type = (body.get("type") or "").strip() or None
    obs_list = body.get("observables") or []
    config = body.get("config") or {}
    simulation_name = (body.get("simulation") or "").strip() or None

    if viz_class:
        _ws_add_to_sys_path(ws_root)
        known = {c["name"] for c in _list_viz_classes(ws_root)["classes"]
                 if c.get("kind") != "analysis"}
        if viz_class not in known:
            return (
                {"error": f"class '{viz_class}' is not a registered Visualization. "
                          f"Available: {sorted(known)}"},
                400,
            )

    # Structured path: if type or observables are provided, validate them fully.
    if viz_type or obs_list:
        if not viz_type:
            return {"error": "type is required when observables are specified"}, 400
        if viz_type not in ("time-series", "phase-space", "heatmap", "histogram"):
            return {"error": "type must be one of: time-series, phase-space, heatmap, histogram"}, 400
        if not isinstance(obs_list, list) or not obs_list:
            return {"error": "observables must be a non-empty list"}, 400

    _ws_add_to_sys_path(ws_root)
    ws_file = ws_root / "workspace.yaml"
    ws: dict = yaml.safe_load(ws_file.read_text(encoding="utf-8")) or {}

    # Only validate observable references when structured fields are provided.
    if obs_list:
        registered_obs = {
            o.get("name") for o in (ws.get("observables") or [])
            if isinstance(o, dict)
        }
        missing = [o for o in obs_list if o not in registered_obs]
        if missing:
            return (
                {"error": f"observables not registered: {missing}. "
                          "Register them first via /api/observable."},
                400,
            )

    # Validate simulation reference if provided.
    if simulation_name:
        registered_sims = {
            s.get("name") for s in (ws.get("simulations") or [])
            if isinstance(s, dict)
        }
        if simulation_name not in registered_sims:
            return (
                {"error": f"simulation '{simulation_name}' not registered. "
                          "Register it first via /api/simulation."},
                400,
            )

    visualizations = ws.setdefault("visualizations", [])
    if visualizations is None:
        visualizations = []
        ws["visualizations"] = visualizations
    for existing in visualizations:
        if isinstance(existing, dict) and existing.get("name") == name:
            return {"error": f"visualization '{name}' already registered"}, 409
    entry: dict[str, Any] = {"name": name}
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
    ws_file.write_text(yaml.safe_dump(ws, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True}, 200


# ---------------------------------------------------------------------------
# visualization_commit_batch
# ---------------------------------------------------------------------------


def visualization_commit_batch(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/visualization-commit-batch — move staged visualizations to the
    workspace package.

    Body: ``{names?: list[str]}`` — if omitted, commits all staged.

    Returns:
      200  ``{ok: True, committed: [names]}``
      404  no staged visualizations / no names match
    """
    wp = WorkspacePaths.load(ws_root)
    staged_dir = wp.pbg / "visualizations-staged"
    if not staged_dir.is_dir():
        return {"error": "no staged visualizations"}, 404

    requested = body.get("names")
    available = sorted(p.stem for p in staged_dir.glob("*.py"))
    if requested:
        names = [n for n in requested if n in available]
    else:
        names = available
    if not names:
        return {"error": "no staged visualizations match"}, 404

    ws_data: dict = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
    target_dir = ws_root / pkg / "visualizations"

    target_dir.mkdir(parents=True, exist_ok=True)
    # Ensure __init__.py exists
    init = target_dir / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    for n in names:
        src = staged_dir / f"{n}.py"
        dest = target_dir / f"{n}.py"
        shutil.copy2(src, dest)
        src.unlink()  # remove staged copy

    return {"ok": True, "committed": names}, 200


# ---------------------------------------------------------------------------
# simulation_delete / visualization_delete
# ---------------------------------------------------------------------------


def _delete_named_ws_entry(
    ws_root: Path, section: str, name: str, kind: str
) -> "tuple[dict, int]":
    """Remove the ``{name: <name>}`` entry from ``workspace.yaml[<section>]``.

    Shared helper for :func:`simulation_delete` and :func:`visualization_delete`.
    Relocated from the retired ``server._delete_simulation`` /
    ``server._delete_visualization`` (minus the ``_active_branch_action`` git
    wrapper — the git commit is the caller's concern, deferred to the flip
    batch like the other FastAPI mutation routes).

    Returns:
      200  ``{ok: True}``
      400  name missing
      404  no such entry
    """
    if not name:
        return {"error": "name is required"}, 400

    _ws_add_to_sys_path(ws_root)
    ws_file = ws_root / "workspace.yaml"
    ws: dict = yaml.safe_load(ws_file.read_text(encoding="utf-8")) or {}
    entries = ws.get(section) or []
    kept = [
        e for e in entries
        if not (isinstance(e, dict) and e.get("name") == name)
    ]
    if len(kept) == len(entries):
        return {"error": f"{kind} '{name}' not found"}, 404
    if kept:
        ws[section] = kept
    else:
        ws.pop(section, None)
    ws_file.write_text(
        yaml.safe_dump(ws, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return {"ok": True}, 200


def simulation_delete(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """DELETE /api/simulation — remove a simulation entry from workspace.yaml.

    Body: ``{name}``.  400 when name missing; 404 when not found; 200
    ``{ok: True}`` on success.
    """
    return _delete_named_ws_entry(
        ws_root, "simulations", (body.get("name") or "").strip(), "simulation"
    )


def visualization_delete(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """DELETE /api/visualization — remove a visualization entry from workspace.yaml.

    Body: ``{name}``.  400 when name missing; 404 when not found; 200
    ``{ok: True}`` on success.
    """
    return _delete_named_ws_entry(
        ws_root, "visualizations", (body.get("name") or "").strip(), "visualization"
    )
