"""Where a workspace's run goes, pinned as a decision table (docs/backend-adapters.md §4 step 1b).

Six predicates answered it before step 1b: ``remote_pinned.resolve_run_target`` / ``uses_viva_v1_dispatch`` /
``uses_compose_dispatch`` / ``uses_named_backend_dispatch`` and ``server_capabilities.compose_dispatch_active`` /
``viva_v1_dispatch_active``. Step 1b moved the decision into ``backends.named/select/active``; step P2 (target
predicates) moved ``resolve_run_target`` into ``backends`` and deleted the three ``uses_*_dispatch`` wrappers (no
production caller), so their rows below are the ``backends.select`` expressions callers write instead. The predicate
half of this file was written and run against the code before step 1b; the tables are unchanged since.

Each case also counts profile reads (``server_capabilities.explicit_backend_profile``) and network probes
(``server_capabilities.backend_profile``). The refactor may read the cached profile fewer times than today, never
more, and must never probe when the decision is made without a named backend.
"""
from __future__ import annotations

import pytest

from vivarium_workbench_sms import remote_pinned
from vivarium_workbench.lib import backends
from vivarium_workbench.lib import server_capabilities as sc
from vivarium_workbench.lib.errors import APIError
from vivarium_workbench_sms.sms_api_client import BACKEND_BASE_ENV_VARS

PROFILES = {
    "compose": {"reachable": True, "dispatch": sc.DISPATCH_COMPOSE, "error": None},
    "viva": {"reachable": True, "dispatch": sc.DISPATCH_DOCUMENT, "error": None},
    "legacy": {"reachable": True, "dispatch": sc.DISPATCH_LEGACY, "error": None},
    "unreachable": {"reachable": False, "dispatch": sc.DISPATCH_LEGACY, "error": "connection refused"},
}

# (named backend, session build, pinned) -> expected predicate values; "raise" = APIError 502.
# resolve, uses_viva, uses_compose, uses_named, compose_active, viva_active
EXPECTED = {
    "none":        ("local",      False, False, False, False, False),
    "compose":     ("deployment", False, True,  True,  True,  False),
    "viva":        ("deployment", True,  False, True,  False, True),
    "legacy":      ("local",      False, False, False, False, False),
    "unreachable": ("raise",      False, False, False, False, False),
}

# Upper bounds on profile reads (today's counts; the cache makes them one network probe).
MAX_READS = {"resolve": 3, "uses_viva": 1, "uses_compose": 1, "uses_named": 2, "compose_active": 1,
             "viva_active": 1}


@pytest.fixture
def world(monkeypatch, tmp_path):
    for v in BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: False)
    sc._PROFILE_CACHE.clear()
    counts = {"reads": 0, "probes": 0}
    state = {"profile": None}

    def probe(client):
        counts["probes"] += 1
        return {"version": "t", "capabilities": [], "services": {}, **state["profile"]}

    real_explicit = sc.explicit_backend_profile

    def counted_explicit():
        counts["reads"] += 1
        return real_explicit()

    monkeypatch.setattr(sc, "backend_profile", probe)
    monkeypatch.setattr(sc, "explicit_backend_profile", counted_explicit)

    def configure(named: str, *, session: bool = False, pinned: bool = False):
        if named != "none":
            monkeypatch.setenv(BACKEND_BASE_ENV_VARS[0], f"http://{named}.test")
            state["profile"] = PROFILES[named]
        if session:
            (tmp_path / ".viv-build.json").write_text("{}")
        if pinned:
            monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: True)
        counts.update(reads=0, probes=0)
        return tmp_path

    yield configure, counts
    sc._PROFILE_CACHE.clear()


def _uses_named(ws):
    chosen = backends.select(ws)
    return chosen is backends.COMPOSE or chosen is backends.VIVA_CORE


def _predicates(ws):
    return {
        "resolve": lambda: backends.resolve_run_target(ws),
        "uses_viva": lambda: backends.select(ws) is backends.VIVA_CORE,
        "uses_compose": lambda: backends.select(ws) is backends.COMPOSE,
        "uses_named": lambda: _uses_named(ws),
        "compose_active": sc.compose_dispatch_active,
        "viva_active": sc.viva_v1_dispatch_active,
    }


def _eval(fn):
    try:
        return fn()
    except APIError as e:
        assert e.status_code == 502
        return "raise"


@pytest.mark.parametrize("named", list(EXPECTED))
@pytest.mark.parametrize("which", list(MAX_READS))
def test_predicates_without_build_or_pin(world, named, which):
    configure, counts = world
    ws = configure(named)
    expected = dict(zip(MAX_READS, EXPECTED[named]))[which]
    assert _eval(_predicates(ws)[which]) == expected
    assert counts["reads"] <= MAX_READS[which]
    assert counts["probes"] <= 1
    if named == "none":
        assert counts["probes"] == 0


@pytest.mark.parametrize("named", list(EXPECTED))
@pytest.mark.parametrize("build", ["session", "pinned"])
@pytest.mark.parametrize("which", list(MAX_READS))
def test_session_build_or_pin_takes_sms_without_asking_the_backend(world, named, build, which):
    """A session build or the pinned config sends the run to SMS before any named backend is consulted. The two
    profile-only predicates ignore the workspace, as today."""
    configure, counts = world
    ws = configure(named, session=build == "session", pinned=build == "pinned")
    got = _eval(_predicates(ws)[which])
    if which in ("compose_active", "viva_active"):
        assert got == dict(zip(MAX_READS, EXPECTED[named]))[which]
    else:
        assert got == {"resolve": "deployment"}.get(which, False)
        assert counts["probes"] == 0 and counts["reads"] == 0


def test_session_build_is_checked_before_the_pin(world, monkeypatch):
    """``SmsProvider.session_build`` short-circuits before ``is_pinned_enabled`` is asked."""
    configure, _ = world
    ws = configure("none", session=True)

    def boom():
        raise AssertionError("is_pinned_enabled asked although a session build exists")

    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", boom)
    for which in ("resolve", "uses_viva", "uses_compose", "uses_named"):
        _eval(_predicates(ws)[which])


# --- run_remote (the Composites tab / CLI path) -------------------------------------------------------------------

class _Stop(Exception):
    pass


def test_run_remote_with_a_caller_client_never_asks_which_backend(monkeypatch, tmp_path):
    """A caller-supplied client (CLI ``run-remote``, tests) goes to the plugin's run directly: /compose/v1, and no
    profile or backend choice is read."""
    from vivarium_workbench.lib import backends
    from vivarium_workbench_sms import deployment_run

    def no(*a, **k):
        raise AssertionError("backend selection consulted although the caller supplied a client")

    monkeypatch.setattr(backends, "select", no)
    monkeypatch.setattr(backends, "unrouted", no)
    monkeypatch.setattr(sc, "explicit_backend_profile", no)
    monkeypatch.setattr(remote_pinned, "pinned_config", lambda: (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):
        deployment_run.run(tmp_path, "c", client=object(), dest=tmp_path / "out")


@pytest.mark.parametrize("named, to_compose", [("compose", True), ("viva", False), ("legacy", False)])
def test_run_remote_without_a_client_follows_the_named_backend(world, monkeypatch, named, to_compose):
    """Named compose-api: run_composite. Anything else continues to the document / ``/compose/v1`` path, which
    reads the pinned config next (stopped there); "legacy" (nothing selected) gets there through the unrouted facet
    (step P2, Runs b), which reads no profile. Which of those two it takes (viva-core vs legacy) is pinned end
    to end by ``test_backend_dispatch_viva_v1::test_run_remote_runs_a_document_and_lands_the_compose_results``."""
    from vivarium_workbench.lib import remote_run, remote_run_compose

    configure, counts = world
    ws = configure(named)
    seen = []
    monkeypatch.setattr(remote_run_compose, "run_composite", lambda *a, **k: seen.append("compose") or "zip")
    monkeypatch.setattr(remote_pinned, "pinned_config", lambda: (_ for _ in ()).throw(_Stop()))
    try:
        remote_run.run_remote(ws, "c", dest=ws / "out")
    except _Stop:
        seen.append("continued")
    assert seen == (["compose"] if to_compose else ["continued"])
    assert counts["reads"] <= 2


# --- backends.named / select / active, directly ------------------------------------------------------------------

# named backend -> (named(), select(), active()) by backend name; "raise" = APIError 502.
DIRECT = {
    "none":        (None,          None,          None),
    "compose":     ("compose-api", "compose-api", "compose-api"),
    "viva":        ("viva-core",   "viva-core",   "viva-core"),
    "legacy":      (None,          None,          None),
    "unreachable": (None,          None,          "raise"),
}


def _name(fn):
    got = _eval(fn)
    return got if got in (None, "raise") else got.name


@pytest.mark.parametrize("named", list(DIRECT))
def test_backend_functions_without_build_or_pin(world, named):
    from vivarium_workbench.lib import backends

    configure, counts = world
    ws = configure(named)
    assert (_name(backends.named), _name(lambda: backends.select(ws)), _name(lambda: backends.active(ws))) \
        == DIRECT[named]
    assert counts["probes"] <= 1
    if named == "none":
        assert counts["probes"] == 0


@pytest.mark.parametrize("named", list(DIRECT))
@pytest.mark.parametrize("build", ["session", "pinned"])
def test_backend_functions_with_build_or_pin_select_sms(world, named, build):
    from vivarium_workbench.lib import backends

    configure, counts = world
    ws = configure(named, session=build == "session", pinned=build == "pinned")
    assert _name(lambda: backends.select(ws)) == "sms-legacy"
    assert _name(lambda: backends.active(ws)) == "sms-legacy"
    assert counts["reads"] == 0 and counts["probes"] == 0
    assert _name(backends.named) == DIRECT[named][0]  # the profile alone ignores the workspace


# --- backends.resolve_run_target (item 18): the ONE local-vs-deployment resolution -------------------------------
#
# Every run entrypoint (composite_test_run, run_study_baseline, run_study_variant, the investigation runs) calls it,
# so none of them can drift apart again. Moved from test_remote_run_pinned.py when it moved into backends (step P2).

@pytest.fixture
def no_named_backend(monkeypatch):
    for v in BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    sc._PROFILE_CACHE.clear()
    yield
    sc._PROFILE_CACHE.clear()


def test_resolve_run_target_local_when_neither_condition_holds(tmp_path, monkeypatch, no_named_backend):
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: False)
    assert backends.resolve_run_target(tmp_path) == "local"


def test_resolve_run_target_deployment_when_pinned_with_no_session_build(tmp_path, monkeypatch, no_named_backend):
    """The exact item-18 bug condition: pinned deployment, but this workspace has no .viv-build.json
    (composite_test_run's own inline check already got this right; run_study_baseline/run_study_variant didn't)."""
    assert not (tmp_path / ".viv-build.json").exists()
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: True)
    assert backends.resolve_run_target(tmp_path) == "deployment"


def test_resolve_run_target_deployment_when_session_build_with_pin_off(tmp_path, monkeypatch, no_named_backend):
    """A materialized session build (.viv-build.json) alone is sufficient -- unchanged from
    run_core.run_target_for's pre-existing behavior."""
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: False)
    assert backends.resolve_run_target(tmp_path) == "deployment"


def test_resolve_run_target_deployment_when_both_conditions_hold(tmp_path, monkeypatch, no_named_backend):
    """Session build AND deployment pin both present -- still resolves cleanly to "deployment"."""
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: True)
    assert backends.resolve_run_target(tmp_path) == "deployment"


def test_resolve_run_target_short_circuits_before_checking_pin(tmp_path, monkeypatch, no_named_backend):
    """A materialized session build resolves "deployment" WITHOUT calling is_pinned_enabled()."""
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')

    def _boom():
        raise AssertionError("is_pinned_enabled must not be called here")

    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", _boom)
    assert backends.resolve_run_target(tmp_path) == "deployment"


@pytest.mark.parametrize("name", ["resolve_run_target", "uses_viva_v1_dispatch", "uses_compose_dispatch",
                                  "uses_named_backend_dispatch"])
def test_the_old_target_predicates_are_gone_from_remote_pinned(name):
    """No alias may come back: a test patching ``remote_pinned.<name>`` would then be a silent no-op, because every
    caller reads ``backends.resolve_run_target`` / ``backends.select``."""
    assert not hasattr(remote_pinned, name)


# --- one decision: the composite card and GET /api/remote-dispatch-preflight agree ---------------------------------

class _CardStop(Exception):
    pass


def _card_branch(ws, monkeypatch):
    """Which branch ``composite_test_run`` takes for a card body with no ``run_target`` and no ``build``: the
    dispatch ends are patched, so nothing runs and nothing is asked over the network."""
    from vivarium_workbench.lib import composite_test_run_views as ctr
    from vivarium_workbench.lib import remote_run_compose, run_core
    from vivarium_workbench_sms import cloud_dispatch

    seen = {}
    monkeypatch.setattr(remote_run_compose, "checked_steps", lambda steps: steps)

    def image(simulator_id, *a, **k):
        seen["image"] = simulator_id
        raise _CardStop

    def invoke(ws_root, **k):
        seen["target"] = k.get("target")
        raise _CardStop

    monkeypatch.setattr(cloud_dispatch, "_dispatch_build_image_run", image)
    monkeypatch.setattr(run_core, "invoke_run", invoke)
    try:
        body, status = ctr.composite_test_run(ws, {"id": "c"})
    except _CardStop:
        pass
    except APIError as e:
        return ("unreachable", e.status_code)
    else:
        return ("refused", status, body.get("reason"))
    if "image" in seen:
        return ("image",)
    chosen = backends.select(ws)
    if seen["target"] == "deployment" and chosen is backends.COMPOSE:
        return ("compose",)
    if seen["target"] == "deployment" and chosen is backends.VIVA_CORE:
        return ("document",)
    return (seen["target"],)


def _preflight_branch(body):
    if body.get("phase") == "unreachable":
        return ("unreachable", 502)
    if body.get("ok") is False:
        # Not ready, whichever dispatch it names: the card refuses that launch with a 409 (its viva-core branch
        # answers the git preflight's own reason). Checked before the dispatch, so a branch that names its dispatch
        # with ``ok: False`` cannot pass for a card that launches.
        return ("refused", 409, body.get("reason"))
    if body["target"] == "local":
        return ("local",)
    if body.get("dispatch") in ("compose", "compose-api"):
        return ("compose",)
    if body.get("dispatch") == "viva-v1-document":
        return ("document",)
    if body.get("dispatch") == "image":
        return ("image",)
    assert body["ok"] is False
    return ("refused", 409, body.get("reason"))


class _FakeComposeSession:
    """compose-api as ``remote_run_compose._session`` opens it: one prebuilt environment (``runtime``) and no registry
    entries, or ``environments()`` raising ``fail``. Both sides run the real compose environment check against it,
    so nothing is asked over the network."""

    def __init__(self, fail=None):
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def environments(self):
        from types import SimpleNamespace

        if self.fail is not None:
            raise self.fail
        return [SimpleNamespace(name="runtime", kind="prebuilt", simulator="sim-1", image="img:1", stale=False)]

    def catalog(self):
        return []


def _same_decision(world, monkeypatch, inproc_client, named, build, tree, environment=None, compose_fail=None):
    """``(preflight's branch, card's branch)`` for one world. The git preflight (``remote_run.remote_dispatch_preflight``)
    answers ``tree`` to both sides: clean, or dirty (``reason: "dirty"``). compose-api answers both sides through one
    fake session (:class:`_FakeComposeSession`), so its real preflight and its real card check both run."""
    from vivarium_workbench.lib import remote_run, remote_run_compose

    configure, _ = world
    ws = configure(named, session=build == "session", pinned=build == "pinned")
    if environment is None:
        monkeypatch.delenv("VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT", environment)
    pf = {"ok": True} if tree == "clean" else {"ok": False, "reason": "dirty", "message": "uncommitted changes"}
    monkeypatch.setattr(remote_run, "remote_dispatch_preflight", lambda ws_root: dict(pf))
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    (ws / ".pbg").mkdir(exist_ok=True)
    # The SMS half: a session build resolves to an image; a pin with no build is the no-build dead end.
    monkeypatch.setattr(remote_pinned, "resolved_from_session_build",
                        lambda ws_root: {"simulator_id": 7, "repo_url": "r", "commit": "c"}
                        if (ws_root / ".viv-build.json").exists() else None)
    monkeypatch.setattr(remote_pinned, "resolve_pinned_simulator_id", lambda client, ws_root: None)
    monkeypatch.setattr(remote_run_compose, "_session", lambda *a, **k: _FakeComposeSession(compose_fail))

    preflight = _preflight_branch(inproc_client(ws).get("/api/remote-dispatch-preflight").json())
    return preflight, _card_branch(ws, monkeypatch)


@pytest.mark.parametrize("named", list(EXPECTED))
@pytest.mark.parametrize("build", ["none", "session", "pinned"])
@pytest.mark.parametrize("tree", ["clean", "dirty"])
def test_preflight_and_card_take_the_same_decision(world, monkeypatch, inproc_client, named, build, tree):
    """The preflight decides once (``backends.active``) and asks the backend's ``runs.dispatch_preflight``; the card
    decides with ``backends.select`` (``active`` when nothing is selected) and asks ``runs.dispatch_composite`` (step
    P2 Builds B2). They read the same select and the same cached profile, so they agree -- this pins it, so a later
    step cannot split them (#1107). Only which branch each side takes is compared: the preflight answers HTTP 200
    with ``ok: False`` bodies where the card raises 502 or returns 409. A dirty tree refuses only viva-core's named
    environment, which ships the workspace from git."""
    preflight, card = _same_decision(world, monkeypatch, inproc_client, named, build, tree)
    assert preflight == card
    expected = {
        "session": ("image",),
        "pinned": ("refused", 409, "no-build"),
    }.get(build) or {"none": ("local",), "compose": ("compose",), "viva": ("document",), "legacy": ("local",),
                     "unreachable": ("unreachable", 502)}[named]
    if tree == "dirty" and expected == ("document",):
        expected = ("refused", 409, "dirty")
    assert card == expected


def test_an_environment_id_with_a_clean_tree_runs_a_document_on_both_sides(world, monkeypatch, inproc_client):
    assert _same_decision(world, monkeypatch, inproc_client, "viva", "none", "clean", "id:env-1") == (
        ("document",), ("document",))


def test_an_environment_id_with_a_dirty_tree_takes_the_same_decision(world, monkeypatch, inproc_client):
    """Finding F, fixed in Builds B2b (rule A plus a non-blocking warning): an ``id:`` environment ships nothing
    from git, so both sides launch a document; the preflight only warns about the dirty tree."""
    preflight, card = _same_decision(world, monkeypatch, inproc_client, "viva", "none", "dirty", "id:env-1")
    assert preflight == card == ("document",)


@pytest.mark.xfail(strict=True, reason="the card does not check the compose environment yet")
def test_an_unknown_compose_environment_takes_the_same_decision(world, monkeypatch, inproc_client):
    """The compose analogue of Finding F: a named environment compose-api does not list is refused by the preflight
    (409 ``unknown-environment``), and the card refuses the launch the same way instead of spawning a run that fails
    in the detached runner."""
    preflight, card = _same_decision(world, monkeypatch, inproc_client, "compose", "none", "clean", "name:missing")
    assert preflight == card == ("refused", 409, "unknown-environment")


@pytest.mark.xfail(strict=True, reason="the card does not check the compose environment yet")
def test_a_compose_registry_error_takes_the_same_decision(world, monkeypatch, inproc_client):
    """compose-api answers the environment listing with an error: the preflight is not ready and the card refuses the
    launch for the same reason. The statuses differ (the preflight's branch reads every ``ok: False`` as a 409; the
    card answers ``_backend_error``'s 502), so only the reasons are compared."""
    from compose_api_client.ext import ApiError

    preflight, card = _same_decision(world, monkeypatch, inproc_client, "compose", "none", "clean",
                                     compose_fail=ApiError(500, "boom"))
    assert preflight[0] == card[0] == "refused"
    assert preflight[2] == card[2] == "backend error"
