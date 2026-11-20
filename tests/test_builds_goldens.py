"""Goldens for the pin, the study baseline's build dispatch and the build routes (docs/backend-adapters.md step P2,
Builds B3), taken on the base before they moved behind ``Backend.builds`` and the SMS plugin's router, and kept.

With providers on, every answer here is byte-identical to the base: ``/api/remote-run-config`` (off, pinned, the pin
unresolved, a session build, a named backend's profile), the request the study baseline hands the SMS submit and its
409, ``/api/source/builds``, ``build-remote``, ``switch-build`` (cookie, ``materialization``, ``managed=True``),
``/api/source/switch`` (``prepare`` with no ``managed``), the OpenAPI operations of the moved routes and the read-only
allowlist. Only the network edge is faked (``SmsApiClient`` methods, the build listing, the materializer), so the
goldens hold whichever module owns the code.
"""

from __future__ import annotations

import json

import pytest

PIN_REPO = "https://github.com/vivarium-collective/v2ecoli"


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text("name: goldens\n", encoding="utf-8")
    sd = w / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(
        "name: demo\nbaseline:\n  - {name: core, composite: pkg.composites.cell, "
        "params: {n_seeds: 3, n_generations: 2, knob: {a: 1}}}\n", encoding="utf-8")
    return w


@pytest.fixture
def no_pin(monkeypatch):
    for k in ("VIVARIUM_WORKBENCH_REMOTE_PINNED", "VIVARIUM_DASHBOARD_REMOTE_PINNED",
              "VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "VIVARIUM_DASHBOARD_REMOTE_REPO_URL",
              "VIVARIUM_WORKBENCH_REMOTE_BRANCH", "VIVARIUM_DASHBOARD_REMOTE_BRANCH",
              "VIVARIUM_WORKBENCH_BACKEND_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", "smscdk")


@pytest.fixture
def pin(monkeypatch, no_pin):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", PIN_REPO)


def _builds(monkeypatch, versions):
    """Fake the one SMS call the pin makes (``list_branch_builds``), on the class, so it holds wherever the client is
    built. Records the base URL each call was made against."""
    from vivarium_workbench_sms import sms_api_client as sac

    seen: list = []

    def list_branch_builds(self, repo_url, branch):
        seen.append((self.base_url, repo_url, branch))
        return {"versions": versions}

    monkeypatch.setattr(sac.SmsApiClient, "list_branch_builds", list_branch_builds)
    return seen


_V75 = [{"git_repo_url": PIN_REPO + ".git", "git_branch": "main", "database_id": 75,
         "git_commit_hash": "c0ffee", "created_at": "2026-11-01"},
        {"git_repo_url": PIN_REPO, "git_branch": "main", "database_id": 60,
         "git_commit_hash": "old", "created_at": "2026-10-01"}]


def _stamp(ws):
    (ws / ".viv-build.json").write_text(json.dumps({
        "repo_url": "https://github.com/o/r", "simulator_id": 66, "commit": "abc1234", "branch": "dev"}))


# --- /api/remote-run-config -------------------------------------------------------------------------------------------

def test_config_off(ws, no_pin, inproc_client):
    r = inproc_client(ws).get("/api/remote-run-config")
    assert (r.status_code, r.text) == (200, '{"pinned":false,"deployment":"smscdk"}')


@pytest.mark.plugin
def test_config_pinned(ws, pin, monkeypatch, inproc_client):
    seen = _builds(monkeypatch, _V75)
    r = inproc_client(ws).get("/api/remote-run-config")
    assert (r.status_code, r.text) == (200, '{"pinned":true,"repo_url":"%s","branch":"main","deployment":"smscdk",'
                                            '"commit":"c0ffee","simulator_id":75}' % PIN_REPO)
    assert seen == [("http://127.0.0.1:1", PIN_REPO, "main")]


@pytest.mark.plugin
def test_config_pin_unresolved(ws, pin, monkeypatch, inproc_client):
    _builds(monkeypatch, [])
    r = inproc_client(ws).get("/api/remote-run-config")
    assert (r.status_code, r.text) == (200, '{"pinned":true,"repo_url":"%s","branch":"main","deployment":"smscdk",'
                                            '"build_error":"no built simulator for %s@main — register/build '
                                            'one first"}' % (PIN_REPO, PIN_REPO))


@pytest.mark.plugin
def test_config_session_build_wins_and_asks_nothing(ws, pin, monkeypatch, inproc_client):
    seen = _builds(monkeypatch, _V75)
    _stamp(ws)
    r = inproc_client(ws).get("/api/remote-run-config")
    assert (r.status_code, r.text) == (200, '{"pinned":true,"repo_url":"https://github.com/o/r","branch":"dev",'
                                            '"deployment":"smscdk","commit":"abc1234","simulator_id":66}')
    assert seen == []


def test_config_carries_the_named_backends_profile(ws, no_pin, monkeypatch, inproc_client):
    from vivarium_workbench.lib import server_capabilities as sc

    profile = {"reachable": True, "dispatch": sc.DISPATCH_LEGACY, "version": "9", "capabilities": []}
    monkeypatch.setattr(sc, "explicit_backend_profile", lambda: profile)
    r = inproc_client(ws).get("/api/remote-run-config")
    assert r.status_code == 200
    assert r.json() == {"pinned": False, "deployment": "smscdk", "backend": profile}
    assert list(r.json()) == ["pinned", "deployment", "backend"]


# --- the study baseline's dispatch on a pin or a session build --------------------------------------------------------

@pytest.fixture
def submits(monkeypatch):
    """What reaches the SMS submit (``sms_legacy.submit_tail``), which answers 202 without sending anything."""
    from vivarium_workbench_sms import sms_legacy

    seen: list = []

    def submit_tail(ws_root, body):
        seen.append(body)
        return {"simulation_id": 999, "phase": "running"}, 202

    monkeypatch.setattr(sms_legacy, "submit_tail", submit_tail)
    return seen


_REQUEST_75 = {"study": "demo", "simulator_id": 75, "num_generations": 2, "num_seeds": 3, "run_parca": True,
               "extra_params": {"knob": {"a": 1}}}


@pytest.mark.plugin
def test_study_baseline_on_a_pin_submits_the_resolved_build(ws, pin, monkeypatch, submits, inproc_client):
    _builds(monkeypatch, _V75)
    r = inproc_client(ws).post("/api/study-run-baseline", json={"study": "demo"})
    assert (r.status_code, r.json()) == (202, {"simulation_id": 999, "phase": "running"})
    assert submits == [_REQUEST_75]
    assert list(submits[0]) == list(_REQUEST_75)


@pytest.mark.plugin
def test_study_baseline_on_a_session_build_submits_its_id(ws, no_pin, monkeypatch, submits, inproc_client):
    _stamp(ws)
    r = inproc_client(ws).post("/api/study-run-baseline", json={"study": "demo", "run_parca": False})
    assert r.status_code == 202
    assert submits == [{**_REQUEST_75, "simulator_id": 66, "run_parca": False}]


@pytest.mark.plugin
def test_study_baseline_with_no_resolvable_build_409s(ws, pin, monkeypatch, submits, inproc_client):
    _builds(monkeypatch, [])
    r = inproc_client(ws).post("/api/study-run-baseline", json={"study": "demo"})
    assert r.status_code == 409
    assert r.text == ('{"error":"no remote build resolved for this deployment/session — switch to a built '
                      'workspace or configure a pinned repo@branch"}')
    assert submits == []


# --- the build routes -------------------------------------------------------------------------------------------------

@pytest.mark.plugin
def test_source_builds(ws, monkeypatch, inproc_client):
    from vivarium_workbench_sms import remote_build_source

    bases: list = []
    canned = {"builds": [{"simulator_id": 7, "repo": "r", "repo_url": "https://github.com/o/r", "commit": "c",
                          "branch": "main", "label": "r @ c (build #7)"}], "error": None}

    def list_build_sources(client):
        bases.append(client.base_url)
        return canned

    monkeypatch.setattr(remote_build_source, "list_build_sources", list_build_sources)
    r = inproc_client(ws).get("/api/source/builds")
    assert (r.status_code, r.json()) == (200, canned)
    assert bases == ["http://127.0.0.1:1"]


@pytest.mark.plugin
def test_build_remote(ws, monkeypatch, inproc_client):
    from vivarium_workbench_sms import sms_api_client as sac

    monkeypatch.setattr(sac.SmsApiClient, "register_branch_head",
                        lambda self, repo, branch: {"database_id": 42, "git_commit_hash": "c0ffee"})
    r = inproc_client(ws).post("/api/source/build-remote", json={"repo": "https://github.com/x/y.git",
                                                                 "branch": "main"})
    assert (r.status_code, r.text) == (200, '{"ok":true,"simulator_id":42,"repo":"https://github.com/x/y",'
                                            '"branch":"main","commit":"c0ffee"}')


def _tc(client):
    """The in-process client's Starlette ``TestClient`` (for request headers)."""
    return client._tc


@pytest.fixture
def prepares(monkeypatch):
    from vivarium_workbench.lib import session_env, session_registry

    session_registry.clear()
    seen: list = []

    def prepare(*a, **k):
        seen.append((a, k))
        return {"status": "ready", "managed": bool(k.get("managed"))}

    monkeypatch.setattr(session_env, "prepare", prepare)
    return seen


@pytest.mark.plugin
def test_switch_build(ws, tmp_path, monkeypatch, prepares, inproc_client):
    from vivarium_workbench.lib import session_registry
    from vivarium_workbench_sms import remote_build_source

    clone = tmp_path / "clone"
    clone.mkdir()
    entry = {"simulator_id": 5, "repo": "y", "repo_url": "https://github.com/x/y", "commit": "deadbeef",
             "branch": "main", "label": "y @ deadbeef (build #5)"}
    monkeypatch.setattr(remote_build_source, "list_build_sources", lambda c: {"builds": [entry]})
    import vivarium_workbench_sms.source_build_views as sbv

    monkeypatch.setattr(sbv, "list_build_sources", lambda c: {"builds": [entry]})
    monkeypatch.setattr(sbv, "materialize_session_build", lambda c, key, sid, commit: clone)
    r = _tc(inproc_client(ws)).post("/api/source/switch-build", json={"simulator_id": 5},
                                    headers={"X-VW-Session": "tab-b3"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "source": {"path": str(clone), "name": "y @ deadbeef (build #5)"},
                        "materialization": {"status": "ready", "managed": True}}
    # the middleware's first-contact cookie, then the route's own alignment (item 78)
    assert r.headers.get_list("set-cookie") == ["vw_session=tab-b3; HttpOnly; Path=/; SameSite=lax"] * 2
    assert prepares == [(("tab-b3", str(clone)), {"managed": True})]
    assert str(session_registry.get("tab-b3").source_path) == str(clone)


@pytest.mark.plugin
def test_switch_build_refusal_binds_nothing(ws, prepares, inproc_client):
    r = _tc(inproc_client(ws)).post("/api/source/switch-build", json={}, headers={"X-VW-Session": "tab-b3x"})
    assert (r.status_code, r.json()) == (400, {"error": "missing 'simulator_id'"})
    assert r.headers.get_list("set-cookie") == ["vw_session=tab-b3x; HttpOnly; Path=/; SameSite=lax"]  # first contact only
    assert prepares == []


def test_source_switch(ws, tmp_path, monkeypatch, prepares, inproc_client):
    from discovery_engine.integration import workspace_catalog

    other = tmp_path / "other"
    other.mkdir()
    (other / "workspace.yaml").write_text("name: other\n", encoding="utf-8")
    monkeypatch.setattr(workspace_catalog, "list_workspaces", lambda: [{"path": str(other), "name": "other"}])
    r = _tc(inproc_client(ws)).post("/api/source/switch", json={"path": str(other)},
                                    headers={"X-VW-Session": "tab-sw"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "source": {"path": str(other), "name": "other"},
                        "materialization": {"status": "ready", "managed": False}}
    assert r.headers.get_list("set-cookie") == ["vw_session=tab-sw; HttpOnly; Path=/; SameSite=lax"] * 2
    assert prepares == [(("tab-sw", str(other)), {})]  # no managed= at all, as before


#: The moved routes' OpenAPI operations, as on the base.
_OPERATIONS = {
    ("/api/source/builds", "get"): "source_builds_route_api_source_builds_get",
    ("/api/source/build-remote", "post"): "source_build_remote_api_source_build_remote_post",
    ("/api/source/switch-build", "post"): "source_switch_build_api_source_switch_build_post",
}


@pytest.mark.plugin
def test_the_build_routes_openapi_operations():
    from vivarium_workbench.api import app as appmod

    paths = appmod.create_app().openapi()["paths"]
    for (path, method), op_id in _OPERATIONS.items():
        op = paths[path][method]
        assert op["operationId"] == op_id
        assert op["tags"] == ["Workspaces & sources"]
    assert paths["/api/source/builds"]["get"]["summary"] == "Remote sms-api simulator build list for the source dropdown"
    assert paths["/api/source/switch-build"]["post"]["summary"] == (
        "Materialize a remote build's workspace and re-point in-process")


@pytest.mark.plugin
def test_read_only_keeps_build_remote_and_switch_build(ws, monkeypatch, prepares, inproc_client):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_READONLY", "1")
    client = inproc_client(ws)
    assert client.post("/api/source/build-remote", json={"repo": "x"}).json() == {
        "error": "repo and branch are required"}
    assert client.post("/api/source/switch-build", json={}).json() == {"error": "missing 'simulator_id'"}


def test_read_only_keeps_source_switch(ws, monkeypatch, prepares, inproc_client):
    """The core's half of the read-only allowlist above: ``/api/source/switch`` is the core's route."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_READONLY", "1")
    client = inproc_client(ws)
    assert client.post("/api/source/switch", json={}).json() == {"error": "missing 'path'"}
