"""Tests for the has_analyses / has_report / downloadable fields added to
``build_composite_run_status`` in Task 3 of the loom-setup-run-revamp.

These tests are NON-VACUOUS: we seed a genuinely completed run row in the
composite-runs DB so that ``build_composite_run_status`` returns
``status == "completed"``, then assert the new flags unconditionally.
"""
from pathlib import Path

from vivarium_dashboard.lib.composite_runs import (
    connect,
    save_metadata,
    complete_metadata,
)
from vivarium_dashboard.lib import composite_run_views as crv


def _seed_completed_run(ws_root: Path, run_id: str) -> None:
    """Create the DB and seed a completed run row."""
    pbg = ws_root / ".pbg"
    pbg.mkdir(parents=True, exist_ok=True)
    db_file = pbg / "composite-runs.db"
    conn = connect(db_file)
    save_metadata(
        conn,
        spec_id="pkg.composites.demo",
        run_id=run_id,
        params={},
        label="test run",
        started_at=0.0,
        n_steps=5,
    )
    complete_metadata(conn, run_id=run_id, n_steps=5, status="completed")
    conn.close()


def test_completed_run_has_report_and_no_analyses_when_empty(tmp_path):
    """report.html present + analyses.json is '[]' → has_report True, has_analyses False."""
    run_id = "rX"
    _seed_completed_run(tmp_path, run_id)

    run_dir = tmp_path / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.html").write_text("<i>ok</i>", encoding="utf-8")
    (run_dir / "analyses.json").write_text("[]", encoding="utf-8")

    body, code = crv.build_composite_run_status(tmp_path, run_id)

    assert code == 200
    assert body["status"] == "completed", f"Expected completed, got: {body['status']}"
    assert body["has_report"] is True
    assert body["downloadable"] is True
    assert body["has_analyses"] is False


def test_completed_run_has_analyses_when_non_empty(tmp_path):
    """analyses.json with real content → has_analyses True."""
    run_id = "rY"
    _seed_completed_run(tmp_path, run_id)

    run_dir = tmp_path / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.html").write_text("<i>ok</i>", encoding="utf-8")
    (run_dir / "analyses.json").write_text(
        '[{"name": "growth_rate", "value": 0.8}]', encoding="utf-8"
    )

    body, code = crv.build_composite_run_status(tmp_path, run_id)

    assert code == 200
    assert body["status"] == "completed"
    assert body["has_analyses"] is True
    assert body["has_report"] is True
    assert body["downloadable"] is True


def test_completed_run_no_report_no_analyses_when_files_absent(tmp_path):
    """Neither file present → both flags False, downloadable still True."""
    run_id = "rZ"
    _seed_completed_run(tmp_path, run_id)
    # run dir exists but neither file written
    run_dir = tmp_path / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True)

    body, code = crv.build_composite_run_status(tmp_path, run_id)

    assert code == 200
    assert body["status"] == "completed"
    assert body["has_report"] is False
    assert body["has_analyses"] is False
    assert body["downloadable"] is True


def test_non_completed_run_downloadable_false(tmp_path):
    """A running (non-completed) run → downloadable False."""
    run_id = "rW"
    pbg = tmp_path / ".pbg"
    pbg.mkdir(parents=True, exist_ok=True)
    db_file = pbg / "composite-runs.db"
    conn = connect(db_file)
    save_metadata(
        conn,
        spec_id="pkg.composites.demo",
        run_id=run_id,
        params={},
        label="",
        started_at=0.0,
        n_steps=5,
    )
    conn.close()

    body, code = crv.build_composite_run_status(tmp_path, run_id)

    assert code == 200
    assert body["status"] == "running"
    assert body["downloadable"] is False
