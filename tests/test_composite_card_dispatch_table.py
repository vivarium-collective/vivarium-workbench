"""The composite card's dispatch table and the dispatch preflight, pinned body for body (step P2, Builds B2).

Every expected body and status below was computed on the commit BEFORE Builds B2 (main @ 59b8d51e), when
``composite_test_run`` and ``/api/remote-dispatch-preflight`` still branched on the backend's identity. B2 moved each
branch behind ``runs.dispatch_composite`` / ``runs.dispatch_preflight``; with providers on, nothing here may change.

Only seams that exist on both sides are patched (the SMS client's class attributes, ``remote_run``'s preflight,
``remote_pinned``'s network hop, the capability profile, ``run_registry``), never a name of the card module, so
the table runs unchanged on the base and on B2. The network is a tripwire: ``sms_api_client.urlopen`` and
``backend_config``'s ``urlopen`` (``ping`` and the probe client) raise.
"""
from __future__ import annotations

import json

import pytest

from vivarium_workbench.lib import backend_config
from vivarium_workbench.lib import remote_run
from vivarium_workbench.lib import run_registry
from vivarium_workbench.lib import server_capabilities as sc
from vivarium_workbench_sms import remote_pinned
from vivarium_workbench_sms import sms_api_client as sac

PROFILES = {
    "compose": {"reachable": True, "dispatch": sc.DISPATCH_COMPOSE, "error": None},
    "viva": {"reachable": True, "dispatch": sc.DISPATCH_DOCUMENT, "error": None},
}

ACTIONS_NONE = [{"label": "Build on cloud", "panel": "builds", "commit": None},
                {"label": "Switch Environment to Local", "panel": "environment"}]
NO_IMAGE_ERROR = ("No cloud image exists for this workspace. Cloud runs execute a registered build's pre-built "
                  "image — build one on the cloud, or switch the Environment to Local.")
NOT_PUSHED = {"ok": False, "reason": "unpushed", "message": "HEAD is not pushed.", "sha": "deadbeefcafe"}


class _Net(Exception):
    pass


@pytest.fixture
def card(tmp_path, monkeypatch):
    """A workspace, no backend named, no pin, no stamp, compose not opted in; the SMS client and the git preflight
    are recorded stubs and the network raises."""
    for v in (*backend_config.BACKEND_BASE_ENV_VARS, "VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT",
              "VIVARIUM_WORKBENCH_REMOTE_PINNED", "VIVARIUM_WORKBENCH_REMOTE_REPO_URL",
              "VIVARIUM_WORKBENCH_REMOTE_BRANCH", "VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH",
              "VIVARIUM_WORKBENCH_BACKEND_PROVIDERS"):
        monkeypatch.delenv(v, raising=False)
    sc._PROFILE_CACHE.clear()

    def net(*a, **k):
        raise _Net("network call in the dispatch table")

    monkeypatch.setattr(sac, "urlopen", net)
    monkeypatch.setattr(backend_config, "urlopen", net)

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    seen: dict = {"spawned": [], "preflight": 0, "run_simulation": [], "get": [], "compose_sessions": []}
    state: dict = {"preflight": {"ok": True, "reason": "ok", "message": "ready"}, "configs": ["api_simulation_default.json"],
                   "run_simulation": {"database_id": 909, "experiment_id": "exp-1"}}
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    monkeypatch.setattr(run_registry, "spawn_detached", lambda *a, **k: (seen["spawned"].append(a), 4242)[1])

    def preflight(ws_root):
        seen["preflight"] += 1
        return dict(state["preflight"])

    monkeypatch.setattr(remote_run, "remote_dispatch_preflight", preflight)

    def _get(self, path, params=None):
        seen["get"].append((path, params))
        return {"config_filenames": list(state["configs"])}

    def run_simulation(self, **kw):
        seen["run_simulation"].append(kw)
        out = state["run_simulation"]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(sac.SmsApiClient, "_get", _get)
    monkeypatch.setattr(sac.SmsApiClient, "run_simulation", run_simulation)

    class World:
        ws = tmp_path

        @staticmethod
        def name(kind):
            monkeypatch.setattr(sc, "explicit_backend_profile", lambda: PROFILES[kind])

        @staticmethod
        def stamp(data):
            (tmp_path / ".viv-build.json").write_text(json.dumps(data), encoding="utf-8")

        @staticmethod
        def pin(simulator_id):
            monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
            monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "https://github.com/x/sms-ecoli.git")

            def resolve(client, repo, branch):
                if simulator_id is None:
                    raise remote_pinned.NoPinnedBuildError("none")
                return {"simulator_id": simulator_id, "commit": "abc", "branch": branch, "repo_url": repo}

            monkeypatch.setattr(remote_pinned, "resolve_pinned_build", resolve)

        @staticmethod
        def compose(environments=("runtime",)):
            """Name compose-api, answering through a fake session (``remote_run_compose._session``) that lists these
            prebuilt environments and no registry entries; records each session it opens."""
            from types import SimpleNamespace

            from vivarium_workbench.lib import remote_run_compose

            World.name("compose")

            class Session:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def environments(self):
                    return [SimpleNamespace(name=n, kind="prebuilt", simulator="sim-1", image="img:1", stale=False)
                            for n in environments]

                def catalog(self):
                    return []

            def session(*a, **k):
                seen["compose_sessions"].append(k)
                return Session()

            monkeypatch.setattr(remote_run_compose, "_session", session)

        @staticmethod
        def opt_in():
            monkeypatch.setenv("VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH", "1")

        @staticmethod
        def env(value):
            monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT", value)

        @staticmethod
        def run(body):
            from vivarium_workbench.lib import composite_test_run_views as ctr

            return ctr.composite_test_run(tmp_path, {"id": "pkg.composites.x", "steps": 7, **body})

        @staticmethod
        def request(resp):
            return json.loads((tmp_path / ".pbg" / "runs" / resp["run_id"] / "request.json").read_text())

    World.seen = seen  # type: ignore[attr-defined]
    World.state = state  # type: ignore[attr-defined]
    yield World
    sc._PROFILE_CACHE.clear()


BUILD = {"simulator_id": 211, "repo_url": "https://github.com/x/sms-ecoli.git", "commit": "33ecd77aabbcc"}
DEFAULT_SIM_CALL = dict(simulator_id=211, num_generations=1, num_seeds=1, run_parca=True, observables=[],
                        config_filename="api_simulation_default.json",
                        description="composite-card cloud run: pkg.composites.x")
IMAGE_909 = {"run_id": "remote-sim-909", "status": "running", "remote": True, "simulation_id": 909,
             "experiment_id": "exp-1"}


# --- the image dispatch (202) ------------------------------------------------------------------------------------

def test_explicit_build_dispatches_its_image(card):
    assert card.run({"run_target": "deployment", "build": BUILD}) == (IMAGE_909, 202)
    assert card.seen["run_simulation"] == [DEFAULT_SIM_CALL]
    assert card.seen["get"] == [("/api/v1/simulations/discovery", {"simulator_id": 211})]
    assert card.seen["preflight"] == 0 and card.seen["spawned"] == []


def test_explicit_build_with_overrides_paths_and_a_config_skips_discovery(card):
    body = {"run_target": "deployment", "build": BUILD, "config_filename": " mine.json ",
            "overrides": {"n_generations": 3, "n_seeds": 2}, "emit_paths": ["a", "b"]}
    assert card.run(body) == (IMAGE_909, 202)
    assert card.seen["run_simulation"] == [{**DEFAULT_SIM_CALL, "num_generations": 3, "num_seeds": 2,
                                            "observables": ["a", "b"], "config_filename": "mine.json"}]
    assert card.seen["get"] == []


def test_a_session_build_dispatches_its_image(card):
    card.stamp({"simulator_id": 124, "repo_url": "r", "commit": "c", "branch": "b"})
    card.state["run_simulation"] = {"simulation_id": 777}
    assert card.run({}) == ({"run_id": "remote-sim-777", "status": "running", "remote": True,
                             "simulation_id": 777, "experiment_id": None}, 202)
    assert card.seen["run_simulation"][0]["simulator_id"] == 124
    assert card.seen["preflight"] == 0


def test_a_pinned_build_dispatches_its_image(card):
    card.pin(333)
    assert card.run({}) == (IMAGE_909, 202)
    assert card.seen["run_simulation"][0]["simulator_id"] == 333


def test_the_session_build_wins_over_a_named_backend(card):
    card.name("compose")
    card.stamp({"simulator_id": 124, "repo_url": "r", "commit": "c"})
    assert card.run({})[1] == 202
    assert card.seen["run_simulation"][0]["simulator_id"] == 124


# --- the SMS dead ends --------------------------------------------------------------------------------------------

def test_explicit_cloud_with_no_build_is_409_with_actions(card):
    assert card.run({"run_target": "deployment"}) == (
        {"error": NO_IMAGE_ERROR, "reason": "no-build", "run_target": "deployment", "actions": ACTIONS_NONE}, 409)
    assert card.seen["spawned"] == [] and card.seen["run_simulation"] == []


def test_explicit_cloud_with_a_build_but_no_simulator_carries_its_commit(card):
    body, status = card.run({"run_target": "deployment", "build": {"commit": "abc1234def", "repo_url": "u"}})
    assert status == 409
    assert body == {
        "error": ("No cloud image exists for this workspace at commit abc1234. Cloud runs execute a registered "
                  "build's pre-built image — build one on the cloud, or switch the Environment to Local."),
        "reason": "no-build", "run_target": "deployment",
        "actions": [{"label": "Build on cloud", "panel": "builds", "commit": "abc1234def"},
                    {"label": "Switch Environment to Local", "panel": "environment"}]}


def test_a_pin_with_no_build_is_409_with_actions(card):
    card.pin(None)
    assert card.run({}) == (
        {"error": NO_IMAGE_ERROR, "reason": "no-build", "run_target": "deployment", "actions": ACTIONS_NONE}, 409)


def test_a_stamp_with_no_repo_is_409_without_the_opt_in(card):
    card.stamp({"simulator_id": 66})
    assert card.run({}) == (
        {"error": NO_IMAGE_ERROR, "reason": "no-build", "run_target": "deployment", "actions": ACTIONS_NONE}, 409)
    assert card.seen["preflight"] == 0


def test_no_config_for_the_composite_is_409(card):
    card.state["configs"] = ["fss.json", "other.json"]
    assert card.run({"run_target": "deployment", "build": BUILD}) == ({
        "error": ("Cloud build #211 has no config matching composite 'pkg.composites.x'. Pick a config explicitly "
                  "before dispatching — configs available on this build: fss.json, other.json."),
        "reason": "no-config-for-composite", "run_target": "deployment", "spec_id": "pkg.composites.x",
        "available_configs": ["fss.json", "other.json"]}, 409)
    assert card.seen["run_simulation"] == []


def test_a_failed_dispatch_is_502(card):
    card.state["run_simulation"] = sac.SmsApiError("boom")
    assert card.run({"run_target": "deployment", "build": BUILD}) == (
        {"error": "cloud dispatch failed: boom", "reason": "dispatch-failed", "run_target": "deployment"}, 502)


# --- the opt-in compose path (VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH) --------------------------------------------

def test_opt_in_with_an_unpushed_tree_is_409_with_actions(card):
    card.opt_in()
    card.state["preflight"] = NOT_PUSHED
    assert card.run({"run_target": "deployment", "build": {"commit": "abc1234def", "repo_url": "u"}}) == ({
        "error": "HEAD is not pushed.", "reason": "unpushed", "preflight": NOT_PUSHED, "run_target": "deployment",
        "actions": [{"label": "Build on cloud", "panel": "builds", "commit": "deadbeefcafe"},
                    {"label": "Switch Environment to Local", "panel": "environment"}]}, 409)
    assert card.seen["spawned"] == []


def test_opt_in_writes_the_selected_build_into_the_run_request(card):
    card.opt_in()
    resp, status = card.run({"run_target": "deployment", "build": {"commit": "abc1234def", "repo_url": "u"}})
    assert (resp, status) == ({"run_id": resp["run_id"], "status": "running"}, 202)
    req = card.request(resp)
    assert req["target"] == "deployment"
    assert req["build_ref"] == {"simulator_id": None, "repo_url": "u", "commit": "abc1234def"}
    assert card.seen["preflight"] == 1 and len(card.seen["spawned"]) == 1


def test_opt_in_on_a_stamp_with_no_repo_runs_detached_with_no_build_ref(card):
    card.opt_in()
    card.stamp({"simulator_id": 66})
    resp, status = card.run({})
    assert status == 202
    req = card.request(resp)
    assert (req["target"], req["build_ref"]) == ("deployment", None)


# --- compose-api and viva-core ------------------------------------------------------------------------------------

def test_compose_refuses_a_step_count_it_cannot_run(card):
    from vivarium_workbench.lib import remote_run_compose

    card.compose()
    m = remote_run_compose.MAX_STEPS
    assert card.run({"steps": m + 1}) == ({
        "error": f"compose-api runs 1 to {m} steps per run; this run asks for {float(m + 1)}",
        "reason": "bad-steps", "max_steps": m, "run_target": "deployment"}, 400)


def test_compose_runs_detached_with_no_preflight(card):
    card.compose()
    resp, status = card.run({"run_target": "deployment", "build": BUILD})
    assert status == 202
    req = card.request(resp)
    assert (req["target"], req["build_ref"]) == ("deployment", None)
    assert card.seen["preflight"] == 0 and card.seen["run_simulation"] == []


@pytest.mark.xfail(strict=True, reason="the card does not check the compose environment yet")
def test_compose_refuses_an_unknown_environment_with_no_run(card):
    """Like the readiness check: a named environment compose-api does not list is refused at launch (409), with no
    run row written and nothing spawned, instead of a run that fails in the detached runner."""
    card.compose()
    card.env("name:missing")
    assert card.run({}) == ({
        "error": "compose-api has no prebuilt environment 'missing' (it has: runtime)",
        "reason": "unknown-environment", "run_target": "deployment"}, 409)
    assert card.seen["spawned"] == [] and card.seen["preflight"] == 0
    assert not (card.ws / ".pbg" / "runs").exists() or not any((card.ws / ".pbg" / "runs").iterdir())


def test_viva_named_environment_with_a_dirty_tree_is_409(card):
    card.name("viva")
    card.state["preflight"] = NOT_PUSHED
    assert card.run({}) == ({"error": "HEAD is not pushed.", "reason": "unpushed", "preflight": NOT_PUSHED,
                             "run_target": "deployment"}, 409)


def test_viva_named_environment_with_a_clean_tree_runs_detached(card):
    card.name("viva")
    card.env("runtime")
    resp, status = card.run({})
    assert status == 202
    assert (card.request(resp)["target"], card.request(resp)["build_ref"]) == ("deployment", None)
    assert card.seen["preflight"] == 1


def test_viva_environment_id_skips_the_git_preflight(card):
    card.name("viva")
    card.env("id:env-1")
    card.state["preflight"] = NOT_PUSHED
    resp, status = card.run({})
    assert status == 202
    assert card.request(resp)["target"] == "deployment"
    assert card.seen["preflight"] == 0


def test_nothing_selected_runs_locally(card):
    resp, status = card.run({})
    assert status == 202
    assert (card.request(resp)["target"], card.request(resp)["build_ref"]) == ("local", None)
    assert card.seen["preflight"] == 0


# --- GET /api/remote-dispatch-preflight ---------------------------------------------------------------------------

def _preflight(card, inproc_client):
    r = inproc_client(card.ws).get("/api/remote-dispatch-preflight")
    assert r.status_code == 200
    return r.json()


def test_preflight_local(card, inproc_client):
    assert _preflight(card, inproc_client) == {"target": "local", "ok": True, "reason": "local",
                                               "message": "This workspace runs locally — no push needed."}


def test_preflight_session_build_image(card, inproc_client):
    card.stamp({"simulator_id": 124, "repo_url": "r", "commit": "c"})
    assert _preflight(card, inproc_client) == {
        "target": "deployment", "ok": True, "reason": "image", "dispatch": "image", "simulator_id": 124,
        "message": "Cloud image (build #124) is ready — runs execute its pre-built image, no push needed."}


def test_preflight_pin_with_no_build(card, inproc_client):
    card.pin(None)
    assert _preflight(card, inproc_client) == {"error": NO_IMAGE_ERROR, "reason": "no-build",
                                               "run_target": "deployment", "actions": ACTIONS_NONE,
                                               "target": "deployment", "ok": False}


def test_preflight_opt_in(card, inproc_client):
    card.opt_in()
    card.stamp({"simulator_id": 66})
    card.state["preflight"] = NOT_PUSHED
    assert _preflight(card, inproc_client) == {
        **NOT_PUSHED, "target": "deployment",
        "actions": [{"label": "Build on cloud", "panel": "builds", "commit": "deadbeefcafe"},
                    {"label": "Switch Environment to Local", "panel": "environment"}],
        "message": ("HEAD is not pushed. The compose path installs your repo from git on the deployment and "
                    "requires the repo to be on sms-api's compose allow-list.")}


def test_preflight_viva_named_environment_runs_the_git_check(card, inproc_client):
    """A named environment ships the workspace from git: the git preflight decides (unchanged by B2b)."""
    card.name("viva")
    card.env("runtime")
    card.state["preflight"] = NOT_PUSHED
    assert _preflight(card, inproc_client) == {**NOT_PUSHED, "target": "deployment", "dispatch": "viva-v1-document"}


def test_preflight_viva_environment_id_is_ready_with_a_warning(card, inproc_client):
    """An environment id runs its prebuilt image (Builds B2b, Finding F): ready like the card, and an unpushed tree
    is only a non-blocking warning. (Before B2b this row answered the git preflight's refusal.)"""
    card.name("viva")
    card.env("id:env-1")
    card.state["preflight"] = NOT_PUSHED
    assert _preflight(card, inproc_client) == {
        **NOT_PUSHED, "ok": True, "reason": "ok", "target": "deployment", "dispatch": "viva-v1-document",
        "message": ("Environment id:env-1 runs its prebuilt image -- nothing is shipped from the workspace's git "
                    "tree, so nothing needs to be committed or pushed first."),
        "warnings": ["unpushed commits are not used by this environment (id:env-1 runs its prebuilt image); "
                     "a run uses the code already in that image"]}


def test_preflight_compose(card, inproc_client, monkeypatch):
    from vivarium_workbench.lib import remote_run_compose

    card.name("compose")
    monkeypatch.setattr(remote_run_compose, "dispatch_preflight",
                        lambda ws_root: {"target": "deployment", "dispatch": "compose", "ok": True, "reason": "x"})
    assert _preflight(card, inproc_client) == {"target": "deployment", "dispatch": "compose", "ok": True,
                                               "reason": "x"}
    assert card.seen["preflight"] == 0
