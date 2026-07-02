"""End-to-end test of the Composite Explorer's run-lifecycle API.

Spins up the dashboard server in-process against a fixture workspace and
exercises POST /api/composite-test-run and the three new GET endpoints.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path
import socket
import subprocess

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
    """Render a tiny fixture workspace and start the dashboard server."""
    if not FIXTURE_WORKSPACE.is_dir():
        pytest.skip(f"Fixture workspace not present at {FIXTURE_WORKSPACE}")
    # Copy fixture to tmp so writes (DB, reports) don't pollute the repo
    import shutil
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WORKSPACE, ws)
    port = _free_port()
    env = os.environ.copy()
    # The subprocess (and the detached run-composite child it spawns) needs
    # (a) this repo's vivarium_workbench — put _REPO_ROOT first so the test
    # exercises the working tree, not whatever happens to be pip-installed —
    # and (b) the workspace's own package (pbg_ws_increase_demo).
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_REPO_ROOT), str(ws), env.get("PYTHONPATH", "")])
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_workbench.cli", "serve",
         "--workspace", str(ws), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )
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
        url, data=body, headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def _get_raw(url):
    """Like _get but tolerates non-2xx without raising."""
    import urllib.error
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _poll_until_terminal(base, run_id, timeout=30):
    """Poll the status endpoint until the run reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = _get(f"{base}/api/composite-run/{run_id}/status")
        if body.get("status") in ("completed", "failed", "orphaned"):
            return body
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_test_run_returns_run_id_and_completes(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    status, body = _post(f"{base}/api/composite-test-run", {
        "id": spec_id, "overrides": {"rate": 2.5}, "steps": 5,
    })
    assert status == 202
    assert "run_id" in body
    assert body["status"] == "running"
    final = _poll_until_terminal(base, body["run_id"])
    assert final["status"] == "completed"
    db_file = server["ws"] / ".pbg" / "composite-runs.db"
    assert db_file.is_file()


def test_list_runs_includes_the_persisted_run(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, post_body = _post(f"{base}/api/composite-test-run", {
        "id": spec_id, "overrides": {"rate": 2.5}, "steps": 5,
    })
    _poll_until_terminal(base, post_body["run_id"])
    status, body = _get(f"{base}/api/composite-runs?"
                        f"spec_id={urllib.parse.quote(spec_id)}")
    assert status == 200
    runs = body["runs"]
    assert len(runs) >= 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["n_steps"] >= 1
    # Verify the run actually produced trajectory rows in the SQLiteEmitter's table.
    import sqlite3
    db_path = server["ws"] / ".pbg" / "composite-runs.db"
    with sqlite3.connect(str(db_path)) as c:
        n = c.execute(
            "SELECT COUNT(*) FROM history WHERE simulation_id=?",
            (runs[0]["run_id"],),
        ).fetchone()[0]
    assert n >= 1, "expected SQLiteEmitter to have written history rows"


def test_fetch_single_run_trajectory(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, post_body = _post(f"{base}/api/composite-test-run", {
        "id": spec_id, "overrides": {}, "steps": 4,
    })
    run_id = post_body["run_id"]
    _poll_until_terminal(base, run_id)
    status, body = _get(f"{base}/api/composite-run/{urllib.parse.quote(run_id)}")
    assert status == 200
    assert "trajectory" in body
    assert len(body["trajectory"]) >= 1


def test_fetch_state_at_step(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, post_body = _post(f"{base}/api/composite-test-run", {
        "id": spec_id, "overrides": {}, "steps": 3,
    })
    run_id = post_body["run_id"]
    _poll_until_terminal(base, run_id)
    status, body = _get(
        f"{base}/api/composite-run/{urllib.parse.quote(run_id)}/state?step=1")
    assert status == 200
    assert "state" in body
    assert isinstance(body["state"], dict)


def test_distinct_runs_get_distinct_ids(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, b1 = _post(f"{base}/api/composite-test-run", {
        "id": spec_id, "overrides": {"rate": 1.0}, "steps": 2,
    })
    _, b2 = _post(f"{base}/api/composite-test-run", {
        "id": spec_id, "overrides": {"rate": 2.0}, "steps": 2,
    })
    assert b1["run_id"] != b2["run_id"]


def test_api_composites_includes_default_n_steps(server):
    """Composites with default_n_steps surface it via /api/composites."""
    base = server["url"]
    status, response = _get(f"{base}/api/composites")
    assert status == 200
    composites = response["composites"]
    # The fixtures workspace has a generator with default_n_steps=42
    # (defined in pbg_ws_increase_demo/composites/__init__.py).
    matching = [c for c in composites if c.get("default_n_steps") == 42]
    assert matching, f"no composite has default_n_steps=42 in {composites}"


def test_status_endpoint_reports_completed(server):
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, body = _post(f"{base}/api/composite-test-run", {
        "id": spec_id, "steps": 4,
    })
    run_id = body["run_id"]
    final = _poll_until_terminal(base, run_id)
    assert final["status"] == "completed"
    assert final["progress_step"] == 4
    assert final["n_steps"] == 4


def test_status_endpoint_404s_for_unknown_run(server):
    base = server["url"]
    status, _ = _get_raw(f"{base}/api/composite-run/no-such-run/status")
    assert status == 404
