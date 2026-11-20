"""The SMS plugin's payload models (docs/backend-adapters.md §2.2, step P3 M1): the wire shapes only SMS serves.

Moved verbatim from ``vivarium_workbench/lib/models.py`` -- same class names, fields and docstrings, so the OpenAPI
schema names, shapes and descriptions are unchanged. The routes in ``vivarium_workbench_sms/routes.py`` and the
legacy pipeline's jobs (``remote_run_jobs.py``) use them; ``SmsProvider.payload_types()`` hands them to the core's
TypeScript generator (``lib/generate_ts.py``), which emits them after the core's types.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

# sms-api job lifecycle (JobStatus enum, mirrored).
RemoteJobStatus = Literal[
    "unknown", "waiting", "pending", "queued",
    "running", "completed", "cancelled", "failed",
]


class RemoteRunStep(BaseModel):
    """One pipeline step of a remote run. ``name`` is one of STEP_NAMES."""

    name: str                  # push | build | run | poll | download | land
    status: str                # pending | running | ok | error | ...
    message: str = ""


class RemoteRunJob(BaseModel):
    """A remote-run job (``vivarium_workbench_sms/remote_run_jobs.py`` ``RemoteRunJob.to_dict``)."""

    job_id: str
    study: str
    status: RemoteJobStatus
    steps: list[RemoteRunStep]
    run_id: Optional[str] = None
    simulation_id: Optional[int] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class SourceBuilds(BaseModel):
    """``GET /api/source/builds`` payload (vivarium_workbench_sms.source_build_views.build_source_builds).

    Best-effort remote build list: ``{builds: [...], error: str|null}``.  Always
    HTTP 200 — ``builds`` is empty and ``error`` carries a reason when the
    sms-api is unreachable.

    Pure pass-through (``extra="allow"``, no declared fields) so build-entry
    keys (simulator_id, repo, branch, commit, label, …) survive verbatim.

    Source: ``vivarium_workbench_sms.source_build_views.build_source_builds`` (the SMS provider's route).
    """

    model_config = ConfigDict(extra="allow")


class BuildRemoteRequest(BaseModel):
    """POST /api/source/build-remote request body — ``{"repo", "branch"}``.

    Registers a repo+branch HEAD as an sms-api build. Empty repo/branch are
    rejected with HTTP 400 by the route's lib builder.
    """

    model_config = ConfigDict(extra="allow")

    repo: str = ""
    branch: str = ""


class BuildRemoteResponse(BaseModel):
    """200-path payload for ``POST /api/source/build-remote``.

    ``{"ok": True, "simulator_id": <id|null>, "repo", "branch", "commit"}``.
    The non-200 error paths (``{"error": ...}``, HTTP 400/502) are returned via
    ``JSONResponse``, not this model.

    Source: ``vivarium_workbench_sms.source_build_views.build_remote``.
    """

    model_config = ConfigDict(extra="allow")

    ok: bool = True
    simulator_id: Optional[int] = None
    repo: str
    branch: str
    commit: str


class SwitchBuildRequest(BaseModel):
    """POST /api/source/switch-build request body — ``{"simulator_id"}``.

    Materializes a build's workspace (cached) + re-points in-process. A missing
    ``simulator_id`` is rejected with HTTP 400 by the route's lib builder.
    """

    model_config = ConfigDict(extra="allow")

    simulator_id: Optional[int] = None


class RemoteRunStartRequest(BaseModel):
    """POST /api/remote-run-start request body.

    ``{"study", "num_generations"?, "num_seeds"?, "run_parca"?}`` — submits a
    remote sms-api pipeline job for the study.  An empty/missing ``study`` is
    rejected with HTTP 400 by the route's lib builder.
    """

    model_config = ConfigDict(extra="allow")

    study: str = ""
    num_generations: Optional[int] = None
    num_seeds: Optional[int] = None
    run_parca: Optional[bool] = None


class RemoteRunStartResponse(BaseModel):
    """202-path payload for ``POST /api/remote-run-start`` — ``{"job_id": <id>}``.

    The non-202 error paths (``{"error": ...}``, HTTP 401/400/409/404) are
    returned via ``JSONResponse``, not this model.

    Source: ``vivarium_workbench_sms.remote_pipeline_views.remote_run_start``.
    """

    model_config = ConfigDict(extra="allow")

    job_id: str
