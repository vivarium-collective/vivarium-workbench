"""Typed data models for the dashboard's JSON payloads.

These pydantic models are the single source of truth for the shapes the server
sends the browser. Today the handlers build plain ``dict``s; these models mirror
those dicts exactly so they can be adopted incrementally:

- validate a handler's output in tests (catches drift),
- construct-then-``model_dump()`` at a seam (identical JSON out, typed within),
- and, later, generate the client-side TypeScript types from these definitions.

Each model documents the handler it mirrors. Field names and optionality are
copied verbatim from those handlers — do not "tidy" them without changing the
handler too, or the JSON contract drifts.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Which emitter wrote a run's store (derived from its store path).
EmitterKind = Literal["xarray", "parquet", "sqlite"]

# sms-api job lifecycle (JobStatus enum, mirrored).
RemoteJobStatus = Literal[
    "unknown", "waiting", "pending", "queued",
    "running", "completed", "cancelled", "failed",
]


class RemoteOrigin(BaseModel):
    """Provenance of a run executed on a remote deployment (sms-api).

    Built in ``lib/simulations_index.py`` ``_row_to_dict`` from the run's
    ``params_json`` provenance.
    """

    deployment: str            # prov["source"], e.g. "smsvpctest"
    simulation_id: int
    experiment_id: Optional[str] = None
    backend: Optional[str] = None       # e.g. "ray"
    s3_uri: Optional[str] = None


class StudyRef(BaseModel):
    """A study a run is attached to (filled in by ``_annotate_studies``)."""

    slug: str
    label: Optional[str] = None


class SimRow(BaseModel):
    """One row of the Simulations DB.

    Mirrors ``_row_to_dict`` in ``lib/simulations_index.py``. ``emitter`` is
    store-derived for remote runs (``None`` -> caller falls back to the SQLite
    ``db_path`` label).
    """

    # Types/nullability copied from the runs_meta schema (lib/composite_runs.py):
    # started_at/completed_at are REAL epoch seconds; sim_name/label/n_steps/
    # progress_step are nullable (some are ALTER-added columns).
    run_id: str
    spec_id: str
    sim_name: Optional[str] = None
    label: Optional[str] = None
    status: str                # "completed" | "running" | "failed" | ...
    n_steps: Optional[int] = None
    progress_step: Optional[int] = None
    started_at: float          # unix epoch seconds (REAL NOT NULL)
    completed_at: Optional[float] = None
    db_path: str
    store_path: Optional[str] = None   # native data store (zarr/parquet dir or s3 uri); None -> data lives in db_path
    emitter: Optional[EmitterKind] = None
    studies: list[StudyRef] = []
    study_slug: Optional[str] = None
    investigation_slug: Optional[str] = None
    remote_origin: Optional[RemoteOrigin] = None


class SimulationsPayload(BaseModel):
    """``GET /api/simulations`` payload (server.py ``_simulations_data``)."""

    simulations: list[SimRow]
    current: Optional[str] = None     # current branch slug


class RemoteRunStep(BaseModel):
    """One pipeline step of a remote run. ``name`` is one of STEP_NAMES."""

    name: str                  # push | build | run | poll | download | land
    status: str                # pending | running | ok | error | ...
    message: str = ""


class RemoteRunJob(BaseModel):
    """A remote-run job (``lib/remote_run_jobs.py`` ``RemoteRunJob.to_dict``)."""

    job_id: str
    study: str
    status: RemoteJobStatus
    steps: list[RemoteRunStep]
    run_id: Optional[str] = None
    simulation_id: Optional[int] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class ChartPayload(BaseModel):
    """One chart in a study-charts payload (``lib/study_charts.py``).

    Charts are polymorphic by ``source``: ``live`` charts carry an inline ``svg``;
    ``static`` / ``declared`` charts may instead carry an ``img`` data-URI (PNG/GIF)
    plus ``media`` / ``freshness`` / ``simulations`` / ``interpretation``. Only the
    common keys are enumerated; ``extra='allow'`` preserves any source-specific
    field (and ``svg`` is optional since image charts use ``img``).
    """

    model_config = ConfigDict(extra="allow")

    key: str
    title: str
    caption: str
    svg: Optional[str] = None
    img: Optional[str] = None
    source: Optional[str] = None
    media: Optional[str] = None
    freshness: Optional[str] = None
    simulations: Optional[str] = None
    interpretation: Optional[str] = None
    data_source: Optional[str] = None


class StudyChartsPayload(BaseModel):
    """``GET /api/study-charts/<slug>`` payload.

    Source: ``lib.study_charts.build_study_charts_payload`` (the single
    implementation shared by the FastAPI seam and the stdlib server's
    ``_study_charts_payload`` forwarder). Every field below is always present.
    """

    study: str
    schema_version: Optional[Any] = None
    charts: list[ChartPayload]
    db_exists: bool
    static_count: int
    live_count: int


class DashConfig(BaseModel):
    """``GET /api/config`` payload (server.py ``Handler._build_api_config_response``).

    Selects the client data source; local mode returns ``{"mode": "local-server"}``.
    """

    mode: str = "local-server"
    basePath: Optional[str] = None


class InvestigationSummary(BaseModel):
    """One entry of the ``GET /api/iset-list`` payload.

    Mirrors ``_build_iset_summary_for_test`` in server.py. A parse failure yields
    a minimal ``{name, error}`` entry, so every field but ``name`` is optional.
    ``lifecycle`` is passed through untyped (``Any``) for now.
    """

    name: str
    title: Optional[str] = None
    status: Optional[str] = None
    effective_status: Optional[str] = None
    description: Optional[str] = None
    question: Optional[str] = None
    hypothesis: Optional[str] = None
    n_studies: Optional[int] = None
    studies: list[str] = []
    lifecycle: Any = None
    current: Optional[bool] = None
    error: Optional[str] = None


class DataSource(BaseModel):
    """One entry of the ``GET /api/data-sources`` ``sources`` list."""

    key: str
    path: str = ""
    category: str = "uncategorized"
    kind: str = "inherited"
    size_bytes: int = 0
    url: str = ""


class DataSourcesPayload(BaseModel):
    """``GET /api/data-sources`` payload (lib.data_sources.enumerate_data_sources)."""

    label: Optional[str] = None
    sources: list[DataSource] = []
    error: Optional[str] = None


class BibEntry(BaseModel):
    """One parsed references.bib entry. Bibtex fields vary, so unknown keys are
    preserved (``extra="allow"``) rather than stripped — only ``key`` is required.
    The documented fields below + enrichment fields (enriched_doi, publisher_url,
    oa_pdf_url, oa_status, …) are added in place by the references-fetch cache."""

    model_config = ConfigDict(extra="allow")

    key: str
    type: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    journal: Optional[str] = None
    year: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    note: Optional[str] = None


class ReferencesBibPayload(BaseModel):
    """``GET /api/references-bib`` payload (server.py ``_get_references_bib``)."""

    entries: list[BibEntry] = []


class SavedViz(BaseModel):
    """One saved 3D visualization pack (``saved`` list)."""

    study: str
    name: str
    pack_url: str
    meta_url: Optional[str] = None
    n_placed: Optional[int] = None
    created: Optional[int] = None
    viewer_url: Optional[str] = None  # set only when ui.viz_viewer_urls maps it


class PtoolsStudy(BaseModel):
    study: str
    n_tsvs: int


class PtoolsInfo(BaseModel):
    configured: bool = False
    studies: list[PtoolsStudy] = []


class ReportCard(BaseModel):
    """One saved comparison report card (``report_cards`` list)."""

    study: Optional[str] = None
    name: str
    url: str
    verdict: Optional[str] = None
    created: Optional[int] = None


class SavedVisualizationsPayload(BaseModel):
    """``GET /api/saved-visualizations`` payload (lib.saved_visualizations)."""

    parsimony_available: bool = False
    saved: list[SavedViz] = []
    ptools: PtoolsInfo = PtoolsInfo()
    report_cards: list[ReportCard] = []


class VizClass(BaseModel):
    """One entry in the ``GET /api/visualization-classes`` ``classes`` list.

    Fields vary slightly between visualization and analysis kinds, so unknown
    keys are preserved (``extra="allow"``).
    """

    model_config = ConfigDict(extra="allow")

    address: str
    name: str
    doc: str = ""
    kind: str = "visualization"


class VisualizationClassesPayload(BaseModel):
    """``GET /api/visualization-classes`` payload
    (lib.visualization_classes.list_visualization_classes).

    Returns all Visualization subclasses registered in the workspace's core
    registry plus standard pbg-superpowers visualization classes and, when
    v2ecoli is installed, its Analysis classes.
    """

    model_config = ConfigDict(extra="allow")

    classes: list[VizClass] = []


class RegistryProcess(BaseModel):
    """One process/step/emitter/visualization entry in the registry.

    The subprocess script in ``lib.registry.build_registry`` emits these fields;
    ``extra="allow"`` preserves any future additions without breaking the route.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    address: str = ""
    kind: str = "other"
    schema_preview: str = ""
    aliases: list[str] = []
    source: str = "environment_only"


class RegistryType(BaseModel):
    """One type-schema entry in the registry.

    ``extra="allow"`` for forward-compatibility.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    schema_preview: str = ""


class RegistryImport(BaseModel):
    """One imported-repository metadata entry (workspace.yaml imports)."""

    model_config = ConfigDict(extra="allow")

    name: str
    package: str = ""
    source: Optional[str] = None
    ref: Optional[str] = None
    description: str = ""


class RegistryPayload(BaseModel):
    """``GET /api/registry`` payload (lib.registry.build_registry).

    Returns the process/type registry discovered from the workspace's
    ``build_core()`` subprocess. Extra keys (e.g. ``workspace_pkgs``,
    ``registry_include``, ``default_emitter``, ``error``) are preserved by
    ``extra="allow"`` so the frontend receives the full payload unchanged.
    """

    model_config = ConfigDict(extra="allow")

    processes: list[RegistryProcess] = []
    types: list[RegistryType] = []
    imports: list[RegistryImport] = []


class CompositeRecord(BaseModel):
    """One composite entry in the ``GET /api/composites`` payload.

    Composites come in two kinds: ``spec`` (a ``*.composite.yaml`` / ``.json``
    file) and ``generator`` (a ``@composite_generator``-decorated Python function).
    Generator entries carry varied additional fields (``parameters``,
    ``visualizations``, ``workspace_local``, etc.) that differ across workspaces;
    ``extra="allow"`` passes them through untouched so the browser receives the
    full payload.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    kind: str = "spec"
    module: str = ""


class CompositesPayload(BaseModel):
    """``GET /api/composites`` payload (lib.composites_query.composites_via_subprocess).

    Discovery runs in a fresh subprocess to avoid stale ``sys.modules`` hiding
    generators.  On subprocess failure the route returns an empty list with an
    ``error`` string rather than a 500.
    """

    composites: list[CompositeRecord] = []
    workspace_package: Optional[str] = None
    error: Optional[str] = None


class InvestigationRow(BaseModel):
    """One row of the ``GET /api/investigations`` ``investigations`` list.

    The row has ~26 keys. The stable scalar/count fields are typed; the rest
    (including the invalid-row shape ``{name, status: "invalid", error}``) are
    preserved via ``extra="allow"``.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    status: Optional[str] = None
    phase: Optional[str] = None
    n_studies: Optional[int] = None
    n_simulations: Optional[int] = None
    description: Optional[str] = None
    error: Optional[str] = None


class InvestigationsPayload(BaseModel):
    """``GET /api/investigations`` payload
    (lib.investigations_index.build_investigations).

    Returns the per-study index used by the Investigations tab.  Each row is
    either a full ``InvestigationRow`` (valid spec) or a minimal
    ``{name, status: "invalid", error}`` entry (malformed spec.yaml).
    ``extra="allow"`` on ``InvestigationRow`` preserves all ~26 builder keys.
    """

    investigations: list[InvestigationRow] = []


class CatalogModule(BaseModel):
    """One module entry in the ``GET /api/catalog`` ``modules`` list.

    Source: ``lib.catalog.build_catalog``.  The stable display/install keys
    are enumerated; install-source, tags, and workspace-specific metadata vary
    across entries, so ``extra="allow"`` preserves them intact.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    installed: bool = False
    install_source: Optional[str] = None
    module: Optional[str] = None
    description: Optional[str] = None


class CatalogPayload(BaseModel):
    """``GET /api/catalog`` payload (lib.catalog.build_catalog).

    Returns the pbg module catalog annotated with per-workspace install state.
    ``extra="allow"`` forwards any top-level keys the builder may add in future
    (e.g. ``error``).
    """

    model_config = ConfigDict(extra="allow")

    modules: list[CatalogModule] = []


# ---------------------------------------------------------------------------
# Git / branch models
# ---------------------------------------------------------------------------

class GitStatus(BaseModel):
    """``GET /api/git-status`` payload (lib.git_status.build_git_status).

    Live sync state for the workspace's git.  All network-dependent fields are
    Optional so the response degrades gracefully when origin is absent.
    ``extra="allow"`` preserves any future extension keys.
    """

    model_config = ConfigDict(extra="allow")

    upstream_repo: Optional[str] = None
    branch: Optional[str] = None
    push_state: str = "no_origin"
    ahead: int = 0
    behind: int = 0
    branch_url: Optional[str] = None
    repo_url: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    base: str = "main"
    ahead_of_base: int = 0
    dirty_count: int = 0
    compare_url: Optional[str] = None
    pr_state: Optional[str] = None
    gh_available: bool = False
    has_active_workstream: bool = False


class WorkStatusInactive(BaseModel):
    """``GET /api/work-status`` payload when no workstream is active.

    Byte-identical to the legacy ``{"active": false}`` — exactly one key. The
    inactive and active responses are modelled as a discriminated union (on
    ``active``) so the inactive path does not leak the active model's null
    defaults (legacy emits only this one key when inactive).
    """

    active: Literal[False] = False


class WorkStatusActive(BaseModel):
    """``GET /api/work-status`` payload when a workstream IS active.

    Every field is always present — legacy ``_get_work_status`` emits them even
    when null (notably ``pr_number`` / ``pr_url`` when no PR is linked), so they
    are NOT excluded.  ``extra="allow"`` preserves any future extension keys.
    """

    model_config = ConfigDict(extra="allow")

    active: Literal[True] = True
    branch: Optional[str] = None
    base: Optional[str] = None
    commits_ahead: Optional[int] = None
    commits_behind: Optional[int] = None
    behind_ref: Optional[str] = None
    stale: Optional[bool] = None
    stale_threshold: Optional[int] = None
    unpushed: Optional[int] = None
    pushed: Optional[bool] = None
    has_origin: Optional[bool] = None
    gh_available: Optional[bool] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None


class BranchStaleness(BaseModel):
    """``GET /api/branch-staleness`` payload (lib.git_status.build_branch_staleness).

    HTTP 400 when the branch cannot be determined (no ``?branch=`` and HEAD is
    detached / not on a named branch).
    """

    branch: str
    base: str = "main"
    behind_ref: str = ""
    commits_behind: int = 0
    stale_threshold: int = 20
    stale: bool = False


class DirtyFile(BaseModel):
    """One entry in the ``DirtyStatus.files`` list."""

    status: str
    path: str


class DirtyStatus(BaseModel):
    """``GET /api/dirty-status`` payload (lib.git_status.build_dirty_status).

    HTTP 500 when ``git status`` itself fails.
    """

    count: int = 0
    files: list[DirtyFile] = []


class BranchCommit(BaseModel):
    """Last-commit summary for a stage branch."""

    model_config = ConfigDict(extra="allow")

    sha: str = ""
    subject: str = ""
    date: str = ""


class BranchInfo(BaseModel):
    """One branch entry in the ``GET /api/branches`` payload."""

    name: str
    last_commit: BranchCommit = BranchCommit()
    ahead_of_main: int = 0


class BranchesPayload(BaseModel):
    """``GET /api/branches`` payload (lib.git_status.list_branches).

    Returns all ``stage/*`` branches with last-commit info.  ``extra="allow"``
    preserves an ``error`` key returned on subprocess failure.
    """

    model_config = ConfigDict(extra="allow")

    branches: list[BranchInfo] = []
    current: Optional[str] = None


class BranchDiff(BaseModel):
    """``GET /api/branch-diff`` payload (lib.git_status.build_branch_diff).

    HTTP 400 when ``?branch=`` is missing or contains unsafe characters.
    """

    branch: str
    log: str = ""
    diff_stat: str = ""


# ---------------------------------------------------------------------------
# Investigation detail models
# ---------------------------------------------------------------------------

class VizHtmlFile(BaseModel):
    """One HTML viz file entry in the ``GET /api/investigation-viz-html`` payload."""

    name: str        # stem of the .html filename (e.g. "time_series_plot")
    html_path: str   # workspace-relative path served by the static-file handler


class InvestigationVizHtmlPayload(BaseModel):
    """``GET /api/investigation-viz-html`` payload.

    HTTP 400 when ``investigation`` or ``run_id`` is missing; on the error path
    the body is ``{error, viz_files: []}`` (not ``{detail}``) — preserved by
    ``extra="allow"``.  The 200 path always has ``viz_files`` (empty when the
    viz dir does not exist yet).

    Source: ``lib.investigation_views.build_investigation_viz_html``.
    """

    model_config = ConfigDict(extra="allow")

    viz_files: list[VizHtmlFile] = []
    error: Optional[str] = None


class InvestigationCompositeEntry(BaseModel):
    """One composite entry in the ``GET /api/investigation-composites`` payload.

    Projected from the investigation's v3 ``baseline[]`` list:
    ``name`` / ``source`` (was ``composite``) / ``params``.  Legacy always
    emits ``params`` as a dict (never null), so the default is ``{}``.
    """

    name: str = ""
    source: str = ""
    params: Any = Field(default_factory=dict)


class InvestigationCompositesPayload(BaseModel):
    """``GET /api/investigation-composites`` payload.

    HTTP 400 when ``?investigation=`` is missing or the spec is malformed;
    HTTP 404 when the investigation is not found.

    Source: ``lib.investigation_views.build_investigation_composites``.
    """

    composites: list[InvestigationCompositeEntry] = []


class InvestigationCompositeDocPayload(BaseModel):
    """``GET /api/investigation-composite-doc`` payload.

    Returns ``{state: <parsed composite YAML>}`` as JSON.  The YAML document
    shape is composite-specific so ``state`` is typed ``Any``.

    HTTP 400 when ``investigation`` or ``composite`` is missing; HTTP 404 when
    the composite YAML file does not exist; HTTP 500 on YAML parse failure.

    Source: ``lib.investigation_views.build_investigation_composite_doc``.
    """

    state: Any = None


class InvestigationHypothesesPayload(BaseModel):
    """``GET /api/investigation-hypotheses`` payload.

    Returns ``{hypotheses: [...], investigation: name}``.  Each hypothesis
    dict is arbitrary (author-authored YAML) so the list is typed
    ``list[Any]``.  Always HTTP 200 — never raises (tolerant fallback).

    Source: ``lib.investigation_views.build_investigation_hypotheses``.
    """

    model_config = ConfigDict(extra="allow")

    hypotheses: list[Any] = []
    investigation: str = ""


class StudyRigor(BaseModel):
    """``GET /api/study-rigor`` payload (lib.rigor_views.build_study_rigor).

    The per-study rigor scorecard from ``pbg_superpowers.rigor.study_rigor``:
    ``study_type`` / ``mode`` / ``descriptive`` / ``dimensions`` / ``score`` /
    ``summary`` on the success path, or ``{error, dimensions, score, summary}``
    on the 200-shaped failure fallback.  The dimension/score shapes are nested
    and vary by study type, so this is a pure pass-through model — no declared
    fields, ``extra="allow"`` so every key the builder emits survives verbatim
    (and none are injected).
    """

    model_config = ConfigDict(extra="allow")


class InvestigationRigor(BaseModel):
    """``GET /api/investigation-rigor`` payload (lib.rigor_views.build_investigation_rigor).

    The rigor roll-up across member studies from
    ``pbg_superpowers.rigor.investigation_rigor`` (``dimensions`` / ``per_study``
    / ``score`` / ``summary``), or one of the 200-shaped error fallbacks.  Like
    :class:`StudyRigor`, a pure pass-through (``extra="allow"``, no declared
    fields) so nothing is stripped or injected.
    """

    model_config = ConfigDict(extra="allow")


class StudyDetail(BaseModel):
    """``GET /api/study/{slug}`` payload (lib.study_spec.load_study_detail_spec).

    The full run-merged study spec built by the loader: ``name``, ``composite``,
    ``runs``, ``simulation_set``, ``param_enforcement``, ``expert_feedback``,
    ``spine_acceptance``, and dozens of other optional keys that vary by study
    type and lifecycle stage.  The shape is open-ended (new keys added as the
    spine matures), so this is a pure pass-through model — no declared fields,
    ``extra="allow"`` so every key the loader emits survives verbatim (and none
    are injected by the model).

    Error paths use :class:`fastapi.responses.JSONResponse` directly
    (400 / 404 / 500) rather than this model.
    """

    model_config = ConfigDict(extra="allow")


class CompositeResolvePayload(BaseModel):
    """``GET /api/composite-resolve`` payload (lib.composite_resolve.resolve_composite).

    Returned when a composite spec or generator is found.  The route returns
    ``null`` (200 with empty body) when ``ref`` is not found — use
    ``Optional[CompositeResolvePayload]`` at the route level.

    ``extra="allow"`` preserves any generator-specific keys (e.g. ``parameters``
    entries with unusual shapes) that aren't enumerated here.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: Optional[str] = None
    parameters: Optional[Any] = None
    state: Optional[Any] = None
    svg: Optional[str] = None
    kind: Optional[str] = None
    module: Optional[str] = None
    default_n_steps: Optional[int] = None


# ---------------------------------------------------------------------------
# Data explorer models  (GET /api/explorer/*)
# ---------------------------------------------------------------------------

class ExplorerRuns(BaseModel):
    """``GET /api/explorer/runs`` payload (lib.explorer_data.list_runs).

    Returns the run-picker list for the Data Explorer card (``{runs: [...]}``),
    or ``{error, runs: []}`` on failure — still HTTP 200 (never-500 contract).
    Pure pass-through (``extra="allow"``, no declared fields) like
    :class:`StudyDetail` / :class:`StudyRigor`: a declared field with a default
    would be serialized even when the builder omits it (injecting a spurious
    key) and an ``int`` field would coerce a float — both break byte-identity
    with the legacy handler.  No declared fields → the builder dict survives
    verbatim.
    """

    model_config = ConfigDict(extra="allow")


class ExplorerObservables(BaseModel):
    """``GET /api/explorer/observables`` payload (lib.explorer_data.list_observables).

    Success shape: ``{categories: {<category>: [<observable>, …], …}}``.
    Error/missing-db shape (still HTTP 200): ``{error, categories: {}}``.
    Pure pass-through (``extra="allow"``, no declared fields) so the builder dict
    survives verbatim — see :class:`ExplorerRuns`.
    """

    model_config = ConfigDict(extra="allow")


class ExplorerSeries(BaseModel):
    """``GET /api/explorer/series`` payload (lib.explorer_data.get_series).

    Success shape: ``{time: […], series: {<key>: […], …}}``.
    Error/missing-db shape (still HTTP 200): ``{error, time: [], series: {}}``.
    Pure pass-through (``extra="allow"``, no declared fields) — see
    :class:`ExplorerRuns`.
    """

    model_config = ConfigDict(extra="allow")


class ExplorerFlux(BaseModel):
    """``GET /api/explorer/flux`` payload (lib.explorer_data.get_flux_auto).

    Success shape: ``{step, time, fluxes: {<bigg_id>: <float>}, coverage: {…}}``.
    Error/missing-db shape (still HTTP 200): ``{error, fluxes: {}}``.
    Pure pass-through (``extra="allow"``, no declared fields) — see
    :class:`ExplorerRuns`.
    """

    model_config = ConfigDict(extra="allow")


class ExplorerVector(BaseModel):
    """``GET /api/explorer/vector`` payload (lib.explorer_data.get_vector).

    Success shape: ``{ids: […], values: […], step: int, time: float|null}``.
    Error/missing-db shape (still HTTP 200):
    ``{error, ids: [], values: [], step: int, time: null}``.
    Pure pass-through (``extra="allow"``, no declared fields) — see
    :class:`ExplorerRuns`.
    """

    model_config = ConfigDict(extra="allow")


class ExplorerProteinBreakdown(BaseModel):
    """``GET /api/explorer/protein-breakdown`` payload.

    Backed by ``lib.explorer_data.get_protein_breakdown``.
    Success shape: ``{breakdown: {<category>: <mass>}, step: int, time: float|null}``.
    Error/missing-db shape (still HTTP 200):
    ``{error, breakdown: {}, step: int, time: null}``.
    Pure pass-through (``extra="allow"``, no declared fields) — see
    :class:`ExplorerRuns`.
    """

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Reports & inputs models  (GET /api/report-lint, /api/needs-attention, etc.)
# ---------------------------------------------------------------------------


class ReportLint(BaseModel):
    """``GET /api/report-lint`` payload (lib.report_views.build_report_lint).

    Shape: ``{findings: [{study, check, severity, message, field_path}]}``,
    in the linter's stable order (error→warning→info).  Always HTTP 200 —
    degrades to ``{findings: []}`` when the linter is unavailable.

    Pure pass-through (``extra="allow"``, no declared fields) so any linter
    output key survives verbatim.

    Source: ``lib.report_views.build_report_lint``.
    """

    model_config = ConfigDict(extra="allow")


class NeedsAttention(BaseModel):
    """``GET /api/needs-attention`` payload (lib.report_views.build_needs_attention).

    Shape: ``{investigation, items: [...], summary: {by_severity, by_kind, total}}``.
    Always HTTP 200 — degrades to empty lists/zeroes when the scan module is
    unavailable.

    Pure pass-through (``extra="allow"``, no declared fields) so summary
    sub-dict shapes survive verbatim.

    Source: ``lib.report_views.build_needs_attention``.
    """

    model_config = ConfigDict(extra="allow")


class InputsPayload(BaseModel):
    """``GET /api/inputs`` payload (lib.report_views.build_inputs).

    Shape: ``{investigation: {...}, global: {...}, current: slug|null}``.
    The investigation + global sub-dicts carry arbitrary keys (datasets,
    references, expert_docs, etc.) so this is a pure pass-through.

    Always HTTP 200.  Pure pass-through (``extra="allow"``, no declared
    fields).

    Source: ``lib.report_views.build_inputs`` — the single implementation the
    stdlib ``server._inputs_payload`` now forwards to.
    """

    model_config = ConfigDict(extra="allow")


class IsetDetail(BaseModel):
    """``GET /api/iset/{slug}`` payload (lib.report_views.build_iset_detail).

    Full investigation-detail dict: name, title, description, studies list,
    acceptance_criteria, computed_acceptance, executive, etc.  The shape is
    complex and version-dependent, so this is a pure pass-through.

    HTTP 404 ``{error}`` when investigation.yaml is absent (served as
    JSONResponse, not through this model).

    Source: ``lib.report_views.build_iset_detail``.
    """

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Observables / never-fabricate guard + linkage-index models
# ---------------------------------------------------------------------------

class ObservablesPayload(BaseModel):
    """``GET /api/observables?ref=<composite>`` payload (lib.observables_views.build_observables).

    Success shape: ``{ref, leaves: [dotted paths], catalogs: {observable: [labels]}}``
    (plus ``cached: true`` on a TTL cache hit).  Error shapes (carried with the
    legacy status code via :class:`fastapi.responses.JSONResponse`, NOT this
    model): ``{error}`` at 400 (no ref / build fail) / 404 (unknown ref) / 500
    (introspection fail) / 501 (validator absent).  Pure pass-through
    (``extra="allow"``, no declared fields) so the builder dict survives
    verbatim — see :class:`ExplorerRuns`.
    """

    model_config = ConfigDict(extra="allow")


class CompositeState(BaseModel):
    """``GET /api/composite-state?ref=<id-or-path>`` payload.

    Backed by ``lib.composite_state_views.build_composite_state``.  Success
    shape: ``{state: <composite document>, kind: "generator"|"static-fallback"|
    "spec", ...}`` (``module`` for generator, ``note`` for static-fallback, plus
    ``cached: true`` on a TTL cache hit).  Error shapes (``{error}`` at 400 /
    500, or ``{error, unresolved: true, ref}`` at 404) are carried at their
    legacy status code via :class:`fastapi.responses.JSONResponse`, not this
    model.  The composite document is composite-specific, so this is a pure
    pass-through (``extra="allow"``, no declared fields) — the builder dict
    survives verbatim, see :class:`ExplorerRuns`.
    """

    model_config = ConfigDict(extra="allow")


class StudyObservableCheck(BaseModel):
    """``GET /api/study-observable-check?study=<slug>`` payload.

    Backed by ``lib.observables_views.build_study_observable_check``.  Success
    shape: ``{composite: ref, readouts: [{name, status, detail}]}`` where
    ``status`` ∈ ``ok|unresolved|not_in_structure|aspirational``.  Error / 422
    fallback shapes (``{error, readouts}`` or ``{composite, readouts, note}``)
    are carried at their legacy status code via
    :class:`fastapi.responses.JSONResponse`, not this model.  Pure pass-through
    (``extra="allow"``, no declared fields) so the builder dict survives
    verbatim — see :class:`ExplorerRuns`.
    """

    model_config = ConfigDict(extra="allow")


class LinkageIndex(BaseModel):
    """``GET /api/linkage-index`` payload (lib.report_views.build_linkage_index).

    SP4a/SP4b navigate surface — ALWAYS HTTP 200.  The shape is param-dependent:
    ``{nodes, edges}`` (no filter), ``{studies}`` (source), ``{findings}``
    (observable), ``{investigation, ac_matrix, dag}`` (investigation),
    ``{studies, composites}`` (observable_registry), or ``{emits,
    used_by_studies}`` (composite); plus an ``error`` key on the tolerant
    fallback.  Pure pass-through (``extra="allow"``, no declared fields) so the
    builder dict survives verbatim — see :class:`StudyDetail`.
    """

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# System & workspace models
# ---------------------------------------------------------------------------

class FrameworkMetrics(BaseModel):
    """``GET /api/framework-metrics`` payload (lib.system_info.build_framework_metrics).

    Aggregated framework-self metrics over every study + investigation in the
    workspace.  Always HTTP 200 (best-effort): ``metrics`` is ``{}`` when
    pbg_superpowers is absent or the compute raises.

    ``extra="allow"`` preserves any forward-compat keys the builder may add.
    """

    model_config = ConfigDict(extra="allow")

    metrics: Any
    n_investigations: int
    n_studies: int


class GithubRepo(BaseModel):
    """``GET /api/github-repo`` payload (lib.system_info.build_github_repo).

    Returns the workspace's GitHub ``owner/name`` slug, or ``null`` when neither
    the git remote nor workspace.yaml resolves to a GitHub URL.  Always 200.

    ``extra="allow"`` for forward-compat.
    """

    model_config = ConfigDict(extra="allow")

    repo: Optional[str] = None


class UiConfig(BaseModel):
    """``GET /api/ui-config`` payload (lib.system_info.build_ui_config).

    UI feature flags read from workspace.yaml's ``ui:`` block, all with
    typed defaults when the block is absent.  Always HTTP 200.

    ``extra="allow"`` for forward-compat.
    """

    model_config = ConfigDict(extra="allow")

    composite_view: str
    ptools_server_url: str
    ptools_omics_url_template: str


class WorkspaceHome(BaseModel):
    """``GET /api/workspace`` payload (lib.system_info.build_workspace_home).

    Workspace narrative metadata: name, description, imports map, and the
    per-investigation summary list.  Shape is variable (investigations list
    rows vary), so this is a pure pass-through (``extra="allow"``, no declared
    fields) — the builder dict survives verbatim.
    """

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Composite runs models  (GET /api/composite-runs, /api/composite-run/*)
# ---------------------------------------------------------------------------

class CompositeRunsList(BaseModel):
    """``GET /api/composite-runs?spec_id=X`` payload.

    Backed by ``lib.composite_run_views.build_composite_runs``.  Success shape:
    ``{runs: [{run_id, spec_id, label, status, ...}, ...]}``.  Error / missing
    spec_id shape (HTTP 400): ``{runs: [], error: "missing spec_id"}``.  Pure
    pass-through (``extra="allow"``, no declared fields) — the builder dict
    survives verbatim.
    """

    model_config = ConfigDict(extra="allow")


class CompositeRunTrajectory(BaseModel):
    """``GET /api/composite-run/{run_id}`` payload.

    Backed by ``lib.composite_run_views.build_composite_run``.  Success shape:
    ``{run_id, trajectory: [{step, time, state}, ...]}``.  Error paths
    (HTTP 404) are carried via :class:`fastapi.responses.JSONResponse`.  Pure
    pass-through (``extra="allow"``, no declared fields).
    """

    model_config = ConfigDict(extra="allow")


class CompositeRunState(BaseModel):
    """``GET /api/composite-run/{run_id}/state?step=N`` payload.

    Backed by ``lib.composite_run_views.build_composite_run_state``.  Success
    shape: ``{run_id, step, state: {...}}``.  Error paths (HTTP 400/404) are
    carried via :class:`fastapi.responses.JSONResponse`.  Pure pass-through
    (``extra="allow"``, no declared fields) — ``state`` is a composite-specific
    dict of arbitrary depth.
    """

    model_config = ConfigDict(extra="allow")


class CompositeRunStatus(BaseModel):
    """``GET /api/composite-run/{run_id}/status`` payload.

    Backed by ``lib.composite_run_views.build_composite_run_status``.  Success
    shape: ``{run_id, status, progress_step, n_steps, heartbeat_at}`` plus, for
    terminal states, ``log_path`` + ``error`` (failed/orphaned) or ``viz_html``
    (completed).  Error paths (HTTP 404) are carried via
    :class:`fastapi.responses.JSONResponse`.  Pure pass-through
    (``extra="allow"``, no declared fields) so all terminal-state fields survive.
    """

    model_config = ConfigDict(extra="allow")


class StudyBigraphPaths(BaseModel):
    """``GET /api/study-bigraph-paths`` payload (lib.study_viz_views.build_study_bigraph_paths).

    Bigraph node paths extracted from a serialized composite state snapshot.
    Variable-shape ``nodes`` entries use pass-through (``extra="allow"``).
    """

    model_config = ConfigDict(extra="allow")


class VisualizationStatus(BaseModel):
    """``GET /api/visualization-status`` payload (lib.study_viz_views.build_visualization_status).

    Lifecycle status for a named visualization:
    ``described`` → ``requested`` → ``created`` → ``added`` → ``committed``.
    Also ``missing`` when the viz name is not in workspace.yaml.
    """

    model_config = ConfigDict(extra="allow")


class VisualizationInstances(BaseModel):
    """``GET /api/visualization-instances`` payload (lib.study_viz_views.build_visualization_instances).

    Class-backed visualization instances configured in workspace.yaml.
    """

    model_config = ConfigDict(extra="allow")


class PtoolsLaunch(BaseModel):
    """``GET /api/ptools-launch/{study}`` payload (lib.study_viz_views.build_ptools_launch).

    Pathway Tools Omics Viewer launch URL + TSV discovery result.
    """

    model_config = ConfigDict(extra="allow")
