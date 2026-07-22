"""Native-analysis gallery for a study's Results tab.

A study's baseline run goes through the same run path as the Composite Explorer
(``run_runner.execute``), which renders the composite's declared
``ParquetAnalysisView`` visualizations (mass fractions, cell mass, replication,
ribosome usage, ...) into ``<run_dir>/viz.json``. The Composite Explorer shows
that ``viz.json`` in its Visualizations tab; the study Results tab uses a
different (SVG) chart path and so never surfaced it.

This module bridges the gap: it locates a study's most recent *completed* run and
returns that run's ``viz.json`` panels, so the study Results tab can embed the
same live figure gallery. No re-rendering — the run already produced the HTML.
"""
from __future__ import annotations

import json
from pathlib import Path

from vivarium_workbench.lib.study_spec import read_runs_db_for_study
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _is_error_panel(html: str) -> bool:
    """A ParquetAnalysisView 'missing prerequisite' / render-error stub, not a
    real figure — dropped so the gallery shows only rendered panels."""
    return (
        "Could not render" in html
        or "Run this composite" in html
        or "Failed to render" in html
        or "not generated yet" in html
    )


def build_study_native_gallery(ws_root: Path, slug: str) -> dict:
    """``{run_id, panels: {name: html}}`` from the study's latest completed run.

    ``panels`` is ordered as ``viz.json`` recorded them (the declared
    visualization order) and excludes error/placeholder stubs. Returns
    ``{"run_id": None, "panels": {}}`` when the study has no completed run or the
    run left no ``viz.json`` (e.g. a legacy run predating the gallery wiring).
    """
    ws_root = Path(ws_root)
    runs = read_runs_db_for_study(ws_root, slug)
    completed = [r for r in runs if (r.get("status") == "completed") and r.get("run_id")]
    if not completed:
        return {"run_id": None, "panels": {}}
    # read_runs_db_for_study returns newest-first.
    run_id = completed[0]["run_id"]
    viz = ws_root / ".pbg" / "runs" / run_id / "viz.json"
    if not viz.is_file():
        return {"run_id": run_id, "panels": {}}
    try:
        data = json.loads(viz.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt viz.json must not 500 the page
        return {"run_id": run_id, "panels": {}}
    panels = {
        k: v for k, v in data.items()
        if isinstance(v, str) and v.strip() and not _is_error_panel(v)
    }
    return {"run_id": run_id, "panels": panels}
