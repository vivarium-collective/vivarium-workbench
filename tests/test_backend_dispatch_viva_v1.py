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

import io
import json
import shutil
import sqlite3
import threading
import zipfile
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
def _no_ambient_github_login(tmp_path, monkeypatch):
    """The servers these tests spawn must not see the developer's GitHub login (gh CLI / keyring / token env):
    the legacy submit gate answers 401 only when there is no session, so the assertions would otherwise depend on
    whoever runs them."""
    home = tmp_path / "no-login-home"
    home.mkdir()
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GH_CONFIG_DIR", str(home / ".config" / "gh"))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


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


def test_a_dependency_the_backend_does_not_allow_is_explained_not_reported_as_unreachable(
        ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    """The real backend answers 422 "'<dep>' is not in the compose allow list". That is a policy refusal from a reachable
    server, so the message says what to do about it, names the dependency, and keeps the backend's own wording."""
    orig = stub.handle
    dep = "git+https://github.com/some-org/some-repo.git@0123456789abcdef"

    def refuse(method, raw_path, raw_body):
        if method == "POST" and raw_path.startswith("/viva/v1/composites"):
            return 422, "application/json", json.dumps({"detail": f"'{dep}' is not in the compose allow list"}).encode()
        return orig(method, raw_path, raw_body)

    stub.handle = refuse
    _name_backend(monkeypatch, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    body = res.json()
    assert res.status_code == 403, res.text
    assert body["reason"] == "not-allow-listed" and body["dependency"] == dep and body["reachable"] is True
    assert "does not allow" in body["error"] and "some-org/some-repo" in body["error"]
    assert "operator" in body["error"].lower()                      # what to do about it
    assert "not in the compose allow list" in body["backend_error"]  # the backend's own words are kept


def test_other_upstream_refusals_are_still_reported_as_before(ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    orig = stub.handle

    def refuse(method, raw_path, raw_body):
        if method == "POST" and raw_path.startswith("/viva/v1/composites"):
            return 422, "application/json", json.dumps({"detail": "some other validation problem"}).encode()
        return orig(method, raw_path, raw_body)

    stub.handle = refuse
    _name_backend(monkeypatch, stub.url)
    res = dashboard_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 502 and "some other validation problem" in res.json()["error"]
    assert "reason" not in res.json()


# --- run_remote (Composites tab / detached runner) ---------------------------------------------

SIM_ID = 35  # the compose simulation the run became; deliberately NOT the 38 of its job id (compose:38)


def _hpcrun(sim_id: int, database_id: int, correlation_id: str) -> dict:
    return {"database_id": database_id, "slurmjobid": 0, "correlation_id": correlation_id, "job_type": "simulation",
            "sim_id": sim_id, "simulator_id": None, "job_backend": "slurm", "status": "completed"}


def _results_zip(steps: int = 2) -> bytes:
    """The archive a SLURM compose run serves (shape read from a real run's download): the emitter's history
    keyed by emitter name, one state per emitted step, plus the event log and the final state."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("emitter_history.json", json.dumps({"emitter": [
            {"results": {"M1": {"auto": {"tellurium": {"time": [0.0, 1.0], "X": [float(i), i + 1.0]}}}}, "step": i}
            for i in range(steps)]}))
        z.writestr("events.jsonl", '{"event": "run.start"}\n')
        z.writestr("final_state.json", "{}")
    return buf.getvalue()


def _serve_results(stub, runs: list[dict], payload: bytes | None = None) -> None:
    stub.respond("GET", "/viva/v1/compose/simulations/status/batch", 200, runs)
    stub.respond_bytes("GET", "/viva/v1/compose/simulation/{simulation_id}/results", 200,
                       payload if payload is not None else _results_zip(), "application/zip")


def _remote_ready(stub, monkeypatch) -> None:
    from vivarium_workbench.lib import remote_run

    stub.respond("GET", "/viva/v1/composites/{id}/status", 200, {"id": RUN_ID, "status": "completed", "message": None})
    _name_backend(monkeypatch, stub.url, environment=None)  # default: the site-named `runtime` environment
    monkeypatch.setattr(remote_run, "git_pip_url", lambda ws_root: "git+https://github.com/o/r.git@abc")
    monkeypatch.setattr(remote_run, "workspace_pinned_deps", lambda ws_root: [])
    monkeypatch.setattr("vivarium_workbench.lib.preflight.preflight_composite_run",
                        lambda *a, **k: type("R", (), {"summary": lambda self: "ok"})())


def test_run_remote_runs_a_document_and_lands_the_compose_results(ws, stub, clean_backend_env, monkeypatch,
                                                                  tmp_path):
    from vivarium_workbench.lib import remote_run

    _remote_ready(stub, monkeypatch)
    _serve_results(stub, [_hpcrun(38, 41, "simulation-SOMEONE-ELSE"), _hpcrun(SIM_ID, 38, RUN_ID)])
    out = remote_run.run_remote(ws, COMPOSITE, dest=tmp_path / "out", n_steps=5, poll_interval=0.01)

    (call,) = stub.calls_to("viva-create-composite-run")
    assert call.body["environment"] == {"name": "runtime"}
    assert call.body["execution"]["options"] == {
        "interval_time": 5.0, "extra_pip_deps": ["git+https://github.com/o/r.git@abc"]}
    # a zip is saved as a zip: the archive's own type names the file
    assert out.is_file() and out.name == "results.zip" and zipfile.is_zipfile(out)
    # The output is fetched from the compose simulation the run became. The run's job id (compose:38) is the
    # compose_hpcrun row, a different number (container builds take rows too), so it is found by the run's own
    # id in the backend's records -- and the other run that is in the same batch is not mistaken for it.
    assert [c.path for c in stub.calls if c.path.endswith("/results")] == [
        f"/viva/v1/compose/simulation/{SIM_ID}/results"]
    (lookup,) = stub.calls_to("compose-get-simulations-status-batch")
    assert str(SIM_ID) in lookup.query["ids"]


def test_run_remote_refuses_to_guess_when_the_backend_does_not_know_the_run(ws, stub, clean_backend_env, monkeypatch,
                                                                            tmp_path):
    from vivarium_workbench.lib import remote_run

    _remote_ready(stub, monkeypatch)
    _serve_results(stub, [_hpcrun(38, 41, "simulation-SOMEONE-ELSE")])
    with pytest.raises(RuntimeError, match=RUN_ID):
        remote_run.run_remote(ws, COMPOSITE, dest=tmp_path / "out", n_steps=5, poll_interval=0.01)
    # nothing was downloaded: another run's output must never be landed as this one's
    assert [c for c in stub.calls if c.path.endswith("/results")] == []


def test_a_matching_run_id_on_a_different_job_row_is_not_accepted(ws, stub, clean_backend_env, monkeypatch, tmp_path):
    """The run id AND the job row its record names (compose:38) must both agree."""
    from vivarium_workbench.lib import remote_run

    _remote_ready(stub, monkeypatch)
    _serve_results(stub, [_hpcrun(SIM_ID, 99, RUN_ID)])
    with pytest.raises(RuntimeError, match="compose:38"):
        remote_run.run_remote(ws, COMPOSITE, dest=tmp_path / "out", n_steps=5, poll_interval=0.01)
    assert [c for c in stub.calls if c.path.endswith("/results")] == []


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


# --- landing a study run through /api/remote-run-land -------------------------------------------

def _runs_db(ws: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(ws / "studies" / "demo" / "runs.db")
    conn.row_factory = sqlite3.Row
    return conn


def test_a_finished_viva_v1_study_run_lands_its_emitter_history_where_the_viewers_read_it(
        ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    _name_backend(monkeypatch, stub.url)
    stub.respond("GET", "/viva/v1/composites/{id}", 200, _run("completed"))
    _serve_results(stub, [_hpcrun(SIM_ID, 38, RUN_ID)])
    c = dashboard_client(ws)
    assert c.post("/api/remote-run-submit", json={"study": "demo"}).status_code == 202
    with _runs_db(ws) as db:  # the dispatch left its placeholder in the Runs tab
        assert [r["run_id"] for r in db.execute("SELECT run_id FROM runs_meta")] == [f"remote-pending-{RUN_ID}"]

    res = c.post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]

    with _runs_db(ws) as db:
        metas = db.execute("SELECT run_id, status, n_steps, params_json FROM runs_meta").fetchall()
        # the placeholder is replaced by the real row, which says where the output came from
        assert [m["run_id"] for m in metas] == [run_id]
        assert metas[0]["status"] == "completed" and metas[0]["n_steps"] == 2
        prov = json.loads(metas[0]["params_json"])
        assert prov["viva_v1"]["simulation_id"] == SIM_ID and prov["viva_v1"]["run_id"] == RUN_ID
        assert prov["viva_v1"]["job_id"] == "compose:38"
        rows = db.execute("SELECT step, state FROM history WHERE simulation_id = ? ORDER BY step", (run_id,)).fetchall()
        assert [r["step"] for r in rows] == [0, 1]
        assert json.loads(rows[1]["state"])["results"]["M1"]["auto"]["tellurium"]["X"] == [1.0, 2.0]
    assert [c.path for c in stub.calls if c.path.endswith("/results")] == [
        f"/viva/v1/compose/simulation/{SIM_ID}/results"]


def test_landing_an_unfinished_viva_v1_run_is_refused_and_downloads_nothing(
        ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    _name_backend(monkeypatch, stub.url)
    stub.respond("GET", "/viva/v1/composites/{id}", 200, _run("running"))
    _serve_results(stub, [_hpcrun(SIM_ID, 38, RUN_ID)])
    res = dashboard_client(ws).post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert res.status_code == 409 and "running" in res.json()["error"]
    assert [c for c in stub.calls if c.path.endswith("/results")] == []
    assert not (ws / "studies" / "demo" / "runs.db").exists()


def test_a_viva_v1_run_shows_in_the_run_list_while_pending_and_as_a_local_store_once_landed(
        ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    """Pending: the list still renders (the run's id is the backend's string, not a number). Landed: its data is
    in this workspace's runs.db, so it is listed as a run whose data is here -- not as one whose data is remote."""
    _name_backend(monkeypatch, stub.url)
    stub.respond("GET", "/viva/v1/composites/{id}", 200, _run("completed"))
    _serve_results(stub, [_hpcrun(SIM_ID, 38, RUN_ID)])
    c = dashboard_client(ws)
    assert c.post("/api/remote-run-submit", json={"study": "demo"}).status_code == 202

    pending = c.get("/api/simulations")
    assert pending.status_code == 200, pending.text
    (row,) = [r for r in pending.json()["simulations"] if r.get("run_id") == f"remote-pending-{RUN_ID}"]
    assert row["remote_origin"]["simulation_id"] == RUN_ID

    run_id = c.post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID}).json()["run_id"]
    landed = c.get("/api/simulations?refresh=true")  # what the UI asks for after a land
    assert landed.status_code == 200, landed.text
    (row,) = [r for r in landed.json()["simulations"] if r.get("run_id") == run_id]
    assert row["remote_origin"] is None and row["store_path"] is None and row["status"] == "completed"
    assert row["config"] is None  # the backend's record is provenance, not a reproduction config


def test_the_run_lists_status_poll_reads_a_viva_v1_run_by_its_own_id(
        ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    """The Runs table polls a pending remote row by the id it recorded; for a /viva/v1 run that is the backend's
    string id, which must go to the run's status read, not the integer sms-api path."""
    _name_backend(monkeypatch, stub.url)
    stub.respond("GET", "/viva/v1/composites/{id}/status", 200, {"id": RUN_ID, "status": "completed", "message": None})
    res = dashboard_client(ws).get(f"/api/remote-run-poll?simulation_id={RUN_ID}")
    assert res.status_code == 200, res.text
    assert (res.json()["phase"], res.json()["run_id"]) == ("done", RUN_ID)
    assert stub.calls_to("viva-get-composite-run-status")



def test_a_simulation_far_below_its_job_row_is_still_found(ws, stub, clean_backend_env, monkeypatch, tmp_path):
    """Every container build takes a job row but no simulation, so the gap grows with a deployment's age; the
    lookup walks down until it finds the run, however far."""
    from vivarium_workbench.lib import remote_run

    _remote_ready(stub, monkeypatch)
    stub.respond("POST", "/viva/v1/composites", 202, _run(job_id="compose:950"))
    _serve_results(stub, [_hpcrun(12, 950, RUN_ID)])
    remote_run.run_remote(ws, COMPOSITE, dest=tmp_path / "out", n_steps=5, poll_interval=0.01)
    assert [c.path for c in stub.calls if c.path.endswith("/results")] == ["/viva/v1/compose/simulation/12/results"]


def test_landing_the_same_run_twice_answers_the_first_landing(ws, stub, clean_backend_env, dashboard_client,
                                                               monkeypatch):
    _name_backend(monkeypatch, stub.url)
    stub.respond("GET", "/viva/v1/composites/{id}", 200, _run("completed"))
    _serve_results(stub, [_hpcrun(SIM_ID, 38, RUN_ID)])
    c = dashboard_client(ws)
    first = c.post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID}).json()
    again = c.post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID}).json()
    assert again == {"run_id": first["run_id"], "already_landed": True}
    with _runs_db(ws) as db:
        assert db.execute("SELECT count(*) FROM runs_meta").fetchone()[0] == 1
    assert len([c for c in stub.calls if c.path.endswith("/results")]) == 1


def test_a_string_id_is_not_landed_through_viva_v1_unless_a_document_backend_is_named(
        ws, stub, clean_backend_env, dashboard_client, monkeypatch):
    """Without `serve --backend-base-url` naming a document backend, the land route keeps its sign-in gate."""
    monkeypatch.setenv("VIVA_API_BASE", stub.url)  # an alias: does not opt in
    res = dashboard_client(ws).post("/api/remote-run-land", json={"study": "demo", "simulation_id": RUN_ID})
    assert res.status_code == 401
    assert stub.calls_to("viva-get-composite-run") == []
