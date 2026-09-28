"""W1 (vivarium-workbench#1150): the client moves its compose and env-worker
operations onto viva-api's ``/viva/v1`` spellings by CAPABILITY MEMBERSHIP, and
keeps the legacy spellings everywhere else.

* ``viva-v1-surface`` -> the 4 compose ops at ``/viva/v1/compose/...``
* ``viva-v1-workers`` -> the 8 env-worker ops at ``/viva/v1/workers/...`` (the
  final spelling, mirroring viva-api's ``viva_core/api/routers/workers.py::PATHS``)
* absent capability / old server (404) / unreachable probe -> legacy paths
* the advertisement is fetched once per client, and only for a path that can move
"""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest

from vivarium_workbench.lib import sms_api_client as mod
from vivarium_workbench.lib.sms_api_client import (
    CAPABILITY_VIVA_V1_SURFACE,
    CAPABILITY_VIVA_V1_WORKERS,
    SmsApiClient,
    resolve_path,
)

BOTH = frozenset({CAPABILITY_VIVA_V1_SURFACE, CAPABILITY_VIVA_V1_WORKERS})
#: Both capability routes: /viva/v1 first, /core/v1 when that 404s (W2).
CAPS_PATHS = ("/viva/v1/capabilities", "/core/v1/capabilities")


class _Resp(io.BytesIO):
    def __init__(self, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        super().__init__(body)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeServer:
    """Routes by path: the capability endpoint answers ``caps`` (a list), 404
    (``caps=404``) or a connection error (``caps="down"``); every other request
    is recorded and answered with a generic payload."""

    def __init__(self, monkeypatch, caps):
        self.caps = caps
        self.requests: list[tuple[str, str]] = []  # (method, path) excluding the probe
        self.probes = 0
        monkeypatch.setattr(mod, "urlopen", self)

    def __call__(self, req, timeout=None):
        path = urlsplit(req.full_url).path
        if path in CAPS_PATHS:
            self.probes += 1
            if self.caps == 404 or (self.caps == "core-only" and path == CAPS_PATHS[0]):
                raise HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"detail":"Not Found"}'))
            if self.caps == "down":
                raise URLError("connection refused")
            names = [CAPABILITY_VIVA_V1_SURFACE] if self.caps == "core-only" else list(self.caps)
            return _Resp({"version": "test", "capabilities": names})
        self.requests.append((req.get_method(), path))
        if path.endswith("/results"):
            return _Resp(b"\x1f\x8bfake")
        if path.endswith("/simulation/run"):
            return _Resp({"simulation_database_id": 1})
        if path.endswith("/status/batch"):
            return _Resp([])
        return _Resp({"ok": True})


# Every op W1 moves: (name, call, method, legacy path, /viva/v1 path, capability).
OPS = [
    ("compose_submit", lambda c, t: c.compose_submit(b"pbg"), "POST",
     "/compose/v1/simulation/run", "/viva/v1/compose/simulation/run", CAPABILITY_VIVA_V1_SURFACE),
    ("compose_status", lambda c, t: c.compose_status(42), "GET",
     "/compose/v1/simulation/42/status", "/viva/v1/compose/simulation/42/status", CAPABILITY_VIVA_V1_SURFACE),
    ("compose_status_batch", lambda c, t: c.compose_status_batch([1, 2]), "GET",
     "/compose/v1/simulations/status/batch", "/viva/v1/compose/simulations/status/batch",
     CAPABILITY_VIVA_V1_SURFACE),
    ("download_compose_results", lambda c, t: c.download_compose_results(42, t), "GET",
     "/compose/v1/simulation/42/results", "/viva/v1/compose/simulation/42/results", CAPABILITY_VIVA_V1_SURFACE),
    ("start_env_worker", lambda c, t: c.start_env_worker(commit="abc", callback_host="h", callback_port=1,
                                                         token="t"), "POST",
     "/env-worker/v1/workers", "/viva/v1/workers", CAPABILITY_VIVA_V1_WORKERS),
    ("env_worker_status", lambda c, t: c.env_worker_status("job-1"), "GET",
     "/env-worker/v1/workers/job-1", "/viva/v1/workers/job-1", CAPABILITY_VIVA_V1_WORKERS),
    ("stop_env_worker", lambda c, t: c.stop_env_worker("job-1"), "DELETE",
     "/env-worker/v1/workers/job-1", "/viva/v1/workers/job-1", CAPABILITY_VIVA_V1_WORKERS),
    ("start_relayed_env_worker", lambda c, t: c.start_relayed_env_worker(commit="abc"), "POST",
     "/env-worker/v1/relay/workers", "/viva/v1/workers/relay", CAPABILITY_VIVA_V1_WORKERS),
    ("call_relayed_env_worker", lambda c, t: c.call_relayed_env_worker("job-1", method="m"), "POST",
     "/env-worker/v1/relay/workers/job-1/call", "/viva/v1/workers/job-1/call", CAPABILITY_VIVA_V1_WORKERS),
    ("stop_relayed_env_worker", lambda c, t: c.stop_relayed_env_worker("job-1"), "DELETE",
     "/env-worker/v1/relay/workers/job-1", "/viva/v1/workers/relay/job-1", CAPABILITY_VIVA_V1_WORKERS),
    ("submit_env_worker_task", lambda c, t: c.submit_env_worker_task("job-1", method="m"), "POST",
     "/env-worker/v1/tasks", "/viva/v1/workers/tasks", CAPABILITY_VIVA_V1_WORKERS),
    ("get_env_worker_task", lambda c, t: c.get_env_worker_task(7), "GET",
     "/env-worker/v1/tasks/7", "/viva/v1/workers/tasks/7", CAPABILITY_VIVA_V1_WORKERS),
]
IDS = [o[0] for o in OPS]


@pytest.mark.parametrize("name,call,method,legacy,new,cap", OPS, ids=IDS)
def test_capability_present_moves_the_op(monkeypatch, tmp_path, name, call, method, legacy, new, cap):
    server = FakeServer(monkeypatch, caps=[cap])
    call(SmsApiClient("http://h:8080"), tmp_path)
    assert server.requests == [(method, new)]
    assert resolve_path(legacy, {cap}) == new


@pytest.mark.parametrize("name,call,method,legacy,new,cap", OPS, ids=IDS)
def test_capability_absent_keeps_the_legacy_path(monkeypatch, tmp_path, name, call, method, legacy, new, cap):
    # Advertises the OTHER switch (and an unknown name) -- only membership of
    # this op's own capability may move it.
    other = BOTH - {cap}
    server = FakeServer(monkeypatch, caps=[*other, "some-future-thing"])
    call(SmsApiClient("http://h:8080"), tmp_path)
    assert server.requests == [(method, legacy)]


@pytest.mark.parametrize("name,call,method,legacy,new,cap", OPS, ids=IDS)
def test_endpoint_missing_404_keeps_the_legacy_path(monkeypatch, tmp_path, name, call, method, legacy, new, cap):
    """A deployment that predates both capability routes advertises nothing."""
    server = FakeServer(monkeypatch, caps=404)
    call(SmsApiClient("http://h:8080"), tmp_path)
    assert server.requests == [(method, legacy)]


def test_probe_unreachable_falls_back_and_is_not_cached(monkeypatch):
    """A failed probe must not fail the call (the real request surfaces the real
    error) and must not be cached as "nothing advertised"."""
    marked = []
    monkeypatch.setattr(mod.SmsApiClient, "_link", lambda self: _NoBreaker(marked))
    server = FakeServer(monkeypatch, caps="down")
    c = SmsApiClient("http://h:8080")
    c.compose_status(1)
    assert server.requests == [("GET", "/compose/v1/simulation/1/status")]
    server.caps = [CAPABILITY_VIVA_V1_SURFACE]
    c.compose_status(1)
    assert server.requests[-1] == ("GET", "/viva/v1/compose/simulation/1/status")
    assert server.probes == 2
    # the probe's failure never tripped the RemoteLink breaker
    assert "down" not in marked


class _NoBreaker:
    def __init__(self, marked):
        self.marked = marked

    def check(self, force=False):
        return None

    def mark_up(self):
        self.marked.append("up")

    def mark_down(self, error):
        self.marked.append("down")


def test_advertisement_fetched_once_per_client(monkeypatch):
    server = FakeServer(monkeypatch, caps=list(BOTH))
    c = SmsApiClient("http://h:8080")
    c.compose_status(1)
    c.env_worker_status("j")
    c.get_env_worker_task(3)
    assert server.probes == 1
    SmsApiClient("http://h:8080").compose_status(1)  # a new client asks again
    assert server.probes == 2


def test_advertisement_refreshed_after_ttl(monkeypatch):
    server = FakeServer(monkeypatch, caps=[])
    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    c = SmsApiClient("http://h:8080")
    c.compose_status(1)
    server.caps = [CAPABILITY_VIVA_V1_SURFACE]  # viva-api redeployed under a long-lived client
    c.compose_status(1)
    assert server.requests[-1][1] == "/compose/v1/simulation/1/status"
    clock["t"] += mod._CAPABILITY_TTL + 1
    c.compose_status(1)
    assert server.requests[-1][1] == "/viva/v1/compose/simulation/1/status"
    assert server.probes == 2


@pytest.mark.parametrize("call,path", [
    (lambda c: c.simulation_status(5), "/api/v1/simulations/5/status"),
    (lambda c: c.list_build_simulations(3), "/api/v1/simulations"),
    (lambda c: c.analysis_status(7), "/api/v1/analyses/7/status"),
    (lambda c: c._get("/api/v1/simulations/discovery"), "/api/v1/simulations/discovery"),
])
def test_paths_with_no_successor_never_probe(monkeypatch, call, path):
    server = FakeServer(monkeypatch, caps=list(BOTH))
    call(SmsApiClient("http://h:8080"))
    assert server.requests == [("GET", path)]
    assert server.probes == 0


def test_ping_keeps_version(monkeypatch):
    server = FakeServer(monkeypatch, caps=list(BOTH))
    SmsApiClient("http://h:8080").ping()
    assert server.requests == [("GET", "/version")]
    assert server.probes == 0


@pytest.mark.parametrize("legacy,new", [
    # the named reads of a held worker nest under the worker (workers.py PATHS)
    ("/env-worker/v1/relay/workers/j/generators", "/viva/v1/workers/j/generators"),
    ("/env-worker/v1/relay/workers/j/composite-state/inner", "/viva/v1/workers/j/composite-state/inner"),
    ("/env-worker/v1/tasks/status/batch", "/viva/v1/workers/tasks/status/batch"),
    ("/env-worker/v1/workers", "/viva/v1/workers"),
])
def test_resolver_mirrors_viva_api_workers_paths(legacy, new):
    assert resolve_path(legacy, BOTH) == new


def test_surface_alone_does_not_move_env_worker_ops():
    """W1 moves the env-worker ops ONCE, to the final spelling -- never to the
    interim /viva/v1/env-worker (dated M5)."""
    assert resolve_path("/env-worker/v1/workers/j", {CAPABILITY_VIVA_V1_SURFACE}) == "/env-worker/v1/workers/j"


def test_removed_dead_ops_are_gone():
    for name in ("observables", "compose_check", "cancel_env_worker_task"):
        assert not hasattr(SmsApiClient, name), name


def test_capabilities_read_from_core_v1_when_viva_v1_route_is_missing(monkeypatch):
    """A server with /core/v1/capabilities but no /viva/v1/capabilities (before
    viva-api 0.9.157) is still read -- by the second route."""
    server = FakeServer(monkeypatch, caps="core-only")
    SmsApiClient("http://h:8080").compose_status(1)
    assert server.requests == [("GET", "/viva/v1/compose/simulation/1/status")]
    assert server.probes == 2  # /viva/v1 (404), then /core/v1
