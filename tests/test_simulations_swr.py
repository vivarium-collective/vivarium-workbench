"""SWR / non-blocking remote-fetch behaviour on the Simulations index.

Covers issue #1106 (b1): ``/api/simulations`` must never block the request
thread on sms-api, must report remote-source provenance (``remote.state``),
and must negative-cache a failed fetch so a down tunnel isn't re-probed on
every 15s auto-refresh.

sms-api is stubbed throughout — no network. The slow-client tests assert
promptness against a client that sleeps, so a regression that reintroduces the
blocking fetch fails loudly (the request would take as long as the fetch).
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

import vivarium_workbench.lib.remote_simulations as rs
from vivarium_workbench.api.app import create_app, get_workspace
from vivarium_workbench.lib import simulations_index as si


@pytest.fixture(autouse=True)
def _clear_caches():
    """Isolate the module-level SWR + build caches between tests."""
    rs._REMOTE_CACHE.clear()
    rs._REMOTE_META.clear()
    with rs._REMOTE_REFRESH_LOCK:
        rs._REMOTE_REFRESH_INFLIGHT.clear()
    si.clear_build_cache()
    yield
    rs._REMOTE_CACHE.clear()
    rs._REMOTE_META.clear()
    with rs._REMOTE_REFRESH_LOCK:
        rs._REMOTE_REFRESH_INFLIGHT.clear()
    si.clear_build_cache()


def _drain_refresh(timeout: float = 5.0) -> None:
    """Wait for any in-flight background refresh thread to finish."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with rs._REMOTE_REFRESH_LOCK:
            if not rs._REMOTE_REFRESH_INFLIGHT:
                return
        time.sleep(0.02)


# ---- fake clients ---------------------------------------------------------

class _SlowClient:
    """A wedged tunnel: list_simulators sleeps a long time before answering."""
    calls = 0
    release = threading.Event()

    def __init__(self, *a, **k):
        pass

    def list_simulators(self):
        type(self).calls += 1
        # Block until released (or a generous cap) so the test can observe the
        # request returning WITHOUT waiting on this. The cap is far larger than
        # any legitimate request-handling cost, so a regression that reintroduces
        # a blocking fetch is unmistakable in the elapsed time.
        self.release.wait(timeout=30.0)
        return {"versions": []}

    def list_build_simulations(self, simulator_id):
        return []


class _DownClient:
    """A dead tunnel: every call raises immediately."""
    calls = 0

    def __init__(self, *a, **k):
        pass

    def list_simulators(self):
        type(self).calls += 1
        raise RuntimeError("tunnel down")


# ---- module-level SWR behaviour -------------------------------------------

def test_swr_never_blocks_on_a_slow_fetch(monkeypatch, tmp_path):
    _SlowClient.calls = 0
    _SlowClient.release.clear()
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _SlowClient)

    t0 = time.time()
    rows = rs.list_remote_simulations_swr(tmp_path)
    elapsed = time.time() - t0

    # Returned immediately (serves the empty cache) while the fetch runs in a
    # background thread — nowhere near the 10s the slow client would take.
    assert elapsed < 1.0
    assert rows == []
    # And it reports the source as refreshing, not a false "fresh".
    assert rs.remote_state(tmp_path)["state"] == "refreshing"

    _SlowClient.release.set()
    _drain_refresh()
    # Exactly one probe was kicked, not one per call.
    assert _SlowClient.calls == 1


def test_fresh_path_reports_unavailable_on_a_down_tunnel(monkeypatch, tmp_path):
    _DownClient.calls = 0
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _DownClient)

    rows = rs.list_remote_simulations(tmp_path, use_cache=False)
    assert rows == []
    st = rs.remote_state(tmp_path)
    assert st["state"] == "unavailable"
    assert st["as_of"] is None
    assert "tunnel down" in (st["error"] or "")


def test_negative_cache_prevents_repeated_probes(monkeypatch, tmp_path):
    _DownClient.calls = 0
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _DownClient)

    # One failing fetch negative-caches the outcome…
    rs.list_remote_simulations(tmp_path, use_cache=False)
    assert _DownClient.calls == 1

    # …so the next SWR reads over the 15s auto-refresh window serve the cached
    # negative result WITHOUT re-probing the dead tunnel.
    for _ in range(5):
        rs.list_remote_simulations_swr(tmp_path)
    _drain_refresh()
    assert _DownClient.calls == 1


def test_successful_fetch_is_fresh_with_as_of(monkeypatch, tmp_path):
    class _Ok:
        def __init__(self, *a, **k):
            pass

        def list_simulators(self):
            return {"versions": []}

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _Ok)
    monkeypatch.setattr("vivarium_workbench.lib.git_status.remote_repo_url",
                        lambda ws: None)  # local checkout, no matching builds

    rs.list_remote_simulations(tmp_path, use_cache=False)
    st = rs.remote_state(tmp_path)
    # Zero remote rows is a SUCCESS, not a failure.
    assert st["state"] == "fresh"
    assert st["error"] is None
    assert isinstance(st["as_of"], float)


def test_bounded_fresh_client_uses_short_timeout_and_one_retry(monkeypatch, tmp_path):
    captured = {}

    class _Recorder:
        def __init__(self, base_url="", timeout=30.0, max_retries=3):
            captured["timeout"] = timeout
            captured["max_retries"] = max_retries

        def list_simulators(self):
            return {"versions": []}

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _Recorder)
    # The fresh path (?refresh=true) must bound the client so it can't hang.
    si._append_remote_simulations([], tmp_path, fresh=True)
    assert captured["timeout"] == rs._FRESH_TIMEOUT
    assert captured["max_retries"] == rs._FRESH_MAX_RETRIES


# ---- /api/simulations route -----------------------------------------------

@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_path
    return TestClient(app)


def test_api_simulations_returns_promptly_when_remote_is_slow(client, monkeypatch, tmp_path):
    _SlowClient.calls = 0
    _SlowClient.release.clear()
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _SlowClient)

    t0 = time.time()
    r = client.get("/api/simulations")
    elapsed = time.time() - t0

    assert r.status_code == 200
    body = r.json()
    assert "remote" in body and body["remote"] is not None
    # The fetch is still in flight — the request did NOT wait for it (proof the
    # blocking fetch was not reintroduced); and the elapsed time is nowhere near
    # the 30s the wedged tunnel would cost if it had blocked.
    assert body["remote"]["state"] == "refreshing"
    assert elapsed < 10.0

    _SlowClient.release.set()
    _drain_refresh()


def test_api_simulations_refresh_forces_bounded_fresh_path(client, monkeypatch, tmp_path):
    _DownClient.calls = 0
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.SmsApiClient", _DownClient)

    r = client.get("/api/simulations?refresh=true")
    assert r.status_code == 200
    body = r.json()
    # ?refresh=true takes the blocking-but-bounded fresh path, so the failure is
    # known synchronously and surfaced (not deferred behind a background probe).
    assert body["remote"]["state"] == "unavailable"
    assert "tunnel down" in (body["remote"]["error"] or "")
