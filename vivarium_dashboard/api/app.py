"""FastAPI application — the typed seam for the dashboard's HTTP API.

This is the **seed of a strangler-fig migration**: the dashboard is served today
by a 16.9k-line stdlib ``http.server`` handler (``vivarium_dashboard/server.py``)
with hand-dispatched routes and untyped dict payloads. Rather than rewrite it in
one pass, we stand up a FastAPI app here that serves a small, growing set of
routes with **typed pydantic responses** (so they get automatic validation and
an OpenAPI schema). Routes move over a few at a time; both servers back onto the
same ``lib/`` functions, so there is one implementation, not two.

Run it standalone (does not yet replace the stdlib server) and browse the
auto-generated **Swagger UI** to see every typed route:

    python -m vivarium_dashboard.api --workspace /path/to/workspace
    # → Swagger UI at http://127.0.0.1:8001/docs
    #   ReDoc      at http://127.0.0.1:8001/redoc
    #   raw schema at http://127.0.0.1:8001/openapi.json

(or, equivalently, ``uvicorn vivarium_dashboard.api.app:app --reload`` with
``VIVARIUM_DASHBOARD_WORKSPACE`` set.)

Today's routes are read-only and stateless (workspace-backed). Stateful routes
(e.g. remote-run status, which reads the in-memory RemoteRunManager owned by the
stdlib server) move over once the two servers share process state.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

import json
import subprocess

from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from vivarium_dashboard.lib import active_workspace
from vivarium_dashboard.lib import csrf as _csrf
from vivarium_dashboard.lib import source_switch_views as _source_switch_views
from vivarium_dashboard.lib import source_build_views as _source_build_views
from vivarium_dashboard.lib import job_status_views as _job_status_views
from vivarium_dashboard.lib import run_jobs as _run_jobs
from vivarium_dashboard.lib import remote_run_jobs as _remote_run_jobs
from vivarium_dashboard.lib import remote_run_views as _remote_run_views
from vivarium_dashboard.lib import auth_views as _auth_views
from vivarium_dashboard.lib import composite_run_views as _cr_views
from vivarium_dashboard.lib import composite_test_run_views as _composite_test_run_views
from vivarium_dashboard.lib import study_create_views as _study_create_views
from vivarium_dashboard.lib import investigation_run_one_views as _investigation_run_one_views
from vivarium_dashboard.lib import investigation_run_views as _investigation_run_views
from vivarium_dashboard.lib import compare_group_mutations as _compare_grp_mut
from vivarium_dashboard.lib import viz_write_mutations as _viz_write_mut
from vivarium_dashboard.lib import viz_commit_mutations as _viz_commit_mut
from vivarium_dashboard.lib import viz_accept_views as _viz_accept_views
from vivarium_dashboard.lib import viz_preview_views as _viz_preview_views
from vivarium_dashboard.lib import viz_preview_instance_views as _viz_preview_instance_views
from vivarium_dashboard.lib import upload_mutations as _upload_mut
from vivarium_dashboard.lib import reference_mutations as _reference_mut
from vivarium_dashboard.lib import composite_mutations as _composite_mut
from vivarium_dashboard.lib import investigation_viz_mutations as _inv_viz_mut
from vivarium_dashboard.lib import lifecycle_mutations as _lifecycle_mut
from vivarium_dashboard.lib import scaffold_mutations as _scaffold_mut
from vivarium_dashboard.lib import composite_state_views as _composite_state_views
from vivarium_dashboard.lib import data_sources as _data_sources
from vivarium_dashboard.lib import download_views as _download_views
from vivarium_dashboard.lib import events as _events
from vivarium_dashboard.lib import explorer_data as _explorer_data
from vivarium_dashboard.lib import metadata_mutations as _meta_mut
from vivarium_dashboard.lib import study_crud_mutations as _study_crud_mut
from vivarium_dashboard.lib import git_status as _git_status
from vivarium_dashboard.lib import git_commit_views as _git_commit_views
from vivarium_dashboard.lib import work_mutations as _work_mutations
from vivarium_dashboard.lib import work_pr_views as _work_pr_views
from vivarium_dashboard.lib import workspaces_mutations as _workspaces_mut
from vivarium_dashboard.lib import workspaces_process_views as _workspaces_proc
from vivarium_dashboard.lib import misc_mutations as _misc_mut
from vivarium_dashboard.lib import misc_post_views as _misc_post_views
from vivarium_dashboard.lib import investigation_status
from vivarium_dashboard.lib import investigation_registry as _inv_registry
from vivarium_dashboard.lib import investigation_views as _inv_views
from vivarium_dashboard.lib import observables_views as _obs_views
from vivarium_dashboard.lib import report_views as _report_views
from vivarium_dashboard.lib import rigor_views as _rigor_views
from vivarium_dashboard.lib import saved_visualizations as _saved_viz
from vivarium_dashboard.lib import static_serving as _static_serving
from vivarium_dashboard.lib import study_page as _study_page
from vivarium_dashboard.lib import study_runs as _study_runs
from vivarium_dashboard.lib import run_unblocked_views as _run_unblocked_views
from vivarium_dashboard.lib import test_run_views as _test_run_views
from vivarium_dashboard.lib import study_spec as _study_spec
from vivarium_dashboard.lib import study_viz_views as _study_viz
from vivarium_dashboard.lib import system_info as _system_info
from vivarium_dashboard.lib import work_views as _work_views
from vivarium_dashboard.lib import workspace_deps_views as _workspace_deps
from vivarium_dashboard.lib import workspace_manifest_views as _workspace_manifest_views
from vivarium_dashboard.lib import install_views as _install_views
from vivarium_dashboard.lib import catalog_install_views as _catalog_install_views
from vivarium_dashboard.lib import catalog_uninstall_views as _catalog_uninstall_views
from vivarium_dashboard.lib.composite_resolve import resolve_composite
from vivarium_dashboard.lib.composites_query import composites_via_subprocess
from vivarium_dashboard.lib.models import (
    BibEntry,
    CatalogModule,
    CatalogPayload,
    CompositeRecord,
    CompositeResolvePayload,
    CompositeRunsList,
    CompositeRunTrajectory,
    CompositeRunState,
    CompositeRunStatus,
    CompositeState,
    CompositesPayload,
    DataSourcesPayload,
    DirtyFile,
    DirtyStatus,
    ExplorerFlux,
    ExplorerObservables,
    ExplorerProteinBreakdown,
    ExplorerRuns,
    IsetDetail,
    InputsPayload,
    NeedsAttention,
    ReportLint,
    ExplorerSeries,
    ExplorerVector,
    FrameworkMetrics,
    GithubRepo,
    GitStatus,
    InvestigationCompositeDocPayload,
    InvestigationCompositesPayload,
    InvestigationStateTree,
    JobStatusPayload,
    InvestigationHypothesesPayload,
    InvestigationSummary,
    InvestigationSummariesPayload,
    InvestigationRigor,
    InvestigationVizHtmlPayload,
    InvestigationsPayload,
    LinkageIndex,
    ObservablesPayload,
    ReferencesBibPayload,
    StudyObservableCheck,
    StudyDetail,
    RegistryPayload,
    SavedVisualizationsPayload,
    PtoolsLaunch,
    SimRow,
    SimulationsPayload,
    SourceBuilds,
    StudyBigraphPaths,
    StudyChartsPayload,
    SystemDepsCheck,
    UiConfig,
    VisualizationClassesPayload,
    VisualizationInstances,
    VisualizationStatus,
    VizClass,
    WorkspaceHome,
    WorkspacesList,
    WorkStatusActive,
    WorkStatusInactive,
    Generation,
    GenerationSummary,
    WorkCompositeDiff,
    WorkCompositeDiffEntry,
    # Batch 18: request-body models for investigation & study mutations
    SetObservablesBody,
    SetConclusionsBody,
    SetOverviewBody,
    SetStatusBody,
    SetObjectiveBody,
    NarrativeSetBody,
    ExpertInputSetBody,
    # Batch 19: request-body models for study CRUD
    StudyVariantAddBody,
    StudyVariantDeleteBody,
    StudyVariantSetParamsBody,
    StudyBaselineAddBody,
    StudyBaselineRemoveBody,
    StudyInterventionAddBody,
    StudyInterventionUpdateBody,
    StudyInterventionDeleteBody,
    StudyRunDeleteBody,
    StudyRunsClearBody,
    StudyComparisonAddBody,
    # Batch 20: request-body models for study lifecycle + feedback
    FeedbackApplyActionBody,
    StudyCreateFromRunBody,
    StudyRenameBody,
    StudySyncRunsBody,
    ProposedInputDecisionBody,
    StudySeedFollowupBody,
    # Batch 21: request-body models for investigation scaffold mutations
    InvestigationCreateRequest,
    IsetCloneBody,
    InvestigationDeleteBody,
    # Batch 22: request-body models for investigation comparison & group mutations
    InvestigationComparisonAddBody,
    InvestigationComparisonUpdateBody,
    InvestigationGroupAddBody,
    InvestigationGroupUpdateBody,
    # Batch 23: request-body models for visualization file-write mutations
    VisualizationCreateBody,
    VisualizationAddToProjectBody,
    VisualizationGenerateBody,
    # Batch 24: request-body models for visualization commit mutations
    ObservableAddBody,
    VisualizationAddBody,
    VisualizationCommitBatchBody,
    # Batch 25: request-body models for upload / import mutations
    DatasetUploadBody,
    ExpertDocUploadBody,
    ImportRegisterBody,
    # Batch 26: request-body models for reference mutations
    ReferencePdf,
    ReferenceBibtex,
    # Batch 27: request-body models for composite mutations
    InvestigationCompositeAdd,
    InvestigationCompositePerturb,
    CompositePromoteToCatalog,
    InvestigationCompositeRebuild,
    # Batch 28: request-body models for investigation composite/viz mutations
    StudyCreateFromComposite,
    InvestigationAddViz,
    InvestigationRenderViz,
    # C-state-3a: source/switch (in-process workspace re-point)
    SourceSwitchRequest,
    SourceSwitchResponse,
    # C-state-3b: source build-remote / switch-build (sms-api network routes)
    BuildRemoteRequest,
    BuildRemoteResponse,
    SwitchBuildRequest,
    # C-state-3c: remote-run submit (manager.submit pipeline job)
    RemoteRunStartRequest,
    RemoteRunStartResponse,
    # C-state-3e: GitHub device-flow auth (pass-through payload)
    AuthPayload,
    # C-state-3f: git-subprocess commit/push routes
    BranchPushRequest,
    BranchPushResponse,
    DirtyCommitAllResponse,
    # C-state-3f2: workstream-lifecycle routes
    WorkStartRequest,
    WorkStartResponse,
    WorkPushResponse,
    WorkEndResponse,
    WorkAttachReportRequest,
    WorkAttachReportResponse,
    # C-state-3f3: workstream GitHub-PR-create route
    WorkCreatePrRequest,
    WorkCreatePrResponse,
    # C-state-3f4: workstream link-branch-to-upstream route
    WorkLinkBranchRequest,
    WorkLinkBranchResponse,
    # C-state-3h1: workspace-registry routes
    WorkspacesPathRequest,
    WorkspacesOkResponse,
    WorkspaceEntry,
    # C-state-3h2: misc FS/render routes
    RenderResponse,
    # C-state-3i: visualization-accept finalize route
    VisualizationAcceptBody,
    VisualizationAcceptResponse,
    # P1: study-run / test-run POST request bodies (JSONResponse-only routes)
    StudyRunBaselineRequest,
    StudyRunVariantRequest,
    StudyTestsRunRequest,
    RunTestsRequest,
    # Misc POST request bodies
    SuggestRequest,
    StudyReportSingleRequest,
    OpenWindowRequest,
    # P2: composite-test-run (detached run launcher) POST request body
    CompositeTestRunRequest,
    # P3: investigation-run-one (ad-hoc "Duplicate run") POST request body
    InvestigationRunOneRequest,
    # P4: investigation-run (run all sims + render viz) POST request body
    InvestigationRunRequest,
    # P5: investigation-run-unblocked (enumerate + submit run job) POST request body
    InvestigationRunUnblockedRequest,
    # Scaffold: study-create (scaffold new study dir) POST request body
    StudyCreateRequest,
    # Viz authoring: visualization-preview (in-process viz render) POST request body
    VisualizationPreviewRequest,
    # Viz authoring: visualization-preview-instance (preview registered instance by name)
    VisualizationPreviewInstanceRequest,
    # Installs: system-deps-install + import-install + catalog-install/uninstall POST request bodies (JSONResponse-only)
    SystemDepsInstallRequest,
    ImportInstallRequest,
    CatalogInstallRequest,
    CatalogUninstallRequest,
)
from vivarium_dashboard.lib.catalog import build_catalog
from vivarium_dashboard.lib.registry import build_registry
from vivarium_dashboard.lib.visualization_classes import list_visualization_classes
from vivarium_dashboard.lib.simulations_index import list_simulations
from vivarium_dashboard.lib.study_charts import build_study_charts_payload

WORKSPACE_ENV = "VIVARIUM_DASHBOARD_WORKSPACE"


def get_workspace() -> Path:
    """Resolve the workspace root (overridable in tests via dependency_overrides).

    Prefers a root registered via ``active_workspace.set_workspace_root`` (the
    single source of truth shared with the stdlib server). Falls back to the
    ``VIVARIUM_DASHBOARD_WORKSPACE`` env var (default ``"."``) when none is set,
    preserving the prior env-var + dependency_overrides behavior.
    """
    root = active_workspace.get_workspace_root()
    if root is not None:
        return root
    return Path(os.environ.get(WORKSPACE_ENV, ".")).resolve()


_OPENAPI_TAGS = [
    {
        "name": "Investigations",
        "description": "Top-level investigations \u2014 the DAG of studies, detail, scaffold, comparisons, viz, runs, and metadata mutations.",
    },
    {
        "name": "Studies",
        "description": "Studies within an investigation \u2014 CRUD, lifecycle, variants, runs, and per-study visualizations.",
    },
    {
        "name": "Runs",
        "description": "Simulation runs and the workspace-wide runs index, including remote (sms-api) run submission.",
    },
    {
        "name": "Composites",
        "description": "Composite spec/generator discovery, variants, and file-backed composite runs.",
    },
    {
        "name": "Visualizations",
        "description": "Visualization authoring, generation, and registration.",
    },
    {
        "name": "Data, inputs & references",
        "description": "Data explorer, observables, BibTeX references, reports, inputs, and uploads/imports.",
    },
    {
        "name": "Workspaces & sources",
        "description": "Workspace switching, the global workspace registry, and remote build sources.",
    },
    {
        "name": "Registry & catalog",
        "description": "Process/type/emitter registry, the pbg package catalog, and venv/system installs.",
    },
    {
        "name": "Git & workstream",
        "description": "Git/branch status, commit/push writes, and workstream activity.",
    },
    {
        "name": "Downloads",
        "description": "Binary / HTML file-download routes (exports, bundles, reports, notebooks).",
    },
    {
        "name": "Rigor & jobs",
        "description": "Evidence/rigor scorecards, job-status polling, and miscellaneous workspace writes.",
    },
    {
        "name": "Auth",
        "description": "GitHub OAuth device-flow authentication.",
    },
    {
        "name": "System",
        "description": "Service health, client configuration, workspace info, and the event stream.",
    },
]


# Read-only mode --------------------------------------------------------------
# When ``VIVARIUM_DASHBOARD_READONLY`` is set, the app serves a read-only
# surface: every GET (reads/display/sync) stays, but mutating routes are
# dropped EXCEPT a small whitelist of *actions* the client legitimately
# triggers — run launches, the remote-build flow, and GitHub auth. The dropped
# authoring/local-management endpoints simply 404 (no route registered), which
# is exactly the "reader/displayer of git-committed content, remote-only
# server" model. Reversible: unset the flag to restore the full surface.
READONLY_ENV = "VIVARIUM_DASHBOARD_READONLY"

_READONLY_ALLOWED_MUTATIONS = {
    # run triggers (local or remote execution)
    "/api/investigation-run", "/api/investigation-run-one", "/api/investigation-run-unblocked",
    "/api/study-run-baseline", "/api/study-run-variant", "/api/composite-test-run",
    "/api/remote-run-start",
    # switch which committed workspace this server serves (in-process re-point)
    # + the remote-build flow (the core of remote-only)
    "/api/source/switch", "/api/source/build-remote", "/api/source/switch-build",
    # GitHub auth (needed to reach the remote / private content)
    "/api/auth/github/start", "/api/auth/github/logout",
    # benign UI telemetry
    "/api/click",
}


def _readonly_enabled() -> bool:
    return os.environ.get(READONLY_ENV, "").strip().lower() not in ("", "0", "false", "no")


def _apply_readonly_filter(app: FastAPI) -> None:
    """Drop every mutating route except the whitelisted actions (in place)."""
    kept = []
    for r in app.router.routes:
        methods = getattr(r, "methods", None)
        if not methods:                                   # Mounts / non-HTTP routes
            kept.append(r); continue
        if methods <= {"GET", "HEAD", "OPTIONS"}:         # pure read
            kept.append(r)
        elif getattr(r, "path", "") in _READONLY_ALLOWED_MUTATIONS:
            kept.append(r)
        # else: an un-whitelisted mutation route -> dropped
    app.router.routes[:] = kept


def create_app() -> FastAPI:
    app = FastAPI(
        title="vivarium-dashboard API",
        version="0.1.0",
        summary="Typed seam over the dashboard HTTP API (strangler-fig migration).",
        description=(
            "Auto-generated, typed view of the dashboard routes that have been "
            "ported from the legacy stdlib `http.server` handler to FastAPI + "
            "pydantic. This page (**Swagger UI**) and `/redoc` are generated from "
            "the same pydantic models that validate every response — so the "
            "schema can't drift from what the routes actually return. Routes are "
            "added a few at a time; the legacy server still serves the rest."
        ),
        openapi_tags=_OPENAPI_TAGS,
    )

    @app.middleware("http")
    async def _csrf_mw(request: Request, call_next):
        """Same-origin guard for the whole FastAPI POST/DELETE surface.

        Mirrors the stdlib ``server.Handler._csrf_ok`` stateless decision (shared
        via ``lib.csrf``): a state-mutating request with a cross-origin ``Origin``
        header is rejected with HTTP 403 ``{"error": "cross-origin request
        forbidden"}`` — emitted via ``JSONResponse`` (NOT ``HTTPException``, whose
        body is ``{"detail": ...}``).  GET and other safe methods are never
        blocked.  No ``Origin`` header (e.g. curl, local CLI, starlette's
        ``TestClient``) → allowed; ``VIVARIUM_DASHBOARD_DISABLE_CSRF=1`` → allowed.
        """
        if request.method in ("POST", "DELETE"):
            if not _csrf.is_request_allowed(
                request.headers.get("origin"),
                request.headers.get("host"),
                disabled=_csrf.is_disabled_via_env(os.environ),
            ):
                return JSONResponse(
                    {"error": "cross-origin request forbidden"}, status_code=403
                )
        return await call_next(request)

    @app.get("/health", tags=["System"], summary="Service liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/simulations",
        response_model=SimulationsPayload,
        tags=["Runs"],
        summary="Workspace-wide simulations index (all runs)",
    )
    def simulations(ws: Path = Depends(get_workspace)) -> SimulationsPayload:
        """Workspace-wide simulations index (mirrors the stdlib /api/simulations).

        `current` (the active branch slug) is computed by the stdlib server today
        and will move here when branch state is shared; until then it is null.
        """
        rows = [SimRow.model_validate(r) for r in list_simulations(ws)]
        return SimulationsPayload(simulations=rows, current=None)

    @app.get(
        "/api/workspace-manifest",
        tags=["System"],
        summary="One-call workspace situational-awareness snapshot for agents",
    )
    def workspace_manifest(ws: Path = Depends(get_workspace)) -> JSONResponse:
        """One-call situational awareness (mirrors the stdlib /api/workspace-manifest).

        Aggregates six best-effort sections into a single snapshot so an agent
        does not have to stitch together ten separate API calls: workspace
        identity + git state (``workspace``), composites (``composites``),
        studies (``studies``), registry summary (``registry``), dirty-tree
        health (``health``), and installed pbg-* skills (``skills``).

        Always returns HTTP 200 — each section degrades to an empty default
        rather than failing the whole manifest.  Library-backed via
        ``lib.workspace_manifest_views.workspace_manifest``.
        """
        out, status = _workspace_manifest_views.workspace_manifest(ws)
        return JSONResponse(content=out, status_code=status)

    @app.get(
        "/api/investigation-summaries",
        response_model=InvestigationSummariesPayload,
        tags=["Investigations"],
        summary="Investigation summary list for the sidebar",
    )
    def investigation_summaries(ws: Path = Depends(get_workspace)) -> InvestigationSummariesPayload:
        """Investigations summary list (mirrors the stdlib /api/investigation-summaries).

        Fully library-backed: builds the payload via
        ``lib.investigation_status.build_iset_summary`` and supplies the
        runs-presence signal from the simulations index — no dependency on the
        stdlib server module.

        The payload is wrapped as ``{"investigations": [...]}`` (NOT a bare
        array) — the client reads ``j.investigations``; a bare list renders the
        Investigations page as "No investigations declared".
        """
        run_slugs = investigation_status.study_run_slugs(ws)

        def study_has_runs(slug: str, spec: dict) -> bool:
            # Parity with the stdlib path's _count_runs_for_study(...) > 0:
            # a study has runs if the index records one, or its spec lists runs.
            return slug in run_slugs or bool((spec or {}).get("runs"))

        summaries = investigation_status.build_iset_summary(ws, study_has_runs=study_has_runs)
        return InvestigationSummariesPayload(
            investigations=[InvestigationSummary.model_validate(d) for d in summaries]
        )

    @app.get(
        "/api/investigation-registry",
        tags=["Investigations"],
        summary="Cross-worktree Investigation registry (current + peers)",
    )
    def investigation_registry(
        request: Request, ws: Path = Depends(get_workspace)
    ) -> JSONResponse:
        """Cross-worktree Investigation view (mirrors the stdlib
        /api/investigation-registry) — unblocks the Investigations page, which
        groups studies by their investigation's ``current`` entry.

        Returns ``{current, local_siblings, running_others, dormant_others}``:
        this worktree's chosen Investigation, the workspace's other
        investigations, every OTHER live dashboard's current Investigation
        (HTTP-probed from each peer's /api/investigation-summaries, cached ~5 s), and open
        investigations on other worktrees with no live dashboard.

        ``current.url`` is derived from the ~/.pbg/servers record
        (``workspace_catalog.find_running``) when available, else from this
        request's ``Host`` header — same precedence as the stdlib handler.
        Pass-through JSON (the buckets carry untyped peer/disk data).
        Library-backed via ``lib.investigation_registry`` — no stdlib
        server dependency.
        """
        this_url = _inv_registry.derive_this_url(ws, request.headers.get("host"))
        out = _inv_registry.build_investigation_registry(ws, this_url)
        return JSONResponse(content=out, status_code=200)

    @app.get(
        "/api/data-sources",
        response_model=DataSourcesPayload,
        tags=["Data, inputs & references"],
        summary="Workspace data-source bundle (from workspace.yaml)",
    )
    def data_sources(ws: Path = Depends(get_workspace)) -> DataSourcesPayload:
        """Repo-wide data-source bundle (workspace.yaml `dashboard.data_sources`
        provider), via lib.data_sources — no stdlib server dependency."""
        return DataSourcesPayload.model_validate(_data_sources.enumerate_data_sources(ws))

    @app.get(
        "/api/references-bib",
        response_model=ReferencesBibPayload,
        tags=["Data, inputs & references"],
        summary="Parsed BibTeX entries with DOI enrichment cache",
    )
    def references_bib(ws: Path = Depends(get_workspace)) -> ReferencesBibPayload:
        """Parsed `references/papers.bib` entries (+ enrichment cache). Bibtex
        fields vary, so `BibEntry` preserves unknown keys (extra='allow')."""
        from vivarium_dashboard.lib.references_fetch import enrich_entries, load_cache
        from vivarium_dashboard.lib.report import _parse_bib_entries

        try:
            entries = _parse_bib_entries(ws)
        except Exception:
            return ReferencesBibPayload(entries=[])
        try:
            entries = enrich_entries(entries, load_cache(ws))
        except Exception:
            pass  # cache failures must never break the references view
        return ReferencesBibPayload(entries=[BibEntry.model_validate(e) for e in entries])

    @app.get(
        "/api/saved-visualizations",
        response_model=SavedVisualizationsPayload,
        tags=["Studies"],
        summary="Saved 3D packs, report cards, and PTools TSVs",
    )
    def saved_visualizations(ws: Path = Depends(get_workspace)) -> SavedVisualizationsPayload:
        """Saved interactive visualizations (3D packs, report cards, PTools TSVs),
        via lib.saved_visualizations — no stdlib server dependency."""
        return SavedVisualizationsPayload.model_validate(
            _saved_viz.build_saved_visualizations(ws))

    @app.get(
        "/api/study-charts/{slug}",
        response_model=StudyChartsPayload,
        tags=["Studies"],
        summary="Per-study charts (live SVG + static images)",
    )
    def study_charts(slug: str, ws: Path = Depends(get_workspace)) -> StudyChartsPayload:
        """Per-study charts (mirrors the stdlib /api/study-charts/<slug>).

        Library-backed via ``lib.study_charts.build_study_charts_payload`` — the
        single implementation the stdlib handler now forwards to. Charts are
        polymorphic: ``live`` charts carry inline SVG, ``static`` / ``declared``
        charts carry an image data-URI plus a freshness badge (see ChartPayload).
        """
        return StudyChartsPayload.model_validate(build_study_charts_payload(ws, slug))

    @app.get(
        "/api/visualization-classes",
        response_model=VisualizationClassesPayload,
        tags=["Studies"],
        summary="Registered Visualization and Analysis classes",
    )
    def visualization_classes(ws: Path = Depends(get_workspace)) -> VisualizationClassesPayload:
        """List registered Visualization / Analysis classes for this workspace.

        Mirrors ``GET /api/visualization-classes`` from the stdlib server.
        Returns all Visualization subclasses found in the workspace's core
        registry plus standard pbg-superpowers classes and (when installed)
        v2ecoli Analysis classes.  Tolerates missing packages / build_core
        failures — returns an empty ``classes`` list rather than 500.

        Library-backed via ``lib.visualization_classes.list_visualization_classes``
        — the single implementation the stdlib ``_visualization_classes_data``
        now forwards to.
        """
        result = list_visualization_classes(ws)
        return VisualizationClassesPayload(
            classes=[VizClass.model_validate(c) for c in result.get("classes", [])]
        )

    # -----------------------------------------------------------------------
    # Batch 11: study-bigraph-paths, visualization-status/instances, ptools-launch
    # -----------------------------------------------------------------------

    @app.get(
        "/api/study-bigraph-paths",
        response_model=StudyBigraphPaths,
        tags=["Studies"],
        summary="Bigraph node paths for a study's baseline composite",
    )
    def study_bigraph_paths(
        study: Optional[str] = None,
        baseline: Optional[str] = None,
        max_depth: str = "8",
        ws: Path = Depends(get_workspace),
    ) -> Union[StudyBigraphPaths, JSONResponse]:
        """Bigraph node paths extracted from a study's serialized composite state.

        Mirrors ``GET /api/study-bigraph-paths?study=<slug>[&baseline=<name>][&max_depth=<n>]``
        from the stdlib server.

        Status codes:
          - 400  missing ``?study=`` / study has no baseline entries
          - 404  no study.yaml or spec.yaml / baseline not found / no serialized state
          - 500  spec parse failure
          - 200  ``{composite, source_file, max_depth, node_count, nodes:[...]}``

        Library-backed via ``lib.study_viz_views.build_study_bigraph_paths``.
        """
        try:
            depth = int(max_depth)
        except (ValueError, TypeError):
            depth = 8
        body, status = _study_viz.build_study_bigraph_paths(
            ws, study or "", baseline_name=baseline or "", max_depth=depth,
        )
        if status == 200:
            return StudyBigraphPaths.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    @app.get(
        "/api/visualization-status",
        response_model=VisualizationStatus,
        tags=["Studies"],
        summary="Lifecycle status for a named visualization",
    )
    def visualization_status(
        name: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[VisualizationStatus, JSONResponse]:
        """Visualization lifecycle status for a named viz.

        Mirrors ``GET /api/visualization-status?name=<name>`` from the stdlib server.

        Status codes:
          - 400  missing ``?name=``
          - 200  ``{status, name, has_request, has_response, has_staged, has_committed}``
                 status ∈ ``described | requested | created | added | committed | missing``

        Library-backed via ``lib.study_viz_views.build_visualization_status``.
        """
        body, status = _study_viz.build_visualization_status(ws, name or "")
        if status == 200:
            return VisualizationStatus.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    @app.get(
        "/api/visualization-instances",
        response_model=VisualizationInstances,
        tags=["Studies"],
        summary="Class-backed visualization instances from workspace.yaml",
    )
    def visualization_instances(
        ws: Path = Depends(get_workspace),
    ) -> VisualizationInstances:
        """Class-backed visualization instances configured in workspace.yaml.

        Mirrors ``GET /api/visualization-instances`` from the stdlib server.
        Always returns HTTP 200 — errors degrade to ``{instances: []}``.

        Library-backed via ``lib.study_viz_views.build_visualization_instances``.
        """
        return VisualizationInstances.model_validate(
            _study_viz.build_visualization_instances(ws)
        )

    @app.get(
        "/api/ptools-launch/{study}",
        response_model=PtoolsLaunch,
        tags=["Studies"],
        summary="Pathway Tools Omics Viewer launch URL for a study",
    )
    def ptools_launch(
        study: str,
        run: Optional[str] = None,
        analysis: Optional[str] = None,
        request: Request = None,  # type: ignore[assignment]
        ws: Path = Depends(get_workspace),
    ) -> Union[PtoolsLaunch, JSONResponse]:
        """Pathway Tools Omics Viewer launch URL for a study.

        Mirrors ``GET /api/ptools-launch/<study>?run=<run_id>&analysis=<name>``
        from the stdlib server.  The slug is validated before delegation
        (identical to the dispatcher's check).

        Status codes:
          - 400  ``ptools_server_url not configured`` / invalid slug
          - 404  study not found / no ptools TSVs found
          - 200  ``{url, tsv_url, available}``

        Library-backed via ``lib.study_viz_views.build_ptools_launch``.
        """
        if not _study_spec.SLUG_RE.match(study):
            return JSONResponse(status_code=400, content={"error": "invalid study name"})
        # Resolve public_base from the Host header; workspace.yaml config
        # (ui.dashboard_public_base_url) takes priority inside the lib builder.
        host = (request.headers.get("host", "localhost") if request else "localhost")
        public_base = f"http://{host}"
        body, status = _study_viz.build_ptools_launch(
            ws, study, run=run, analysis=analysis, public_base=public_base,
        )
        if status == 200:
            return PtoolsLaunch.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    @app.get(
        "/api/registry",
        response_model=RegistryPayload,
        tags=["Registry & catalog"],
        summary="Process/type/emitter registry for this workspace",
    )
    def registry(ws: Path = Depends(get_workspace)) -> RegistryPayload:
        """Process/type registry for this workspace.

        Mirrors ``GET /api/registry`` from the stdlib server.  Runs
        ``build_core()`` in a subprocess to discover registered processes,
        steps, emitters and visualization classes without polluting the
        server's import state.  The response is cached for 30 s.

        Library-backed via ``lib.registry.build_registry`` — the single
        implementation the stdlib ``_get_registry_data`` now forwards to.
        """
        return RegistryPayload.model_validate(build_registry(ws))

    @app.get(
        "/api/composites",
        response_model=CompositesPayload,
        tags=["Composites"],
        summary="Discoverable composites (specs + generators)",
    )
    def composites(ws: Path = Depends(get_workspace)) -> CompositesPayload:
        """Composite spec / generator index for this workspace.

        Mirrors ``GET /api/composites`` from the stdlib server.  Discovery runs
        in a fresh Python subprocess so that stale ``sys.modules`` in the
        long-running server process cannot hide ``@composite_generator``-decorated
        entries.

        On subprocess failure (timeout / import error / parse error) the route
        returns ``{"composites": [], "error": "composite discovery unavailable"}``
        rather than a 500 — keeping the UI operational even when the workspace
        package can't be imported.

        Library-backed via ``lib.composites_query.composites_via_subprocess``.
        """
        data = composites_via_subprocess(ws)
        if data is None:
            return CompositesPayload(
                composites=[],
                error="composite discovery unavailable",
            )
        raw_composites = data.get("composites") or []
        return CompositesPayload(
            composites=[CompositeRecord.model_validate(c) for c in raw_composites],
            workspace_package=data.get("workspace_package"),
            error=data.get("error"),
        )

    @app.get(
        "/api/composite-resolve",
        response_model=Optional[CompositeResolvePayload],
        tags=["Composites"],
        summary="Resolve a single composite spec or generator by ID",
    )
    def composite_resolve(
        id: str,
        overrides: str = "{}",
        ws: Path = Depends(get_workspace),
    ) -> Optional[CompositeResolvePayload]:
        """Resolve a single composite spec or generator by ID.

        Mirrors ``GET /api/composite-resolve?id=<spec_id>&overrides=<json>`` from
        the stdlib server — the real client (the Composite Explorer) sends the
        spec under ``id`` (not ``ref``) plus an ``overrides`` JSON blob.  Returns
        the composite payload when found, or ``null`` (200 with null body) when
        ``id`` doesn't match any spec or generator.

        Library-backed via ``lib.composite_resolve.resolve_composite`` — the
        single implementation the stdlib ``_composite_resolve_data`` now forwards
        to.  (``svg`` is ``None`` from the seam; the interactive loom viewer
        renders the wiring from ``state``.)
        """
        try:
            ov = json.loads(overrides) if overrides else {}
        except (json.JSONDecodeError, TypeError):
            ov = {}
        if not isinstance(ov, dict):
            ov = {}
        result = resolve_composite(ws, id, ov)
        if result is None:
            # Miss → the stdlib's honest-degrade shape (404 with ``unresolved``),
            # NOT a bare 200 ``null`` (which makes the explorer crash on
            # ``data.unresolved``). The Composite Explorer keys on ``unresolved``.
            return JSONResponse(
                status_code=404,
                content={
                    "error": (f"composite not found: {id} — not a registered "
                              "composite (this study may not declare a real composite)"),
                    "unresolved": True,
                    "ref": id,
                },
            )
        return CompositeResolvePayload.model_validate(result)

    def _composite_state_response(
        ref: str, fresh: Optional[str], ws: Path
    ) -> Union[CompositeState, JSONResponse]:
        """Shared worker for both composite-state URL forms.

        Mirrors the legacy ``_get_composite_state``: no ref → 400; else build via
        the lib seam (TTL cache, subprocess generator build, static fallback,
        spec/path resolution) and carry the exact legacy status + body.
        """
        ref = (ref or "").strip()
        if not ref:
            return JSONResponse(status_code=400, content={"error": "ref required"})
        body, status = _composite_state_views.build_composite_state(
            ws, ref, fresh=fresh in ("1", "true", "yes")
        )
        if status == 200:
            return CompositeState.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    @app.get(
        "/api/composite-state",
        response_model=CompositeState,
        tags=["Composites"],
        summary="Built/parsed composite-state document for the Explorer",
    )
    def composite_state(
        ref: Optional[str] = None,
        fresh: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[CompositeState, JSONResponse]:
        """Composite-state document for a dotted spec ID or workspace-relative path.

        Mirrors ``GET /api/composite-state?ref=<id-or-path>&fresh=<bool>`` from
        the stdlib server.  For a ``@composite_generator`` entry it runs
        ``build_generator`` in a fresh subprocess (its own main thread) and
        returns the summarized document; otherwise it parses the resolved spec
        file.  Success: ``{state, kind: "generator"|"static-fallback"|"spec",
        ...}`` (plus ``cached: true`` on a TTL cache hit; ``?fresh=1|true|yes``
        bypasses the cache).

        Error paths replicate the legacy handler's exact status codes + bodies
        (carried via :class:`JSONResponse`): HTTP 400 (no ref, or generator
        build failed with no static fallback); HTTP 404 (nothing resolves —
        ``{error, unresolved: true, ref}``); HTTP 500 (spec parse failed).

        Library-backed via ``lib.composite_state_views.build_composite_state``.
        """
        return _composite_state_response(ref or "", fresh, ws)

    @app.get(
        "/api/composite-state/{ref:path}",
        response_model=CompositeState,
        tags=["Composites"],
        summary="Composite-state document (loom static ?stateUrl= form)",
    )
    def composite_state_path(
        ref: str,
        fresh: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[CompositeState, JSONResponse]:
        """Path form of ``/api/composite-state`` for the loom's ``?stateUrl=`` mode.

        Mirrors ``GET /api/composite-state/<ref>.json`` — the read-only loom's
        static-snapshot form (``{ref:path}`` so dotted/aliased refs match).  A
        trailing ``.json`` is stripped, then resolution is identical to the
        query form.
        """
        if ref.endswith(".json"):
            ref = ref[: -len(".json")]
        return _composite_state_response(ref, fresh, ws)

    @app.get(
        "/api/investigations",
        response_model=InvestigationsPayload,
        tags=["Investigations"],
        summary="Per-study investigations index (all rows)",
    )
    def investigations(ws: Path = Depends(get_workspace)) -> InvestigationsPayload:
        """Investigations index (mirrors the stdlib /api/investigations).

        Returns the per-study index used by the Investigations tab. Each row
        is either a full investigation-row dict (~26 keys) or a minimal
        ``{name, status: "invalid", error}`` entry for a malformed spec.yaml.

        Library-backed via ``lib.investigations_index.build_investigations`` --
        the single implementation the stdlib ``_investigations_data`` now
        forwards to.
        """
        from vivarium_dashboard.lib.investigations_index import build_investigations

        return InvestigationsPayload.model_validate(build_investigations(ws))

    @app.get(
        "/api/catalog",
        response_model=CatalogPayload,
        tags=["Registry & catalog"],
        summary="Package catalog with per-workspace install state",
    )
    def catalog(ws: Path = Depends(get_workspace)) -> CatalogPayload:
        """Package catalog for this workspace (mirrors the stdlib /api/catalog).

        Returns the pbg module catalog annotated with per-workspace install
        state (imports / pyproject / venv presence).  Best-effort: venv/
        pyproject probes swallow errors — the route never returns 500.

        Library-backed via ``lib.catalog.build_catalog`` — the single
        implementation the stdlib ``_catalog_data`` now forwards to.
        """
        return CatalogPayload.model_validate(build_catalog(ws))

    # -----------------------------------------------------------------------
    # Git & branches routes
    # -----------------------------------------------------------------------

    @app.get(
        "/api/git-status",
        response_model=GitStatus,
        tags=["Git & workstream"],
        summary="Live git sync state for the workspace",
    )
    def git_status_route(ws: Path = Depends(get_workspace)) -> GitStatus:
        """Live sync state for the workspace's git remote.

        Returns branch, push state (pushed/ahead/behind/diverged/no_origin),
        commit counts, PR linkage, dirty-file count, and GitHub availability.

        Library-backed via ``lib.git_status.build_git_status`` — always 200,
        degrades gracefully when origin is absent or git is not available.
        """
        return GitStatus.model_validate(_git_status.build_git_status(ws))

    @app.get(
        "/api/work-status",
        response_model=Union[WorkStatusInactive, WorkStatusActive],
        tags=["Git & workstream"],
        summary="Active workstream status (branch + ahead/behind counts)",
    )
    def work_status_route(
        ws: Path = Depends(get_workspace),
    ) -> Union[WorkStatusInactive, WorkStatusActive]:
        """Active workstream status.

        Returns exactly ``{active: false}`` (one key) when no workstream is
        running, or the full 14-key branch/commit-ahead/behind/staleness/push
        payload otherwise.  Modelled as a discriminated union (on ``active``)
        so the inactive path stays byte-identical to the legacy handler's
        single-key body while the active path keeps every key (including the
        nullable ``pr_number`` / ``pr_url``), rather than a single model whose
        14 null defaults would leak into the inactive response.

        Library-backed via ``lib.git_status.build_work_status``.
        """
        payload = _git_status.build_work_status(ws)
        if payload.get("active"):
            return WorkStatusActive.model_validate(payload)
        return WorkStatusInactive.model_validate(payload)

    @app.get(
        "/api/dirty-status",
        response_model=DirtyStatus,
        tags=["Git & workstream"],
        summary="Filtered list of uncommitted files in the workspace",
    )
    def dirty_status_route(
        ws: Path = Depends(get_workspace),
    ) -> Union[DirtyStatus, JSONResponse]:
        """Filtered list of uncommitted files (excludes reports/, out/, .pbg/).

        HTTP 500 when ``git status`` itself fails (not a git repo, corrupt
        index, etc.). The 500 body is ``{"error": "git status failed: ..."}`` —
        byte-identical to the legacy handler.

        Library-backed via ``lib.git_status.build_dirty_status``.
        """
        try:
            payload = _git_status.build_dirty_status(ws)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return JSONResponse(
                status_code=500,
                content={"error": f"git status failed: {stderr[:200]}"},
            )
        files = [DirtyFile.model_validate(f) for f in payload["files"]]
        return DirtyStatus(count=payload["count"], files=files)

    @app.get(
        "/api/generation",
        response_model=Generation,
        tags=["Git & workstream"],
        summary="Workspace coordinated-generation provenance banner",
    )
    def generation_route(
        ws: Path = Depends(get_workspace),
    ) -> Generation:
        """Current coordinated generation for the workspace.

        Returns ``{generation: {generation_id, git_sha, param_set_hash,
        created_at, label, n_runs}}`` or ``{generation: null}`` when no
        generation is active.  Always HTTP 200 (best-effort; any error →
        ``{generation: null}`` rather than 500).

        Library-backed via ``lib.work_views.build_generation``.
        """
        return Generation.model_validate(_work_views.build_generation(ws))

    @app.get(
        "/api/work-composite-diff",
        response_model=WorkCompositeDiff,
        tags=["Git & workstream"],
        summary="Model-code changes on the active branch vs its merge-base",
    )
    def work_composite_diff_route(
        ws: Path = Depends(get_workspace),
    ) -> WorkCompositeDiff:
        """Files changed on the active branch that look like model code.

        Returns ``{base, branch, changes: [{path, lines_added, lines_removed,
        category}, ...]}`` sorted by largest diff, capped at 500 entries.
        Categories: ``composite``, ``process``, ``step``, ``library helper``,
        ``type definition``.

        Always HTTP 200 — merge-base / diff failures carry an ``error`` key
        with an empty ``changes`` list rather than a 500.

        Library-backed via ``lib.work_views.build_work_composite_diff``.
        """
        return WorkCompositeDiff.model_validate(
            _work_views.build_work_composite_diff(ws)
        )

    # -----------------------------------------------------------------------
    # Investigations detail routes
    # -----------------------------------------------------------------------

    @app.get(
        "/api/investigation-viz-html",
        response_model=InvestigationVizHtmlPayload,
        tags=["Investigations"],
        summary="Viz HTML files for one investigation run",
    )
    def investigation_viz_html_route(
        investigation: Optional[str] = None,
        run_id: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[InvestigationVizHtmlPayload, JSONResponse]:
        """List persisted viz HTML files for one run of an investigation.

        Returns ``{viz_files: [{name, html_path}]}``.  ``html_path`` is the
        workspace-relative path the static-file handler serves.  Returns an
        empty ``viz_files`` list when the viz directory does not exist yet
        (run has no rendered files).

        HTTP 400 when ``?investigation=`` or ``?run_id=`` is missing.  The 400
        body is ``{error, viz_files: []}`` — byte-identical to the legacy
        ``_get_investigation_viz_html``.

        Library-backed via ``lib.investigation_views.build_investigation_viz_html``.
        """
        try:
            body = _inv_views.build_investigation_viz_html(
                ws, investigation or "", run_id or ""
            )
        except _inv_views.InvViewError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body)
        return InvestigationVizHtmlPayload.model_validate(body)

    @app.get(
        "/api/investigation-composites",
        response_model=InvestigationCompositesPayload,
        tags=["Investigations"],
        summary="Composite baseline entries for an investigation",
    )
    def investigation_composites_route(
        investigation: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[InvestigationCompositesPayload, JSONResponse]:
        """List the composite baseline entries from an investigation's spec.

        Returns ``{composites: [{name, source, params}]}``, projected from
        the v3 ``baseline[]`` list in the investigation's ``study.yaml`` /
        ``spec.yaml``.

        HTTP 400 when ``?investigation=`` is missing or the spec is malformed;
        HTTP 404 when no spec file exists for the given investigation name.
        Error bodies are ``{"error": <msg>}`` — byte-identical to the legacy
        ``_get_investigation_composites``.

        Library-backed via
        ``lib.investigation_views.build_investigation_composites``.
        """
        try:
            body = _inv_views.build_investigation_composites(
                ws, investigation or ""
            )
        except _inv_views.InvViewError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body)
        return InvestigationCompositesPayload.model_validate(body)

    # /api/study-rigor and /api/investigation-rigor are ported below (Batch 3),
    # on top of the run-merging loader extracted to lib.study_spec.

    @app.get(
        "/api/investigation-composite-doc",
        response_model=InvestigationCompositeDocPayload,
        tags=["Investigations"],
        summary="Parsed composite YAML document for the bigraph-loom iframe",
    )
    def investigation_composite_doc_route(
        investigation: Optional[str] = None,
        composite: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[InvestigationCompositeDocPayload, JSONResponse]:
        """Return a composite YAML document as JSON for the bigraph-loom iframe.

        The iframe can't parse YAML in-browser; this endpoint converts
        ``investigations/<inv>/composites/<composite>.yaml`` (or
        ``studies/<inv>/composites/<composite>.yaml``) to ``{state: <parsed>}``.

        HTTP 400 when ``?investigation=`` or ``?composite=`` is missing;
        HTTP 404 when the composite YAML file does not exist; HTTP 500 on YAML
        parse failure.  Error bodies are ``{"error": <msg>}`` — byte-identical
        to the legacy ``_get_investigation_composite_doc``.

        Library-backed via
        ``lib.investigation_views.build_investigation_composite_doc``.
        """
        try:
            body = _inv_views.build_investigation_composite_doc(
                ws, investigation or "", composite or ""
            )
        except _inv_views.InvViewError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body)
        return InvestigationCompositeDocPayload.model_validate(body)

    @app.get(
        "/api/investigation-state-tree",
        response_model=InvestigationStateTree,
        tags=["Investigations"],
        summary="Flattened state tree of an investigation's composite document",
    )
    def investigation_state_tree_route(
        investigation: Optional[str] = None,
        composite: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[InvestigationStateTree, JSONResponse]:
        """Flatten a composite YAML document into a list of state-tree nodes.

        Reads ``studies/<inv>/composites/<composite>.yaml`` and returns
        ``{nodes: [...]}`` (each node: ``path`` + ``kind`` plus store/process
        fields) for the bigraph state-tree picker.

        HTTP 400 when ``?investigation=`` or ``?composite=`` is missing; HTTP 404
        when the composite YAML file does not exist (body carries the resolved
        path); HTTP 500 on YAML parse failure.  Error bodies are
        ``{"error": <msg>}`` — byte-identical to the legacy
        ``_get_investigation_state_tree``.

        Library-backed via
        ``lib.investigation_views.build_investigation_state_tree``.
        """
        try:
            body = _inv_views.build_investigation_state_tree(
                ws, (investigation or "").strip(), (composite or "").strip()
            )
        except _inv_views.InvViewError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body)
        return InvestigationStateTree.model_validate(body)

    @app.get(
        "/api/investigation-hypotheses",
        response_model=InvestigationHypothesesPayload,
        tags=["Investigations"],
        summary="Competing hypotheses with computed support log",
    )
    def investigation_hypotheses_route(
        investigation: Optional[str] = None,
        inv: Optional[str] = None,
        name: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> InvestigationHypothesesPayload:
        """Competing hypotheses for an investigation, with support-log enrichment.

        Returns ``{hypotheses: [...], investigation: name}``.  Each hypothesis
        carries a computed ``support_log`` (via
        ``pbg_superpowers.hypotheses.rollup_support`` / ``score_support``).
        Always HTTP 200 — missing investigations return an empty list rather
        than 404; import / compute failures degrade to the authored hypotheses.

        The investigation slug accepts the legacy query-param aliases
        ``?investigation=`` / ``?inv=`` / ``?name=`` (same precedence as the
        stdlib dispatcher at ``server.py``).

        Library-backed via
        ``lib.investigation_views.build_investigation_hypotheses``.
        """
        slug = (investigation or inv or name or "").strip()
        body = _inv_views.build_investigation_hypotheses(ws, slug)
        return InvestigationHypothesesPayload.model_validate(body)

    # -----------------------------------------------------------------------
    # Rigor routes
    # -----------------------------------------------------------------------

    @app.get(
        "/api/investigation-rigor",
        response_model=InvestigationRigor,
        tags=["Rigor & jobs"],
        summary="Investigation-level rigor roll-up across member studies",
    )
    def investigation_rigor_route(
        investigation: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[InvestigationRigor, JSONResponse]:
        """Investigation rigor roll-up (mirrors the stdlib /api/investigation-rigor).

        Aggregates per-study rigor across the investigation's member studies
        (each loaded run-merged) plus investigation-level dimensions
        (adversarial coverage, traceable methodology).

        HTTP 400 when ``?investigation=`` is missing; HTTP 404 when the
        investigation.yaml does not exist. Error bodies are ``{"error": <msg>}``.
        An unreadable investigation.yaml or a rigor-computation failure degrade
        to a 200 body carrying ``error`` — byte-identical to the legacy
        ``_get_investigation_rigor``.

        Library-backed via ``lib.rigor_views.build_investigation_rigor``.
        """
        try:
            body = _rigor_views.build_investigation_rigor(ws, investigation)
        except _rigor_views.RigorViewError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body)
        return InvestigationRigor.model_validate(body)

    # -----------------------------------------------------------------------
    # Studies detail routes
    # -----------------------------------------------------------------------

    @app.get(
        "/api/study/{slug}",
        response_model=StudyDetail,
        tags=["Studies"],
        summary="Full run-merged study detail spec",
    )
    def study_detail_route(
        slug: str,
        ws: Path = Depends(get_workspace),
    ) -> Union[StudyDetail, JSONResponse]:
        """Full run-merged study detail spec (mirrors the stdlib GET /api/study/<slug>).

        Returns the complete per-study payload built by
        ``lib.study_spec.load_study_detail_spec``: the spec from study.yaml /
        spec.yaml with runs.db rows merged in, ``simulation_set`` reconciled,
        param-enforcement computed, expert feedback attached, and all
        lifecycle-derived keys (derived_status, computed_gate_verdict, …).

        Error paths replicate the legacy builder's exact HTTP status codes and
        body shapes (``{"error": ...}`` with an optional ``"traceback"`` field):

        - HTTP 400 ``{"error": "invalid slug"}`` — path segment fails slug RE.
        - HTTP 500 ``{"error": "failed to build study '<slug>': <Type>: <msg>",
          "traceback": "..."}`` — loader raised an exception.
        - HTTP 404 ``{"error": "study not found: <slug>"}`` — no spec file.
        - HTTP 500 ``{"error": "failed to serialize study '<slug>': ...",
          "traceback": "..."}`` — JSON serialization failed.
        - HTTP 200 — the full study spec dict (validated through StudyDetail).

        ``StudyDetail`` is a pure pass-through (``extra="allow"``, no declared
        fields) so no keys are stripped or injected.

        Note: the stdlib also serves ``/api/investigation/<slug>`` as an alias
        of this route via a do_GET path-rewrite map. That alias is a dispatch-
        layer concern handled at the flip; this batch ports only
        ``/api/study/{slug}``.
        """
        import traceback as _tb

        if not _study_spec.SLUG_RE.match(slug):
            return JSONResponse(status_code=400, content={"error": "invalid slug"})
        try:
            spec = _study_spec.load_study_detail_spec(ws, slug)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=500,
                content={
                    "error": f"failed to build study {slug!r}: {type(exc).__name__}: {exc}",
                    "traceback": _tb.format_exc(),
                },
            )
        if spec is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"study not found: {slug}"},
            )
        try:
            return StudyDetail.model_validate(spec)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=500,
                content={
                    "error": f"failed to serialize study {slug!r}: {type(exc).__name__}: {exc}",
                    "traceback": _tb.format_exc(),
                },
            )

    # -----------------------------------------------------------------------
    # Data explorer routes  (always HTTP 200 — error carried in body)
    # -----------------------------------------------------------------------

    @app.get(
        "/api/explorer/runs",
        response_model=ExplorerRuns,
        tags=["Data, inputs & references"],
        summary="Run-picker list for the Data Explorer card",
    )
    def explorer_runs(ws: Path = Depends(get_workspace)) -> ExplorerRuns:
        """Run-picker list for the Analyses Data Explorer.

        Returns all SQLite, zarr, and parquet runs that have emitted history,
        ordered so that parquet runs appear first.  Always HTTP 200 — on error
        the body carries ``{"error": <msg>, "runs": []}``.

        Library-backed via ``lib.explorer_data.list_runs``.
        """
        try:
            result = _explorer_data.list_runs(ws)
            return ExplorerRuns.model_validate({"runs": result})
        except Exception as exc:  # noqa: BLE001
            return ExplorerRuns.model_validate({"error": str(exc), "runs": []})

    @app.get(
        "/api/explorer/observables",
        response_model=ExplorerObservables,
        tags=["Data, inputs & references"],
        summary="Observable discovery for one run store",
    )
    def explorer_observables(
        db: Optional[str] = None,
        run: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> ExplorerObservables:
        """Discover observable paths (scalars, vectors, bulk molecules) in a run.

        ``?db=<path>`` selects the run store (SQLite, zarr, or parquet path).
        ``?run=<id>`` narrows to a specific simulation inside a multi-run SQLite
        db (ignored for zarr/parquet stores).

        Missing ``?db=`` returns ``{"error": "missing db", "categories": {}}``
        at HTTP 200 — matching the legacy handler exactly.

        Library-backed via ``lib.explorer_data.list_observables``.
        """
        if not db:
            return ExplorerObservables.model_validate(
                {"error": "missing db", "categories": {}}
            )
        try:
            result = _explorer_data.list_observables(db, run, workspace=ws)
            return ExplorerObservables.model_validate(result)
        except Exception as exc:  # noqa: BLE001
            return ExplorerObservables.model_validate(
                {"error": str(exc), "categories": {}}
            )

    @app.get(
        "/api/explorer/series",
        response_model=ExplorerSeries,
        tags=["Data, inputs & references"],
        summary="Aligned time-series for one or more observables",
    )
    def explorer_series(
        db: Optional[str] = None,
        paths: Optional[str] = None,
        subsample: str = "400",
        run: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> ExplorerSeries:
        """Aligned time-series values for one or more observable paths.

        ``?db=<path>`` selects the run store.  ``?paths=a,b#2,c`` is a
        comma-separated list of observable paths, each optionally followed by
        ``#<int>`` to select a vector index.  ``?subsample=N`` (default 400)
        limits the number of time-steps returned; non-integer values fall back
        to 400.  ``?run=<id>`` selects a specific simulation in a multi-run db.

        Missing ``?db=`` returns ``{"error": "missing db", "time": [], "series":
        {}}`` at HTTP 200.

        Library-backed via ``lib.explorer_data.get_series``.
        """
        if not db:
            return ExplorerSeries.model_validate(
                {"error": "missing db", "time": [], "series": {}}
            )
        # Replicate legacy paths parsing: comma-split, strip blanks, #index.
        specs = []
        for tok in (paths or "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            if "#" in tok:
                p, _, i = tok.partition("#")
                specs.append((p, int(i) if i.isdigit() else None))
            else:
                specs.append((tok, None))
        # Replicate legacy int-parse-with-fallback for subsample.
        try:
            sub = int(subsample)
        except ValueError:
            sub = 400
        try:
            result = _explorer_data.get_series(db, specs, sub, run, workspace=ws)
            return ExplorerSeries.model_validate(result)
        except Exception as exc:  # noqa: BLE001
            return ExplorerSeries.model_validate(
                {"error": str(exc), "time": [], "series": {}}
            )

    @app.get(
        "/api/explorer/flux",
        response_model=ExplorerFlux,
        tags=["Data, inputs & references"],
        summary="Flux map snapshot at one time-step",
    )
    def explorer_flux(
        db: Optional[str] = None,
        step: str = "0",
        run: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> ExplorerFlux:
        """FBA flux map for one time-step, keyed by BiGG reaction ID.

        ``?db=<path>`` selects the run store.  ``?step=<int>`` (default 0)
        selects the emit step; non-integer values fall back to 0.
        ``?run=<id>`` selects a specific simulation in a multi-run db.

        Missing ``?db=`` returns ``{"error": "missing db", "fluxes": {}}`` at
        HTTP 200.

        Library-backed via ``lib.explorer_data.get_flux_auto``.
        """
        if not db:
            return ExplorerFlux.model_validate({"error": "missing db", "fluxes": {}})
        try:
            step_int = int(step)
        except ValueError:
            step_int = 0
        try:
            _, id_map = _explorer_data.load_flux_assets()
            result = _explorer_data.get_flux_auto(
                db, step_int, id_map, run, workspace=ws
            )
            return ExplorerFlux.model_validate(result)
        except Exception as exc:  # noqa: BLE001
            return ExplorerFlux.model_validate({"error": str(exc), "fluxes": {}})

    @app.get(
        "/api/explorer/vector",
        response_model=ExplorerVector,
        tags=["Data, inputs & references"],
        summary="Per-entity vector snapshot at one time-step",
    )
    def explorer_vector(
        db: Optional[str] = None,
        path: Optional[str] = None,
        step: str = "0",
        run: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> ExplorerVector:
        """Per-entity (ids, values) snapshot of a vector observable.

        ``?db=<path>`` and ``?path=<observable>`` are both required.
        ``?step=<int>`` (default 0) selects the emit step; non-integer falls
        back to 0.  ``?run=<id>`` selects a specific simulation.

        Missing ``?db=`` or ``?path=`` returns
        ``{"error": "missing db/path", "ids": [], "values": [], "step": 0,
        "time": null}`` at HTTP 200 — byte-identical to the legacy handler.

        Library-backed via ``lib.explorer_data.get_vector``.
        """
        step_int = 0
        if not db or not path:
            return ExplorerVector.model_validate(
                {"error": "missing db/path", "ids": [], "values": [],
                 "step": 0, "time": None}
            )
        try:
            step_int = int(step)
        except ValueError:
            step_int = 0
        try:
            result = _explorer_data.get_vector(db, path, step_int, run, ws)
            return ExplorerVector.model_validate(result)
        except Exception as exc:  # noqa: BLE001
            return ExplorerVector.model_validate(
                {"error": str(exc), "ids": [], "values": [],
                 "step": step_int, "time": None}
            )

    @app.get(
        "/api/explorer/protein-breakdown",
        response_model=ExplorerProteinBreakdown,
        tags=["Data, inputs & references"],
        summary="Protein mass by functional category at one time-step",
    )
    def explorer_protein_breakdown(
        db: Optional[str] = None,
        path: Optional[str] = None,
        step: str = "0",
        run: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> ExplorerProteinBreakdown:
        """Protein mass grouped by functional category (count × MW per category).

        ``?db=<path>`` and ``?path=<monomer-counts observable>`` are both
        required.  ``?step=<int>`` (default 0) selects the emit step;
        non-integer falls back to 0.  ``?run=<id>`` selects a specific
        simulation.

        Missing ``?db=`` or ``?path=`` returns
        ``{"error": "missing db/path", "breakdown": {}, "step": 0,
        "time": null}`` at HTTP 200 — byte-identical to the legacy handler.

        Library-backed via ``lib.explorer_data.get_protein_breakdown``.
        """
        step_int = 0
        if not db or not path:
            return ExplorerProteinBreakdown.model_validate(
                {"error": "missing db/path", "breakdown": {}, "step": 0, "time": None}
            )
        try:
            step_int = int(step)
        except ValueError:
            step_int = 0
        try:
            result = _explorer_data.get_protein_breakdown(
                db, path, step_int, run, ws
            )
            return ExplorerProteinBreakdown.model_validate(result)
        except Exception as exc:  # noqa: BLE001
            return ExplorerProteinBreakdown.model_validate(
                {"error": str(exc), "breakdown": {}, "step": step_int, "time": None}
            )

    # -----------------------------------------------------------------------
    # Reports & inputs routes
    # -----------------------------------------------------------------------

    @app.get(
        "/api/report-lint",
        response_model=ReportLint,
        tags=["Data, inputs & references"],
        summary="Per-study report-readiness linter findings",
    )
    def report_lint(ws: Path = Depends(get_workspace)) -> ReportLint:
        """Run the deterministic workspace report-linter and return its findings.

        Runs ``pbg_superpowers.report_linter.lint_workspace_report`` over the
        workspace and returns ``{findings: [{study, check, severity, message,
        field_path}]}``, in the linter's stable error→warning→info order.

        Always HTTP 200 — degrades to ``{findings: []}`` when the linter is
        unavailable (older pbg_superpowers) or the workspace cannot be scanned.

        Library-backed via ``lib.report_views.build_report_lint``.
        """
        body, _ = _report_views.build_report_lint(ws)
        return ReportLint.model_validate(body)

    @app.get(
        "/api/needs-attention",
        response_model=NeedsAttention,
        tags=["Data, inputs & references"],
        summary="Investigation needs-attention scan",
    )
    def needs_attention(
        investigation: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> NeedsAttention:
        """Run the deterministic needs-attention scan for an investigation.

        Returns ``{investigation, items: [...], summary: {by_severity,
        by_kind, total}}``.  Always HTTP 200 — degrades to empty lists/zeroes
        when ``pbg_superpowers.needs_attention`` is unavailable.

        Library-backed via ``lib.report_views.build_needs_attention``.
        """
        body, _ = _report_views.build_needs_attention(ws, investigation=investigation)
        return NeedsAttention.model_validate(body)

    @app.get(
        "/api/inputs",
        response_model=InputsPayload,
        tags=["Data, inputs & references"],
        summary="Investigation inputs + global inputs for the Inputs tab",
    )
    def inputs(
        investigation: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> InputsPayload:
        """Loaded investigation's inputs (top) + repo-wide global inputs.

        Returns ``{investigation: {...}, global: {...}, current: slug|null}``.
        ``?investigation=<slug>`` overrides the git-branch-derived slug so the
        tab follows the SPA-selected investigation.

        Always HTTP 200.  Library-backed via ``lib.report_views.build_inputs``
        — the single implementation the stdlib ``server._inputs_payload`` now
        forwards to.
        """
        body = _report_views.build_inputs(ws, investigation)
        return InputsPayload.model_validate(body)

    @app.get(
        "/api/investigation/{slug}",
        response_model=IsetDetail,
        tags=["Data, inputs & references"],
        summary="Full investigation detail (one investigation.yaml + resolved studies)",
    )
    def investigation_detail(
        slug: str,
        ws: Path = Depends(get_workspace),
    ) -> Union[IsetDetail, JSONResponse]:
        """Full investigation-detail dict for the investigation-detail SPA page.

        Returns the complete investigation payload built by
        ``lib.report_views.build_iset_detail``: investigation.yaml fields +
        each member study's resolved spec (n_runs, effective_status, findings,
        discovery_implications, acceptance roll-up, etc.).

        HTTP 404 ``{"error": "no investigation.yaml for '<slug>'"}`` when the
        investigation.yaml does not exist — byte-identical to the legacy
        ``_get_iset_detail`` handler.

        Library-backed via ``lib.report_views.build_iset_detail``.
        """
        result = _report_views.build_iset_detail(ws, slug)
        if result is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"no investigation.yaml for {slug!r}"},
            )
        return IsetDetail.model_validate(result)

    # -----------------------------------------------------------------------
    # Observables / never-fabricate guard + linkage-index routes
    # -----------------------------------------------------------------------

    @app.get(
        "/api/observables",
        response_model=ObservablesPayload,
        tags=["Data, inputs & references"],
        summary="Emittable observables of a composite (never-fabricate source)",
    )
    def observables(
        ref: str = "",
        ws: Path = Depends(get_workspace),
    ) -> Union[ObservablesPayload, JSONResponse]:
        """Emittable observables of a composite (mirrors the stdlib /api/observables).

        Runs the SAME in-process composite build the Composite Explorer uses and
        reports its emittable observables via
        ``pbg_superpowers.readout_validation.available_observables``:
        ``{ref, leaves: [dotted paths], catalogs: {observable: [labels]}}`` (plus
        ``cached: true`` on a TTL cache hit).

        Error paths replicate the legacy worker's exact status codes + bodies
        (``{"error": ...}`` via :class:`JSONResponse`):

        - HTTP 400 — no ``?ref=`` (``{"error": "ref required"}``) or composite
          build failure.
        - HTTP 404 — unknown ref.
        - HTTP 500 — observable introspection failed.
        - HTTP 501 — ``readout_validation`` validator absent.
        - HTTP 200 — the payload dict (validated through ``ObservablesPayload``).

        Library-backed via ``lib.observables_views.build_observables``.
        """
        body, status = _obs_views.build_observables(ws, ref)
        if status == 200:
            return ObservablesPayload.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    @app.get(
        "/api/study-observable-check",
        response_model=StudyObservableCheck,
        tags=["Data, inputs & references"],
        summary="Validate a study's readouts against its composite structure",
    )
    def study_observable_check(
        study: str = "",
        investigation: str = "",
        name: str = "",
        ws: Path = Depends(get_workspace),
    ) -> Union[StudyObservableCheck, JSONResponse]:
        """Per-readout never-fabricate validation (mirrors /api/study-observable-check).

        The study slug is read from ``?study=`` (falling back to
        ``?investigation=`` then ``?name=``), matching the legacy do_GET
        dispatch.  Validates every readout against the study's baseline composite
        structure: ``{composite: ref, readouts: [{name, status, detail}]}`` with
        ``status`` ∈ ``ok|unresolved|not_in_structure|aspirational``.

        Error paths replicate the legacy worker's exact status codes + bodies via
        :class:`JSONResponse`:

        - HTTP 400 — invalid slug, or study spec parse failure.
        - HTTP 404 — study not found.
        - HTTP 422 — no baseline composite / no composite ref, OR the composite
          could not be built (every readout surfaced as ``aspirational`` with a
          note — never a 500).
        - HTTP 501 — ``readout_validation`` validator absent.
        - HTTP 500 — readout validation itself raised.
        - HTTP 200 — the payload dict (validated through ``StudyObservableCheck``).

        Library-backed via ``lib.observables_views.build_study_observable_check``.
        """
        slug = (study or investigation or name or "").strip()
        body, status = _obs_views.build_study_observable_check(ws, slug)
        if status == 200:
            return StudyObservableCheck.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    @app.get(
        "/api/linkage-index",
        response_model=LinkageIndex,
        tags=["Data, inputs & references"],
        summary="Deterministic linkage index / navigate queries (always 200)",
    )
    def linkage_index(
        investigation: str = "",
        inv: str = "",
        source: str = "",
        observable: str = "",
        observable_registry: str = "",
        composite: str = "",
        ws: Path = Depends(get_workspace),
    ) -> LinkageIndex:
        """SP4a/SP4b linkage index + navigate queries (mirrors /api/linkage-index).

        Param-dispatch (all optional; ``investigation`` accepts the ``inv``
        alias, matching the legacy do_GET):

        - ``?source=``               → ``{studies: [...]}`` (studies citing the bib_key)
        - ``?observable=``           → ``{findings: [...]}`` (findings measuring the token)
        - ``?observable_registry=``  → ``{studies, composites}`` emitting the token
        - ``?composite=``            → ``{emits, used_by_studies}`` for that composite
        - ``?investigation=`` (or ``?inv=``) → ``{investigation, ac_matrix, dag}``
        - (none)                     → the full ``{nodes, edges}`` graph

        ALWAYS HTTP 200 — an older/absent pbg_superpowers or an unscannable
        workspace returns an empty/typed payload rather than erroring.  The
        ``observable_registry`` / ``composite`` paths trigger a (cached)
        composite build, sourcing observables from
        ``lib.observables_views.observables_for_ref_payload`` — so this route
        produces identical linkage data to the legacy stdlib worker.

        Library-backed via ``lib.report_views.build_linkage_index``.
        """
        body, _ = _report_views.build_linkage_index(
            ws,
            investigation=(investigation or inv).strip() or None,
            source=source.strip() or None,
            observable=observable.strip() or None,
            observable_registry=observable_registry.strip() or None,
            composite=composite.strip() or None,
            # The enrich callable takes (ws_root, ref) — pass the lib function directly.
            observables_for_ref_fn=_obs_views.observables_for_ref_payload,
        )
        return LinkageIndex.model_validate(body)

    # -----------------------------------------------------------------------
    # System & workspace routes
    # -----------------------------------------------------------------------

    @app.get(
        "/api/framework-metrics",
        response_model=FrameworkMetrics,
        tags=["System"],
        summary="Aggregated framework-self metrics across all studies + investigations",
    )
    def framework_metrics_route(
        ws: Path = Depends(get_workspace),
    ) -> Union[FrameworkMetrics, JSONResponse]:
        """Framework-self metrics scorecard for GET /api/framework-metrics.

        Aggregates ``pbg_superpowers.rigor.framework_metrics`` over every
        study + investigation in the workspace.  Returns
        ``{metrics: {…}, n_investigations: int, n_studies: int}``.

        Always HTTP 200 (best-effort): ``metrics`` degrades to ``{}`` when
        pbg_superpowers is absent or the compute raises.  If the builder dict
        fails typed validation (forward-compat / off-spec shape) the raw dict is
        returned at HTTP 200 — byte-identical to the legacy handler, never 500.

        Library-backed via ``lib.system_info.build_framework_metrics``.
        """
        data = _system_info.build_framework_metrics(ws)
        try:
            return FrameworkMetrics.model_validate(data)
        except ValidationError:
            return JSONResponse(status_code=200, content=data)

    @app.get(
        "/api/github-repo",
        response_model=GithubRepo,
        tags=["System"],
        summary="Workspace GitHub repo slug (owner/name or null)",
    )
    def github_repo_route(
        ws: Path = Depends(get_workspace),
    ) -> Union[GithubRepo, JSONResponse]:
        """The workspace's GitHub repo slug for GET /api/github-repo.

        Resolution order (first hit wins):
          1. ``git remote get-url origin`` parsed for github.com.
          2. workspace.yaml ``dashboard.github_repo`` / ``dashboard.repository``.

        Returns ``{repo: "owner/name"}`` or ``{repo: null}``.  Always 200; an
        off-spec builder dict degrades to the raw dict at HTTP 200 (byte-identical
        to the legacy handler, never 500).

        Library-backed via ``lib.system_info.build_github_repo``.
        """
        data = _system_info.build_github_repo(ws)
        try:
            return GithubRepo.model_validate(data)
        except ValidationError:
            return JSONResponse(status_code=200, content=data)

    @app.get(
        "/api/ui-config",
        response_model=UiConfig,
        tags=["System"],
        summary="UI feature flags from workspace.yaml",
    )
    def ui_config_route(
        ws: Path = Depends(get_workspace),
    ) -> Union[UiConfig, JSONResponse]:
        """UI feature-flag config for GET /api/ui-config.

        Reads workspace.yaml's ``ui:`` block.  Missing/unreadable workspace →
        all-default values.  Always 200.

        Keys: ``composite_view`` (default "bigraph-loom"),
        ``ptools_server_url`` (default ""),
        ``ptools_omics_url_template`` (default template string).

        The legacy handler serializes whatever ``ui.get(...)`` returns at HTTP
        200, even a non-string value (e.g. ``composite_view: 42``).  The typed
        ``UiConfig`` declares ``str`` fields, so such a value would raise a
        ``ValidationError`` → 500; the fallback returns the raw builder dict at
        HTTP 200 instead, preserving never-500 + byte-identity.

        Library-backed via ``lib.system_info.build_ui_config``.
        """
        data = _system_info.build_ui_config(ws)
        try:
            return UiConfig.model_validate(data)
        except ValidationError:
            return JSONResponse(status_code=200, content=data)

    @app.get(
        "/api/workspace",
        response_model=WorkspaceHome,
        tags=["System"],
        summary="Workspace narrative metadata (name, description, investigations)",
    )
    def workspace_home_route(
        ws: Path = Depends(get_workspace),
    ) -> Union[WorkspaceHome, JSONResponse]:
        """Workspace home metadata for GET /api/workspace.

        Reads workspace.yaml + enumerates investigation dirs.  Returns
        ``{name, description, imports, investigations: [...]}``.  Always 200; an
        off-spec builder dict degrades to the raw dict at HTTP 200 (byte-identical
        to the legacy handler, never 500).

        Library-backed via ``lib.system_info.build_workspace_home``.
        """
        data = _system_info.build_workspace_home(ws)
        try:
            return WorkspaceHome.model_validate(data)
        except ValidationError:
            return JSONResponse(status_code=200, content=data)

    # -----------------------------------------------------------------------
    # Composite runs routes  (file-backed SQLite reads, Phase A)
    # -----------------------------------------------------------------------

    @app.get(
        "/api/composite-runs",
        response_model=CompositeRunsList,
        tags=["Composites"],
        summary="List runs for one composite spec",
    )
    def composite_runs_list(
        spec_id: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[CompositeRunsList, JSONResponse]:
        """List runs for a composite spec (mirrors stdlib GET /api/composite-runs).

        ``?spec_id=<id>`` — required; returns HTTP 400
        ``{"runs": [], "error": "missing spec_id"}`` when absent.  Returns
        ``{"runs": []}`` (HTTP 200) when ``.pbg/composite-runs.db`` does not
        exist yet (no runs have been launched).

        Library-backed via ``lib.composite_run_views.build_composite_runs``.
        """
        body, status = _cr_views.build_composite_runs(ws, spec_id)
        if status == 200:
            return CompositeRunsList.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    @app.get(
        "/api/composite-run/{run_id}/state",
        response_model=CompositeRunState,
        tags=["Composites"],
        summary="Single state snapshot for a run at a given step",
    )
    def composite_run_state_route(
        run_id: str,
        step: str = "0",
        ws: Path = Depends(get_workspace),
    ) -> Union[CompositeRunState, JSONResponse]:
        """Single composite-state snapshot at one step (mirrors stdlib
        GET /api/composite-run/<run_id>/state?step=N).

        ``?step=<int>`` (default 0); returns HTTP 400
        ``{"error": "step must be int"}`` on non-integer input — mirroring the
        legacy ``int(step_raw, ValueError→400)`` behaviour exactly.  Returns
        HTTP 404 when the db is absent or the step is not in history.

        Library-backed via ``lib.composite_run_views.build_composite_run_state``.
        """
        try:
            step_int = int(step)
        except ValueError:
            return JSONResponse(
                status_code=400, content={"error": "step must be int"}
            )
        body, status = _cr_views.build_composite_run_state(ws, run_id, step_int)
        if status == 200:
            return CompositeRunState.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    @app.get(
        "/api/composite-run/{run_id}/status",
        response_model=CompositeRunStatus,
        tags=["Composites"],
        summary="Lightweight run status (progress, terminal-state error/viz_html)",
    )
    def composite_run_status_route(
        run_id: str,
        ws: Path = Depends(get_workspace),
    ) -> Union[CompositeRunStatus, JSONResponse]:
        """Lightweight status for a composite run (mirrors stdlib
        GET /api/composite-run/<run_id>/status).

        Returns ``{run_id, status, progress_step, n_steps, heartbeat_at}`` plus
        (for terminal states) ``log_path`` + ``error`` excerpt
        (failed/orphaned) or ``viz_html`` (completed).

        HTTP 404 when the db is absent or the run is not found.

        Library-backed via ``lib.composite_run_views.build_composite_run_status``.
        """
        body, status = _cr_views.build_composite_run_status(ws, run_id)
        if status == 200:
            return CompositeRunStatus.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    @app.get(
        "/api/composite-run/{run_id}",
        response_model=CompositeRunTrajectory,
        tags=["Composites"],
        summary="Return full trajectory for a composite run",
    )
    def composite_run_route(
        run_id: str,
        ws: Path = Depends(get_workspace),
    ) -> Union[CompositeRunTrajectory, JSONResponse]:
        """Full trajectory for a composite run (mirrors stdlib
        GET /api/composite-run/<run_id>).

        Returns ``{run_id, trajectory: [{step, time, state}, ...]}`` on success.
        HTTP 404 when the db is absent (``{"error": "no run database"}``) or the
        trajectory is empty (``{"error": "run not found"}``).

        Note: ``run_id`` values contain colons but no slashes so path
        routing is unambiguous; the ``/state`` and ``/status`` sub-routes are
        distinct paths and registered before this bare-id route.

        Library-backed via ``lib.composite_run_views.build_composite_run``.
        """
        body, status = _cr_views.build_composite_run(ws, run_id)
        if status == 200:
            return CompositeRunTrajectory.model_validate(body)
        return JSONResponse(status_code=status, content=body)

    # Workspace & source routes  (Batch 13)
    # -----------------------------------------------------------------------

    @app.get(
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

        Library-backed via ``lib.workspace_deps_views.build_source_builds``.
        """
        return SourceBuilds.model_validate(_workspace_deps.build_source_builds())

    @app.get(
        "/api/workspaces",
        response_model=WorkspacesList,
        tags=["Workspaces & sources"],
        summary="Workspace-switcher dropdown (catalog + live server status)",
    )
    def workspaces_route(ws: Path = Depends(get_workspace)) -> WorkspacesList:
        """Workspace-switcher dropdown payload (mirrors the stdlib GET /api/workspaces).

        Reads ``~/.pbg/workspaces.json`` (global catalog) and joins each entry
        with ``~/.pbg/servers/<name>.json`` to determine live/stale/stopped
        status.  Returns ``{current: {name, path}, workspaces: [...]}``.

        Always HTTP 200 — falls back to current-workspace-only when the catalog
        is missing or corrupt.

        Library-backed via ``lib.workspace_deps_views.build_workspaces``.
        """
        return WorkspacesList.model_validate(_workspace_deps.build_workspaces(ws))

    @app.get(
        "/api/system-deps-check",
        response_model=SystemDepsCheck,
        tags=["Workspaces & sources"],
        summary="Check whether a catalog module's system dependencies are satisfied",
    )
    def system_deps_check_route(
        name: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[SystemDepsCheck, JSONResponse]:
        """System-dependency check for a catalog module (mirrors the stdlib
        GET /api/system-deps-check?name=<module>).

        Runs each ``system_dependencies.checks[]`` entry's ``import_check``
        snippet inside the workspace venv (``<ws>/.venv/bin/python3``) and
        returns structured results:
        ``{name, platform, ok, checks: [{name, description, ok, reason, install,
        notes}]}``.

        Error paths replicate the legacy handler's exact status codes + bodies
        (``{"error": ...}`` via :class:`JSONResponse`):

        - HTTP 400 ``{"error": "name required"}`` — ``?name=`` missing or empty.
        - HTTP 404 ``{"error": "unknown module: <name>"}`` — not in registry.
        - HTTP 200 — the full check payload (validated through SystemDepsCheck).

        Library-backed via ``lib.workspace_deps_views.build_system_deps_check``.
        """
        body, status = _workspace_deps.build_system_deps_check(ws, name or "")
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return SystemDepsCheck.model_validate(body)

    # -----------------------------------------------------------------------
    # Downloads — binary / HTML file responses (FileResponse / Response, not a
    # pydantic model; a response_model would 422 binary content).  Each route
    # reproduces the legacy Content-Type + inline-vs-attachment disposition +
    # status codes exactly; error paths return ``{"error": ...}`` JSON.
    # -----------------------------------------------------------------------

    @app.get(
        "/api/study-export",
        tags=["Downloads"],
        summary="Download a study directory as a zip archive",
        response_class=Response,
    )
    def study_export_route(
        study: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Response:
        """Zip ``studies/<study>/`` and serve it as ``application/zip`` attachment
        ``<study>.zip`` (mirrors the stdlib GET /api/study-export).

        HTTP 400 ``{"error": "missing study"}`` when ``?study=`` is missing;
        HTTP 404 ``{"error": "study not found"}`` when the study dir is absent.

        Library-backed via ``lib.download_views.build_study_export``.
        """
        try:
            data, mime, filename = _download_views.build_study_export(
                ws, (study or "").strip()
            )
        except _download_views.DownloadError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body)
        return Response(
            content=data,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get(
        "/api/data-source-file",
        tags=["Downloads"],
        summary="Serve one workspace data-source bundle file by key",
        response_class=Response,
    )
    def data_source_file_route(
        key: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Response:
        """Serve the bytes of the data-source bundle entry whose ``key`` matches
        (mirrors the stdlib GET /api/data-source-file?key=...).

        The path comes ONLY from the provider enumeration (no traversal
        surface).  Text kinds (tsv/csv/json/txt/fasta/yaml/md) are served
        inline; anything else as an attachment.  All responses carry
        ``Cache-Control: no-store``.

        HTTP 400 ``{"error": "missing ?key="}`` when ``?key=`` is missing;
        HTTP 404 when the key is unknown or its file is missing; HTTP 500 on an
        OS read error.

        Library-backed via ``lib.download_views.resolve_data_source_file``.
        """
        try:
            data, mime, inline, filename = _download_views.resolve_data_source_file(
                ws, key
            )
        except _download_views.DownloadError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body)
        headers = {"Cache-Control": "no-store"}
        if not inline:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return Response(content=data, media_type=mime, headers=headers)

    @app.get(
        "/api/investigation/{slug}/report",
        tags=["Downloads"],
        summary="Per-investigation HTML report file",
        response_class=Response,
    )
    def investigation_report_route(
        slug: str,
        ws: Path = Depends(get_workspace),
    ) -> Response:
        """Serve the per-investigation report ``index.html`` as ``text/html``
        (mirrors the stdlib GET /api/investigation/<slug>/report).

        HTTP 404 ``{"error": "no report for investigation '<slug>'"}`` when no
        report file exists.

        Library-backed via ``lib.download_views.resolve_iset_report``.
        """
        path = _download_views.resolve_iset_report(ws, slug)
        if path is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"no report for investigation {slug!r}"},
            )
        # Plain Response(read_bytes): byte-identical header set to _serve_file
        # (bare text/html + Cache-Control: no-store, no FileResponse ETag/
        # Last-Modified/Accept-Ranges). These reports are live-regenerated, so a
        # conditional 304 must never serve stale content.
        return Response(
            content=path.read_bytes(),
            headers={"Content-Type": "text/html", "Cache-Control": "no-store"},
        )

    @app.get(
        "/api/guidance",
        tags=["Downloads"],
        summary="Latest guidance HTML (204 when none)",
        response_class=Response,
    )
    def guidance_route(ws: Path = Depends(get_workspace)) -> Response:
        """Serve the latest ``*.html`` in ``<pbg>/server/content`` as
        ``text/html`` (mirrors the stdlib GET /api/guidance).

        HTTP 204 No Content when the content dir or any ``*.html`` is absent.

        Library-backed via ``lib.download_views.resolve_guidance``.
        """
        latest = _download_views.resolve_guidance(ws)
        if latest is None:
            return Response(status_code=204)
        # Plain Response(read_bytes): byte-identical header set to _serve_file.
        return Response(
            content=latest.read_bytes(),
            headers={"Content-Type": "text/html", "Cache-Control": "no-store"},
        )

    @app.get(
        "/api/investigation-notebook/{slug}",
        tags=["Downloads"],
        summary="Download an investigation's runnable notebook (.ipynb) or script (.py)",
        response_class=Response,
    )
    def investigation_notebook_route(
        slug: str,
        format: str = "ipynb",
        ws: Path = Depends(get_workspace),
    ) -> Response:
        """Generate + download an investigation's notebook/script (mirrors the
        stdlib GET /api/investigation-notebook/<slug>[?format=py]).

        Deterministic export (no AI). ``?format=py`` → ``text/x-python``;
        otherwise → ``application/x-ipynb+json``.  Served as an attachment with
        ``Cache-Control: no-store``.

        HTTP 400 ``{"error": "investigation slug required"}`` when ``slug`` is
        empty; HTTP 404 ``{"error": "no investigation '<slug>'"}`` when absent;
        HTTP 500 on export failure.

        Library-backed via ``lib.download_views.build_investigation_notebook``.
        """
        try:
            data, mime, filename = _download_views.build_investigation_notebook(
                ws, slug, format
            )
        except _download_views.DownloadError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body)
        # Set Content-Type via headers (not media_type) so Starlette does not
        # append "; charset=utf-8" to the legacy bare "text/x-python" /
        # "application/x-ipynb+json" values — keeps the header byte-identical.
        return Response(
            content=data,
            headers={
                "Content-Type": mime,
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    # -----------------------------------------------------------------------
    # Events (Phase C, Batch 15): SSE workspace-state stream
    # -----------------------------------------------------------------------

    @app.get("/api/events", tags=["System"], summary="SSE workspace-state stream")
    def events(ws: Path = Depends(get_workspace)) -> StreamingResponse:
        """Server-Sent Events stream: polls ``workspace.yaml`` every 1 s.

        Emits ``event: state\\ndata: <json>\\n\\n`` whenever the file changes.
        First event fires immediately when the file already exists (no initial
        delay).  On parse failure: ``data: {"_error": "yaml parse"}``.

        Mirrors ``server.Handler._serve_events_sse`` exactly (byte-identical
        SSE framing, same cadence, same Content-Type / Cache-Control headers).

        Library-backed via ``lib.events.workspace_state_stream`` and
        ``lib.events.workspace_state_payload`` — no dependency on the stdlib
        server module.
        """
        return StreamingResponse(
            _events.workspace_state_stream(ws),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.get(
        "/api/state",
        tags=["System"],
        summary="One-shot workspace.yaml state read",
    )
    def state(ws: Path = Depends(get_workspace)) -> JSONResponse:
        """One-shot ``workspace.yaml`` state (mirrors the stdlib /api/state).

        Returns the parsed ``workspace.yaml`` mapping with ``Cache-Control:
        no-store``; 404 (empty body) when the file does not exist — byte-
        identical to ``server.Handler._serve_state``.  Library-backed via
        ``lib.events.read_workspace_state`` — no stdlib server dependency.
        """
        data = _events.read_workspace_state(ws)
        if data is None:
            return JSONResponse(content=None, status_code=404)
        return JSONResponse(content=data, headers={"Cache-Control": "no-store"})

    @app.get(
        "/api/suggest-poll",
        tags=["System"],
        summary="Poll for a pbg-suggest skill response",
    )
    def suggest_poll(
        id: str = "", ws: Path = Depends(get_workspace)
    ) -> JSONResponse:
        """Poll for a ``/pbg-suggest`` skill response (mirrors the stdlib
        /api/suggest-poll).

        Query ``?id=<request-id>``. Returns ``{"ready": false}`` until the
        skill writes its response, then ``{"ready": true, suggestion,
        rationale}``. Missing ``id`` -> 400 ``{"error": "missing id"}``.
        Library-backed via ``lib.suggest_requests.read_response`` — no stdlib
        server dependency.
        """
        from vivarium_dashboard.lib.suggest_requests import read_response

        if not id:
            return JSONResponse(content={"error": "missing id"}, status_code=400)
        resp = read_response(ws, id)
        if not resp:
            return JSONResponse(content={"ready": False}, status_code=200)
        return JSONResponse(
            content={
                "ready": True,
                "suggestion": resp.get("suggestion", ""),
                "rationale": resp.get("rationale", ""),
            },
            status_code=200,
        )

    # -----------------------------------------------------------------------
    # Static + SPA-shell serving (Phase C, Batch 16): the SPA index, the
    # standalone loom/parsimony viewer bundles, and a catch-all asset route.
    # FileResponse (not a pydantic model); each reproduces the legacy
    # ``do_GET`` static branch byte-for-byte — the bundled→assets-strip→
    # workspace→reports resolution priority, the ``_guess_mime`` table, and
    # ``Cache-Control: no-store`` on every served file.  ``/studies/{slug}``
    # (the study-detail HTML page) is DEFERRED (needs the heavy
    # ``_render_study_detail_html`` extraction) and is NOT ported here.
    # -----------------------------------------------------------------------

    def _serve_static_file(target: Path, rel: str) -> Response:
        """Serve *target* with the guessed bare mime + ``Cache-Control: no-store``;
        404 (empty body) when it is not a file.  Mirrors ``server._serve_file``."""
        if not target.is_file():
            return Response(status_code=404)
        # Read the bytes and return a plain Response (NOT FileResponse) so the
        # header set is byte-identical to the legacy _serve_file: Content-Type
        # (bare mime, no charset suffix via the headers dict) + Cache-Control:
        # no-store, and nothing else. FileResponse would also emit ETag /
        # Last-Modified / Accept-Ranges, enabling conditional 304s and Range
        # 206s that _serve_file never did (it always sends a full 200).
        return Response(
            content=target.read_bytes(),
            headers={
                "Content-Type": _static_serving.guess_mime(rel),
                "Cache-Control": "no-store",
            },
        )

    @app.get(
        "/",
        tags=["Static & shell"],
        summary="SPA shell index (re-render then serve reports/index.html)",
        response_class=Response,
        include_in_schema=False,
    )
    @app.get(
        "/index.html",
        tags=["Static & shell"],
        summary="SPA shell index (re-render then serve reports/index.html)",
        response_class=Response,
        include_in_schema=False,
    )
    def index_shell(ws: Path = Depends(get_workspace)) -> Response:
        """Render the SPA shell (best-effort) then serve ``reports/index.html``.

        Re-renders via ``lib.report.render_workspace_report`` BEFORE serving so
        the live dashboard is decoupled from the on-disk ``reports/index.html``
        (which offline tools may overwrite); a render failure never blocks the
        load — we fall back to whatever is on disk.  Served as ``text/html`` with
        ``Cache-Control: no-store``; 404 when the file is absent.

        Mirrors the legacy ``do_GET`` ``("/", "/index.html")`` branch.
        """
        try:
            from vivarium_dashboard.lib.report import render_workspace_report
            render_workspace_report(ws)
        except Exception as render_exc:  # noqa: BLE001 — never block load
            import sys as _sys
            print(
                f"[dashboard] / re-render failed; serving on-disk file: "
                f"{type(render_exc).__name__}: {render_exc}", file=_sys.stderr,
            )
        path = _static_serving.index_html_path(ws)
        if not path.is_file():
            return Response(status_code=404)
        # Plain Response(read_bytes) — byte-identical header set to _serve_file
        # (no FileResponse ETag/Last-Modified/Accept-Ranges).
        return Response(
            content=path.read_bytes(),
            headers={"Content-Type": "text/html", "Cache-Control": "no-store"},
        )

    @app.get(
        "/bigraph-loom/{rel:path}",
        tags=["Static & shell"],
        summary="bigraph-loom viewer bundle asset",
        response_class=Response,
        include_in_schema=False,
    )
    def bigraph_loom_asset(rel: str = "") -> Response:
        """Serve a ``bigraph-loom`` viewer asset from ``bigraph_loom.asset_dir()``.

        ``rel`` empty → ``index.html``.  HTTP 403 (empty body) on a ``..`` path
        segment (traversal guard); 404 when the file is absent.  Served with the
        guessed bare mime + ``Cache-Control: no-store``.  Mirrors the legacy
        ``/bigraph-loom`` branch.

        Library-backed via ``lib.static_serving.resolve_loom_asset``.
        """
        try:
            target = _static_serving.resolve_loom_asset(rel)
        except _static_serving.AssetTraversal:
            return Response(status_code=403)
        return _serve_static_file(target, rel or "index.html")

    @app.get(
        "/parsimony-viewer/{rel:path}",
        tags=["Static & shell"],
        summary="pbg_parsimony 3D viewer bundle asset (404 when not installed)",
        response_class=Response,
        include_in_schema=False,
    )
    def parsimony_viewer_asset(rel: str = "") -> Response:
        """Serve a ``pbg_parsimony`` viewer asset (feature-detected).

        HTTP 404 (empty body) when ``pbg_parsimony`` is not installed (the
        Analyses gallery hides its 3D cards), or when the file is absent; HTTP
        403 on a ``..`` path segment.  ``rel`` empty → ``index.html``.  Served
        with the guessed bare mime + ``Cache-Control: no-store``.  Mirrors the
        legacy ``/parsimony-viewer`` branch.

        Library-backed via ``lib.static_serving.resolve_parsimony_asset``.
        """
        try:
            target = _static_serving.resolve_parsimony_asset(rel)
        except _static_serving.AssetTraversal:
            return Response(status_code=403)
        if target is None:
            return Response(status_code=404)
        return _serve_static_file(target, rel or "index.html")

    # -----------------------------------------------------------------------
    # Study-detail HTML page (Phase C, Batch 17)
    # MUST be registered BEFORE the catch-all ``/{rel:path}`` below so
    # Starlette matches ``/studies/<slug>`` here instead of falling through
    # to the catch-all asset handler (which would serve a 404 for the path).
    # -----------------------------------------------------------------------

    @app.get(
        "/studies/{slug}",
        tags=["Static & shell"],
        summary="Study-detail HTML page (server-side rendered)",
        response_class=Response,
        include_in_schema=False,
    )
    def study_detail_page(slug: str, ws: Path = Depends(get_workspace)) -> Response:
        """Render the study-detail HTML page for ``/studies/<slug>``.

        Validates *slug* against ``lib.study_spec.SLUG_RE``; invalid slug →
        404 ``<h1>Not found</h1>``.  Unknown slug (no spec file) → 404 with
        the "Study not found" body.  Valid study → 200 HTML page.

        ``media_type="text/html"`` causes Starlette to emit
        ``Content-Type: text/html; charset=utf-8`` — byte-identical to the
        legacy ``_send_html`` content-type.  No ``Cache-Control`` header is
        set (``_send_html`` omits it; it is NOT the ``no-store`` of
        ``_serve_file``).

        Library-backed via ``lib.study_page.build_study_detail_page``.
        """
        html, status = _study_page.build_study_detail_page(ws, slug)
        return Response(content=html, status_code=status, media_type="text/html")

    # -----------------------------------------------------------------------
    # Batch 18: Investigation & study mutations (POST routes)
    # NOTE: CSRF guard is deferred to the state/flip batch — the live do_POST
    # still enforces _csrf_ok; the FastAPI POST routes are not live until the
    # flip.  A shared Depends(csrf_guard) for all POST routes is added then.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/investigation-set-observables",
        tags=["Investigations"],
        summary="Set investigation observable paths",
    )
    def investigation_set_observables(
        req: SetObservablesBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Rewrite spec.yaml/study.yaml observables[].

        Body: ``{investigation, paths: [[str,...]], emit_all?: bool}``
        """
        body, status = _meta_mut.set_investigation_observables(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-set-conclusions",
        tags=["Investigations"],
        summary="Set investigation conclusions markdown",
    )
    def investigation_set_conclusions(
        req: SetConclusionsBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Write spec.yaml/study.yaml conclusions (256 KB limit).

        Body: ``{investigation|name|study, markdown: str}``
        """
        body, status = _meta_mut.set_investigation_conclusions(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-set-overview",
        tags=["Investigations"],
        summary="Set investigation overview metadata fields",
    )
    def investigation_set_overview(
        req: SetOverviewBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Selectively update question/hypothesis/status/topic on spec.yaml.

        Body: ``{investigation, fields: {question?, hypothesis?, status?, topic?}}``
        """
        body, status = _meta_mut.set_investigation_overview(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-set-status",
        tags=["Investigations"],
        summary="Set investigation status (archived / active / …)",
    )
    def investigation_set_status(
        req: SetStatusBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Write the status field into investigations/<slug>/investigation.yaml.

        Body: ``{investigation, status}``
        Valid statuses: active, in-progress, planning, completed, archived, closed.
        """
        body, status = _meta_mut.set_investigation_status(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-set-objective",
        tags=["Investigations"],
        summary="Set study objective text",
    )
    def study_set_objective(
        req: SetObjectiveBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Write study.yaml objective field.

        Body: ``{study, text?: str}``
        """
        body, status = _meta_mut.set_study_objective(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-refresh-viz/{name}",
        tags=["Studies"],
        summary="Re-render a study's declared visualizations against its latest run",
    )
    def study_refresh_viz(
        name: str, ws: Path = Depends(get_workspace)
    ) -> JSONResponse:
        """Re-render study ``name``'s ``visualizations[]`` against its latest
        run, stamping provenance (mirrors the stdlib /api/study-refresh-viz).

        Tolerant by design: per-chart render failures come back as
        ``status="error"`` entries inside ``results`` (never a 500); an
        unexpected exception -> 500 ``{error, study}``; a missing study -> 404
        ``{error, study}``; a blank name -> 400.  Library-backed via
        ``lib.study_viz_views.study_refresh_viz`` — no stdlib server dependency.
        """
        if not name.strip():
            return JSONResponse(
                content={"error": "missing study name"}, status_code=400
            )
        try:
            payload = _study_viz.study_refresh_viz(ws, name)
        except Exception as e:  # noqa: BLE001
            return JSONResponse(
                content={"error": str(e), "study": name}, status_code=500
            )
        if payload.get("not_found"):
            return JSONResponse(
                content={"error": payload["error"], "study": name}, status_code=404
            )
        return JSONResponse(content=payload, status_code=200)

    @app.post(
        "/api/study-narrative-set",
        tags=["Investigations"],
        summary="Set a v4 narrative-spine field at a dotted path",
    )
    def study_narrative_set(
        req: NarrativeSetBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Generic writer for v4 narrative-spine fields.

        Body: ``{study, path: "dotted.path", value: any}``
        ``value`` absence (not sent) is distinct from null — absence triggers
        a 400; null clears the leaf.  Pass ``model_dump(exclude_unset=True)``
        so the lib builder's ``"value" not in body`` check works correctly.
        """
        body, status = _meta_mut.set_study_narrative(
            ws, req.model_dump(exclude_unset=True)
        )
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-expert-input-set",
        tags=["Investigations"],
        summary="Patch conditions.model_settings[i].current in study.yaml",
    )
    def study_expert_input_set(
        req: ExpertInputSetBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Update one expert model-setting value.

        Body: ``{study, name, current: any}``
        ``current`` absence is distinct from null — absence triggers a 400.
        """
        body, status = _meta_mut.set_study_expert_input(
            ws, req.model_dump(exclude_unset=True)
        )
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    # -----------------------------------------------------------------------
    # Batch 19: Study CRUD — variant / baseline / intervention / run / comparison
    # -----------------------------------------------------------------------

    @app.post(
        "/api/study-variant-add",
        tags=["Studies"],
        summary="Add a variant entry to study.yaml",
    )
    def study_variant_add(
        req: StudyVariantAddBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Append a new variant (base_composite + optional parameter_overrides).

        Body: ``{study|investigation, name, base_composite, parameter_overrides?}``
        """
        body, status = _study_crud_mut.study_variant_add(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-variant-delete",
        tags=["Studies"],
        summary="Remove a variant entry from study.yaml",
    )
    def study_variant_delete(
        req: StudyVariantDeleteBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Delete a named variant.

        Body: ``{name|study|investigation, variant}``
        """
        body, status = _study_crud_mut.study_variant_delete(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-variant-set-params",
        tags=["Studies"],
        summary="Replace a variant's parameter_overrides in study.yaml",
    )
    def study_variant_set_params(
        req: StudyVariantSetParamsBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Replace (not merge) a variant's parameter_overrides.

        Body: ``{name|study|investigation, variant, parameter_overrides: dict}``
        """
        body, status = _study_crud_mut.study_variant_set_params(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-baseline-add",
        tags=["Studies"],
        summary="Append a composite to study.yaml baseline[]",
    )
    def study_baseline_add(
        req: StudyBaselineAddBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Add a named baseline entry (composite + optional params).

        Body: ``{study|investigation, name, composite, params?}``
        """
        body, status = _study_crud_mut.study_baseline_add(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-baseline-remove",
        tags=["Studies"],
        summary="Remove a baseline entry from study.yaml",
    )
    def study_baseline_remove(
        req: StudyBaselineRemoveBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Remove a named baseline entry.

        Body: ``{study|investigation, name}``
        409 if any variant references this entry; 400 if removal would empty baseline.
        """
        body, status = _study_crud_mut.study_baseline_remove(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-intervention-add",
        tags=["Studies"],
        summary="Append an intervention to study.yaml interventions[]",
    )
    def study_intervention_add(
        req: StudyInterventionAddBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Add a named intervention (with optional description).

        Body: ``{study|investigation, name, description?}``
        """
        body, status = _study_crud_mut.study_intervention_add(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-intervention-update",
        tags=["Studies"],
        summary="Update an intervention's description in study.yaml",
    )
    def study_intervention_update(
        req: StudyInterventionUpdateBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Replace an intervention's description field.

        Body: ``{study|investigation, name, description}``
        """
        body, status = _study_crud_mut.study_intervention_update(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-intervention-delete",
        tags=["Studies"],
        summary="Remove an intervention from study.yaml",
    )
    def study_intervention_delete(
        req: StudyInterventionDeleteBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Delete a named intervention.

        Body: ``{study|investigation, name}``
        """
        body, status = _study_crud_mut.study_intervention_delete(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-run-delete",
        tags=["Studies"],
        summary="Delete one run from runs.db and study.yaml",
    )
    def study_run_delete(
        req: StudyRunDeleteBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Remove a single run by run_id (from runs.db and spec.runs[]).

        Body: ``{name|study|investigation, run_id}``
        """
        body, status = _study_crud_mut.study_run_delete(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-runs-clear",
        tags=["Studies"],
        summary="Delete all runs from runs.db and study.yaml",
    )
    def study_runs_clear(
        req: StudyRunsClearBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Remove all runs for a study (truncates runs.db and empties spec.runs[]).

        Body: ``{name|study|investigation}``
        """
        body, status = _study_crud_mut.study_runs_clear(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-comparison-add",
        tags=["Studies"],
        summary="Add a named comparison (run_ids set) to study.yaml",
    )
    def study_comparison_add(
        req: StudyComparisonAddBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Append a named comparison grouping of run_ids.

        Body: ``{name|study|investigation, run_ids: [str, ...], name?}``
        At least 2 run_ids required.
        """
        body, status = _study_crud_mut.study_comparison_add(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    # -----------------------------------------------------------------------
    # Batch 20: Study lifecycle + feedback (POST routes)
    # NOTE: CSRF guard is deferred to the state/flip batch — same as batches 18/19.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/feedback-apply-action",
        tags=["Studies"],
        summary="Apply a tracked feedback action (SP3b, AI-free)",
    )
    def feedback_apply_action(
        req: FeedbackApplyActionBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Apply a tracked SP3b feedback action via the pbg-superpowers primitive.

        Body: ``{item_id}``
        200 ``{applied: true, ...}`` on success; 400 when item_id is missing or
        the action target is not found; 404 on FileNotFoundError; 500 when
        pbg-superpowers is not installed.
        """
        body, status = _lifecycle_mut.feedback_apply_action(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-create-from-run",
        tags=["Studies"],
        summary="Create a new Study from a scratchpad composite run",
    )
    def study_create_from_run(
        req: StudyCreateFromRunBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Promote a scratchpad composite run into a new named study.

        Body: ``{name, source_run_id, objective?, description?}``
        400 when name/source_run_id is missing or name is invalid; 404 when
        source_run_id is not in the scratchpad DB; 409 when a study with that
        name already exists; 200 ``{study, url}`` on success.
        """
        body, status = _lifecycle_mut.study_create_from_run(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-rename",
        tags=["Studies"],
        summary="Rename a study directory and update study.yaml",
    )
    def study_rename(
        req: StudyRenameBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Rename a study directory and patch the name field in study.yaml.

        Body: ``{study, new_name}``
        400 when study/new_name is missing or new_name is not a valid slug; 404
        when the study directory does not exist; 409 when new_name already exists;
        200 ``{ok: true, name: new_name}`` on success.
        """
        body, status = _lifecycle_mut.study_rename(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-sync-runs",
        tags=["Studies"],
        summary="Reconcile a study's runs.db into study.yaml runs[]",
    )
    def study_sync_runs(
        req: StudySyncRunsBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Sync a study's runs.db records into study.yaml runs[] and roll up outcomes.

        Body: ``{study}``
        400 when study slug is missing; 404 when the study directory is not found;
        200 ``{ok: true, summary: {...}}`` on success.
        """
        body, status = _lifecycle_mut.study_sync_runs(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/proposed-input-decision",
        tags=["Studies"],
        summary="Accept or decline an agent-proposed input",
    )
    def proposed_input_decision(
        req: ProposedInputDecisionBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Apply an expert accept/decline decision to a proposed_inputs item.

        Body: ``{investigation, item_id, decision}``
        On ``accept`` + ``kind: reference``, promotes the item into
        ``inputs.references``.  400 when required fields are missing or decision
        is invalid; 404 when the investigation.yaml or item is not found; 200
        ``{ok: true, item_id, kind, status, ...}`` on success.
        """
        body, status = _lifecycle_mut.decide_proposed_input(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-seed-followup",
        tags=["Studies"],
        summary="Seed a child study from a parent's followup/finding",
    )
    def study_seed_followup(
        req: StudySeedFollowupBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Seed a new child study from a parent study's followup or finding.

        Body: ``{parent, finding_id?, followup_idx?, proposal_id?, proposal_idx?, study_type?}``
        Routes through the four unified followup field families (finding_id wins).
        400 on bad args; 404 when parent not found; 500 when pbg-superpowers
        is unavailable; 200 ``{new_study_name, new_slug}`` on success.
        """
        body, status = _lifecycle_mut.study_seed_followup(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    # -----------------------------------------------------------------------
    # Batch 21: Investigation scaffold (POST routes)
    # NOTE: CSRF guard is deferred to the state/flip batch — same as batches 18-20.
    # investigation-delete calls the lib builder directly (no _active_branch_action
    # here — the commit is deferred to the flip/state batch).
    # -----------------------------------------------------------------------

    @app.post(
        "/api/investigation-create",
        tags=["Investigations"],
        summary="Scaffold a new investigation.yaml",
    )
    def investigation_create(
        req: InvestigationCreateRequest,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Create a new ``investigation.yaml`` under ``investigations/<name>/``.

        Body: ``{name, overview?, parent_studies?}``
        Slug must match ``^[a-z0-9][a-z0-9-]*$``. Atomic write (tmp+rename).
        400 when name is missing or invalid; 409 when the investigation already
        exists; 200 returns the new investigation in the same shape as
        ``GET /api/investigation/<name>``.
        """
        body, status = _scaffold_mut.investigation_create(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-clone",
        tags=["Investigations"],
        summary="Clone an investigation into a fresh planning state",
    )
    def investigation_clone(
        req: IsetCloneBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Clone an existing investigation via the workspace's clone script.

        Body: ``{source, target, source_prefix?, target_prefix?}``
        Delegates to ``scripts/clone_investigation.py``; returns the new
        investigation in the same shape as ``GET /api/investigation/<target>`` with an
        extra ``clone_summary`` field describing the study remap.
        400 when slugs are missing or invalid; 404 when source not found;
        409 when target already exists; 501 when clone script is absent.
        """
        body, status = _scaffold_mut.iset_clone(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-delete",
        tags=["Investigations"],
        summary="Delete an investigation directory (no git commit on the FastAPI path)",
    )
    def investigation_delete(
        req: InvestigationDeleteBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Remove an investigation directory (pure rmtree, no git commit).

        Body: ``{name}`` (also accepts ``study`` / ``investigation`` aliases).
        400 when name is missing; 404 when the investigation directory does not
        exist; 200 ``{ok: true, name: <slug>}`` on success.

        Note: the live server path commits the deletion via
        ``_active_branch_action``; this FastAPI route calls the lib builder
        directly (git commit deferred to the flip/state batch).
        """
        body, status = _scaffold_mut.delete_investigation(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    # -----------------------------------------------------------------------
    # Batch 22: Investigation comparisons & groups (POST routes)
    # NOTE: CSRF guard is deferred to the state/flip batch — same as batches 18-21.
    # The live server shim still routes through _commit_or_run; these FastAPI
    # routes call the lib builder directly (commit deferred to the flip batch).
    # -----------------------------------------------------------------------

    @app.post(
        "/api/investigation-comparison-add",
        tags=["Investigations"],
        summary="Append a comparison entry to an investigation's spec.yaml",
    )
    def investigation_comparison_add(
        req: InvestigationComparisonAddBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Append a comparison entry to ``spec.yaml.comparisons``.

        Body: ``{investigation, name, variants[], observables[], description?}``
        (``study`` accepted as alias for ``investigation``.)
        400 when required fields are missing/invalid; 404 when investigation
        not found; 409 when the comparison name already exists; 200
        ``{ok: true}`` on success.

        Note: the live server path commits via ``_commit_or_run``; this
        FastAPI route calls the lib builder directly (commit deferred).
        """
        body, status = _compare_grp_mut.comparison_add(ws, req.model_dump(exclude_unset=True))
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-comparison-update",
        tags=["Investigations"],
        summary="Replace fields on an existing investigation comparison",
    )
    def investigation_comparison_update(
        req: InvestigationComparisonUpdateBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Replace fields on an existing ``spec.yaml.comparisons`` entry.

        Body: ``{investigation, name, fields_to_update}``
        Only ``description``, ``variants``, and ``observables`` may be updated;
        ``name`` is immutable.
        400 when required fields are missing/invalid; 404 when investigation
        or comparison not found; 200 ``{ok: true}`` on success.

        Note: the live server path commits via ``_commit_or_run``; this
        FastAPI route calls the lib builder directly (commit deferred).
        """
        body, status = _compare_grp_mut.comparison_update(ws, req.model_dump(exclude_unset=True))
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-group-add",
        tags=["Investigations"],
        summary="Append a group entry to an investigation's spec.yaml",
    )
    def investigation_group_add(
        req: InvestigationGroupAddBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Append a group entry to ``spec.yaml.groups``.

        Body: ``{investigation, name, variants[], description?}``
        Every entry in ``variants`` must reference a name declared in
        ``spec.variants``; 400 with ``unknown variant(s)`` when not.
        400 when required fields are missing/invalid; 404 when investigation
        not found; 409 when the group name already exists; 200
        ``{ok: true}`` on success.

        Note: the live server path commits via ``_commit_or_run``; this
        FastAPI route calls the lib builder directly (commit deferred).
        """
        body, status = _compare_grp_mut.group_add(ws, req.model_dump(exclude_unset=True))
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-group-update",
        tags=["Investigations"],
        summary="Replace fields on an existing investigation group",
    )
    def investigation_group_update(
        req: InvestigationGroupUpdateBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Replace fields on an existing ``spec.yaml.groups`` entry.

        Body: ``{investigation, name, fields_to_update}``
        Only ``description`` and ``variants`` may be updated; ``name`` is
        immutable.  When replacing ``variants``, each entry must reference a
        name declared in ``spec.variants``; 400 with ``unknown variant(s)``
        when not; 400 when the replacement list is empty.
        400 when required fields are missing/invalid; 404 when investigation
        or group not found; 200 ``{ok: true}`` on success.

        Note: the live server path commits via ``_commit_or_run``; this
        FastAPI route calls the lib builder directly (commit deferred).
        """
        body, status = _compare_grp_mut.group_update(ws, req.model_dump(exclude_unset=True))
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    # -----------------------------------------------------------------------
    # Batch 23: Visualization file-write (POST routes)
    # NOTE: CSRF guard is deferred to the state/flip batch — same as batches 18-22.
    # No git commit, no _commit_or_run — simplest POST shape (plain file writes).
    # -----------------------------------------------------------------------

    @app.post(
        "/api/visualization-create",
        tags=["Visualizations"],
        summary="Write a viz-request .md file for /pbg-viz (old-contract)",
    )
    def visualization_create(
        req: VisualizationCreateBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Write a ``.pbg/viz-requests/<name>.md`` file from workspace context.

        Body: ``{name}``
        The visualization must already be registered in ``workspace.yaml`` with
        a non-empty ``description``. Writes the request file and returns
        ``{ok, request_path, skill_command, instructions}``.
        400 when name is invalid or the description is empty; 404 when the
        visualization is not registered; 200 on success.

        Note: no git commit — both source and dest are gitignored.
        Delegates to ``lib.viz_write_mutations.visualization_create``.
        """
        body, status = _viz_write_mut.visualization_create(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/visualization-add-to-project",
        tags=["Visualizations"],
        summary="Stage a skill viz response for commit",
    )
    def visualization_add_to_project(
        req: VisualizationAddToProjectBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Copy ``.pbg/viz-responses/<name>.py`` to ``.pbg/visualizations-staged/<name>.py``.

        Body: ``{name}``
        Does NOT commit — both source and dest are gitignored.  The staged file
        is picked up by ``/api/visualization-commit-batch`` in the next step.
        400 when name is missing; 404 when no skill response exists yet; 200
        ``{ok, staged_path}`` on success.

        Delegates to ``lib.viz_write_mutations.visualization_add_to_project``.
        """
        body, status = _viz_write_mut.visualization_add_to_project(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/visualization-generate",
        tags=["Visualizations"],
        summary="Write a new-contract viz-request file for /pbg-viz",
    )
    def visualization_generate(
        req: VisualizationGenerateBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Write a new-contract ``.pbg/viz-requests/<name>.md`` request file.

        Body: ``{name, description}``
        The /pbg-viz skill reads the request and writes a ``@as_visualization``
        decorated function to ``<pkg>/visualizations/<snake>.py``.
        400 when name is invalid or description is empty; 200
        ``{ok, request_path, target_file, skill_command, instructions}`` on
        success.

        Delegates to ``lib.viz_write_mutations.visualization_generate``.
        """
        body, status = _viz_write_mut.visualization_generate(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    # -----------------------------------------------------------------------
    # Batch 24: Visualization commit (POST routes)
    # NOTE: CSRF guard is deferred to the state/flip batch — same as batches 18-23.
    # The live server shim routes through _active_branch_action; these FastAPI
    # routes call the lib builder directly (commit deferred to the flip batch).
    # -----------------------------------------------------------------------

    @app.post(
        "/api/observable",
        tags=["Visualizations"],
        summary="Register an observable in workspace.yaml (no git commit on the FastAPI path)",
    )
    def observable_add(
        req: ObservableAddBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Add an observable entry to ``workspace.yaml.observables``.

        Body: ``{name, store_path, units?, description?}``
        400 when name or store_path is missing; 409 when the observable name
        already exists; 200 ``{ok: true}`` on success.

        Note: the live server path commits via ``_active_branch_action``; this
        FastAPI route calls the lib builder directly (git commit deferred to
        the flip/state batch).

        Delegates to ``lib.viz_commit_mutations.observable_add``.
        """
        body, status = _viz_commit_mut.observable_add(ws, req.model_dump(exclude_unset=True))
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/visualization",
        tags=["Visualizations"],
        summary="Register a visualization in workspace.yaml (no git commit on the FastAPI path)",
    )
    def visualization_add(
        req: VisualizationAddBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Add a visualization entry to ``workspace.yaml.visualizations``.

        Body: ``{name, description?, class?, type?, observables?, config?, simulation?}``
        400 when name is missing/invalid, class is unregistered, or type/observables
        fail structured-path validation; 409 when the name already exists; 200
        ``{ok: true}`` on success.

        Note: the live server path commits via ``_active_branch_action``; this
        FastAPI route calls the lib builder directly (git commit deferred to
        the flip/state batch).

        Delegates to ``lib.viz_commit_mutations.visualization_add``.
        """
        # Preserve alias so body.get("class") resolves correctly in the lib builder.
        body = req.model_dump(by_alias=True, exclude_unset=True)
        status_body, status = _viz_commit_mut.visualization_add(ws, body)
        if status != 200:
            return JSONResponse(status_code=status, content=status_body)
        return status_body

    @app.post(
        "/api/visualization-commit-batch",
        tags=["Visualizations"],
        summary="Move staged visualizations to the workspace package (no git commit on the FastAPI path)",
    )
    def visualization_commit_batch(
        req: VisualizationCommitBatchBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Move all staged ``.pbg/visualizations-staged/*.py`` files into the
        workspace package's ``visualizations/`` directory.

        Body: ``{names?: list[str]}`` — if omitted, commits all staged files.
        404 when no staged visualizations exist or none match the requested names;
        200 ``{ok: true, committed: [names]}`` on success.

        Note: the live server path commits via ``_active_branch_action``; this
        FastAPI route calls the lib builder directly (git commit deferred to
        the flip/state batch).

        Delegates to ``lib.viz_commit_mutations.visualization_commit_batch``.
        """
        body, status = _viz_commit_mut.visualization_commit_batch(ws, req.model_dump(exclude_unset=True))
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/visualization-accept",
        response_model=VisualizationAcceptResponse,
        tags=["Visualizations"],
        summary="Finalize a generated visualization file (no git commit on the FastAPI path)",
    )
    def visualization_accept(
        req: VisualizationAcceptBody,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Finalize a generated visualization: invalidate the registry cache,
        verify the generated file imports cleanly in-process, smoke-test the
        workspace ``build_core()``, and confirm the class is discoverable.

        Body: ``{name, class_name?}``.

        Status codes (byte-identical to the legacy handler's pre-wrapper steps):
          - 200  ``{"ok": True}``
          - 400  ``{"error": "name is required"}``
          - 404  ``{"error": "generated file not found at <rel>"}``
          - 500  ``{"error": "generated file failed to import: ..."}``
          - 500  ``{"error": "workspace build_core() failed after importing ..."}``
          - 500  ``{"error": "class <name> not found in generated file after import; ..."}``

        Note: the live server path commits the no-op via ``_active_branch_action``;
        this FastAPI route defers that git commit to the flip and returns
        ``{ok: true}`` on success.  The CSRF middleware already guards this POST.

        Library-backed via the pure ``lib.viz_accept_views.visualization_accept``;
        every path is wrapped in ``JSONResponse`` so the lib-returned status code
        is preserved verbatim.
        """
        resp, status = _viz_accept_views.visualization_accept(
            ws, req.model_dump(exclude_unset=True)
        )
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/visualization-preview",
        tags=["Visualizations"],
        summary="Render a visualization class in-process against demo or investigation data",
    )
    def visualization_preview(
        req: VisualizationPreviewRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Render a ``pbg_superpowers`` Visualization class IN-PROCESS (instantiate
        + call ``.update()``; NO subprocess) against synthetic demo data or an
        existing investigation's emitter outputs.

        Body: ``{"address", "config"?, "source"?}`` — ``source`` is ``"demo"``
        (default) or ``"investigation:<name>"``.  Returns ``{ok, html,
        source_used, notes}``.

        Status codes (byte-identical to the legacy handler, via
        ``lib.viz_preview_views.visualization_preview``):
          - 400  ``{"error": "address is required"}``
          - 404  ``{"error": "class not registered: <address>"}``
          - 200  demo / investigation render success (``{ok: true, …}``) OR a
            demo render that RAISES (``{ok: false, html: "<…demo render
            failed…>", …}``) — only validation is non-200.

        ``model_dump(exclude_none=True)`` keeps omitted optionals absent so the
        builder's ``.get(...)`` defaults apply.  The CSRF middleware already
        guards this POST.  Every path is wrapped in ``JSONResponse`` so the
        lib-returned status code is preserved verbatim.
        """
        resp, status = _viz_preview_views.visualization_preview(
            ws, req.model_dump(exclude_none=True)
        )
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/visualization-preview-instance",
        tags=["Visualizations"],
        summary="Preview a workspace.yaml-registered visualization instance by name",
    )
    def visualization_preview_instance(
        req: VisualizationPreviewInstanceRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Preview a ``workspace.yaml``-registered visualization instance BY NAME.

        Body: ``{"name", "source"?}``.  Looks up the instance entry and either
        returns a description-only **stub** (entry has no ``class``) or delegates
        to ``lib.viz_preview_views.visualization_preview`` with ``{"address":
        f"local:{cls}", "config": …, "source": …}`` (entry has a class).  NO
        network, NO subprocess.

        Status codes (byte-identical to the legacy handler, via
        ``lib.viz_preview_instance_views.visualization_preview_instance``):
          - 400  ``{"error": "name is required"}``
          - 404  ``{"error": "visualization '<name>' not registered"}``
          - 200  description-only stub (``{ok: true, source_used: "stub", …}``)
            OR whatever the delegated ``visualization_preview`` returns.

        ``model_dump(exclude_none=True)`` keeps omitted optionals absent so the
        builder's ``.get(...)`` defaults apply.  The CSRF middleware already
        guards this POST.  Every path is wrapped in ``JSONResponse`` so the
        lib-returned status code is preserved verbatim.
        """
        resp, status = _viz_preview_instance_views.visualization_preview_instance(
            ws, req.model_dump(exclude_none=True)
        )
        return JSONResponse(status_code=status, content=resp)

    # -----------------------------------------------------------------------
    # Batch 25: Upload / import (POST routes)
    # NOTE: CSRF guard is deferred to the state/flip batch — same as batches 18-24.
    # The live server shim routes through _active_branch_action; these FastAPI
    # routes call the lib builder directly (commit deferred to the flip batch).
    # -----------------------------------------------------------------------

    @app.post(
        "/api/dataset",
        tags=["Data, inputs & references"],
        summary="Register a dataset in workspace.yaml or an investigation (no git commit on the FastAPI path)",
    )
    def dataset_upload(
        req: DatasetUploadBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Save a dataset (file/path/url forms) + register it.

        Body: ``{name, claims?, file_b64?, filename?, path?, url?, sha256?, investigation?}``
        For ``file_b64`` the file is written under ``datasets/<slug>/<filename>``
        (or ``investigations/<inv>/inputs/datasets/<slug>/<filename>``) and its
        SHA256 recorded; for ``path`` the SHA256 is computed when the file
        exists; for ``url`` an optional ``sha256`` is stored.
        400 on missing name / invalid investigation slug / missing filename /
        no source; 404 when the investigation is not found; 409 when the dataset
        name already exists; 200 ``{ok: true}`` on success.

        Note: the live server path commits via ``_active_branch_action``; this
        FastAPI route calls the lib builder directly (git commit deferred to
        the flip/state batch).

        Delegates to ``lib.upload_mutations.register_dataset``.
        """
        body, status = _upload_mut.register_dataset(ws, req.model_dump(exclude_unset=True))
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/expert-doc",
        tags=["Data, inputs & references"],
        summary="Register an expert document in workspace.yaml or an investigation (no git commit on the FastAPI path)",
    )
    def expert_doc_upload(
        req: ExpertDocUploadBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Save an expert document (PDF/markdown) + register it.

        Body: ``{name, file_b64?, filename?, source_path?, description?,
        contributor?, claims_supported?, investigation?}`` — one of
        ``file_b64``+``filename`` or ``source_path`` is required.  The file is
        written under ``references/expert/<slug><ext>`` (or
        ``investigations/<inv>/inputs/expert/<slug><ext>``) and its SHA256
        recorded.
        400 on invalid investigation slug / missing name / no source / missing
        filename / bad source_path; 404 when the investigation is not found;
        409 when the expert doc name already exists; 200 ``{ok: true}`` on
        success.

        Note: the live server path commits via ``_active_branch_action``; this
        FastAPI route calls the lib builder directly (git commit deferred to
        the flip/state batch).

        Delegates to ``lib.upload_mutations.register_expert_doc``.
        """
        body, status = _upload_mut.register_expert_doc(ws, req.model_dump(exclude_unset=True))
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/import",
        tags=["Data, inputs & references"],
        summary="Register an import in workspace.yaml.imports (no git commit on the FastAPI path)",
    )
    def import_register(
        req: ImportRegisterBody,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Register an import in ``workspace.yaml.imports``.

        Body: ``{name, source, ref, mode, description?}`` — ``mode`` is one of
        ``reference``, ``fork-source``, ``in-place``.  git submodule add is NOT
        performed (requires terminal for network/auth); the response carries
        ``next_terminal_step`` + ``note`` with the exact command to run.
        400 on missing required fields / invalid mode / invalid name chars;
        409 when the import name already exists; 200
        ``{ok: true, next_terminal_step, note}`` on success.

        Note: the live server path commits via ``_active_branch_action``; this
        FastAPI route calls the lib builder directly (git commit deferred to
        the flip/state batch).

        Delegates to ``lib.upload_mutations.register_import_entry``.
        """
        body, status = _upload_mut.register_import_entry(ws, req.model_dump(exclude_unset=True))
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    # -----------------------------------------------------------------------
    # Batch 26: References (POST routes)
    # NOTE: CSRF guard is deferred to the state/flip batch — same as batches 18-25.
    # The live server shim routes through _active_branch_action; these FastAPI
    # routes call the lib builder directly (commit deferred to the flip batch).
    # The lib builder reads via ``body.get(...)``, so a plain ``model_dump()``
    # (unset → None) is correct — no ``exclude_unset`` presence semantics here.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/reference-pdf",
        tags=["Data, inputs & references"],
        summary="Add a paper reference from a PDF (drop-and-go; no git commit on the FastAPI path)",
    )
    def reference_pdf(
        req: ReferencePdf,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Drop-and-go PDF reference flow.

        Body: ``{pdf_b64, title?, authors?, year?, journal?, doi?, bib_key?,
        investigation?, claim_mappings?}``.  pypdf extracts title/authors/year
        from the PDF; the typed fields override it.  Saves the PDF under
        ``references/papers/<bib_key>.pdf`` (or
        ``investigations/<inv>/inputs/references/<bib_key>.pdf``), appends a
        BibTeX entry to ``references/papers.bib``, registers the PDF in
        ``workspace.yaml.references_pdfs`` (with ``_metadata_pending`` when the
        metadata is incomplete), optionally registers the bare key in an
        investigation, and merges claim mappings into ``claims.yaml``.

        400 on missing ``pdf_b64`` / invalid investigation slug / invalid
        ``bib_key``; 404 when the investigation is not found; 409 when the
        BibTeX key already exists in papers.bib; 200
        ``{ok: true, bib_key, metadata_pending, extracted}`` on success.

        Note: the live server path commits via ``_active_branch_action``; this
        FastAPI route calls the lib builder directly (git commit deferred to
        the flip/state batch).

        Delegates to ``lib.reference_mutations.register_reference_pdf``.
        """
        body, status = _reference_mut.register_reference_pdf(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    def _reference_bibtex(req: ReferenceBibtex, ws: Path) -> dict:
        """Shared handler for /api/reference-bibtex and its /api/reference alias."""
        body, status = _reference_mut.register_reference(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/reference-bibtex",
        tags=["Data, inputs & references"],
        summary="Add a paper reference from pasted BibTeX (no git commit on the FastAPI path)",
    )
    def reference_bibtex(
        req: ReferenceBibtex,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """BibTeX-paste reference flow.

        Body: ``{bibtex_text, claim_mappings?, pdf_b64?, investigation?}``.
        Parses the bib key from the pasted entry and appends it to
        ``references/papers.bib`` (an investigation-scoped request MAY reuse an
        existing key; the global flow treats a duplicate as a conflict),
        optionally registers the bare key in an investigation, merges claim
        mappings into ``claims.yaml``, and optionally saves a PDF + registers
        it in ``workspace.yaml.references_pdfs``.

        400 on missing ``bibtex_text`` / unparseable key / invalid investigation
        slug; 404 when the investigation is not found; 409 when the BibTeX key
        already exists in papers.bib (global flow only); 200 ``{ok: true}`` on
        success.

        Note: the live server path commits via ``_active_branch_action``; this
        FastAPI route calls the lib builder directly (git commit deferred to
        the flip/state batch).

        Delegates to ``lib.reference_mutations.register_reference``.
        """
        return _reference_bibtex(req, ws)

    @app.post(
        "/api/reference",
        tags=["Data, inputs & references"],
        summary="Add a paper reference from pasted BibTeX (legacy alias of /api/reference-bibtex)",
    )
    def reference(
        req: ReferenceBibtex,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Legacy alias of ``POST /api/reference-bibtex`` — identical behaviour.

        Both paths map to the same ``_post_reference`` handler in the live
        server; here both call ``lib.reference_mutations.register_reference``.
        """
        return _reference_bibtex(req, ws)

    # -----------------------------------------------------------------------
    # Batch 27: investigation-composite POST writers (Composites tag)
    # -----------------------------------------------------------------------

    @app.post(
        "/api/investigation-composite-add",
        tags=["Composites"],
        summary="Add a composite to a study from a workspace source/generator (no git commit on the FastAPI path)",
    )
    def investigation_composite_add(
        req: InvestigationCompositeAdd,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Clone a registered workspace composite into a study.

        Body: ``{investigation, name, source}``.  ``source`` resolves to a YAML
        source path on disk OR a registered ``@composite_generator`` (the latter
        is materialized to a concrete doc).  Writes
        ``investigations/<inv>/composites/<name>.yaml`` and appends a spec
        ``composites`` entry.

        400 on missing fields / generator-not-serializable; 404 on unknown
        source / investigation not found; 409 when the composite name already
        exists; 200 ``{ok: true}`` on success.

        Note: the live server path commits via ``_commit_or_run``; this FastAPI
        route calls the lib builder directly (git commit deferred to the flip
        batch).  Delegates to
        ``lib.composite_mutations.add_investigation_composite``.
        """
        body, status = _composite_mut.add_investigation_composite(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-composite-perturb",
        tags=["Composites"],
        summary="Derive a composite variant by applying overrides (no git commit on the FastAPI path)",
    )
    def investigation_composite_perturb(
        req: InvestigationCompositePerturb,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Derive a composite from an existing sidecar by applying overrides.

        Body: ``{investigation|study, name, extends, description?,
        parameter_overrides?, process_overrides?}``.  Deep-copies the parent
        sidecar, applies the parameter/process overrides, writes the derived
        sidecar, and upserts a v2 ``variants`` entry (replacing an existing
        same-named variant in-place — no 409).

        400 on missing fields / override KeyError; 404 when the investigation or
        parent composite is not found; 500 on a non-KeyError override failure;
        200 ``{ok: true}`` on success.

        Note: the live server path commits via ``_commit_or_run``; this FastAPI
        route calls the lib builder directly (git commit deferred to the flip
        batch).  Delegates to
        ``lib.composite_mutations.perturb_investigation_composite``.
        """
        body, status = _composite_mut.perturb_investigation_composite(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/composite-promote-to-catalog",
        tags=["Composites"],
        summary="Promote an investigation variant into the workspace catalog (no git commit on the FastAPI path)",
    )
    def composite_promote_to_catalog(
        req: CompositePromoteToCatalog,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Promote a variant's sidecar into the workspace composite catalog.

        Body: ``{investigation, variant, target_name?, description?}``.  Writes
        ``<pkg>/composites/<target_name>.composite.yaml`` (with the doc's
        ``name`` set to ``target_name`` and, if provided, ``description`` set)
        and marks the variant ``promoted: true`` in the spec.  Non-destructive.

        400 on missing fields; 404 when the investigation or variant sidecar is
        not found; 409 when the catalog entry already exists; 500 if
        ``workspace.yaml`` is unreadable; 200
        ``{ok: true, name: <target>, path: <relative>}`` on success.

        Note: the live server path commits via ``_commit_or_run``; this FastAPI
        route calls the lib builder directly (git commit deferred to the flip
        batch).  Delegates to
        ``lib.composite_mutations.promote_composite_to_catalog``.
        """
        body, status = _composite_mut.promote_composite_to_catalog(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-composite-rebuild",
        tags=["Composites"],
        summary="Rebuild a derived composite from its recipe (no git commit on the FastAPI path)",
    )
    def investigation_composite_rebuild(
        req: InvestigationCompositeRebuild,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Re-render a derived composite by re-applying its recipe overrides.

        Body: ``{investigation, name}``.  Looks up the ``composites`` entry,
        re-applies the recorded parameter/process overrides on the current
        parent document, and rewrites the derived sidecar.

        400 on missing fields / not-derived (no ``extends``) / override
        KeyError; 404 when the investigation, composite, or parent document is
        not found; 500 on a non-KeyError override failure; 200 ``{ok: true}`` on
        success.

        Note: the live server path commits via ``_commit_or_run``; this FastAPI
        route calls the lib builder directly (git commit deferred to the flip
        batch).  Delegates to
        ``lib.composite_mutations.rebuild_investigation_composite``.
        """
        body, status = _composite_mut.rebuild_investigation_composite(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/study-create-from-composite",
        tags=["Studies"],
        summary="Clone a workspace-catalog composite into a fresh study (no git commit on the FastAPI path)",
    )
    def study_create_from_composite(
        req: StudyCreateFromComposite,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Clone a workspace-catalog composite into a fresh study.

        Body: ``{composite_name}``.  Matched against the catalog record's
        ``name`` first, then the dotted-id stem.  Creates ``studies/<auto>/``
        with a v2-shape ``spec.yaml`` (uuid auto-named
        ``study-<slug>-<6-hex>``) and copies the resolved source YAML to
        ``./composites/<composite_name>.yaml``.

        400 on missing ``composite_name`` / generator build failure; 404 when
        the composite is not in the catalog / generator not registered / source
        missing; 409 on an auto-name collision; 500 on lookup/catalog/workspace
        failures; 200 ``{name: <auto>}`` on success.

        Note: the live server path commits via ``_commit_or_run``; this FastAPI
        route calls the lib builder directly (git commit deferred to the flip
        batch).  Delegates to
        ``lib.composite_mutations.create_from_composite``.
        """
        body, status = _composite_mut.create_from_composite(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-add-viz",
        tags=["Investigations"],
        summary="Append a visualization entry to a study's spec (no git commit on the FastAPI path)",
    )
    def investigation_add_viz(
        req: InvestigationAddViz,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Append a visualization entry to a study's ``spec.yaml``.

        Body: ``{investigation, name, address, config?}``.  Validates the
        viz-name regex ``^[a-zA-Z0-9_-]+$`` and appends ``{name, address,
        config}`` to the spec's ``visualizations`` list.

        400 on missing fields / bad viz-name; 404 when the investigation is not
        found; 409 when a visualization with that name already exists; 200
        ``{ok: true, investigation, viz_name}`` on success.

        Note: the live server path commits via ``_active_branch_action`` (a
        duplicate raises → 500 there); this FastAPI route calls the lib builder
        directly and returns the precise 409 (git commit deferred to the flip
        batch).  Delegates to
        ``lib.investigation_viz_mutations.add_viz``.
        """
        body, status = _inv_viz_mut.add_viz(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    @app.post(
        "/api/investigation-render-viz",
        tags=["Investigations"],
        summary="Re-render a study's visualizations against existing emitter data (no sim re-run)",
    )
    def investigation_render_viz(
        req: InvestigationRenderViz,
        ws: Path = Depends(get_workspace),
    ) -> dict:
        """Re-render a study's declared visualizations. No simulation re-run.

        Body: ``{name}``.  Builds the workspace ``<pkg>.core``, augments the
        link registry with pbg_superpowers viz classes, and runs each viz doc
        through a ``process_bigraph.Composite``.

        400 on missing ``name`` / spec error; 404 when the investigation is not
        found; 500 on build-core / render failure; 200 ``{ok: true,
        investigation, n_visualizations, viz_paths}`` on success.

        No commit wrapper in the live server — a plain no-commit render.
        Delegates to ``lib.investigation_viz_mutations.render_viz``.
        """
        body, status = _inv_viz_mut.render_viz(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body

    # -----------------------------------------------------------------------
    # Job status routes (read-only, in-memory manager singletons)
    # -----------------------------------------------------------------------

    @app.get(
        "/api/investigation-run-unblocked-status",
        response_model=JobStatusPayload,
        tags=["Rigor & jobs"],
        summary="Status of an investigation-wide multi-variant run job",
    )
    def investigation_run_unblocked_status(
        job_id: str = "",
    ) -> Union[JobStatusPayload, JSONResponse]:
        """Status of an investigation run-unblocked job (mirrors the stdlib
        ``GET /api/investigation-run-unblocked-status?job_id=<id>``).

        Reads the in-process ``lib.run_jobs.manager`` singleton.  No ``job_id``
        returns ``{jobs: manager.list_recent(10)}``; a known id returns the
        job's ``to_dict()`` (an ``items[]`` shape); an unknown id returns HTTP
        404 ``{error: "job not found"}``.

        The manager is read at call time via the module attribute
        (``_run_jobs.manager``) so tests can monkeypatch it.  Library-backed via
        the pure ``lib.job_status_views.job_status``.
        """
        body, status = _job_status_views.job_status(_run_jobs.manager, job_id)
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return JobStatusPayload.model_validate(body)

    @app.get(
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
    # Source — in-process workspace re-pointing (first stateful POST)
    # -----------------------------------------------------------------------

    @app.post(
        "/api/source/switch",
        response_model=SourceSwitchResponse,
        tags=["Workspaces & sources"],
        summary="Re-point the active workspace in-process",
    )
    def source_switch(
        req: SourceSwitchRequest,
    ) -> Union[SourceSwitchResponse, JSONResponse]:
        """Re-point the active workspace to a registered catalog entry.

        Mirrors the stdlib ``POST /api/source/switch``.  Body: ``{"path": <dir>}``
        — the path MUST resolve to a registered ``workspace_catalog`` entry (no
        arbitrary paths).  On success the lib-side switch fires (sets
        ``lib._root`` + invalidates the lib caches via
        ``active_workspace.switch_workspace``) and returns ``{ok, source}``; the
        client reloads.

        Status codes (byte-identical to the legacy handler):
          - 400  missing ``path`` (``{"error": "missing 'path'"}``)
          - 400  unregistered path (``{"error": "<path> is not a registered workspace"}``)
          - 200  ``{ok: true, source: {path, name}}``

        The CSRF middleware already guards this POST.  Library-backed via
        ``lib.source_switch_views.source_switch``.
        """
        body, status = _source_switch_views.source_switch(req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return SourceSwitchResponse.model_validate(body)

    @app.post(
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

    @app.post(
        "/api/source/switch-build",
        response_model=SourceSwitchResponse,
        tags=["Workspaces & sources"],
        summary="Materialize a remote build's workspace and re-point in-process",
    )
    def source_switch_build(
        req: SwitchBuildRequest,
    ) -> Union[SourceSwitchResponse, JSONResponse]:
        """Materialize a remote build's workspace (cached) and re-point to it.

        Mirrors the stdlib ``POST /api/source/switch-build``.  Body:
        ``{"simulator_id"}`` — looks the build up in the sms-api listing,
        downloads+extracts its workspace once (cached per commit), stamps build
        provenance into the cache dir (best-effort), then fires the lib-side
        switch (``active_workspace.switch_workspace`` — sets ``lib._root`` +
        invalidates the lib caches) and returns ``{ok, source}``.

        Status codes (byte-identical to the legacy handler):
          - 400  missing ``simulator_id`` (``{"error": "missing 'simulator_id'"}``)
          - 502  sms-api unreachable (``{"error": "sms-api unavailable: <err>"}``)
          - 404  build not found (``{"error": "build <id> not found"}``)
          - 502  materialize failed (``{"error": "materialize failed: <err>"}``)
          - 200  ``{ok: true, source: {path, name}}``

        The CSRF middleware already guards this POST.  Library-backed via
        ``lib.source_build_views.switch_build`` (a network route — sms-api is
        reached through the lib client).
        """
        body, status = _source_build_views.switch_build(req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return SourceSwitchResponse.model_validate(body)

    # -----------------------------------------------------------------------
    # Study runs — local study-run engine + workspace/study test runners
    #
    # The 3 study-run routes call the already-extracted study-run engine
    # orchestrators (``lib.study_runs.run_study_*`` — phase E4) directly; the 2
    # test routes call the P1 pure builders (``lib.test_run_views``).  Every
    # route returns large, variable result dicts (and 400/404/409/422/500 error
    # shapes), so every path is wrapped in ``JSONResponse`` to preserve the
    # lib-returned status code verbatim (a declared response_model would force
    # 200 / coerce the variable shapes).  The CSRF middleware already guards
    # these POSTs.  The request models use ``model_dump(exclude_none=True)`` so
    # an OMITTED optional (e.g. ``steps``) stays absent and the lib builders'
    # ``.get(...)`` defaults apply.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/study-run-baseline",
        tags=["Studies"],
        summary="Run a study's baseline composite (local engine)",
    )
    def study_run_baseline(
        req: StudyRunBaselineRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Run a Study's baseline composite + post-run stages.

        Mirrors the stdlib ``POST /api/study-run-baseline``.  Body:
        ``{"study", "composite"?, "steps"?}`` — resolves the study, builds/runs
        the baseline composite subprocess, fires post-run side-effects (viz,
        post-run scripts, analyses, outcome sync), returns the run result dict.

        Status codes (byte-identical to the legacy handler, via
        ``lib.study_runs.run_study_baseline``):
          - 400  missing study / baseline entry has no composite
          - 404  study not found / requested baseline composite not found
          - 200  run-result dict
        """
        body, status = _study_runs.run_study_baseline(ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    @app.post(
        "/api/study-run-variant",
        tags=["Studies"],
        summary="Run a study variant (single run or ensemble sweep)",
    )
    def study_run_variant(
        req: StudyRunVariantRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Run a study variant (single run) or delegate an ensemble sweep.

        Mirrors the stdlib ``POST /api/study-run-variant``.  Body:
        ``{"study", "variant", "steps"?}`` — runs the variant composite or, for
        a ``kind: seeds`` / ``kind: sweep`` variant, delegates an ensemble sweep
        to v2ecoli-workflow.

        Status codes (byte-identical to the legacy handler, via
        ``lib.study_runs.run_study_variant``):
          - 400  missing study/variant / baseline entry has no composite
          - 404  study / variant / base_composite not found
          - 422  ensemble misconfiguration (n_seeds / sweep_over / sweep target)
          - 200  run-result dict
        """
        body, status = _study_runs.run_study_variant(ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    @app.post(
        "/api/study-tests-run",
        tags=["Studies"],
        summary="Run pytest against a study's tests/ directory",
    )
    def study_tests_run(
        req: StudyTestsRunRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Run pytest against ``studies/<study>/tests/``.

        Mirrors the stdlib ``POST /api/study-tests-run``.  Body: ``{"study"}`` —
        returns ``{"summary", "tests", "note"}``.

        Status codes (byte-identical to the legacy handler, via
        ``lib.test_run_views.study_tests_run``):
          - 400  missing ``study`` (``{"error": "missing 'study' in body"}``)
          - 404  study not found (``{"error": "study not found: <slug>"}``)
          - 409  tests already running (``StudyTestsConcurrentError``)
          - 200  ``{summary, tests, note}``
        """
        body, status = _test_run_views.study_tests_run(ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    @app.post(
        "/api/run-tests",
        tags=["Studies"],
        summary="Run pytest for the whole workspace",
    )
    def run_tests(
        req: RunTestsRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Run pytest for the workspace's ``tests/`` directory (v0.3.0: no model
        param).

        Mirrors the stdlib ``POST /api/run-tests``.  Returns ``{"returncode",
        "stdout", "stderr"}`` (200) for a completed run.

        Status codes (byte-identical to the legacy handler, via
        ``lib.test_run_views.run_workspace_tests``):
          - 500  ``{"error": "pytest timed out after 120s"}`` (TimeoutExpired)
          - 500  ``{"error": "<exc>"}`` (any other exception)
          - 200  ``{returncode, stdout, stderr}``
        """
        body, status = _test_run_views.run_workspace_tests(ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # P2: composite-test-run — start a DETACHED composite run
    #
    # Writes a run-request file + a runs_meta row, spawns the run-composite CLI
    # detached via ``run_registry.spawn_detached``, and returns 202 {run_id}
    # immediately (the browser then polls /api/composite-run/<id>/status).  Like
    # the P1 routes it JSONResponses every path so the lib-returned status code
    # (202 happy / 400 missing-id / 429 at-cap / 500 spawn-failure) is preserved
    # verbatim.  ``model_dump(exclude_none=True)`` keeps omitted optionals absent
    # so the builder's ``.get(...)`` defaults apply.  The CSRF middleware already
    # guards this POST.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/composite-test-run",
        tags=["Composites"],
        summary="Start a detached composite run (returns 202 {run_id})",
    )
    def composite_test_run(
        req: CompositeTestRunRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Start a detached composite run.

        Mirrors the stdlib ``POST /api/composite-test-run``.  Body:
        ``{"id", "overrides"?, "steps"?, "label"?, "emit_paths"?}`` — writes the
        run-request file + runs_meta row, spawns the run-composite CLI detached,
        and returns 202 ``{run_id, status: "running"}`` immediately.

        Status codes (byte-identical to the legacy handler, via
        ``lib.composite_test_run_views.composite_test_run``):
          - 400  missing ``id`` (``{"error": "missing id"}``)
          - 429  at concurrency cap (``{"error": "too many runs in progress …"}``)
          - 500  spawn failure (``{"error": "spawn failed: …", "run_id": …}``)
          - 202  ``{run_id, status: "running"}``
        """
        body, status = _composite_test_run_views.composite_test_run(
            ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # P3: investigation-run-one — ad-hoc single composite execution
    #
    # The "Duplicate run" flow: resolve the investigation's baseline composite
    # (v2 ``variants[]`` sidecar OR a legacy top-level ``composite``), substitute
    # params, inject a SQLiteEmitter, run it once in an embedded subprocess,
    # persist the rendered viz HTML, and append to the investigation's runs.db.
    # JSONResponses every path so the lib-returned status is preserved verbatim:
    # 400 missing-inv / spec-error, 404 spec-or-composite-not-found, and 200 for
    # BOTH the run success (``{ok: true, …}``) AND the run failure
    # (``{ok: false, run_id, error}``) — only validation is non-200.
    # ``model_dump(exclude_none=True)`` keeps omitted optionals absent so the
    # builder's ``.get(...)`` defaults apply.  CSRF middleware already guards it.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/investigation-run-one",
        tags=["Investigations"],
        summary="Run a single ad-hoc composite (the 'Duplicate run' flow)",
    )
    def investigation_run_one(
        req: InvestigationRunOneRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Run a single ad-hoc composite execution for an investigation.

        Mirrors the stdlib ``POST /api/investigation-run-one``.  Body:
        ``{"investigation"|"study"|"name", "sim_name"?, "overrides"?, "steps"?,
        "label"?}`` — appends the result as a one-off run to the investigation's
        ``runs.db``.

        Status codes (byte-identical to the legacy handler, via
        ``lib.investigation_run_one_views.investigation_run_one``):
          - 400  missing investigation / spec parse error / shape-less spec
          - 404  spec.yaml or composite (sidecar / registry) not found
          - 200  run succeeded (``{ok: true, run_id, investigation, sim_name,
            viz_html}``) OR run failed (``{ok: false, run_id, error}``)
        """
        body, status = _investigation_run_one_views.investigation_run_one(
            ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # P4: investigation-run — run ALL of an investigation's simulations + viz
    #
    # The "Run investigation" flow: build the workspace core in-process for the
    # visualization registry, run each composite once in an embedded subprocess,
    # render every viz, and delegate orchestration to
    # ``lib.investigations.run_investigation``.  The live stdlib handler commits
    # the rendered viz via ``_active_branch_action``; this FastAPI path runs the
    # action inline with the commit DEFERRED (like every other committer port)
    # and returns the summary verbatim.  JSONResponses every path so the
    # lib-returned status is preserved: 400 missing-name / spec-error, 404
    # file-not-found, 500 core-build-failed, 200 success.
    # ``model_dump(exclude_none=True)`` keeps omitted optionals absent so the
    # builder's ``_study_name_from_body`` name resolution applies.  CSRF
    # middleware already guards it.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/investigation-run",
        tags=["Investigations"],
        summary="Run all of an investigation's simulations + render its visualizations",
    )
    def investigation_run(
        req: InvestigationRunRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Run every simulation of an investigation + render its visualizations.

        Mirrors the stdlib ``POST /api/investigation-run``.  Body:
        ``{"name"|"study"|"investigation"}``.

        Status codes (byte-identical to the legacy handler, via
        ``lib.investigation_run_views.investigation_run``, modulo the deferred
        commit):
          - 400  missing name / ``InvestigationSpecError`` (spec error)
          - 404  ``FileNotFoundError`` (composite/spec file missing)
          - 500  workspace core build failed
          - 200  the run/render summary dict
        """
        body, status = _investigation_run_views.investigation_run(
            ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # Scaffold: study-create — scaffold a new study directory.
    #
    # Creates ``investigations/<name>/`` (keyed by the workspace ``studies/``
    # dir) with a ``data/.keep`` plus one of three scaffold shapes keyed on the
    # resolved ``source`` composite: a v4 ``study.yaml`` for a
    # ``@composite_generator`` ref, a v2 ``spec.yaml`` + copied sidecar for a
    # YAML source, or a blank ``spec.yaml`` stub.  The live stdlib handler
    # commits the scaffold via ``_active_branch_action``; this FastAPI path runs
    # the scaffold inline with the commit DEFERRED (like every other committer
    # port) and returns ``{ok, name}`` verbatim.  JSONResponse every path so the
    # lib-returned status is preserved: 400 bad/missing name, 404 source not
    # found, 409 already exists, 500 scaffold failed, 200 success.
    # ``model_dump(exclude_none=True)`` keeps omitted optionals absent so a
    # missing ``name`` reaches the builder's 400 (not a 422).  CSRF middleware
    # already guards it.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/study-create",
        tags=["Studies"],
        summary="Scaffold a new study directory (deferred commit)",
    )
    def study_create(
        req: StudyCreateRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Scaffold a new study directory.

        Mirrors the stdlib ``POST /api/study-create``.  Body:
        ``{"name", "source"?, "composite"?}``.

        Status codes (byte-identical to the legacy handler, via
        ``lib.study_create_views.study_create``, modulo the
        deferred commit):
          - 400  missing ``name`` / name fails ``^[a-zA-Z0-9_-]+$``
          - 404  ``source`` composite not resolvable
          - 409  investigation already exists
          - 500  scaffold ``action`` raised (``{"error": "action failed: …"}``)
          - 200  ``{ok: true, name}``  (scaffold run inline; the live
            ``_active_branch_action`` commit is deferred)
        """
        body, status = _study_create_views.study_create(
            ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # P5: investigation-run-unblocked — enumerate every member study's unblocked
    # variants + SUBMIT one background run job (the FINAL sim-execution port).
    #
    # The "Run unblocked" flow: enumerate each member study's runnable/blocked
    # variants (``lib.run_jobs.enumerate_unblocked``), then submit a single
    # background job to the SAME in-process ``lib.run_jobs.manager`` singleton
    # the already-ported ``GET /api/investigation-run-unblocked-status`` reads,
    # so the submit is visible to the status GET.  The worker fires each queued
    # item through the E4 study-run orchestrators
    # (``study_runs.run_study_baseline`` / ``run_study_variant``) and renders the
    # E5 comparative visualisations — all delegated to the pure lib builder
    # ``lib.run_unblocked_views.investigation_run_unblocked``.  JSONResponse
    # every path so the lib-returned status is preserved: 400 missing-inv /
    # no-queued breakdown, 404 inv.yaml-missing, 500 yaml-parse, 202 success.
    # ``model_dump(exclude_none=True)`` keeps an omitted ``studies`` absent so
    # the builder's "all studies" default applies.  CSRF middleware guards it.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/investigation-run-unblocked",
        tags=["Investigations"],
        summary="Enumerate + run all of an investigation's unblocked variants",
        status_code=202,
    )
    def investigation_run_unblocked(
        req: InvestigationRunUnblockedRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Enumerate + submit a run job for an investigation's unblocked variants.

        Mirrors the stdlib ``POST /api/investigation-run-unblocked``.  Body:
        ``{"investigation", "studies"?}`` — enumerates every member study's
        unblocked variants and runs them sequentially on a background thread
        submitted to the in-process ``lib.run_jobs.manager`` singleton (the SAME
        manager the ``GET /api/investigation-run-unblocked-status`` poller reads,
        so the submit is visible to the status GET); returns a ``job_id``
        immediately.  ``studies`` optionally narrows to a subset (a single slug
        or a list).

        Status codes (byte-identical to the legacy handler, via
        ``lib.run_unblocked_views.investigation_run_unblocked``):
          - 400  missing investigation (``{"error": "investigation is required"}``)
          - 404  investigation not found (``{"error": "investigation not found: <slug>"}``)
          - 500  ``yaml.YAMLError`` (``{"error": "yaml parse failed: <e>"}``)
          - 400  no variants to queue (the per-status breakdown + ``items[]``)
          - 202  ``{"job_id": <id>, "items": [...]}``

        The CSRF middleware already guards this POST.  Every path (incl. the 202
        success and the no-queued 400) is wrapped in ``JSONResponse`` so the
        lib-returned status code is preserved verbatim.  ``model_dump(
        exclude_none=True)`` keeps an omitted ``studies`` absent so the builder's
        "all studies" default applies.
        """
        body, status = _run_unblocked_views.investigation_run_unblocked(
            ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # Runs — remote (sms-api) simulation-run SUBMIT
    # -----------------------------------------------------------------------

    @app.post(
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
        body, status = _remote_run_views.remote_run_start(ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # Auth — GitHub OAuth Device Flow (5 thin wrappers over lib.github_auth)
    #
    # The device-flow/session state lives in the lib.github_auth process-global
    # singletons shared with the stdlib server, so these routes need no state
    # extraction.  Every path (success AND error) is returned via JSONResponse
    # so the lib-mapped status code (200/202/400/401/403/410/502/503) is
    # preserved verbatim — a plain model return would force 200.  The 2 POSTs
    # accept a permissive body they ignore (Body(default={})); the CSRF
    # middleware already guards them.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/auth/github/start",
        response_model=AuthPayload,
        tags=["Auth"],
        summary="Initiate a GitHub OAuth Device Flow",
    )
    def auth_github_start(body: dict = Body(default={})) -> JSONResponse:
        """Initiate a GitHub Device Flow (mirrors the stdlib
        ``POST /api/auth/github/start``).

        Returns the ``user_code`` + ``verification_uri`` to display plus a
        server-issued ``flow_id`` for ``/poll`` (the ``device_code`` is held
        server-side, never returned).  The request body is accepted and ignored.

        Status codes (byte-identical to the legacy handler): 503
        (``{"error": "no_client_id"}`` — deployment not configured), 502
        (other GitHub-side failure), 200 (success).  Library-backed via the pure
        ``lib.auth_views.auth_start``.
        """
        resp, code = _auth_views.auth_start(body)
        return JSONResponse(content=resp, status_code=code)

    @app.get(
        "/api/auth/github/poll",
        response_model=AuthPayload,
        tags=["Auth"],
        summary="Poll the GitHub token endpoint for a pending device flow",
    )
    def auth_github_poll(flow_id: str = "") -> JSONResponse:
        """Poll a pending device flow (mirrors the stdlib
        ``GET /api/auth/github/poll?flow_id=<uuid>``).

        A missing/blank ``flow_id`` returns HTTP 400
        ``{"status": "error", "detail": "missing_flow_id"}``; otherwise the
        ``poll_device_flow`` outcome maps to an HTTP code the client can use
        without parsing JSON: 200 (ok), 202 (pending), 410 (expired), 403
        (denied), 400 (any other/error status).  Library-backed via the pure
        ``lib.auth_views.auth_poll``.
        """
        resp, code = _auth_views.auth_poll(flow_id)
        return JSONResponse(content=resp, status_code=code)

    @app.get(
        "/api/auth/github/status",
        response_model=AuthPayload,
        tags=["Auth"],
        summary="Current GitHub auth session (never the token)",
    )
    def auth_github_status() -> JSONResponse:
        """Current auth session (mirrors the stdlib
        ``GET /api/auth/github/status``).

        Returns ``{authenticated: false}`` or ``{authenticated: true, login,
        source, scopes}`` — never the token itself.  Always HTTP 200.
        Library-backed via the pure ``lib.auth_views.auth_status``.
        """
        resp, code = _auth_views.auth_status()
        return JSONResponse(content=resp, status_code=code)

    @app.post(
        "/api/auth/github/logout",
        response_model=AuthPayload,
        tags=["Auth"],
        summary="Clear the GitHub auth session + keyring entry",
    )
    def auth_github_logout(body: dict = Body(default={})) -> JSONResponse:
        """Clear the in-memory session + persisted keyring entry (mirrors the
        stdlib ``POST /api/auth/github/logout``).

        Always returns HTTP 200 ``{"ok": true}``.  The request body is accepted
        and ignored.  The CSRF middleware already guards this POST.
        Library-backed via the pure ``lib.auth_views.auth_logout``.
        """
        resp, code = _auth_views.auth_logout(body)
        return JSONResponse(content=resp, status_code=code)

    @app.get(
        "/api/auth/github/orgs",
        response_model=AuthPayload,
        tags=["Auth"],
        summary="The user's GitHub namespace + orgs",
    )
    def auth_github_orgs() -> JSONResponse:
        """List the user's personal namespace + orgs (mirrors the stdlib
        ``GET /api/auth/github/orgs``).

        Returns ``{login, orgs: [{name, kind}, ...]}`` on success.  Status codes
        (byte-identical to the legacy handler): 401
        (``{"error": "unauthenticated"}`` — no session), 502 (orgs lookup
        failed), 200 (success).  Library-backed via the pure
        ``lib.auth_views.auth_orgs``.
        """
        resp, code = _auth_views.auth_orgs()
        return JSONResponse(content=resp, status_code=code)

    # -----------------------------------------------------------------------
    # Git — subprocess commit/push WRITE routes (2 POSTs)
    #
    # Both shell out to git in the active workspace via the pure
    # lib.git_commit_views builders (parameterised on ws_root, reusing
    # lib.git_status).  Every path (success AND error) is returned via
    # JSONResponse so the lib-returned status code is preserved verbatim — a
    # plain model return would force 200.  The CSRF middleware already guards
    # both POSTs.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/branch/push",
        response_model=BranchPushResponse,
        tags=["Git & workstream"],
        summary="Commit workspace changes + push the current branch",
    )
    def branch_push(
        req: Optional[BranchPushRequest] = None,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Commit the workspace's changes (skip if clean) + push the current branch.

        Mirrors the stdlib ``POST /api/branch/push``.  Body: ``{"message"?}`` —
        an empty/omitted message falls back to ``"dashboard commit"``.  Stages
        ``git add -A``, commits when dirty, then pushes ``-u origin <branch>``
        with the GitHub token and returns the pushed HEAD SHA.

        Status codes (byte-identical to the legacy handler):
          - 409  not a git workspace (``{"error": "active source is not a git workspace (no commit/push)"}``)
          - 500  ``{"error": <git/push failure>}``
          - 200  ``{ok: true, pushed, commit, branch}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.git_commit_views.branch_push``; every path is wrapped in
        ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        payload = req.model_dump(exclude_none=True) if req is not None else {}
        body, status = _git_commit_views.branch_push(ws, payload)
        return JSONResponse(status_code=status, content=body)

    @app.post(
        "/api/dirty-commit-all",
        response_model=DirtyCommitAllResponse,
        tags=["Git & workstream"],
        summary="Stage + commit all dirty files (minus reports/) on the active branch",
    )
    def dirty_commit_all(
        body: dict = Body(default={}),
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Stage and commit all dirty files (minus reports/) under the active workstream.

        Mirrors the stdlib ``POST /api/dirty-commit-all``.  Adopts the workspace's
        current git HEAD as the workstream when none is active, checks it out if
        HEAD differs, stages ``git add -A``, un-stages ``reports/``, then commits
        under the ``pbg-template`` identity with an auto-generated conventional
        message.  The request body is accepted and ignored.

        Status codes (byte-identical to the legacy handler):
          - 409  no active workstream (``{"error": "no active workstream"}``)
          - 500  ``{"error": "git rev-parse failed: <stderr[:200]>"}``
          - 500  ``{"error": "could not check out '<branch>': <stderr[:200]>"}``
          - 409  already clean (``{"error": "working tree is already clean"}``)
          - 500  ``{"error": "git operation failed: <stderr[:300]>"}``
          - 200  ``{commit_sha: <sha[:7]>, message, paths}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.git_commit_views.dirty_commit_all``; every path is wrapped in
        ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        resp, status = _git_commit_views.dirty_commit_all(ws, body)
        return JSONResponse(status_code=status, content=resp)

    # -----------------------------------------------------------------------
    # Workstream — branch lifecycle + report-attach WRITE routes (4 POSTs)
    #
    # All four shell out to git in the active workspace via the pure
    # lib.work_mutations builders (parameterised on ws_root, reusing
    # lib.work_state + lib.git_status).  Every path (success AND error) is
    # returned via JSONResponse so the lib-returned status code is preserved
    # verbatim — a plain model return would force 200.  The CSRF middleware
    # already guards all four POSTs.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/work-start",
        response_model=WorkStartResponse,
        tags=["Git & workstream"],
        summary="Create a working branch from base + set it active",
    )
    def work_start(
        req: Optional[WorkStartRequest] = None,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Create a new working branch from ``base`` and mark it the active workstream.

        Mirrors the stdlib ``POST /api/work-start``.  Body: ``{"branch", "base"?}``
        — ``base`` defaults to ``"main"``.  Validates the branch name, refuses
        when a workstream is already active or the tree is dirty, verifies the
        base exists + the branch does not, then ``git checkout <base>`` +
        ``git checkout -b <branch>`` and persists the workstream state.

        Status codes (byte-identical to the legacy handler):
          - 400  ``{"error": "invalid branch name"}``
          - 409  ``{"error": "already on workstream '<b>'. End it first."}``
          - 409  ``{"error": "working tree dirty — commit or stash first"}``
          - 404  ``{"error": "base branch '<base>' not found"}``
          - 409  ``{"error": "branch '<b>' already exists. ..."}``
          - 500  ``{"error": "branch create failed: <stderr[:300]>"}``
          - 200  ``{ok: true, branch, base}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.work_mutations.work_start``; every path is wrapped in
        ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        payload = req.model_dump(exclude_none=True) if req is not None else {}
        resp, status = _work_mutations.work_start(ws, payload)
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/work-push",
        response_model=WorkPushResponse,
        tags=["Git & workstream"],
        summary="Push the active workstream branch to origin",
    )
    def work_push(
        body: dict = Body(default={}),
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Push the active workstream branch to ``origin`` with ``-u``.

        Mirrors the stdlib ``POST /api/work-push``.  Adopts the workspace's
        current git HEAD as the workstream when none is active.  Refuses cleanly
        with a structured ``no_origin`` diagnosis when no origin remote exists,
        attaches a ``diagnose_push_error`` diagnosis to push failures, and marks
        the workstream ``pushed`` on success.  The request body is ignored.

        Status codes (byte-identical to the legacy handler):
          - 409  ``{"error": "no active workstream"}``
          - 409  ``{"error": "no GitHub remote configured", "diagnosis": {...no_origin...}}``
          - 500  ``{"error": "push failed: <err[:300]>", "diagnosis"?}``
          - 200  ``{ok: true, branch, log}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.work_mutations.work_push``; every path is wrapped in
        ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        resp, status = _work_mutations.work_push(ws, body)
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/work-end",
        response_model=WorkEndResponse,
        tags=["Git & workstream"],
        summary="Check out the base branch + clear the workstream",
    )
    def work_end(
        body: dict = Body(default={}),
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """End the active workstream: check out the base branch + clear state.

        Mirrors the stdlib ``POST /api/work-end``.  Refuses when no workstream is
        active or the tree is dirty, then ``git checkout <base>`` (base from the
        workstream state, default ``"main"``) and clears the workstream state.
        The request body is ignored.

        Status codes (byte-identical to the legacy handler):
          - 409  ``{"error": "no active workstream"}``
          - 409  ``{"error": "uncommitted changes — commit or stash before ending"}``
          - 200  ``{ok: true}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.work_mutations.work_end``; every path is wrapped in
        ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        resp, status = _work_mutations.work_end(ws, body)
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/work-attach-report",
        response_model=WorkAttachReportResponse,
        tags=["Git & workstream"],
        summary="Write a report file to reports/ + commit it on the active branch",
    )
    def work_attach_report(
        req: Optional[WorkAttachReportRequest] = None,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Write ``html`` to ``reports/<filename>`` and commit it on the active branch.

        Mirrors the stdlib ``POST /api/work-attach-report``.  Body:
        ``{"filename", "html", "commit_message"?}``.  Requires an active branch +
        a bare ``filename`` and non-empty ``html`` string, writes the file under
        the workspace's ``reports`` dir, then ``git add`` + ``git commit`` the
        single file (a soft-success ``unchanged: true`` when nothing changed).

        Status codes (byte-identical to the legacy handler):
          - 409  ``{"error": "no active investigation branch"}``
          - 400  ``{"error": "filename + html required"}``
          - 400  ``{"error": "filename must be a bare name (no path / no leading .)"}``
          - 500  ``{"error": "git add failed: <(stderr or stdout)[:300]>"}``
          - 200  ``{ok: true, unchanged: true, path, branch}`` (nothing to commit)
          - 500  ``{"error": "git commit failed: <stderr[:300]>"}``
          - 200  ``{ok: true, path, branch, commit_sha}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.work_mutations.work_attach_report``; every path is wrapped in
        ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        payload = req.model_dump(exclude_none=True) if req is not None else {}
        resp, status = _work_mutations.work_attach_report(ws, payload)
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/work-create-pr",
        response_model=WorkCreatePrResponse,
        tags=["Git & workstream"],
        summary="Create a GitHub PR for the active workstream via the gh CLI",
    )
    def work_create_pr(
        req: Optional[WorkCreatePrRequest] = None,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Create a GitHub pull request for the active workstream branch.

        Mirrors the stdlib ``POST /api/work-create-pr``.  Body:
        ``{"title"?, "body"?, "draft"?}`` (``draft`` defaults to ``True``).
        Adopts the workspace's current git HEAD as the workstream when none is
        active, opportunistically marks the branch ``pushed`` when local matches
        ``origin/<branch>``, defaults the PR title to the matching
        investigation's ``title:`` (else ``Workstream: <branch>``), prepends
        ``investigation: `` when the branch touches ``investigations/``, then
        shells out to ``gh pr create`` and records the resulting PR url/number.

        Status codes (byte-identical to the legacy handler):
          - 409  ``{"error": "no active workstream"}``
          - 409  ``{"error": "branch not yet pushed. ..."}`` (the long UI message)
          - 409  ``{"error": "PR already exists: <url>", "pr_url"}``
          - 500  ``{"error": "gh CLI not installed. Open manually:", "manual_url"}``
          - 500  ``{"error": "gh pr create failed: <err[:300]>"}``
          - 200  ``{ok: true, pr_url, pr_number}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.work_pr_views.work_create_pr``; every path is wrapped in
        ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        payload = req.model_dump(exclude_none=True) if req is not None else {}
        resp, status = _work_pr_views.work_create_pr(ws, payload)
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/work-link-branch",
        response_model=WorkLinkBranchResponse,
        tags=["Git & workstream"],
        summary="Link the active workstream to an upstream branch (branch or fork mode)",
    )
    def work_link_branch(
        req: Optional[WorkLinkBranchRequest] = None,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Link the workspace to an upstream branch via the ``gh``/``git`` CLIs.

        Mirrors the stdlib ``POST /api/work-link-branch``.  Body:
        ``{upstream_repo?, branch_name?, push?: bool=True, mode?: "branch"|"fork"}``.

        ``mode="branch"`` (default) sets git ``origin`` to the upstream (refusing
        to overwrite a divergent origin), pushes the active branch, and marks the
        workstream pushed.  ``mode="fork"`` forks the upstream under the
        authenticated gh user (``gh repo fork``), points ``origin`` at the fork +
        adds an ``upstream`` remote, and pushes to the fork.  ``upstream_repo``
        defaults to the auto-detected upstream; ``branch_name`` renames the active
        branch first when it differs.

        Status codes (byte-identical to the legacy handler):
          - 409  ``{"error": "no active workstream — Start one first ..."}``
          - 500  ``{"error": "gh CLI not installed. ..."}`` / ``"gh not authenticated. ..."``
          - 400  bad ``mode`` / ``upstream_repo`` / branch name
          - 500  branch rename / gh / git failure (``[:300]``/``[:500]`` slices)
          - 409  ``{"error": "origin already configured to ...", "current_origin"}``
          - 200  branch: ``{ok, upstream_repo, branch, branch_url}``;
                 fork: ``{ok, fork, upstream, branch, branch_url}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.work_pr_views.work_link_branch``; every path is wrapped in
        ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        payload = req.model_dump(exclude_none=True) if req is not None else {}
        resp, status = _work_pr_views.work_link_branch(ws, payload)
        return JSONResponse(status_code=status, content=resp)

    # -----------------------------------------------------------------------
    # Workspaces — global ~/.pbg catalog WRITE routes (3 POSTs)
    #
    # These 3 POSTs edit the GLOBAL ~/.pbg workspace catalog via
    # pbg_superpowers.workspace_catalog (process-global, NOT server state; no
    # workspace/ws_root).  All three call the pure lib.workspaces_mutations
    # builders, which take ONLY the request body's ``path``.  Every path
    # (success AND error) is returned via JSONResponse so the lib-returned
    # status code is preserved verbatim — a plain model return would force 200.
    # The request model's ``path`` is Optional so an omitted path reaches the
    # lib builder's own 400 (not FastAPI's 422).  The CSRF middleware already
    # guards all three POSTs.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/workspaces/add",
        response_model=WorkspaceEntry,
        tags=["Workspaces & sources"],
        summary="Register an existing workspace in the global ~/.pbg catalog",
    )
    def workspaces_add(
        req: Optional[WorkspacesPathRequest] = None,
    ) -> JSONResponse:
        """Register an existing workspace by absolute path in the global catalog.

        Mirrors the stdlib ``POST /api/workspaces/add``.  Body: ``{"path"}`` — an
        absolute path string.  Adds the entry via
        ``workspace_catalog.add(path)`` and returns it.

        Status codes (byte-identical to the legacy handler):
          - 400  ``{"error": "path must be an absolute string"}`` (missing /
                 non-string / non-absolute path)
          - 400  ``{"error": <ValueError message>}`` (``add`` rejected the path)
          - 200  the catalog ``entry`` dict

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.workspaces_mutations.workspaces_add``; every path is wrapped
        in ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        payload = req.model_dump() if req is not None else {}
        resp, status = _workspaces_mut.workspaces_add(payload)
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/workspaces/forget",
        response_model=WorkspacesOkResponse,
        tags=["Workspaces & sources"],
        summary="Remove a catalog entry (refuses a running workspace)",
    )
    def workspaces_forget(
        req: Optional[WorkspacesPathRequest] = None,
    ) -> JSONResponse:
        """Remove a workspace's catalog entry; refuse a running workspace.

        Mirrors the stdlib ``POST /api/workspaces/forget``.  Body: ``{"path"}``.
        Refuses with 409 when ``workspace_catalog.find_running(path)`` is not
        ``None`` (the caller must stop the server first), else forgets the entry.

        Status codes (byte-identical to the legacy handler):
          - 400  ``{"error": "path required"}`` (missing / non-string path)
          - 409  ``{"error": "stop the server before forgetting"}``
          - 200  ``{"ok": True}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.workspaces_mutations.workspaces_forget``; every path is
        wrapped in ``JSONResponse`` so the lib-returned status code is preserved
        verbatim.
        """
        payload = req.model_dump() if req is not None else {}
        resp, status = _workspaces_mut.workspaces_forget(payload)
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/workspaces/cleanup-stale",
        response_model=WorkspacesOkResponse,
        tags=["Workspaces & sources"],
        summary="Unregister a stale running entry + unlink orphan server files",
    )
    def workspaces_cleanup_stale(
        req: Optional[WorkspacesPathRequest] = None,
    ) -> JSONResponse:
        """Unregister a stale running-registry entry + best-effort orphan cleanup.

        Mirrors the stdlib ``POST /api/workspaces/cleanup-stale``.  Body:
        ``{"path"}``.  Refuses with 409 when the PID is in fact alive
        (``find_running(path)`` not ``None``), else
        ``workspace_catalog.unregister_server(path)`` and best-effort unlinks the
        orphan ``<path>/.pbg/server/server-info`` and ``server.pid`` files (each
        ignoring ``FileNotFoundError``).

        Status codes (byte-identical to the legacy handler):
          - 400  ``{"error": "path required"}`` (missing / non-string path)
          - 409  ``{"error": "server is still running"}``
          - 200  ``{"ok": True}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.workspaces_mutations.workspaces_cleanup_stale``; every path is
        wrapped in ``JSONResponse`` so the lib-returned status code is preserved
        verbatim.
        """
        payload = req.model_dump() if req is not None else {}
        resp, status = _workspaces_mut.workspaces_cleanup_stale(payload)
        return JSONResponse(status_code=status, content=resp)

    # -----------------------------------------------------------------------
    # Workspace process-management WRITE routes (2 POSTs)
    #   POST /api/workspaces/start  /api/workspaces/stop
    # Spawn (subprocess.Popen) / SIGTERM (os.kill) a `vivarium-dashboard serve`
    # child for a catalogued workspace.  Each delegates to a pure builder in
    # lib.workspaces_process_views (parameterised on ws_root — start ignores it,
    # stop uses it for the self-stop guard).  The request model's ``path`` is
    # Optional so an omitted path reaches the lib builder's own 400 (not
    # FastAPI's 422).  Every path (success AND error) is returned via
    # JSONResponse so the lib-returned status code is preserved verbatim.  The
    # CSRF middleware already guards both POSTs.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/workspaces/start",
        response_model=WorkspaceEntry,
        tags=["Workspaces & sources"],
        summary="Spawn `vivarium-dashboard serve` for a stopped workspace",
    )
    def workspaces_start(
        req: Optional[WorkspacesPathRequest] = None,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Spawn ``vivarium-dashboard serve`` for a stopped catalogued workspace.

        Mirrors the stdlib ``POST /api/workspaces/start``.  Body: ``{"path"}`` —
        an absolute path string for a workspace registered in the global
        catalog.  Idempotent: returns the existing URL if a live entry already
        exists; otherwise spawns the child via ``subprocess.Popen`` and polls
        the catalog (8 s) until it registers.

        Status codes (byte-identical to the legacy handler):
          - 400  ``{"error": "path must be an absolute string"}``
          - 400  ``{"error": "not a workspace (no workspace.yaml)"}``
          - 400  ``{"error": "workspace not in catalog — Add it first"}``
          - 200  ``{"url": ..., "pid": ...}`` (idempotent-live OR spawned)
          - 504  ``{"error": "start_timeout", "log_path": ..., "hint": ...}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.workspaces_process_views.workspaces_start``; every path is
        wrapped in ``JSONResponse`` so the lib-returned status code is preserved
        verbatim.
        """
        payload = req.model_dump(exclude_none=True) if req is not None else {}
        resp, status = _workspaces_proc.workspaces_start(ws, payload)
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/workspaces/stop",
        response_model=WorkspacesOkResponse,
        tags=["Workspaces & sources"],
        summary="SIGTERM a running workspace's dashboard (refuses self-stop)",
    )
    def workspaces_stop(
        req: Optional[WorkspacesPathRequest] = None,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """SIGTERM a running workspace's dashboard child; refuse self-stop.

        Mirrors the stdlib ``POST /api/workspaces/stop``.  Body: ``{"path"}``.
        Refuses to stop the dashboard's own bound workspace (``ws_root``) and
        uncatalogued paths.  SIGTERMs the running child and polls (3 s) for its
        atexit hook to remove the global registry entry.  Does NOT escalate to
        SIGKILL on timeout.

        Status codes (byte-identical to the legacy handler):
          - 400  ``{"error": "path must be an absolute string"}``
          - 400  ``{"error": "workspace not in catalog"}``
          - 400  ``{"error": "refusing to stop self — use the terminal: kill <pid>"}``
          - 400  ``{"error": "not running"}``
          - 200  ``{"ok": True}`` (deregistered OR already-dead ProcessLookupError)
          - 504  ``{"error": "stop_timeout", "hint": ...}``

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.workspaces_process_views.workspaces_stop``; every path is
        wrapped in ``JSONResponse`` so the lib-returned status code is preserved
        verbatim.
        """
        payload = req.model_dump(exclude_none=True) if req is not None else {}
        resp, status = _workspaces_proc.workspaces_stop(ws, payload)
        return JSONResponse(status_code=status, content=resp)

    # -----------------------------------------------------------------------
    # C-state-3h2: misc FS/render POST routes
    #   POST /api/click  /api/render  /api/feedback-import
    # Workspace-scoped writes that do only local FS work / an in-process render.
    # Each delegates to a pure builder in lib.misc_mutations.  /api/click returns
    # a RAW empty 204 (no JSON body — byte-matching the legacy send_response(204));
    # render + feedback-import wrap every path in JSONResponse so the lib-returned
    # status code is preserved verbatim.  All accept a permissive body
    # (Body(default={})); the CSRF middleware already guards them.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/click",
        status_code=204,
        tags=["Rigor & jobs"],
        summary="Append a UI click/telemetry event to the events log",
    )
    def click(
        body: dict = Body(default={}),
        ws: Path = Depends(get_workspace),
    ) -> Response:
        """Append the request body as a JSON line to the workspace events log.

        Mirrors the stdlib ``POST /api/click``.  Appends ``json.dumps(body) +
        "\\n"`` to ``<ws>/.pbg/server/state/events`` (under a module lock) and
        returns a RAW empty ``204 No Content`` — NO JSON body, byte-matching the
        legacy ``send_response(204)``.

        Library-backed via the pure ``lib.misc_mutations.record_click``.
        """
        _misc_mut.record_click(ws, body)
        return Response(status_code=204)

    @app.post(
        "/api/render",
        response_model=RenderResponse,
        tags=["Rigor & jobs"],
        summary="Re-render the workspace dashboard in-process",
    )
    def render(
        body: dict = Body(default={}),
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Re-render the workspace dashboard report in-process.

        Mirrors the stdlib ``POST /api/render``.  Calls
        ``render_workspace_report(ws)``; the request body is accepted and ignored.

        Status codes (byte-identical to the legacy handler):
          - 200  ``{"ok": True}``
          - 500  ``{"error": <str(e)>}`` (any render failure)

        The CSRF middleware already guards this POST.  Library-backed via the
        pure ``lib.misc_mutations.render_dashboard``; every path is wrapped in
        ``JSONResponse`` so the lib-returned status code is preserved verbatim.
        """
        resp, status = _misc_mut.render_dashboard(ws)
        return JSONResponse(status_code=status, content=resp)

    # -----------------------------------------------------------------------
    # Misc POST routes (cont.): suggest + study-report-single + open-window
    #   POST /api/suggest  /api/study-report-single  /api/open-window
    # Workspace-scoped writes that do local FS work + a short subprocess.run
    # (git-log / browser open — NEVER spawned in tests; the lib fn is
    # monkeypatched).  Each delegates to a pure builder in lib.misc_post_views
    # and wraps every path (success AND error) in JSONResponse so the
    # lib-returned status code is preserved verbatim.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/suggest",
        tags=["Rigor & jobs"],
        summary="Write a Claude-suggestion request file",
    )
    def suggest(
        req: SuggestRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Write a Claude-suggestion request file. Body: ``{kind, context_extras?}``.

        Mirrors the stdlib ``POST /api/suggest``.  Builds the request context
        (workspace name/description, active branch, capped ``main..<branch>``
        git-log commits) and persists it via ``suggest_requests.write_request``.

        Status codes (byte-identical to the legacy handler, via
        ``lib.misc_post_views.suggest``):
          - 400  ``{"error": "invalid kind (must be one of …)"}``
          - 200  ``{ok, id, skill_command, instructions}``
        """
        body, status = _misc_post_views.suggest(ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    @app.post(
        "/api/study-report-single",
        tags=["Rigor & jobs"],
        summary="Render a standalone one-study HTML report",
    )
    def study_report_single(
        req: StudyReportSingleRequest,
        ws: Path = Depends(get_workspace),
        skeptic: Optional[str] = None,
    ) -> JSONResponse:
        """Render a standalone HTML report for ONE study (the investigation's
        ``focus_study`` or an explicit ``study`` override).

        Mirrors the stdlib ``POST /api/study-report-single``.  The ``?skeptic=``
        QUERY param is honored as an alternative to the body flag: if present and
        ``"skeptic"`` is not already in the body, ``body["skeptic"]`` is set to
        ``skeptic not in ("0", "false", "")`` before the builder runs (matching
        the legacy ``_post_study_report_single`` URL handling).

        Status codes (byte-identical to the legacy handler, via
        ``lib.misc_post_views.study_report_single``):
          - 400/404  bad input / missing files (builder error shapes)
          - 500  ``{"error": <str(e)>}`` (any exception)
          - 200  ``{html_path, size_bytes, study, investigation?}``
        """
        body = req.model_dump(exclude_none=True)
        if skeptic is not None and "skeptic" not in body:
            body["skeptic"] = skeptic not in ("0", "false", "")
        resp, status = _misc_post_views.study_report_single(ws, body)
        return JSONResponse(status_code=status, content=resp)

    @app.post(
        "/api/open-window",
        tags=["Rigor & jobs"],
        summary="Open a dashboard URL in the user's browser",
    )
    def open_window(
        req: OpenWindowRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Open a dashboard URL in the user's browser. Body: ``{route?}``.

        Mirrors the stdlib ``POST /api/open-window``.  Reads the base URL from
        ``<ws>/.pbg/server/server-info`` and dispatches the platform-appropriate
        open command via a 5s-timeout ``subprocess.run``.

        Status codes (byte-identical to the legacy handler, via
        ``lib.misc_post_views.open_window``):
          - 503  server-info file not found
          - 501  unsupported platform
          - 500  server-info parse failed / open failed
          - 200  ``{ok: True, url}``
        """
        body, status = _misc_post_views.open_window(ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # Installs: system-deps-install + import-install — venv / system installs
    #
    # Two POSTs that run install commands via subprocess (NEVER spawned in
    # tests — the lib fn is monkeypatched).  ``system-deps-install`` runs a
    # catalog module's per-check install commands and re-checks; ``import-install``
    # pip-installs a registered import into the venv.  ``import-install`` is an
    # ``_active_branch_action`` committer in the live server; this FastAPI path
    # runs the workspace.yaml ``installed=True`` mutation inline with the commit
    # DEFERRED (like every other committer port) and returns the success body
    # verbatim.  Both JSONResponse every path so the lib-returned (dict, status)
    # is preserved.  ``model_dump(exclude_none=True)`` keeps omitted optionals
    # absent so the builder's ``.get(...)`` defaults apply.  CSRF middleware
    # already guards both.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/system-deps-install",
        tags=["Registry & catalog"],
        summary="Run install commands for a catalog module's system-dep checks",
    )
    def system_deps_install(
        req: SystemDepsInstallRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Run install commands for the named checks of a catalog module.

        Mirrors the stdlib ``POST /api/system-deps-install``.  Body:
        ``{"name", "check_names"}`` — runs each check's platform install commands
        (``shell=True``, 600s timeout), then re-checks every requested dep.

        Status codes (byte-identical to the legacy handler, via
        ``lib.install_views.system_deps_install``):
          - 400  missing ``name`` / empty ``check_names``
          - 404  unknown module (``{"error": "unknown module: …"}``)
          - 200  ``{ok, log, recheck}``
        """
        body, status = _install_views.system_deps_install(
            ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    @app.post(
        "/api/import-install",
        tags=["Registry & catalog"],
        summary="Pip-install a registered import into the workspace venv",
    )
    def import_install(
        req: ImportInstallRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Pip-install a ``workspace.yaml``-registered import into the venv.

        Mirrors the stdlib ``POST /api/import-install``.  Body:
        ``{"name", "target"?}`` — installs editable (venv ``pip`` preferred,
        system ``uv`` fallback, 120s timeout); on success it marks the import
        ``installed=True`` (+ ``install_path``) in workspace.yaml and invalidates
        the registry cache.

        Status codes (byte-identical to the legacy handler, via
        ``lib.install_views.import_install``, modulo the deferred commit):
          - 400  missing name / no install target
          - 404  import not registered / target path missing
          - 500  no installer / install timeout / install error / non-zero
            returncode (``{"error": "install failed", "log": …[, "diagnosis"]}``)
          - 200  ``{ok: true, log}``  (workspace.yaml mutation run inline)
        """
        body, status = _install_views.import_install(
            ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    @app.post(
        "/api/catalog-install",
        tags=["Registry & catalog"],
        summary="Install a catalog module (PyPI direct or git-submodule) into the venv",
    )
    def catalog_install(
        req: CatalogInstallRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Install a catalog module into the workspace venv.

        Mirrors the stdlib ``POST /api/catalog-install`` (the biggest handler in
        the migration).  Body: ``{"name", "skip_system_deps_check"?}`` — installs
        directly from PyPI when the catalog entry carries a ``pypi_name``, else
        via the legacy git-submodule + editable-install path, gated on a
        system-dependency check.

        Status codes (byte-identical to the legacy handler, via
        ``lib.catalog_install_views.catalog_install``, modulo the deferred
        commit):
          - 400  missing ``name``
          - 404  module not in catalog (``{"error": "module '…' not in catalog"}``)
          - 409  unmet system dependencies (``{error, name, platform, missing,
            hint}``; bypass with ``skip_system_deps_check=true``)
          - 500  no installer available / install failure (``{"error":
            "action failed: …", "log", "install_mode"[, "diagnosis"]}``)
          - 200  ``{ok: true, module, install_mode, log}``  (install + workspace.yaml
            mutation run inline; the live ``_commit_or_run`` commit is deferred)
        """
        body, status = _catalog_install_views.catalog_install(
            ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    @app.post(
        "/api/catalog-uninstall",
        tags=["Registry & catalog"],
        summary="Uninstall a catalog module (PyPI or git-submodule) from the workspace",
    )
    def catalog_uninstall(
        req: CatalogUninstallRequest,
        ws: Path = Depends(get_workspace),
    ) -> JSONResponse:
        """Remove a catalog module from the workspace venv (the last catalog committer).

        Mirrors the stdlib ``POST /api/catalog-uninstall``.  Body: ``{"name"}`` —
        reverses catalog-install (pypi: remove dependency + pip uninstall;
        reference: remove dependency + uv source + git submodule deinit/rm + pip
        uninstall; both: pop ``workspace.yaml`` imports entry).  A module not
        declared in ``workspace.yaml.imports`` is delegated to the "unmanaged"
        path (venv-only uninstall + untracked ``external/<name>/`` removal).

        Status codes (byte-identical to the legacy handler, via
        ``lib.catalog_uninstall_views.catalog_uninstall``, modulo the deferred
        commit):
          - 400  missing ``name``
          - 200  unmanaged already-uninstalled (``{ok, already_uninstalled}``)
          - 409  unmanaged module still required by a parent
          - 500  no installer available / uninstall ``action`` failure
          - 200  ``{ok: true, module, install_mode, log}``  (uninstall +
            workspace.yaml mutation run inline; the live ``_commit_or_run``
            commit is deferred)
        """
        body, status = _catalog_uninstall_views.catalog_uninstall(
            ws, req.model_dump(exclude_none=True))
        return JSONResponse(status_code=status, content=body)

    # -----------------------------------------------------------------------
    # CATCH-ALL — MUST stay registered LAST (immediately before ``return app``)
    # so Starlette, which matches routes in registration order, resolves every
    # specific route first (all ``/api/*``, ``/``, the loom/parsimony viewers,
    # FastAPI's ``/docs`` & ``/openapi.json``).  This route only handles
    # otherwise-unmatched paths.  DO NOT add routes below it.
    # -----------------------------------------------------------------------
    @app.get(
        "/{rel:path}",
        tags=["Static & shell"],
        summary="Catch-all static asset (bundled → assets-strip → workspace → reports)",
        response_class=Response,
        include_in_schema=False,
    )
    def catch_all_asset(rel: str, ws: Path = Depends(get_workspace)) -> Response:
        """Generic static-file serving for otherwise-unmatched GET paths.

        Resolution priority (mirrors the legacy ``do_GET`` static fall-through):
        package-bundled ``STATIC_DIR`` → ``assets/``-prefix-strip retry against
        ``STATIC_DIR`` → the workspace tree → the rendered ``reports/`` dir
        (served unconditionally → 404 when absent).  HTTP 403 on a ``..`` path
        segment; served with the guessed bare mime + ``Cache-Control: no-store``.

        Library-backed via ``lib.static_serving.resolve_asset``.
        """
        rel = rel.lstrip("/")
        if ".." in rel.split("/"):
            return Response(status_code=403)
        target = _static_serving.resolve_asset(ws, rel)
        return _serve_static_file(target, rel)

    if _readonly_enabled():
        _apply_readonly_filter(app)

    return app


app = create_app()
