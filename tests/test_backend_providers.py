"""Backend providers and the core's registry (docs/backend-adapters.md §2.2, step P1c).

With providers ON (the default, and the only configuration in use) everything is as before step P1c -- the existing
routing, select, capability and interop tests prove that unchanged. These tests pin the registry itself and the
core's behaviour with providers OFF (``VIVARIUM_WORKBENCH_BACKEND_PROVIDERS=none``, step P3's core-only CI job).
"""

from __future__ import annotations

import threading
from urllib.parse import urlencode

import pytest

from vivarium_workbench.lib import backends
from vivarium_workbench.lib.backends import provider as provider_mod


@pytest.fixture
def providers_off(monkeypatch):
    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    yield
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()


@pytest.fixture(autouse=True)
def _fresh_registry():
    backends.reset_registry()
    yield
    backends.reset_registry()


# --- providers on ---------------------------------------------------------------------------------------------------

def test_the_sms_provider_is_found_and_brings_viva_core_and_sms_legacy():
    (sms,) = provider_mod.providers()
    assert sms.name == "sms"
    assert [b.name for b in backends._ALL] == ["compose-api", "viva-core", "sms-legacy"]
    assert backends.by_kind("viva-core") is backends.VIVA_CORE
    assert backends.by_kind("sms-legacy") is backends.SMS_LEGACY
    assert backends.VIVA_CORE.identity is backends.SMS_LEGACY.identity  # one viva-api server


def test_the_registry_hands_out_the_same_objects_even_under_concurrent_first_use():
    seen = []
    barrier = threading.Barrier(8)

    def grab():
        barrier.wait()
        seen.append((backends.VIVA_CORE, backends.SMS_LEGACY, backends.by_kind("viva-core")))

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(v) for v, _, _ in seen}) == 1 and len({id(s) for _, s, _ in seen}) == 1
    assert all(v is k for v, _, k in seen)


def test_a_missing_dependency_inside_the_plugin_is_not_mistaken_for_no_plugin(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def boom(name, *a, **k):
        if name == "vivarium_workbench_sms" and a and a[2] and "provider" in a[2]:
            raise ModuleNotFoundError("No module named 'some_dep_of_the_plugin'", name="some_dep_of_the_plugin")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    provider_mod.reset()
    with pytest.raises(ModuleNotFoundError):
        provider_mod.providers()


def test_recognise_never_raises_and_stops_when_the_backend_cannot_be_asked():
    from vivarium_workbench_sms.sms_api_client import SmsApiError

    class Down:
        def capabilities(self):
            raise SmsApiError("unreachable")

        def _get(self, *a, **k):
            raise SmsApiError("unreachable")

    (sms,) = provider_mod.providers()
    r = sms.recognise(Down())
    assert r.stop and not r.has_composites and r.profile["reachable"] is False


# --- providers off --------------------------------------------------------------------------------------------------

def test_with_providers_off_only_compose_api_is_a_backend(providers_off):
    assert provider_mod.providers() == ()
    assert backends._ALL == (backends.COMPOSE,)
    with pytest.raises(KeyError):
        backends.by_kind("viva-core")
    assert backends.for_run("12") is None and backends.for_run("simulation-X") is None
    assert backends.for_run("compose-7") is backends.COMPOSE


def test_with_providers_off_the_sms_attributes_are_inert_sentinels(providers_off):
    viva, sms = backends.VIVA_CORE, backends.SMS_LEGACY
    assert viva is not None and sms is not None
    assert (None is viva, None is sms) == (False, False)  # `chosen is backends.VIVA_CORE` stays false for local runs
    assert not viva.runs.owns("simulation-X") and not sms.runs.owns("12")
    with pytest.raises(RuntimeError):
        sms.runs.status("12")


@pytest.mark.parametrize("session_build, pinned", [(True, False), (False, True)])
def test_with_providers_off_a_session_build_or_pin_does_not_pick_sms(providers_off, tmp_path, monkeypatch,
                                                                     session_build, pinned):
    from vivarium_workbench_sms import remote_pinned

    if session_build:
        (tmp_path / ".viv-build.json").write_text("{}")  # any stamp: run_core.run_target_for says "deployment"
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: pinned)
    monkeypatch.setattr(backends, "named", lambda: None)
    assert backends.select(tmp_path) is None


@pytest.mark.parametrize("session_build, pinned", [(True, False), (False, True)])
def test_with_providers_on_a_session_build_or_pin_picks_sms_legacy(tmp_path, monkeypatch, session_build, pinned):
    from vivarium_workbench_sms import remote_pinned

    if session_build:
        (tmp_path / ".viv-build.json").write_text("{}")
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: pinned)
    assert backends.select(tmp_path) is backends.SMS_LEGACY


def test_the_same_backends_come_back_across_a_providers_toggle(monkeypatch):
    """A provider keeps the backends it built, so ``is`` comparisons stay valid after reset_registry()."""
    before = (backends.VIVA_CORE, backends.SMS_LEGACY)
    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    assert backends.VIVA_CORE is not before[0]  # the sentinel while off
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV)
    backends.reset_registry()
    assert (backends.VIVA_CORE, backends.SMS_LEGACY) == before
    assert backends.VIVA_CORE is before[0] and backends.SMS_LEGACY is before[1]


def test_with_providers_off_the_profile_never_asks_viva_core(providers_off):
    from vivarium_workbench.lib import server_capabilities as sc

    asked = []

    class Client:
        def capabilities(self):
            asked.append("capabilities")
            raise AssertionError("asked viva-core's capabilities with providers off")

        def openapi_operations(self):
            asked.append("openapi")
            return "0.7", frozenset(sc.COMPOSE_OPERATIONS)

    profile = sc.backend_profile(Client())
    assert asked == ["openapi"]
    assert profile["dispatch"] == sc.DISPATCH_COMPOSE and profile["reachable"] is True


def test_with_providers_off_a_named_compose_api_is_probed_by_the_core_client(providers_off, monkeypatch):
    """Step P2 Client: with providers off the named backend is read by ``backend_config.ProbeClient``, never by the
    SMS client."""
    from vivarium_workbench.lib import backend_config as bc
    from vivarium_workbench.lib import server_capabilities as sc
    from vivarium_workbench_sms import sms_api_client as sac

    def no_sms_client(self, *a, **k):
        raise AssertionError("built the SMS client with providers off")

    monkeypatch.setattr(sac.SmsApiClient, "__init__", no_sms_client)
    asked = []

    def ops(self):
        asked.append(self.base_url)
        return "0.7", frozenset(sc.COMPOSE_OPERATIONS)

    monkeypatch.setattr(bc.ProbeClient, "openapi_operations", ops)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "http://p2-off:1")
    sc._PROFILE_CACHE.clear()
    try:
        profile = sc.explicit_backend_profile()
    finally:
        sc._PROFILE_CACHE.clear()
    assert asked == ["http://p2-off:1"]
    assert profile is not None and profile["dispatch"] == sc.DISPATCH_COMPOSE


def test_with_providers_on_the_named_backend_is_probed_by_the_sms_probe_client(monkeypatch):
    """The golden for the pre-P2 ``SmsApiClient.for_("probe", base)``: the provider's ``probe_client`` builds it."""
    from vivarium_workbench.lib import server_capabilities as sc
    from vivarium_workbench_sms import sms_api_client as sac
    from vivarium_workbench_sms.provider import PROVIDER

    built = []

    class Client:
        def capabilities(self):
            return {"version": "1", "capabilities": []}

        def openapi_operations(self):
            return None, frozenset()

    monkeypatch.setattr(sac.SmsApiClient, "for_",
                        classmethod(lambda cls, kind, base=None, **k: built.append((kind, base)) or Client()))
    assert isinstance(PROVIDER.probe_client("http://p2-on:1"), Client)
    assert built == [("probe", "http://p2-on:1")]
    built.clear()
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "http://p2-on:2")
    sc._PROFILE_CACHE.clear()
    try:
        profile = sc.explicit_backend_profile()
    finally:
        sc._PROFILE_CACHE.clear()
    assert built == [("probe", "http://p2-on:2")]
    assert profile is not None and profile["dispatch"] == sc.DISPATCH_LEGACY and profile["version"] == "1"


def test_with_providers_off_a_stale_document_profile_does_not_hand_out_the_sentinel(providers_off, monkeypatch):
    from vivarium_workbench.lib import server_capabilities as sc

    monkeypatch.setattr(sc, "explicit_backend_profile", lambda: {"dispatch": sc.DISPATCH_DOCUMENT, "reachable": True})
    assert backends.named() is None


def test_with_providers_off_an_sms_record_is_refused_backend_not_installed(providers_off, tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.backends import records

    monkeypatch.setenv("SMS_API_BASE", "http://api:8000")
    monkeypatch.delenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", raising=False)
    ws = tmp_path / "ws"
    (ws / "studies" / "s").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    conn = cr.connect(ws / "studies" / "s" / "runs.db")
    try:
        cr.save_metadata(conn, spec_id="c", run_id="remote-pending-12", label=None, started_at=0.0, n_steps=0,
                         params={"source": "smsvpctest", "simulation_id": "12", "backend": "ray",
                                 "remote": {"kind": "sms-legacy", "id": "12", "base_url": "http://api:8000",
                                            "deployment": "smsvpctest", "server_id": None}})
    finally:
        conn.close()
    res = records.resolve_existing(ws, "12")
    assert res.refusal is not None
    body, code = res.refusal
    assert (code, body["reason"], body["terminal"]) == (409, "backend-not-installed", True)
    assert "sms-legacy" in body["error"]


# --- the run target with providers off (step P2, target predicates) --------------------------------------------------

@pytest.mark.parametrize("case, expected", [
    ("session-build", "local"),
    ("pin", "local"),
    ("named-compose", "deployment"),
    ("named-document", "local"),
    ("named-unreachable", "raise"),
])
def test_with_providers_off_the_run_target_ignores_sms(providers_off, tmp_path, monkeypatch, inproc_client,
                                                       case, expected):
    """With providers off nothing claims a session build or a pin, and a document profile is not a backend (viva-core
    is the plugin's), so those run locally; a named compose-api is still the deployment, and a named backend that
    cannot be reached is still refused. The preflight answers the same target."""
    from vivarium_workbench.lib import remote_run_compose as _rc
    from vivarium_workbench.lib import server_capabilities as sc
    from vivarium_workbench.lib.errors import APIError
    from vivarium_workbench_sms import remote_pinned

    ws = tmp_path
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    if case == "session-build":
        (ws / ".viv-build.json").write_text('{"simulator_id": 66}')
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: case == "pin")
    profile = {
        "named-compose": {"reachable": True, "dispatch": sc.DISPATCH_COMPOSE, "error": None},
        "named-document": {"reachable": True, "dispatch": sc.DISPATCH_DOCUMENT, "error": None},
        "named-unreachable": {"reachable": False, "dispatch": sc.DISPATCH_LEGACY, "error": "connection refused"},
    }.get(case)
    if case == "named-unreachable":
        monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "http://dead.test")
    monkeypatch.setattr(sc, "explicit_backend_profile", lambda: profile)
    # compose-api's preflight would otherwise ask its registry for this workspace's environment.
    monkeypatch.setattr(_rc, "dispatch_preflight",
                        lambda ws_root: {"target": "deployment", "ok": True, "dispatch": "compose"})

    if expected == "raise":
        with pytest.raises(APIError) as e:
            backends.resolve_run_target(ws)
        assert e.value.status_code == 502
    else:
        assert backends.resolve_run_target(ws) == expected

    body = inproc_client(ws).get("/api/remote-dispatch-preflight").json()
    if expected == "raise":
        assert (body["target"], body["ok"], body["phase"]) == ("deployment", False, "unreachable")
    else:
        assert body["target"] == expected
        if expected == "local":
            assert body["ok"] is True
        else:
            assert body["dispatch"] == "compose"


# --- the unrouted facet and the reverse seam (step P2, Runs a1) ------------------------------------------------------

#: The names that moved from lib.remote_run_views to the plugin's sms_legacy. remote_run_views must bind none of them:
#: a stale ``monkeypatch.setattr(rrv, name, ...)`` then raises instead of silently patching a dead copy.
_MOVED_TO_SMS_LEGACY = (
    "SmsApiClient", "SmsApiError", "_sms_api_base", "load_spec", "_run_auth_ok", "land_remote_run",
    "_TERMINAL_OK", "_TERMINAL_BAD", "_ANALYSIS_POLL_INTERVAL_SECONDS", "_ANALYSIS_POLL_MAX_ATTEMPTS",
    "_ANALYSIS_LAND_WAIT_SECONDS", "_ANALYSIS_TERMINAL_STATUSES",
)


def test_remote_run_views_binds_no_sms_seam():
    from vivarium_workbench.lib import remote_run_views as rrv
    from vivarium_workbench_sms import sms_legacy

    assert [n for n in _MOVED_TO_SMS_LEGACY if hasattr(rrv, n)] == []
    assert [n for n in _MOVED_TO_SMS_LEGACY if not hasattr(sms_legacy, n)] == []


def test_with_providers_on_sms_legacy_is_the_unrouted_backend():
    assert backends.unrouted() is backends.SMS_LEGACY
    assert backends.VIVA_CORE.unrouted is None and backends.COMPOSE.unrouted is None


def test_two_backends_declaring_the_unrouted_facet_are_refused(monkeypatch):
    from vivarium_workbench_sms.sms_legacy import SmsLegacyRuns, SmsLegacyUnrouted

    class Two:
        name = "two"

        def backends(self):
            return (backends.Backend("a", SmsLegacyRuns(), unrouted=SmsLegacyUnrouted()),
                    backends.Backend("b", SmsLegacyRuns(), unrouted=SmsLegacyUnrouted()))

    monkeypatch.setattr(provider_mod, "providers", lambda: (Two(),))
    backends.reset_registry()
    with pytest.raises(RuntimeError, match="unrouted"):
        backends.unrouted()


def _sms_record_ws(tmp_path, monkeypatch, sim_id="12"):
    """A workspace whose study ``s`` holds an sms-legacy record for ``sim_id`` on the configured SMS host."""
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.backends import records
    from vivarium_workbench_sms.sms_api_client import BACKEND_BASE_ENV_VARS

    for v in BACKEND_BASE_ENV_VARS + ("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT",):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("SMS_API_BASE", "http://api:8000")
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: None)
    monkeypatch.setattr(records, "refresh_server_id", lambda url, **_: None)
    ws = tmp_path / "ws"
    (ws / "studies" / "s").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    conn = cr.connect(ws / "studies" / "s" / "runs.db")
    try:
        cr.save_metadata(conn, spec_id="c", run_id=f"remote-pending-{sim_id}", label=None, started_at=0.0, n_steps=0,
                         params={"source": "smsvpctest", "simulation_id": sim_id, "backend": "ray",
                                 "remote": {"kind": "sms-legacy", "id": sim_id, "base_url": "http://api:8000",
                                            "deployment": "smsvpctest", "server_id": None}})
    finally:
        conn.close()
    return ws


def test_with_providers_on_an_sms_runs_status_keeps_its_analysis_id(tmp_path, monkeypatch, inproc_client):
    """The status rule ``chosen is not None and chosen.unrouted is None``: an SMS run's poll goes to the unrouted
    facet with every id it carries (analysis beats simulation), never to ``runs.status(rid)``, which would drop it."""
    from vivarium_workbench_sms import sms_legacy

    ws = _sms_record_ws(tmp_path, monkeypatch)
    assert backends.for_existing(ws, "12") is backends.SMS_LEGACY  # the record routes it to SMS

    class Client:
        def __init__(self, base=None):
            pass

        def analysis_status(self, aid):
            return {"status": "completed"}

        def simulation_status(self, sid):
            raise AssertionError("the analysis id must win")

    monkeypatch.setattr(sms_legacy, "SmsApiClient", Client)
    r = inproc_client(ws).get("/api/remote-run-poll?simulation_id=12&analysis_id=5")
    assert r.status_code == 200
    assert (r.json()["kind"], r.json()["analysis_id"]) == ("analysis", 5)


def test_with_providers_off_there_is_no_unrouted_backend(providers_off):
    assert backends.unrouted() is None
    assert backends.SMS_LEGACY.unrouted is None  # the inert sentinel


_REFUSED = (409, "backend-not-installed", "backend-not-installed", True)


@pytest.mark.parametrize("method, path, payload, expected", [
    # no id: the old 400 texts, without the SMS login check before them
    ("post", "/api/remote-run-land", {"study": "s"}, (400, "study and simulation_id are required")),
    ("get", "/api/remote-run-poll", {}, (400, "simulator_id, simulation_id or analysis_id required")),
    ("get", "/api/remote-run-chain-progress", {"run_id": "0"}, (400, "simulation_id required")),
    ("post", "/api/remote-run-cancel", {}, (400, "simulation_id required")),
    # an id no active backend owns, or a build / analysis id: refused, terminal
    ("post", "/api/remote-run-land", {"study": "s", "simulation_id": 12}, _REFUSED),
    ("get", "/api/remote-run-poll", {"simulation_id": 12}, _REFUSED),
    ("get", "/api/remote-run-poll", {"simulator_id": 3}, _REFUSED),
    ("get", "/api/remote-run-poll", {"analysis_id": 5}, _REFUSED),
    ("get", "/api/remote-run-chain-progress", {"simulation_id": 12}, _REFUSED),
    ("post", "/api/remote-run-cancel", {"simulation_id": 12}, _REFUSED),
    ("post", "/api/remote-run-submit", {"study": "s", "simulator_id": 3}, _REFUSED),
    # nothing named at all
    ("post", "/api/remote-run-submit", {"study": "s"}, (409, "no-backend")),
])
def test_with_providers_off_the_core_answers_unrouted_requests(providers_off, tmp_path, monkeypatch, inproc_client,
                                                               method, path, payload, expected):
    from vivarium_workbench.lib import server_capabilities as sc
    from vivarium_workbench_sms import sms_legacy

    monkeypatch.setattr(sc, "explicit_backend_profile", lambda: None)
    # Nothing may reach SMS: its login check would answer 401 first, and its client would be asked.
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: pytest.fail("the SMS login check was consulted"))
    monkeypatch.setattr(sms_legacy, "SmsApiClient", lambda *a, **k: pytest.fail("SMS was asked"))
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    client = inproc_client(tmp_path)
    r = client.get(f"{path}?{urlencode(payload)}") if method == "get" else client.post(path, json=payload)
    body = r.json()
    if expected == _REFUSED:
        assert (r.status_code, body.get("reason"), body.get("phase"), body.get("terminal")) == _REFUSED, body
    elif expected[0] == 409:
        assert (r.status_code, body["reason"]) == expected, body
    else:
        assert (r.status_code, body["error"]) == expected, body
