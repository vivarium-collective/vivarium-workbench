import json
import sqlite3
import tarfile
from pathlib import Path

from vivarium_dashboard.lib.remote_run_landing import land_remote_run


def _make_remote_zarr_tar(tmp_path: Path, seed: int = 0) -> Path:
    """Build a tar.gz mirroring a Ray run: seed_NN/store.zarr with an experiment_id=* partition.

    Note: xarray/numpy are not installed in the dashboard venv so we create the
    zarr directory structure manually.  _latest_zarr_for_study only requires the
    ``experiment_id=*`` child directory to exist (study_charts.py:641), not
    parseable zarr data, so a plain directory is sufficient for all test assertions.
    """
    staging = tmp_path / "staging"
    # Minimal store: the dashboard reader only needs the runs.*.zarr dir to contain an
    # experiment_id=* child to be selected; internal leaf detail is exercised elsewhere.
    part = staging / f"seed_{seed:02d}" / "store.zarr" / f"experiment_id=exp-seed{seed:02d}"
    part.mkdir(parents=True)
    # Place a sentinel file so the partition dir is non-empty (mirrors a real zarr shard)
    (part / ".zgroup").write_text('{"zarr_format":2}')
    tar_path = tmp_path / "sim_49.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return tar_path


def test_land_zarr_places_store_and_writes_runs_meta(tmp_path: Path):
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = land_remote_run(
        study,
        spec_id="v2ecoli.composites.baseline",
        simulation_id=49,
        experiment_id="exp-abc",
        commit="abc123",
        tar_path=tar,
        seed=0,
    )
    # zarr store placed at <study>/runs.<run_id>.zarr with the experiment_id=* partition intact
    zarr_dir = study / f"runs.{run_id}.zarr"
    assert zarr_dir.is_dir()
    assert next(zarr_dir.glob("experiment_id=*"), None) is not None

    # runs_meta written, status completed, provenance carries simulation_id, store path recorded
    conn = sqlite3.connect(str(study / "runs.db"))
    try:
        meta = conn.execute(
            "SELECT status, params_json FROM runs_meta WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert meta[0] == "completed"
    prov = json.loads(meta[1])
    assert prov["simulation_id"] == 49
    assert prov["store_path"].endswith(f"runs.{run_id}.zarr")


def test_landed_zarr_is_discovered_by_study_charts(tmp_path: Path):
    from vivarium_dashboard.lib import study_charts

    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = land_remote_run(
        study, spec_id="s", simulation_id=7, experiment_id="e", commit="c", tar_path=tar, seed=0
    )
    found = study_charts._latest_zarr_for_study(study)
    assert found == study / f"runs.{run_id}.zarr"


def test_land_stores_s3_uri_in_provenance(tmp_path: Path):
    """s3_uri kwarg is recorded in params_json provenance."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = land_remote_run(
        study,
        spec_id="v2ecoli.composites.baseline",
        simulation_id=77,
        experiment_id="exp-s3",
        commit="deadbeef",
        tar_path=tar,
        seed=0,
        s3_uri="s3://bucket/prefix/exp/",
    )
    conn = sqlite3.connect(str(study / "runs.db"))
    try:
        meta = conn.execute(
            "SELECT params_json FROM runs_meta WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    prov = json.loads(meta[0])
    assert prov["s3_uri"] == "s3://bucket/prefix/exp/"
