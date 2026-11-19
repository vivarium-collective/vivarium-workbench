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
  compose-api's;
* a backend that was named and cannot be asked never turns a run into a local one.

Removing a viva-api path should mean deleting a test here on purpose, not finding out from a deployment.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import server_capabilities as sc
from vivarium_workbench.lib import sms_api_client as sac

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
    for v in ENV_VARS + ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                         "VIVARIUM_WORKBENCH_GH_TOKEN", "VIVARIUM_DASHBOARD_GH_TOKEN"):
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


# --- the pinned SMS build is asked of the SMS host, not of a named compose-api -------------------------------

class _SmsHost(_Backend):
    """A second server, answering as the SMS API: its own routes and its own request log, so a test can tell which
    host a request reached."""

    routes: dict[str, dict] = {}
    seen: list[tuple[str, str]] = []


@pytest.fixture
def sms_host():
    _SmsHost.routes, _SmsHost.seen = {}, []
    srv = HTTPServer(("127.0.0.1", 0), _SmsHost)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


# --- a named backend that cannot be reached never runs locally -----------------------------------------------

DEAD = "http://127.0.0.1:9"  # nothing listens on the discard port (the Stanford test above names it too)
NAMED = "VIVARIUM_WORKBENCH_BACKEND_BASE_URL"


def _assert_unreachable(body: dict, base: str) -> None:
    """The envelope ``remote_run_compose._backend_error`` answers for an unreachable compose-api, for any named backend."""
    assert (body["phase"], body["reachable"], body["reason"], body["backend"]) == (
        "unreachable", False, "backend unreachable", base)
    assert base in body["error"] and "not started locally" in body["error"]


@pytest.fixture
def local_runs(monkeypatch):
    """Every LOCAL run the two run-start routes would launch, recorded and not launched: the study baseline's
    subprocess and the Composites card's detached runner are replaced at their process boundary. Whether to
    launch is not replaced: that decision is what these tests exercise."""
    from vivarium_workbench.lib import composite_subprocess, run_registry

    started: list[str] = []

    def _study_subprocess(*a, **k):
        started.append("study-baseline")
        return {"error": "a local study run was started"}, 500

    def _card_runner(*a, **k):
        started.append("composite-card")
        return 4321

    monkeypatch.setattr(composite_subprocess, "run_composite_subprocess", _study_subprocess)
    monkeypatch.setattr(run_registry, "spawn_detached", _card_runner)
    return started


def test_a_named_backend_that_cannot_be_reached_never_resolves_to_a_local_run(ws, monkeypatch):
    from vivarium_workbench.lib import remote_pinned
    from vivarium_workbench.lib.errors import APIError

    monkeypatch.setenv(NAMED, DEAD)
    with pytest.raises(APIError) as e:
        remote_pinned.resolve_run_target(ws)
    assert e.value.status_code == 502
    _assert_unreachable(e.value.to_body(), DEAD)


@pytest.mark.parametrize("body", [
    pytest.param({"study": "demo"}, id="the-default-baseline"),
    pytest.param({"study": "demo", "composite": "core"}, id="a-named-baseline-entry"),
])
def test_the_study_baseline_starts_no_local_run_when_the_named_backend_cannot_be_reached(
        ws, monkeypatch, inproc_client, local_runs, body):
    """Two different code paths resolve the target (the default entry in run_study_baseline, a named one in
    launch_into_study); neither may fall to the laptop."""
    monkeypatch.setenv(NAMED, DEAD)
    res = inproc_client(ws).post("/api/study-run-baseline", json=body)
    assert res.status_code == 502, res.text
    _assert_unreachable(res.json(), DEAD)
    assert local_runs == []


def test_the_composite_card_starts_no_local_run_when_the_named_backend_cannot_be_reached(
        ws, monkeypatch, inproc_client, local_runs):
    monkeypatch.setenv(NAMED, DEAD)
    res = inproc_client(ws).post("/api/composite-test-run", json={"id": COMPOSITE, "steps": 1})
    assert res.status_code == 502, res.text
    _assert_unreachable(res.json(), DEAD)
    assert local_runs == []


def test_a_baseline_preview_does_not_wait_on_the_named_backend(ws, monkeypatch, inproc_client, local_runs):
    """A dry run starts nothing, and run_study_baseline says it "falls through unchanged (preview stays local-only)":
    it answers when the named backend cannot, instead of refusing like the run it previews."""
    monkeypatch.setenv(NAMED, DEAD)
    res = inproc_client(ws).post("/api/study-run-baseline", json={"study": "demo", "dry_run": True})
    assert res.status_code == 200, res.text
    assert res.json()["dry_run"] is True
    assert local_runs == []


def test_the_preflight_says_unreachable_not_that_the_workspace_runs_locally(ws, monkeypatch, inproc_client):
    monkeypatch.setenv(NAMED, DEAD)
    body = inproc_client(ws).get("/api/remote-dispatch-preflight").json()
    assert (body["target"], body["ok"]) == ("deployment", False)
    _assert_unreachable(body, DEAD)


def test_a_submit_that_names_no_simulator_asks_the_named_backend_first(ws, monkeypatch, inproc_client):
    """Without a simulator id the submit is the named backend's (compose-api or viva-v1 documents). When that backend
    cannot be asked the answer says so, not "not authenticated" or "simulator_id is required" from the SMS path."""
    monkeypatch.setenv(NAMED, DEAD)
    res = inproc_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert res.status_code == 502, res.text
    _assert_unreachable(res.json(), DEAD)


def test_a_pinned_submit_without_a_simulator_id_is_still_the_sms_paths_answer(ws, monkeypatch, inproc_client):
    """A pinned deployment never runs on a named backend, so a dead one changes nothing for it: the submit still gets
    the SMS path's own answer, not a 502 about the named backend."""
    monkeypatch.setenv(NAMED, DEAD)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", SMS_ECOLI)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_BRANCH", "main")
    res = inproc_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert (res.status_code, res.json()) == (400, {"error": "simulator_id is required"})


def test_a_session_build_submit_without_a_simulator_id_is_still_the_sms_paths_answer(ws, monkeypatch, inproc_client):
    """A session build (.viv-build.json) sends the submit to SMS and never asks the named backend, dead or not."""
    monkeypatch.setenv(NAMED, DEAD)
    (ws / ".viv-build.json").write_text(json.dumps({"repo_url": SMS_ECOLI, "simulator_id": 1}))
    res = inproc_client(ws).post("/api/remote-run-submit", json={"study": "demo"})
    assert (res.status_code, res.json()) == (401, {"error": "not authenticated"})  # no login: the SMS gate's own answer


def test_a_submit_that_carries_a_simulator_id_does_not_ask_the_named_backend(ws, monkeypatch, inproc_client):
    """A simulator id makes the submit the SMS path's, whatever backend was named: a dead one changes nothing, so the
    answer is the SMS gate's own (no login: not authenticated), not a 502 about the named backend."""
    monkeypatch.setenv(NAMED, DEAD)
    res = inproc_client(ws).post("/api/remote-run-submit", json={
        "study": "demo", "simulator_id": 66, "num_generations": 2, "num_seeds": 3})
    assert (res.status_code, res.json()) == (401, {"error": "not authenticated"})


def test_the_cli_says_unreachable_where_it_used_to_start_a_local_run(ws, monkeypatch, capsys, local_runs):
    """``VIVARIUM_WORKBENCH_BACKEND_BASE_URL`` exported in a shell reaches ``vwb run`` too. The refusal is the CLI's
    own failure shape (the JSON body, exit 1), not a traceback."""
    from vivarium_workbench.cli import main

    monkeypatch.setenv(NAMED, DEAD)
    rc = main(["run", "study", "demo", "--workspace", str(ws), "--json"])
    assert rc == 1
    _assert_unreachable(json.loads(capsys.readouterr().out), DEAD)
    assert local_runs == []


def test_a_named_backend_that_is_reachable_but_legacy_only_still_runs_locally(
        ws, backend, monkeypatch, inproc_client, local_runs):
    """The contract the guard must not touch (test_backend_dispatch_viva_v1.py pins it too): an older viva-api
    answers, and offers neither /viva/v1 nor an OpenAPI document that looks like compose-api's. It stays local."""
    _Backend.routes = {}
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", backend)
    c = inproc_client(ws)
    assert c.get("/api/remote-dispatch-preflight").json()["target"] == "local"
    res = c.post("/api/composite-test-run", json={"id": COMPOSITE, "steps": 1})
    assert res.status_code == 202, res.text
    assert local_runs == ["composite-card"]

# --- a backend that comes back is asked again ----------------------------------------------------------------

class _Dropping(_Backend):
    """A backend that can be made to drop every connection without answering (what a dead tunnel looks like to the
    client), and then brought back."""

    routes: dict[str, dict] = {}
    seen: list[tuple[str, str]] = []
    up = True
    attempts = 0  # every GET that reached it, answered or dropped

    def do_GET(self):  # noqa: N802
        type(self).attempts += 1
        if not type(self).up:
            self.close_connection = True  # no status line: the client reads a connection-level failure
            return
        super().do_GET()


@pytest.fixture
def dropping_backend():
    _Dropping.routes, _Dropping.seen, _Dropping.up, _Dropping.attempts = {}, [], True, 0
    srv = HTTPServer(("127.0.0.1", 0), _Dropping)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _age(base: str, seconds: float) -> None:
    """Make the cached profile of ``base`` ``seconds`` older."""
    stamp, profile = sc._PROFILE_CACHE[base]
    sc._PROFILE_CACHE[base] = (stamp - seconds, profile)


def test_a_backend_that_comes_back_is_seen_again_once_its_circuit_breaker_closes(dropping_backend, monkeypatch):
    """A connection failure opens that link's circuit breaker (RemoteLink) for a minute, and while it is open the
    profile makes no network call: a dead or hung backend is not asked every few seconds by every request thread.
    The breaker's own probe (every 30 s while ``serve`` runs, on the one URL ``sms_api_base()`` names at start-up) or
    the first call after its minute is up closes it when the backend is back; the profile reads the backend again
    within its own 5 s window after that."""
    from vivarium_workbench.lib import remote_link

    base = dropping_backend
    _Dropping.routes = dict(COMPOSE)
    monkeypatch.setenv(NAMED, base)
    _Dropping.up = False
    assert sc.explicit_backend_profile()["reachable"] is False
    assert remote_link.link(base).state == remote_link.STATE_DOWN
    assert _Dropping.attempts == 1

    _Dropping.up = True
    _age(base, 10.0)  # the failure is older than its window
    assert sc.explicit_backend_profile()["reachable"] is False  # the circuit is still open: the backend is not asked
    assert _Dropping.attempts == 1, "the profile probed through an open circuit"

    assert remote_link.link(base).probe() is True  # what RemoteLink's background probe does
    _age(base, 10.0)
    profile = sc.explicit_backend_profile()
    assert (profile["reachable"], profile["dispatch"]) == (True, sc.DISPATCH_COMPOSE)


def test_a_failed_probe_is_remembered_for_seconds_not_for_half_a_minute(monkeypatch):
    """resolve_run_target and its callers ask for the profile several times per request. A failure is cached so those
    asks share one probe, but only briefly, because the backend may be back in seconds; a success keeps its 30 s."""
    probes: list[int] = []
    answer = {"reachable": False, "version": None, "capabilities": [], "services": {}, "dispatch": sc.DISPATCH_LEGACY,
              "error": "down"}

    def fake_probe(client):
        probes.append(1)
        return dict(answer)

    monkeypatch.setattr(sc, "backend_profile", fake_probe)
    monkeypatch.setenv(NAMED, DEAD)
    sc.explicit_backend_profile()
    sc.explicit_backend_profile()
    assert len(probes) == 1, "two asks in one breath must share one probe"

    _age(DEAD, 10.0)  # a failure, ten seconds old
    sc.explicit_backend_profile()
    assert len(probes) == 2, "a ten-second-old failure must be probed again"

    answer.update(reachable=True)
    sc._PROFILE_CACHE[DEAD] = (time.monotonic() - 10.0, dict(answer))  # a success, ten seconds old
    sc.explicit_backend_profile()
    assert len(probes) == 2, "a ten-second-old success is still trusted"


def test_the_failure_window_counts_from_the_end_of_the_probe(monkeypatch):
    """A hung backend makes a probe take its whole timeout (3 s, one attempt: sms_api_client._CALL_CLASS_POLICY). A
    window counted from the start of the probe is nearly spent when it returns, so the callers of one request would
    probe again. A fake clock, so the test cannot flake."""
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    probes: list[int] = []

    def slow_failure(client):
        probes.append(1)
        clock[0] += 3.0  # the probe takes its whole timeout
        return {"reachable": False, "version": None, "capabilities": [], "services": {},
                "dispatch": sc.DISPATCH_LEGACY, "error": "down"}

    monkeypatch.setattr(sc, "backend_profile", slow_failure)
    monkeypatch.setenv(NAMED, DEAD)
    sc.explicit_backend_profile()  # started at 1000, returned at 1003
    clock[0] += 4.0  # 4 s after it returned: inside the 5 s window
    sc.explicit_backend_profile()
    assert len(probes) == 1, "the window was counted from the start of the probe"
    clock[0] += 2.0  # 6 s after it returned: outside
    sc.explicit_backend_profile()
    assert len(probes) == 2


# --- one remote backend per process (docs/backend-adapters.md step 1c; Jim, 2026-11-19) --------------------------
#
# These replace five tests that pinned a named compose-api running beside an SMS alias (sms_api_base() skipping the
# named URL for the SMS flows). That configuration is now refused when the process starts, so a workbench never talks
# to two backends; the refusal itself is pinned in tests/test_backends_startup.py.

def test_compose_api_named_beside_the_stanford_alias_is_refused_at_startup(backend, sms_host, monkeypatch):
    from vivarium_workbench import cli

    monkeypatch.setenv(NAMED, backend)
    _stanford_env(monkeypatch, sms_host)
    with pytest.raises(sac.BackendConfigError):
        sac.check_single_backend()
    assert cli.main(["serve", "--workspace", str(FIXTURE_WS)]) == 2


def test_the_named_backend_alone_is_the_sms_base(backend, monkeypatch):
    monkeypatch.setenv(NAMED, backend)
    assert sac.sms_api_base() == backend
    sac.check_single_backend()


# --- a backend that cannot say what it is --------------------------------------------------------------------

class _Stumbling(_Backend):
    """A backend that is up and is not viva-v1 (every route is a 404) but fails when asked for its OpenAPI document."""

    routes: dict[str, dict] = {}
    seen: list[tuple[str, str]] = []

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        type(self).seen.append(("GET", path))
        if path == "/openapi.json":
            self._reply(503, {"detail": "try again later"})
        else:
            self._reply(404, {"detail": "Not Found"})


@pytest.fixture
def stumbling_backend():
    _Stumbling.routes, _Stumbling.seen = {}, []
    srv = HTTPServer(("127.0.0.1", 0), _Stumbling)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_a_backend_that_fails_when_asked_what_it_is_reads_as_unreachable_not_as_a_healthy_legacy_one(
        ws, stumbling_backend, monkeypatch, inproc_client, local_runs):
    """A 404 on the capabilities route alone says "not viva-v1"; compose-api is then recognised by its OpenAPI
    document. When that document cannot be had (a 5xx, a refused connection, a timeout) the backend is not a legacy
    one that is up: it cannot be told apart, so a run must not fall to the laptop. (A 404 there still reads as
    legacy: test_each_named_backend_gets_its_own_dispatch.)"""
    monkeypatch.setenv(NAMED, stumbling_backend)
    c = inproc_client(ws)
    profile = c.get("/api/remote-run-config").json()["backend"]
    assert profile["reachable"] is False
    res = c.post("/api/composite-test-run", json={"id": COMPOSITE, "steps": 1})
    assert res.status_code == 502, res.text
    _assert_unreachable(res.json(), stumbling_backend)
    assert local_runs == []
