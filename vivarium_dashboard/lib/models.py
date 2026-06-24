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

from pydantic import BaseModel, ConfigDict

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
