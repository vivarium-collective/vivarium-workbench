"""Generic post-run flush for composite runs: analyses + a report card.

Called by run_runner.execute after visualizations render. Best-effort:
never raises into the run loop; a failure is logged and reflected in the
returned has_* flags."""
from __future__ import annotations

import html as _html
import json
import traceback
from pathlib import Path


def _dispatch_analyses(*, spec_id: str, db_file: str, run_id: str, core) -> list:
    """Render @composite_generator(analyses=[...]) entries over this run's
    emitter output. Returns a list of {name, result} dicts; [] when the
    composite declares no analyses. Mirrors run_runner._render_canonical_viz."""
    try:
        from pbg_superpowers.composite_generator import _REGISTRY, discover_generators
    except ImportError:
        return []
    if not _REGISTRY:
        discover_generators()
    entry = _REGISTRY.get(spec_id)
    analyses = list(getattr(entry, "analyses", []) or []) if entry else []
    if not analyses:
        return []
    out = []
    for a in analyses:
        name = a.get("name") if isinstance(a, dict) else str(a)
        out.append({"name": name, "status": "declared"})
    # NOTE: rendering the analysis composites over gathered_emitter_outputs is
    # the richer follow-up; day-one dispatch records declarations so the UI can
    # list them. Expand here when composites declare real analyses.
    return out


def render_report_card(*, req, viz_names: list, analyses: list) -> str:
    steps = getattr(req, "steps", "?")
    spec_id = getattr(req, "spec_id", "") or ""
    name = spec_id.rsplit(".", 1)[-1] if spec_id else "composite"
    rows = "".join(
        f"<li><code>{_html.escape(str(n))}</code></li>" for n in viz_names
    ) or "<li><em>none</em></li>"
    an = "".join(
        f"<li>{_html.escape(str(a.get('name', a)))}</li>" for a in analyses
    ) or "<li><em>none</em></li>"
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<div style='font-family:system-ui;max-width:720px;margin:24px auto'>"
        f"<h2>Run report — <code>{_html.escape(name)}</code></h2>"
        f"<p><strong>Composite:</strong> <code>{_html.escape(spec_id)}</code><br>"
        f"<strong>Steps:</strong> {_html.escape(str(steps))}</p>"
        f"<h3>Figures ({len(viz_names)})</h3><ul>{rows}</ul>"
        f"<h3>Analyses ({len(analyses)})</h3><ul>{an}</ul>"
        "</div>"
    )


def run_flush(run_dir: Path, *, req, spec_id: str, db_file: str,
              run_id: str, core) -> dict:
    run_dir = Path(run_dir)
    analyses: list = []
    has_analyses = False
    try:
        analyses = _dispatch_analyses(
            spec_id=spec_id, db_file=db_file, run_id=run_id, core=core)
        has_analyses = bool(analyses)
    except Exception:
        traceback.print_exc()
    try:
        (run_dir / "analyses.json").write_text(
            json.dumps(analyses, default=str), encoding="utf-8")
    except Exception:
        traceback.print_exc()

    # Report card — always attempt; read viz names from the already-written viz.json.
    viz_names: list = []
    try:
        vj = run_dir / "viz.json"
        if vj.is_file():
            viz_names = list(json.loads(vj.read_text()).keys())
    except Exception:
        pass
    has_report = False
    try:
        (run_dir / "report.html").write_text(
            render_report_card(req=req, viz_names=viz_names, analyses=analyses),
            encoding="utf-8")
        has_report = True
    except Exception:
        traceback.print_exc()
    return {"has_analyses": has_analyses, "has_report": has_report}
