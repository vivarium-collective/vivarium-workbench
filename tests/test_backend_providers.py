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
        (tmp_path / ".viv-build.json").write_text("{}")  # any stamp: SmsProvider.session_build claims it
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
    "SmsApiClient", "SmsApiError", "_sms_api_base", "load_spec", "_run_auth_ok",
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
    # step P2 Analyses and figures: no backend offers artifacts or analyses (before it, an integer id reached SMS)
    ("post", "/api/remote-run-land-artifacts", {"run_id": "r"}, (400, "simulation_id and run_id are required")),
    ("post", "/api/remote-run-land-artifacts", {"simulation_id": 12, "run_id": "r"}, _REFUSED),
    ("post", "/api/remote-run-analysis", {}, (400, "simulation_id is required")),
    ("post", "/api/remote-run-analysis", {"simulation_id": 12}, _REFUSED),
    ("get", "/api/remote-analysis-figures", {"simulation_id": 12}, _REFUSED),
    ("get", "/api/remote-analysis-figure", {"simulation_id": 12, "analysis": "a", "path": "viz/x.png"}, _REFUSED),
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


def test_with_providers_off_the_study_gallery_is_empty(providers_off, tmp_path, monkeypatch, inproc_client):
    from vivarium_workbench_sms import sms_legacy

    monkeypatch.setattr(sms_legacy, "SmsApiClient", lambda *a, **k: pytest.fail("SMS was asked"))
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    r = inproc_client(tmp_path).get("/api/study-remote-figures?study=s")
    assert (r.status_code, r.json()) == (200, {"available": False, "reason": "no-backend", "study": "s", "sims": []})


# --- the optional artifacts / analyses facets (step P2, Analyses and figures) ---------------------------------------

def test_only_sms_legacy_offers_the_artifacts_and_analyses_facets():
    from vivarium_workbench_sms.analyses import SmsAnalyses, SmsArtifacts

    sms = backends.SMS_LEGACY
    assert isinstance(sms.artifacts, SmsArtifacts) and isinstance(sms.analyses, SmsAnalyses)
    for method in ("land_outputs", "list_figures", "read_figure", "study_figures", "fetch_store"):
        assert callable(getattr(sms.artifacts, method)), method
    assert callable(sms.analyses.rerun)
    for backend in (backends.COMPOSE, backends.VIVA_CORE, *backends._SENTINELS.values()):
        assert (backend.artifacts, backend.analyses) == (None, None), backend.name
    assert backends.offering("artifacts") == (sms,) and backends.offering("analyses") == (sms,)
    assert backends.unrouted() is sms  # the no-id land / re-run requests go to its facets


def test_with_providers_off_no_backend_offers_artifacts_or_analyses(providers_off):
    assert backends.offering("artifacts") == () and backends.offering("analyses") == ()


# --- the optional listing facet (step P2, Remote tab R1) -------------------------------------------------------------

def test_only_sms_legacy_offers_the_listing_facet():
    from vivarium_workbench_sms.remote_simulations import SmsListing

    sms = backends.SMS_LEGACY
    assert isinstance(sms.listing, SmsListing)
    for method in ("list_runs", "state", "invalidate"):
        assert callable(getattr(sms.listing, method)), method
    for backend in (backends.COMPOSE, backends.VIVA_CORE, *backends._SENTINELS.values()):
        assert backend.listing is None, backend.name
    assert backends.offering("listing") == (sms,)


def test_with_providers_off_no_backend_offers_the_listing(providers_off):
    assert backends.offering("listing") == ()


def test_with_providers_off_a_runs_store_is_never_fetched(providers_off, tmp_path, monkeypatch, inproc_client):
    """Step P2 Remote tab R2: an s3-only row of an SMS simulation (no record: routed by its id's form) has no backend
    offering ``artifacts`` with providers off, so the Results zip is a 404 and SMS is never asked."""
    from vivarium_workbench.lib import simulations_index as si
    from vivarium_workbench_sms import sms_api_client, sms_legacy

    monkeypatch.setattr(sms_api_client.SmsApiClient, "__init__", lambda *a, **k: pytest.fail("SMS was asked"))
    monkeypatch.setattr(sms_legacy, "SmsApiClient", lambda *a, **k: pytest.fail("SMS was asked"))
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    row = {"run_id": "r", "db_path": None, "store_path": "s3://bucket/r", "study_slug": None,
           "remote_origin": {"simulation_id": 113, "deployment": "smsvpctest"}}
    assert si.resolve_or_fetch_store(tmp_path, row) == (None, None)
    monkeypatch.setattr(si, "list_simulations", lambda workspace: [row])
    r = inproc_client(tmp_path).get("/api/simulation-run-download?run_id=r")
    assert (r.status_code, r.json()) == (404, {"error": "run data not found: r"})


# --- the optional batch status (step P2, Runs c2) ---------------------------------------------------------------------

def test_only_sms_legacy_declares_the_optional_batch_status():
    """``runs.status_many`` (:class:`backends.BatchStatus`) is optional and found by ``getattr``: SMS declares it (its
    one ``compose_status_batch`` call), compose-api and viva-core do not, and neither does any inert sentinel nor the
    unrouted facet."""
    assert callable(getattr(backends.SMS_LEGACY.runs, "status_many", None))
    assert not hasattr(backends.COMPOSE.runs, "status_many")
    assert not hasattr(backends.VIVA_CORE.runs, "status_many")
    for sentinel in backends._SENTINELS.values():
        assert not hasattr(sentinel.runs, "status_many")
    assert not hasattr(backends.SMS_LEGACY.unrouted, "status_many")


# --- step P2 Runs b: the deployment runner's facet method -----------------------------------------------------------

def test_every_backend_runs_a_composite_and_the_sentinels_refuse_it(tmp_path):
    """``RunFacet.run_composite`` (the detached runner's blocking call) on every active backend; the unrouted facet
    has it too; an inert sentinel refuses it rather than answering."""
    for backend in backends._ALL:
        assert callable(getattr(backend.runs, "run_composite", None)), backend.name
        if backend.unrouted is not None:
            assert callable(getattr(backend.unrouted, "run_composite", None)), backend.name
    for sentinel in backends._SENTINELS.values():
        with pytest.raises(RuntimeError, match="provider is not active"):
            sentinel.runs.run_composite(tmp_path, "c", dest=tmp_path, n_steps=1, overrides=None, emit_paths=None,
                                        analysis_options=None, build_ref=None, poll_interval=0, poll_timeout=0)


# --- the plugin router (step P2, Runs a2) ---------------------------------------------------------------------------

#: The SMS-only routes ``SmsProvider.router()`` serves, and how a router-less app answers each (Starlette: a POST
#: matches the GET-only catch-all's path -> 405; a GET falls through to it and misses -> an empty 404).
_SMS_ONLY_ROUTES = (
    ("post", "/api/remote-run-start", 405),
    ("get", "/api/remote-run-status", 404),
    ("post", "/api/remote-run-build", 405),
    ("post", "/api/remote-run-pinned-build", 405),
    # step P2 Builds B3: the Source panel's build routes (with providers off its build list shows "unreachable")
    ("get", "/api/source/builds", 404),
    ("post", "/api/source/build-remote", 405),
    ("post", "/api/source/switch-build", 405),
)

#: What moved from lib.remote_run_views to the plugin's remote_pipeline_views (the three views and their helper), and
#: what remote_run_views no longer binds (the pipeline seams, read through remote_run_jobs, and the modules only those
#: views used).
_MOVED_TO_PIPELINE_VIEWS = ("remote_run_start", "remote_run_build_start", "remote_run_pinned_build_start",
                            "_resolve_repo_branch")
_DROPPED_FROM_RRV = ("PipelineCtx", "manager", "run_remote_pipeline", "github_auth", "git_status", "subprocess")


def _app_routes(app):
    return {(r.path, m.lower()): r for r in app.router.routes if getattr(r, "methods", None) for m in r.methods}


def test_the_sms_only_views_moved_to_the_plugin_and_bind_no_pipeline_seam():
    from vivarium_workbench.lib import remote_run_views as rrv
    from vivarium_workbench_sms import remote_pipeline_views as pv

    assert [n for n in _MOVED_TO_PIPELINE_VIEWS + _DROPPED_FROM_RRV if hasattr(rrv, n)] == []
    assert [n for n in _MOVED_TO_PIPELINE_VIEWS if not hasattr(pv, n)] == []
    # The pipeline seams are read through remote_run_jobs and the SMS ones through sms_legacy: one patch target each.
    assert [n for n in ("PipelineCtx", "manager", "run_remote_pipeline") + _MOVED_TO_SMS_LEGACY if hasattr(pv, n)] == []


def test_the_registry_does_not_load_the_routes_or_the_pipeline():
    """An env worker loads the provider for the registry (``backends.active`` -> ``_registry``); that must not pull in
    the plugin's routes, its views or the in-process pipeline (``remote_run_jobs``), which only ``create_app`` needs."""
    import subprocess
    import sys

    code = ("import sys\n"
            "from vivarium_workbench.lib import backends\n"
            "assert [b.name for b in backends._ALL] == ['compose-api', 'viva-core', 'sms-legacy']\n"
            "import vivarium_workbench_sms.sms_legacy\n"
            "mods = ('vivarium_workbench_sms.routes', 'vivarium_workbench_sms.remote_pipeline_views',\n"
            "        'vivarium_workbench_sms.remote_run_jobs', 'vivarium_workbench.api.app')\n"
            "print(sorted(m for m in mods if m in sys.modules))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_with_providers_on_the_sms_only_routes_are_served_under_the_app_wide_guards():
    from vivarium_workbench.api import app as appmod

    app = appmod.create_app()
    routes = _app_routes(app)
    paths = app.openapi()["paths"]
    for method, path, _ in _SMS_ONLY_ROUTES:
        route = routes[(path, method)]
        assert route.endpoint.__module__ == "vivarium_workbench_sms.routes"
        assert appmod._plain_identifiers in [d.call for d in route.dependant.dependencies], path
        assert method in paths[path]
    # Included before the SPA catch-all, which stays last.
    assert app.router.routes[-1].path == "/{rel:path}"
    assert app.state.readonly_allowed_mutations >= {"/api/remote-run-start", "/api/remote-run-build"}
    assert "/api/remote-run-pinned-build" not in app.state.readonly_allowed_mutations


def test_read_only_keeps_start_build_and_status_and_drops_pinned_build(tmp_path, monkeypatch, inproc_client):
    from vivarium_workbench.lib import github_auth

    monkeypatch.setenv("VIVARIUM_WORKBENCH_READONLY", "1")
    monkeypatch.setattr(github_auth, "current_session", lambda: None)
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    client = inproc_client(tmp_path)
    # kept: the views answer (401 is the views' own login check)
    assert client.post("/api/remote-run-start", json={"study": "s"}).status_code == 401
    assert client.post("/api/remote-run-build", json={"study": "s"}).status_code == 401
    assert client.get("/api/remote-run-status").status_code == 200
    # dropped, as before step P2 Runs a2
    assert client.post("/api/remote-run-pinned-build", json={}).status_code == 405


@pytest.mark.parametrize("method, path, status", _SMS_ONLY_ROUTES)
def test_with_providers_off_the_sms_only_routes_are_absent(providers_off, tmp_path, inproc_client,
                                                          method, path, status):
    from vivarium_workbench.api import app as appmod

    app = appmod.create_app()
    assert path not in app.openapi()["paths"]
    assert (path, method) not in _app_routes(app)
    assert app.state.readonly_allowed_mutations == frozenset(appmod._READONLY_ALLOWED_MUTATIONS)
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    client = inproc_client(tmp_path)
    r = client.get(path) if method == "get" else client.post(path, json={"study": "s"})
    assert r.status_code == status


def _fake_provider(router, readonly_allowed=frozenset(), ai_excluded=frozenset()):
    from vivarium_workbench.lib.backends.provider import ProviderRouter

    class Fake:
        name = "fake"

        def router(self):
            return ProviderRouter(router, frozenset(readonly_allowed), frozenset(ai_excluded))

    return Fake()


def test_a_provider_route_that_clashes_with_a_core_route_is_refused(monkeypatch):
    from fastapi import APIRouter

    from vivarium_workbench.api import app as appmod

    r = APIRouter()
    r.post("/api/remote-run-submit")(lambda: None)
    monkeypatch.setattr(provider_mod, "providers", lambda: (_fake_provider(r),))
    with pytest.raises(RuntimeError, match="clash"):
        appmod.create_app()


def test_a_provider_cannot_allowlist_a_path_that_is_not_its_own(monkeypatch):
    from fastapi import APIRouter

    from vivarium_workbench.api import app as appmod

    r = APIRouter()
    r.post("/api/fake-own")(lambda: None)
    monkeypatch.setattr(provider_mod, "providers",
                        lambda: (_fake_provider(r, {"/api/fake-own", "/api/study-create"}),))
    with pytest.raises(RuntimeError, match="not its own"):
        appmod.create_app()


def test_a_provider_cannot_ai_exclude_a_path_that_is_not_its_own(monkeypatch):
    """Step P2 Routes Rt2: ``ProviderRouter.ai_excluded`` speaks only for the provider's own routes."""
    from fastapi import APIRouter

    from vivarium_workbench.api import app as appmod

    r = APIRouter()
    r.post("/api/fake-own")(lambda: None)
    monkeypatch.setattr(provider_mod, "providers",
                        lambda: (_fake_provider(r, ai_excluded={"/api/fake-own", "/api/study-create"}),))
    with pytest.raises(RuntimeError, match="AI-excluded paths that are not its own"):
        appmod.create_app()


def test_a_provider_ai_excluded_path_leaves_the_ai_index(monkeypatch):
    pytest.importorskip("pydantic_ai")
    from fastapi import APIRouter

    from vivarium_workbench.api import app as appmod
    from vivarium_workbench.lib import ai_tools

    r = APIRouter()
    r.post("/api/fake-pushes")(lambda: None)
    r.post("/api/fake-local")(lambda: None)
    monkeypatch.setattr(provider_mod, "providers",
                        lambda: (_fake_provider(r, ai_excluded={"/api/fake-pushes"}),))
    app = appmod.create_app()
    assert app.state.provider_ai_excluded == frozenset({"/api/fake-pushes"})
    served = {(e["method"], e["path"]) for e in ai_tools.build_index(app).values()}
    assert ("POST", "/api/fake-local") in served
    assert ("POST", "/api/fake-pushes") not in served
    assert "/api/fake-pushes" in ai_tools.capabilities(app)["excluded"]


# --- step P2 Builds B2: the card's dispatch and the dispatch preflight ----------------------------------------------

def test_every_backend_dispatches_a_card_run_and_answers_the_preflight_and_the_sentinels_refuse(tmp_path):
    """``RunFacet.dispatch_preflight`` / ``dispatch_composite`` on every active backend; the unrouted facet has
    ``dispatch_composite`` (the explicit cloud run no backend selected); an inert sentinel refuses both."""
    for backend in backends._ALL:
        assert callable(getattr(backend.runs, "dispatch_preflight", None)), backend.name
        assert callable(getattr(backend.runs, "dispatch_composite", None)), backend.name
        if backend.unrouted is not None:
            assert callable(getattr(backend.unrouted, "dispatch_composite", None)), backend.name
    for sentinel in backends._SENTINELS.values():
        with pytest.raises(RuntimeError, match="provider is not active"):
            sentinel.runs.dispatch_preflight(tmp_path)
        with pytest.raises(RuntimeError, match="provider is not active"):
            sentinel.runs.dispatch_composite(tmp_path, {}, spec_id="c", overrides={}, emit_paths=[], steps=1)


def _card_ws(tmp_path, monkeypatch):
    from vivarium_workbench.lib import run_registry

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    spawned: list = []
    monkeypatch.setattr(run_registry, "spawn_detached", lambda *a, **k: (spawned.append(a), 1)[1])
    return spawned


def test_with_providers_off_an_explicit_cloud_run_is_refused_no_backend(providers_off, tmp_path, monkeypatch):
    """Declared change (step P2 Builds B2): the Environment picker's explicit cloud run, with nothing selected, goes
    to the unrouted facet; with providers off there is none, so it is refused (409 ``no-backend``) and nothing is
    sent or spawned. Before B2 it reached the SMS dispatch (a ``run_simulation`` call, or SMS's no-build 409)."""
    from vivarium_workbench.lib import composite_test_run_views as ctr
    from vivarium_workbench_sms import sms_api_client as sac

    spawned = _card_ws(tmp_path, monkeypatch)
    monkeypatch.setattr(sac.SmsApiClient, "run_simulation",
                        lambda self, **kw: (_ for _ in ()).throw(AssertionError("SMS was asked")))
    for body in ({"run_target": "deployment", "build": {"simulator_id": 211, "repo_url": "r", "commit": "c"}},
                 {"run_target": "deployment"}):
        assert ctr.composite_test_run(tmp_path, {"id": "pkg.composites.x", **body}) == (
            {"error": "no remote backend was named (serve --backend-base-url)", "reason": "no-backend",
             "run_target": "deployment"}, 409)
    assert spawned == []


def test_with_providers_off_a_stamped_or_pinned_card_run_is_local(providers_off, tmp_path, monkeypatch):
    """Unchanged by B2 (nothing claims a session build or a pin with providers off): the card runs locally."""
    import json

    from vivarium_workbench.lib import composite_test_run_views as ctr

    spawned = _card_ws(tmp_path, monkeypatch)
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 124, "repo_url": "r", "commit": "c"}')
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "https://github.com/x/sms-ecoli.git")
    resp, status = ctr.composite_test_run(tmp_path, {"id": "pkg.composites.x"})
    assert status == 202 and len(spawned) == 1
    req = json.loads((tmp_path / ".pbg" / "runs" / resp["run_id"] / "request.json").read_text())
    assert (req["target"], req["build_ref"]) == ("local", None)


#: Names that moved from the core's composite card (lib.composite_test_run_views) to the plugin's cloud_dispatch in
#: step P2 Builds B2. The card must bind none of them: a stale ``monkeypatch.setattr(ctr, name, ...)`` then raises
#: instead of silently patching a dead copy.
_MOVED_TO_CLOUD_DISPATCH = (
    "_WHOLE_CELL_DEFAULT_CONFIG", "_config_for_spec", "_dispatch_build_image_run", "CloudTarget", "_COMPOSE_TRUTHY",
    "_compose_dispatch_allowed", "cloud_build_actions", "_no_image", "_explicit_build_ref", "resolve_cloud_target",
)


def test_the_card_binds_no_sms_seam():
    from vivarium_workbench.lib import composite_test_run_views as ctr
    from vivarium_workbench_sms import cloud_dispatch

    assert [n for n in _MOVED_TO_CLOUD_DISPATCH if hasattr(ctr, n)] == []
    assert [n for n in _MOVED_TO_CLOUD_DISPATCH if not hasattr(cloud_dispatch, n)] == []


def test_viva_cores_environment_grammar_left_the_core():
    """``backend_environment`` / ``DEFAULT_BACKEND_ENVIRONMENT`` (viva-core's reading of the shared
    ``VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT``) live in the plugin since step P2 Builds B2, with no core alias."""
    from vivarium_workbench.lib import remote_run
    from vivarium_workbench_sms import remote_run_viva_v1

    for name in ("backend_environment", "DEFAULT_BACKEND_ENVIRONMENT"):
        assert not hasattr(remote_run, name), name
        assert hasattr(remote_run_viva_v1, name), name


def test_cloud_dispatch_reads_every_seam_through_its_module():
    """The moved card code reads core names (``remote_run``, ``backends``) and the plugin's own (``remote_pinned``,
    ``sms_api_client``) as ``<module>.<name>`` at call time, never from-imports them: tests patch them on those
    modules, and a from-imported copy would make those patches silently dead."""
    import ast
    from pathlib import Path

    import vivarium_workbench_sms.cloud_dispatch as cd

    watched = {"vivarium_workbench.lib.remote_run", "vivarium_workbench.lib.backends",
               "vivarium_workbench_sms.remote_pinned", "vivarium_workbench_sms.sms_api_client"}
    tree = ast.parse(Path(cd.__file__).read_text(encoding="utf-8"))
    offenders = [f"{n.module}:{n.lineno}" for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module in watched]
    assert offenders == []
# --- the builds facet and the build routes (step P2, Builds B3) ------------------------------------------------------

#: The build routes the SMS router declares read-only-safe (the remote-build flow, "the core of remote-only").
_BUILD_POSTS = frozenset({"/api/source/build-remote", "/api/source/switch-build"})


def test_only_sms_legacy_declares_builds_and_the_sentinels_do_not():
    from vivarium_workbench_sms.sms_legacy import SmsLegacyBuilds

    assert isinstance(backends.SMS_LEGACY.builds, SmsLegacyBuilds)
    assert [b.name for b in backends._ALL if b.builds is not None] == ["sms-legacy"]
    assert all(s.builds is None for s in backends._SENTINELS.values())


def test_the_pin_the_study_dispatch_and_the_build_routes_left_the_core():
    """Permanent anti-seam test: the moved names are gone from the core (a stale patch on them raises), and the
    alias users read ``sms_api_client.sms_api_base`` through its module instead of ``workspace_deps_views``."""
    import ast
    import inspect

    from vivarium_workbench.api import app as appmod
    from vivarium_workbench.lib import remote_run_views as rrv
    from vivarium_workbench.lib import study_runs
    from vivarium_workbench.lib import workspace_deps_views as wdv
    from vivarium_workbench_sms import source_build_views as sbv

    assert [n for n in ("_sms_api_base", "build_source_builds") if hasattr(wdv, n)] == []
    assert [n for n in ("_remote_run_config", "remote_pinned") if hasattr(rrv, n)] == []
    assert not hasattr(appmod, "_source_build_views")
    assert hasattr(sbv, "build_source_builds")
    # study_runs names no plugin module at all (its dispatch is ``builds.run_study``).
    tree = ast.parse(inspect.getsource(study_runs))
    plugin = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
              and (n.module or "").startswith("vivarium_workbench_sms")]
    assert plugin == []


def test_the_build_routes_are_the_plugins_and_read_only_keeps_the_two_posts():
    from vivarium_workbench.api import app as appmod

    app = appmod.create_app()
    routes = _app_routes(app)
    for path, method in (("/api/source/builds", "get"), ("/api/source/build-remote", "post"),
                         ("/api/source/switch-build", "post")):
        assert routes[(path, method)].endpoint.__module__ == "vivarium_workbench_sms.routes"
    assert app.state.readonly_allowed_mutations >= _BUILD_POSTS
    assert not (_BUILD_POSTS & set(appmod._READONLY_ALLOWED_MUTATIONS))  # declared by the plugin, not the core
    # The core's switch stays core and keeps its read-only exemption.
    assert routes[("/api/source/switch", "post")].endpoint.__module__ == "vivarium_workbench.api.app"
    assert "/api/source/switch" in appmod._READONLY_ALLOWED_MUTATIONS


def test_with_providers_off_the_pin_is_not_reported_and_sms_is_not_asked(providers_off, tmp_path, monkeypatch,
                                                                       inproc_client):
    """Declared change (Builds B3), providers off only: a pin env var used to reach SMS's pin; now no backend with
    builds is selected, so the card reads ``pinned: false`` with the configured deployment name, and nothing is
    sent."""
    from vivarium_workbench_sms import remote_pinned
    from vivarium_workbench_sms import sms_api_client as sac

    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "https://github.com/o/r")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", "smscdk")
    monkeypatch.setattr(sac.SmsApiClient, "list_branch_builds", lambda *a, **k: pytest.fail("SMS was asked"))
    monkeypatch.setattr(remote_pinned, "resolved_from_session_build", lambda *a: pytest.fail("SMS's pin was read"))
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    r = inproc_client(tmp_path).get("/api/remote-run-config")
    assert (r.status_code, r.json()) == (200, {"pinned": False, "deployment": "smscdk"})


def test_bind_session_does_nothing_without_a_key_or_a_path(monkeypatch):
    from fastapi import Response

    from vivarium_workbench.api import deps
    from vivarium_workbench.lib import session_env, session_registry

    monkeypatch.setattr(session_registry, "rebind", lambda *a: pytest.fail("rebound"))
    monkeypatch.setattr(session_env, "prepare", lambda *a, **k: pytest.fail("prepared"))
    for key, body in ((None, {"source": {"path": "/x"}}), ("k", {"source": {}}), ("k", {})):
        resp = Response()
        deps.bind_session(None, resp, key, body, managed=True)  # type: ignore[arg-type]
        assert "set-cookie" not in resp.headers and "materialization" not in body




# --- step P2 Runs b2: the trace -------------------------------------------------------------------------------------

def test_every_backend_traces_and_the_unrouted_facet_traces_and_answers_support(tmp_path, monkeypatch):
    """``RunFacet.trace`` on every active backend -- real for viva-core and sms-legacy since Runs b2, no longer
    ``NotImplementedError`` -- and ``UnroutedFacet.trace`` / ``trace_support`` on the unrouted backend; an inert
    sentinel refuses ``trace``."""
    from vivarium_workbench_sms import sms_trace

    calls: list = []

    class Client:
        def capabilities(self):
            calls.append(("capabilities",))
            return {"version": "1", "capabilities": ["viva-v1-trace"]}

        def simulation_trace(self, sid):
            calls.append(("simulation_trace", sid))
            return b"{}"

        def composite_run_trace(self, rid):
            calls.append(("composite_run_trace", rid))
            return b"{}"

    monkeypatch.setattr(sms_trace, "make_client", Client)
    for backend in backends._ALL:
        assert callable(getattr(backend.runs, "trace", None)), backend.name
        if backend.unrouted is not None:
            assert callable(getattr(backend.unrouted, "trace", None)), backend.name
            assert callable(getattr(backend.unrouted, "trace_support", None)), backend.name
    assert backends.SMS_LEGACY.runs.trace("12") == (b"{}", 200, "simulation-12-trace.json")
    assert backends.VIVA_CORE.runs.trace("simulation-X") == (b"{}", 200, "composite-simulation-X-trace.json")
    assert calls == [("capabilities",), ("simulation_trace", 12), ("capabilities",),
                     ("composite_run_trace", "simulation-X")]
    unrouted = backends.unrouted().unrouted
    assert unrouted.trace({"simulation_id": None, "composite_run_id": None, "compose_id": None})[1] == 400
    assert unrouted.trace_support() == ({"supported": True, "reason": None, "capability": "viva-v1-trace",
                                         "server_version": "1"}, 200)
    for sentinel in backends._SENTINELS.values():
        with pytest.raises(RuntimeError, match="provider is not active"):
            sentinel.runs.trace("12")


_TRACE_REFUSED = (409, "backend-not-installed", "backend-not-installed", True)
_TRACE_ONE_ID = "give exactly one of simulation_id, composite_run_id, compose_id"


@pytest.mark.parametrize("query, expected", [
    # Declared change (Runs b2): one id that no record or id form routes is refused, terminal; before, the SMS
    # client was asked (or, for a non-integer simulation_id, a 400).
    ({"simulation_id": 12}, _TRACE_REFUSED),
    ({"simulation_id": "abc"}, _TRACE_REFUSED),
    ({"composite_run_id": "simulation-X"}, _TRACE_REFUSED),
    ({"compose_id": 7}, _TRACE_REFUSED),
    # Unchanged: no id, or several, is the old 400.
    ({}, (400, _TRACE_ONE_ID)),
    ({"simulation_id": 12, "compose_id": 7}, (400, _TRACE_ONE_ID)),
])
def test_with_providers_off_the_trace_never_reaches_sms(providers_off, tmp_path, monkeypatch, inproc_client,
                                                        query, expected):
    from vivarium_workbench.lib import server_capabilities as sc
    from vivarium_workbench_sms import sms_api_client, sms_legacy

    monkeypatch.setattr(sc, "explicit_backend_profile", lambda: None)
    monkeypatch.setattr(sms_api_client.SmsApiClient, "__init__", lambda *a, **k: pytest.fail("SMS was asked"))
    monkeypatch.setattr(sms_legacy, "SmsApiClient", lambda *a, **k: pytest.fail("SMS was asked"))
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    r = inproc_client(tmp_path).get(f"/api/remote-run-trace?{urlencode(query)}")
    body = r.json()
    if expected == _TRACE_REFUSED:
        assert (r.status_code, body.get("reason"), body.get("phase"), body.get("terminal")) == expected, body
        assert body["simulation_id"] == str(next(iter(query.values())))
    else:
        assert (r.status_code, body["error"]) == expected, body


def test_with_providers_off_trace_support_says_no_backend(providers_off, tmp_path, monkeypatch, inproc_client):
    """Declared change (Runs b2): with nothing named and providers off the support answer is ``no-backend``, with no
    request and the viewer as always (``perfetto-open.js`` keeps the action hidden); before, the SMS client was asked
    at the configured or default URL."""
    from vivarium_workbench.lib import server_capabilities as sc
    from vivarium_workbench_sms import sms_api_client

    monkeypatch.setattr(sc, "explicit_backend_profile", lambda: None)
    monkeypatch.setattr(sms_api_client.SmsApiClient, "__init__", lambda *a, **k: pytest.fail("SMS was asked"))
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI", "off")
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    r = inproc_client(tmp_path).get("/api/remote-run-trace-support")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["supported", "reason", "capability", "error", "viewer"]
    assert (body["supported"], body["reason"], body["capability"]) == (False, "no-backend", None)
    assert body["viewer"]["mode"] == "off"


def test_with_providers_off_a_compose_run_is_still_traced_by_compose_api(providers_off, tmp_path, monkeypatch):
    """compose-api is core: its runs are traced with providers off, under either key, and a named compose-api
    still says it serves traces."""
    from vivarium_workbench.lib import remote_run_compose, remote_trace
    from vivarium_workbench.lib import server_capabilities as sc
    from vivarium_workbench_sms import sms_api_client

    monkeypatch.setattr(sc, "explicit_backend_profile",
                        lambda: {"reachable": True, "dispatch": sc.DISPATCH_COMPOSE, "error": None})
    monkeypatch.setattr(sms_api_client.SmsApiClient, "__init__", lambda *a, **k: pytest.fail("SMS was asked"))
    monkeypatch.setattr(remote_run_compose, "trace", lambda rid: (b"{}", 200, f"{rid}.json"))
    for key in ("simulation_id", "composite_run_id"):
        assert remote_trace.fetch_trace(ws_root=tmp_path, **{key: "compose-6"}) == (b"{}", 200, "compose-6.json")
    body, status = remote_trace.trace_support()
    assert (status, body["supported"], body["capability"]) == (200, True, "get-simulation-trace-chrome")


def test_with_providers_off_a_blank_trace_id_is_no_id(providers_off, tmp_path):
    """A whitespace-only id counts as no id (as the run views' ``_first_id``): 400, not a 409 naming an empty run."""
    from vivarium_workbench.lib import remote_trace

    body, status, _ = remote_trace.fetch_trace(ws_root=tmp_path, simulation_id="  ")
    assert status == 400, body
