"""The native gallery surfaces inline Visualization-STEP panels.

A composite can embed a ``Visualization`` step (viva-munk's MultibodyVizStep, a
CPM/tyssue spatial-snapshot step). The run captures its HTML via
``render_results``, which yields a ``{'html': '<rendered>'}`` payload. The study
run writes that into ``.pbg/runs/<id>/viz.json`` alongside the declared-viz
strings, so ``build_study_native_gallery`` must accept BOTH a bare HTML string
and the ``{'html': ...}`` payload shape (otherwise the embedded spatial snapshot
is silently dropped).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from vivarium_workbench.lib.study_native_gallery import build_study_native_gallery


def _study_with_viz_json(tmp_path: Path, panels: dict) -> tuple[Path, str]:
    ws = tmp_path / "ws"
    (ws).mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    study_dir = ws / "studies" / "demo"
    study_dir.mkdir(parents=True)
    db = study_dir / "runs.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT, label TEXT, "
        "params_json TEXT, started_at REAL, completed_at REAL, n_steps INTEGER, "
        "status TEXT, sim_name TEXT)")
    conn.execute(
        "INSERT INTO runs_meta (run_id, spec_id, label, params_json, started_at, "
        "completed_at, n_steps, status, sim_name) VALUES (?,?,?,?,?,?,?,?,?)",
        ("r1", "demo", "", "{}", time.time() - 10, time.time() - 1, 10,
         "completed", "demo"))
    conn.commit()
    conn.close()
    run_dir = ws / ".pbg" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "viz.json").write_text(json.dumps(panels), encoding="utf-8")
    return ws, "demo"


def test_native_gallery_accepts_inline_step_html_dict(tmp_path):
    ws, name = _study_with_viz_json(tmp_path, {
        "growth_curve": "<div>a declared, bare-string panel</div>",
        "colony_snapshot": {"html": "<div><img src='x'></div>"},  # inline viz step
    })
    gal = build_study_native_gallery(ws, name)
    panels = gal["panels"]
    assert panels.get("growth_curve") == "<div>a declared, bare-string panel</div>"
    # the {'html': ...} payload is unwrapped to its HTML string, not dropped
    assert panels.get("colony_snapshot") == "<div><img src='x'></div>"


def test_native_gallery_drops_error_and_empty_panels(tmp_path):
    ws, name = _study_with_viz_json(tmp_path, {
        "ok": {"html": "<div>fine</div>"},
        "err": {"html": "Could not render this view"},
        "empty": {"html": "   "},
        "none": {"html": None},
    })
    panels = build_study_native_gallery(ws, name)["panels"]
    assert set(panels) == {"ok"}
