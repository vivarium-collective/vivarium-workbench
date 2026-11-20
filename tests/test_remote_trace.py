"""The "Open in Perfetto" backend: the trace proxy and its capability gate.

viva-api serves a run's Chrome Trace Event JSON at ``/api/v1/simulations/{id}/trace``
and ``/viva/v1/composites/{id}/trace`` when it advertises ``viva-v1-trace``. The
workbench proxies it (``/api/remote-run-trace``) and says whether to offer the
action at all (``/api/remote-run-trace-support``). A fake ``SmsApiClient`` stands
in for viva-api throughout -- nothing here reaches a network.
"""

from __future__ import annotations

from pathlib import Path

import json

import pytest

from vivarium_workbench.lib import remote_trace

#: The capability viva-api advertises for the trace endpoints (``sms_api_client.CAPABILITY_VIVA_V1_TRACE``; a plugin
#: test below pins that the two agree). Spelled here so the core tests collect without the plugin (step P3, K2c).
CAPABILITY_VIVA_V1_TRACE = "viva-v1-trace"


def _sms_trace():
    from vivarium_workbench_sms import sms_trace
    return sms_trace


def SmsApiError(*args, **kwargs):  # noqa: N802 -- builds the plugin's exception; only the plugin tests call it
    from vivarium_workbench_sms.sms_api_client import SmsApiError as _SmsApiError
    return _SmsApiError(*args, **kwargs)

TRACE = json.dumps({"traceEvents": [
    {"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": 1, "tid": 1},
]}).encode()



# Step 1c-2: the run views and fetch_trace look the run up in the workspace's records; an empty workspace has none,
# so these tests keep exercising the id's form.
import tempfile as _tempfile  # noqa: E402
_WS = Path(_tempfile.mkdtemp(prefix="no-records-ws-"))

class FakeClient:
    """Just the surface ``sms_trace`` touches."""

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


@pytest.fixture
def use(monkeypatch):
    """The client viva-api's half gets (step P2, Runs b2: ``sms_trace.make_client``, the plugin's builder). The lib
    tests call the CORE functions, so each assertion covers the whole chain: core routing, the SMS unrouted facet,
    ``sms_trace``. A test reaching the client needs the plugin (marked ``plugin``); without the plugin there is no
    client to hand out, so a core test using it asserts the request never got that far."""
    slot = {"client": FakeClient()}
    try:
        monkeypatch.setattr(_sms_trace(), "make_client", lambda: slot["client"])
    except ImportError:   # the core-only job
        pass

    def _use(client):
        slot["client"] = client
        return client

    return _use


# ---------------------------------------------------------------- lib: support


@pytest.mark.plugin
def test_support_true_when_capability_advertised(monkeypatch, use):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI", "https://ui.perfetto.dev")
    use(FakeClient())
    body, status = remote_trace.trace_support()
    assert status == 200
    assert body["supported"] is True and body["reason"] is None
    assert body["capability"] == "viva-v1-trace"
    assert body["viewer"] == {"mode": "external", "url": "https://ui.perfetto.dev/", "version": None}


@pytest.mark.plugin
def test_support_false_when_capability_absent(use):
    use(FakeClient(caps=("chain-progress",)))
    body, _ = remote_trace.trace_support()
    assert body["supported"] is False and body["reason"] == "capability-absent"


@pytest.mark.plugin
def test_support_false_on_pre_capabilities_server(use):
    """A deployment predating the capabilities endpoint (404) advertises nothing."""
    use(FakeClient(caps_error=SmsApiError("nope", status=404)))
    body, _ = remote_trace.trace_support()
    assert body["supported"] is False and body["reason"] == "capability-absent"


@pytest.mark.plugin
def test_support_unreachable_is_not_reported_as_unsupported(use):
    use(FakeClient(caps_error=SmsApiError("tunnel down")))
    body, status = remote_trace.trace_support()
    assert status == 200
    assert body["supported"] is False and body["reason"] == "unreachable"


# ---------------------------------------------------------------- lib: fetch


@pytest.mark.plugin
def test_fetch_simulation_trace_passes_bytes_through(use):
    client = use(FakeClient())
    body, status, filename = remote_trace.fetch_trace(simulation_id="42", ws_root=_WS)
    assert status == 200 and body == TRACE
    assert filename == "simulation-42-trace.json"
    assert client.calls == [("simulation_trace", 42)]


@pytest.mark.plugin
def test_fetch_composite_run_trace(use):
    client = use(FakeClient())
    body, status, filename = remote_trace.fetch_trace(composite_run_id="run/../x y", ws_root=_WS)
    assert status == 200 and body == TRACE
    assert client.calls == [("composite_run_trace", "run/../x y")]
    assert "/" not in filename and " " not in filename


@pytest.mark.plugin
def test_fetch_compose_id_resolves_correlation_id(use):
    client = use(FakeClient(correlation_id="compose-sim-xyz"))
    body, status, _ = remote_trace.fetch_trace(compose_id=7, ws_root=_WS)
    assert status == 200
    assert client.calls == [("compose_status", 7), ("composite_run_trace", "compose-sim-xyz")]


@pytest.mark.plugin
def test_fetch_compose_id_without_correlation_is_404(use):
    use(FakeClient(correlation_id=None))
    body, status, _ = remote_trace.fetch_trace(compose_id=7, ws_root=_WS)
    assert status == 404 and "correlation_id" in body["error"]


@pytest.mark.parametrize("kwargs", [
    {},
    {"simulation_id": 1, "composite_run_id": "r"},
    pytest.param({"simulation_id": "abc"}, marks=pytest.mark.plugin),
    pytest.param({"simulation_id": 0}, marks=pytest.mark.plugin),
    pytest.param({"compose_id": -3}, marks=pytest.mark.plugin),
])
def test_fetch_rejects_bad_or_ambiguous_ids(kwargs, use):
    client = use(FakeClient())
    body, status, _ = remote_trace.fetch_trace(**kwargs, ws_root=_WS)
    assert status == 400 and "error" in body
    assert client.calls == []


@pytest.mark.plugin
def test_fetch_capability_absent_is_409_and_never_calls_trace(use):
    client = use(FakeClient(caps=()))
    body, status, _ = remote_trace.fetch_trace(simulation_id=1, ws_root=_WS)
    assert status == 409
    assert body["missing"] == ["viva-v1-trace"]
    assert client.calls == []


@pytest.mark.plugin
def test_fetch_unreachable_is_502(use):
    use(FakeClient(caps_error=SmsApiError("down")))
    body, status, _ = remote_trace.fetch_trace(simulation_id=1, ws_root=_WS)
    assert status == 502


@pytest.mark.parametrize("upstream,expected", [(404, 404), (409, 409), (500, 502), (None, 502)])
@pytest.mark.plugin
def test_fetch_upstream_errors(upstream, expected, use):
    use(FakeClient(trace_error=SmsApiError("x", status=upstream)))
    body, status, _ = remote_trace.fetch_trace(simulation_id=3, ws_root=_WS)
    assert status == expected and "error" in body


# ---------------------------------------------------------------- routes


@pytest.fixture
def rc(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from vivarium_workbench.api.app import create_app, get_workspace

    state = {"client": FakeClient()}
    try:
        monkeypatch.setattr(_sms_trace(), "make_client", lambda: state["client"])
    except ImportError:   # the core-only job: no client to hand out (the core tests never reach one)
        pass
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI", "off")
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_path
    c = TestClient(app)
    c.state = state  # type: ignore[attr-defined]
    return c


@pytest.mark.plugin
def test_route_support(rc):
    r = rc.get("/api/remote-run-trace-support")
    assert r.status_code == 200
    assert r.json()["supported"] is True
    assert r.json()["viewer"]["mode"] == "off"


@pytest.mark.plugin
def test_route_trace_returns_raw_json(rc):
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 42})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert 'filename="simulation-42-trace.json"' in r.headers["content-disposition"]
    assert r.content == TRACE


@pytest.mark.plugin
def test_route_trace_capability_gate(rc):
    rc.state["client"] = FakeClient(caps=())
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 42})
    assert r.status_code == 409
    assert "viva-v1-trace" in r.json()["error"]


def test_route_trace_bad_request(rc):
    assert rc.get("/api/remote-run-trace").status_code == 400


@pytest.mark.plugin
def test_trace_routes_survive_readonly(monkeypatch, tmp_path):
    """GETs: a read-only deployment keeps them (the filter only drops mutations)."""
    from fastapi.testclient import TestClient

    from vivarium_workbench.api import app as _app_mod
    from vivarium_workbench.api.app import create_app, get_workspace

    monkeypatch.setattr(_sms_trace(), "make_client", lambda: FakeClient())
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


@pytest.mark.plugin
def test_route_trace_reports_its_event_count(rc):
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 42})
    assert r.headers["x-trace-events"] == "1"
    rc.state["client"] = _EmptyTraceClient()
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 1519})
    assert r.status_code == 200
    assert r.headers["x-trace-events"] == "0"
    assert r.content == EMPTY, "the document itself is still passed through verbatim"


@pytest.mark.plugin
def test_route_trace_omits_the_count_when_unknown(rc, monkeypatch):
    monkeypatch.setattr(remote_trace, "COUNT_EVENTS_MAX_BYTES", 10)
    r = rc.get("/api/remote-run-trace", params={"simulation_id": 42})
    assert r.status_code == 200 and "x-trace-events" not in r.headers


# ---------------------------------------------------------------- the seam (step P2, Runs b2)

#: What moved from lib.remote_trace to the plugin's sms_trace. The core binds none of them: a stale
#: ``monkeypatch.setattr(remote_trace, name, ...)`` raises instead of silently patching a dead copy.
_MOVED_TO_SMS_TRACE = ("make_client", "SmsApiClient", "SmsApiError", "require_capabilities", "fetch_capabilities",
                       "CapabilityUnsupportedError", "CAPABILITY_VIVA_V1_TRACE", "sms_api_base", "_positive_int",
                       "_safe")


def test_the_core_binds_no_viva_api_seam():
    assert [n for n in _MOVED_TO_SMS_TRACE if hasattr(remote_trace, n)] == []


@pytest.mark.plugin
def test_the_moved_seam_lives_in_sms_trace():
    from vivarium_workbench_sms.sms_api_client import CAPABILITY_VIVA_V1_TRACE as plugin_capability

    sms_trace = _sms_trace()
    assert [n for n in ("_positive_int", "_safe") if not hasattr(sms_trace, n)] == []
    assert sms_trace.make_client.__module__ == sms_trace.__name__
    assert plugin_capability == CAPABILITY_VIVA_V1_TRACE


def test_the_core_names_no_provider_backend():
    """The core routes by the record's kind through ``backends.by_kind``; it never spells a provider's kind."""
    import ast

    tree = ast.parse(Path(remote_trace.__file__).read_text(encoding="utf-8"))
    kinds = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value in ("viva-core", "sms-legacy")]
    names = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ("VIVA_CORE", "SMS_LEGACY")]
    assert (kinds, names) == ([], [])


def test_the_core_functions_take_no_client_and_only_keywords():
    import inspect

    assert list(inspect.signature(remote_trace.trace_support).parameters) == []
    params = inspect.signature(remote_trace.fetch_trace).parameters
    assert "client" not in params
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    with pytest.raises(TypeError):
        remote_trace.fetch_trace(FakeClient(), simulation_id=1, ws_root=_WS)  # type: ignore[misc]


@pytest.mark.plugin
def test_sms_trace_reads_its_seams_through_their_modules():
    """``sms_trace`` builds its client from ``sms_legacy``'s seams and reads the capability helpers from
    ``sms_api_client`` at call time, never from-imports them: tests patch them on those modules."""
    import ast

    watched = {"vivarium_workbench_sms.sms_api_client", "vivarium_workbench_sms.sms_legacy"}
    tree = ast.parse(Path(_sms_trace().__file__).read_text(encoding="utf-8"))
    offenders = [f"{n.module}:{n.lineno}" for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module in watched]
    assert offenders == []


@pytest.mark.plugin
def test_the_client_is_built_from_sms_legacys_seams(monkeypatch):
    """One patch point with the rest of the plugin: ``sms_legacy.SmsApiClient`` / ``sms_legacy._sms_api_base``
    (review fix 4), so a test patching those reaches the trace too."""
    from vivarium_workbench_sms import sms_legacy

    built = []
    monkeypatch.setattr(sms_legacy, "_sms_api_base", lambda: "http://seam:1")
    monkeypatch.setattr(sms_legacy, "SmsApiClient", lambda base: built.append(base) or "client")
    assert _sms_trace().make_client() == "client" and built == ["http://seam:1"]
