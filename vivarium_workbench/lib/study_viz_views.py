"""Study-spec + visualization lifecycle builders (library seam).

HTTP-free builders behind three dashboard routes:

  * ``GET /api/study-bigraph-paths``     → :func:`build_study_bigraph_paths`
  * ``GET /api/visualization-status``    → :func:`build_visualization_status`
  * ``GET /api/visualization-instances`` → :func:`build_visualization_instances`

Pure ``ws_root``-parameterised functions: NO ``import server`` (the stdlib
``vivarium_workbench.server`` keeps thin shims that delegate here, passing the
``WORKSPACE`` global).  The FastAPI app imports this module directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml

from vivarium_workbench.lib.study_spec import study_dir as _study_dir_fn
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# ---------------------------------------------------------------------------
# study_refresh_viz — re-render a study's declared visualizations
# ---------------------------------------------------------------------------

def study_refresh_viz(ws_root: Path, name: str) -> dict:
    """Re-render every ``visualizations[]`` entry of study ``name`` against its
    latest run, stamping provenance (pure, unit-testable seam).

    Mirrors ``server._study_refresh_viz``: resolves the study dir
    (layout-aware), loads ``study.yaml``, finds the latest run row, and
    delegates to the vendored :func:`refresh_study_viz` (which swallows
    per-chart render errors and returns ``status="error"`` entries, so this
    never raises on a bad render).

    Returns ``{"study": name, "results": [...]}`` or
    ``{"error": ..., "not_found": True}`` when the study does not exist (the
    HTTP wrapper maps that to 404).
    """
    from vivarium_workbench.lib.refresh_viz import refresh_study_viz
    from vivarium_workbench.lib.study_charts import latest_run_row

    study_dir = WorkspacePaths.load(ws_root).studies / name
    if not study_dir.is_dir():
        return {"error": f"study {name!r} not found", "not_found": True}
    spec_path = study_dir / "study.yaml"
    spec: dict = {}
    if spec_path.is_file():
        try:
            loaded = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                spec = loaded
        except Exception:
            spec = {}
    latest = latest_run_row(study_dir / "runs.db")
    results = refresh_study_viz(study_dir, spec, latest)
    return {"study": name, "results": results}


# ---------------------------------------------------------------------------
# Bigraph path cache (keyed by (abs_source_file, mtime, max_depth))
# ---------------------------------------------------------------------------
_BIGRAPH_PATH_CACHE: dict = {}


# ---------------------------------------------------------------------------
# build_study_bigraph_paths
# ---------------------------------------------------------------------------

def build_study_bigraph_paths(
    ws_root: Path,
    slug: str,
    baseline_name: str = "",
    max_depth: int = 8,
) -> tuple[dict, int]:
    """Return ``(body, status)`` for ``GET /api/study-bigraph-paths``.

    Mirrors ``server.Handler._get_study_bigraph_paths`` exactly:
      - 400  no slug / no baseline entries
      - 404  no study spec / baseline not found / no serialized state
      - 500  spec parse failure
      - 200  ``{composite, source_file, max_depth, node_count, nodes:[...]}``
    """
    ws_root = Path(ws_root)
    slug = slug.strip()
    if not slug:
        return {"error": "study slug required (?study=<slug>)"}, 400

    sd = _study_dir_fn(ws_root, slug)
    spec_path = sd / "study.yaml"
    if not spec_path.is_file():
        spec_path = sd / "spec.yaml"
    if not spec_path.is_file():
        return {"error": f"no study.yaml or spec.yaml at {sd}"}, 404
    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return {"error": f"failed to parse study spec: {e}"}, 500

    baselines = spec.get("baseline") or []
    if not baselines:
        return {"error": "study has no baseline entries"}, 400
    if baseline_name:
        chosen = next((b for b in baselines if b.get("name") == baseline_name), None)
        if chosen is None:
            return (
                {"error": f"baseline {baseline_name!r} not found in study {slug!r}"}, 404,
            )
    else:
        chosen = baselines[0]

    composite_ref = chosen.get("composite") or ""
    basename = composite_ref.rsplit(".", 1)[-1] if composite_ref else ""

    candidates = [
        ws_root / "models" / f"{basename}.pbg",
        ws_root / "models" / f"{basename}.json",
    ]
    # v2ecoli legacy: the "baseline" composite is serialized as "partitioned".
    if basename == "baseline":
        candidates.append(ws_root / "models" / "partitioned.pbg")
    source_file = next((p for p in candidates if p.is_file()), None)
    if source_file is None:
        return {
            "error":     "no serialized composite state found",
            "composite": composite_ref,
            "looked_in": [str(p) for p in candidates],
            "hint": (
                "run the baseline to populate <workspace>/models/<composite>.pbg,"
                " or commit a snapshot."
            ),
        }, 404

    mtime = source_file.stat().st_mtime
    cache_key = (str(source_file), mtime, max_depth)
    nodes = _BIGRAPH_PATH_CACHE.get(cache_key)
    if nodes is None:
        from vivarium_workbench.lib.composite_recipes import walk_state_snapshot
        try:
            doc = json.loads(source_file.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": f"failed to parse {source_file.name}: {e}"}, 500
        nodes = walk_state_snapshot(doc, max_depth=max_depth)
        if len(_BIGRAPH_PATH_CACHE) > 8:
            _BIGRAPH_PATH_CACHE.clear()
        _BIGRAPH_PATH_CACHE[cache_key] = nodes

    source_display = (
        str(source_file.relative_to(ws_root))
        if str(source_file).startswith(str(ws_root))
        else str(source_file)
    )
    return {
        "composite":   composite_ref,
        "source_file": source_display,
        "max_depth":   max_depth,
        "node_count":  len(nodes),
        "nodes":       nodes,
    }, 200


# ---------------------------------------------------------------------------
# build_visualization_status
# ---------------------------------------------------------------------------

def build_visualization_status(
    ws_root: Path,
    name: str,
) -> tuple[dict, int]:
    """Return ``(body, 200)`` for ``GET /api/visualization-status``.

    Lifecycle ordering (committed > added > created > requested > described)
    mirrors ``server.Handler._get_visualization_status`` exactly.

    Special cases:
      - 400  empty name
      - 200  ``{status: "missing", name}`` when not in workspace.yaml
      - 200  full ``{status, name, has_request, has_response, has_staged, has_committed}``
    """
    ws_root = Path(ws_root)
    if not name:
        return {"error": "missing name"}, 400

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    viz = next(
        (v for v in (ws_data.get("visualizations") or []) if v.get("name") == name),
        None,
    )
    if not viz:
        return {"status": "missing", "name": name}, 200

    pkg = ws_data.get("package_path") or (
        "pbg_" + ws_data.get("name", "").replace("-", "_")
    )
    wp = WorkspacePaths.load(ws_root)
    response_path = wp.pbg / "viz-responses" / f"{name}.py"
    staged_path = wp.pbg / "visualizations-staged" / f"{name}.py"
    committed_path = ws_root / pkg / "visualizations" / f"{name}.py"
    request_path = wp.pbg / "viz-requests" / f"{name}.md"

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

    return {
        "status": status,
        "name": name,
        "has_request": request_path.exists(),
        "has_response": response_path.exists(),
        "has_staged": staged_path.exists(),
        "has_committed": committed_path.exists(),
    }, 200


# ---------------------------------------------------------------------------
# build_visualization_instances
# ---------------------------------------------------------------------------

def build_visualization_instances(ws_root: Path) -> dict:
    """Return ``{instances: [...]}`` for ``GET /api/visualization-instances``.

    Lists class-backed viz entries from ``workspace.yaml.visualizations``
    (entries that have a ``class:`` key).  Always returns ``{instances: []}``
    on read failure (tolerant — never raises).

    Mirrors ``server.Handler._get_visualization_instances`` exactly.
    """
    ws_root = Path(ws_root)
    try:
        ws_data = yaml.safe_load(
            (ws_root / "workspace.yaml").read_text(encoding="utf-8")
        )
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
            "name":        entry.get("name"),
            "class":       cls,
            "address":     f"local:{cls}",
            "config":      entry.get("config") or {},
            "description": entry.get("description") or "",
        })
    return {"instances": out}
