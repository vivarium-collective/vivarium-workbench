"""Tests for the remote sms-api health indicator (#2/#3 hardening).

`workspace_deps_views.remote_health()` powers the Source panel's 🟢/🔴 dot and the
startup log; `SmsApiClient.ping()` is its lightweight reachability probe. Both must
degrade cleanly (never raise) so a fresh operator/Chris always gets a clear signal.
"""
import pytest

from vivarium_workbench.lib import backend_config
from vivarium_workbench.lib import sms_api_client as sac
from vivarium_workbench.lib import workspace_deps_views as wdv
from vivarium_workbench.lib.sms_api_client import SmsApiError


def _ping_with(monkeypatch, client_cls):
    """step P1a (patch target moved): remote_health pings through backend_config.ping, not SmsApiClient.ping."""
    monkeypatch.setattr(backend_config, "ping", lambda base, timeout=None: client_cls(base).ping(timeout))


class _OkClient:
    def __init__(self, base, **kw):
        self.base_url = base

    def ping(self, timeout=None):
        return "0.9.27"


class _DownClient:
    def __init__(self, base, **kw):
        self.base_url = base

    def ping(self, timeout=None):
        raise SmsApiError("GET .../version failed (sms-api unreachable — is the tunnel up?)")


def test_remote_health_reachable(monkeypatch):
    # Clear the canonical name: _sms_api_base reads VIVA_API_BASE first, and
    # conftest's _isolate_viva_api_base sets both. This test exercises the
    # legacy alias specifically (cf. test_env_worker_launcher, same pattern).
    monkeypatch.delenv("VIVA_API_BASE", raising=False)
    monkeypatch.setenv("SMS_API_BASE", "http://sms-api.example:8080")
    _ping_with(monkeypatch, _OkClient)
    assert wdv.remote_health() == {
        "configured": True,
        "base_url": "http://sms-api.example:8080",
        "reachable": True,
        "version": "0.9.27",
        "error": None,
    }


def test_remote_health_unreachable_does_not_raise(monkeypatch):
    # Clear the canonical name: _sms_api_base reads VIVA_API_BASE first, and
    # conftest's _isolate_viva_api_base sets both. This test exercises the
    # legacy alias specifically (cf. test_env_worker_launcher, same pattern).
    monkeypatch.delenv("VIVA_API_BASE", raising=False)
    monkeypatch.setenv("SMS_API_BASE", "http://sms-api.example:8080")
    _ping_with(monkeypatch, _DownClient)
    h = wdv.remote_health()
    assert h["configured"] is True
    assert h["reachable"] is False
    assert h["version"] is None
    assert "unreachable" in h["error"]


def test_remote_health_unconfigured_uses_default_and_flags_it(monkeypatch):
    # Both names, not just the legacy alias — see the note in
    # test_remote_run_endpoints.test_sms_api_base_default_and_override.
    monkeypatch.delenv("VIVA_API_BASE", raising=False)
    monkeypatch.delenv("SMS_API_BASE", raising=False)
    _ping_with(monkeypatch, _DownClient)
    h = wdv.remote_health()
    assert h["configured"] is False
    assert h["base_url"] == "http://localhost:8080"
    assert h["reachable"] is False


# --- SmsApiClient.ping() ----------------------------------------------------

class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ping_parses_json_string_version(monkeypatch):
    monkeypatch.setattr(sac, "urlopen", lambda req, timeout=None: _Resp(b'"0.9.27"'))
    assert sac.SmsApiClient("http://x").ping() == "0.9.27"


def test_ping_parses_dict_version(monkeypatch):
    monkeypatch.setattr(sac, "urlopen", lambda req, timeout=None: _Resp(b'{"version": "1.2.3"}'))
    assert sac.SmsApiClient("http://x").ping() == "1.2.3"


def test_ping_raises_smsapierror_when_unreachable(monkeypatch):
    from urllib.error import URLError

    def _boom(req, timeout=None):
        raise URLError("connection refused")

    monkeypatch.setattr(sac, "urlopen", _boom)
    with pytest.raises(SmsApiError, match="unreachable"):
        sac.SmsApiClient("http://x").ping()
