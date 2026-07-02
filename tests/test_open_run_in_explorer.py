"""Integration smoke for the 'open prior run in Composite Explorer' feature.

The actual rendering is browser-side JS; this test verifies (a) the served
walkthrough.js exposes the new symbols, (b) the underlying backend
endpoints produce a complete canonical input the JS would render from.
"""
import json
import os
import shutil
import socket
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
    env["PYTHONPATH"] = (str(_REPO_ROOT) + os.pathsep + str(ws)
                         + os.pathsep + env.get("PYTHONPATH", ""))
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_workbench.cli", "serve",
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


def _get_text(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode()


def _poll_until_terminal(base, run_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = _get(f"{base}/api/composite-run/{run_id}/status")
        if body.get("status") in ("completed", "failed", "orphaned"):
            return body
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_walkthroughjs_exports_required_symbols(server):
    base = server["url"]
    status, js = _get_text(f"{base}/walkthrough.js")
    assert status == 200
    for needle in (
        "_ceLoadRunFromId",
        "_ceRenderRunResults",
        "_trajectoryToObservables",
        "_ceStopRunPoll",
    ):
        assert needle in js, f"missing {needle}"
    # _ceTestRun must read run_id from a 202 response, not the old fields.
    assert "window._ceLastRunId = run_id" in js, \
        "rewritten _ceTestRun should assign run_id from the 202 body"


def test_explorer_loads_with_run_id_then_endpoints_serve(server):
    """End-to-end: POST a run → poll terminal → both endpoints return the
    canonical input shape the JS would render from."""
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, body = _post(f"{base}/api/composite-test-run",
                    {"id": spec_id, "steps": 3})
    run_id = body["run_id"]
    final = _poll_until_terminal(base, run_id)

    # Status endpoint: terminal state, full shape.
    assert final["status"] == "completed"
    assert final["n_steps"] == 3
    assert "viz_html" in final  # may be empty dict, must be present

    # Trajectory endpoint: rows with the (step, time, state) shape.
    _, traj = _get(f"{base}/api/composite-run/{run_id}")
    assert "trajectory" in traj
    assert isinstance(traj["trajectory"], list)
    if traj["trajectory"]:
        first = traj["trajectory"][0]
        assert "step" in first
        assert "state" in first
