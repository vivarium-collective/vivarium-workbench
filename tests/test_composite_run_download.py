"""Tests for build_composite_run_zip and GET /api/composite-run/<id>/download.

Unit tests:
  - build_composite_run_zip: 404 (no run dir), 409 (non-terminal), 200 (happy path)

HTTP-level tests via dashboard_client:
  - 404 for unknown run_id
  - 409 for a non-terminal (running) run
  - 200 + application/zip for a completed run
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from vivarium_dashboard.lib import composite_run_views as crv
from vivarium_dashboard.lib import composite_runs as cr


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_composite_run_views_lib.py)
# ---------------------------------------------------------------------------

def _make_ws(tmp_path: Path) -> Path:
    (tmp_path / ".pbg").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _seed_db(
    ws: Path,
    *,
    run_id: str = "run-1",
    status: str = "completed",
    n_steps: int = 5,
) -> Path:
    db = ws / ".pbg" / "composite-runs.db"
    conn = cr.connect(db)
    cr.save_metadata(
        conn,
        spec_id="demo.spec",
        run_id=run_id,
        params={},
        label="test",
        started_at=1_000_000.0,
        n_steps=n_steps,
    )
    cr.complete_metadata(conn, run_id=run_id, n_steps=n_steps, status=status)
    conn.close()
    return db


# ---------------------------------------------------------------------------
# Unit tests for build_composite_run_zip
# ---------------------------------------------------------------------------

def test_zip_contains_run_artifacts(tmp_path, monkeypatch):
    run_dir = tmp_path / ".pbg" / "runs" / "rZ"
    run_dir.mkdir(parents=True)
    (run_dir / "report.html").write_text("<i>r</i>")
    (run_dir / "viz.json").write_text("{}")
    (run_dir / "analyses.json").write_text("[]")
    (run_dir / "store.parquet").write_bytes(b"PAR1data")
    # Force "terminal" so the zip is allowed.
    monkeypatch.setattr(crv, "_run_is_terminal", lambda ws, rid: True, raising=False)
    data, fname, code = crv.build_composite_run_zip(str(tmp_path), "rZ")
    assert code == 200
    assert fname == "run_rZ.zip"
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "report.html" in names and "store.parquet" in names


def test_zip_excludes_request_json(tmp_path, monkeypatch):
    run_dir = tmp_path / ".pbg" / "runs" / "rZ"
    run_dir.mkdir(parents=True)
    (run_dir / "report.html").write_text("<i>r</i>")
    (run_dir / "request.json").write_text('{"spec_id": "x"}')
    monkeypatch.setattr(crv, "_run_is_terminal", lambda ws, rid: True, raising=False)
    data, fname, code = crv.build_composite_run_zip(str(tmp_path), "rZ")
    assert code == 200
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "request.json" not in names
    assert "report.html" in names


def test_zip_returns_404_when_run_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(crv, "_run_is_terminal", lambda ws, rid: True, raising=False)
    _, fname, code = crv.build_composite_run_zip(str(tmp_path), "no-such-run")
    assert code == 404
    assert fname == "run_no-such-run.zip"


def test_zip_returns_409_when_run_not_terminal(tmp_path, monkeypatch):
    run_dir = tmp_path / ".pbg" / "runs" / "rZ"
    run_dir.mkdir(parents=True)
    (run_dir / "report.html").write_text("x")
    monkeypatch.setattr(crv, "_run_is_terminal", lambda ws, rid: False, raising=False)
    _, fname, code = crv.build_composite_run_zip(str(tmp_path), "rZ")
    assert code == 409


# ---------------------------------------------------------------------------
# Unit tests for _run_is_terminal
# ---------------------------------------------------------------------------

class TestRunIsTerminal:
    def test_completed_is_terminal(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1", status="completed")
        assert crv._run_is_terminal(ws, "r1") is True

    def test_failed_is_terminal(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1", status="failed")
        assert crv._run_is_terminal(ws, "r1") is True

    def test_orphaned_is_terminal(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1", status="orphaned")
        assert crv._run_is_terminal(ws, "r1") is True

    def test_running_is_not_terminal(self, tmp_path):
        ws = _make_ws(tmp_path)
        db = ws / ".pbg" / "composite-runs.db"
        conn = cr.connect(db)
        cr.save_metadata(conn, spec_id="s", run_id="r1", params={}, label="",
                         started_at=0.0, n_steps=10)
        conn.close()
        assert crv._run_is_terminal(ws, "r1") is False

    def test_no_db_returns_false(self, tmp_path):
        ws = _make_ws(tmp_path)
        assert crv._run_is_terminal(ws, "r1") is False

    def test_unknown_run_id_returns_false(self, tmp_path):
        ws = _make_ws(tmp_path)
        _seed_db(ws, run_id="r1", status="completed")
        assert crv._run_is_terminal(ws, "no-such-run") is False


# ---------------------------------------------------------------------------
# HTTP-level tests through dashboard_client
# ---------------------------------------------------------------------------

def _minimal_ws(tmp_path: Path, run_id: str, status: str) -> Path:
    """Minimal workspace with workspace.yaml + seeded DB for the given run."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: test-ws\n")
    _seed_db(ws, run_id=run_id, status=status)
    return ws


def test_http_download_missing_run_returns_404(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: test-ws\n")
    client = dashboard_client(workspace=ws)
    resp = client.get("/api/composite-run/nonexistent-run/download")
    assert resp.status_code == 404, resp.text


def test_http_download_running_run_returns_409(tmp_path, dashboard_client):
    """A run whose PID matches a live process stays 'running' after reconcile.

    The server calls ``reconcile_stale_runs`` at startup: runs with a dead/missing
    PID are promoted to 'orphaned' (a terminal status). To keep a run truly
    non-terminal across the server start, we record the *pytest process* PID
    as the run's PID — that process is alive, so reconcile leaves it alone.
    """
    import os

    run_id = "run-running"
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: test-ws\n")
    # Seed a "running" run with a live PID so the server won't orphan it.
    db = ws / ".pbg" / "composite-runs.db"
    conn = cr.connect(db)
    cr.save_metadata(conn, spec_id="s", run_id=run_id, params={}, label="",
                     started_at=0.0, n_steps=10)
    cr.set_pid(conn, run_id=run_id, pid=os.getpid())
    conn.close()
    # Create the run dir so we don't hit 404 first.
    run_dir = ws / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.html").write_text("partial")
    client = dashboard_client(workspace=ws)
    resp = client.get(f"/api/composite-run/{run_id}/download")
    assert resp.status_code == 409, resp.json()


def test_http_download_completed_run_returns_zip(tmp_path, dashboard_client):
    run_id = "run-complete"
    ws = _minimal_ws(tmp_path, run_id=run_id, status="completed")
    # Populate the run dir with artifacts.
    run_dir = ws / ".pbg" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.html").write_text("<h1>done</h1>")
    (run_dir / "analyses.json").write_text('[]')
    (run_dir / "viz.json").write_text('{}')
    (run_dir / "store.parquet").write_bytes(b"PAR1fakeparquet")
    client = dashboard_client(workspace=ws)
    resp = client.get(f"/api/composite-run/{run_id}/download")
    assert resp.status_code == 200, resp.text
    # The response must declare itself a zip attachment...
    assert resp.headers.get("content-type") == "application/zip"
    assert 'filename="run_run-complete.zip"' in resp.headers.get("content-disposition", "")
    # ...and the body must be a valid non-empty zip.
    zf = zipfile.ZipFile(io.BytesIO(resp._body))
    names = zf.namelist()
    assert len(names) > 0
    assert "report.html" in names
    assert "request.json" not in names
