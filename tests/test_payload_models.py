"""The pydantic payload models must match what the handlers actually emit.

These tests feed REAL handler output into the models (not hand-written literals),
so they fail if a handler's dict shape drifts away from its model.
"""

from vivarium_dashboard.lib.models import (
    ChartPayload,
    RemoteOrigin,
    RemoteRunJob as RemoteRunJobModel,
    SimRow,
    SimulationsPayload,
    StudyChartsPayload,
)
from vivarium_dashboard.lib.remote_run_jobs import RemoteRunJob
from vivarium_dashboard.lib.simulations_index import _row_to_dict

import json


def _row(**overrides):
    """A dict standing in for a sqlite3.Row (which _row_to_dict accesses by key)."""
    base = {
        "run_id": "r1",
        "spec_id": "baseline",
        "sim_name": "sim",
        "label": "Run 1",
        "status": "completed",
        "n_steps": 10,
        "progress_step": 10,
        "started_at": None,
        "completed_at": None,
        "params_json": None,
    }
    base.update(overrides)
    return base


def test_simrow_matches_local_row():
    """A local run (no provenance) validates: emitter None, remote_origin None."""
    out = _row_to_dict(_row(), "/ws/study-a/runs.db")
    model = SimRow.model_validate(out)
    assert model.emitter is None
    assert model.remote_origin is None
    assert model.run_id == "r1"


def test_simrow_matches_remote_zarr_row():
    """A remote Ray/zarr run validates: store-derived emitter + RemoteOrigin."""
    prov = {
        "source": "smsvpctest",
        "simulation_id": 105,
        "experiment_id": "exp-1",
        "backend": "ray",
        "s3_uri": "s3://bucket/prefix/exp-1",
        "store_path": "/ws/study-a/runs.r1.zarr",
    }
    out = _row_to_dict(_row(params_json=json.dumps(prov)), "/ws/study-a/runs.db")
    model = SimRow.model_validate(out)
    assert model.emitter == "xarray"
    assert isinstance(model.remote_origin, RemoteOrigin)
    assert model.remote_origin.simulation_id == 105
    assert model.remote_origin.deployment == "smsvpctest"


def test_simrow_dump_roundtrips_to_handler_dict():
    """model_dump() reproduces the handler dict exactly (safe to adopt at a seam)."""
    out = _row_to_dict(_row(), "/ws/study-a/runs.db")
    assert SimRow.model_validate(out).model_dump() == out


def test_simulations_payload_shape():
    out = _row_to_dict(_row(), "/ws/study-a/runs.db")
    payload = {"simulations": [out], "current": "study-a"}
    model = SimulationsPayload.model_validate(payload)
    assert model.current == "study-a"
    assert model.simulations[0].run_id == "r1"


def test_remote_run_job_to_dict_validates():
    """The real RemoteRunJob.to_dict() output validates against the model."""
    job = RemoteRunJob("study-a")
    job.set_step("push", "ok", "pushed abc123")
    job.simulation_id = 105
    model = RemoteRunJobModel.model_validate(job.to_dict())
    assert model.status == "queued"
    assert [s.name for s in model.steps] == ["push", "build", "run", "poll", "download", "land"]
    assert model.steps[0].status == "ok"
    assert model.simulation_id == 105


def test_chart_payload_shape():
    chart = {"key": "cell_mass", "title": "Cell mass", "caption": "over time", "svg": "<svg/>"}
    assert ChartPayload.model_validate(chart).key == "cell_mass"
    payload = StudyChartsPayload.model_validate({"charts": [chart], "extra_ignored": True})
    assert len(payload.charts) == 1
