"""Shared results driver: run declared analyses + viz + report card for a run.

Intended for BOTH the study flush (Task 4) and the composite completion seam
(Task 5) so studies and composite runs emit an identical results contract.
``spec`` is any study-shaped dict -- a real ``study.yaml`` or the ephemeral
single-composite spec (``vivarium_workbench.lib.ephemeral_study``) -- read
only for its ``analyses``/``visualizations`` entries.

Status: this module's ``run_declared_results`` is not yet called from any
production path. The study completion path calls ``run_study_analyses``
(``study_run_post.py``) directly; the live composite completion seam uses
its own sweep-dir-scoped ``composite_flush._dispatch_analyses`` /
``_render_analysis``; the remote (GovCloud) path injects
``analysis_options`` directly into the dispatch. All three currently reach
the same env-worker ``run_study_analyses`` capability without routing
through this function -- a deliberate outcome of the Task 4 descope and
Task 5 reshape, not an oversight. This module is retained as the intended
shared entry point (not dead code): a future unification pass should route
the study and composite seams through ``run_declared_results`` instead of
their separate call sites.
"""
from __future__ import annotations

import json
from pathlib import Path

from vivarium_workbench.lib.study_run_post import (
    run_study_analyses, render_study_visualizations,
)


def run_declared_results(run_dir, spec: dict, *, ws_root, run_id: str,
                          spec_id: str | None = None) -> dict:
    """Run a run's declared analyses + visualizations + report card.

    Writes ``analyses.json``, ``report.html``, and viz files under
    ``run_dir``. Never raises: every stage is best-effort and any failure is
    captured into the returned ``errors`` list, which also determines
    ``status`` (``"PARTIAL"`` if non-empty, else ``"OK"``).

    An empty spec (no ``analyses`` and no ``visualizations`` declared) is a
    pure no-op: nothing is written to disk.

    Returns ``{"status": "OK"|"PARTIAL", "analyses": <path or None>,
    "report": <path or None>, "viz": [<names>], "errors": [...]}``.
    """
    run_dir = Path(run_dir)
    ws_root = Path(ws_root)
    analyses_entries = list(spec.get("analyses") or [])
    viz_entries = list(spec.get("visualizations") or [])

    if not analyses_entries and not viz_entries:
        return {"status": "OK", "analyses": None, "report": None,
                "viz": [], "errors": []}

    errors: list[dict] = []

    analyses_path = None
    if analyses_entries:
        written, ana_errors = run_study_analyses(run_dir, spec, run_id, ws_root)
        errors.extend(ana_errors)
        # analyses.json must be a JSON LIST -- it mirrors the shape the other
        # two writers of this same file use (composite_flush.run_flush's
        # per-analysis dicts; remote_run_landing.fold_analyses's
        # {"name","written","errors"} entries), because the real consumer
        # (composite_run_views.py) derives has_analyses from
        # `content not in ("", "[]")`. A dict is never "[]", so it would
        # false-positive has_analyses=True even when nothing was produced.
        # Only emit a non-empty list when analyses actually wrote output --
        # a pure-failure run (no written files, even with errors) must still
        # read as has_analyses=False.
        if written:
            names = [e.get("name") for e in analyses_entries if isinstance(e, dict)]
            entries = [{"name": ", ".join(str(n) for n in names if n) or None,
                       "written": written, "errors": ana_errors}]
        else:
            entries = []
        try:
            out_path = run_dir / "analyses.json"
            out_path.write_text(json.dumps(entries, default=str), encoding="utf-8")
            analyses_path = str(out_path)
        except Exception as exc:  # noqa: BLE001 — best-effort, never raise
            errors.append({"error": f"failed to write analyses.json: "
                                     f"{type(exc).__name__}: {exc}"})

    viz_names: list = []
    if viz_entries:
        viz_files, viz_errors = render_study_visualizations(
            ws_root, run_dir, spec, spec_id)
        viz_names = viz_files
        errors.extend(viz_errors)

    report_path = None
    try:
        # render_report_card lives in composite_flush.py, which (Task 5) will
        # import run_declared_results from this module -- import lazily here
        # (inside this try, not at module top) so the two modules never form
        # a load-time cycle AND so an import failure degrades to an error
        # entry + PARTIAL rather than raising out of a function documented
        # as never raising.
        from vivarium_workbench.lib.composite_flush import render_report_card
        html = render_report_card(req=None, viz_names=viz_names,
                                   analyses=analyses_entries)
        out_path = run_dir / "report.html"
        out_path.write_text(html, encoding="utf-8")
        report_path = str(out_path)
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise
        errors.append({"error": f"failed to render report card: "
                                 f"{type(exc).__name__}: {exc}"})

    status = "PARTIAL" if errors else "OK"
    return {"status": status, "analyses": analyses_path, "report": report_path,
            "viz": viz_names, "errors": errors}
