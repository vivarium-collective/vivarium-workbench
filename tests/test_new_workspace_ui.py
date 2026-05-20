"""Phase B of todo #8: '+ New Workspace…' form modal in the workspace switcher.

These tests boot the dashboard in workspaceless mode and grep the served
``workspace-switcher.js`` + ``style.css`` for the wiring that the JS-side
form depends on. The browser-driven form itself is not exercised here
(no JS runner is configured in this repo) — the test guards against a
regression where the button or the create-form structure silently disappears.

Also asserts that ``POST /api/workspaces/create`` is on the workspaceless
allowlist (returns 404 because Phase C's handler isn't wired yet, not 409),
so the frontend can POST to it without first binding a workspace.
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


def test_switcher_js_includes_new_workspace_button(workspaceless_server):
    status, body = _request(workspaceless_server, "/assets/workspace-switcher.js")
    assert status == 200
    js = body.decode("utf-8")
    # The footer carries both the existing Add button and the new Create
    # button. Phase B wired the latter.
    assert "+ New Workspace…" in js
    assert "viv-ws-modal-new" in js
    assert "openCreate" in js
    assert "submitCreate" in js


def test_switcher_js_carries_validation_helpers(workspaceless_server):
    """The slug regex, backend list, and org normaliser are required by the
    form to validate client-side before posting."""
    status, body = _request(workspaceless_server, "/assets/workspace-switcher.js")
    assert status == 200
    js = body.decode("utf-8")
    assert "SLUG_RE" in js
    assert "BACKENDS" in js
    assert "normaliseOrg" in js
    # The backend dropdown must offer the two values from the plan.
    assert "'local'" in js
    assert "'hpc:ccam'" in js


def test_style_css_has_create_form_rules(workspaceless_server):
    """Modal + form styles were lifted into style.css so the landing page
    can render the switcher and create-form modal without inline CSS."""
    status, body = _request(workspaceless_server, "/assets/style.css")
    assert status == 200
    css = body.decode("utf-8")
    # Modal shell (lifted from index.html.j2 — landing page now reads it from
    # style.css too).
    assert ".viv-ws-modal" in css
    assert ".viv-ws-modal-card" in css
    # New rules added for the create form.
    assert ".viv-ws-create-form" in css
    assert ".viv-ws-create-submit" in css
    assert ".viv-ws-create-error" in css
    assert ".viv-ws-modal-create" in css


def test_create_endpoint_is_allowlisted_workspaceless(workspaceless_server):
    """The frontend POSTs to /api/workspaces/create from the landing page
    (before any workspace is bound). The workspaceless dispatch guard must
    let that through to the Phase-C handler. We probe with an *invalid*
    backend so the request short-circuits at input validation (400) instead
    of actually scaffolding a workspace on disk — proves both that the
    route is allowlisted AND that the handler exists, without side-effects."""
    status, body = _request(
        workspaceless_server, "/api/workspaces/create", method="POST",
        json_body={"name": "demo-ws", "backend": "k8s-not-real"},
    )
    payload = json.loads(body)
    assert status == 400, f"expected 400 (handler rejects bad backend), got {status} {payload}"
    assert "unknown backend" in payload["error"]
    assert "allowed" in payload


def test_non_allowlisted_post_still_409(workspaceless_server):
    """Sanity check the guard didn't accidentally let everything through:
    a workspace-required POST is still 409'd in workspaceless mode."""
    status, body = _request(
        workspaceless_server, "/api/visualization", method="POST", json_body={},
    )
    payload = json.loads(body)
    assert status == 409
    assert payload == {"error": "no workspace bound"}
