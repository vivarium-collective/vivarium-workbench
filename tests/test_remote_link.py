"""Tests for the RemoteLink circuit breaker (b2) and call-class timeouts (e3)."""

import io
import json
import time
from urllib.error import URLError

import pytest

from vivarium_workbench.lib import remote_link
from vivarium_workbench.lib.remote_link import (
    CircuitOpen,
    RemoteLink,
    STATE_DOWN,
    STATE_UP,
    link,
)
from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError


class _Resp(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _patch_ok(monkeypatch, payload):
    def fake_urlopen(req, timeout=None):
        return _Resp(payload)

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)


def _patch_unreachable(monkeypatch, sentinel=None):
    def fake_urlopen(req, timeout=None):
        if sentinel is not None:
            sentinel.append(1)
        raise URLError("connection refused")

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)


def _fresh(name):
    """A RemoteLink on a base_url unique to a test, to avoid singleton bleed."""
    return RemoteLink(f"http://test-{name}:8080")


# -- CircuitOpen is an SmsApiError -----------------------------------------

def test_circuit_open_is_sms_api_error():
    assert issubclass(CircuitOpen, SmsApiError)
    e = CircuitOpen("nope")
    assert isinstance(e, SmsApiError)
    assert e.status is None  # no HTTP response ever happened


# -- a known-down link raises fast without a real call ----------------------

def test_check_down_raises_without_network():
    lk = _fresh("down")
    lk.mark_down("connection refused")
    assert lk.state == STATE_DOWN
    with pytest.raises(CircuitOpen):
        lk.check()


def test_get_on_down_link_is_fast_and_no_urlopen(monkeypatch):
    base = "http://test-fastfail:8080"
    link(base).mark_down("wedged")
    called = []
    _patch_unreachable(monkeypatch, sentinel=called)  # would append if reached
    client = SmsApiClient(base)
    t0 = time.monotonic()
    with pytest.raises(CircuitOpen):
        client.list_simulators()
    assert time.monotonic() - t0 < 0.5  # microseconds, not the timeout budget
    assert called == []  # urlopen never reached


def test_force_bypasses_open_breaker(monkeypatch):
    base = "http://test-force:8080"
    link(base).mark_down("wedged")
    _patch_ok(monkeypatch, {"versions": []})
    client = SmsApiClient(base, force_link=True)
    assert client.list_simulators() == {"versions": []}  # not blocked
    assert link(base).state == STATE_UP  # and the success marked it back up


# -- half-open retry after OPEN_FOR -----------------------------------------

def test_half_open_probe_success_reopens(monkeypatch):
    lk = _fresh("halfopen-ok")
    lk.mark_down("was down")
    lk._retry_at = time.monotonic() - 1  # window elapsed
    monkeypatch.setattr(lk, "probe", lambda: (lk.mark_up() or True))
    lk.check()  # half-open probe succeeds -> no raise


def test_half_open_probe_failure_still_open(monkeypatch):
    lk = _fresh("halfopen-fail")
    lk.mark_down("was down")
    lk._retry_at = time.monotonic() - 1  # window elapsed
    monkeypatch.setattr(lk, "probe", lambda: False)
    with pytest.raises(CircuitOpen):
        lk.check()


def test_not_due_does_not_probe():
    lk = _fresh("notdue")
    lk.mark_down("was down")  # retry_at set OPEN_FOR in the future
    probed = []
    lk.probe = lambda: probed.append(1) or True  # type: ignore[method-assign]
    with pytest.raises(CircuitOpen):
        lk.check()
    assert probed == []  # not yet due -> raises without probing


# -- a successful call marks the link up ------------------------------------

def test_successful_get_marks_up(monkeypatch):
    base = "http://test-markup:8080"
    _patch_ok(monkeypatch, {"versions": [1]})
    SmsApiClient(base).list_simulators()
    assert link(base).state == STATE_UP
    assert link(base).last_ok is not None


def test_connection_failure_marks_down(monkeypatch):
    base = "http://test-markdown:8080"
    _patch_unreachable(monkeypatch)
    with pytest.raises(SmsApiError):
        SmsApiClient(base, max_retries=1).list_simulators()
    assert link(base).state == STATE_DOWN


def test_http_error_does_not_trip_breaker(monkeypatch):
    base = "http://test-httperr:8080"

    def fake_urlopen(req, timeout=None):
        from urllib.error import HTTPError

        raise HTTPError(req.full_url, 404, "not found", {}, io.BytesIO(b"nope"))

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    with pytest.raises(SmsApiError):
        SmsApiClient(base, max_retries=1).list_simulators()
    # 404 means the server answered — the tunnel is alive, breaker untouched.
    assert link(base).state != STATE_DOWN


def test_probe_marks_state(monkeypatch):
    lk = _fresh("probe")
    _patch_ok(monkeypatch, {"version": "1.2.3"})
    assert lk.probe() is True
    assert lk.state == STATE_UP
    _patch_unreachable(monkeypatch)
    assert lk.probe() is False
    assert lk.state == STATE_DOWN


def test_probe_http_error_is_reachable(monkeypatch):
    # An HTTP response of ANY status (even 501/5xx) means the server answered,
    # so the tunnel is up — only a connection-level failure marks it down.
    lk = _fresh("probe-501")
    lk.mark_down("was down")

    def fake_urlopen(req, timeout=None):
        from urllib.error import HTTPError

        raise HTTPError(req.full_url, 501, "Error response", {}, io.BytesIO(b"nope"))

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    assert lk.probe() is True
    assert lk.state == STATE_UP


# -- snapshot shape ---------------------------------------------------------

def test_snapshot_shape():
    lk = _fresh("snap")
    snap = lk.snapshot()
    assert set(snap) == {"state", "last_ok", "error", "base_url", "sso_expires_at"}
    assert snap["state"] == "unknown"
    assert snap["base_url"] == lk.base_url


def test_snapshot_reflects_down():
    lk = _fresh("snap-down")
    lk.mark_down("boom")
    snap = lk.snapshot()
    assert snap["state"] == STATE_DOWN
    assert snap["error"] == "boom"


# -- singleton accessor -----------------------------------------------------

def test_link_singleton_per_base_url():
    a = link("http://test-singleton:8080")
    b = link("http://test-singleton:8080/")  # trailing slash normalised
    assert a is b
    assert link("http://other-singleton:8080") is not a


# -- e3: timeouts by call class ---------------------------------------------

def test_for_call_classes():
    assert (SmsApiClient.for_("probe").timeout, SmsApiClient.for_("probe").max_retries) == (3.0, 1)
    assert (SmsApiClient.for_("status").timeout, SmsApiClient.for_("status").max_retries) == (5.0, 2)
    assert (SmsApiClient.for_("list").timeout, SmsApiClient.for_("list").max_retries) == (15.0, 2)
    dl = SmsApiClient.for_("download")
    assert dl.max_retries == 1 and dl.timeout >= 1800.0


def test_for_unknown_kind():
    with pytest.raises(ValueError):
        SmsApiClient.for_("bogus")


def test_for_uses_default_base_and_force_flag():
    c = SmsApiClient.for_("status", force_link=True)
    assert c.base_url  # resolved from sms_api_base()
    assert c.force_link is True


# -- probe thread is opt-in -------------------------------------------------

def test_start_is_idempotent():
    lk = _fresh("startonce")
    lk.probe = lambda: True  # type: ignore[method-assign]  # keep the loop cheap
    lk.start()
    t1 = lk._thread
    lk.start()
    assert lk._thread is t1  # no second thread


def test_sso_expiry_never_raises():
    # Best-effort: returns a str or None, never raises even with no cache dir.
    val = remote_link._sso_expiry()
    assert val is None or isinstance(val, str)
