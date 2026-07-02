"""End-to-end test of the Simulations API.

Spins up the dashboard server against the ws_increase_demo fixture and
exercises GET /api/simulations + DELETE /api/simulation-run.
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WORKSPACE = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(tmp_path):
    if not FIXTURE_WORKSPACE.is_dir():
        pytest.skip(f"Fixture workspace not present at {FIXTURE_WORKSPACE}")
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WORKSPACE, ws)
    port = _free_port()
    env = os.environ.copy()
    # See test_composite_explorer_api.py — put the repo root first so the
    # detached run-composite child resolves the working-tree code.
    env["PYTHONPATH"] = (str(_REPO_ROOT) + os.pathsep + str(ws)
                         + os.pathsep + env.get("PYTHONPATH", ""))
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_dashboard.cli", "serve",
         "--workspace", str(ws), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    # serve_fastapi writes server-info before uvicorn binds the port, so wait
    # for the app to actually answer /health, not just for the file to exist.
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=2)
            pytest.fail(f"server did not start:\n{out.decode()}\n{err.decode()}")
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(0.25)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=2)
        pytest.fail(f"server did not answer /health:\n{out.decode()}\n{err.decode()}")
    yield {"url": base_url, "ws": ws}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _post(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def _delete(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"}, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _poll_until_terminal(base, run_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = _get(f"{base}/api/composite-run/{run_id}/status")
        if body.get("status") in ("completed", "failed", "orphaned"):
            return body
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_get_simulations_lists_a_completed_run(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, body = _post(f"{base}/api/composite-test-run",
                    {"id": spec_id, "steps": 3})
    run_id = body["run_id"]
    _poll_until_terminal(base, run_id)

    status, body = _get(f"{base}/api/simulations")
    assert status == 200
    sims = body["simulations"]
    matching = [s for s in sims if s["run_id"] == run_id]
    assert matching, f"expected our run in the list, got {sims}"
    assert matching[0]["status"] == "completed"
    assert matching[0]["spec_id"] == spec_id
    assert matching[0]["db_path"] == ".pbg/composite-runs.db"
    assert matching[0]["studies"] == []


def test_get_simulations_includes_current_and_emitter_type(server):
    """The endpoint returns the {simulations, current} shape and tags each
    sim with a capitalized emitter_type. Seeds a runs.db directly (read live
    on each request) so this doesn't depend on the detached run machinery."""
    base = server["url"]
    ws = server["ws"]
    db = ws / "studies" / "demo-study" / "runs.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS runs_meta ("
            "run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, label TEXT, "
            "params_json TEXT, started_at REAL NOT NULL, completed_at REAL, "
            "n_steps INTEGER, status TEXT NOT NULL, sim_name TEXT);")
        conn.execute(
            "INSERT INTO runs_meta (run_id, spec_id, started_at, completed_at, "
            "n_steps, status) VALUES (?,?,?,?,?,?)",
            ("seeded-run", "pkg.demo", 100.0, 101.0, 3, "completed"))
        conn.commit()
    finally:
        conn.close()

    status, body = _get(f"{base}/api/simulations")
    assert status == 200
    assert "simulations" in body
    assert "current" in body  # may be None depending on the fixture's branch
    sims = body["simulations"]
    # Every returned sim is tagged with a capitalized emitter_type.
    assert sims, "expected the seeded run in the listing"
    for s in sims:
        assert s["emitter_type"] in {"SQLite", "Parquet", "XArray"}
    seeded = [s for s in sims if s["run_id"] == "seeded-run"]
    assert seeded and seeded[0]["emitter_type"] == "SQLite"


def test_delete_simulation_run_removes_everything(server):
    # The stdlib ``DELETE /api/simulation-run`` (full-summary delete) was
    # retired; the FastAPI app only exposes the lighter ``/api/run-delete``.
    # Exercise the lib function that owned the summary contract directly.
    from vivarium_dashboard.lib.simulations_index import delete_simulation
    base = server["url"]
    ws = server["ws"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, body = _post(f"{base}/api/composite-test-run",
                    {"id": spec_id, "steps": 2})
    run_id = body["run_id"]
    _poll_until_terminal(base, run_id)

    # The lib "removes everything" contract: the runs_meta row, its history
    # rows, and the run's artifact dir are all gone; no per-file errors. (The
    # SQLiteEmitter's own `simulations` table is intentionally not touched by
    # this function, so we assert the documented summary rather than the live
    # server's merged listing.)
    summary = delete_simulation(ws, run_id)
    assert summary["deleted_rows"] == 1
    assert summary["deleted_history"] >= 1
    assert summary["removed_dir"] is True
    assert summary["errors"] == []


def test_delete_simulation_run_404_unknown(server):
    # Unknown run id → the lib delete raises RunNotFound (was HTTP 404).
    from vivarium_dashboard.lib.simulations_index import (
        delete_simulation, RunNotFound)
    ws = server["ws"]
    with pytest.raises(RunNotFound):
        delete_simulation(ws, "ghost-run")


def test_delete_simulation_run_400_missing_run_id(server):
    # Missing/empty run id → the lib delete finds no DB and raises RunNotFound
    # (was the HTTP 400 "missing run_id" validation).
    from vivarium_dashboard.lib.simulations_index import (
        delete_simulation, RunNotFound)
    ws = server["ws"]
    with pytest.raises(RunNotFound):
        delete_simulation(ws, "")


def test_post_route_matches_with_query_string(server):
    """do_POST must strip the query string before route lookup (mirrors do_GET).

    Regression: before the fix, ANY POST carrying a query string 404'd at the
    router — including handlers that explicitly read query params (e.g.
    ``/api/study-report-single?skeptic=1`` for the "View as skeptic" button).
    We assert the route is REACHED (the handler's own 400 for a missing study),
    not the router's bare 404 "not found".
    """
    import urllib.error
    base = server["url"]
    req = urllib.request.Request(
        f"{base}/api/study-report-single?skeptic=1",
        data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15)
        assert False, "expected a 4xx error"
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())
        assert e.code == 400, f"route not reached (got {e.code}: {body})"
        assert "study" in (body.get("error") or "").lower()
