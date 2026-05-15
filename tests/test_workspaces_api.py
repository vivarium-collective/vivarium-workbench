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


def _post_json(url, payload, timeout=10):
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
    with urllib.request.urlopen(req, timeout=timeout) as r:
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


def test_post_workspaces_forget_happy_path(server, tmp_path):
    """POST /api/workspaces/forget removes a catalog entry; returns 200 + {ok: true}."""
    pbg_home = server["pbg_home"]

    # Create a workspace and add it to the catalog
    ws = tmp_path / "forget-me"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: forget-me\npackage: pbg_forget_me\n")

    # Register it via the add endpoint first
    _post_json(f"{server['url']}/api/workspaces/add", {"path": str(ws)})

    # Verify it's in the catalog
    catalog_data = json.loads((pbg_home / "workspaces.json").read_text())
    paths_before = [e["path"] for e in catalog_data["workspaces"]]
    assert str(ws.resolve()) in paths_before

    # Now forget it
    status, resp = _post_json(f"{server['url']}/api/workspaces/forget", {"path": str(ws)})
    assert status == 200
    assert resp == {"ok": True}

    # Verify it's no longer in the catalog
    catalog_data = json.loads((pbg_home / "workspaces.json").read_text())
    paths_after = [e["path"] for e in catalog_data["workspaces"]]
    assert str(ws.resolve()) not in paths_after


def test_post_workspaces_forget_refuses_running(server, tmp_path):
    """POST /api/workspaces/forget returns 409 when the workspace server is running."""
    import urllib.error

    pbg_home = server["pbg_home"]

    # Create a workspace and add it to the catalog
    ws = tmp_path / "running-forget-ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: running-forget-ws\npackage: pbg_running_forget\n")
    _post_json(f"{server['url']}/api/workspaces/add", {"path": str(ws)})

    # Register it as running with the current (alive) PID
    servers_dir = pbg_home / "servers"
    servers_dir.mkdir(parents=True, exist_ok=True)
    server_entry = {
        "name": "running-forget-ws",
        "path": str(ws.resolve()),
        "pid": os.getpid(),
        "port": 9997,
        "url": "http://127.0.0.1:9997",
    }
    (servers_dir / "running-forget-ws.json").write_text(json.dumps(server_entry))

    # Attempt to forget — should be refused with 409
    req = urllib.request.Request(
        f"{server['url']}/api/workspaces/forget",
        data=json.dumps({"path": str(ws)}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 409
    body = json.loads(exc.value.read())
    assert body["error"] == "stop the server before forgetting"


def test_post_workspaces_cleanup_stale_happy_path(server, tmp_path):
    """POST /api/workspaces/cleanup-stale removes registry entry + orphan files; returns 200."""
    pbg_home = server["pbg_home"]

    # Create a workspace on disk with orphan .pbg/server files
    ws = tmp_path / "stale-cleanup-ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: stale-cleanup-ws\npackage: pbg_stale_cleanup\n")
    ws_sdir = ws / ".pbg" / "server"
    ws_sdir.mkdir(parents=True)
    (ws_sdir / "server-info").write_text("http://127.0.0.1:9996")
    (ws_sdir / "server.pid").write_text("99996")

    # Spawn a real subprocess and wait for it to exit so we have a guaranteed-dead PID.
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead_pid = proc.pid

    # Register the stale workspace in ~/.pbg/servers/ with the dead PID
    servers_dir = pbg_home / "servers"
    servers_dir.mkdir(parents=True, exist_ok=True)
    server_entry = {
        "name": "stale-cleanup-ws",
        "path": str(ws.resolve()),
        "pid": dead_pid,
        "port": 9996,
        "url": "http://127.0.0.1:9996",
    }
    (servers_dir / "stale-cleanup-ws.json").write_text(json.dumps(server_entry))

    # POST cleanup-stale — should succeed
    status, resp = _post_json(
        f"{server['url']}/api/workspaces/cleanup-stale", {"path": str(ws)}
    )
    assert status == 200
    assert resp == {"ok": True}

    # Global registry entry is gone
    remaining = list(servers_dir.glob("stale-cleanup-ws*.json"))
    assert remaining == [], f"Expected no server files, got: {remaining}"

    # Orphan workspace-local files are removed
    assert not (ws_sdir / "server-info").exists(), "server-info should have been deleted"
    assert not (ws_sdir / "server.pid").exists(), "server.pid should have been deleted"


def test_post_workspaces_cleanup_stale_refuses_alive(server, tmp_path):
    """POST /api/workspaces/cleanup-stale returns 409 when the workspace server is alive."""
    import urllib.error

    pbg_home = server["pbg_home"]

    # Create a workspace
    ws = tmp_path / "alive-cleanup-ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: alive-cleanup-ws\npackage: pbg_alive_cleanup\n")

    # Register it as running with the current (alive) PID
    servers_dir = pbg_home / "servers"
    servers_dir.mkdir(parents=True, exist_ok=True)
    server_entry = {
        "name": "alive-cleanup-ws",
        "path": str(ws.resolve()),
        "pid": os.getpid(),
        "port": 9995,
        "url": "http://127.0.0.1:9995",
    }
    (servers_dir / "alive-cleanup-ws.json").write_text(json.dumps(server_entry))

    # Attempt cleanup — should be refused with 409
    req = urllib.request.Request(
        f"{server['url']}/api/workspaces/cleanup-stale",
        data=json.dumps({"path": str(ws)}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 409
    body = json.loads(exc.value.read())
    assert body["error"] == "server is still running"


# ---------------------------------------------------------------------------
# POST /api/workspaces/start tests
# ---------------------------------------------------------------------------


def test_post_workspaces_start_spawns_dashboard(server, tmp_path):
    """Posting start for a stopped workspace should spawn `vivarium-dashboard
    serve` and return its URL once it has registered itself."""
    import urllib.error

    # Create a minimal workspace that cmd_serve can handle.
    other_ws = tmp_path / "start-target"
    other_ws.mkdir()
    (other_ws / "workspace.yaml").write_text(
        "name: start-target\npackage: pbg_start_target\n"
    )
    # cmd_serve renders reports; the directory must exist.
    (other_ws / "reports").mkdir()

    # Add the workspace to the catalog via the running server (shares PBG_HOME).
    _post_json(
        f"{server['url']}/api/workspaces/add",
        {"path": str(other_ws)},
    )

    spawned_pid = None
    try:
        status, resp = _post_json(
            f"{server['url']}/api/workspaces/start",
            {"path": str(other_ws)},
            timeout=15,
        )
        assert status == 200, f"Expected 200, got {status}: {resp}"
        assert resp["url"].startswith("http://127.0.0.1:"), (
            f"url looks wrong: {resp['url']}"
        )
        assert isinstance(resp["pid"], int) and resp["pid"] > 0, (
            f"pid looks wrong: {resp['pid']}"
        )
        spawned_pid = resp["pid"]
    finally:
        if spawned_pid:
            try:
                os.kill(spawned_pid, 15)  # SIGTERM
            except ProcessLookupError:
                pass
            # Poll for the child's atexit to unregister itself. Up to 3 s.
            # workspace_catalog.find_entry uses PBG_HOME from os.environ, which
            # is NOT set in the test process — so check the servers/ directory
            # directly via the fixture's pbg_home path instead.
            pbg_home = server["pbg_home"]
            servers_dir = pbg_home / "servers"
            ws_name = "start-target"
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                remaining = list(servers_dir.glob(f"{ws_name}*.json"))
                if not remaining:
                    break
                time.sleep(0.1)


def test_post_workspaces_start_refuses_arbitrary_path(server, tmp_path):
    """Paths not in the catalog must be refused (safety guard)."""
    import urllib.error

    not_in_catalog = tmp_path / "uncatalogued"
    not_in_catalog.mkdir()
    (not_in_catalog / "workspace.yaml").write_text(
        "name: uncatalogued\npackage: pbg_uncatalogued\n"
    )
    # Deliberately do NOT add it to the catalog.

    req = urllib.request.Request(
        f"{server['url']}/api/workspaces/start",
        data=json.dumps({"path": str(not_in_catalog)}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 400
    body = json.loads(exc.value.read())
    assert "catalog" in body["error"]


def test_post_workspaces_start_returns_existing_url_if_live(server, tmp_path):
    """If a live entry already exists for the path, the handler returns
    immediately (idempotent) without spawning a new process."""
    pbg_home = server["pbg_home"]

    other_ws = tmp_path / "already-up"
    other_ws.mkdir()
    (other_ws / "workspace.yaml").write_text(
        "name: already-up\npackage: pbg_already_up\n"
    )

    # Add to catalog via the running server.
    _post_json(
        f"{server['url']}/api/workspaces/add",
        {"path": str(other_ws)},
    )

    # Write a live servers/ entry using os.getpid() (this process is alive).
    servers_dir = pbg_home / "servers"
    servers_dir.mkdir(parents=True, exist_ok=True)
    server_entry = {
        "name": "already-up",
        "path": str(other_ws.resolve()),
        "pid": os.getpid(),
        "port": 8006,
        "url": "http://127.0.0.1:8006",
    }
    (servers_dir / "already-up.json").write_text(json.dumps(server_entry))

    status, resp = _post_json(
        f"{server['url']}/api/workspaces/start",
        {"path": str(other_ws)},
    )
    assert status == 200
    assert resp["url"] == "http://127.0.0.1:8006"
    assert resp["pid"] == os.getpid()

    # Verify no spawn occurred: start.log must NOT exist (it's only created
    # by the Popen branch, which the idempotent path skips).
    assert not (other_ws / ".pbg" / "server" / "start.log").exists(), \
        "handler should NOT spawn a process when a live entry already exists"
