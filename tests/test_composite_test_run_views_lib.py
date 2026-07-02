"""Tests for ``lib.composite_test_run_views.composite_test_run``.

Behaviour-preserving port of ``server.Handler._post_composite_test_run``.  Every
test monkeypatches ``run_registry.spawn_detached`` (fake pid / raise),
``run_registry.count_running``, and ``cr.generate_run_id`` (fixed id) so NO real
subprocess is ever spawned.  Asserts the 400 / 429 / 202 / 500 paths plus the
exact request.json keys (incl. ``"workspace": str(ws_root)``) and the db calls
(save_metadata / set_pid / complete_metadata).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import run_registry
from vivarium_workbench.lib import composite_test_run_views as views


def _make_ws(tmp_path: Path, *, name: str = "demo-ws") -> Path:
    """A minimal workspace root: workspace.yaml + .pbg dir."""
    (tmp_path / "workspace.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    (tmp_path / ".pbg").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def fixed_run_id(monkeypatch):
    rid = "demo.spec__1700000000__abcdef"
    monkeypatch.setattr(cr, "generate_run_id", lambda spec_id, params=None, now=None: rid)
    return rid


# ---------------------------------------------------------------------------
# 400 — missing id
# ---------------------------------------------------------------------------

def test_missing_id_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.composite_test_run(ws, {})
    assert status == 400
    assert body == {"error": "missing id"}


def test_blank_id_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.composite_test_run(ws, {"id": "   "})
    assert status == 400
    assert body == {"error": "missing id"}


# ---------------------------------------------------------------------------
# 429 — concurrency cap
# ---------------------------------------------------------------------------

def test_at_concurrency_cap_429(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(run_registry, "count_running",
                        lambda db_file: run_registry.CONCURRENCY_CAP)

    def _no_spawn(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("spawn_detached must not be called at cap")

    monkeypatch.setattr(run_registry, "spawn_detached", _no_spawn)
    body, status = views.composite_test_run(ws, {"id": "demo.spec"})
    assert status == 429
    assert body == {"error": "too many runs in progress — wait for one to finish"}


# ---------------------------------------------------------------------------
# 202 — happy path
# ---------------------------------------------------------------------------

def test_happy_path_202(tmp_path, monkeypatch, fixed_run_id):
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    spawn_calls = {}

    def _fake_spawn(request_path, *, workspace, log_path):
        spawn_calls["request_path"] = request_path
        spawn_calls["workspace"] = workspace
        spawn_calls["log_path"] = log_path
        return 4242

    monkeypatch.setattr(run_registry, "spawn_detached", _fake_spawn)

    body, status = views.composite_test_run(
        ws, {"id": "demo.spec", "overrides": {"k": 1}, "steps": 9})

    assert status == 202
    assert body == {"run_id": fixed_run_id, "status": "running"}

    # spawn_detached received ws_root (NOT a server WORKSPACE global) as workspace.
    assert spawn_calls["workspace"] == ws

    # request.json written with the EXACT keys + "workspace": str(ws_root).
    req_path = ws / ".pbg" / "runs" / fixed_run_id / "request.json"
    assert req_path.exists()
    req = json.loads(req_path.read_text())
    assert set(req) == {
        "run_id", "spec_id", "pkg", "workspace", "overrides",
        "steps", "emit_paths", "db_file", "log_path",
    }
    assert req["run_id"] == fixed_run_id
    assert req["spec_id"] == "demo.spec"
    assert req["workspace"] == str(ws)
    assert req["pkg"] == "pbg_demo_ws"  # derived from name "demo-ws"
    assert req["overrides"] == {"k": 1}
    assert req["steps"] == 9
    assert req["emit_paths"] == []
    assert req["log_path"] == str(
        (ws / ".pbg" / "runs" / fixed_run_id / "run.log").relative_to(ws))
    assert req["db_file"] == str(ws / ".pbg" / "composite-runs.db")

    # save_metadata wrote a running row; set_pid stamped the fake pid.
    db = ws / ".pbg" / "composite-runs.db"
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT spec_id, status, n_steps, pid FROM runs_meta WHERE run_id=?",
            (fixed_run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("demo.spec", "running", 9, 4242)


def test_package_path_override_used(tmp_path, monkeypatch, fixed_run_id):
    ws = tmp_path
    (ws / "workspace.yaml").write_text(
        "name: demo-ws\npackage_path: pbg_custom\n", encoding="utf-8")
    (ws / ".pbg").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    monkeypatch.setattr(run_registry, "spawn_detached",
                        lambda request_path, *, workspace, log_path: 7)
    views.composite_test_run(ws, {"id": "demo.spec"})
    req = json.loads(
        (ws / ".pbg" / "runs" / fixed_run_id / "request.json").read_text())
    assert req["pkg"] == "pbg_custom"


def test_non_list_emit_paths_coerced(tmp_path, monkeypatch, fixed_run_id):
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    monkeypatch.setattr(run_registry, "spawn_detached",
                        lambda request_path, *, workspace, log_path: 1)
    views.composite_test_run(ws, {"id": "demo.spec", "emit_paths": "not-a-list"})
    req = json.loads(
        (ws / ".pbg" / "runs" / fixed_run_id / "request.json").read_text())
    assert req["emit_paths"] == []


# ---------------------------------------------------------------------------
# 500 — spawn failure
# ---------------------------------------------------------------------------

def test_spawn_failure_500(tmp_path, monkeypatch, fixed_run_id):
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)

    def _boom(request_path, *, workspace, log_path):
        raise RuntimeError("no exe")

    monkeypatch.setattr(run_registry, "spawn_detached", _boom)

    body, status = views.composite_test_run(ws, {"id": "demo.spec"})
    assert status == 500
    assert body == {"error": "spawn failed: no exe", "run_id": fixed_run_id}

    # complete_metadata flipped the row to status="failed" (n_steps=0).
    db = ws / ".pbg" / "composite-runs.db"
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT status, n_steps FROM runs_meta WHERE run_id=?",
            (fixed_run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("failed", 0)
