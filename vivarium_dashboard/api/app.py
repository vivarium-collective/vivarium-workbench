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

import subprocess

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from vivarium_dashboard.lib import composite_run_views as _cr_views
from vivarium_dashboard.lib import lifecycle_mutations as _lifecycle_mut
from vivarium_dashboard.lib import composite_state_views as _composite_state_views
from vivarium_dashboard.lib import data_sources as _data_sources
from vivarium_dashboard.lib import download_views as _download_views
from vivarium_dashboard.lib import events as _events
from vivarium_dashboard.lib import explorer_data as _explorer_data
from vivarium_dashboard.lib import metadata_mutations as _meta_mut
from vivarium_dashboard.lib import study_crud_mutations as _study_crud_mut
from vivarium_dashboard.lib import git_status as _git_status
from vivarium_dashboard.lib import investigation_status
from vivarium_dashboard.lib import investigation_views as _inv_views
from vivarium_dashboard.lib import observables_views as _obs_views
from vivarium_dashboard.lib import report_views as _report_views
from vivarium_dashboard.lib import rigor_views as _rigor_views
from vivarium_dashboard.lib import saved_visualizations as _saved_viz
from vivarium_dashboard.lib import static_serving as _static_serving
from vivarium_dashboard.lib import study_page as _study_page
from vivarium_dashboard.lib import study_spec as _study_spec
from vivarium_dashboard.lib import study_viz_views as _study_viz
from vivarium_dashboard.lib import system_info as _system_info
from vivarium_dashboard.lib import work_views as _work_views
from vivarium_dashboard.lib import workspace_deps_views as _workspace_deps
from vivarium_dashboard.lib.composite_resolve import resolve_composite
from vivarium_dashboard.lib.composites_query import composites_via_subprocess
from vivarium_dashboard.lib.models import (
    BibEntry,
    BranchDiff,
    BranchesPayload,
    BranchInfo,
    BranchCommit,
    BranchStaleness,
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
    DashConfig,
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
    InvestigationHypothesesPayload,
    InvestigationSummary,
    InvestigationRigor,
    InvestigationVizHtmlPayload,
    InvestigationsPayload,
    LinkageIndex,
    ObservablesPayload,
    ReferencesBibPayload,
    StudyObservableCheck,
    StudyDetail,
    StudyRigor,
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
    PendingEntries,
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
)
from vivarium_dashboard.lib.catalog import build_catalog
from vivarium_dashboard.lib.registry import build_registry
from vivarium_dashboard.lib.visualization_classes import list_visualization_classes
from vivarium_dashboard.lib.simulations_index import list_simulations
from vivarium_dashboard.lib.study_charts import build_study_charts_payload

WORKSPACE_ENV = "VIVARIUM_DASHBOARD_WORKSPACE"


def get_workspace() -> Path:
    """Resolve the workspace root (overridable in tests via dependency_overrides)."""
    return Path(os.environ.get(WORKSPACE_ENV, ".")).resolve()


_OPENAPI_TAGS = [
    {
        "name": "System",
        "description": "Service health and client-configuration endpoints.",
    },
    {
        "name": "System & workspace",
        "description": (
            "Workspace-level read-only info: framework-self metrics, GitHub "
            "repo slug, UI feature flags, and workspace narrative metadata. "
            "All routes always return HTTP 200 (best-effort; errors degrade "
            "to empty-default bodies)."
        ),
    },
    {
        "name": "Workspace & source",
        "description": (
            "Workspace-switcher dropdown, remote sms-api build list, and "
            "catalog system-dependency check.  source/builds and workspaces "
            "always return HTTP 200 (best-effort); system-deps-check returns "
            "400/404/200."
        ),
    },
    {
        "name": "Simulations",
        "description": (
            "Workspace-wide simulation run index — all runs across all studies."
        ),
    },
    {
        "name": "Investigations",
        "description": (
            "Investigation and study metadata: sidebar summary list and full "
            "per-study index."
        ),
    },
    {
        "name": "Studies & visualizations",
        "description": (
            "Per-study charts (live + static), saved 3D/report-card "
            "visualizations, and registered visualization classes."
        ),
    },
    {
        "name": "Composites",
        "description": (
            "Composite spec/generator discovery and single-composite resolution."
        ),
    },
    {
        "name": "Registry & catalog",
        "description": (
            "Process/type/emitter registry and pbg package catalog for the "
            "active workspace."
        ),
    },
    {
        "name": "References & data",
        "description": (
            "BibTeX reference entries (with DOI enrichment) and workspace "
            "data-source bundle."
        ),
    },
    {
        "name": "Git & branches",
        "description": (
            "Read-only git/branch status endpoints: live sync state, workstream "
            "activity, branch staleness, dirty-file list, stage-branch index, "
            "and branch diff summary."
        ),
    },
    {
        "name": "Investigations detail",
        "description": (
            "Per-investigation detail endpoints: viz HTML files, composite "
            "baseline list, rigor roll-up, composite YAML document, and "
            "competing hypotheses with support-log enrichment."
        ),
    },
    {
        "name": "Rigor",
        "description": (
            "Deterministic evidence/rigor scorecards computed by "
            "pbg_superpowers.rigor over the run-merged study spec: per-study "
            "and per-investigation roll-up."
        ),
    },
    {
        "name": "Studies detail",
        "description": (
            "Full per-study run-merged detail spec (the same payload the SPA "
            "study-detail page consumes): runs, simulation_set, param_enforcement, "
            "expert_feedback, spine_acceptance, and all lifecycle-derived keys."
        ),
    },
    {
        "name": "Data explorer",
        "description": (
            "Analyses Data Explorer endpoints: run-picker list, observable "
            "discovery, time-series, flux map, vector snapshots, and protein "
            "breakdown.  All routes always return HTTP 200 — errors are carried "
            "in the body under an ``error`` key with empty-default data fields."
        ),
    },
    {
        "name": "Reports & inputs",
        "description": (
            "Report-readiness linter, linkage-index graph, needs-attention "
            "scan, investigation inputs, and iset detail.  All routes except "
            "``/api/iset/{slug}`` always return HTTP 200 — errors degrade "
            "gracefully to empty payloads rather than 500."
        ),
    },
    {
        "name": "Observables",
        "description": (
            "Never-fabricate observable guard + SP4a/SP4b navigate surface: the "
            "in-process composite build's emittable observables, per-study "
            "readout validation against the real composite structure, and the "
            "deterministic linkage index/queries (AC→study gating, source↔study, "
            "finding-by-observable, study DAG, observable registry)."
        ),
    },
    {
        "name": "Composite runs",
        "description": (
            "File-backed composite-run read routes: list runs for a spec, fetch a "
            "run's trajectory or a single-step state snapshot, and poll lightweight "
            "status (progress, terminal-state error excerpt, completed viz_html). "
            "All read from ``.pbg/composite-runs.db``."
        ),
    },
    {
        "name": "Downloads",
        "description": (
            "Binary / HTML file-download routes (FileResponse / Response, not a "
            "JSON model): study-export zip, single data-source bundle file, "
            "per-investigation HTML report, latest guidance HTML, and the "
            "investigation notebook/script export.  Each reproduces the legacy "
            "Content-Type, inline-vs-attachment disposition, and status codes "
            "(incl. guidance 204 No Content). Error paths return ``{\"error\": "
            "...}`` JSON."
        ),
    },
    {
        "name": "Events",
        "description": (
            "Server-Sent Events stream: polls ``workspace.yaml`` and emits an "
            "``event: state`` frame whenever the file changes.  First event fires "
            "immediately if the file already exists.  Uses raw "
            "``StreamingResponse`` (no sse-starlette dep)."
        ),
    },
    {
        "name": "Investigation & study mutations",
        "description": (
            "Batch 18 POST routes — metadata writers for investigations and studies: "
            "set observables, conclusions, overview, status, objective, "
            "narrative-spine fields, and expert model-settings.  Each route "
            "delegates to a pure lib builder in ``lib.metadata_mutations``.  "
            "CSRF guard is deferred to the state/flip batch; the live do_POST "
            "still enforces it via ``_csrf_ok``.  Errors carry ``{error: ...}`` "
            "at 400/404/500; success returns ``{ok: true}`` or the mutated record."
        ),
    },
    {
        "name": "Study CRUD",
        "description": (
            "Batch 19 POST routes — variant/baseline/intervention/run/comparison "
            "CRUD writers for v3 studies.  Each route delegates to a pure lib "
            "builder in ``lib.study_crud_mutations``.  CSRF guard is deferred to "
            "the flip batch; the live do_POST still enforces it via ``_csrf_ok``.  "
            "Errors carry ``{error: ...}`` at 400/404/409; success returns "
            "``{ok: true}`` or ``{ok: true, name: ...}``."
        ),
    },
    {
        "name": "Study lifecycle",
        "description": (
            "Batch 20 POST routes — study lifecycle writers and feedback actions: "
            "seed a child study from a followup/finding, apply a tracked feedback "
            "action, rename a study, create a study from a scratchpad run, sync "
            "study runs from runs.db, and accept/decline a proposed input.  Each "
            "route delegates to a pure lib builder in ``lib.lifecycle_mutations``.  "
            "CSRF guard is deferred to the flip batch.  Errors carry "
            "``{error: ...}`` at 400/404/409/500; success returns the respective "
            "payload dict."
        ),
    },
    {
        "name": "Static & shell",
        "description": (
            "Static-asset + SPA-shell serving (FileResponse, not a JSON model): "
            "the ``/`` index shell (best-effort re-render then serve "
            "``reports/index.html``), the standalone ``bigraph-loom`` and "
            "``pbg_parsimony`` viewer bundles, and a catch-all asset route. The "
            "catch-all is registered LAST so every specific route (all "
            "``/api/*``, ``/``, the viewers, ``/docs``) matches first. All served "
            "files carry ``Cache-Control: no-store`` with the legacy bare mime."
        ),
    },
]


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

    @app.get("/health", tags=["System"], summary="Service liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/simulations",
        response_model=SimulationsPayload,
        tags=["Simulations"],
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
        "/api/config",
        response_model=DashConfig,
        tags=["System"],
        summary="Client data-source mode selector",
    )
    def config() -> DashConfig:
        """Client data-source selector (mirrors the stdlib /api/config)."""
        return DashConfig(mode="local-server")

    @app.get(
        "/api/iset-list",
        response_model=list[InvestigationSummary],
        tags=["Investigations"],
        summary="Investigation summary list for the sidebar",
    )
    def iset_list(ws: Path = Depends(get_workspace)) -> list[InvestigationSummary]:
        """Investigations summary list (mirrors the stdlib /api/iset-list).

        Fully library-backed: builds the payload via
        ``lib.investigation_status.build_iset_summary`` and supplies the
        runs-presence signal from the simulations index — no dependency on the
        stdlib server module.
        """
        run_slugs = investigation_status.study_run_slugs(ws)

        def study_has_runs(slug: str, spec: dict) -> bool:
            # Parity with the stdlib path's _count_runs_for_study(...) > 0:
            # a study has runs if the index records one, or its spec lists runs.
            return slug in run_slugs or bool((spec or {}).get("runs"))

        summaries = investigation_status.build_iset_summary(ws, study_has_runs=study_has_runs)
        return [InvestigationSummary.model_validate(d) for d in summaries]

    @app.get(
        "/api/data-sources",
        response_model=DataSourcesPayload,
        tags=["References & data"],
        summary="Workspace data-source bundle (from workspace.yaml)",
    )
    def data_sources(ws: Path = Depends(get_workspace)) -> DataSourcesPayload:
        """Repo-wide data-source bundle (workspace.yaml `dashboard.data_sources`
        provider), via lib.data_sources — no stdlib server dependency."""
        return DataSourcesPayload.model_validate(_data_sources.enumerate_data_sources(ws))

    @app.get(
        "/api/references-bib",
        response_model=ReferencesBibPayload,
        tags=["References & data"],
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
        tags=["Studies & visualizations"],
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
        tags=["Studies & visualizations"],
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
        tags=["Studies & visualizations"],
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
        tags=["Studies & visualizations"],
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
        tags=["Studies & visualizations"],
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
        tags=["Studies & visualizations"],
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
        tags=["Studies & visualizations"],
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
        ref: str, ws: Path = Depends(get_workspace)
    ) -> Optional[CompositeResolvePayload]:
        """Resolve a single composite spec or generator by ID.

        Mirrors ``GET /api/composite-resolve?ref=<spec_id>`` from the stdlib
        server.  Returns the composite payload when found, or ``null`` (200 with
        null body) when ``ref`` doesn't match any spec or generator — identical
        miss-behaviour to the legacy handler.

        Library-backed via ``lib.composite_resolve.resolve_composite`` — the
        single implementation the stdlib ``_composite_resolve_data`` now forwards
        to.
        """
        result = resolve_composite(ws, ref)
        if result is None:
            return None
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
        tags=["Git & branches"],
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
        tags=["Git & branches"],
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
        "/api/branch-staleness",
        response_model=BranchStaleness,
        tags=["Git & branches"],
        summary="How many commits is a branch behind its base?",
    )
    def branch_staleness_route(
        branch: Optional[str] = None,
        base: str = "main",
        ws: Path = Depends(get_workspace),
    ) -> Union[BranchStaleness, JSONResponse]:
        """Branch staleness check (commits behind base).

        ``?branch=<name>`` defaults to the workspace's current HEAD.
        ``?base=<name>`` defaults to ``main``.

        HTTP 400 when neither ``?branch=`` is given nor the current HEAD can
        be determined (detached HEAD / not a git repo). The 400 body is
        ``{"error": <msg>}`` — byte-identical to the legacy handler (not
        FastAPI's default ``{"detail": ...}``).

        Library-backed via ``lib.git_status.build_branch_staleness``.
        """
        try:
            payload = _git_status.build_branch_staleness(ws, branch=branch, base=base)
        except _git_status.NoBranchError as exc:
            # Legacy emits the NoBranchError message verbatim under "error".
            return JSONResponse(status_code=400, content={"error": str(exc)})
        return BranchStaleness.model_validate(payload)

    @app.get(
        "/api/dirty-status",
        response_model=DirtyStatus,
        tags=["Git & branches"],
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
        "/api/branches",
        response_model=BranchesPayload,
        tags=["Git & branches"],
        summary="stage/* branches with last-commit info",
    )
    def branches_route(
        ws: Path = Depends(get_workspace),
    ) -> Union[BranchesPayload, JSONResponse]:
        """List ``stage/*`` branches with last-commit SHA/subject/date and
        commits-ahead-of-main count.

        Returns ``{branches: [], current: <HEAD>}`` when no stage branches
        exist. Per-branch errors are swallowed by the builder, but a top-level
        git failure (the builder returns ``{"error": ...}``) maps to HTTP 500
        with that exact ``{"error": ...}`` body — byte-identical to the legacy
        ``_serve_branches``.

        Library-backed via ``lib.git_status.list_branches``.
        """
        payload = _git_status.list_branches(ws)
        if "error" in payload:
            return JSONResponse(status_code=500, content={"error": payload["error"]})
        raw_branches = payload.get("branches") or []
        branch_list = []
        for b in raw_branches:
            lc = b.get("last_commit") or {}
            branch_list.append(BranchInfo(
                name=b["name"],
                last_commit=BranchCommit.model_validate(lc),
                ahead_of_main=b.get("ahead_of_main", 0),
            ))
        return BranchesPayload(
            branches=branch_list,
            current=payload.get("current"),
        )

    @app.get(
        "/api/branch-diff",
        response_model=BranchDiff,
        tags=["Git & branches"],
        summary="Short diff summary for a branch vs main",
    )
    def branch_diff_route(
        branch: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[BranchDiff, JSONResponse]:
        """Short log + diff-stat summary for ``?branch=<name>`` vs ``main``.

        HTTP 400 when ``?branch=`` is missing/empty or contains unsafe
        characters — matching the legacy ``_get_branch_diff``.  (``branch`` is
        declared Optional so a missing query param yields a 400, not FastAPI's
        422 "field required".)  The 400 body is the legacy verbatim
        ``{"error": "invalid branch name"}`` — NOT the builder's more detailed
        ``ValueError`` text.

        Library-backed via ``lib.git_status.build_branch_diff``.
        """
        try:
            payload = _git_status.build_branch_diff(ws, branch or "")
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "invalid branch name"})
        return BranchDiff.model_validate(payload)

    @app.get(
        "/api/pending",
        response_model=PendingEntries,
        tags=["Git & branches"],
        summary="Pending entries from unmerged stage/* branches",
    )
    def pending_route(
        ws: Path = Depends(get_workspace),
    ) -> Union[PendingEntries, JSONResponse]:
        """Unmerged ``stage/*`` branch entries not yet on ``main``'s ``workspace.yaml``.

        Returns ``{observables, visualizations, phases, datasets,
        references_pdfs, expert_docs, imports}`` — each a list of
        ``{entry, branch}`` objects for entries new relative to ``main``.
        Returns ``{}`` (empty lists) when there are no stage branches or the
        workspace is not a git repo.

        HTTP 200 on success; HTTP 500 ``{error}`` when an unexpected exception
        escapes the inner git walk.

        Library-backed via ``lib.work_views.build_pending``.
        """
        body, status = _work_views.build_pending(ws)
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return PendingEntries.model_validate(body)

    @app.get(
        "/api/generation",
        response_model=Generation,
        tags=["Git & branches"],
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
        tags=["Git & branches"],
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
        tags=["Investigations detail"],
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
        tags=["Investigations detail"],
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
        tags=["Investigations detail"],
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
        tags=["Investigations detail"],
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
        tags=["Investigations detail"],
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
        "/api/study-rigor",
        response_model=StudyRigor,
        tags=["Rigor"],
        summary="Per-study evidence & rigor scorecard",
    )
    def study_rigor_route(
        study: Optional[str] = None,
        investigation: Optional[str] = None,
        ws: Path = Depends(get_workspace),
    ) -> Union[StudyRigor, JSONResponse]:
        """Per-study rigor scorecard (mirrors the stdlib /api/study-rigor).

        Deterministic dimensions (replication, negative controls, alternative
        hypotheses, claim discipline, falsifiability, …) from
        ``pbg_superpowers.rigor`` over the **run-merged** study spec — the
        runs.db merge feeds the replication + run-persistence dimensions.

        ``?study=`` selects the study (legacy ``?investigation=`` alias also
        accepted). HTTP 400 when neither is given; HTTP 404 when the study has
        no spec file. Error bodies are ``{"error": <msg>}`` — byte-identical to
        the legacy ``_get_study_rigor`` (not FastAPI's ``{"detail": ...}``).  A
        rigor-computation failure degrades to a 200 body carrying ``error`` plus
        empty ``dimensions``/``score``/``summary``.

        Library-backed via ``lib.rigor_views.build_study_rigor``.
        """
        slug = study or investigation
        try:
            body = _rigor_views.build_study_rigor(ws, slug)
        except _rigor_views.RigorViewError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body)
        return StudyRigor.model_validate(body)

    @app.get(
        "/api/investigation-rigor",
        response_model=InvestigationRigor,
        tags=["Rigor"],
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
        tags=["Studies detail"],
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
        tags=["Data explorer"],
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
        tags=["Data explorer"],
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
        tags=["Data explorer"],
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
        tags=["Data explorer"],
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
        tags=["Data explorer"],
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
        tags=["Data explorer"],
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
        tags=["Reports & inputs"],
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
        tags=["Reports & inputs"],
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
        tags=["Reports & inputs"],
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
        "/api/iset/{slug}",
        response_model=IsetDetail,
        tags=["Reports & inputs"],
        summary="Full investigation detail (one investigation.yaml + resolved studies)",
    )
    def iset_detail(
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
        tags=["Observables"],
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
        tags=["Observables"],
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
        tags=["Observables"],
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
        tags=["System & workspace"],
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
        tags=["System & workspace"],
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
        tags=["System & workspace"],
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
        tags=["System & workspace"],
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
        tags=["Composite runs"],
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
        tags=["Composite runs"],
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
        tags=["Composite runs"],
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
        tags=["Composite runs"],
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
        tags=["Workspace & source"],
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
        tags=["Workspace & source"],
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
        tags=["Workspace & source"],
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
        "/api/iset/{slug}/report",
        tags=["Downloads"],
        summary="Per-investigation HTML report file",
        response_class=Response,
    )
    def iset_report_route(
        slug: str,
        ws: Path = Depends(get_workspace),
    ) -> Response:
        """Serve the per-investigation report ``index.html`` as ``text/html``
        (mirrors the stdlib GET /api/iset/<slug>/report).

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

    @app.get("/api/events", tags=["Events"], summary="SSE workspace-state stream")
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
        tags=["Investigation & study mutations"],
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
        tags=["Investigation & study mutations"],
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
        tags=["Investigation & study mutations"],
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
        tags=["Investigation & study mutations"],
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
        tags=["Investigation & study mutations"],
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
        "/api/study-narrative-set",
        tags=["Investigation & study mutations"],
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
        tags=["Investigation & study mutations"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study CRUD"],
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
        tags=["Study lifecycle"],
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
        tags=["Study lifecycle"],
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
        tags=["Study lifecycle"],
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
        tags=["Study lifecycle"],
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
        tags=["Study lifecycle"],
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
        tags=["Study lifecycle"],
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

    return app


app = create_app()
