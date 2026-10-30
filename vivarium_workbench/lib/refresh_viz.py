"""Re-run the render: command of each visualizations[] entry against the
study's latest run, stamping provenance. Error-tolerant: a failed render leaves
the old chart + meta in place (still flagged stale) and never raises.

De-vendored in Phase 2.1a (this module is now workbench-canonical; the
plugin's copy is deleted in PR-2). Ported forward two fixes that had drifted
into the plugin's copy ahead of this one (§0.3 of the 2.1 decomposition
plan): the VIVA_RUN_DIR/VIVA_RUN_ID env vars alongside the deprecated
PBG_RUN_* names, and tagging each rendered chart with its producing run via
chart_store.tag_chart.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from discovery_engine.viz_freshness import stamp_meta          # A.3: plugin-canonical

from vivarium_workbench.lib.refusal import Refusal, present_leaves_from_runs_db

# TEMPORARY (Phase 2.1a → retired in 2.1c when chart_store moves into lib/).
# chart_store is still plugin-owned; guarded so a plugin without tag_chart
# degrades to "chart rendered but not run-tagged" rather than failing the
# refresh.
try:
    from viva_superpowers import chart_store
except Exception:  # noqa: BLE001
    chart_store = None


def _requires_refusal(study_dir: Path, requires: list) -> Refusal | None:
    """Check a figure's declared ``requires: [observable]`` against the
    study's latest run. Returns a :class:`Refusal` naming the first missing
    observable (and what the run's store DOES carry) when one is absent,
    else ``None`` (all required observables are present, or ``requires`` is
    unusable/empty).

    Boundary V / ``wb.figure.render`` in the typed run→finding chain design
    (§3b, "refuse-to-render"). Sqlite-dialect only for now — mirrors the
    ``wb.measure.read`` stand-in in ``lib/study_charts.py`` (a full
    dialect-general leaf manifest is the design's later `wb.run.to_dialect`
    work, out of scope here). A study on a dialect this can't inspect simply
    never refuses via this path (falls through to the caller's existing
    behavior) — this is a strict ADDITION, never a new way to fail closed.
    """
    names = [r for r in (requires or []) if isinstance(r, str) and r.strip()]
    if not names:
        return None
    present = present_leaves_from_runs_db(study_dir / "runs.db")
    if not present:
        # Can't inspect this run's store (no sqlite runs.db, or it's empty) —
        # nothing to compare `requires` against, so don't manufacture a
        # false-positive refusal. Falls through to needs_manual_refresh.
        return None
    missing = [r for r in names if r not in present]
    if not missing:
        return None
    return Refusal(
        reason=(
            f"needs {', '.join(missing)} — absent from this run's store"
            f" (present: {', '.join(present[:8])}"
            f"{', …' if len(present) > 8 else ''})"
        ),
        missing=missing,
        present=present,
    )


def refresh_study_viz(study_dir, spec: dict, latest: dict | None) -> list[dict]:
    study_dir = Path(study_dir)
    results: list[dict] = []
    for entry in (spec.get("visualizations") or []):
        name = entry.get("name") or entry.get("chart") or "<unnamed>"
        chart_rel = entry.get("chart")
        cmd = entry.get("render")
        if not cmd or not chart_rel:
            # Backward-compatible escape hatch: an entry with NO `requires`
            # (or `requires: []`) keeps the exact prior behavior — a bare
            # `needs_manual_refresh` — since self-contained/baked figures
            # (no declared observable contract) must still work unchanged.
            requires = entry.get("requires") or []
            refusal = _requires_refusal(study_dir, requires) if requires else None
            if refusal is not None:
                results.append({"name": name, "chart": chart_rel,
                                **refusal.to_dict()})
                continue
            results.append({"name": name, "chart": chart_rel,
                            "status": "needs_manual_refresh"})
            continue
        chart = study_dir / chart_rel
        filled = cmd.replace("{chart}", chart_rel)
        # A pinned entry (source_run:) anchors the chart to a specific run, so
        # we stamp THAT run id (not the study's latest). Falls back to latest.
        run_id = entry.get("source_run") or (latest or {}).get("run_id")
        env = dict(os.environ)
        # Set BOTH env names (pbg→viva): new VIVA_* + deprecated PBG_* so any
        # reader (viz runner, workbench, scaffolded scripts) works either way.
        if latest and latest.get("emitter_path"):
            ep = latest["emitter_path"]
            run_dir = ep if os.path.isabs(ep) else str(study_dir / ep)
            env["VIVA_RUN_DIR"] = run_dir
            env["PBG_RUN_DIR"] = run_dir
        if run_id:
            env["VIVA_RUN_ID"] = run_id
            env["PBG_RUN_ID"] = run_id
        try:
            proc = subprocess.run(filled, shell=True, cwd=study_dir, env=env,
                                  capture_output=True, text=True, timeout=900)
        except (subprocess.SubprocessError, OSError) as e:
            results.append({"name": name, "chart": chart_rel,
                            "status": "error", "error": str(e)})
            continue
        if proc.returncode != 0:
            results.append({"name": name, "chart": chart_rel, "status": "error",
                            "error": (proc.stderr or proc.stdout or "")[-2000:]})
            continue
        stamp_meta(chart,
                   source_run_id=run_id,
                   generation_id=(latest or {}).get("generation_id"),
                   rendered_at=time.time(), command=filled)
        if run_id and chart_store is not None:
            # Record which run produced this chart so a later canonical run can
            # prune it as superseded (chart_store; best-effort, never blocks).
            try:
                chart_store.tag_chart(chart, run_id)
            except Exception:
                pass
        results.append({"name": name, "chart": chart_rel, "status": "rendered"})
    return results
