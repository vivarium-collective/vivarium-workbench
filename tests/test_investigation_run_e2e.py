"""End-to-end test of the /api/investigation-run lifecycle.

Reuses the ws_increase_demo fixture from test_composite_explorer_api.py
plus a fixture investigation in tests/_fixtures/ws_increase_demo/investigations/baseline/.
"""
import json
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path
import socket
import subprocess
import shutil
import os

import pytest

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WORKSPACE = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close()
    return p


@pytest.fixture
def server(tmp_path):
    if not FIXTURE_WORKSPACE.is_dir():
        pytest.skip(f"Fixture workspace not present at {FIXTURE_WORKSPACE}")
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WORKSPACE, ws)

    # /api/investigation-run goes through _active_branch_action(), which
    # demands an active workstream branch and a clean git tree. Bootstrap
    # both in the temp workspace so the test isn't blocked by 409s.
    # The server writes to .pbg/server/server-info on boot, and the
    # workspace's Python package generates __pycache__/ entries the moment
    # we import it; both must be gitignored so the tree stays clean.
    (ws / ".gitignore").write_text(
        ".pbg/\n__pycache__/\n*.pyc\nreports/\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "test@local"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=ws, check=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=ws, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "test-workstream"], cwd=ws, check=True)
    (ws / ".pbg").mkdir(exist_ok=True)
    (ws / ".pbg" / "state.json").write_text(
        json.dumps({"active_branch": "test-workstream"})
    )

    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ws) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_dashboard.server",
         "--workspace", str(ws), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    info_path = ws / ".pbg" / "server" / "server-info"
    for _ in range(40):
        if info_path.exists():
            break
        time.sleep(0.1)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=2)
        pytest.fail(f"server did not start:\n{out.decode()}\n{err.decode()}")
    yield {"url": f"http://127.0.0.1:{port}", "ws": ws}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait()


def _post(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read().decode())


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def test_list_includes_fixture_investigation(server):
    status, body = _get(f"{server['url']}/api/investigations")
    assert status == 200
    names = [inv["name"] for inv in body["investigations"]]
    assert "baseline" in names


def test_run_baseline_investigation(server):
    status, body = _post(f"{server['url']}/api/investigation-run", {"name": "baseline"})
    assert status == 200, body
    # Post-execution status is "ran" (runs finished without error). The
    # previous "complete" value was retired by the multi-axis status
    # redesign (Pass A) — "complete" is now reserved for the Decide-phase
    # confirmation set after evaluation, not the immediate post-run state.
    assert body["status"] == "ran"
    assert body["n_runs"] == 4  # 1 single + 3 sweep
    assert body["n_visualizations"] == 1
    db = server["ws"] / "investigations" / "baseline" / "runs.db"
    assert db.is_file()
    viz = server["ws"] / "investigations" / "baseline" / "viz" / "levels.html"
    assert viz.is_file()
    # Loose size sanity check (rejects literally-empty / 404-style stubs).
    # The previous 1000-byte threshold was empirical; the viz pipeline got
    # leaner (~674 bytes for this fixture's output) without losing the
    # Plotly content check below — that's the meaningful signal.
    assert viz.stat().st_size > 400
    assert "Plotly.newPlot" in viz.read_text()


def test_detail_after_run(server):
    _post(f"{server['url']}/api/investigation-run", {"name": "baseline"})
    status, body = _get(f"{server['url']}/api/investigation/baseline")
    assert status == 200
    # See test_run_baseline_investigation above: post-run status is "ran",
    # not "complete" (which is reserved for user-set Decide confirmation).
    assert body["spec"]["status"] == "ran"
    assert len(body["runs_summary"]) == 4
    assert any(v["name"] == "levels" for v in body["viz_files"])
