"""The SMS provider's own routes (docs/backend-adapters.md §2.2, step P2 Runs a2): ``SmsProvider.router()``.

The four routes only SMS serves, moved verbatim from ``api/app.py`` (same paths, methods, status codes, models,
tags, summaries, docstrings and function names, so the same OpenAPI operations): the legacy pipeline
(``POST /api/remote-run-start``, ``GET /api/remote-run-status``) and phase 1 of the WS1 thin client
(``POST /api/remote-run-build``, ``POST /api/remote-run-pinned-build``). No browser code calls them.

``create_app`` includes the router just before the SPA catch-all and refuses a path it already serves
(``api.app._include_provider_routers``). The app-wide guards (CSRF/origin, Host, session, the plain-identifier
dependency, the error envelope) apply to these routes as to the core's. A read-only deployment keeps the paths in
``READONLY_ALLOWED`` (and every GET); ``remote-run-pinned-build`` is dropped there, as before.

The route bodies read the views (``remote_pipeline_views``) and ``remote_run_jobs.manager`` through their modules at
call time, so a patch on those modules steers them. Only ``SmsProvider.router()`` imports this module, lazily, so an
env worker that loads the provider never imports FastAPI or the route models.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from vivarium_workbench.api.deps import get_workspace
from vivarium_workbench.lib import job_status_views as _job_status_views
from vivarium_workbench.lib.models import JobStatusPayload, RemoteRunStartRequest, RemoteRunStartResponse
from vivarium_workbench_sms import remote_pipeline_views as _views
from vivarium_workbench_sms import remote_run_jobs as _remote_run_jobs

#: Its POST paths a read-only deployment keeps (``ProviderRouter.readonly_allowed``): the run launch and the
#: thin client's build phase, as on the core's allowlist before step P2 Runs a2. ``remote-run-pinned-build`` was
#: never on it.
READONLY_ALLOWED = frozenset({"/api/remote-run-start", "/api/remote-run-build"})


def build_router() -> APIRouter:
    """A new router with the four SMS-only routes (built per call: one per app)."""
    router = APIRouter()

    @router.get(
        "/api/remote-run-status",
        response_model=JobStatusPayload,
        tags=["Rigor & jobs"],
        summary="Status of a remote (sms-api) run job",
    )
    def remote_run_status(
        job_id: str = "",
    ) -> Union[JobStatusPayload, JSONResponse]:
        """Status of a remote-run job (mirrors the stdlib
        ``GET /api/remote-run-status?job_id=<id>``).

        Reads the in-process ``lib.remote_run_jobs.manager`` singleton.  No
        ``job_id`` returns ``{jobs: manager.list_recent(10)}``; a known id
        returns the job's ``to_dict()`` (a ``steps[]`` shape); an unknown id
        returns HTTP 404 ``{error: "job not found"}``.

        The manager is read at call time via the module attribute
        (``_remote_run_jobs.manager``) so tests can monkeypatch it.
        Library-backed via the pure ``lib.job_status_views.job_status``.
        """
        body, status = _job_status_views.job_status(_remote_run_jobs.manager, job_id)
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return JobStatusPayload.model_validate(body)

    # -----------------------------------------------------------------------
    # Runs — remote (sms-api) simulation-run SUBMIT
    # -----------------------------------------------------------------------

    @router.post(
        "/api/remote-run-start",
        response_model=RemoteRunStartResponse,
        tags=["Runs"],
        summary="Submit a remote (sms-api) simulation pipeline job for a study",
        status_code=202,
    )
    def remote_run_start(
        req: RemoteRunStartRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Submit a remote sms-api simulation pipeline job for a study.

        Mirrors the stdlib ``POST /api/remote-run-start``.  Body:
        ``{"study", "num_generations"?, "num_seeds"?, "run_parca"?}`` — pushes
        the workspace branch, builds a simulator from the pushed commit, runs it
        on smsvpctest, polls, downloads, and lands the native store as a study
        run, all as one background pipeline job on the in-process
        ``lib.remote_run_jobs.manager`` singleton (the SAME manager the
        ``GET /api/remote-run-status`` poller reads, so the submit is visible to
        the status GET).

        Status codes (byte-identical to the legacy handler):
          - 401  not authenticated (``{"error": "not authenticated"}``)
          - 400  missing study (``{"error": "study is required"}``)
          - 409  no origin remote (``{"error": "no GitHub remote configured"}``)
          - 409  unresolved url (``{"error": "could not resolve origin remote url"}``)
          - 404  spec missing (``{"error": "study <slug> not found"}``)
          - 202  ``{"job_id": <id>}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.remote_run_views.remote_run_start`` (ws_root-parameterised);
        every path (incl. the 202 success) is wrapped in ``JSONResponse`` so the
        lib-returned status code is preserved verbatim.
        """
        # exclude_none so an OMITTED optional field is absent (not present-as-None)
        # — the lib builder's ``body.get("run_parca", True)`` must see the True
        # default for a client that omits the key, matching the legacy raw-JSON
        # contract (``bool(None)`` would otherwise flip the default to False).
        body, status = _views.remote_run_start(ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # Remote-run THIN CLIENT (WS1, two-phase), phase 1. sms-api owns
    # async/state; these routes are stateless maps over one sms-api call each.
    # The lifecycle routes after phase 1 (submit, poll, land, cancel) are core.
    # -----------------------------------------------------------------------
    @router.post("/api/remote-run-build", tags=["Runs"], status_code=202,
                 summary="Thin-client phase 1: push + register the simulator build")
    def remote_run_build(
        req: Union[dict, None] = Body(default=None),
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        body, status = _views.remote_run_build_start(ws, req or {})
        return JSONResponse(status_code=status, content=body)

    @router.post("/api/remote-run-pinned-build", tags=["Runs"], status_code=202,
                 summary="Pinned phase 1: resolve the latest built simulator (no push/login)")
    def remote_run_pinned_build(
        req: Union[dict, None] = Body(default=None),
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Pinned variant of phase 1: resolve the latest built simulator for the
        configured repo@branch. No git push, no GitHub login — enabled by the
        ``VIVARIUM_WORKBENCH_REMOTE_PINNED`` deployment config."""
        body, status = _views.remote_run_pinned_build_start(ws, req or {})
        return JSONResponse(status_code=status, content=body)

    return router
