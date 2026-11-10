"""Single render dispatcher for one declared figure (discovery viz loop, Build).

``render_one(ws_root, study, name)`` renders ONE ``visualizations[]`` entry of a
study by its derived kind (``discovery_engine.viz_freshness.normalize_entry``):

  * ``command``     -> the entry's ``render:`` recipe via ``refresh_study_viz``
  * ``class``       -> a ``local:`` viz-class composite via ``render_visualizations``
  * ``figure``      -> refused (a static file has no refresh recipe)
  * ``broken_stub`` -> refused (declare an address/render first)

It never fabricates or overwrites a figure on failure: both underlying paths keep
an existing figure when a render fails. Every render is handed the study's LATEST
run so the stamped ``source_run_id`` is the run the figure actually reflects.
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml


def find_study_dir(ws_root, study: str) -> Path | None:
    ws = Path(ws_root)
    direct = ws / "studies" / study
    if (direct / "study.yaml").is_file():
        return direct
    for p in sorted((ws / "investigations").glob(f"*/studies/{study}")):
        if (p / "study.yaml").is_file():
            return p
    return None


def _fail(detail: str, **kw) -> dict:
    return {"ok": False, "chart": kw.get("chart"), "source_run_id": None, "detail": detail}


def render_one(ws_root, study: str, name: str, *, build_and_run=None,
               inputs_by_class: dict | None = None, core_registry: dict | None = None) -> dict:
    """Render one figure. Returns ``{ok, chart, source_run_id, detail}``."""
    try:
        from discovery_engine.viz_freshness import normalize_entry, stamp_meta
    except ImportError:
        return _fail("discovery_engine is not available")
    sdir = find_study_dir(ws_root, study)
    if sdir is None:
        return _fail(f"no study.yaml for {study!r}")
    try:
        spec = yaml.safe_load((sdir / "study.yaml").read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return _fail(f"cannot read study.yaml: {e}")
    entry = next((e for e in spec.get("visualizations") or []
                  if isinstance(e, dict) and e.get("name") == name), None)
    if entry is None:
        return _fail(f"{study} declares no visualization named {name!r}")
    ns = normalize_entry(entry)
    kind, output = ns["kind"], ns["output"]
    if kind == "broken_stub":
        return _fail("declared but not renderable — declare an address/render first")
    if kind == "figure":
        return _fail("static figure has no refresh recipe", chart=output)

    if not output:
        return _fail("no resolvable output path for this figure")
    chart = (sdir / output).resolve()
    try:
        chart.relative_to(sdir.resolve())
    except ValueError:
        return _fail(f"output {output!r} escapes the study directory", chart=output)

    from vivarium_workbench.lib.study_charts import latest_run_row
    latest = latest_run_row(sdir / "runs.db")
    run_id = entry.get("source_run") or (latest or {}).get("run_id")
    meta_p = Path(str(chart) + ".meta.json")

    # ONE snapshot-and-verify guard around both dispatch paths: a render counts only if
    # the figure exists, is non-empty, and was actually (re)written; otherwise restore
    # the prior bytes/meta (or remove what the attempt created) and do not stamp.
    prior = chart.read_bytes() if chart.is_file() else None
    prior_mtime = chart.stat().st_mtime_ns if prior is not None else None
    prior_meta = meta_p.read_bytes() if meta_p.is_file() else None

    def _restore():
        for path, data in ((chart, prior), (meta_p, prior_meta)):
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        if prior_mtime is not None:
            import os
            os.utime(chart, ns=(prior_mtime, prior_mtime))

    def _refuse(detail):
        _restore()
        return _fail(detail, chart=output)

    if kind == "command":
        from vivarium_workbench.lib.refresh_viz import refresh_study_viz
        res = refresh_study_viz(sdir, {"visualizations": [{**entry, "chart": output}]}, latest)
        r = res[0] if res else {}
        if r.get("status") != "rendered":
            return _refuse(r.get("error") or r.get("reason") or r.get("status") or "render failed")
    else:  # class
        from vivarium_workbench.lib.investigations import render_visualizations
        if build_and_run is None:
            from vivarium_workbench.lib.viz_render import viz_render_hooks
            inputs_by_class, build_and_run = viz_render_hooks(Path(ws_root))
        skipped: list = []
        try:
            paths = render_visualizations(
                {"name": spec.get("name", study), "visualizations": [entry]}, sdir, study,
                core_registry=core_registry, inputs_by_class=inputs_by_class,
                build_and_run=build_and_run, skipped=skipped,
                latest_run_id=(latest or {}).get("run_id"), output_rel=output)
        except Exception as e:  # noqa: BLE001
            return _refuse(f"render raised {type(e).__name__}: {e}")
        if not paths:
            return _refuse(skipped[0]["reason"] if skipped else "nothing rendered")

    if not chart.is_file():
        return _refuse(f"render wrote no figure at {output}")
    now = chart.read_bytes()
    if not now.strip():
        return _refuse("render produced an empty figure")
    if now[:400].decode("utf-8", "ignore").lstrip().startswith("<p") and \
            "Failed to render" in now[:400].decode("utf-8", "ignore"):
        return _refuse("viz class render failed (error stub)")
    if prior is not None and now == prior and chart.stat().st_mtime_ns == prior_mtime:
        return _refuse("render did not write a new figure (kept the existing one)")

    stamp_meta(chart, source_run_id=(latest or {}).get("run_id") if kind == "class" else run_id,
               generation_id=(latest or {}).get("generation_id"),
               rendered_at=time.time(), command=f"render_one:{name}")
    return {"ok": True, "chart": output, "source_run_id": run_id, "detail": "rendered"}
