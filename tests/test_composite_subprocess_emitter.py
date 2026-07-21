from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_subprocess as cs


def test_injects_both_ram_and_declared_parquet(tmp_path: Path):
    # Asserting on the *declared* emitter requires the v2ecoli generator to be
    # registered; v2ecoli is a workspace repo, not a workbench dependency, so
    # without it the declared branch no-ops and this fails rather than skips.
    pytest.importorskip("v2ecoli.composites.baseline")
    state = {"global_time": 0.0, "bulk": {}, "listeners": {}}
    out = cs.inject_run_emitters(
        state, spec_id="v2ecoli.composites.baseline",
        run_id="v2ecoli.composites.baseline__1__aa",
        emit_paths=["global_time"], workspace=tmp_path,
        db_file=tmp_path / "runs.db")
    assert "user_emitter" in out["state"]                 # RAM live view
    assert out["state"]["user_emitter"]["address"].lower().endswith("ramemitter")
    assert "declared_emitter" in out["state"]             # parquet persistence
    assert out["emitter"] == "parquet"


def test_sqlite_fallback_honors_callers_db_file(tmp_path: Path):
    """CRITICAL regression test: when spec_id has no declared emitter, the
    SQLite fallback must persist to the CALLER's db_file (e.g.
    ``<study_dir>/runs.db``), not a hardcoded ``<workspace>/.pbg/
    composite-runs.db`` scratchpad path — otherwise per-tick history lands
    somewhere the Study Runs tab never reads it back from."""
    db_file = tmp_path / "studies" / "foo" / "runs.db"
    state = {"global_time": 0.0, "bulk": {}, "listeners": {}}
    out = cs.inject_run_emitters(
        state, spec_id="unregistered.spec.not.in.catalog",
        run_id="unregistered.spec.not.in.catalog__1__bb",
        emit_paths=["global_time"], workspace=tmp_path,
        db_file=db_file)
    assert out["emitter"] == "sqlite"
    cfg = out["state"]["sqlite_emitter"]["config"]
    assert cfg["file_path"] == str(db_file.parent)
    assert cfg["db_file"] == db_file.name
