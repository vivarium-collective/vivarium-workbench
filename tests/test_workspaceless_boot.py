"""Phase A of todo #8: ``vivarium-dashboard serve`` with no ``--workspace``
runs in launcher / workspaceless mode.

These tests spawn the server as a subprocess (same pattern as
``conftest.dashboard_client``) so the assertions exercise the real dispatch
chain — not just the pure-helper level. Workspaceless boot doesn't write a
workspace-local ``server-info`` file (there is no workspace), so we poll the
port directly instead of waiting on a file.
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"server on port {port} did not accept connections within {timeout}s")


def _request(port: int, path: str, *, method: str = "GET", json_body=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(json_body).encode() if json_body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture
def workspaceless_server():
    """Spawn ``vivarium-dashboard.server`` with no ``--workspace`` and yield the port."""
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(_REPO_ROOT), env.get("PYTHONPATH", "")])
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_dashboard.server", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        _wait_for_port(port)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_get_root_returns_landing_page(workspaceless_server):
    """``GET /`` serves the workspaceless landing page (not workspace/reports/index.html)."""
    status, body = _request(workspaceless_server, "/")
    assert status == 200
    text = body.decode("utf-8")
    assert "No workspace bound" in text
    assert 'id="viv-workspace-switcher-trigger"' in text


def test_workspace_required_routes_409(workspaceless_server):
    """Routes that need a bound workspace 409 with a structured error."""
    for path in ("/api/registry", "/api/state", "/api/pending", "/api/catalog"):
        status, body = _request(workspaceless_server, path)
        assert status == 409, f"{path} returned {status}, expected 409"
        assert json.loads(body) == {"error": "no workspace bound"}


def test_post_to_non_allowlisted_route_409(workspaceless_server):
    """POST to a route that requires a workspace 409s."""
    status, body = _request(
        workspaceless_server, "/api/visualization", method="POST", json_body={},
    )
    assert status == 409
    assert json.loads(body) == {"error": "no workspace bound"}


def test_workspaces_endpoint_reachable(workspaceless_server):
    """``GET /api/workspaces`` is on the allowlist; reports ``current: null``."""
    status, body = _request(workspaceless_server, "/api/workspaces")
    assert status == 200
    payload = json.loads(body)
    assert payload["current"] is None
    assert isinstance(payload["workspaces"], list)


def test_bundled_static_assets_served(workspaceless_server):
    """Bundled package-static (style.css, workspace-switcher.js) is served — needed
    by the landing page's <link>/<script> tags."""
    for path in ("/assets/style.css", "/assets/workspace-switcher.js"):
        status, body = _request(workspaceless_server, path)
        assert status == 200, f"{path} returned {status}"
        assert len(body) > 0


def test_unknown_static_path_409(workspaceless_server):
    """Paths that don't match a known static asset 409 — they're not workspace-relative."""
    status, _ = _request(workspaceless_server, "/reports/missing-file.html")
    assert status == 409
