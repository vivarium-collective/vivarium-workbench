"""The workbench, run as a real server, dispatching through ``POST /viva/v1/composites``.

The backend is ``VivaV1Stub``: routes, request validation and default answers generated from
viva-core's own OpenAPI (see ``tests/_viva_v1_stub.py``), so what the workbench sends is checked
against the published contract, not against this file's idea of it.

What these greens prove: the request bodies/paths/queries the workbench produces are accepted by
the contract; the UI routes (config / submit / poll / progress / cancel / study-run) wire through;
with NO named backend, or a backend without the document run surface, nothing changes.
They do NOT prove a real deployment runs a workspace's document (its image's processes, allow-lists,
auth) -- only a real run can; nothing here talks to one.
"""
from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from _viva_v1_stub import VivaV1Stub
from vivarium_workbench.lib import server_capabilities as sc

FIXTURE_WS = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
COMPOSITE = "pbg_ws_increase_demo.composites.increase-demo"
CAPS_DOC = ["viva-v1-composites", "viva-v1-composites-documents", "viva-v1-jobs", "viva-v1-surface"]
RUN_ID = "simulation-ABC1234"


def _run(status: str = "running", **over) -> dict:
    base = {"id": RUN_ID, "status": status, "spec": "document", "environment": {"name": "runtime"},
            "composite_id": None, "params": {}, "execution": {"protocol": None, "options": {}},
            "created_at": "2026-01-01T00:00:00Z", "job_id": "compose:38"}
    return {**base, **over}


@pytest.fixture(autouse=True)
def _fresh_profile_cache():
    sc._PROFILE_CACHE.clear()
    yield
    sc._PROFILE_CACHE.clear()


@pytest.fixture
def clean_backend_env(monkeypatch):
    for v in ("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT",
              "VIVARIUM_WORKBENCH_REMOTE_PINNED", "VIVARIUM_WORKBENCH_REMOTE_REPO_URL"):
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


@pytest.fixture
def stub():
    s = VivaV1Stub()
    s.url = s.start()
    s.respond("GET", "/viva/v1/capabilities", 200, {"version": "0.1.9", "capabilities": CAPS_DOC})
    s.respond("GET", "/viva/v1/health", 200, {"status": "ok", "version": "0.1.9", "services": {"composites": True}})
    s.respond("POST", "/viva/v1/composites", 202, _run())
    s.respond("GET", "/viva/v1/composites/{id}/status", 200, {"id": RUN_ID, "status": "running", "message": None})
    yield s
    s.stop()


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


def _name_backend(monkeypatch, url: str, environment: str | None = "id:env-1") -> None:
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", url)
    if environment:
        monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT", environment)


# --- (a) no named backend: nothing changes, nothing is asked ----------------------------------

def test_without_a_named_backend_the_config_and_target_are_exactly_as_before(
        ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    """The alias env points at a document-capable backend; since it is an ALIAS (not the flag),
    the workbench neither probes its /viva/v1 surface nor moves any run there."""
    monkeypatch.setenv("VIVA_API_BASE", stub.url)
    c = dashboard_client(ws)
    cfg = c.get("/api/remote-run-config")
    assert cfg.status_code == 200
    assert cfg.json() == {"pinned": False, "deployment": "smsvpctest"}  # no `backend` key at all
    assert c.get("/api/remote-dispatch-preflight").json()["target"] == "local"
    assert [x for x in stub.calls if x.path.startswith(("/viva/v1/capabilities", "/viva/v1/composites"))] == []


def test_resolve_run_target_makes_no_network_call_without_a_named_backend(tmp_path, clean_backend_env, monkeypatch):
    from vivarium_workbench.lib import remote_pinned

    def boom(*a, **k):
        raise AssertionError("probed a backend nobody named")

    monkeypatch.setattr(sc, "backend_profile", boom)
    (tmp_path / "workspace.yaml").write_text("name: w\n")
    assert remote_pinned.resolve_run_target(tmp_path) == "local"
    assert remote_pinned.uses_viva_v1_dispatch(tmp_path) is False


# --- (b) a backend WITHOUT the run surface keeps the legacy path -------------------------------

class _Legacy(BaseHTTPRequestHandler):
    """An older viva-api: no /viva/v1, no capabilities routes; /api/v1/simulations only."""
    seen: list[tuple[str, str]] = []

    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        type(self).seen.append(("GET", self.path))
        self._reply(404, {"detail": "Not Found"})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        type(self).seen.append(("POST", self.path))
        self._reply(200, {"database_id": 4242} if self.path.startswith("/api/v1/simulations") else {"detail": "nope"})

    def log_message(self, *a):
        pass


@pytest.fixture
def legacy_backend():
    _Legacy.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Legacy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Legacy
    srv.shutdown()
    srv.server_close()


def test_named_backend_without_capabilities_route_stays_legacy(ws, clean_backend_env, dashboard_client,
                                                               monkeypatch, legacy_backend):
    url, handler = legacy_backend
    _name_backend(monkeypatch, url, environment=None)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "https://github.com/vivarium-collective/v2ecoli")
    c = dashboard_client(ws)
    cfg = c.get("/api/remote-run-config").json()
    assert cfg["backend"]["dispatch"] == "legacy" and cfg["backend"]["reachable"] is True
    res = c.post("/api/remote-run-submit", json={
        "study": "demo", "simulator_id": 66, "num_generations": 2, "num_seeds": 3})
    assert res.status_code == 202, res.text
    assert (res.json()["simulation_id"], res.json()["phase"]) == (4242, "running")
    assert "run_id" not in res.json()  # the legacy response shape
    assert ("POST", "/api/v1/simulations") in {(m, p.split("?")[0]) for m, p in handler.seen}
    assert not any("/viva/v1/composites" in p for _, p in handler.seen)


def test_named_backend_without_documents_capability_stays_legacy(ws, stub, clean_backend_env, dashboard_client,
                                                                 monkeypatch):
    stub.respond("GET", "/viva/v1/capabilities", 200, {"version": "0.1.9", "capabilities": ["viva-v1-composites"]})
    _name_backend(monkeypatch, stub.url)
    c = dashboard_client(ws)
    assert c.get("/api/remote-run-config").json()["backend"]["dispatch"] == "legacy"
    assert c.get("/api/remote-dispatch-preflight").json()["target"] == "local"
    # not hijacked: with no simulator id and no auth the legacy gate answers exactly as it always did
    res = c.post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 401 and res.json() == {"error": "not authenticated"}
    assert stub.calls_to("viva-create-composite-run") == []


def test_pinned_and_session_build_keep_the_simulator_path_even_with_a_named_backend(
        ws, stub, clean_backend_env, monkeypatch):
    from vivarium_workbench.lib import remote_pinned

    _name_backend(monkeypatch, stub.url)
    assert remote_pinned.uses_viva_v1_dispatch(ws) is True
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "https://github.com/o/r")
    assert remote_pinned.uses_viva_v1_dispatch(ws) is False
    monkeypatch.delenv("VIVARIUM_WORKBENCH_REMOTE_PINNED")
    (ws / ".viv-build.json").write_text(json.dumps({"repo_url": "https://github.com/o/r", "simulator_id": 1}))
    assert remote_pinned.uses_viva_v1_dispatch(ws) is False


# --- a backend that runs documents -------------------------------------------------------------

def test_config_and_preflight_report_the_negotiated_backend(ws, stub, clean_backend_env, dashboard_client,
                                                            monkeypatch):
    _name_backend(monkeypatch, stub.url)
    c = dashboard_client(ws)
    backend = c.get("/api/remote-run-config").json()["backend"]
    assert backend["dispatch"] == "viva-v1-document"
    assert "viva-v1-composites-documents" in backend["capabilities"]
    assert backend["services"] == {"composites": True}
    assert "url" not in json.dumps(backend).lower() or stub.url not in json.dumps(backend)


def test_submit_poll_progress_cancel_over_a_real_server(ws, stub, clean_backend_env, dashboard_client,
                                                        monkeypatch):
    stub.respond("GET", "/viva/v1/composites/{id}/progress", 200, {
        "id": RUN_ID, "status": "running", "total": 4,
        "by_kind": {"simulation": {"completed": 1, "running": 2, "failed": 1}}})
    stub.respond("DELETE", "/viva/v1/composites/{id}", 202, {"run": _run("cancelled"), "pending": "dispatch"})
    _name_backend(monkeypatch, stub.url)
    c = dashboard_client(ws)

    res = c.post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 202, res.text
    assert res.json() == {"run_id": RUN_ID, "phase": "running", "backend": "viva-v1"}

    (call,) = stub.calls_to("viva-create-composite-run")
    assert call.status == 202  # the stub validated this body against the contract (strict)
    assert call.body["environment"] == {"id": "env-1"}
    assert call.body["document"]["state"]  # the REAL exported workspace composite
    assert call.body["execution"]["options"]["interval_time"] == 1.0
    assert "composite" not in call.body  # a document run, never a composite id the backend may not serve

    poll = c.get(f"/api/remote-run-poll?run_id={RUN_ID}").json()
    assert (poll["phase"], poll["run_id"]) == ("running", RUN_ID)

    prog = c.get(f"/api/remote-run-chain-progress?run_id={RUN_ID}").json()
    assert (prog["seeds_total"], prog["seeds_succeeded"], prog["seeds_failed"], prog["seeds_in_progress"]) == (4, 1, 1, 2)
    assert prog["terminal"] is False and prog["simulation_id"] == RUN_ID

    cancel = c.post("/api/remote-run-cancel", json={"simulation_id": RUN_ID})
    assert cancel.status_code == 200 and cancel.json()["run_id"] == RUN_ID
    assert [x.method for x in stub.calls if x.method == "DELETE"] == ["DELETE"]


def test_study_run_baseline_dispatches_through_the_run_surface(ws, stub, clean_backend_env, dashboard_client,
                                                               monkeypatch):
    _name_backend(monkeypatch, stub.url)
    c = dashboard_client(ws)
    res = c.post("/api/study-run-baseline", json={"study": "demo", "overrides": {}})
    assert res.status_code == 202, res.text
    assert res.json()["run_id"] == RUN_ID
    assert len(stub.calls_to("viva-create-composite-run")) == 1


def test_upstream_refusal_is_a_502_with_the_real_reason(ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    orig = stub.handle  # 409 carries no JSON schema in the contract, so serve it raw

    def refuse(method, raw_path, raw_body):
        if method == "POST" and raw_path.startswith("/viva/v1/composites"):
            return 409, "application/json", json.dumps({"detail": "environment env-1 is building, not ready"}).encode()
        return orig(method, raw_path, raw_body)

    stub.handle = refuse
    _name_backend(monkeypatch, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 502
    assert "building, not ready" in res.json()["error"] and res.json()["reachable"] is False


# --- run_remote (Composites tab / detached runner) ---------------------------------------------

def test_run_remote_runs_a_document_and_lands_the_compose_results(ws, stub, clean_backend_env, monkeypatch,
                                                                  tmp_path):
    from vivarium_workbench.lib import remote_run

    stub.respond("GET", "/viva/v1/composites/{id}/status", 200, {"id": RUN_ID, "status": "completed", "message": None})
    _name_backend(monkeypatch, stub.url, environment=None)  # default: the site-named `runtime` environment
    monkeypatch.setattr(remote_run, "git_pip_url", lambda ws_root: "git+https://github.com/o/r.git@abc")
    monkeypatch.setattr(remote_run, "workspace_pinned_deps", lambda ws_root: [])
    monkeypatch.setattr("vivarium_workbench.lib.preflight.preflight_composite_run",
                        lambda *a, **k: type("R", (), {"summary": lambda self: "ok"})())
    out = remote_run.run_remote(ws, COMPOSITE, dest=tmp_path / "out", n_steps=5, poll_interval=0.01)

    (call,) = stub.calls_to("viva-create-composite-run")
    assert call.body["environment"] == {"name": "runtime"}
    assert call.body["execution"]["options"] == {
        "interval_time": 5.0, "extra_pip_deps": ["git+https://github.com/o/r.git@abc"]}
    assert out.is_file()
    # the output was fetched from the compose simulation the run became (job_id compose:38),
    # at the /viva/v1 spelling the backend advertises (viva-v1-surface)
    assert [c.path for c in stub.calls if "results" in c.path] == ["/viva/v1/compose/simulation/38/results"]


def test_run_remote_with_a_supplied_client_never_uses_the_run_surface(ws, stub, clean_backend_env, monkeypatch,
                                                                      tmp_path):
    """CLI ``run-remote --sms-api-url`` and every test double pass a client: unchanged /compose/v1."""
    from vivarium_workbench.lib import remote_run
    from vivarium_workbench.lib.sms_api_client import SmsApiClient

    _name_backend(monkeypatch, stub.url)
    used = []

    class Legacy(SmsApiClient):
        def compose_submit(self, pbg_bytes, **kw):
            used.append(("compose_submit", kw))
            return 7

        def compose_status(self, task_id):
            return {"status": "completed"}

        def download_compose_results(self, sim_id, dest, timeout=None):
            p = Path(dest) / "results.tar.gz"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
            return p

    monkeypatch.setattr(remote_run, "git_pip_url", lambda ws_root: "git+https://github.com/o/r.git@abc")
    monkeypatch.setattr(remote_run, "workspace_pinned_deps", lambda ws_root: [])
    monkeypatch.setattr("vivarium_workbench.lib.preflight.preflight_composite_run",
                        lambda *a, **k: type("R", (), {"summary": lambda self: "ok"})())
    remote_run.run_remote(ws, COMPOSITE, client=Legacy("http://127.0.0.1:1"), dest=tmp_path / "o", poll_interval=0.01)
    assert used and stub.calls_to("viva-create-composite-run") == []


def test_a_composite_that_cannot_be_exported_is_a_422_with_the_reason_and_nothing_is_sent(
        ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    (ws / "studies" / "demo" / "study.yaml").write_text(yaml.safe_dump({
        "name": "demo", "schema_version": 3,
        "baseline": [{"name": "core", "composite": "pbg_ws_increase_demo.composites.no-such", "params": {}}]}))
    _name_backend(monkeypatch, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 422 and "could not export composite" in res.json()["error"]
    assert stub.calls_to("viva-create-composite-run") == []
