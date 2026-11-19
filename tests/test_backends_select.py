"""Where a workspace's run goes, pinned as a decision table (docs/backend-adapters.md §4 step 1b).

Six predicates answer it today: ``remote_pinned.resolve_run_target`` / ``uses_viva_v1_dispatch`` /
``uses_compose_dispatch`` / ``uses_named_backend_dispatch`` and ``server_capabilities.compose_dispatch_active`` /
``viva_v1_dispatch_active``. Step 1b moves the decision into ``backends.named/select/active`` and keeps the six as
wrappers. The predicate half of this file was written and run against the code before that move.

Each case also counts profile reads (``server_capabilities.explicit_backend_profile``) and network probes
(``server_capabilities.backend_profile``). The refactor may read the cached profile fewer times than today, never
more, and must never probe when the decision is made without a named backend.
"""
from __future__ import annotations

import pytest

from vivarium_workbench_sms import remote_pinned
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


def _predicates(ws):
    return {
        "resolve": lambda: remote_pinned.resolve_run_target(ws),
        "uses_viva": lambda: remote_pinned.uses_viva_v1_dispatch(ws),
        "uses_compose": lambda: remote_pinned.uses_compose_dispatch(ws),
        "uses_named": lambda: remote_pinned.uses_named_backend_dispatch(ws),
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
    """``run_core.run_target_for`` short-circuits before ``is_pinned_enabled`` is asked."""
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
    """A caller-supplied client (CLI ``run-remote``, tests) keeps /compose/v1 and reads no profile."""
    from vivarium_workbench.lib import backends, remote_run

    def no(*a, **k):
        raise AssertionError("backend selection consulted although the caller supplied a client")

    monkeypatch.setattr(backends, "select", no)
    monkeypatch.setattr(sc, "explicit_backend_profile", no)
    monkeypatch.setattr(remote_pinned, "pinned_config", lambda: (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):
        remote_run.run_remote(tmp_path, "c", client=object(), dest=tmp_path / "out")


@pytest.mark.parametrize("named, to_compose", [("compose", True), ("viva", False), ("legacy", False)])
def test_run_remote_without_a_client_follows_the_named_backend(world, monkeypatch, named, to_compose):
    """Named compose-api: run_composite. Anything else continues to the document / ``/compose/v1`` path, which
    reads the pinned config next (stopped there). Which of those two it takes (viva-core vs legacy) is pinned end
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
