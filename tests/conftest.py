"""Test config for vivarium-dashboard.

The dashboard package itself is import-able from the venv (``pip install -e .``)
so we don't need to munge sys.path for ``vivarium_workbench.*``. We do need
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
    def __init__(self, status_code: int, body: bytes, headers=None):
        self.status_code = status_code
        self._body = body
        # Case-insensitive header map (keys lowercased) so tests can assert
        # Content-Type / Content-Disposition regardless of the server's casing.
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}

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
                return _Response(r.status, r.read(), headers=r.headers)
        except urllib.error.HTTPError as e:
            return _Response(e.code, e.read(), headers=e.headers)

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
        # Spawn the live FastAPI app (via the `serve` CLI -> startup.serve_fastapi,
        # which writes the .pbg/server/server-info readiness file this fixture waits
        # on). This exercises the production server, not the retired stdlib server.py.
        proc = subprocess.Popen(
            [sys.executable, "-m", "vivarium_workbench.cli", "serve",
             "--workspace", str(workspace), "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )
        procs.append(proc)
        client = _Client(f"http://127.0.0.1:{port}")
        # serve_fastapi writes server-info before uvicorn binds the port, so wait
        # for the app to actually answer /health — not just for the file to exist.
        for _ in range(60):
            if proc.poll() is not None:  # process died during startup
                out, err = proc.communicate(timeout=2)
                pytest.fail(
                    f"server exited during startup (code {proc.returncode}):\n"
                    f"stdout:\n{out.decode()}\nstderr:\n{err.decode()}"
                )
            try:
                if client.get("/health").status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            proc.terminate()
            out, err = proc.communicate(timeout=2)
            pytest.fail(
                f"server did not answer /health within 15s:\n"
                f"stdout:\n{out.decode()}\nstderr:\n{err.decode()}"
            )
        return client

    yield _make

    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


# ---------------------------------------------------------------------------
# Minimal workspace fixture for study-run tests (dry-run guard, CLI, etc.)
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_study_ws(tmp_path):
    """Return (ws_path, study_slug) for a minimal workspace with one baseline study.

    The workspace has:
      - workspace.yaml  (name + package_path)
      - studies/<slug>/study.yaml  (v4 schema, conditions.baseline.composite,
                                    params: {n_steps: 5}, one variant)
    The composite id is intentionally un-registered (tests that use dry_run
    never reach composite resolution; tests that need resolution must mock it).
    """
    import yaml as _yaml

    ws = tmp_path / "test_ws"
    slug = "demo-study"
    pkg = "pbg_demo"
    composite_id = f"{pkg}.composites.demo"

    # workspace.yaml
    (ws).mkdir(parents=True)
    (ws / "workspace.yaml").write_text(
        _yaml.safe_dump({"name": "demo", "package_path": pkg}),
        encoding="utf-8",
    )

    # studies/<slug>/study.yaml  (v4 shape with conditions block)
    study_dir = ws / "studies" / slug
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        _yaml.safe_dump({
            "schema_version": 4,
            "name": slug,
            "question": "Does the demo composite run correctly?",
            "conditions": {
                "baseline": {
                    "composite": composite_id,
                    "params": {"n_steps": 5},
                },
                "variants": [
                    {
                        "name": "var-one",
                        "composite": composite_id,
                        "parameter_overrides": {"n_steps": 10},
                    }
                ],
            },
        }),
        encoding="utf-8",
    )

    return ws, slug


@pytest.fixture
def fixture_study_with_recorded_run(fixture_study_ws):
    """Return (ws_path, study_slug, run_id) with a recorded run in runs.db.

    Builds on fixture_study_ws and seeds the study's runs.db with one
    completed run via composite_runs helpers so find_run / list_study_runs
    can locate it without spinning up a real simulation.
    """
    import time
    from vivarium_workbench.lib import composite_runs as cr

    ws, slug = fixture_study_ws
    study_dir = ws / "studies" / slug
    db_file = str(study_dir / "runs.db")

    spec_id = "pbg_demo.composites.demo"
    run_id = cr.generate_run_id(spec_id, {"seed": 42})
    conn = cr.connect(db_file)
    try:
        cr.save_metadata(
            conn,
            spec_id=spec_id,
            run_id=run_id,
            params={"seed": 42},
            label="baseline",
            started_at=time.time(),
            n_steps=5,
        )
        cr.complete_metadata(conn, run_id=run_id, n_steps=5, status="complete")
    finally:
        conn.close()

    return ws, slug, run_id
