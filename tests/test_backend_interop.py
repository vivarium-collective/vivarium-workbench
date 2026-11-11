"""The workbench must keep running on viva-api / sms-api while it also runs on compose-api.

compose-api dispatch (#1447) was added beside the viva-api paths, not in place of them, and the deployed
workbenches still use those paths: the Stanford sites set only ``SMS_API_BASE`` plus the pinned SMS build
(``VIVARIUM_WORKBENCH_REMOTE_PINNED=1``, ``VIVARIUM_WORKBENCH_REMOTE_REPO_URL``), with no
``--backend-base-url``. Until viva-api is retired (the removal is Phase 4 of compose-api#345's plan, "executed only on
request"), this file pins the contract that keeps both working from one branch:

* the Stanford environment takes the SMS route and asks nothing of compose-api or ``/viva/v1``, even from a
  server that would answer as either;
* the pinned SMS build keeps the simulator path when compose-api is the named backend, as it already does when
  viva-v1 is (``test_backend_dispatch_viva_v1``);
* each named backend gets its own dispatch, and a viva-v1 backend wins over an ``/openapi.json`` that looks like
  compose-api's.

Removing a viva-api path should mean deleting a test here on purpose, not finding out from a deployment.
"""
from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import server_capabilities as sc

HERE = Path(__file__).parent
FIXTURE_WS = HERE / "_fixtures" / "ws_increase_demo"
COMPOSITE = "pbg_ws_increase_demo.composites.increase-demo"
COMPOSE_SPEC = json.loads((HERE / "_contracts" / "compose_api_openapi_0.7.72.json").read_text(encoding="utf-8"))
CAPS_DOC = ["viva-v1-composites", "viva-v1-composites-documents", "viva-v1-jobs", "viva-v1-surface"]
SMS_ECOLI = "https://github.com/CovertLabEcoli/sms-ecoli"
SIM_ID = 4242

ENV_VARS = ("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT",
            "VIVARIUM_WORKBENCH_REMOTE_PINNED", "VIVARIUM_WORKBENCH_REMOTE_REPO_URL",
            "VIVARIUM_WORKBENCH_REMOTE_BRANCH", "VIVA_API_BASE", "SMS_API_BASE")


class _Backend(BaseHTTPRequestHandler):
    """One server that can answer as any of the backends: what each GET returns is set per test in ``routes``.
    Anything unset is a 404, which is how every backend answers a route it does not have. POST
    ``/api/v1/simulations`` is SMS's run submission."""

    routes: dict[str, dict] = {}
    seen: list[tuple[str, str]] = []

    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        type(self).seen.append(("GET", path))
        if path in type(self).routes:
            self._reply(200, type(self).routes[path])
        else:
            self._reply(404, {"detail": "Not Found"})

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        path = self.path.split("?")[0]
        type(self).seen.append(("POST", path))
        if path == "/api/v1/simulations":
            self._reply(200, {"database_id": SIM_ID})
        else:
            self._reply(404, {"detail": "Not Found"})

    def log_message(self, *a):
        pass


VIVA_V1 = {"/viva/v1/capabilities": {"version": "0.1.9", "capabilities": CAPS_DOC},
           "/viva/v1/health": {"status": "ok", "version": "0.1.9", "services": {"composites": True}}}
COMPOSE = {"/openapi.json": COMPOSE_SPEC}


@pytest.fixture
def backend():
    _Backend.routes, _Backend.seen = {}, []
    srv = HTTPServer(("127.0.0.1", 0), _Backend)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """No backend env from the developer's shell, no GitHub login (the submit gate depends on it), and a fresh
    profile cache (it is keyed by URL and would carry one test's backend into the next)."""
    for v in ENV_VARS + ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    home = tmp_path / "no-login-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GH_CONFIG_DIR", str(home / ".config" / "gh"))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    sc._PROFILE_CACHE.clear()
    yield
    sc._PROFILE_CACHE.clear()


@pytest.fixture
def ws(tmp_path) -> Path:
    dest = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, dest)
    (dest / ".pbg").mkdir(exist_ok=True)
    sd = dest / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "demo", "schema_version": 3,
        "baseline": [{"name": "core", "composite": COMPOSITE, "params": {}}],
    }))
    return dest


def _stanford_env(monkeypatch, url: str) -> None:
    """What ``kustomize/base/workbench/workbench.yaml`` in viva-api sets on the Stanford workbenches."""
    monkeypatch.setenv("SMS_API_BASE", url)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", SMS_ECOLI)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_BRANCH", "main")


# --- the deployed configuration ------------------------------------------------------------------------------

def test_the_stanford_workbench_env_submits_to_sms_and_asks_nothing_else(ws, backend, monkeypatch, inproc_client):
    """The server answers as viva-v1 AND as compose-api. Neither is asked: an alias never names a backend."""
    _Backend.routes = {**VIVA_V1, **COMPOSE}
    _stanford_env(monkeypatch, backend)
    c = inproc_client(ws)

    cfg = c.get("/api/remote-run-config").json()
    assert "backend" not in cfg  # nothing negotiated
    res = c.post("/api/remote-run-submit", json={
        "study": "demo", "simulator_id": 66, "num_generations": 2, "num_seeds": 3})
    assert res.status_code == 202, res.text
    assert res.json()["simulation_id"] == SIM_ID

    assert ("POST", "/api/v1/simulations") in _Backend.seen
    # SmsApiClient reads /viva/v1/capabilities to pick each route's spelling on this server; that is the SMS
    # path's own read, not backend selection. Selection would read /openapi.json (compose-api detection) or send
    # the run to /viva/v1/composites (viva-v1 dispatch), and neither may happen.
    assert ("GET", "/openapi.json") not in _Backend.seen
    assert not [p for _, p in _Backend.seen if p.startswith("/viva/v1/composites")]


def test_the_stanford_env_resolves_run_targets_without_probing(ws, monkeypatch):
    from vivarium_workbench.lib import remote_pinned

    def boom(*a, **k):
        raise AssertionError("probed a backend nobody named")

    monkeypatch.setattr(sc, "backend_profile", boom)
    _stanford_env(monkeypatch, "http://127.0.0.1:9")
    assert remote_pinned.uses_compose_dispatch(ws) is False
    assert remote_pinned.uses_viva_v1_dispatch(ws) is False
    assert remote_pinned.resolve_run_target(ws) == "deployment"  # the pinned SMS build, as before #1447


# --- the pinned SMS build beats a named compose-api ----------------------------------------------------------

def test_the_pinned_sms_build_keeps_the_simulator_path_when_compose_api_is_named(ws, backend, monkeypatch):
    from vivarium_workbench.lib import remote_pinned

    _Backend.routes = dict(COMPOSE)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", backend)
    assert remote_pinned.uses_compose_dispatch(ws) is True

    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", SMS_ECOLI)
    assert remote_pinned.uses_compose_dispatch(ws) is False
    monkeypatch.delenv("VIVARIUM_WORKBENCH_REMOTE_PINNED")

    (ws / ".viv-build.json").write_text(json.dumps({"repo_url": SMS_ECOLI, "simulator_id": 1}))
    assert remote_pinned.uses_compose_dispatch(ws) is False


# --- one process, one named backend, its own dispatch --------------------------------------------------------

@pytest.mark.parametrize(("routes", "dispatch"), [
    pytest.param({}, sc.DISPATCH_LEGACY, id="sms-api-without-capabilities"),
    pytest.param({"/viva/v1/capabilities": {"version": "0.9.182", "capabilities": ["viva-v1-composites"]}},
                 sc.DISPATCH_LEGACY, id="viva-api-composites-by-id-only"),
    pytest.param(VIVA_V1, sc.DISPATCH_DOCUMENT, id="viva-v1-documents"),
    pytest.param(COMPOSE, sc.DISPATCH_COMPOSE, id="compose-api"),
    pytest.param({**VIVA_V1, **COMPOSE}, sc.DISPATCH_DOCUMENT, id="viva-v1-wins-over-a-compose-like-openapi"),
])
def test_each_named_backend_gets_its_own_dispatch(ws, backend, monkeypatch, inproc_client, routes, dispatch):
    _Backend.routes = routes
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", backend)
    profile = inproc_client(ws).get("/api/remote-run-config").json()["backend"]
    assert profile["reachable"] is True
    assert profile["dispatch"] == dispatch
