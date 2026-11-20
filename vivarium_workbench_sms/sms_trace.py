"""A viva-api run's trace, for the workbench's "Open in Perfetto" (step P2, Runs b2: moved verbatim from
``vivarium_workbench.lib.remote_trace``).

viva-api serves a run's trace as a Chrome Trace Event JSON document at
``GET /api/v1/simulations/{id}/trace`` (a viva-api simulation) and
``GET /viva/v1/composites/{id}/trace`` (a composite run), advertised by the
``viva-v1-trace`` capability. The browser never talks to viva-api: it asks the
workbench (``/api/remote-run-trace``), whose core routes the request here --
``SMS_LEGACY.runs.trace`` (a recorded sms-legacy simulation), ``VIVA_CORE.runs.trace`` (a recorded viva-core run) or
the SMS ``unrouted`` facet (anything no record routes) -- and then hands the bytes to Perfetto in the browser.

Branching is on capability membership, never on version (the capability contract in ``sms_api_client``):
a deployment that does not advertise ``viva-v1-trace`` gets **409** naming the
capability, and :func:`support` reports ``supported: false`` so the UI hides
the action instead of offering one that cannot work.

The client is built from :mod:`sms_legacy`'s seams (``SmsApiClient``, ``_sms_api_base``) and the capability helpers
are read through :mod:`sms_api_client`, both at call time: one patch target per name, none bound here.
"""

from __future__ import annotations

from typing import Optional, Union

from vivarium_workbench_sms import sms_api_client, sms_legacy

TraceBody = Union[bytes, dict]


def make_client() -> "sms_api_client.SmsApiClient":
    """A client on the viva-api host, built through ``sms_legacy`` at call time (the plugin's one patch point)."""
    return sms_legacy.SmsApiClient(sms_legacy._sms_api_base())


def support(client: "sms_api_client.SmsApiClient") -> "tuple[dict, int]":
    """Whether viva-api serves run traces (the core adds ``viewer``).

    Always 200: an unreachable viva-api is ``supported: false`` with
    ``reason: "unreachable"`` (the UI just keeps the action hidden), kept
    distinct from ``reason: "capability-absent"`` for a deployment that is
    reachable but does not serve traces.
    """
    try:
        caps = sms_api_client.fetch_capabilities(client)
    except sms_api_client.SmsApiError as e:
        return ({"supported": False, "reason": "unreachable", "error": str(e),
                 "capability": sms_api_client.CAPABILITY_VIVA_V1_TRACE}, 200)
    supported = caps.supports(sms_api_client.CAPABILITY_VIVA_V1_TRACE)
    return ({"supported": supported,
             "reason": None if supported else "capability-absent",
             "capability": sms_api_client.CAPABILITY_VIVA_V1_TRACE,
             "server_version": caps.version}, 200)


def _positive_int(value: object) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        n = int(str(value))
    except (TypeError, ValueError):
        return -1
    return n if n > 0 else -1


def fetch(client: "sms_api_client.SmsApiClient", *, simulation_id: object = None, composite_run_id: object = None,
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
        sms_api_client.require_capabilities(client, sms_api_client.CAPABILITY_VIVA_V1_TRACE)
    except sms_api_client.CapabilityUnsupportedError as e:
        return ({"error": str(e), "missing": e.missing, "server_version": e.version}, 409, "")
    except sms_api_client.SmsApiError as e:
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
    except sms_api_client.SmsApiError as e:
        if e.status in (404, 409):
            return ({"error": str(e)}, e.status, "")
        return ({"error": str(e)}, 502, "")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:80] or "run"
