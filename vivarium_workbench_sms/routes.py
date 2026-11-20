"""The SMS provider's own routes (docs/backend-adapters.md §2.2, step P2 Runs a2): ``SmsProvider.router()``.

The four routes only SMS serves, moved verbatim from ``api/app.py`` (same paths, methods, status codes, models,
tags, summaries, docstrings and function names, so the same OpenAPI operations): the legacy pipeline
(``POST /api/remote-run-start``, ``GET /api/remote-run-status``) and phase 1 of the WS1 thin client
(``POST /api/remote-run-build``, ``POST /api/remote-run-pinned-build``). No browser code calls them.

Step P2 Builds B3 added the three build routes the Source panel calls, moved the same way: the build list
(``GET /api/source/builds``), registering a branch head (``POST /api/source/build-remote``) and switching the session
to a build (``POST /api/source/switch-build``). The session half of the switch (rebind, cookie, eager prepare) is the
core's ``api.deps.bind_session``, shared with the core's ``/api/source/switch``.

``create_app`` includes the router just before the SPA catch-all and refuses a path it already serves
(``api.app._include_provider_routers``). The app-wide guards (CSRF/origin, Host, session, the plain-identifier
dependency, the error envelope) apply to these routes as to the core's. A read-only deployment keeps the paths in
``READONLY_ALLOWED`` (and every GET); ``remote-run-pinned-build`` is dropped there, as before. The built-in AI never
sees the paths in ``AI_EXCLUDED`` (step P2 Routes Rt2).

The route bodies read the views (``remote_pipeline_views``, ``source_build_views``), ``remote_run_jobs.manager`` and
``api.deps``' ``session_key_of`` / ``bind_session`` through their modules at call time, so a patch on those modules
steers them. Only ``SmsProvider.router()`` imports this module, lazily, so an
env worker that loads the provider never imports FastAPI or the route models.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse

from vivarium_workbench.api import deps as _deps
from vivarium_workbench.api.deps import get_workspace
from vivarium_workbench.lib import job_status_views as _job_status_views
from vivarium_workbench.lib.models import JobStatusPayload, SourceSwitchResponse
from vivarium_workbench_sms import remote_pipeline_views as _views
from vivarium_workbench_sms import remote_run_jobs as _remote_run_jobs
from vivarium_workbench_sms import source_build_views as _source_build_views
from vivarium_workbench_sms.models import (
    BuildRemoteRequest,
    BuildRemoteResponse,
    RemoteRunStartRequest,
    RemoteRunStartResponse,
    SourceBuilds,
    SwitchBuildRequest,
)

#: Its POST paths a read-only deployment keeps (``ProviderRouter.readonly_allowed``): the run launch and the
#: thin client's build phase, as on the core's allowlist before step P2 Runs a2. ``remote-run-pinned-build`` was
#: never on it. The remote-build flow (register a build, switch to it: "the core of remote-only") was on the core's
#: allowlist before step P2 Builds B3.
READONLY_ALLOWED = frozenset({"/api/remote-run-start", "/api/remote-run-build",
                              "/api/source/build-remote", "/api/source/switch-build"})

#: Its paths the built-in AI must never see or call (``ProviderRouter.ai_excluded``, step P2 Routes Rt2): the legacy
#: pipeline and the thin client's build phase push the workspace branch to origin before building. They were on
#: ``lib/ai_tools.EXCLUDED_PATHS`` before. The ``/api/source/*`` routes are already out of the AI's reach by the
#: core's own ``/api/source/`` prefix rule (switching what the server is bound to), so they are not repeated here.
AI_EXCLUDED = frozenset({"/api/remote-run-start", "/api/remote-run-build"})


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

    # -----------------------------------------------------------------------
    # Source — the sms-api build list, register a build, switch to a build (step P2 Builds B3)
    # -----------------------------------------------------------------------

    @router.get(
        "/api/source/builds",
        response_model=SourceBuilds,
        tags=["Workspaces & sources"],
        summary="Remote sms-api simulator build list for the source dropdown",
    )
    def source_builds_route() -> SourceBuilds:
        """Remote sms-api build list (mirrors the stdlib GET /api/source/builds).

        Best-effort: returns ``{builds: [], error: <reason>}`` when the sms-api
        tunnel is not reachable.  Always HTTP 200.  No workspace dependency — the
        sms-api base URL is read from the ``SMS_API_BASE`` env var.

        Library-backed via ``vivarium_workbench_sms.source_build_views.build_source_builds``.
        """
        return SourceBuilds.model_validate(_source_build_views.build_source_builds())

    @router.post(
        "/api/source/build-remote",
        response_model=BuildRemoteResponse,
        tags=["Workspaces & sources"],
        summary="Register a repo+branch HEAD as an sms-api build",
    )
    def source_build_remote(
        req: BuildRemoteRequest,
    ) -> Union[BuildRemoteResponse, JSONResponse]:
        """Register a repo+branch's HEAD as an sms-api simulator build.

        Mirrors the stdlib ``POST /api/source/build-remote``.  Body:
        ``{"repo", "branch"}`` — resolves the branch HEAD via sms-api and
        registers it, returning ``{ok, simulator_id, repo, branch, commit}``.

        Status codes (byte-identical to the legacy handler):
          - 400  missing repo/branch (``{"error": "repo and branch are required"}``)
          - 502  unresolved HEAD (``{"error": "could not resolve branch HEAD via sms-api"}``)
          - 502  ``{"error": "sms-api: <err>"}``
          - 200  ``{ok, simulator_id, repo, branch, commit}``

        The CSRF middleware already guards this POST.  Library-backed via
        ``lib.source_build_views.build_remote`` (a network route — sms-api is
        reached through the lib client).
        """
        body, status = _source_build_views.build_remote(req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return BuildRemoteResponse.model_validate(body)

    @router.post(
        "/api/source/switch-build",
        response_model=SourceSwitchResponse,
        tags=["Workspaces & sources"],
        summary="Materialize a remote build's workspace and re-point in-process",
    )
    def source_switch_build(
        req: SwitchBuildRequest,
        request: Request,
        response: Response,
    ) -> Union[SourceSwitchResponse, JSONResponse]:
        """Materialize a remote build's workspace (cached) and re-point to it.

        Body: ``{"simulator_id"}`` — looks the build up in the sms-api listing,
        downloads+extracts its workspace once (cached per commit), then
        materializes THIS session's own exclusive clone of it (item 20's fix —
        every session used to bind to the same shared directory, with no write
        isolation), stamps build provenance into that clone (best-effort), then
        binds the **calling session** to it (per-session, slice 4/5 — the
        process-global root and other sessions are untouched) and returns
        ``{ok, source}``.

        Status codes:
          - 400  missing ``simulator_id`` (``{"error": "missing 'simulator_id'"}``)
          - 502  sms-api unreachable (``{"error": "sms-api unavailable: <err>"}``)
          - 404  build not found (``{"error": "build <id> not found"}``)
          - 502  materialize failed (``{"error": "materialize failed: <err>"}``)
          - 200  ``{ok: true, source: {path, name}}``

        The CSRF middleware already guards this POST.  ``switch_active=False`` —
        materialize + resolve without the global re-point; the per-session bind is
        ``session_registry.rebind``. ``session_key`` is resolved BEFORE the call so
        ``switch_build`` can materialize this session's own clone rather than the
        shared base every session used to bind to directly.

        **Aligns the cookie on this response (item 78)** — same as ``source_switch``
        and for the same reason: the client's own ``window.location.reload()`` right
        after this call carries no ``X-VW-Session`` header (a plain navigation, not a
        ``fetch()``), so it resolves via the cookie. Scoped to *this* response only —
        the middleware no longer refreshes the cookie on unrelated traffic (see
        ``_session_workspace_mw``), so without this the reload would keep rendering
        whatever workspace the cookie happened to already be on.
        """
        session_key = _deps.session_key_of(request)
        body, status = _source_build_views.switch_build(
            req.model_dump(), switch_active=False, session_key=session_key)
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        # The core's session binding (api.deps.bind_session): rebind, the cookie, and managed=True (item 63): the
        # materialized build is a bare tarball extraction with no `.venv` of its own, so it is provisioned via
        # `uv sync` against this commit's own lock file before anything trusts this session's environment.
        _deps.bind_session(request, response, session_key, body, managed=True)
        return SourceSwitchResponse.model_validate(body)

    return router
