"""The core's generic backend plumbing (docs/backend-adapters.md step P1a, ``lib/backend_config.py``).

P1a moved these out of the SMS modules with no behaviour change. These tests pin that the SMS names still mean the
same thing (the wrappers and re-exports), that the moved functions behave as before, and that the ping the SMS client
keeps (until step P1b) agrees with the core's.
"""

from __future__ import annotations

import io
from urllib.error import HTTPError, URLError

import pytest

from vivarium_workbench.lib import backend_config as bc
from vivarium_workbench_sms import remote_link, remote_pinned
from vivarium_workbench_sms import sms_api_client as sac


# --- the SMS names are the core's ----------------------------------------------------------------------------------

def test_sms_api_error_is_the_core_error():
    assert sac.SmsApiError is bc.BackendError
    assert issubclass(remote_link.CircuitOpen, sac.SmsApiError)
    assert remote_link.CircuitOpen is bc.CircuitOpen
    assert issubclass(sac.BranchHeadUnresolved, bc.BackendError)


def test_one_breaker_registry():
    assert remote_link._links is bc._links
    assert remote_link._links_lock is bc._links_lock
    assert remote_link.RemoteLink is bc.RemoteLink
    lk = remote_link.link("http://p1a-registry:1")
    assert bc.link("http://p1a-registry:1") is lk


def test_remote_link_defaults_to_the_sms_endpoint(monkeypatch):
    monkeypatch.setenv("VIVA_API_BASE", "http://p1a-default:2")
    assert remote_link.link().base_url == "http://p1a-default:2"
    with pytest.raises(TypeError):
        bc.link()  # type: ignore[call-arg]  -- the core requires a URL; the SMS default is remote_link's


@pytest.mark.parametrize("name", [
    "BACKEND_BASE_ENV_VARS", "DEFAULT_BACKEND_BASE", "BackendConfigError", "backend_configured",
    "check_single_backend", "explicit_backend_base_url", "normalize_backend_base_url", "IDENTITY_HEADER",
    "caller_identity", "_http_error_detail",
])
def test_reexports_are_the_core_objects(name):
    assert getattr(sac, name) is getattr(bc, name)


# --- the moved functions behave as before --------------------------------------------------------------------------

@pytest.mark.parametrize("env, expected", [
    ({}, "http://localhost:8080"),
    ({"SMS_API_BASE": "http://legacy:1"}, "http://legacy:1"),
    ({"VIVA_API_BASE": "http://viva:1", "SMS_API_BASE": "http://legacy:1"}, "http://viva:1"),
    ({"VIVARIUM_WORKBENCH_BACKEND_BASE_URL": "http://named:1", "VIVA_API_BASE": "http://viva:1"}, "http://named:1"),
])
def test_configured_base_url_precedence(monkeypatch, env, expected):
    for var in bc.BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert bc.configured_base_url() == expected
    assert sac.sms_api_base() == expected


def test_sms_api_base_reads_the_core_at_call_time(monkeypatch):
    monkeypatch.setattr(bc, "configured_base_url", lambda: "http://patched:1")
    assert sac.sms_api_base() == "http://patched:1"


@pytest.mark.parametrize("env, named, expected", [
    ({}, None, "smsvpctest"),
    ({"VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT": "smscdk"}, None, "smscdk"),
    ({}, "https://sms.cam.uchc.edu", "sms.cam.uchc.edu"),
    ({"VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT": "smscdk"}, "https://sms.cam.uchc.edu", "smscdk"),
])
def test_deployment_name(monkeypatch, env, named, expected):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", raising=False)
    monkeypatch.delenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if named:
        monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", named)
    assert bc.deployment_name() == expected
    assert remote_pinned.remote_deployment_name() == expected
    assert remote_pinned._DEFAULT_REMOTE_DEPLOYMENT == bc._DEFAULT_DEPLOYMENT == "smsvpctest"


def test_remote_deployment_name_reads_the_core_at_call_time(monkeypatch):
    monkeypatch.setattr(bc, "deployment_name", lambda: "patched")
    assert remote_pinned.remote_deployment_name() == "patched"


def test_check_single_backend(monkeypatch):
    for var in bc.BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "http://Named:1/")
    monkeypatch.setenv("SMS_API_BASE", "http://named:1")
    bc.check_single_backend()  # same URL after normalising
    monkeypatch.setenv("SMS_API_BASE", "http://other:1")
    with pytest.raises(bc.BackendConfigError):
        bc.check_single_backend()


def test_canonical_url():
    assert bc.canonical_url("HTTP://Host:8080/x/") == "http://host:8080/x"


# --- the core ping, and the SMS client's copy of it (until P1b) ----------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _serve(monkeypatch, behaviour):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, dict(req.header_items()), timeout))
        return behaviour(req)

    monkeypatch.setattr(bc, "urlopen", fake_urlopen)
    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    # A fixed caller (not the machine's real GitHub sign-in): both pings must send the same identity header.
    monkeypatch.setattr(bc, "caller_identity", lambda: "tester@github")
    monkeypatch.setattr(sac, "caller_identity", lambda: "tester@github")
    return seen


@pytest.mark.parametrize("body, version", [(b'"0.9.27"', "0.9.27"), (b'{"version": "1.2.3"}', "1.2.3"),
                                           (b"plain", "plain")])
def test_ping_agrees_with_the_sms_client_on_success(monkeypatch, body, version):
    seen = _serve(monkeypatch, lambda req: _Resp(body))
    assert bc.ping("http://x:1") == version
    assert sac.SmsApiClient("http://x:1").ping() == version
    (core_url, core_headers, core_timeout), (sms_url, sms_headers, sms_timeout) = seen
    assert core_url == sms_url == "http://x:1/version"
    assert core_headers == sms_headers
    assert core_headers.get("X-auth-request-email") == "tester@github"
    assert core_timeout == sms_timeout == 5.0


def test_ping_agrees_with_the_sms_client_on_an_http_error(monkeypatch):
    def boom(req):
        raise HTTPError(req.full_url, 501, "nope", {}, io.BytesIO(b'{"detail": "not here"}'))

    _serve(monkeypatch, boom)
    with pytest.raises(bc.BackendError) as core:
        bc.ping("http://x:1")
    with pytest.raises(sac.SmsApiError) as sms:
        sac.SmsApiClient("http://x:1").ping()
    assert core.value.status == sms.value.status == 501
    assert str(core.value) == str(sms.value)


def test_ping_agrees_with_the_sms_client_when_unreachable(monkeypatch):
    def boom(req):
        raise URLError("connection refused")

    _serve(monkeypatch, boom)
    with pytest.raises(bc.BackendError) as core:
        bc.ping("http://x:1")
    with pytest.raises(sac.SmsApiError) as sms:
        sac.SmsApiClient("http://x:1").ping()
    assert core.value.status is None and sms.value.status is None
    assert str(core.value) == str(sms.value)


def test_the_breaker_probe_pings_through_the_core(monkeypatch):
    _serve(monkeypatch, lambda req: _Resp(b'"1"'))
    monkeypatch.setattr(sac, "urlopen", lambda *a, **k: pytest.fail("the probe must use the core ping"))
    lk = bc.RemoteLink("http://p1a-probe:1")
    assert lk.probe() is True
    assert lk.state == bc.STATE_UP


# --- server identity is the backend's (Backend.identity) -----------------------------------------------------------

def test_compose_api_has_no_identity_and_is_never_asked(monkeypatch):
    from vivarium_workbench.lib import backends
    from vivarium_workbench.lib.backends import records

    assert backends.COMPOSE.identity is None
    assert records.has_identity("compose-api") is False
    assert records.has_identity("viva-core") and records.has_identity("sms-legacy")
    assert records.has_identity("no-such-kind") is False
    monkeypatch.setattr(records, "fetch_server_id", lambda url: pytest.fail("compose-api has no identity to ask"))
    rec = records.remote_record("compose-api", "compose-1", "http://compose:1")
    assert rec["server_id"] is None
