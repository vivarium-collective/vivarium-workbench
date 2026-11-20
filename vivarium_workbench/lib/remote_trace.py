"""The trace routes' core (``/api/remote-run-trace``, ``/api/remote-run-trace-support``) for the workbench's
"Open in Perfetto": route by the run's record, else the ``unrouted`` backend; count the events; name the viewer.

A run's trace is a Chrome Trace Event JSON document its backend serves. The browser never talks to a backend: it
asks this server, which routes the request (step P2, Runs b2) and hands the bytes to Perfetto in the browser
(``static/perfetto-open.js``):

* a compose-api run (by its record or its id's form, under either id key): compose-api's ``runs.trace``;
* a run of another backend or deployment: its record's 409 refusal;
* a LONE ``simulation_id`` whose record names a provider's backend: that backend's ``runs.trace``;
* anything else (no record, a ``/compose/v1`` ``compose_id``, several ids, none): the ``unrouted`` backend's
  ``trace``, whose own id checks answer. With providers off: 400 for no id or several, else a terminal 409
  ``backend-not-installed``.

Both functions return ``(body, status)`` like the other ``*_views`` modules; a successful :func:`fetch_trace` returns
the trace BYTES as the body. :func:`trace_support` says whether to offer the action at all (always 200).
"""

from __future__ import annotations

from pathlib import Path

import json
from typing import Optional, Union

from vivarium_workbench.lib import perfetto_ui

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


def trace_support() -> "tuple[dict, int]":
    """Whether the trace action can be offered, and which Perfetto to open (``viewer``, always last).

    compose-api serves every run's trace (it has no capabilities route); otherwise the ``unrouted`` backend says
    whether it does. With providers off: ``supported: false, reason: "no-backend"``, with no request (the UI keeps
    the action hidden). Always 200.
    """
    viewer = perfetto_ui.viewer_config().as_dict()
    from vivarium_workbench.lib import backends

    if backends.named() is backends.COMPOSE:
        # compose-api serves every run's trace (get-simulation-trace-chrome); it has no capabilities route
        return ({"supported": True, "reason": None, "capability": "get-simulation-trace-chrome",
                 "viewer": viewer}, 200)
    ub = backends.unrouted()
    if ub is not None and ub.unrouted is not None:
        body, status = ub.unrouted.trace_support()
    else:
        body, status = ({"supported": False, "reason": "no-backend", "capability": None,
                         "error": "no remote backend was named (serve --backend-base-url)"}, 200)
    return {**body, "viewer": viewer}, status


def fetch_trace(*, ws_root: "Path | str", simulation_id: object = None, composite_run_id: object = None,
                compose_id: object = None, deployment: "str | None" = None,
                study: "str | None" = None) -> "tuple[TraceBody, int, str]":
    """The trace for exactly one of ``simulation_id``, ``composite_run_id`` or ``compose_id`` (a ``/compose/v1``
    submission number, never a run's id), routed as the module docstring says.

    Returns ``(body, status, filename)``; ``body`` is the trace bytes on 200 and an ``{"error": ...}`` dict
    otherwise. Statuses are the answering backend's (400 bad/ambiguous id, 404, 409, 502), or a record's 409 refusal.
    """
    from vivarium_workbench.lib import backends
    import vivarium_workbench.lib.backends.records as records

    ids = {"simulation_id": simulation_id, "composite_run_id": composite_run_id, "compose_id": compose_id}
    # Step 1c-2: a run id is looked up in its record, which names the backend that ran it. compose-api answers
    # itself under either key; a run of another backend is refused. A provider's backend answers only a LONE
    # simulation_id (the Runs tab sends a viva-core row's id as simulation_id): any other combination goes to the
    # unrouted backend as given, exactly as before Runs b2.
    for key in ("simulation_id", "composite_run_id"):
        given = ids[key]
        if given in (None, ""):
            continue
        res = backends.resolve(ws_root, given, deployment=deployment, study=study)
        if res.refusal is not None:
            return res.refusal[0], res.refusal[1], ""
        if res.kind == backends.COMPOSE.name:
            return backends.COMPOSE.runs.trace(str(given).strip())
        if (res.via == "record" and res.kind and key == "simulation_id"
                and composite_run_id in (None, "") and compose_id in (None, "")):
            return backends.by_kind(res.kind).runs.trace(str(given).strip())
    ub = backends.unrouted()
    if ub is not None and ub.unrouted is not None:
        return ub.unrouted.trace(ids)
    # Providers off: nothing installed serves a run that no record or id form routed.
    named_ids = [v for v in ids.values() if v not in (None, "")]
    if len(named_ids) != 1:
        return ({"error": "give exactly one of simulation_id, composite_run_id, compose_id"}, 400, "")
    body, status = records.unowned(str(named_ids[0]).strip())
    return body, status, ""
