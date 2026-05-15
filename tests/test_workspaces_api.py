"""Tests for GET /api/workspaces — workspace switcher dropdown endpoint."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
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


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


@pytest.fixture
def server(tmp_path):
    """Spin up the dashboard against the fixture workspace with an isolated PBG_HOME."""
    if not FIXTURE_WORKSPACE.is_dir():
        pytest.skip(f"Fixture workspace not present at {FIXTURE_WORKSPACE}")
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WORKSPACE, ws)
    pbg_home = tmp_path / "pbg-home"
    pbg_home.mkdir()
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_REPO_ROOT), str(ws), env.get("PYTHONPATH", "")]
    )
    env["PBG_HOME"] = str(pbg_home)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "vivarium_dashboard.server",
            "--workspace", str(ws), "--port", str(port),
        ],
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
        pytest.fail(
            f"server did not start:\nstdout:\n{out.decode()}\nstderr:\n{err.decode()}"
        )
    yield {"url": f"http://127.0.0.1:{port}", "ws": ws, "pbg_home": pbg_home}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def test_empty_catalog_returns_current_workspace(server):
    """With an empty catalog, the endpoint returns only the current workspace as 'current'."""
    status, body = _get(f"{server['url']}/api/workspaces")
    assert status == 200

    current = body["current"]
    assert current["name"] == "ws_increase_demo"
    assert current["path"] == str(server["ws"].resolve())

    workspaces = body["workspaces"]
    assert len(workspaces) == 1
    ws0 = workspaces[0]
    assert ws0["status"] == "current"
    assert ws0["path"] == str(server["ws"].resolve())
    assert ws0["name"] == "ws_increase_demo"


def test_catalog_with_running_stopped_missing(server, tmp_path):
    """Catalog entries with different states are reflected in the response.

    We pre-populate ~/.pbg/workspaces.json with three extra entries:
    - A 'running' workspace (registered in servers/ with the current PID)
    - A 'stopped' workspace (in catalog only, no servers/ entry)
    - A 'missing' workspace (path doesn't exist)

    Then we verify that the endpoint sorts them: current → running → stopped → missing.
    """
    pbg_home = server["pbg_home"]

    # Build a second workspace on disk (will be 'running')
    running_ws = tmp_path / "running-ws"
    running_ws.mkdir()
    (running_ws / "workspace.yaml").write_text("name: running-ws\npackage: pbg_running_ws\n")

    # Build a third workspace on disk (will be 'stopped')
    stopped_ws = tmp_path / "stopped-ws"
    stopped_ws.mkdir()
    (stopped_ws / "workspace.yaml").write_text("name: stopped-ws\npackage: pbg_stopped_ws\n")

    # Path that does NOT exist (will be 'missing')
    missing_path = tmp_path / "ghost-ws"

    # Write ~/.pbg/workspaces.json
    catalog_path = pbg_home / "workspaces.json"
    catalog_data = {
        "workspaces": [
            {
                "name": "running-ws",
                "path": str(running_ws.resolve()),
                "package": "pbg_running_ws",
                "added_at": "2026-01-01T00:00:00",
            },
            {
                "name": "stopped-ws",
                "path": str(stopped_ws.resolve()),
                "package": "pbg_stopped_ws",
                "added_at": "2026-01-01T00:00:00",
            },
            {
                "name": "ghost-ws",
                "path": str(missing_path),
                "package": None,
                "added_at": "2026-01-01T00:00:00",
            },
        ]
    }
    catalog_path.write_text(json.dumps(catalog_data))

    # Register the 'running-ws' in servers/ using the current (alive) PID
    servers_dir = pbg_home / "servers"
    servers_dir.mkdir(parents=True, exist_ok=True)
    server_entry = {
        "name": "running-ws",
        "path": str(running_ws.resolve()),
        "pid": os.getpid(),
        "port": 9999,
        "url": "http://127.0.0.1:9999",
    }
    (servers_dir / "running-ws.json").write_text(json.dumps(server_entry))

    status, body = _get(f"{server['url']}/api/workspaces")
    assert status == 200

    workspaces = body["workspaces"]
    by_name = {w["name"]: w for w in workspaces}

    # Current workspace is present and sorted first
    assert "ws_increase_demo" in by_name
    assert by_name["ws_increase_demo"]["status"] == "current"

    # running-ws detected as running
    assert "running-ws" in by_name
    assert by_name["running-ws"]["status"] == "running"
    assert by_name["running-ws"]["url"] == "http://127.0.0.1:9999"

    # stopped-ws: in catalog, no servers entry
    assert "stopped-ws" in by_name
    assert by_name["stopped-ws"]["status"] == "stopped"

    # ghost-ws: path doesn't exist
    assert "ghost-ws" in by_name
    assert by_name["ghost-ws"]["status"] == "missing"

    # Verify sort order: current → running → stopped → missing
    statuses = [w["status"] for w in workspaces]
    order = {"current": 0, "running": 1, "stopped": 2, "stale": 3, "missing": 4}
    assert statuses == sorted(statuses, key=lambda s: order.get(s, 99)), (
        f"Sort order wrong: {statuses}"
    )


def test_catalog_with_stale_entry(server, tmp_path):
    """A workspace with a server-registry file but a dead PID should report status 'stale'."""
    pbg_home = server["pbg_home"]

    # Spawn a real subprocess and wait for it to exit so we have a guaranteed-dead PID.
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    dead_pid = p.pid

    # Make a workspace that exists on disk but whose registered server has died.
    stale_ws = tmp_path / "stale-ws"
    stale_ws.mkdir()
    (stale_ws / "workspace.yaml").write_text("name: stale-ws\npackage: pbg_stale\n")

    # Write ~/.pbg/workspaces.json with the stale workspace entry.
    catalog_path = pbg_home / "workspaces.json"
    catalog_data = {
        "workspaces": [
            {
                "name": "stale-ws",
                "path": str(stale_ws.resolve()),
                "package": "pbg_stale",
                "added_at": "2026-01-01T00:00:00",
            },
        ]
    }
    catalog_path.write_text(json.dumps(catalog_data))

    # Register the stale workspace in servers/ using the confirmed-dead PID.
    servers_dir = pbg_home / "servers"
    servers_dir.mkdir(parents=True, exist_ok=True)
    server_entry = {
        "name": "stale-ws",
        "path": str(stale_ws.resolve()),
        "pid": dead_pid,
        "port": 9998,
        "url": "http://127.0.0.1:9998",
    }
    (servers_dir / "stale-ws.json").write_text(json.dumps(server_entry))

    status, body = _get(f"{server['url']}/api/workspaces")
    assert status == 200

    workspaces = body["workspaces"]
    by_name = {w["name"]: w for w in workspaces}

    assert "stale-ws" in by_name, f"stale-ws not in response: {by_name}"
    stale_row = by_name["stale-ws"]
    assert stale_row["status"] == "stale", (
        f"Expected 'stale', got '{stale_row['status']}'"
    )
    assert stale_row["pid"] == dead_pid, (
        f"Expected pid={dead_pid}, got pid={stale_row.get('pid')}"
    )


def _post_json(url, payload):
    """POST JSON to url; return (status_code, response_dict).
    Raises urllib.error.HTTPError on 4xx/5xx (caller can catch and read body).
    """
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def test_post_workspaces_add(server, tmp_path):
    """POST /api/workspaces/add registers a valid workspace and returns its catalog entry."""
    import urllib.error

    new_ws = tmp_path / "added-ws"
    new_ws.mkdir()
    (new_ws / "workspace.yaml").write_text("name: added-ws\npackage: pbg_added_ws\n")

    status, resp = _post_json(f"{server['url']}/api/workspaces/add", {"path": str(new_ws)})
    assert status == 200
    assert resp["name"] == "added-ws"
    assert resp["path"] == str(new_ws.resolve())

    # Verify the catalog on disk was updated (pbg_home is shared with the subprocess)
    catalog_path = server["pbg_home"] / "workspaces.json"
    catalog_data = json.loads(catalog_path.read_text())
    paths = [e["path"] for e in catalog_data["workspaces"]]
    assert str(new_ws.resolve()) in paths


def test_post_workspaces_add_rejects_non_workspace(server, tmp_path):
    """POST /api/workspaces/add returns 400 when the path has no workspace.yaml."""
    import urllib.error

    bogus = tmp_path / "no-yaml-here"
    bogus.mkdir()

    req = urllib.request.Request(
        f"{server['url']}/api/workspaces/add",
        data=json.dumps({"path": str(bogus)}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 400
    body = json.loads(exc.value.read())
    assert "workspace.yaml" in body["error"]
