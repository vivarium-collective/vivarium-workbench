"""Refresh-viz seam: _study_refresh_viz re-renders declared visualizations
against the study's latest run and stamps provenance.

Builds a workspace with one study whose visualizations[].render is a
``python -c`` one-liner that writes the chart file. Calling the seam should
return a ``status="rendered"`` result and leave the chart's .meta.json stamped
with the latest run id.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import yaml

from vivarium_dashboard.server import _study_refresh_viz
from vivarium_dashboard.lib.study_viz_views import study_refresh_viz as _lib_study_refresh_viz


def _write_runs_db(study_dir: Path, run_id: str, completed_at: float) -> None:
    db = study_dir / "runs.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE runs_meta ("
            "run_id TEXT, started_at REAL, completed_at REAL, "
            "generation_id TEXT, emitter_path TEXT)"
        )
        conn.execute(
            "INSERT INTO runs_meta "
            "(run_id, started_at, completed_at, generation_id, emitter_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, completed_at - 1.0, completed_at, "gen0", ""),
        )
        conn.commit()
    finally:
        conn.close()


def _build_study(tmp_path: Path, *, latest_run_id: str) -> tuple[Path, str]:
    ws = tmp_path / "ws"
    study_dir = ws / "studies" / "demo"
    (study_dir / "charts").mkdir(parents=True)

    py = sys.executable
    # render writes the chart file via a python one-liner.
    render = f'{py} -c "open(\'{{chart}}\', \'w\').write(\'<svg/>\')"'
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({
            "name": "demo",
            "visualizations": [
                {"name": "c", "chart": "charts/c.svg", "render": render},
            ],
        }),
        encoding="utf-8",
    )

    _write_runs_db(study_dir, latest_run_id, 1_700_000_000.0)
    return ws, "demo"


def test_refresh_renders_and_stamps(tmp_path: Path):
    ws, name = _build_study(tmp_path, latest_run_id="run-9")

    out = _study_refresh_viz(ws, name)

    results = out["results"]
    assert len(results) == 1
    assert results[0]["status"] == "rendered"
    assert results[0]["chart"] == "charts/c.svg"

    chart = ws / "studies" / name / "charts" / "c.svg"
    assert chart.is_file()
    meta = json.loads(
        (chart.with_suffix(chart.suffix + ".meta.json")).read_text(encoding="utf-8")
    )
    assert meta["source_run_id"] == "run-9"


def test_refresh_missing_study_flagged_not_found(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "studies").mkdir(parents=True)
    out = _study_refresh_viz(ws, "nope")
    assert out.get("not_found") is True


def test_lib_seam_matches_server_for_render(tmp_path: Path):
    """The pure ``lib.study_viz_views.study_refresh_viz`` (used by the FastAPI
    route) renders + stamps identically to the stdlib ``_study_refresh_viz``."""
    ws, name = _build_study(tmp_path, latest_run_id="run-7")
    out = _lib_study_refresh_viz(ws, name)
    assert out["study"] == name
    assert out["results"][0]["status"] == "rendered"
    chart = ws / "studies" / name / "charts" / "c.svg"
    meta = json.loads(
        (chart.with_suffix(chart.suffix + ".meta.json")).read_text(encoding="utf-8")
    )
    assert meta["source_run_id"] == "run-7"


def test_lib_seam_missing_study_flagged_not_found(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "studies").mkdir(parents=True)
    out = _lib_study_refresh_viz(ws, "nope")
    assert out.get("not_found") is True
    assert "not found" in out["error"]
