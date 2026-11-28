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

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# Which emitter wrote a run's store (derived from its store path).
EmitterKind = Literal["xarray", "parquet", "sqlite"]


class _Passthrough(BaseModel):
    """Base for the forward-compat passthrough payloads: unknown keys are kept
    (``extra="allow"``) so a newer server field survives a round-trip through an
    older client model. Subclasses add only their known fields (those with none
    are pure passthroughs). Centralizes what was a per-class
    ``model_config = ConfigDict(extra="allow")``."""
    model_config = ConfigDict(extra="allow")

class RemoteOrigin(BaseModel):
    """Provenance of a run executed on a remote deployment (a remote backend).

    Built in ``lib/simulations_index.py`` ``_row_to_dict`` from the run's
    ``params_json`` provenance.
    """

    deployment: str            # prov["source"], e.g. "smsvpctest"
    # The run's id on its backend: opaque, whatever its form (an SMS simulation's integer, a /viva/v1 run's
    # "simulation-E9Ec819"); the run's record, not this form, says which backend ran it.
    simulation_id: Union[int, str]
    experiment_id: Optional[str] = None
    backend: Optional[str] = None       # e.g. "ray"
    s3_uri: Optional[str] = None
    # The run's own features (step P3 F0): the optional facets of the backend that ran it, among "artifacts" and
    # "analyses", and its optional run methods ("visibility") (``lib/backends/features.run_features``). They only
    # hide optional controls on the row.
    features: list[str] = []


class BackendFeatures(BaseModel):
    """``GET /api/backend/features`` payload (step P3 F0, ``lib/backends/features.process_features``).

    The features this process's active backends declare, sorted: the optional facets some active backend has
    (``listing``, ``artifacts``, ``analyses``, ``builds``) and each provider's own UI features. Empty with providers
    off or the plugin absent. A registry read: no network call. Features only hide optional controls; no dispatch or
    run-target decision reads them.
    """

    features: list[str]


class StudyRef(BaseModel):
    """A study a run is attached to (filled in by ``_annotate_studies``)."""

    slug: str
    label: Optional[str] = None


class SimRow(BaseModel):
    """One row of the Simulations DB.

    Mirrors the row shapes emitted by ``lib/simulations_index.py``. The primary
    builder is ``_row_to_dict`` (runs_meta SQLite rows), but the index also
    synthesises rows for spec/study-declared runs, and those are looser than a
    DB row: ``spec_id``/``db_path`` can be ``None``, ``emitter`` can be a
    spec-declared free string (e.g. ``"unknown"``), and ``studies`` can be a
    list of bare study-slug strings (the DB path fills in ``StudyRef`` objects
    via ``_annotate_studies``). The field types accept that union so the typed
    seam never 500s on a real workspace; the JSON output is unchanged.
    """

    # Types/nullability copied from the runs_meta schema (lib/composite_runs.py):
    # started_at/completed_at are REAL epoch seconds; sim_name/label/n_steps/
    # progress_step are nullable (some are ALTER-added columns).
    run_id: str
    spec_id: Optional[str] = None   # the run's composite id; None for rows lacking one
    # Enforcement (every simulation must map to one registered composite): True
    # when ``spec_id`` is an exact registered composite in this workspace, False
    # when it is missing or unregistered. Annotated by GET /api/simulations.
    composite_registered: Optional[bool] = None
    # The exact config that reproduces this run: the composite generator params
    # (seed, config_overrides, condition/media, n_steps, …). Sourced from the
    # dashboard run's ``params_json`` or the study.yaml run/condition params.
    # None when no config was captured. JSON-serialisable dict.
    config: Optional[dict[str, Any]] = None
    sim_name: Optional[str] = None
    label: Optional[str] = None
    status: str                # "completed" | "running" | "failed" | ...
    n_steps: Optional[int] = None
    progress_step: Optional[int] = None
    started_at: Optional[float] = None   # epoch seconds; None for synthesised rows lacking a timestamp
    completed_at: Optional[float] = None
    db_path: Optional[str] = None   # None when the row isn't backed by a runs.db
    store_path: Optional[str] = None   # native data store (zarr/parquet dir or s3 uri); None -> data lives in db_path
    # The backend a run executed on (e.g. "sms.cam.uchc.edu"), independent of where its data now lives: a
    # landed /viva/v1 run has ran_on set and remote_origin None. None for a run that ran in this workspace.
    ran_on: Optional[str] = None
    # Canonically "xarray" | "parquet" | "sqlite" (``EmitterKind``), but a
    # spec may declare a free-form emitter (e.g. "unknown"), so accept any str.
    emitter: Optional[str] = None
    # Human-facing emitter label ("SQLite" | "Parquet" | "XArray" | "—"),
    # computed by ``build_simulations_data`` for the /api/simulations index.
    emitter_type: Optional[str] = None
    # DB-path rows carry ``StudyRef`` objects; spec/study-synthesised rows carry
    # bare study-slug strings — accept either.
    studies: list[Union[StudyRef, str]] = []
    study_slug: Optional[str] = None
    investigation_slug: Optional[str] = None
    remote_origin: Optional[RemoteOrigin] = None
    # Source provenance: the repo + commit the run launched from, derived from
    # the run manifest's ``code_version`` (repo/commit/remote_url/package), or
    # the inferred workspace HEAD (``inferred: True``) when the run has no
    # manifest. None only when the workspace itself isn't a resolvable checkout.
    # Shape: {repo, commit, commit_short, remote_url, commit_url, package,
    # inferred}. See lib/simulations_index.py's `_source_from_manifest`.
    source_ref: Optional[dict[str, Any]] = None
    capabilities: list[str] = []  # capability tags advertised by this run
    # Installed analysis tools whose `requires` is satisfied by `capabilities`,
    # each reduced to {"id","label","kind","launch_url"} — see
    # lib/simulations_index.py's `_attach_matched_tools`. Best-effort: []
    # when no tool matches or tool-matching itself failed.
    matched_tools: list[dict[str, Any]] = []


class RemoteSourceState(BaseModel):
    """Provenance of the remote backend's runs merged into the Simulations DB.

    Lets the Runs tab show "remote runs as of HH:MM (refreshing…)" instead of a
    spinner, and stay honest when the backend is unreachable — the fetch is
    stale-while-revalidate, so the payload never blocks on it.
    """

    #: fresh (cache current) | stale (serving last-known, refresh pending) |
    #: refreshing (a background fetch is in flight) | unavailable (unreachable,
    #: no prior data).
    state: str
    as_of: Optional[float] = None      # epoch of the last successful fetch
    error: Optional[str] = None        # last fetch error, when unreachable


class SimulationsPayload(BaseModel):
    """``GET /api/simulations`` payload (server.py ``_simulations_data``)."""

    simulations: list[SimRow]
    current: Optional[str] = None     # current branch slug
    # Remote-source provenance (present only when the remote merge ran). SWR, so
    # a cold/slow/down tunnel never blocks this payload — the state field says so.
    remote: Optional[RemoteSourceState] = None
    # Pagination (all optional / back-compatible: omitting ``limit`` returns
    # every row, ``total is None``). ``total`` is the row count for the current
    # view (after any ``?study=`` filter, before the page slice) so the client
    # can show "N runs" and drive a pager.
    total: Optional[int] = None
    offset: int = 0
    limit: Optional[int] = None


class ChartPayload(_Passthrough):
    """One chart in a study-charts payload (``lib/study_charts.py``).

    Charts are polymorphic by ``source``: ``live`` charts carry an inline ``svg``;
    ``static`` / ``declared`` charts may instead carry an ``img`` data-URI (PNG/GIF)
    plus ``media`` / ``freshness`` / ``simulations`` / ``interpretation``. Only the
    common keys are enumerated; ``extra='allow'`` preserves any source-specific
    field (and ``svg`` is optional since image charts use ``img``).
    """


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
    run_id: Optional[str] = None


class StudyChartsPayload(BaseModel):
    """``GET /api/study-charts/<slug>`` payload.

    Source: ``lib.study_charts.build_study_charts_payload`` (called directly by
    the FastAPI seam and ``publish.build_bundle``). Every field below is always
    present.
    """

    study: str
    schema_version: Optional[Any] = None
    charts: list[ChartPayload]
    # Figureless per-test cards (derived-scalar readouts / unresolved gates) —
    # gate RESULTS graded from run data, surfaced under Simulations › Results
    # rather than the Visualizations tab.
    gate_results: list[ChartPayload] = []
    db_exists: bool
    data_store: Optional[str] = None
    static_count: int
    live_count: int
    gate_result_count: int = 0


class InvestigationSummary(BaseModel):
    """One entry of the ``GET /api/investigation-summaries`` payload.

    Mirrors ``_build_iset_summary_for_test`` in server.py. A parse failure yields
    a minimal ``{name, error}`` entry, so every field but ``name`` is optional.
    ``lifecycle`` is passed through untyped (``Any``) for now.
    """

    name: str
    title: Optional[str] = None
    status: Optional[str] = None
    # Authored verdict rollup (executive.verdict_status, else status) so the
    # sidebar can badge each card without fetching the full detail.
    verdict_status: Optional[str] = None
    # "authored" | "derived" (roll-up of member run-aware verdicts)
    verdict_status_source: Optional[str] = None
    # Open/Closed: authored status closed/archived, else the engine's closure.
    closed: Optional[bool] = None
    closure_source: Optional[str] = None
    effective_status: Optional[str] = None
    description: Optional[str] = None
    question: Optional[str] = None
    hypothesis: Optional[str] = None
    n_studies: Optional[int] = None
    n_figures: Optional[int] = None
    studies: list[str] = []
    lifecycle: Any = None
    current: Optional[bool] = None
    run_command: Optional[str] = None
    error: Optional[str] = None
    origin_repo: Optional[str] = None
    read_only: Optional[bool] = None


class InvestigationSummariesPayload(BaseModel):
    """``GET /api/investigation-summaries`` payload.

    The client reads ``j.investigations``, so the payload MUST be wrapped as
    ``{"investigations": [...]}`` (an object), not a bare array. (The first
    FastAPI port returned a bare list, which made the Investigations page render
    "No investigations declared".)
    """

    investigations: list[InvestigationSummary] = []


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


class BibEntry(_Passthrough):
    """One parsed references.bib entry. Bibtex fields vary, so unknown keys are
    preserved (``extra="allow"``) rather than stripped — only ``key`` is required.
    The documented fields below + enrichment fields (enriched_doi, publisher_url,
    oa_pdf_url, oa_status, …) are added in place by the references-fetch cache."""


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
    # set only when ui.viz_viewer_bundle names a local 3D-viewer package
    viewer_bundle_package: Optional[str] = None


class ReportCard(BaseModel):
    """One saved comparison report card (``report_cards`` list)."""

    study: Optional[str] = None
    name: str
    url: str
    verdict: Optional[str] = None
    created: Optional[int] = None


class SavedVisualizationsPayload(BaseModel):
    """``GET /api/saved-visualizations`` payload (lib.saved_visualizations)."""

    saved: list[SavedViz] = []
    report_cards: list[ReportCard] = []


class AnalysisToolsPayload(_Passthrough):
    """``GET /api/analysis-tools`` payload — the tools-first Analysis Tools tab.

    Composed by ``lib.analysis_tools.build_analysis_tools``: contributed viewers
    (a package's ``workbench_viewers``, incl. the 3D viewer) + core built-in
    tools, each capability-matched to candidate runs/studies. Tool shapes vary
    (viewers carry extra fields), so unknown keys are preserved.
    """


    tools: list[dict] = []


class VizClass(_Passthrough):
    """One entry in the ``GET /api/visualization-classes`` ``classes`` list.

    Fields vary slightly between visualization and analysis kinds, so unknown
    keys are preserved (``extra="allow"``).
    """


    address: str
    name: str
    doc: str = ""
    kind: str = "visualization"


class VisualizationClassesPayload(_Passthrough):
    """``GET /api/visualization-classes`` payload
    (lib.visualization_classes.list_visualization_classes).

    Returns all Visualization subclasses registered in the workspace's core
    registry plus standard pbg-superpowers visualization classes and, when
    v2ecoli is installed, its Analysis classes.
    """


    classes: list[VizClass] = []


class RegistryProcess(_Passthrough):
    """One process/step/emitter/visualization entry in the registry.

    The subprocess script in ``lib.registry.build_registry`` emits these fields;
    ``extra="allow"`` preserves any future additions without breaking the route.
    """


    name: str
    address: str = ""
    kind: str = "other"
    schema_preview: str = ""
    aliases: list[str] = []
    source: str = "environment_only"


class RegistryType(_Passthrough):
    """One type-schema entry in the registry.

    ``extra="allow"`` for forward-compatibility.
    """


    name: str
    schema_preview: str = ""


class RegistryImport(_Passthrough):
    """One imported-repository metadata entry (workspace.yaml imports)."""


    name: str
    package: str = ""
    source: Optional[str] = None
    ref: Optional[str] = None
    description: str = ""


class RegistryPayload(_Passthrough):
    """``GET /api/registry`` payload (lib.registry.build_registry).

    Returns the process/type registry discovered from the workspace's
    ``build_core()`` subprocess. Extra keys (e.g. ``workspace_pkgs``,
    ``registry_include``, ``default_emitter``, ``error``) are preserved by
    ``extra="allow"`` so the frontend receives the full payload unchanged.
    """


    processes: list[RegistryProcess] = []
    types: list[RegistryType] = []
    imports: list[RegistryImport] = []


class CompositeRecord(_Passthrough):
    """One composite entry in the ``GET /api/composites`` payload.

    Composites come in two kinds: ``spec`` (a ``*.composite.yaml`` / ``.json``
    file) and ``generator`` (a ``@composite_generator``-decorated Python function).
    Generator entries carry varied additional fields (``parameters``,
    ``visualizations``, ``workspace_local``, etc.) that differ across workspaces;
    ``extra="allow"`` passes them through untouched so the browser receives the
    full payload.
    """


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


class InvestigationRow(_Passthrough):
    """One row of the ``GET /api/investigations`` ``investigations`` list.

    The row has ~26 keys. The stable scalar/count fields are typed; the rest
    (including the invalid-row shape ``{name, status: "invalid", error}``) are
    preserved via ``extra="allow"``.
    """


    name: str
    status: Optional[str] = None
    phase: Optional[str] = None
    n_studies: Optional[int] = None
    n_simulations: Optional[int] = None
    description: Optional[str] = None
    error: Optional[str] = None
    origin_repo: Optional[str] = None
    read_only: bool = False
    investigations: list[str] = []


class InvestigationsPayload(BaseModel):
    """``GET /api/investigations`` payload
    (lib.investigations_index.build_investigations).

    Returns the per-study index used by the Investigations tab.  Each row is
    either a full ``InvestigationRow`` (valid spec) or a minimal
    ``{name, status: "invalid", error}`` entry (malformed spec.yaml).
    ``extra="allow"`` on ``InvestigationRow`` preserves all ~26 builder keys.
    """

    investigations: list[InvestigationRow] = []


class CatalogModule(_Passthrough):
    """One module entry in the ``GET /api/catalog`` ``modules`` list.

    Source: ``lib.catalog.build_catalog``.  The stable display/install keys
    are enumerated; install-source, tags, and workspace-specific metadata vary
    across entries, so ``extra="allow"`` preserves them intact.
    """


    name: str
    display_name: Optional[str] = None
    installed: bool = False
    install_source: Optional[str] = None
    module: Optional[str] = None
    description: Optional[str] = None


class CatalogPayload(_Passthrough):
    """``GET /api/catalog`` payload (lib.catalog.build_catalog).

    Returns the pbg module catalog annotated with per-workspace install state.
    ``extra="allow"`` forwards any top-level keys the builder may add in future
    (e.g. ``error``).
    """


    modules: list[CatalogModule] = []


class StructuredResourcesPayload(_Passthrough):
    """``GET /api/structured-resources`` payload
    (``lib.structured_resources.load_structured_resources``).

    A workspace's curated ``structured_resources`` vg_data manifest, grouped by
    resource ``type`` plus a ``paper-reference`` lookup by id (so the Resources
    tab can cross-link each ``references: [id]`` to its citation card).

    ``available`` is ``False`` when the workspace ships no manifest — the tab
    then stays hidden.  Per-resource shapes are type-specific (lipid-structure,
    reaction-pathway, evidence-dataset, imported-engine, paper-reference) and
    vary widely, so each grouped entry is a passthrough ``dict`` rather than a
    tight model; ``extra="allow"`` also forwards any future top-level keys.
    ``evidence_dataset_qa_rule`` is the manifest-level QA rule the Resources tab
    renders once as a banner above the evidence-dataset group.
    """


    available: bool = False
    name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    evidence_dataset_qa_rule: Optional[str] = None
    type_order: list[str] = []
    groups: dict[str, list[dict]] = {}
    references_by_id: dict[str, dict] = {}
    counts: dict[str, int] = {}
    n_resources: int = 0


# ---------------------------------------------------------------------------
# Git / branch models
# ---------------------------------------------------------------------------

class GitStatus(_Passthrough):
    """``GET /api/git-status`` payload (lib.git_status.build_git_status).

    Live sync state for the workspace's git.  All network-dependent fields are
    Optional so the response degrades gracefully when origin is absent.
    ``extra="allow"`` preserves any future extension keys.
    """


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


class WorkStatusActive(_Passthrough):
    """``GET /api/work-status`` payload when a workstream IS active.

    Every field is always present — legacy ``_get_work_status`` emits them even
    when null (notably ``pr_number`` / ``pr_url`` when no PR is linked), so they
    are NOT excluded.  ``extra="allow"`` preserves any future extension keys.
    """


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


# ---------------------------------------------------------------------------
# Work & branches models (generation, composite diff)
# ---------------------------------------------------------------------------

class GenerationSummary(_Passthrough):
    """The inner ``generation`` object inside ``GET /api/generation``.

    Fields from ``vivarium_workbench.lib.generation.Generation``.
    ``extra="allow"`` for forward-compat.
    """


    generation_id: str
    git_sha: Optional[str] = None
    param_set_hash: Optional[str] = None
    created_at: Optional[str] = None
    label: Optional[str] = None
    n_runs: int = 0


class Generation(_Passthrough):
    """``GET /api/generation`` payload (lib.work_views.build_generation).

    Returns ``{generation: <summary>}`` or ``{generation: null}`` when no
    generation is active.  Always HTTP 200 (best-effort; errors → null).
    ``extra="allow"`` for forward-compat.
    """


    generation: Optional[GenerationSummary] = None


class WorkCompositeDiffEntry(BaseModel):
    """One file entry in the ``GET /api/work-composite-diff`` ``changes`` list."""

    path: str
    lines_added: int = 0
    lines_removed: int = 0
    category: str


class WorkCompositeDiff(_Passthrough):
    """``GET /api/work-composite-diff`` payload (lib.work_views.build_work_composite_diff).

    Files changed on the active branch that look like model code (composites,
    processes, steps, library helpers, type definitions).  Sorted by largest
    diff; capped at 500 entries from the ``git diff --numstat`` output.

    Always HTTP 200 — merge-base/diff failures carry an ``error`` key with an
    empty ``changes`` list rather than raising.  ``extra="allow"`` preserves
    any future additions.
    """


    base: str = "main"
    branch: str = ""
    changes: list[WorkCompositeDiffEntry] = []
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Investigation detail models
# ---------------------------------------------------------------------------

class VizHtmlFile(BaseModel):
    """One HTML viz file entry in the ``GET /api/investigation-viz-html`` payload."""

    name: str        # stem of the .html filename (e.g. "time_series_plot")
    html_path: str   # workspace-relative path served by the static-file handler


class InvestigationVizHtmlPayload(_Passthrough):
    """``GET /api/investigation-viz-html`` payload.

    HTTP 400 when ``investigation`` or ``run_id`` is missing; on the error path
    the body is ``{error, viz_files: []}`` (not ``{detail}``) — preserved by
    ``extra="allow"``.  The 200 path always has ``viz_files`` (empty when the
    viz dir does not exist yet).

    Source: ``lib.investigation_views.build_investigation_viz_html``.
    """


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


class InvestigationStateTree(_Passthrough):
    """``GET /api/investigation-state-tree`` payload.

    Returns ``{nodes: [...]}`` — the flattened state tree of a composite YAML
    document (each node carries ``path`` + ``kind`` plus kind-specific fields:
    ``type``/``default`` for stores, ``address``/``config`` for processes).
    The node shape is recipe-specific, so this is a pure pass-through
    (``extra="allow"``, ``nodes`` typed ``Any``).

    HTTP 400 when ``investigation`` or ``composite`` is missing; HTTP 404 when
    the composite YAML file does not exist; HTTP 500 on YAML parse failure.

    Source: ``lib.investigation_views.build_investigation_state_tree``.
    """


    nodes: list[Any] = []


class InvestigationHypothesesPayload(_Passthrough):
    """``GET /api/investigation-hypotheses`` payload.

    Returns ``{hypotheses: [...], investigation: name}``.  Each hypothesis
    dict is arbitrary (author-authored YAML) so the list is typed
    ``list[Any]``.  Always HTTP 200 — never raises (tolerant fallback).

    Source: ``lib.investigation_views.build_investigation_hypotheses``.
    """


    hypotheses: list[Any] = []
    investigation: str = ""


class StudyRigor(_Passthrough):
    """``GET /api/study-rigor`` payload (lib.rigor_views.build_study_rigor, Fable G5).

    The per-study evidence & rigor scorecard from
    ``discovery_engine.reporting.rigor.study_rigor`` (``dimensions`` / ``score`` /
    ``summary`` / ``study_type`` / ``mode`` / ``descriptive``), or the
    ``{"unavailable": true, "reason": ...}`` degrade shape when rigor could not
    be computed (missing dependency, malformed spec, ...) — never a fabricated
    empty scorecard. Pure pass-through (``extra="allow"``, no declared fields)
    like :class:`InvestigationRigor` so nothing is stripped or injected.
    """



class InvestigationRigor(_Passthrough):
    """``GET /api/investigation-rigor`` payload (lib.rigor_views.build_investigation_rigor).

    The rigor roll-up across member studies from
    ``discovery_engine.reporting.rigor.investigation_rigor`` (``dimensions`` / ``per_study``
    / ``score`` / ``summary``), or one of the 200-shaped error fallbacks.  Like
    :class:`StudyRigor`, a pure pass-through (``extra="allow"``, no declared
    fields) so nothing is stripped or injected.
    """



class StudyDetail(_Passthrough):
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



class CompositeResolvePayload(_Passthrough):
    """``GET /api/composite-resolve`` payload (lib.composite_resolve.resolve_composite).

    Returned when a composite spec or generator is found.  The route returns
    ``null`` (200 with empty body) when ``ref`` is not found — use
    ``Optional[CompositeResolvePayload]`` at the route level.

    ``extra="allow"`` preserves any generator-specific keys (e.g. ``parameters``
    entries with unusual shapes) that aren't enumerated here.
    """


    id: str
    name: str
    description: Optional[str] = None
    parameters: Optional[Any] = None
    state: Optional[Any] = None
    svg: Optional[str] = None
    kind: Optional[str] = None
    module: Optional[str] = None
    default_n_steps: Optional[int] = None
    schema: dict = {}
    requires: dict = {}
    tags: list = []
    analyses: list = []
    visualizations: list = []
    emitters: list = []
    wiring_status: Optional[str] = None
    notice: Optional[str] = None



# ---------------------------------------------------------------------------
# Reports & inputs models  (GET /api/report-lint, /api/needs-attention, etc.)
# ---------------------------------------------------------------------------


class ReportLint(_Passthrough):
    """``GET /api/report-lint`` payload (lib.report_views.build_report_lint).

    Shape: ``{findings: [{study, check, severity, message, field_path}]}``,
    in the linter's stable order (error→warning→info).  Always HTTP 200 —
    degrades to ``{findings: []}`` when the linter is unavailable.

    Pure pass-through (``extra="allow"``, no declared fields) so any linter
    output key survives verbatim.

    Source: ``lib.report_views.build_report_lint``.
    """



class NeedsAttention(_Passthrough):
    """``GET /api/needs-attention`` payload (lib.report_views.build_needs_attention).

    Shape: ``{investigation, items: [...], summary: {by_severity, by_kind, total}}``.
    Always HTTP 200 — degrades to empty lists/zeroes when the scan module is
    unavailable.

    Pure pass-through (``extra="allow"``, no declared fields) so summary
    sub-dict shapes survive verbatim.

    Source: ``lib.report_views.build_needs_attention``.
    """



class BandProvenanceMissing(_Passthrough):
    """``GET /api/band-provenance`` payload
    (lib.cite_bands_views.build_band_provenance_missing).

    Shape: ``{study, missing: [{name, kind, band, field_path}]}`` — exactly
    ``discovery_engine.study.band_provenance.bands_missing_provenance``'s output,
    wrapped with the resolved study slug. Empty ``missing`` means every band
    is already cited.

    Pure pass-through (``extra="allow"``, no declared fields).

    Source: ``lib.cite_bands_views.build_band_provenance_missing``.
    """



class BandProvenanceResult(_Passthrough):
    """``POST /api/band-provenance`` payload
    (lib.cite_bands_views.write_band_provenance).

    Shape: ``{study, test_name, written: bool}``. ``written`` is ``False``
    when the named entry was not found (never fabricates) or the write was a
    no-op (already identical — idempotent).

    Pure pass-through (``extra="allow"``, no declared fields).

    Source: ``lib.cite_bands_views.write_band_provenance``.
    """



class CitationGaps(_Passthrough):
    """``GET /api/citation-gaps`` payload (lib.cite_bands_views.build_citation_gaps).

    Shape: ``{investigation, gaps: {study_slug: {uncited_bands:
    [{test, observable?}], available_references: [bib_key]}}}`` — ``gaps`` is
    exactly ``vivarium_workbench.lib.citation_gaps.investigation_citation_gaps``'s
    return value.

    Pure pass-through (``extra="allow"``, no declared fields).

    Source: ``lib.cite_bands_views.build_citation_gaps``.
    """



class ExpertSearchResult(_Passthrough):
    """``GET /api/expert-search`` payload (lib.cite_bands_views.build_expert_search).

    Shape: ``{terms: [str], hits: [{doc, page, snippet, term}]}`` — ``hits``
    is exactly ``vivarium_workbench.lib.expert_search.search_expert_docs``'s return
    value for the parsed ``q`` terms.

    Pure pass-through (``extra="allow"``, no declared fields).

    Source: ``lib.cite_bands_views.build_expert_search``.
    """



class InputsPayload(_Passthrough):
    """``GET /api/inputs`` payload (lib.report_views.build_inputs).

    Shape: ``{investigation: {...}, global: {...}, current: slug|null}``.
    The investigation + global sub-dicts carry arbitrary keys (datasets,
    references, expert_docs, etc.) so this is a pure pass-through.

    Always HTTP 200.  Pure pass-through (``extra="allow"``, no declared
    fields).

    Source: ``lib.report_views.build_inputs`` — the single implementation the
    stdlib ``server._inputs_payload`` now forwards to.
    """



class IsetDetail(_Passthrough):
    """``GET /api/investigation/{slug}`` payload (lib.report_views.build_iset_detail).

    Full investigation-detail dict: name, title, description, studies list,
    acceptance_criteria, computed_acceptance, executive, etc.  The shape is
    complex and version-dependent, so this is a pure pass-through.

    HTTP 404 ``{error}`` when investigation.yaml is absent (served as
    JSONResponse, not through this model).

    Source: ``lib.report_views.build_iset_detail``.
    """



# ---------------------------------------------------------------------------
# Observables / never-fabricate guard + linkage-index models
# ---------------------------------------------------------------------------

class ObservablesPayload(_Passthrough):
    """``GET /api/observables?ref=<composite>`` payload (lib.observables_views.build_observables).

    Success shape: ``{ref, leaves: [dotted paths], catalogs: {observable: [labels]}}``
    (plus ``cached: true`` on a TTL cache hit).  Error shapes (carried with the
    legacy status code via :class:`fastapi.responses.JSONResponse`, NOT this
    model): ``{error}`` at 400 (no ref / build fail) / 404 (unknown ref) / 500
    (introspection fail) / 501 (validator absent).  Pure pass-through
    (``extra="allow"``, no declared fields) so the builder dict survives
    verbatim — see :class:`ExplorerRuns`.
    """



class CompositeState(_Passthrough):
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



class StudyObservableCheck(_Passthrough):
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



class ReadoutRow(BaseModel):
    """One row of ``GET /api/study-readouts`` (lib.readouts_views.build_study_readouts).

    A merged view of one emit-plan leaf and any authored ``study.yaml`` readout
    annotation. ``emit_status``: ``emitted`` (a real emitter path) / ``derived``
    (authored derived-needed|aspirational metric, exempt from the emit-plan
    check) / ``not_in_emit_plan`` (authored ``available`` readout whose
    ``store_path`` is missing or not an emitted leaf — the never-fabricate flag)
    / ``unverified`` (the emit plan could not be built — e.g. a remote build with
    no local ParCa cache — so the authored row was not checked either way).
    """

    store_path: str
    name: str
    description: str = ""
    units: str = ""
    index_by: Optional[dict] = None
    notes: str = ""
    annotated: bool
    emit_status: Literal["emitted", "not_in_emit_plan", "derived", "unverified"]


class StudyReadouts(BaseModel):
    """``GET /api/study-readouts?study=<slug>`` payload.

    Backed by ``lib.readouts_views.build_study_readouts``. ``rows`` is the union
    of the composite's emit-plan leaves and authored readouts; ``note`` carries a
    human explanation when the composite could not be built (rows then come from
    authored readouts only, unverified).
    """

    composite: str
    rows: list[ReadoutRow]
    note: str = ""
    # Design-time emitter & config summary (kind, interval, scope, output_dir)
    # from ``readouts_views._emitter_block`` — the Readouts tab's one-line emitter
    # summary reads this. Free-form dict (always computable from the spec).
    emitter: Optional[dict] = None
    # The emit plan could not be built (rows are authored-only, ``unverified``).
    # ``remote_build`` marks the EXPECTED case (a materialized repo@commit with no
    # local ParCa cache) so the UI can show a soft notice instead of a hard error.
    degraded: bool = False
    remote_build: bool = False
    # Fable Increment A #5 (docs/superpowers/specs/2026-08-01-study-design-fable-pass.md
    # §5.2): the real saved/excluded split — ``excluded`` = the full observable
    # surface minus ``composite_runs.collect_emit_paths_from_spec(spec)`` — plus
    # the R2 three-state marker so the UI can say "unknown" rather than "empty"
    # when the split could not be computed.
    excluded: list[dict] = []
    excluded_state: Optional[Literal["computed", "empty", "unavailable"]] = None
    emit_selection: Optional[Literal["total", "subset"]] = None
    reason: str = ""
    # Deprecated alias for ``emit_selection == "total"``; keep for one release.
    emit_is_total: bool = False


class LinkageIndex(_Passthrough):
    """``GET /api/linkage-index`` payload (lib.report_views.build_linkage_index).

    SP4a/SP4b navigate surface — ALWAYS HTTP 200.  The shape is param-dependent:
    ``{nodes, edges}`` (no filter), ``{studies}`` (source), ``{findings}``
    (observable), ``{investigation, ac_matrix, dag}`` (investigation),
    ``{studies, composites}`` (observable_registry), or ``{emits,
    used_by_studies}`` (composite); plus an ``error`` key on the tolerant
    fallback.  Pure pass-through (``extra="allow"``, no declared fields) so the
    builder dict survives verbatim — see :class:`StudyDetail`.
    """



# ---------------------------------------------------------------------------
# System & workspace models
# ---------------------------------------------------------------------------

class FrameworkMetrics(_Passthrough):
    """``GET /api/framework-metrics`` payload (lib.system_info.build_framework_metrics).

    Aggregated framework-self metrics over every study + investigation in the
    workspace.  Always HTTP 200 (best-effort): ``metrics`` is ``{}`` when
    viva_superpowers is absent or the compute raises.

    ``extra="allow"`` preserves any forward-compat keys the builder may add.
    """


    metrics: Any
    n_investigations: int
    n_studies: int


class GithubRepo(_Passthrough):
    """``GET /api/github-repo`` payload (lib.system_info.build_github_repo).

    Returns the workspace's GitHub ``owner/name`` slug, or ``null`` when neither
    the git remote nor workspace.yaml resolves to a GitHub URL.  Always 200.

    ``extra="allow"`` for forward-compat.
    """


    repo: Optional[str] = None


class UiConfig(_Passthrough):
    """``GET /api/ui-config`` payload (lib.system_info.build_ui_config).

    UI feature flags read from workspace.yaml's ``ui:`` block, all with
    typed defaults when the block is absent.  Always HTTP 200.

    ``extra="allow"`` for forward-compat.
    """


    # True when VIVARIUM_WORKBENCH_READONLY is set — the client hides authoring
    # controls and goes remote-only (no Local source option).
    readonly: bool = False
    composite_view: str
    auto_results: bool = True


class UiConfigUpdateBody(_Passthrough):
    """POST /api/ui-config {auto_results: bool}

    Task 9 (composite-run-auto-results): the write side of ``UiConfig.auto_results``.
    """


    auto_results: Optional[bool] = None


class WorkspaceHome(_Passthrough):
    """``GET /api/workspace`` payload (lib.system_info.build_workspace_home).

    Workspace narrative metadata: name, description, imports map, and the
    per-investigation summary list.  Shape is variable (investigations list
    rows vary), so this is a pure pass-through (``extra="allow"``, no declared
    fields) — the builder dict survives verbatim.
    """



# ---------------------------------------------------------------------------
# Composite runs models  (GET /api/composite-runs, /api/composite-run/*)
# ---------------------------------------------------------------------------

class CompositeRunsList(_Passthrough):
    """``GET /api/composite-runs?spec_id=X`` payload.

    Backed by ``lib.composite_run_views.build_composite_runs``.  Success shape:
    ``{runs: [{run_id, spec_id, label, status, ...}, ...]}``.  Error / missing
    spec_id shape (HTTP 400): ``{runs: [], error: "missing spec_id"}``.  Pure
    pass-through (``extra="allow"``, no declared fields) — the builder dict
    survives verbatim.
    """



class CompositeRunTrajectory(_Passthrough):
    """``GET /api/composite-run/{run_id}`` payload.

    Backed by ``lib.composite_run_views.build_composite_run``.  Success shape:
    ``{run_id, trajectory: [{step, time, state}, ...]}``.  Error paths
    (HTTP 404) are carried via :class:`fastapi.responses.JSONResponse`.  Pure
    pass-through (``extra="allow"``, no declared fields).
    """



class CompositeRunState(_Passthrough):
    """``GET /api/composite-run/{run_id}/state?step=N`` payload.

    Backed by ``lib.composite_run_views.build_composite_run_state``.  Success
    shape: ``{run_id, step, state: {...}}``.  Error paths (HTTP 400/404) are
    carried via :class:`fastapi.responses.JSONResponse`.  Pure pass-through
    (``extra="allow"``, no declared fields) — ``state`` is a composite-specific
    dict of arbitrary depth.
    """



class CompositeRunStatus(_Passthrough):
    """``GET /api/composite-run/{run_id}/status`` payload.

    Backed by ``lib.composite_run_views.build_composite_run_status``.  Success
    shape: ``{run_id, status, progress_step, n_steps, heartbeat_at}`` plus, for
    terminal states, ``log_path`` + ``error`` (failed/orphaned, and a stopped
    remote run's cancelled) or ``viz_html`` (completed).  Error paths (HTTP 404) are carried via
    :class:`fastapi.responses.JSONResponse`.  Pure pass-through
    (``extra="allow"``, no declared fields) so all terminal-state fields survive.
    """



class StudyBigraphPaths(_Passthrough):
    """``GET /api/study-bigraph-paths`` payload (lib.study_viz_views.build_study_bigraph_paths).

    Bigraph node paths extracted from a serialized composite state snapshot.
    Variable-shape ``nodes`` entries use pass-through (``extra="allow"``).
    """



class VisualizationStatus(_Passthrough):
    """``GET /api/visualization-status`` payload (lib.study_viz_views.build_visualization_status).

    Lifecycle status for a named visualization:
    ``described`` → ``requested`` → ``created`` → ``added`` → ``committed``.
    Also ``missing`` when the viz name is not in workspace.yaml.
    """



class VisualizationInstances(_Passthrough):
    """``GET /api/visualization-instances`` payload (lib.study_viz_views.build_visualization_instances).

    Class-backed visualization instances configured in workspace.yaml.
    """



class RemoteHealth(BaseModel):
    """``GET /api/source/remote-health`` payload (lib.workspace_deps_views.remote_health).

    Reachability + config status of the configured remote backend
    for the Source panel's health indicator. Always HTTP 200; never raises.
    """

    configured: bool
    base_url: str | None  # None: no URL to ask (no backend named, no active provider supplies one)
    reachable: bool
    version: str | None = None
    error: str | None = None


class ProvenanceManifest(_Passthrough):
    """repo@commit + lockfile + result pointers — the portable round-trip unit."""
    repo: str
    commit: str
    branch: str
    workspace: str
    lockfile: Optional[str] = None
    results: dict[str, list[str]] = {}
    simulator_id: Optional[int] = None


class WorkspacesList(_Passthrough):
    """``GET /api/workspaces`` payload (lib.workspace_deps_views.build_workspaces).

    Workspace-switcher dropdown: ``{current: {name, path}, workspaces: [...]}``.
    Each workspace row carries ``name, path, repo, branch, commit, label,
    status`` plus optional ``url`` and ``pid`` when the server is live.

    Always HTTP 200 (falls back to current-only on a missing/corrupt catalog).

    Pure pass-through (``extra="allow"``, no declared fields) so row keys and
    future catalog annotations survive verbatim.

    Source: ``lib.workspace_deps_views.build_workspaces``.
    """



class SystemDepsCheck(_Passthrough):
    """``GET /api/system-deps-check?name=<module>`` payload
    (lib.workspace_deps_views.build_system_deps_check).

    Success shape (HTTP 200): ``{name, platform, ok, checks: [{name,
    description, ok, reason, install, notes}]}``.  Error shapes (carried via
    :class:`fastapi.responses.JSONResponse`):

    - HTTP 400 ``{error: "name required"}`` — ``?name=`` missing or empty.
    - HTTP 404 ``{error: "unknown module: <name>"}`` — module not in registry.

    Pure pass-through (``extra="allow"``, no declared fields) so ``checks[]``
    entries and future install-spec sub-keys survive verbatim.

    Source: ``lib.workspace_deps_views.build_system_deps_check``.
    """



# ---------------------------------------------------------------------------
# Batch 18: Investigation & study mutation request-body models
# ---------------------------------------------------------------------------
# These model the POST bodies for the set-* metadata writers.  extra="allow"
# so unknown keys pass through to the lib builder unchanged (same contract as
# the legacy dict).  Response models are plain dict/JSONResponse (the handlers
# return {ok:True}/{error:...} — too generic to need a separate model).


class SetAnalysesBody(_Passthrough):
    """POST /api/investigation-set-analyses {investigation, analyses}

    ``analyses`` is a list of ``{name, params?}`` — ``name`` must match an
    entry in ``v2ecoli.workflow.analysis.ANALYSIS_REGISTRY`` (validated at
    dispatch time by ``lib.study_run_post.build_analysis_options``, not here —
    this endpoint only persists the spec)."""


    investigation: str = ""
    analyses: Optional[list] = None


# ---------------------------------------------------------------------------
# Consolidated PATCH bodies: study / investigation metadata
#
# One typed partial-update body per entity, replacing the per-field setter POSTs
# (study-set-objective, study-narrative-set, ...). Each present field dispatches
# to its existing lib.metadata_mutations function. Field names mirror the on-disk
# study.yaml / investigation.yaml so a future typed schema tightens these rather
# than redefining them. These are deliberately NOT extra="allow": they are the
# typed contract. The handler applies only the fields the client actually sends
# (``model_fields_set``), so a structured field explicitly set to null (e.g. a
# narrative leaf delete) stays distinct from an absent field.
# ---------------------------------------------------------------------------


class NarrativePatch(BaseModel):
    """One narrative-tree leaf write: dotted ``path`` under an allowlisted root;
    ``value`` written at it (null/"" deletes the leaf and prunes empties)."""

    path: str
    value: Any = None


class ExpertInputPatch(BaseModel):
    """Patch one expert-input entry: set ``current`` on the model-settings entry
    whose ``name`` matches."""

    name: str
    current: Any = None


class StudyPatchBody(BaseModel):
    """PATCH /api/study/{slug} — partial update of a study's metadata.

    Each present field routes to its ``lib.metadata_mutations`` function:
    ``objective``→set_study_objective, ``narrative``→set_study_narrative,
    ``expert_input``→set_study_expert_input, ``observables``/``emit_all``→
    set_investigation_observables, ``conclusions``→set_investigation_conclusions,
    ``overview``→set_investigation_overview.

    (Note: the legacy ``study-set-description`` route was a misnomer — it aliased
    set_investigation_overview and wrote the overview fields, never a
    ``description`` scalar — so this body exposes ``overview``, not ``description``.)"""

    objective: Optional[str] = None
    narrative: Optional[NarrativePatch] = None
    expert_input: Optional[ExpertInputPatch] = None
    observables: Optional[list[list[str]]] = None
    emit_all: Optional[bool] = None
    conclusions: Optional[str] = None
    overview: Optional[dict[str, Any]] = None


class InvestigationPatchBody(BaseModel):
    """PATCH /api/investigation/{slug} — partial update of an investigation's metadata.

    ``observables``/``emit_all``→set_investigation_observables,
    ``conclusions``→set_investigation_conclusions,
    ``overview``→set_investigation_overview, ``status``→set_investigation_status.
    NOTE two distinct statuses: top-level ``status`` writes
    ``investigations/<slug>/investigation.yaml`` (enum active/in-progress/planning/
    completed/archived/closed); the spec/study.yaml overview status is set via
    ``overview: {status: ...}`` (enum draft/in-progress/completed/archived)."""

    observables: Optional[list[list[str]]] = None
    emit_all: Optional[bool] = None
    conclusions: Optional[str] = None
    overview: Optional[dict[str, Any]] = None
    status: Optional[str] = None


# ---------------------------------------------------------------------------
# Batch 19: Study CRUD request-body models
# ---------------------------------------------------------------------------


class StudyVariantAddBody(_Passthrough):
    """POST /api/study-variant-add {study|investigation, name, base_composite, parameter_overrides?}"""


    study: Optional[str] = None
    investigation: Optional[str] = None
    name: Optional[str] = None
    base_composite: Optional[str] = None
    parameter_overrides: Optional[dict] = None


class StudyVariantDeleteBody(_Passthrough):
    """POST /api/study-variant-delete {name|study|investigation, variant}"""


    name: Optional[str] = None
    study: Optional[str] = None
    investigation: Optional[str] = None
    variant: Optional[str] = None


class StudyFindingAddBody(_Passthrough):
    """POST /api/study-finding-add

    {study|investigation, statement, status?, kind?, explanation?}
    """


    study: Optional[str] = None
    investigation: Optional[str] = None
    statement: Optional[str] = None
    status: Optional[str] = None
    kind: Optional[str] = None
    explanation: Optional[str] = None


class StudyFindingDeleteBody(_Passthrough):
    """POST /api/study-finding-delete {study|investigation, id}"""


    study: Optional[str] = None
    investigation: Optional[str] = None
    id: Optional[str] = None


class StudyVariantSetParamsBody(_Passthrough):
    """POST /api/study-variant-set-params {name|study|investigation, variant, parameter_overrides}"""


    name: Optional[str] = None
    study: Optional[str] = None
    investigation: Optional[str] = None
    variant: Optional[str] = None
    parameter_overrides: Optional[dict] = None


class StudyBaselineAddBody(_Passthrough):
    """POST /api/study-baseline-add {study|investigation, name, composite, params?}"""


    study: Optional[str] = None
    investigation: Optional[str] = None
    name: Optional[str] = None
    composite: Optional[str] = None
    params: Optional[dict] = None


class StudyBaselineSetBody(_Passthrough):
    """POST /api/study-baseline-set {study|investigation, name, composite?, params?}

    In-place repoint of an existing baseline entry (preserves its name).
    """


    study: Optional[str] = None
    investigation: Optional[str] = None
    name: Optional[str] = None
    composite: Optional[str] = None
    params: Optional[dict] = None


class StudyBaselineRemoveBody(_Passthrough):
    """POST /api/study-baseline-remove {study|investigation, name}"""


    study: Optional[str] = None
    investigation: Optional[str] = None
    name: Optional[str] = None


class StudyInterventionAddBody(_Passthrough):
    """POST /api/study-intervention-add {study|investigation, name, description?}"""


    study: Optional[str] = None
    investigation: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None


class StudyInterventionUpdateBody(_Passthrough):
    """POST /api/study-intervention-update {study|investigation, name, description}"""


    study: Optional[str] = None
    investigation: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None


class StudyInterventionDeleteBody(_Passthrough):
    """POST /api/study-intervention-delete {study|investigation, name}"""


    study: Optional[str] = None
    investigation: Optional[str] = None
    name: Optional[str] = None


class StudyRunDeleteBody(_Passthrough):
    """POST /api/study-run-delete {name|study|investigation, run_id}"""


    name: Optional[str] = None
    study: Optional[str] = None
    investigation: Optional[str] = None
    run_id: Optional[str] = None


class StudyRunsClearBody(_Passthrough):
    """POST /api/study-runs-clear {name|study|investigation}"""


    name: Optional[str] = None
    study: Optional[str] = None
    investigation: Optional[str] = None


class StudyComparisonAddBody(_Passthrough):
    """POST /api/study-comparison-add {name|study|investigation, run_ids, name?}

    ``name`` serves dual purpose: it is checked first by _study_name_from_body
    as a legacy study-id alias, AND it is used as the comparison name when
    the client explicitly sends it.  In practice callers send ``study`` for the
    study id and optionally ``name`` for the comparison label.
    """


    name: Optional[str] = None
    study: Optional[str] = None
    investigation: Optional[str] = None
    run_ids: Optional[list] = None


# ---------------------------------------------------------------------------
# Batch 20: Study lifecycle + feedback mutation request-body models
# ---------------------------------------------------------------------------


class FeedbackApplyActionBody(_Passthrough):
    """POST /api/feedback-apply-action {item_id}"""


    item_id: Optional[str] = None


class StudyCreateFromRunBody(_Passthrough):
    """POST /api/study-create-from-run {name, source_run_id, objective?, description?}"""


    name: Optional[str] = None
    source_run_id: Optional[str] = None
    objective: Optional[str] = None
    description: Optional[str] = None


class StudyRenameBody(_Passthrough):
    """POST /api/study-rename {study, new_name}"""


    study: Optional[str] = None
    new_name: Optional[str] = None


class StudySyncRunsBody(_Passthrough):
    """POST /api/study-sync-runs {study}"""


    study: Optional[str] = None


class StudyReadoutMigrateBody(_Passthrough):
    """POST /api/study-readout-migrate {study, write?, apply?}

    ``write`` (or its alias ``apply``) defaults to ``False`` (dry-run),
    mirroring ``discovery_engine.study.readout_migration.migrate_study_file``'s own
    ``write`` keyword.
    """


    study: Optional[str] = None
    write: Optional[bool] = None
    apply: Optional[bool] = None


class StudyFindingsPopulateBody(_Passthrough):
    """POST /api/study-findings-populate-observations {study}

    Fills only ABSENT code-owned finding slots from ``computed_outcomes`` +
    band + readouts (inherently fill-absent-only + idempotent — no dry-run
    flag; mirrors
    ``discovery_engine.study.finding_observations.populate_finding_observations``).
    """


    study: Optional[str] = None


class StudyFindingsPopulateResult(_Passthrough):
    """``POST /api/study-findings-populate-observations`` response.

    Shape is exactly
    ``discovery_engine.study.finding_observations.populate_finding_observations``'s
    ``{filled, skipped}`` plus the resolved ``study`` slug.

    Source: ``lib.finding_observations_views.study_findings_populate_observations``.
    """


    study: Optional[str] = None
    filled: int = 0
    skipped: int = 0


# --- viva-study rewire endpoints (Phase 2.1g) ------------------------------


class StudyVerifyBody(_Passthrough):
    """POST /api/study-verify {study}

    Statically verifies a study.yaml's internal consistency and workspace
    cross-references (baselines/variants/simulation_set/behavior_tests/
    observables/parent_studies/cites/findings-evidence). No simulation is run.
    Mirrors ``vivarium_workbench.lib.study_verify.verify_study``.
    """


    study: Optional[str] = None


class StudyVerifyResult(_Passthrough):
    """``POST /api/study-verify`` response — the plugin CLI's ``--json`` shape
    plus the resolved ``study`` slug: ``{study, study_yaml, findings, summary}``.
    ``findings`` are ``study_verify.VerifyFinding.to_dict()`` mappings; ``summary``
    carries the ``{error, warning, info}`` level counts.

    Source: ``lib.study_verify_views.study_verify``.
    """


    study: Optional[str] = None
    study_yaml: Optional[str] = None
    findings: Optional[list[dict]] = None
    summary: Optional[dict] = None


class StudyNarrativeCommandBody(_Passthrough):
    """POST /api/study-narrative-command {study, subcommand, args, dry_run?}

    Dispatches on ``subcommand`` ∈ {set-verdicts, add-literature-anchor,
    add-pivot, add-requirement} — each wraps the matching
    ``vivarium_workbench.lib.study_narrative`` callable. ``args`` carries the chosen
    subcommand's fields keyed by the target dataclass's field names;
    ``dry_run`` computes the diff without writing study.yaml.
    """


    study: Optional[str] = None
    subcommand: Optional[str] = None
    args: Optional[dict] = None
    dry_run: Optional[bool] = None


class StudyNarrativeCommandResult(_Passthrough):
    """``POST /api/study-narrative-command`` response. ``message`` is the
    diff/summary string the plugin callable returns.

    Source: ``lib.study_narrative_command_views.study_narrative_command``.
    """


    study: Optional[str] = None
    subcommand: Optional[str] = None
    message: Optional[str] = None
    dry_run: bool = False


class StudyFindingsBody(_Passthrough):
    """POST /api/study-findings {study, auto?, dry_run?}

    Walks a study's ``behavior_tests[]`` outcomes under ``runs[]`` and DRAFTS
    one ``findings[]`` entry per outcome not already covered by an
    ``evidence.from_test`` link. ``auto``/``dry_run`` mirror
    ``vivarium_workbench.lib.study_findings.run_findings_walk``'s keywords.
    Distinct from ``/api/study-findings-populate-observations`` (which fills
    slots on EXISTING findings).
    """


    study: Optional[str] = None
    auto: Optional[bool] = None
    dry_run: Optional[bool] = None


class StudyFindingsResult(_Passthrough):
    """``POST /api/study-findings`` response — flattens
    ``study_findings.run_findings_walk``'s ``WalkResult`` to JSON-able counts
    plus the resolved ``study`` slug.

    Source: ``lib.study_findings_views.study_findings_draft``.
    """


    study: Optional[str] = None
    proposed: int = 0
    appended: int = 0
    skipped_existing: int = 0
    cited_bib_keys: list[str] = Field(default_factory=list)
    unknown_bib_keys: list[str] = Field(default_factory=list)
    dry_run: bool = False
    wrote: bool = False
    wrote_path: Optional[str] = None


class StudyReadoutMigrationStatusResult(_Passthrough):
    """GET /api/study-readout-migration-status result — verbatim passthrough of
    ``discovery_engine.study.readout_migration.readout_migration_status`` (three
    buckets classifying a study's readouts) plus the resolved ``study`` slug.
    PURE read: never modifies study.yaml.

    Source: ``lib.readout_migration_status_views.readout_migration_status_view``.
    """


    study: str
    canonical: list[dict] = Field(default_factory=list)
    migratable: list[dict] = Field(default_factory=list)
    needs_human: list[dict] = Field(default_factory=list)


class FeedbackRecordActionBody(_Passthrough):
    """POST /api/feedback-record-action
    {item_id, kind, target_study, proposed_text, target_finding?, by?}

    Keys off ``item_id`` (NOT a study slug); wraps
    ``vivarium_workbench.lib.feedback_actions.record_feedback_action``. Extra keys are
    forwarded to that primitive's ``**extra``.
    """


    item_id: Optional[str] = None
    kind: Optional[str] = None
    target_study: Optional[str] = None
    proposed_text: Optional[str] = None
    target_finding: Optional[str] = None
    by: Optional[str] = None


class FeedbackRecordActionResult(_Passthrough):
    """``POST /api/feedback-record-action`` response — exactly what
    ``feedback_actions.record_feedback_action`` returns on success
    (``{recorded, path, kind}``); best-effort failures carry ``error`` via
    ``extra="allow"``.

    Source: ``lib.feedback_record_views.feedback_record_action``.
    """


    recorded: bool = False
    path: Optional[str] = None
    kind: Optional[str] = None


class FeedbackSaveBody(_Passthrough):
    """POST /api/feedback-save — a collected inline-feedback payload from the
    study page's capture widget (``static/study-feedback.js``).

    Canonical ingest shape: ``{meta:{investigation, study?, reviewer?,
    generated_at?, ...}, annotations:{section_id: [{author, text, ts, ...}]}}``.
    Wraps ``viva_superpowers.feedback_import.write_feedback_payload``, which
    writes it under ``investigations/<inv>/feedback/<ts>.yaml``.
    """


    meta: dict[str, Any] = {}
    annotations: dict[str, Any] = {}


class FeedbackSaveResult(_Passthrough):
    """``POST /api/feedback-save`` response: ``{saved, path}`` on success;
    failures carry ``error`` via ``extra="allow"``.

    Source: ``lib.feedback_save_views.feedback_save``.
    """


    saved: bool = False
    path: Optional[str] = None


class ProseEditBody(_Passthrough):
    """POST /api/prose-edit — write one whitelisted prose field back into a
    study.yaml / investigation.yaml (comment-preserving).

    Body ``{kind: study|investigation, slug, field, value}``. The inline-edit
    client (``static/inline-edit.js``) sends it when a tagged field is saved.
    """


    kind: Optional[str] = None
    slug: Optional[str] = None
    field: Optional[str] = None
    value: Optional[str] = None


class ProseEditResult(_Passthrough):
    """``POST /api/prose-edit`` response: ``{saved, file}`` on success; failures
    carry ``error`` via ``extra="allow"``.

    Source: ``lib.prose_edit_views.prose_edit``.
    """


    saved: bool = False
    file: Optional[str] = None


# --- viva-investigation rewire endpoint (Phase 2.1h) -----------------------


class ISetCloseBody(_Passthrough):
    """POST /api/iset-close {slug, dry_run?, no_pr?, skip_report?}

    Closes an investigation: renders the workspace report, stamps the
    investigation YAML (``status: closed`` + ``closed_at`` + ``report_url`` +
    ``contributors[]``), commits on the investigation branch, and — unless
    ``no_pr`` — opens/refreshes a PR (NEVER ``--auto``). Wraps
    ``discovery_engine.investigation.close.close_investigation``.
    """


    slug: Optional[str] = None
    dry_run: Optional[bool] = None
    no_pr: Optional[bool] = None
    skip_report: Optional[bool] = None


class ISetCloseResult(_Passthrough):
    """``POST /api/iset-close`` response — the plugin's ``CloseResult.to_dict()``
    (``slug, branch, contributors, actions, pr_url, dry_run``).

    Source: ``lib.iset_close_views.iset_close``.
    """


    slug: Optional[str] = None
    branch: Optional[str] = None
    contributors: list[dict] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    pr_url: Optional[str] = None
    dry_run: bool = False


class BandProvenanceSetBody(_Passthrough):
    """POST /api/band-provenance {study, test_name, cites, calibration_anchor?}

    ``cites`` is a non-empty list of bib_keys (required — this is the
    ``/viva-cite-bands`` skill's write op). ``calibration_anchor`` is an
    optional object, written verbatim when provided (mirrors
    ``discovery_engine.study.band_provenance.set_band_provenance``'s keyword).
    """


    study: Optional[str] = None
    test_name: Optional[str] = None
    cites: Optional[list[str]] = None
    calibration_anchor: Optional[dict] = None


class ProposedInputDecisionBody(_Passthrough):
    """POST /api/proposed-input-decision {investigation, item_id, decision}"""


    investigation: Optional[str] = None
    item_id: Optional[Any] = None
    decision: Optional[str] = None


class ResolveDecisionBody(_Passthrough):
    """POST /api/investigation-decision-resolve {investigation, index, resolution, question?}"""


    investigation: Optional[str] = None
    index: Optional[Any] = None
    resolution: Optional[str] = None
    question: Optional[str] = None


class ResolveRevisionProposalBody(_Passthrough):
    """POST /api/investigation-revision-proposal-resolve {investigation, id?|index?, decision, note?}"""

    investigation: Optional[str] = None
    id: Optional[str] = None
    index: Optional[Any] = None
    decision: Optional[str] = None
    note: Optional[str] = None


class ResolveReconciliationBody(_Passthrough):
    """POST /api/investigation-reconciliation-resolve {investigation, id?|index?, note?}"""

    investigation: Optional[str] = None
    id: Optional[str] = None
    index: Optional[Any] = None
    note: Optional[str] = None


class StudySeedFollowupBody(_Passthrough):
    """POST /api/study-seed-followup {parent, finding_id?, followup_idx?, proposal_id?, proposal_idx?, study_type?}"""


    parent: Optional[str] = None
    finding_id: Optional[Any] = None
    followup_idx: Optional[Any] = None
    proposal_id: Optional[str] = None
    proposal_idx: Optional[Any] = None
    study_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Batch 21: Investigation scaffold mutation request-body models
# ---------------------------------------------------------------------------


class InvestigationCreateRequest(_Passthrough):
    """POST /api/investigation-create {name, overview?, parent_studies?}"""


    name: Optional[str] = None
    overview: Optional[str] = None
    parent_studies: Optional[list] = None


class IsetCloneBody(_Passthrough):
    """POST /api/investigation-clone {source, target, source_prefix?, target_prefix?}"""


    source: Optional[str] = None
    target: Optional[str] = None
    source_prefix: Optional[str] = None
    target_prefix: Optional[str] = None


class InvestigationDeleteBody(_Passthrough):
    """POST /api/investigation-delete {name} (also accepts study / investigation aliases)"""


    name: Optional[str] = None
    study: Optional[str] = None
    investigation: Optional[str] = None


class StudyDeleteBody(_Passthrough):
    """POST /api/study-delete {name} (also accepts study / investigation aliases)"""


    name: Optional[str] = None
    study: Optional[str] = None
    investigation: Optional[str] = None


# ---------------------------------------------------------------------------
# Batch 22: Investigation comparison & group mutation request-body models
# ---------------------------------------------------------------------------


class InvestigationComparisonAddBody(_Passthrough):
    """POST /api/investigation-comparison-add {investigation, name, variants[], observables[], description?}

    ``study`` is also accepted as an alias for ``investigation`` (the legacy
    handler checks both: ``body.get("investigation") or body.get("study")``).
    """


    investigation: Optional[str] = None
    study: Optional[str] = None  # legacy alias
    name: Optional[str] = None
    variants: Optional[list] = None
    observables: Optional[list] = None
    description: Optional[str] = None


class InvestigationComparisonUpdateBody(_Passthrough):
    """POST /api/investigation-comparison-update {investigation, name, fields_to_update}"""


    investigation: Optional[str] = None
    name: Optional[str] = None
    fields_to_update: Optional[dict] = None


class InvestigationGroupAddBody(_Passthrough):
    """POST /api/investigation-group-add {investigation, name, variants[], description?}"""


    investigation: Optional[str] = None
    name: Optional[str] = None
    variants: Optional[list] = None
    description: Optional[str] = None


class InvestigationGroupUpdateBody(_Passthrough):
    """POST /api/investigation-group-update {investigation, name, fields_to_update}"""


    investigation: Optional[str] = None
    name: Optional[str] = None
    fields_to_update: Optional[dict] = None


# ---------------------------------------------------------------------------
# DELETE-route request-body models (re-exposed over FastAPI). The old stdlib
# server read a JSON body on DELETE; these mirror that shape verbatim.
# ---------------------------------------------------------------------------


class SimulationDeleteBody(_Passthrough):
    """DELETE /api/simulation {name}"""


    name: Optional[str] = None


class SimulationRunDeleteBody(_Passthrough):
    """DELETE /api/simulation-run {run_id}"""


    run_id: Optional[str] = None


class CompositeConfigTranslateBody(_Passthrough):
    """POST /api/composite-config-translate {composite_id, config_json}

    Item 86: adapts a user-supplied JSON document onto a composite's own
    declared parameter names — the "external config" input mode alongside
    the per-field parameter form. Non-mutating (no write side effects).
    """


    composite_id: Optional[str] = None
    config_json: Optional[dict] = None


class ConfigToCompositeBody(_Passthrough):
    """POST /api/config-to-composite {config_json}

    Translates a vEcoli config JSON document into a loom-renderable composite
    document via the workspace env worker's ``config_to_composite`` method.
    ``config_json`` is typed ``Any`` (not ``dict``) so a non-object payload
    reaches :func:`~vivarium_workbench.lib.config_to_composite_views.build_config_composite`
    for its own 422, rather than being rejected by pydantic validation first.
    """


    config_json: Any = None


class VisualizationDeleteBody(_Passthrough):
    """DELETE /api/visualization {name}"""


    name: Optional[str] = None


class InvestigationCompositeDeleteBody(_Passthrough):
    """DELETE /api/investigation-composite {investigation, name}"""


    investigation: Optional[str] = None
    name: Optional[str] = None


class InvestigationComparisonDeleteBody(_Passthrough):
    """DELETE /api/investigation-comparison {investigation, name}"""


    investigation: Optional[str] = None
    name: Optional[str] = None


class InvestigationGroupDeleteBody(_Passthrough):
    """DELETE /api/investigation-group {investigation, name}"""


    investigation: Optional[str] = None
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# Batch 23: Visualization file-write mutation request-body models
# ---------------------------------------------------------------------------


class VisualizationCreateBody(_Passthrough):
    """POST /api/visualization-create {name}"""


    name: Optional[str] = None


class VisualizationAddToProjectBody(_Passthrough):
    """POST /api/visualization-add-to-project {name}"""


    name: Optional[str] = None


class VisualizationGenerateBody(_Passthrough):
    """POST /api/visualization-generate {name, description}"""


    name: Optional[str] = None
    description: Optional[str] = None


# Batch 24: Visualization commit mutation request-body models


class ObservableAddBody(_Passthrough):
    """POST /api/observable {name, store_path, units?, description?}"""


    name: Optional[str] = None
    store_path: Optional[str] = None
    units: Optional[str] = None
    description: Optional[str] = None


class VisualizationAddBody(BaseModel):
    """POST /api/visualization {name, description?, class?, type?, observables?, config?, simulation?}

    ``class`` is a reserved keyword in Python; model field uses ``class_`` with alias ``class``.
    ``model_dump(exclude_unset=True)`` preserves the ``"key" in body`` semantics for
    optional fields (``config``, ``observables``) so the lib builder sees absence vs empty.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: Optional[str] = None
    description: Optional[str] = None
    # ``class`` is a Python keyword — expose via alias; lib reads body.get("class")
    class_: Optional[str] = Field(default=None, alias="class")
    type: Optional[str] = None
    observables: Optional[list] = None
    config: Optional[dict] = None
    simulation: Optional[str] = None


class VisualizationCommitBatchBody(_Passthrough):
    """POST /api/visualization-commit-batch {names?: list[str]}"""


    names: Optional[list] = None


class VisualizationAcceptBody(_Passthrough):
    """POST /api/visualization-accept {name, class_name?}

    ``name`` is required (400 when missing/blank); ``class_name`` is optional —
    when supplied, the generated module must expose a discoverable visualization
    class of that name.
    """


    name: Optional[str] = None
    class_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Batch 25: Upload / import mutation request-body models
# ---------------------------------------------------------------------------


class DatasetUploadBody(_Passthrough):
    """POST /api/dataset {name, claims?, file_b64?, filename?, path?, url?, sha256?, investigation?}

    Three mutually exclusive source forms (file_b64+filename / path / url); the
    lib builder dispatches on which is present. ``extra="allow"`` keeps any
    forward-compatible keys; ``model_dump(exclude_unset=True)`` preserves the
    ``"key" in body`` presence semantics the builder relies on.
    """


    name: Optional[str] = None
    claims: Optional[Any] = None
    file_b64: Optional[str] = None
    filename: Optional[str] = None
    path: Optional[str] = None
    url: Optional[str] = None
    sha256: Optional[str] = None
    investigation: Optional[str] = None


class CompositeConfigPersistBody(_Passthrough):
    """POST /api/composite-config-persist {file_b64, filename, composite_id?}

    Backs the Composite Explorer's file-upload control for ``config_file``
    params: the uploaded file is stored under the workspace and its absolute
    path is returned to become the param's value. ``model_dump(exclude_unset=True)``
    preserves the ``"key" in body`` presence semantics the builder relies on.
    """


    file_b64: Optional[str] = None
    filename: Optional[str] = None
    composite_id: Optional[str] = None


class ExpertDocUploadBody(_Passthrough):
    """POST /api/expert-doc {name, file_b64?, filename?, source_path?, description?, contributor?, claims_supported?, investigation?}

    One of ``file_b64``+``filename`` or ``source_path`` is required; the lib
    builder validates and dispatches.
    """


    name: Optional[str] = None
    file_b64: Optional[str] = None
    filename: Optional[str] = None
    source_path: Optional[str] = None
    description: Optional[str] = None
    contributor: Optional[str] = None
    claims_supported: Optional[Any] = None
    investigation: Optional[str] = None


class ImportRegisterBody(_Passthrough):
    """POST /api/import {name, source, ref, mode, description?}"""


    name: Optional[str] = None
    source: Optional[str] = None
    ref: Optional[str] = None
    mode: Optional[str] = None
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# Batch 26: Reference mutation request-body models
# ---------------------------------------------------------------------------


class ReferencePdf(_Passthrough):
    """POST /api/reference-pdf {pdf_b64, title?, authors?, year?, journal?, doi?, bib_key?, investigation?, claim_mappings?}

    Drop-and-go PDF reference flow: pypdf extracts metadata from the PDF and the
    typed fields override it.  The lib builder reads via ``body.get(...)`` so a
    plain ``model_dump()`` (unset → None) is fine; ``extra="allow"`` keeps any
    forward-compatible keys.  ``year`` / ``claim_mappings`` are intentionally
    loose (the builder coerces ``int(year)`` and accepts a str or list of claim
    ids).
    """


    pdf_b64: Optional[str] = None
    title: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[Any] = None
    journal: Optional[str] = None
    doi: Optional[str] = None
    bib_key: Optional[str] = None
    investigation: Optional[str] = None
    claim_mappings: Optional[Any] = None


class ReferenceBibtex(_Passthrough):
    """POST /api/reference-bibtex (alias /api/reference) {bibtex_text, pdf_b64?, investigation?, claim_mappings?}

    BibTeX-paste reference flow.  ``claim_mappings`` accepts a ``"cid:bkey,..."``
    string or a dict; the builder normalizes both.
    """


    bibtex_text: Optional[str] = None
    pdf_b64: Optional[str] = None
    investigation: Optional[str] = None
    claim_mappings: Optional[Any] = None


# ---------------------------------------------------------------------------
# Batch 27: Composite mutation request-body models
# ---------------------------------------------------------------------------


class InvestigationCompositeAdd(_Passthrough):
    """POST /api/investigation-composite-add {investigation, name, source}

    Clone a registered workspace composite (YAML source or
    ``@composite_generator``) into the study. The lib builder reads via
    ``body.get(...)``; ``extra="allow"`` keeps forward-compatible keys.
    """


    investigation: Optional[str] = None
    name: Optional[str] = None
    source: Optional[str] = None


class InvestigationCompositePerturb(_Passthrough):
    """POST /api/investigation-composite-perturb {investigation|study, name,
    extends, description?, parameter_overrides?, process_overrides?}

    Derive a composite from an existing sidecar by applying overrides and upsert
    a v2 ``variants`` entry. ``investigation`` and ``study`` are interchangeable
    (the builder reads either). Overrides are intentionally loose mappings.
    """


    investigation: Optional[str] = None
    study: Optional[str] = None
    name: Optional[str] = None
    extends: Optional[str] = None
    description: Optional[str] = None
    parameter_overrides: Optional[Any] = None
    process_overrides: Optional[Any] = None


class CompositePromoteToCatalog(_Passthrough):
    """POST /api/composite-promote-to-catalog {investigation, variant,
    target_name?, description?}

    Promote an investigation variant's sidecar into the workspace composite
    catalog. ``target_name`` defaults to ``variant`` when omitted.
    """


    investigation: Optional[str] = None
    variant: Optional[str] = None
    target_name: Optional[str] = None
    description: Optional[str] = None


class InvestigationCompositeRebuild(_Passthrough):
    """POST /api/investigation-composite-rebuild {investigation, name}

    Re-render a derived composite by re-applying the recipe overrides on the
    current parent document.
    """


    investigation: Optional[str] = None
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# Batch 28: Investigation composite/viz mutation request-body models
# ---------------------------------------------------------------------------


class StudyCreateFromComposite(_Passthrough):
    """POST /api/study-create-from-composite {composite_name}

    Clone a workspace-catalog composite into a fresh study. The lib
    builder reads via ``body.get(...)``; ``extra="allow"`` keeps
    forward-compatible keys.
    """


    composite_name: Optional[str] = None


class InvestigationAddViz(_Passthrough):
    """POST /api/investigation-add-viz {investigation, name, address, config}

    Append a visualization entry to a study's ``spec.yaml``. ``config`` is an
    arbitrary mapping passed through to the viz entry.
    """


    investigation: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    config: Optional[Any] = None


class InvestigationRenderViz(_Passthrough):
    """POST /api/investigation-render-viz {name}

    Re-render a study's declared visualizations against its existing emitter
    data (no simulation re-run).
    """


    name: Optional[str] = None


# ---------------------------------------------------------------------------
# SP-C: Configure & Run widget request-body models
# ---------------------------------------------------------------------------


class SaveRunAsVariantRequest(_Passthrough):
    """POST /api/save-run-as-variant {run_id, source_db?, study, variant_name}

    Persist a completed composite run (identified by ``run_id`` in
    ``source_db``) as a named study variant inside ``studies/<study>/study.yaml``.
    ``source_db`` defaults to ``<workspace>/.pbg/composite-runs.db`` when omitted.
    """


    run_id: str = ""
    source_db: Optional[str] = None
    study: str = ""
    variant_name: str = ""


class RunDeleteRequest(_Passthrough):
    """POST /api/run-delete {run_id, db_path?}

    Delete a composite run row from the SQLite store and remove its artifact
    directory (``<workspace>/.pbg/runs/<run_id>/``) if present.
    ``db_path`` defaults to ``<workspace>/.pbg/composite-runs.db`` when omitted.
    Only ``run_id`` is required (400 if missing).
    """


    run_id: str = ""
    db_path: str = ""


class JobStatusPayload(_Passthrough):
    """200-path payload for the in-memory job-status GET routes.

    Shared by ``GET /api/investigation-run-unblocked-status`` (reads
    ``lib.run_jobs.manager``) and ``GET /api/remote-run-status`` (reads
    ``lib.remote_run_jobs.manager``).  The 200 body is one of two variable
    shapes:

      * no ``job_id``    → ``{"jobs": [<job.to_dict()>, ...]}`` (recent jobs)
      * known ``job_id`` → a single ``job.to_dict()`` dict

    ``RunJob.to_dict()`` emits ``items[]`` while ``RemoteRunJob.to_dict()``
    emits ``steps[]``, so the body is genuinely polymorphic.  Pure pass-through
    (``extra="allow"``, **no declared fields**) — a declared field would
    strip/coerce one of the two shapes.  The 404 (``{"error": "job not
    found"}``) is a real non-200 path returned via ``JSONResponse``, not this
    model.

    Source: ``lib.job_status_views.job_status``.
    """



class SourceSwitchRequest(_Passthrough):
    """POST /api/source/switch request body.

    Target a registered workspace-catalog entry by ``path`` (legacy) or by
    ``name`` (the session-per-tab spawn: a tab opens ``/?workspace=<name>`` and its
    bootstrap POSTs ``{"name": <catalog name>}`` — no filesystem path in the URL).
    ``path`` wins when both are set. Either must resolve to a registered entry;
    arbitrary paths / unknown names are rejected with HTTP 400 by the lib builder.
    """


    path: str = ""
    name: str = ""


class SourceSwitchResponse(_Passthrough):
    """200-path payload for ``POST /api/source/switch``.

    ``{"ok": True, "source": {"path": <resolved>, "name": <catalog name|null>}}``.
    The non-200 error paths (``{"error": ...}``, HTTP 400) are returned via
    ``JSONResponse``, not this model.

    Source: ``lib.source_switch_views.source_switch``.
    """


    ok: bool = True
    source: "SourceSwitchSource"


class SourceSwitchSource(BaseModel):
    """The ``source`` sub-object of :class:`SourceSwitchResponse`."""

    path: str
    name: Optional[str] = None


SourceSwitchResponse.model_rebuild()


class MaterializeRepoRequest(_Passthrough):
    """POST /api/source/materialize-repo request body — ``{"repo", "ref"}``.

    Kicks off async materialization of a managed ``(repo, ref)`` source (clone +
    ``uv sync``). Missing ``repo``/``ref`` are rejected with HTTP 400 by the route.
    """


    repo: Optional[str] = None
    ref: Optional[str] = None


class AuthPayload(_Passthrough):
    """Pass-through payload for the 5 GitHub device-flow auth routes, and the 3 compose-api sign-in routes
    (``/api/auth/compose-api/{start,status,logout}``).

    Shared response schema for ``POST /api/auth/github/start``, ``GET
    /api/auth/github/poll``, ``GET /api/auth/github/status``, ``POST
    /api/auth/github/logout`` and ``GET /api/auth/github/orgs``.  The bodies are
    genuinely variable per route AND per outcome — ``{flow_id, user_code,
    verification_uri, ...}`` (start), ``{status, interval?}`` / ``{status,
    login}`` (poll), ``{authenticated, login?, source?, scopes?}`` (status),
    ``{ok: true}`` (logout), ``{login, orgs:[...]}`` (orgs), and every error
    shape ``{error: ...}`` / ``{status: "error", detail: ...}``.

    Pure pass-through (``extra="allow"``, **no declared fields**) — a declared
    field would strip/coerce one of the shapes.  The route returns every path
    (success AND error) via ``JSONResponse`` so the lib-returned status code is
    preserved verbatim; this model exists only to document the surface in the
    OpenAPI schema / generated TypeScript.

    Source: ``lib.auth_views`` (5 builders over ``lib.github_auth``).
    """



class BranchPushRequest(_Passthrough):
    """POST /api/branch/push request body — ``{"message"?: <commit message>}``.

    Optional; an empty/omitted ``message`` falls back to ``"dashboard commit"``
    inside the route's lib builder.
    """


    message: Optional[str] = None


class BranchPushResponse(_Passthrough):
    """200-path payload for ``POST /api/branch/push``.

    ``{"ok": True, "pushed": <bool>, "commit": <sha>, "branch": <name>}``.
    The non-200 error paths (``{"error": ...}``, HTTP 409/500) are returned via
    ``JSONResponse``, not this model.

    Source: ``lib.git_commit_views.branch_push``.
    """


    ok: bool = True
    pushed: bool
    commit: str
    branch: str


class DirtyCommitAllResponse(_Passthrough):
    """200-path payload for ``POST /api/dirty-commit-all``.

    ``{"commit_sha": <sha[:7]>, "message": <auto-message>, "paths": [<rel path>, ...]}``.
    The non-200 error paths (``{"error": ...}``, HTTP 409/500) are returned via
    ``JSONResponse``, not this model.

    Source: ``lib.git_commit_views.dirty_commit_all``.
    """


    commit_sha: str
    message: str
    paths: list[str]


# ---------------------------------------------------------------------------
# C-state-3f2: workstream-lifecycle POST routes
#   POST /api/work-start /api/work-push /api/work-end /api/work-attach-report
# All four shell out to git in the active workspace via the pure
# lib.work_mutations builders; every path (success AND error) is returned via
# JSONResponse so the lib-returned status code is preserved verbatim — these
# response models document only the 200-path surface in the OpenAPI schema /
# generated TypeScript.  Request bodies use Optional fields so the lib's own
# validation (not FastAPI's 422) produces the legacy 400 messages.
# ---------------------------------------------------------------------------


class WorkStartRequest(_Passthrough):
    """POST /api/work-start request body — ``{"branch", "base"?}``.

    ``branch`` is Optional so an empty/omitted/invalid name reaches the lib
    builder's ``invalid branch name`` 400 path (rather than FastAPI's 422);
    ``base`` defaults to ``"main"`` inside the builder.
    """


    branch: Optional[str] = None
    base: Optional[str] = None


class WorkStartResponse(_Passthrough):
    """200-path payload for ``POST /api/work-start``.

    ``{"ok": True, "branch": <name>, "base": <name>}``.  The non-200 error
    paths (``{"error": ...}``, HTTP 400/404/409/500) are returned via
    ``JSONResponse``, not this model.

    Source: ``lib.work_mutations.work_start``.
    """


    ok: bool = True
    branch: str
    base: str


class WorkPushResponse(_Passthrough):
    """200-path payload for ``POST /api/work-push``.

    ``{"ok": True, "branch": <name>, "log": <push stdout tail>}``.  The non-200
    error paths (``{"error", "diagnosis"?}``, HTTP 409/500) are returned via
    ``JSONResponse``, not this model.

    Source: ``lib.work_mutations.work_push``.
    """


    ok: bool = True
    branch: str
    log: str


class WorkEndResponse(_Passthrough):
    """200-path payload for ``POST /api/work-end`` — ``{"ok": True}``.

    The non-200 error paths (``{"error": ...}``, HTTP 409) are returned via
    ``JSONResponse``, not this model.

    Source: ``lib.work_mutations.work_end``.
    """


    ok: bool = True


class WorkAttachReportRequest(_Passthrough):
    """POST /api/work-attach-report request body — ``{"filename", "html", "commit_message"?}``.

    All fields Optional so missing ``filename``/``html`` reach the lib builder's
    ``filename + html required`` 400 path (rather than FastAPI's 422).
    """


    filename: Optional[str] = None
    html: Optional[str] = None
    commit_message: Optional[str] = None


class WorkAttachReportResponse(_Passthrough):
    """200-path payload for ``POST /api/work-attach-report``.

    Happy path ``{"ok": True, "path": <rel>, "branch": <name>, "commit_sha": <sha>}``;
    the soft-success no-op path adds ``"unchanged": True`` (and omits
    ``commit_sha``).  The non-200 error paths (``{"error": ...}``, HTTP
    400/409/500) are returned via ``JSONResponse``, not this model.

    Source: ``lib.work_mutations.work_attach_report``.
    """


    ok: bool = True
    path: str
    branch: str


class WorkCreatePrRequest(_Passthrough):
    """POST /api/work-create-pr request body — ``{"title"?, "body"?, "draft"?}``.

    All fields Optional: ``title``/``body`` default inside the lib builder
    (investigation title / ``Workstream: <branch>``, and the canned PR body);
    ``draft`` defaults to ``True`` (the builder reads ``body.get("draft", True)``)
    so an omitted flag still produces a draft PR.
    """


    title: Optional[str] = None
    body: Optional[str] = None
    draft: Optional[bool] = None


class WorkCreatePrResponse(_Passthrough):
    """200-path payload for ``POST /api/work-create-pr``.

    ``{"ok": True, "pr_url": <url>, "pr_number": <int|null>}``.  The non-200
    error paths (``{"error", "pr_url"?, "manual_url"?}``, HTTP 409/500) are
    returned via ``JSONResponse``, not this model.

    Source: ``lib.work_pr_views.work_create_pr``.
    """


    ok: bool = True
    pr_url: str
    pr_number: Optional[int] = None


class WorkLinkBranchRequest(_Passthrough):
    """POST /api/work-link-branch request body.

    ``{upstream_repo?, branch_name?, push?: bool, mode?: "branch"|"fork"}`` —
    all Optional.  ``upstream_repo`` defaults inside the lib builder to the
    auto-detected upstream (workspace.yaml / external remote / fallback);
    ``branch_name`` defaults to the active workstream branch; ``push`` defaults
    to ``True`` (``body.get("push", True)``); ``mode`` defaults to ``"branch"``.
    The route uses ``model_dump(exclude_none=True)`` so omitted fields stay
    absent and the builder applies its own defaults.
    """


    upstream_repo: Optional[str] = None
    branch_name: Optional[str] = None
    push: Optional[bool] = None
    mode: Optional[str] = None


class WorkLinkBranchResponse(_Passthrough):
    """200-path payload for ``POST /api/work-link-branch``.

    Branch mode → ``{ok, upstream_repo, branch, branch_url}``; fork mode →
    ``{ok, fork, upstream, branch, branch_url}`` (``extra="allow"`` carries the
    mode-specific keys).  The non-200 error paths (HTTP 400/409/500,
    ``{"error", "current_origin"?}``) are returned via ``JSONResponse``, not
    this model.

    Source: ``lib.work_pr_views.work_link_branch``.
    """


    ok: bool = True
    branch: str
    branch_url: str


# ---------------------------------------------------------------------------
# C-state-3h1: workspace-registry POST routes
#   POST /api/workspaces/add /api/workspaces/forget /api/workspaces/cleanup-stale
# All three edit the GLOBAL ~/.pbg workspace catalog via
# discovery_engine.integration.workspace_catalog (no ws_root) through the pure
# lib.workspaces_mutations builders; every path (success AND error) is returned
# via JSONResponse so the lib-returned status code is preserved verbatim.  The
# request model's ``path`` is Optional so an omitted path reaches the lib
# builder's own 400 validation (the legacy ``path must be an absolute string`` /
# ``path required`` messages) rather than FastAPI's 422.
# ---------------------------------------------------------------------------


class WorkspacesPathRequest(_Passthrough):
    """Request body for the 3 workspace-registry POSTs — ``{"path"?: <abs path>}``.

    Shared by ``POST /api/workspaces/add`` / ``/api/workspaces/forget`` /
    ``/api/workspaces/cleanup-stale``.  ``path`` is Optional so a
    missing/empty/relative value reaches the lib builder's own 400 validation
    (``"path must be an absolute string"`` for add, ``"path required"`` for
    forget/cleanup-stale) rather than FastAPI's 422.
    """


    path: Optional[str] = None


class WorkspacesOkResponse(_Passthrough):
    """200-path payload for ``POST /api/workspaces/forget`` and
    ``/api/workspaces/cleanup-stale`` — ``{"ok": True}``.

    The non-200 error paths (``{"error": ...}``, HTTP 400/409) are returned via
    ``JSONResponse``, not this model.  ``POST /api/workspaces/add`` returns the
    catalog ``entry`` dict (variable shape) instead, served as the pass-through
    ``WorkspaceEntry``.

    Source: ``lib.workspaces_mutations.workspaces_forget`` /
    ``workspaces_cleanup_stale``.
    """


    ok: bool = True


class WorkspaceEntry(_Passthrough):
    """200-path payload for ``POST /api/workspaces/add`` — the catalog entry dict.

    Pure pass-through (``extra="allow"``, no declared fields) — the
    ``workspace_catalog.add`` entry shape is owned by ``viva_superpowers`` and
    carries variable keys (``name``, ``path``, status, ...).  The non-200 error
    paths (``{"error": ...}``, HTTP 400) are returned via ``JSONResponse``, not
    this model.

    Source: ``lib.workspaces_mutations.workspaces_add``.
    """



class RenderRequest(_Passthrough):
    """POST /api/render request body — ``{"today"?, "force"?}``. Both optional;
    an absent/empty body preserves the previous unconditional-render behavior.

    Phase 2.1d — makes ``/api/render`` a thin server-side equivalent of the old
    ``/viva-report`` skill's CLI render usage
    (``discovery_engine.reporting.report.render_workspace_report``):

      * ``today`` — forwarded into the render as the generation date, for a
        byte-stable CI render (the old ``--today`` option).
      * ``force`` — mirrors the old skill's forced-render path
        (``render_workspace_report(force=True)``): before rendering, every
        currently-blocking report-lint finding is logged to
        ``.pbg/report-lint-overrides.json``.

    Source: ``lib.misc_mutations.render_dashboard``.
    """


    today: Optional[str] = None
    force: bool = False


class RenderResponse(_Passthrough):
    """200-path payload for ``POST /api/render`` — ``{"ok": True}``.

    The error path (``{"error": str(e)}``, HTTP 500) is returned via
    ``JSONResponse``, not this model.

    Source: ``lib.misc_mutations.render_dashboard``.
    """


    ok: bool = True


class VisualizationAcceptResponse(_Passthrough):
    """200-path payload for ``POST /api/visualization-accept`` — ``{"ok": True}``.

    The FastAPI path returns ``{ok: True}`` on success; the git commit is not
    yet wired under the FastAPI seam.  The
    error paths (``{"error": ...}``, HTTP 400/404/500) are returned via
    ``JSONResponse``, not this model.

    Source: ``lib.viz_accept_views.visualization_accept``.
    """


    ok: bool = True


# ---------------------------------------------------------------------------
# P1: study-run / test-run POST request bodies (4 routes under "Study runs").
#
# All four routes return large, variable result dicts (or 400/404/409/500
# error shapes), so the routes use ``JSONResponse`` on every path and declare
# NO ``response_model`` — only these request models are needed.  ``extra="allow"``
# keeps the legacy raw-JSON contract (e.g. ``_study_name_from_body`` also reads
# ``name``/``investigation`` aliases, ``run_study_baseline`` reads ``composite``);
# the routes pass ``model_dump(exclude_none=True)`` so an OMITTED optional stays
# absent and the lib builders' ``.get(...)`` defaults apply.
# ---------------------------------------------------------------------------
class StudyRunBaselineRequest(_Passthrough):
    """POST /api/study-run-baseline request body — ``{"study", "steps"?}``.

    Runs a study's baseline composite. A missing study is rejected with HTTP 400
    by ``lib.study_runs.run_study_baseline``.
    """


    study: str = ""
    steps: Optional[int] = None


class StudyRunVariantRequest(_Passthrough):
    """POST /api/study-run-variant request body — ``{"study", "variant", "steps"?}``.

    Runs a study variant (single run) or delegates an ensemble sweep. A missing
    study/variant is rejected with HTTP 400 by ``lib.study_runs.run_study_variant``.
    """


    study: str = ""
    variant: str = ""
    steps: Optional[int] = None


class StudyTestsRunRequest(_Passthrough):
    """POST /api/study-tests-run request body — ``{"study"}``.

    Runs pytest against ``studies/<study>/tests/``. A missing study is rejected
    with HTTP 400 by ``lib.test_run_views.study_tests_run``.
    """


    study: str = ""


class StudyGradeRequest(BaseModel):
    """POST /api/study-grade request body — ``{"study"}``."""
    study: str



class StudyReproduceRequest(_Passthrough):
    """POST /api/study-reproduce request body — ``{"study", "run_id"}``.

    reproducible-rerun-spine Task 4 / G2: replays a previously-recorded run's
    stored MANIFEST (params/seed/emitter/emit_paths/runtime exactly as they
    were at launch time) — never the study's current ``study.yaml``. This is
    the deliberately-named counterpart to ``POST /api/study-run-baseline``
    ("Run current spec", which re-derives from the live spec): the two are
    kept as separate routes/buttons precisely so "Reproduce" can never be
    confused with "run the spec as it stands today". ``study`` is carried for
    routing/UI symmetry with the other study-scoped routes; the actual replay
    target is resolved from ``run_id`` alone (``lib.rerun.resolve_rerun_target``
    looks the run up directly, independent of which study it's passed as).
    """


    study: str = ""
    run_id: str = ""


class InvestigationRerunRequest(_Passthrough):
    """POST /api/investigation-rerun request body — ``{"investigation"}``.

    Reruns every member study's baseline for the named investigation. An
    investigation with no declared studies (or that doesn't exist) yields an
    empty, still-200 batch result from ``lib.rerun.rerun_investigation``.
    """


    investigation: str = ""



class InvestigationResolveRequest(_Passthrough):
    """POST /api/investigation-resolve request body — ``{"investigation", "force"?}``.

    Opt-in topological pull-or-compute over an investigation's member DAG
    (``lib.artifacts.pipeline.resolve_investigation``), as opposed to the
    declared-order ``/api/investigation-rerun`` (``InvestigationRerunRequest``)
    which stays the default and is untouched by this endpoint.
    """


    investigation: str = ""
    force: bool = False


class InvestigationResolveNode(_Passthrough):
    """One DAG node's outcome from ``resolve_investigation`` — a study or a
    discovered (non-member) producer it transitively depends on.
    """


    slug: str
    artifact_id: Optional[str] = None
    status: str = ""
    inputs: list = []


class InvestigationResolveResult(_Passthrough):
    """Result of ``POST /api/investigation-resolve`` — the topological
    pull-or-compute outcome across an investigation's member DAG: resolution
    order, each node's cached/computed/skipped/failed status, and (only on a
    cyclic member DAG) an ``error`` string.
    """


    investigation: Optional[str] = None
    order: list = []
    nodes: list[InvestigationResolveNode] = []
    error: Optional[str] = None


class InvestigationTriggerRequest(_Passthrough):
    """POST /api/investigation-trigger request body (Layer-4 pull-or-compute).

    Wraps ``process_bigraph.templates.trigger`` over an investigation's study
    graph: build the pbg investigation document, ``trigger`` on ``target_study``
    (pulling every cached prerequisite, leaving the target to compute, pruning
    non-ancestors), and — unless ``launch`` is ``False`` — launch the target
    through the existing detached composite-run subsystem. "Run this study" and
    "continue from here reusing cached upstream" are the same call with a
    different ``target_study``.
    """


    investigation: str = ""
    target_study: str = ""
    # 'error' (default): refuse to silently recompute an uncached prerequisite,
    # naming it (HTTP 409). 'compute': leave uncached prerequisites to compute.
    on_missing: str = "error"
    # When False, return the {pulled, computed, pruned} plan only (no run
    # spawned) — used by the read-only bundle / for previewing the plan.
    launch: bool = True


class RunTestsRequest(_Passthrough):
    """POST /api/run-tests request body — empty ``{}`` (v0.3.0: no model param).

    Runs pytest for the whole workspace. Source:
    ``lib.test_run_views.run_workspace_tests``.
    """



class CompositeTestRunRequest(_Passthrough):
    """POST /api/composite-test-run request body.

    ``{"id", "overrides"?, "steps"?, "label"?, "emit_paths"?}`` — starts a
    detached composite run. A missing ``id`` is rejected with HTTP 400 by
    ``lib.composite_test_run_views.composite_test_run``. ``model_dump(
    exclude_none=True)`` keeps omitted optionals absent so the builder's
    ``.get(...)`` defaults apply.
    """


    id: str = ""
    overrides: Optional[dict] = None
    # Run length in composite time units. float so a temporal composite can run a
    # fractional duration (e.g. 2.5); a workflow just passes 1.
    steps: Optional[float] = None
    label: Optional[str] = None
    emit_paths: Optional[list] = None
    # Config declaration surface (composite-auto-results Task 6): optional
    # study-shaped (possibly scale-grouped) analyses/visualizations blocks to
    # auto-run on flush, merged with the composite's own defaults downstream
    # (lib.ephemeral_study.merge_declarations). Declared explicitly (rather
    # than relying on extra="allow" passthrough) so they're documented in the
    # OpenAPI schema and generated TS types.
    analyses: Optional[list] = None
    visualizations: Optional[list] = None


# ---------------------------------------------------------------------------
# Misc POST request bodies (3 routes under the "Misc" tag).
#
# All three routes return variable result dicts (or error shapes), so the routes
# use ``JSONResponse`` on every path and declare NO ``response_model`` — only
# these request models are needed.  ``extra="allow"`` keeps the legacy raw-JSON
# contract; the routes pass ``model_dump(exclude_none=True)`` so an OMITTED
# optional stays absent and the lib builders' ``.get(...)`` defaults apply.
# ---------------------------------------------------------------------------
class SuggestRequest(_Passthrough):
    """POST /api/suggest request body — ``{"kind", "context_extras"?}``.

    Writes a Claude-suggestion request file. An invalid ``kind`` is rejected with
    HTTP 400 by ``lib.misc_post_views.suggest``.
    """


    kind: str = ""
    context_extras: Optional[dict] = None


class StudyReportSingleRequest(_Passthrough):
    """POST /api/study-report-single request body.

    ``{"investigation"?, "study"?, "skeptic"?}`` — renders a standalone one-study
    HTML report. The ``?skeptic=`` query param is merged into the body at the
    route. Either ``study`` or ``investigation`` is required (enforced by
    ``lib.single_study_report.build_single_study_report_for_test``, HTTP 400).
    """


    investigation: Optional[str] = None
    study: Optional[str] = None
    skeptic: Optional[bool] = None


class OpenWindowRequest(_Passthrough):
    """POST /api/open-window request body — ``{"route"?}``.

    Opens a dashboard URL in the user's browser. Source:
    ``lib.misc_post_views.open_window``.
    """


    route: Optional[str] = None


class InvestigationRunOneRequest(_Passthrough):
    """POST /api/investigation-run-one request body.

    ``{"investigation"|"study"|"name", "sim_name"?, "overrides"?, "steps"?,
    "label"?}`` — runs a single ad-hoc composite execution (the "Duplicate run"
    flow) and appends to the investigation's ``runs.db``. A missing investigation
    is rejected with HTTP 400 by
    ``lib.investigation_run_one_views.investigation_run_one``. ``model_dump(
    exclude_none=True)`` keeps omitted optionals absent so the builder's
    ``.get(...)`` defaults apply.
    """


    investigation: Optional[str] = None
    study: Optional[str] = None
    name: Optional[str] = None
    sim_name: Optional[str] = None
    overrides: Optional[dict] = None
    steps: Optional[int] = None
    label: Optional[str] = None


class InvestigationRunRequest(_Passthrough):
    """POST /api/investigation-run request body.

    ``{"name"|"study"|"investigation"}`` — runs ALL of an investigation's
    simulations and renders its visualizations (the "Run investigation" flow).
    A missing name is rejected with HTTP 400 by
    ``lib.investigation_run_views.investigation_run``. ``model_dump(
    exclude_none=True)`` keeps omitted optionals absent so the builder's
    ``_study_name_from_body`` name resolution applies. The variable summary
    dict is returned via ``JSONResponse`` (no response model).
    """


    name: Optional[str] = None
    study: Optional[str] = None
    investigation: Optional[str] = None


class StudyCreateRequest(_Passthrough):
    """POST /api/study-create request body.

    ``{"name", "source"?, "composite"?}`` — scaffolds a new investigation
    directory (``data/.keep`` plus one of three scaffold shapes keyed on the
    resolved ``source`` composite). ``source`` is an optional composite ref that
    seeds a baseline composite; the legacy ``composite`` field is accepted but
    ignored when ``source`` is provided. A missing/blank ``name`` (or one that
    fails the ``^[a-zA-Z0-9_-]+$`` regex) is rejected with HTTP 400, an existing
    investigation with HTTP 409, and an unresolvable ``source`` with HTTP 404 by
    ``lib.study_create_views.study_create``. ``extra="allow"``
    preserves any other body keys, and ``model_dump(exclude_none=True)`` keeps
    omitted optionals absent so the builder's ``.get(...)`` defaults apply (and
    so a missing ``name`` reaches the builder's 400 rather than a 422). The
    ``{ok, name}`` success body (the live ``_active_branch_action`` commit is
    deferred — the builder runs the scaffold inline) and every error path are
    returned via ``JSONResponse`` (no response model).
    """


    name: Optional[str] = None
    source: Optional[str] = None
    composite: Optional[str] = None


class InvestigationRunUnblockedRequest(_Passthrough):
    """POST /api/investigation-run-unblocked request body.

    ``{"investigation", "studies"?}`` — enumerates every member study's
    unblocked variants and submits one background run job (the "Run unblocked"
    flow); the client polls ``GET /api/investigation-run-unblocked-status``.
    ``studies`` optionally narrows the run to a subset (a single slug or a list
    of slugs). A missing/empty ``investigation`` is rejected with HTTP 400 by
    ``lib.run_unblocked_views.investigation_run_unblocked``. ``model_dump(
    exclude_none=True)`` keeps an omitted ``studies`` absent so the builder's
    "all studies" default applies. The ``{job_id, items}`` 202 body and every
    error path are returned via ``JSONResponse`` (no response model).
    """


    investigation: str = ""
    studies: Optional[Union[list[str], str]] = None


class VisualizationPreviewRequest(_Passthrough):
    """POST /api/visualization-preview request body.

    ``{"address", "config"?, "source"?}`` — renders a ``viva_superpowers``
    Visualization class IN-PROCESS against synthetic demo data (``source:
    "demo"``, default) OR an existing investigation's emitter outputs
    (``source: "investigation:<name>"``). A missing ``address`` is rejected with
    HTTP 400 and an unregistered class with HTTP 404 by
    ``lib.viz_preview_views.visualization_preview``; EVERY other path — including
    a demo render that raises — returns 200. ``model_dump(exclude_none=True)``
    keeps omitted optionals absent so the builder's ``.get(...)`` defaults apply.
    The ``{ok, html, source_used, notes}`` body is returned via ``JSONResponse``
    (no response model).
    """


    address: str = ""
    config: Optional[dict] = None
    source: Optional[str] = None


class VisualizationPreviewInstanceRequest(_Passthrough):
    """POST /api/visualization-preview-instance request body.

    ``{"name", "source"?}`` — previews a ``workspace.yaml``-registered
    visualization instance BY NAME. The instance's class + config are looked up
    and the route delegates to ``lib.viz_preview_views.visualization_preview``;
    a description-only entry (no ``class``) returns a stub instead. A missing
    ``name`` is rejected with HTTP 400 and an unregistered instance with HTTP 404
    by ``lib.viz_preview_instance_views.visualization_preview_instance``.
    ``model_dump(exclude_none=True)`` keeps omitted optionals absent so the
    builder's ``.get(...)`` defaults apply. The ``{ok, html, source_used, notes}``
    (or delegated) body is returned via ``JSONResponse`` (no response model).
    """


    name: str = ""
    source: Optional[str] = None


class SystemDepsInstallRequest(_Passthrough):
    """POST /api/system-deps-install request body — ``{"name", "check_names"}``.

    Runs the install commands for the named catalog module's system-dependency
    checks. A missing ``name`` or empty ``check_names`` is rejected with HTTP 400
    by ``lib.install_views.system_deps_install``; an unknown module with HTTP
    404. ``model_dump(exclude_none=True)`` keeps both fields present (they are
    never ``None``) so the builder's validation applies. The ``{ok, log, recheck}``
    body is returned via ``JSONResponse`` (no response model).
    """


    name: str = ""
    check_names: list = []


class ImportInstallRequest(_Passthrough):
    """POST /api/import-install request body — ``{"name", "target"?}``.

    Pip-installs a ``workspace.yaml``-registered import into the workspace venv
    (``target`` overrides the default ``imports[name].path``). A missing ``name``
    is rejected with HTTP 400 and an unregistered import with HTTP 404 by
    ``lib.install_views.import_install``. ``model_dump(exclude_none=True)`` keeps
    an omitted ``target`` absent so the builder's path fallback applies. The
    ``{ok, log}`` success body (the live ``_active_branch_action`` commit is
    deferred — the builder runs the workspace.yaml mutation inline) and every
    error path are returned via ``JSONResponse`` (no response model).
    """


    name: str = ""
    target: Optional[str] = None


class CatalogInstallRequest(_Passthrough):
    """POST /api/catalog-install request body — ``{"name", "skip_system_deps_check"?}``.

    Installs a catalog module into the workspace venv — directly from PyPI when
    the catalog entry carries a ``pypi_name``, otherwise via the legacy
    git-submodule + editable-install path. A missing ``name`` is rejected with
    HTTP 400 and an unknown module with HTTP 404 by
    ``lib.catalog_install_views.catalog_install``. When the catalog declares
    native system-dependency checks and any are unsatisfied, the install is
    refused with HTTP 409 (structured ``missing`` info); ``skip_system_deps_check``
    bypasses that gate. ``extra="allow"`` preserves any other body keys, and
    ``model_dump(exclude_none=True)`` keeps an omitted ``skip_system_deps_check``
    absent so the builder's default applies. The ``{ok, module, install_mode,
    log}`` success body (the live ``_commit_or_run`` commit is deferred — the
    builder runs the install + workspace.yaml mutation inline) and every error
    path are returned via ``JSONResponse`` (no response model).
    """


    name: str = ""
    skip_system_deps_check: Optional[bool] = None


class CatalogUninstallRequest(_Passthrough):
    """POST /api/catalog-uninstall request body — ``{"name"}``.

    Removes a catalog module from the workspace, reversing catalog-install. A
    missing ``name`` is rejected with HTTP 400 by
    ``lib.catalog_uninstall_views.catalog_uninstall``. When the module is not
    declared in ``workspace.yaml.imports`` the builder delegates to
    ``uninstall_unmanaged_or_404`` (a venv-only "unmanaged" install: best-effort
    uv/pip uninstall + untracked ``external/<name>/`` removal → 200, real-parent
    409, or no-pip/uv 500). Otherwise it runs the pypi (remove dependency + pip
    uninstall) or reference (remove dependency + uv source + git submodule
    deinit/rm + pip uninstall) uninstall and pops the ``imports`` entry.
    ``extra="allow"`` preserves any other body keys, and
    ``model_dump(exclude_none=True)`` mirrors the legacy ``body.get`` access. The
    ``{ok, module, install_mode, log}`` success body (the live ``_commit_or_run``
    commit is deferred — the builder runs the uninstall + workspace.yaml mutation
    inline) and every error path are returned via ``JSONResponse`` (no response
    model).
    """


    name: str = ""


class AiProviderStatus(BaseModel):
    """One provider row of ``GET /api/ai/status``. Never carries a key.

    ``source`` is where the credential comes from (``keyring`` / ``memory`` /
    ``environment`` / ``aws``), ``None`` when the provider is not configured.
    """

    id: str
    configured: bool
    source: Optional[str] = None
    base_url: Optional[str] = None


class AiSelection(BaseModel):
    """The chosen provider + model (non-secret)."""

    provider: str
    model: str


class AiStatusPayload(BaseModel):
    """``GET /api/ai/status``. ``available`` is False when the ``[chat]`` extra
    is not installed or the chat is switched off (``reason`` says which); ``storage_mode`` is ``keyring`` (loopback bind) or
    ``memory`` (hosted — keys live only in this process, per session).

    Source: ``lib.ai_views.ai_status``.
    """

    available: bool
    reason: Optional[str] = None
    providers: list[AiProviderStatus]
    selected: Optional[AiSelection] = None
    storage_mode: Literal["keyring", "memory"]


class AiOllamaModelsPayload(BaseModel):
    """``POST /api/ai/ollama-models`` — the models installed in an Ollama server (its
    ``/api/tags``); ``source`` is the URL asked."""

    models: list[str]
    source: str


class AiOllamaModelsRequest(BaseModel):
    """``POST /api/ai/ollama-models`` body: the Ollama endpoint to ask (default: the saved one, else localhost)."""

    base_url: Optional[str] = Field(default=None, max_length=2048)


class AiCredentialsRequest(BaseModel):
    """``POST /api/ai/credentials`` body. ``api_key`` (or ``base_url`` for
    ``openai-compatible``) is checked with one real 1-token request to ``model``
    before anything is stored."""

    provider: str = Field(max_length=64)
    model: str = Field(max_length=256)
    api_key: Optional[str] = Field(default=None, max_length=8192)
    base_url: Optional[str] = Field(default=None, max_length=2048)


class AiSelectRequest(BaseModel):
    """``POST /api/ai/select`` body — switch provider/model."""

    provider: str = Field(max_length=64)
    model: str = Field(max_length=256)


class AiOkPayload(_Passthrough):
    """``{ok: true, provider, model?, source?}`` acknowledgement for the
    ``/api/ai/*`` writes."""


    ok: bool
    provider: str


class ChatTurnRequest(BaseModel):
    """``POST /api/chat/turn`` body. ``messages`` is the transcript the browser
    got back in the previous turn's ``done`` frame (pydantic-ai messages as
    JSON; the server stores none). Send exactly one of ``prompt`` (a new user
    message) or ``deferred_results`` (``{"approvals": {tool_call_id: true |
    {"denied": reason}}}`` — the user's decision on the calls the previous turn
    paused on). The response is an NDJSON stream (see ``lib.ai_chat``).
    """

    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    prompt: Optional[str] = Field(default=None, max_length=300_000)   # attached text files are inlined
    deferred_results: Optional[dict[str, Any]] = None
    # marimo's footer modes: manual = pure chat (no tools), ask = read-only tools,
    # agent = read + write tools (every write still pauses for approval).
    mode: Literal["manual", "ask", "agent"] = "agent"
    # Inject a live workspace summary into the instructions (costs tokens, and it leaves the machine).
    # Unset: on for a local (keyring) server, off on a shared one. Manual mode never sends it.
    include_manifest: Optional[bool] = None


class AiCapabilitiesPayload(BaseModel):
    """``GET /api/ai/capabilities`` — what the assistant can reach, counted from the live
    OpenAPI (feeds the chat's Capabilities popover). ``excluded`` lists the deliberately
    withheld routes/tags."""

    reads: int
    writes: int
    excluded: list[str]



class ComparisonEngineRun(BaseModel):
    """One engine's run of one job (``diagnostics.runs.<model>.<job>.<engine>``)."""

    status: str                      # ok | unavailable | error | ...
    runtime_s: Optional[float] = None
    n_points: Optional[int] = None
    error: str = ""


class ComparisonJob(BaseModel):
    """One ``comparisons.<model>.<job>`` entry, reduced for display
    (``lib/study_comparison.py``). ``matrix`` is the engine-by-engine NRMSE
    (null where a cell was non-finite or absent); the per-species ``pairs``
    blobs are intentionally not carried."""

    model: str
    job: str
    engines: list[str]
    matrix: dict[str, dict[str, Optional[float]]]
    max_nrmse: Optional[float] = None
    worst_pair: Optional[list[str]] = None
    bucket: Optional[str] = None
    bucket_label: Optional[str] = None
    closeness_bucket_label: Optional[str] = None
    runs: dict[str, ComparisonEngineRun] = Field(default_factory=dict)


class ComparisonBucketCount(BaseModel):
    label: str
    count: int


class StudyComparison(BaseModel):
    """The ``comparison`` block of ``GET /api/study-results`` when the latest
    run's state carries cross-engine comparisons. ``jobs`` is worst-first;
    ``buckets`` is best-to-worst."""

    n_models: int
    n_jobs: int
    engines: list[str]
    buckets: list[ComparisonBucketCount]
    jobs: list[ComparisonJob]
