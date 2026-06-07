# VENDORED COPY — canonical source is pbg_superpowers/refresh_viz.py.
# Keep identical to the canonical; drift is caught by tests/test_refresh_viz_mirror.py.
"""Re-run the render: command of each visualizations[] entry against the
study's latest run, stamping provenance. Error-tolerant: a failed render leaves
the old chart + meta in place (still flagged stale) and never raises."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .viz_freshness import stamp_meta


def refresh_study_viz(study_dir, spec: dict, latest: dict | None) -> list[dict]:
    study_dir = Path(study_dir)
    results: list[dict] = []
    for entry in (spec.get("visualizations") or []):
        name = entry.get("name") or entry.get("chart") or "<unnamed>"
        chart_rel = entry.get("chart")
        cmd = entry.get("render")
        if not cmd or not chart_rel:
            results.append({"name": name, "chart": chart_rel,
                            "status": "needs_manual_refresh"})
            continue
        chart = study_dir / chart_rel
        filled = cmd.replace("{chart}", chart_rel)
        env = dict(os.environ)
        if latest and latest.get("emitter_path"):
            ep = latest["emitter_path"]
            env["PBG_RUN_DIR"] = ep if os.path.isabs(ep) else str(study_dir / ep)
        if latest and latest.get("run_id"):
            env["PBG_RUN_ID"] = latest["run_id"]
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
                   source_run_id=(latest or {}).get("run_id"),
                   generation_id=(latest or {}).get("generation_id"),
                   rendered_at=time.time(), command=filled)
        results.append({"name": name, "chart": chart_rel, "status": "rendered"})
    return results
