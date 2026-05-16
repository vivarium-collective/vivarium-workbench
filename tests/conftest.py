"""Test config for vivarium-dashboard.

The dashboard package itself is import-able from the venv (``pip install -e .``)
so we don't need to munge sys.path for ``vivarium_dashboard.*``. We do need
the fixture workspaces (``_fixtures/<name>/<pbg_pkg>``) on sys.path for
end-to-end tests that import the workspace's own package.
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "_fixtures"
for fixture_ws in _FIXTURES.iterdir() if _FIXTURES.is_dir() else []:
    if fixture_ws.is_dir() and (fixture_ws / "workspace.yaml").exists():
        p = str(fixture_ws)
        if p not in sys.path:
            sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Shared dashboard_client fixture
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Response:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    def json(self):
        return json.loads(self._body.decode())

    @property
    def text(self):
        return self._body.decode()


class _Client:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def _request(self, method: str, path: str, *, json_body=None):
        import urllib.request
        import urllib.error
        url = self.base_url + path
        data = json.dumps(json_body).encode() if json_body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return _Response(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Response(e.code, e.read())

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, json=None):
        return self._request("POST", path, json_body=json)

    def delete(self, path: str, json=None):
        return self._request("DELETE", path, json_body=json)


@pytest.fixture
def dashboard_client():
    """Factory: dashboard_client(workspace=path) -> _Client.

    Spawns a subprocess server against the given workspace and tears it
    down at the end of the test.  Future endpoint tests (Tasks 8-11) reuse
    this fixture via conftest.py.
    """
    procs = []

    def _make(workspace: Path) -> _Client:
        workspace = Path(workspace)
        port = _free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(_REPO_ROOT), str(workspace), env.get("PYTHONPATH", "")])
        proc = subprocess.Popen(
            [sys.executable, "-m", "vivarium_dashboard.server",
             "--workspace", str(workspace), "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )
        procs.append(proc)
        info_path = workspace / ".pbg" / "server" / "server-info"
        for _ in range(40):
            if info_path.exists():
                break
            time.sleep(0.25)
        else:
            proc.terminate()
            out, err = proc.communicate(timeout=2)
            pytest.fail(
                f"server did not start within 10s:\n"
                f"stdout:\n{out.decode()}\nstderr:\n{err.decode()}"
            )
        return _Client(f"http://127.0.0.1:{port}")

    yield _make

    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
