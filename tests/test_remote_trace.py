"""The "Open in Perfetto" backend: the trace proxy and its capability gate.

viva-api serves a run's Chrome Trace Event JSON at ``/api/v1/simulations/{id}/trace``
and ``/viva/v1/composites/{id}/trace`` when it advertises ``viva-v1-trace``. The
workbench proxies it (``/api/remote-run-trace``) and says whether to offer the
action at all (``/api/remote-run-trace-support``). A fake ``SmsApiClient`` stands
in for viva-api throughout -- nothing here reaches a network.
"""

from __future__ import annotations

import json

import pytest

from vivarium_workbench.lib import remote_trace
from vivarium_workbench.lib.server_capabilities import CAPABILITY_VIVA_V1_TRACE
from vivarium_workbench.lib.sms_api_client import SmsApiError

TRACE = json.dumps({"traceEvents": [
    {"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": 1, "tid": 1},
]}).encode()


class FakeClient:
    """Just the surface ``remote_trace`` touches."""

    def __init__(self, caps=(CAPABILITY_VIVA_V1_TRACE,), *, caps_error=None,
                 trace_error=None, correlation_id="compose-sim-abc"):
        self.caps = list(caps)
        self.caps_error = caps_error
        self.trace_error = trace_error
        self.correlation_id = correlation_id
        self.calls: list = []

    def capabilities(self):
        if self.caps_error is not None:
            raise self.caps_error
        return {"version": "0.9.999", "capabilities": self.caps}

    def simulation_trace(self, simulation_id):
        self.calls.append(("simulation_trace", simulation_id))
        if self.trace_error is not None:
            raise self.trace_error
        return TRACE

    def composite_run_trace(self, run_id):
        self.calls.append(("composite_run_trace", run_id))
        if self.trace_error is not None:
            raise self.trace_error
        return TRACE

    def compose_status(self, compose_id):
        self.calls.append(("compose_status", compose_id))
        return {"status": "completed", "correlation_id": self.correlation_id}


# ---------------------------------------------------------------- lib: support


def test_support_true_when_capability_advertised(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI", "https://ui.perfetto.dev")
    body, status = remote_trace.trace_support(FakeClient())
    assert status == 200
    assert body["supported"] is True and body["reason"] is None
    assert body["capability"] == "viva-v1-trace"
    assert body["viewer"] == {"mode": "external", "url": "https://ui.perfetto.dev/", "version": None}


def test_support_false_when_capability_absent():
    body, _ = remote_trace.trace_support(FakeClient(caps=("chain-progress",)))
    assert body["supported"] is False and body["reason"] == "capability-absent"


def test_support_false_on_pre_capabilities_server():
    """A deployment predating the capabilities endpoint (404) advertises nothing."""
    body, _ = remote_trace.trace_support(FakeClient(caps_error=SmsApiError("nope", status=404)))
    assert body["supported"] is False and body["reason"] == "capability-absent"


def test_support_unreachable_is_not_reported_as_unsupported():
    body, status = remote_trace.trace_support(FakeClient(caps_error=SmsApiError("tunnel down")))
    assert status == 200
    assert body["supported"] is False and body["reason"] == "unreachable"


# ---------------------------------------------------------------- lib: fetch


def test_fetch_simulation_trace_passes_bytes_through():
    client = FakeClient()
    body, status, filename = remote_trace.fetch_trace(client, simulation_id="42")
    assert status == 200 and body == TRACE
    assert filename == "simulation-42-trace.json"
    assert client.calls == [("simulation_trace", 42)]


def test_fetch_composite_run_trace():
    client = FakeClient()
    body, status, filename = remote_trace.fetch_trace(client, composite_run_id="run/../x y")
    assert status == 200 and body == TRACE
    assert client.calls == [("composite_run_trace", "run/../x y")]
    assert "/" not in filename and " " not in filename


def test_fetch_compose_id_resolves_correlation_id():
    client = FakeClient(correlation_id="compose-sim-xyz")
    body, status, _ = remote_trace.fetch_trace(client, compose_id=7)
    assert status == 200
    assert client.calls == [("compose_status", 7), ("composite_run_trace", "compose-sim-xyz")]


def test_fetch_compose_id_without_correlation_is_404():
    body, status, _ = remote_trace.fetch_trace(FakeClient(correlation_id=None), compose_id=7)
    assert status == 404 and "correlation_id" in body["error"]


@pytest.mark.parametrize("kwargs", [
    {},
    {"simulation_id": 1, "composite_run_id": "r"},
    {"simulation_id": "abc"},
    {"simulation_id": 0},
    {"compose_id": -3},
])
def test_fetch_rejects_bad_or_ambiguous_ids(kwargs):
    client = FakeClient()
    body, status, _ = remote_trace.fetch_trace(client, **kwargs)
    assert status == 400 and "error" in body
    assert client.calls == []


def test_fetch_capability_absent_is_409_and_never_calls_trace():
    client = FakeClient(caps=())
    body, status, _ = remote_trace.fetch_trace(client, simulation_id=1)
    assert status == 409
    assert body["missing"] == ["viva-v1-trace"]
    assert client.calls == []


def test_fetch_unreachable_is_502():
    body, status, _ = remote_trace.fetch_trace(FakeClient(caps_error=SmsApiError("down")), simulation_id=1)
    assert status == 502


@pytest.mark.parametrize("upstream,expected", [(404, 404), (409, 409), (500, 502), (None, 502)])
def test_fetch_upstream_errors(upstream, expected):
    client = FakeClient(trace_error=SmsApiError("x", status=upstream))
    body, status, _ = remote_trace.fetch_trace(client, simulation_id=3)
    assert status == expected and "error" in body


# ---------------------------------------------------------------- routes


@pytest.fixture
def rc(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from vivarium_workbench.api.app import create_app, get_workspace

    state = {"client": FakeClient()}
    monkeypatch.setattr(remote_trace, "make_client", lambda: state["client"])
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI", "off")
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_path
    c = TestClient(app)
    c.state = state  # type: ignore[attr-defined]
    return c


def test_route_support(rc):
    r = rc.get("/api/remote-run-trace-support")
    assert r.status_code == 200
    assert r.json()["supported"] is True
    assert r.json()["viewer"]["mode"] == "off"


def test_route_trace_returns_raw_json(rc):
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 42})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert 'filename="simulation-42-trace.json"' in r.headers["content-disposition"]
    assert r.content == TRACE


def test_route_trace_capability_gate(rc):
    rc.state["client"] = FakeClient(caps=())
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 42})
    assert r.status_code == 409
    assert "viva-v1-trace" in r.json()["error"]


def test_route_trace_bad_request(rc):
    assert rc.get("/api/remote-run-trace").status_code == 400


def test_trace_routes_survive_readonly(monkeypatch, tmp_path):
    """GETs: a read-only deployment keeps them (the filter only drops mutations)."""
    from fastapi.testclient import TestClient

    from vivarium_workbench.api import app as _app_mod
    from vivarium_workbench.api.app import create_app, get_workspace

    monkeypatch.setattr(remote_trace, "make_client", lambda: FakeClient())
    app = create_app()
    _app_mod._apply_readonly_filter(app)
    app.dependency_overrides[get_workspace] = lambda: tmp_path
    c = TestClient(app)
    assert c.get("/api/remote-run-trace", params={"simulation_id": 1}).status_code == 200
    assert c.get("/api/remote-run-trace-support").status_code == 200


# ------------------------------------------------ empty traces (X-Trace-Events)

EMPTY = json.dumps({"traceEvents": [], "displayTimeUnit": "ms",
                    "otherData": {"simulation_id": 1519}}).encode()


@pytest.mark.parametrize("body,expected", [
    (TRACE, 1),
    (EMPTY, 0),
    # metadata (process/thread names) draws nothing: a trace of only those is empty
    (json.dumps({"traceEvents": [{"ph": "M", "name": "process_name", "pid": 1}]}).encode(), 0),
    (json.dumps([{"ph": "X", "ts": 0, "dur": 1}, {"ph": "M"}, {"ph": "i", "ts": 2}]).encode(), 2),
    (b"not json", None),
    (json.dumps({"no": "events"}).encode(), None),
])
def test_count_trace_events(body, expected):
    assert remote_trace.count_trace_events(body) == expected


def test_count_trace_events_does_not_parse_a_large_document(monkeypatch):
    monkeypatch.setattr(remote_trace, "COUNT_EVENTS_MAX_BYTES", 10)
    assert remote_trace.count_trace_events(EMPTY) is None


class _EmptyTraceClient(FakeClient):
    def simulation_trace(self, simulation_id):
        self.calls.append(("simulation_trace", simulation_id))
        return EMPTY


def test_route_trace_reports_its_event_count(rc):
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 42})
    assert r.headers["x-trace-events"] == "1"
    rc.state["client"] = _EmptyTraceClient()
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 1519})
    assert r.status_code == 200
    assert r.headers["x-trace-events"] == "0"
    assert r.content == EMPTY, "the document itself is still passed through verbatim"


def test_route_trace_omits_the_count_when_unknown(rc, monkeypatch):
    monkeypatch.setattr(remote_trace, "COUNT_EVENTS_MAX_BYTES", 10)
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 42})
    assert r.status_code == 200 and "x-trace-events" not in r.headers
