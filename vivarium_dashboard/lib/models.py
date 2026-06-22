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

from typing import Literal, Optional

from pydantic import BaseModel

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
    """One inline-SVG chart (``lib/study_charts.py``)."""

    key: str
    title: str
    caption: str
    svg: str


class StudyChartsPayload(BaseModel):
    """``GET /api/study-charts/<slug>`` payload (server.py ``_study_charts_payload``).

    The handler may attach additional top-level keys; only ``charts`` is part of
    the typed contract here. Unknown keys are ignored on validation.
    """

    charts: list[ChartPayload]
