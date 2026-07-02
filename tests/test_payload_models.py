"""The pydantic payload models must match what the handlers actually emit.

These tests feed REAL handler output into the models (not hand-written literals),
so they fail if a handler's dict shape drifts away from its model.
"""

from vivarium_workbench.lib.models import (
    ChartPayload,
    RemoteOrigin,
    RemoteRunJob as RemoteRunJobModel,
    SimRow,
    SimulationsPayload,
    StudyChartsPayload,
)
from vivarium_workbench.lib.remote_run_jobs import RemoteRunJob
from vivarium_workbench.lib.simulations_index import _row_to_dict

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
        "started_at": 1700000000.0,   # runs_meta.started_at is REAL epoch seconds
        "completed_at": 1700000050.0,
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
    assert isinstance(model.started_at, float)   # epoch seconds, not a string


def test_simrow_accepts_spec_study_synthesised_shapes():
    """Spec/study-synthesised index rows are looser than DB rows: ``spec_id`` and
    ``db_path`` may be None, ``emitter`` may be a free-form spec string, and
    ``studies`` may be bare slug strings. SimRow must validate them (so
    /api/simulations never 500s on a real workspace) and round-trip unchanged."""
    loose = {
        "run_id": "r9",
        "spec_id": None,
        "status": "completed",
        "started_at": 1.0,
        "db_path": None,
        "emitter": "unknown",            # not one of the EmitterKind literals
        "studies": ["ketchup-exchange-comparison"],   # bare slug, not a StudyRef
    }
    m = SimRow.model_validate(loose)
    assert m.spec_id is None
    assert m.db_path is None
    assert m.emitter == "unknown"
    assert m.studies == ["ketchup-exchange-comparison"]   # bare string preserved
    dumped = m.model_dump()
    assert dumped["spec_id"] is None and dumped["db_path"] is None
    assert dumped["emitter"] == "unknown"
    assert dumped["studies"] == ["ketchup-exchange-comparison"]


def test_row_to_dict_is_model_backed():
    """_row_to_dict now builds output via SimRow.model_dump() (load-bearing)."""
    out = _row_to_dict(_row(), "/ws/study-a/runs.db")
    # The returned dict equals a fresh model_dump of the same data → it came
    # through the model, and re-validating is a no-op.
    assert SimRow.model_validate(out).model_dump() == out


def test_malformed_row_falls_back_with_warning(recwarn):
    """A row that fails validation (non-numeric started_at) warns and returns the
    raw dict rather than raising — the simulations index must not 500 on one bad
    row. (``started_at=None`` is now a *valid* synthesised-row shape, so use a
    genuinely uncoercible value to exercise the fallback.)"""
    out = _row_to_dict(_row(started_at="not-a-number"), "/ws/study-a/runs.db")
    assert out["run_id"] == "r1"            # legacy dict still served
    assert out["started_at"] == "not-a-number"
    assert any("SimRow" in str(w.message) for w in recwarn.list)


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
    """RemoteRunJob.to_dict() is model-backed and validates against the model."""
    job = RemoteRunJob("study-a")
    job.set_step("push", "ok", "pushed abc123")
    job.simulation_id = 105
    out = job.to_dict()
    # to_dict now builds output via RemoteRunJobModel.model_dump() — re-validating
    # and dumping is a no-op, confirming it came through the model.
    assert RemoteRunJobModel.model_validate(out).model_dump() == out
    model = RemoteRunJobModel.model_validate(out)
    assert model.status == "queued"
    assert [s.name for s in model.steps] == ["push", "build", "run", "poll", "download", "land"]
    assert model.steps[0].status == "ok"
    assert model.simulation_id == 105


def test_remote_run_job_falls_back_on_bad_status(recwarn):
    """An unexpected status (outside the RemoteJobStatus literal) warns and
    returns the legacy dict rather than breaking the status endpoint."""
    job = RemoteRunJob("study-a")
    job.status = "bogus-status"
    out = job.to_dict()
    assert out["status"] == "bogus-status"          # legacy dict still served
    assert any("RemoteRunJob" in str(w.message) for w in recwarn.list)


def test_chart_payload_shape():
    chart = {"key": "cell_mass", "title": "Cell mass", "caption": "over time", "svg": "<svg/>"}
    assert ChartPayload.model_validate(chart).key == "cell_mass"
    payload = StudyChartsPayload.model_validate({
        "study": "dnaa-1", "schema_version": 4, "charts": [chart],
        "db_exists": True, "static_count": 0, "live_count": 1,
    })
    assert len(payload.charts) == 1
    assert payload.study == "dnaa-1" and payload.live_count == 1


def test_chart_payload_polymorphic_static():
    """A static image chart (img data-URI, no svg) validates and keeps extras."""
    chart = {
        "key": "chromosome-map", "title": "Chromosome", "caption": "static",
        "img": "data:image/png;base64,AAAA", "source": "static",
        "media": "png", "freshness": "fresh", "simulations": "baseline",
    }
    c = ChartPayload.model_validate(chart)
    assert c.svg is None and c.img.startswith("data:image/png") and c.source == "static"
