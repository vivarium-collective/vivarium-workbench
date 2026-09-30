"""A remote run's trace, fetched from viva-api for the workbench's "Open in Perfetto".

viva-api serves a run's trace as a Chrome Trace Event JSON document at
``GET /api/v1/simulations/{id}/trace`` (a viva-api simulation) and
``GET /viva/v1/composites/{id}/trace`` (a composite run), advertised by the
``viva-v1-trace`` capability. The browser never talks to viva-api: it asks this
server (``/api/remote-run-trace``), which proxies through
:class:`~vivarium_workbench.lib.sms_api_client.SmsApiClient`, and then hands the
bytes to Perfetto in the browser (``static/perfetto-open.js``).

Both functions return ``(body, status)`` like the other ``*_views`` modules; a
successful :func:`fetch_trace` returns the trace BYTES as the body.

Branching is on capability membership, never on version (``server_capabilities``):
a deployment that does not advertise ``viva-v1-trace`` gets **409** naming the
capability, and :func:`trace_support` reports ``supported: false`` so the UI hides
the action instead of offering one that cannot work.
"""

from __future__ import annotations

import json
from typing import Optional, Union

from vivarium_workbench.lib import perfetto_ui
from vivarium_workbench.lib.server_capabilities import (
    CAPABILITY_VIVA_V1_TRACE,
    CapabilityUnsupportedError,
    fetch_capabilities,
    require_capabilities,
)
from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError, sms_api_base

TraceBody = Union[bytes, dict]

#: The response header carrying :func:`count_trace_events` (omitted when unknown).
TRACE_EVENTS_HEADER = "X-Trace-Events"

#: Above this size the document is not parsed to count its events. An empty trace is
#: ~100 bytes of envelope (``{"traceEvents": [], "displayTimeUnit": ..., "otherData":
#: {...}}``), so the question "did this run record anything?" is always answered for the
#: documents it matters for; a multi-megabyte one is not empty, and parsing it just to say
#: so would cost server memory for nothing.
COUNT_EVENTS_MAX_BYTES = 8 * 1024 * 1024


def count_trace_events(body: bytes) -> Optional[int]:
    """The number of real (non-metadata) events in a Chrome Trace Event document.

    Metadata events (``"ph": "M"`` -- process/thread names) draw nothing, so a trace
    holding only those is as empty as ``{"traceEvents": []}``: Perfetto opens it on an
    empty workspace, which is what users of runs recorded without event sinks saw.
    Accepts both forms of the format (an object with ``traceEvents``, or a bare array).
    ``None`` when unknown -- too large to be worth parsing (see
    :data:`COUNT_EVENTS_MAX_BYTES`) or not a trace document -- and the caller then says
    nothing rather than guessing.
    """
    if len(body) > COUNT_EVENTS_MAX_BYTES:
        return None
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    events = doc.get("traceEvents") if isinstance(doc, dict) else doc
    if not isinstance(events, list):
        return None
    return sum(1 for e in events if not (isinstance(e, dict) and e.get("ph") == "M"))


def make_client() -> SmsApiClient:
    """The client the routes use (a seam: tests replace ``SmsApiClient`` here)."""
    return SmsApiClient(sms_api_base())


def trace_support(client: SmsApiClient) -> "tuple[dict, int]":
    """Whether the trace action can be offered, and which Perfetto to open.

    Always 200: an unreachable viva-api is ``supported: false`` with
    ``reason: "unreachable"`` (the UI just keeps the action hidden), kept
    distinct from ``reason: "capability-absent"`` for a deployment that is
    reachable but does not serve traces.
    """
    viewer = perfetto_ui.viewer_config().as_dict()
    try:
        caps = fetch_capabilities(client)
    except SmsApiError as e:
        return ({"supported": False, "reason": "unreachable", "error": str(e),
                 "capability": CAPABILITY_VIVA_V1_TRACE, "viewer": viewer}, 200)
    supported = caps.supports(CAPABILITY_VIVA_V1_TRACE)
    return ({"supported": supported,
             "reason": None if supported else "capability-absent",
             "capability": CAPABILITY_VIVA_V1_TRACE,
             "server_version": caps.version,
             "viewer": viewer}, 200)


def _positive_int(value: object) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        n = int(str(value))
    except (TypeError, ValueError):
        return -1
    return n if n > 0 else -1


def fetch_trace(client: SmsApiClient, *, simulation_id: object = None,
                composite_run_id: object = None,
                compose_id: object = None) -> "tuple[TraceBody, int, str]":
    """The trace for exactly one of: a viva-api simulation, a composite run, or a
    ``/compose/v1`` submission (resolved to its composite run by ``correlation_id``).

    Returns ``(body, status, filename)``; ``body`` is the trace bytes on 200 and an
    ``{"error": ...}`` dict otherwise. Statuses: 400 bad/ambiguous id, 409
    capability absent, 404 no such run / no trace yet (passed through from
    viva-api), 502 viva-api unreachable or failed.
    """
    sim = _positive_int(simulation_id)
    compose = _positive_int(compose_id)
    run = str(composite_run_id).strip() if composite_run_id not in (None, "") else None
    given = [x for x in (sim, compose, run) if x is not None]
    if len(given) != 1:
        return ({"error": "give exactly one of simulation_id, composite_run_id, compose_id"}, 400, "")
    if sim == -1 or compose == -1:
        return ({"error": "simulation_id / compose_id must be a positive integer"}, 400, "")

    try:
        require_capabilities(client, CAPABILITY_VIVA_V1_TRACE)
    except CapabilityUnsupportedError as e:
        return ({"error": str(e), "missing": e.missing, "server_version": e.version}, 409, "")
    except SmsApiError as e:
        return ({"error": f"viva-api unreachable: {e}"}, 502, "")

    try:
        if sim is not None:
            return client.simulation_trace(sim), 200, f"simulation-{sim}-trace.json"
        if compose is not None:
            status = client.compose_status(compose) or {}
            run = str(status.get("correlation_id") or "").strip() or None
            if run is None:
                return ({"error": f"compose run {compose} has no correlation_id to find its trace by"},
                        404, "")
        assert run is not None
        return client.composite_run_trace(run), 200, f"composite-{_safe(run)}-trace.json"
    except SmsApiError as e:
        if e.status in (404, 409):
            return ({"error": str(e)}, e.status, "")
        return ({"error": str(e)}, 502, "")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:80] or "run"
