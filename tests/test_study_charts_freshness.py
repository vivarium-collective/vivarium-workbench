"""Per-chart freshness in the study-charts payload.

Builds a workspace with one study that declares a visualization, has a
rendered chart + provenance sidecar, and a runs.db. The charts payload
should badge each chart fresh/stale/untracked based on whether the chart's
stamped source_run_id matches the study's latest run.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import yaml

from vivarium_dashboard.server import _study_charts_payload
from vivarium_dashboard.lib.viz_freshness import stamp_meta


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


def _build_study(tmp_path: Path, *, stamped_run_id: str, latest_run_id: str) -> tuple[Path, str]:
    ws = tmp_path / "ws"
    study_dir = ws / "studies" / "demo"
    charts = study_dir / "charts"
    charts.mkdir(parents=True)

    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({
            "name": "demo",
            "visualizations": [
                {"name": "c", "chart": "charts/c.svg", "render": "cmd"},
            ],
        }),
        encoding="utf-8",
    )

    chart = charts / "c.svg"
    chart.write_text("<svg/>", encoding="utf-8")
    # On-disk orphan chart with no visualizations[] entry.
    (charts / "orphan.svg").write_text("<svg/>", encoding="utf-8")

    completed = 1_700_000_000.0
    _write_runs_db(study_dir, latest_run_id, completed)

    # Stamp the chart as produced by stamped_run_id, rendered AFTER completion.
    stamp_meta(
        chart,
        source_run_id=stamped_run_id,
        generation_id="gen0",
        rendered_at=completed + 10.0,
        command="cmd",
    )
    return ws, "demo"


def _freshness_by_key(payload: dict) -> dict[str, str]:
    return {c.get("key"): c.get("freshness") for c in payload["charts"]
            if c.get("source") == "static"}


def test_chart_fresh_when_stamp_matches_latest(tmp_path: Path):
    ws, name = _build_study(tmp_path, stamped_run_id="run-1", latest_run_id="run-1")
    payload = _study_charts_payload(ws, name)
    fb = _freshness_by_key(payload)
    assert fb["c"] == "fresh"


def test_chart_stale_when_stamp_differs(tmp_path: Path):
    ws, name = _build_study(tmp_path, stamped_run_id="run-old", latest_run_id="run-new")
    payload = _study_charts_payload(ws, name)
    fb = _freshness_by_key(payload)
    assert fb["c"] == "stale"


def test_orphan_chart_is_untracked(tmp_path: Path):
    ws, name = _build_study(tmp_path, stamped_run_id="run-1", latest_run_id="run-1")
    payload = _study_charts_payload(ws, name)
    fb = _freshness_by_key(payload)
    assert fb["orphan"] == "untracked"
