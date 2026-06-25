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

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from vivarium_dashboard.lib import data_sources as _data_sources
from vivarium_dashboard.lib import git_status as _git_status
from vivarium_dashboard.lib import investigation_status
from vivarium_dashboard.lib import investigation_views as _inv_views
from vivarium_dashboard.lib import saved_visualizations as _saved_viz
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
    CompositesPayload,
    DashConfig,
    DataSourcesPayload,
    DirtyFile,
    DirtyStatus,
    GitStatus,
    InvestigationCompositeDocPayload,
    InvestigationCompositesPayload,
    InvestigationHypothesesPayload,
    InvestigationSummary,
    InvestigationVizHtmlPayload,
    InvestigationsPayload,
    ReferencesBibPayload,
    RegistryPayload,
    SavedVisualizationsPayload,
    SimRow,
    SimulationsPayload,
    StudyChartsPayload,
    VisualizationClassesPayload,
    VizClass,
    WorkStatusActive,
    WorkStatusInactive,
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

    # NOTE: /api/investigation-rigor is intentionally NOT ported in this batch.
    # It depends on per-study run-merging (server._study_detail_spec merges
    # runs.db + reconciles simulation_set), which pbg_superpowers.rigor reads
    # via spec["runs"] (replication + run-persistence dimensions). Extracting
    # that run-merging loader belongs with Batch 3 (study/<slug>, study-rigor),
    # so the rigor route stays on the legacy stdlib handler for now.

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

    return app


app = create_app()
