"""The core's generic backend plumbing (docs/backend-adapters.md step P1a, ``lib/backend_config.py``).

P1a moved these out of the SMS modules with no behaviour change. These tests pin that the SMS names still mean the
same thing (the wrappers and re-exports), that the moved functions behave as before, and that the ping the SMS client
keeps (until step P1b) agrees with the core's.

Step P3 K2a: what needs the SMS plugin (its names, its client, its aliases and deployment default) is marked
``plugin``; a test that checked the core function and the plugin's side together is split, so the core's half runs
core-only too.
"""

from __future__ import annotations

import io
from urllib.error import HTTPError, URLError

import pytest

from vivarium_workbench.lib import backend_config as bc


# --- the SMS names are the core's ----------------------------------------------------------------------------------

@pytest.mark.plugin
def test_sms_api_error_is_the_core_error():
    from vivarium_workbench_sms import remote_link
    from vivarium_workbench_sms import sms_api_client as sac

    assert sac.SmsApiError is bc.BackendError
    assert issubclass(remote_link.CircuitOpen, sac.SmsApiError)
    assert remote_link.CircuitOpen is bc.CircuitOpen
    assert issubclass(sac.BranchHeadUnresolved, bc.BackendError)


@pytest.mark.plugin
def test_one_breaker_registry():
    from vivarium_workbench_sms import remote_link

    assert remote_link._links is bc._links
    assert remote_link._links_lock is bc._links_lock
    assert remote_link.RemoteLink is bc.RemoteLink
    lk = remote_link.link("http://p1a-registry:1")
    assert bc.link("http://p1a-registry:1") is lk


@pytest.mark.plugin
def test_remote_link_defaults_to_the_sms_endpoint(monkeypatch):
    from vivarium_workbench_sms import remote_link

    monkeypatch.setenv("VIVA_API_BASE", "http://p1a-default:2")
    assert remote_link.link().base_url == "http://p1a-default:2"
    with pytest.raises(TypeError):
        bc.link()  # type: ignore[call-arg]  -- the core requires a URL; the SMS default is remote_link's


@pytest.mark.plugin
@pytest.mark.parametrize("name", [
    "BackendConfigError", "explicit_backend_base_url", "normalize_backend_base_url", "IDENTITY_HEADER",
    "caller_identity", "_http_error_detail",
])
def test_reexports_are_the_core_objects(name):
    from vivarium_workbench_sms import sms_api_client as sac

    assert getattr(sac, name) is getattr(bc, name)


def test_the_core_keeps_no_sms_config_name():
    """Step P3 C1: the tunnel default and the one-backend check left the core (split from the plugin's half below,
    so this runs core-only: docs/backend-adapters.md step P3, K2a)."""
    assert not hasattr(bc, "DEFAULT_BACKEND_BASE") and not hasattr(bc, "check_single_backend")
    assert not hasattr(bc, "backend_configured")


@pytest.mark.plugin
def test_the_config_names_are_not_reexported():
    """Step P3 C1: the alias names, the tunnel default and the one-backend check are the plugin's own (or the
    core's ``backends.config``); the old re-exports are gone, not aliased."""
    from vivarium_workbench_sms import sms_api_client as sac

    for name in ("BACKEND_BASE_ENV_VARS", "backend_configured", "check_single_backend"):
        assert not hasattr(sac, name), name
    assert sac.DEFAULT_BACKEND_BASE == "http://localhost:8080"
    assert bc.BACKEND_BASE_ENV_VARS == (bc.BACKEND_BASE_ENV, *sac.ALIAS_ENV_VARS)  # the core's warning list agrees


# --- the moved functions behave as before --------------------------------------------------------------------------

_BASE_URLS = [
    ({}, "http://localhost:8080"),
    ({"SMS_API_BASE": "http://legacy:1"}, "http://legacy:1"),
    ({"VIVA_API_BASE": "http://viva:1", "SMS_API_BASE": "http://legacy:1"}, "http://viva:1"),
    ({"VIVARIUM_WORKBENCH_BACKEND_BASE_URL": "http://named:1", "VIVA_API_BASE": "http://viva:1"}, "http://named:1"),
]


def _set_env(monkeypatch, env):
    for var in bc.BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize("env, expected", _BASE_URLS)
def test_the_core_alone_knows_only_the_named_backend(monkeypatch, env, expected):
    """Step P3 C1 (split from the provider half below so it runs core-only: docs/backend-adapters.md step P3, K2a)."""
    _set_env(monkeypatch, env)
    assert bc.configured_base_url() == env.get("VIVARIUM_WORKBENCH_BACKEND_BASE_URL")


@pytest.mark.plugin
@pytest.mark.parametrize("env, expected", _BASE_URLS)
def test_configured_base_url_precedence(monkeypatch, env, expected):
    from vivarium_workbench_sms import sms_api_client as sac

    from vivarium_workbench.lib.backends import config

    _set_env(monkeypatch, env)
    assert config.configured_base_url() == expected
    assert sac.sms_api_base() == expected


@pytest.mark.plugin
def test_the_provider_base_url_reads_sms_api_base_at_call_time(monkeypatch):
    from vivarium_workbench_sms import sms_api_client as sac

    from vivarium_workbench.lib.backends import config

    monkeypatch.delenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", raising=False)
    monkeypatch.setattr(sac, "sms_api_base", lambda: "http://patched:1")
    assert config.configured_base_url() == "http://patched:1"


_DEPLOYMENTS = [
    ({}, None, "smsvpctest"),
    ({"VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT": "smscdk"}, None, "smscdk"),
    ({}, "https://sms.cam.uchc.edu", "sms.cam.uchc.edu"),
    ({"VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT": "smscdk"}, "https://sms.cam.uchc.edu", "smscdk"),
]


def _set_deployment_env(monkeypatch, env, named):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", raising=False)
    monkeypatch.delenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if named:
        monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", named)


@pytest.mark.parametrize("env, named, expected", _DEPLOYMENTS)
def test_the_cores_deployment_default_is_neutral(monkeypatch, env, named, expected):
    """The core's own default is neutral (step P3 C1); the plugin's default is passed in (split from the provider
    half below so it runs core-only: docs/backend-adapters.md step P3, K2a)."""
    _set_deployment_env(monkeypatch, env, named)
    assert bc._DEFAULT_DEPLOYMENT == "default"
    assert bc.deployment_name() == (expected if expected != "smsvpctest" else "default")


@pytest.mark.plugin
@pytest.mark.parametrize("env, named, expected", _DEPLOYMENTS)
def test_deployment_name(monkeypatch, env, named, expected):
    from vivarium_workbench_sms import remote_pinned

    from vivarium_workbench.lib.backends import config

    _set_deployment_env(monkeypatch, env, named)
    assert config.deployment_name() == expected
    assert remote_pinned.remote_deployment_name() == expected
    assert remote_pinned._DEFAULT_REMOTE_DEPLOYMENT == "smsvpctest"


@pytest.mark.plugin
def test_remote_deployment_name_reads_the_core_at_call_time(monkeypatch):
    from vivarium_workbench_sms import remote_pinned

    monkeypatch.setattr(bc, "deployment_name", lambda default=None: f"patched-{default}")
    assert remote_pinned.remote_deployment_name() == "patched-smsvpctest"


def test_the_core_half_of_the_check_never_reads_an_alias(monkeypatch):
    """Split from ``test_check_config`` below so it runs core-only (docs/backend-adapters.md step P3, K2a)."""
    for var in bc.BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "http://Named:1/")
    monkeypatch.setenv("SMS_API_BASE", "http://named:1")
    bc.named_backend()  # the core half: the named URL is valid
    monkeypatch.setenv("SMS_API_BASE", "http://other:1")
    assert bc.named_backend() == "http://Named:1/"  # the core half never reads an alias


@pytest.mark.plugin
def test_check_config(monkeypatch):
    """The aliases are the SMS plugin's (step P3 C1): only with it does the check compare them."""
    from vivarium_workbench.lib.backends import config

    for var in bc.BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "http://Named:1/")
    monkeypatch.setenv("SMS_API_BASE", "http://named:1")
    config.check_config()  # same URL after normalising
    monkeypatch.setenv("SMS_API_BASE", "http://other:1")
    with pytest.raises(bc.BackendConfigError):
        config.check_config()


def test_canonical_url():
    assert bc.canonical_url("HTTP://Host:8080/x/") == "http://host:8080/x"


# --- the core ping, and the SMS client's copy of it (until P1b) ----------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _sms():
    """The SMS client module, or None when the plugin is absent (the core-only job)."""
    try:
        from vivarium_workbench_sms import sms_api_client
    except ImportError:
        return None
    return sms_api_client


def _serve(monkeypatch, behaviour):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, dict(req.header_items()), timeout))
        return behaviour(req)

    monkeypatch.setattr(bc, "urlopen", fake_urlopen)
    # A fixed caller (not the machine's real GitHub sign-in): both pings must send the same identity header.
    monkeypatch.setattr(bc, "caller_identity", lambda: "tester@github")
    sac = _sms()
    if sac is not None:   # the SMS client's copy too, where the plugin is installed
        monkeypatch.setattr(sac, "urlopen", fake_urlopen)
        monkeypatch.setattr(sac, "caller_identity", lambda: "tester@github")
    return seen


@pytest.mark.plugin
@pytest.mark.parametrize("body, version", [(b'"0.9.27"', "0.9.27"), (b'{"version": "1.2.3"}', "1.2.3"),
                                           (b"plain", "plain")])
def test_ping_agrees_with_the_sms_client_on_success(monkeypatch, body, version):
    from vivarium_workbench_sms import sms_api_client as sac

    seen = _serve(monkeypatch, lambda req: _Resp(body))
    assert bc.ping("http://x:1") == version
    assert sac.SmsApiClient("http://x:1").ping() == version
    (core_url, core_headers, core_timeout), (sms_url, sms_headers, sms_timeout) = seen
    assert core_url == sms_url == "http://x:1/version"
    assert core_headers == sms_headers
    assert core_headers.get("X-auth-request-email") == "tester@github"
    assert core_timeout == sms_timeout == 5.0


@pytest.mark.plugin
def test_ping_agrees_with_the_sms_client_on_an_http_error(monkeypatch):
    from vivarium_workbench_sms import sms_api_client as sac

    def boom(req):
        raise HTTPError(req.full_url, 501, "nope", {}, io.BytesIO(b'{"detail": "not here"}'))

    _serve(monkeypatch, boom)
    with pytest.raises(bc.BackendError) as core:
        bc.ping("http://x:1")
    with pytest.raises(sac.SmsApiError) as sms:
        sac.SmsApiClient("http://x:1").ping()
    assert core.value.status == sms.value.status == 501
    assert str(core.value) == str(sms.value)


@pytest.mark.plugin
def test_ping_agrees_with_the_sms_client_when_unreachable(monkeypatch):
    from vivarium_workbench_sms import sms_api_client as sac

    def boom(req):
        raise URLError("connection refused")

    _serve(monkeypatch, boom)
    with pytest.raises(bc.BackendError) as core:
        bc.ping("http://x:1")
    with pytest.raises(sac.SmsApiError) as sms:
        sac.SmsApiClient("http://x:1").ping()
    assert core.value.status is None and sms.value.status is None
    # The text differs on purpose (step P3 C2): the core's is neutral, the SMS client keeps its own.
    assert str(core.value) == ("GET http://x:1/version failed (backend unreachable — is the endpoint up?): "
                               "<urlopen error connection refused>")
    assert str(sms.value) == ("GET http://x:1/version failed (sms-api unreachable — is the tunnel up?): "
                              "<urlopen error connection refused>")


def test_the_breaker_probe_pings_through_the_core(monkeypatch):
    _serve(monkeypatch, lambda req: _Resp(b'"1"'))
    sac = _sms()
    if sac is not None:   # with the plugin absent there is no SMS ping to use
        monkeypatch.setattr(sac, "urlopen", lambda *a, **k: pytest.fail("the probe must use the core ping"))
    lk = bc.RemoteLink("http://p1a-probe:1")
    assert lk.probe() is True
    assert lk.state == bc.STATE_UP


# --- server identity is the backend's (Backend.identity) -----------------------------------------------------------

def test_compose_api_reports_its_identity_through_its_own_function(monkeypatch):
    # compose-api identity (deliberate change): compose-api reports deployment_id on GET /health (compose-api#543).
    from vivarium_workbench.lib import backends
    from vivarium_workbench.lib.backends import compose_api, records

    assert backends.COMPOSE.identity is compose_api.compose_health_identity
    assert records.has_identity("compose-api") is True
    assert records.has_identity("no-such-kind") is False
    asked = []
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **k: asked.append((url, k)) or "c-1")
    rec = records.remote_record("compose-api", "compose-1", "http://compose:1")
    assert rec["server_id"] == "c-1" and asked == [("http://compose:1", {"kind": "compose-api"})]


@pytest.mark.plugin
def test_the_sms_providers_backends_report_their_identity():
    """viva-core and sms-legacy are the SMS provider's backends (split from the compose-api test above so that one
    runs core-only: docs/backend-adapters.md step P3, K2a)."""
    from vivarium_workbench.lib.backends import records

    assert records.has_identity("viva-core") and records.has_identity("sms-legacy")


# --- the core's plain-HTTP probe client, and the SMS client it stands in for (step P2 Client) ------------------------

_PROBE_CLIENTS = {
    "core": lambda base: bc.ProbeClient(base),
    "sms": lambda base: _sms().SmsApiClient.for_("probe", base),
}
#: The probe clients to compare: the SMS one needs the plugin (docs/backend-adapters.md step P3, K2a).
_WHICH = [pytest.param(w, marks=pytest.mark.plugin) if w == "sms" else w for w in sorted(_PROBE_CLIENTS)]
_OPENAPI = (b'{"info": {"version": "0.7"}, "paths": {"/simulation/run": {"post": {"operationId": "run-simulation"}},'
            b' "/x": {"get": {"operationId": "list-environments"}, "parameters": []}}}')


@pytest.fixture
def _fresh_links():
    with bc._links_lock:
        saved = dict(bc._links)
        bc._links.clear()
    yield
    with bc._links_lock:
        bc._links.clear()
        bc._links.update(saved)


_UNREACHABLE = {
    "core": "(backend unreachable — is the endpoint up?)",
    "sms": "(sms-api unreachable — is the tunnel up?)",
}


def _ask(which: str, base: str):
    return _PROBE_CLIENTS[which](base).openapi_operations()


@pytest.mark.parametrize("which", _WHICH)
def test_probe_client_reads_the_compose_operations(monkeypatch, _fresh_links, which):
    seen = _serve(monkeypatch, lambda req: _Resp(_OPENAPI))
    assert _ask(which, "http://p2-probe:1/") == ("0.7", frozenset({"run-simulation", "list-environments"}))
    [(url, headers, timeout)] = seen
    assert url == "http://p2-probe:1/openapi.json"
    assert headers == {"Accept": "application/json", "X-auth-request-email": "tester@github"}
    assert timeout == 3.0
    assert bc.link("http://p2-probe:1").state == bc.STATE_UP


@pytest.mark.parametrize("which", _WHICH)
@pytest.mark.parametrize("code", [404, 503])
def test_probe_client_http_error_has_its_status_and_no_retry(monkeypatch, _fresh_links, which, code):
    def boom(req):
        raise HTTPError(req.full_url, code, "nope", {}, io.BytesIO(b'{"detail": "no"}'))

    seen = _serve(monkeypatch, boom)
    with pytest.raises(bc.BackendError) as ei:
        _ask(which, "http://p2-probe:2")
    assert ei.value.status == code
    assert str(ei.value) == f"GET http://p2-probe:2/openapi.json -> {code}: no"
    assert len(seen) == 1
    assert bc.link("http://p2-probe:2").state == bc.STATE_UNKNOWN  # an HTTP status is not a link failure


@pytest.mark.parametrize("which", _WHICH)
def test_probe_client_unreachable_opens_the_breaker(monkeypatch, _fresh_links, which):
    def boom(req):
        raise URLError("connection refused")

    seen = _serve(monkeypatch, boom)
    with pytest.raises(bc.BackendError) as ei:
        _ask(which, "http://p2-probe:3")
    assert ei.value.status is None
    # The type and status agree; the text is each client's own (step P3 C2: the core's is neutral). Both keep the
    # "unreachable" substring that run_runner._remote_failure_reason classifies on.
    assert str(ei.value) == ("GET http://p2-probe:3/openapi.json failed " + _UNREACHABLE[which]
                             + ": <urlopen error connection refused>")
    assert "unreachable" in str(ei.value)
    assert bc.link("http://p2-probe:3").state == bc.STATE_DOWN
    with pytest.raises(bc.CircuitOpen):
        _ask(which, "http://p2-probe:3")
    assert len(seen) == 1  # the second call never touched the socket


@pytest.mark.parametrize("which", _WHICH)
def test_probe_client_not_json_is_a_value_error(monkeypatch, _fresh_links, which):
    _serve(monkeypatch, lambda req: _Resp(b"<html>"))
    with pytest.raises(ValueError):
        _ask(which, "http://p2-probe:4")
    assert bc.link("http://p2-probe:4").state == bc.STATE_UNKNOWN
