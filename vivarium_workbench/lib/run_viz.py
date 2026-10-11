"""Write the per-run visualization artifact — ``.pbg/runs/<run_id>/viz.json``.

The Runs-tab "📊 Viz" action (``static/sim-table.js``) links to
``/api/composite-run/<run_id>/artifact/viz``, which
:func:`lib.composite_run_views.build_run_artifact` serves by reading
``<ws>/.pbg/runs/<run_id>/viz.json`` — a ``{panel_name: html_string}`` dict
(one panel per chart; each value a standalone HTML string, or a raw SVG
document / PNG data-URI for image-only paper figures).

Two write paths feed that file and must stay consistent:

* the dashboard-launched study-run path (:mod:`lib.study_runs`); and
* the canonical ``sims/run.py`` runners the viva-expert skill scaffolds.

This module is the single framework-aligned primitive both call, so a run
started from either path carries the same figures into the Runs tab. Writes
are atomic (``lib.atomic_io``) and best-effort by convention — callers wrap
the call so a viz mirror never fails the run.
"""
from __future__ import annotations

import json
from pathlib import Path

from vivarium_workbench.lib.atomic_io import atomic_write_text
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# The render stub `run_runner._render_viz` writes when a Visualization step
# raises — never persisted as a "figure" (build_run_artifact would show a
# broken panel). study_runs.py already filters on this same marker.
_STUB_MARKER = "Failed to render"


def write_run_viz(ws_root, run_id: str, panels: dict) -> list[str]:
    """Write ``{name: html}`` panels to ``<ws>/.pbg/runs/<run_id>/viz.json``.

    Panels whose value is not a string, or whose html contains the render-stub
    marker (``"Failed to render"``), are skipped. Writes atomically after
    ``mkdir -p`` of the run directory. Returns the list of panel names actually
    written. When no panel survives the filter nothing is written (no empty
    file), so :func:`lib.composite_run_views.build_run_artifact`'s
    absent-file / zarr-fallback path still applies.
    """
    clean = {
        str(name): html
        for name, html in (panels or {}).items()
        if isinstance(html, str) and _STUB_MARKER not in html
    }
    if not clean:
        return []
    run_dir = WorkspacePaths.load(ws_root).pbg / "runs" / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(run_dir / "viz.json", json.dumps(clean, default=str))
    return list(clean)


def mirror_viz_dir(ws_root, run_id: str, html_paths) -> list[str]:
    """Mirror rendered ``viz/<name>.html`` files into the run's ``viz.json``.

    Convenience for canonical ``sims/run.py`` runners that render interactive
    figures to ``studies/<slug>/viz/<name>.html``: pass the rendered paths and
    this reads each one, keys it by its filename without the ``.html`` suffix
    (matching the study-run path's convention), and delegates to
    :func:`write_run_viz`. Missing / unreadable files are skipped; the first
    file wins on a key collision. Returns the panel names written.
    """
    panels: dict[str, str] = {}
    for p in (html_paths or []):
        p = Path(p)
        if not p.is_file():
            continue
        try:
            html = p.read_text(encoding="utf-8")
        except OSError:
            continue
        key = p.name[:-5] if p.name.endswith(".html") else p.name
        panels.setdefault(key, html)
    return write_run_viz(ws_root, run_id, panels)
