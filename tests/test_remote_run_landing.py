import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from vivarium_workbench.lib.remote_run_landing import land_ptools_tsvs


# The SMS landing (``land_remote_run`` and friends) is the SMS plugin's: the tests that use it take the ``sl`` fixture
# (or the golden tail) and are marked ``plugin``; ``land_ptools_tsvs`` and the core half of the seam check are core
# (docs/backend-adapters.md step P3, K2b).
@pytest.fixture
def sl():
    """The SMS plugin's ``sms_landing`` module."""
    from vivarium_workbench_sms import sms_landing

    return sms_landing


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


def _make_remote_zarr_tar_multiseed(tmp_path: Path, seeds: tuple[int, ...] = (0, 1)) -> Path:
    """Build a tar.gz mirroring a real multi-seed Ray dispatch: one independently-rooted
    seed_NN/store.zarr per seed, each with its own uniquely-named experiment_id=* partition —
    exactly the shape confirmed against real GovCloud/Ray S3 output."""
    staging = tmp_path / "staging"
    for seed in seeds:
        part = staging / f"seed_{seed:02d}" / "store.zarr" / f"experiment_id=exp-seed{seed:02d}"
        part.mkdir(parents=True)
        (part / ".zgroup").write_text('{"zarr_format":2}')
        # Trivial top-level group marker, repeated per seed store (mirrors real output).
        (staging / f"seed_{seed:02d}" / "store.zarr" / "zarr.json").write_text('{"zarr_format":3}')
    tar_path = tmp_path / "sim_multiseed.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return tar_path


def _make_remote_zarr_tar_with_analysis(tmp_path: Path, seed: int = 0) -> Path:
    """A tar mirroring what lands once ``v2ecoli-analyze`` has completed:
    seed_NN/store.zarr plus an ``analyses/<name>/analysis.json`` (the real file
    the CLI writes at its out_dir -- NOT a ``_manifest.json``, which v2ecoli
    never emits) alongside its ``viz/<name>__<group>.html`` outputs, written to
    the same S3 experiment prefix the download streams whole."""
    staging = tmp_path / "staging"
    part = staging / f"seed_{seed:02d}" / "store.zarr" / f"experiment_id=exp-seed{seed:02d}"
    part.mkdir(parents=True)
    (part / ".zgroup").write_text('{"zarr_format":2}')
    analysis_dir = staging / "analyses" / "analysis-exp-ab12"
    (analysis_dir / "viz").mkdir(parents=True)
    (analysis_dir / "viz" / "doubling_time_line__all.html").write_text("<html></html>")
    (analysis_dir / "analysis.json").write_text(json.dumps({
        "multivariant": {"doubling_time_line": {"all": {"n_cells": 2}}},
        "status": "OK",
        "summary": {"multivariant": {"doubling_time_line": "ok"}},
        "errors": [],
    }))
    tar_path = tmp_path / "sim_with_analysis.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return tar_path


def _make_empty_remote_tar(tmp_path: Path) -> Path:
    """A multi-node composite (colony) dispatch's POST /data returns an EMPTY
    (0-byte) tar -- it has no per-seed native store. tarfile.open raises
    ReadError on it, which land_remote_run must treat as "no store, try the
    analysis id-space" rather than letting it fail the landing."""
    tar_path = tmp_path / "sim_colony_empty.tar.gz"
    tar_path.write_bytes(b"")
    return tar_path


class _FakeColonyClient:
    """Stands in for SmsApiClient's analysis-figures API: the colony recovery
    surface. Records which figures were fetched so the test can assert each one
    was persisted."""

    base_url = "http://test"

    def __init__(self, analyses=None):
        self._analyses = analyses if analyses is not None else [{
            "name": "analysis-colony-1", "status": "completed", "n_tp": 5,
            "figures": [
                {"path": "viz/mass_fraction__gen=0.html"},
                {"path": "viz/mass_fraction__gen=1.html"},
            ],
        }]
        self.fetched: list[tuple[str, str]] = []

    def analysis_figures(self, simulation_id):
        return {"available": True, "reason": "ok", "analyses": self._analyses}

    def analysis_figure(self, simulation_id, analysis, path):
        self.fetched.append((analysis, path))
        return f"<html>{analysis}:{path}</html>".encode()

    def download_data(self, simulation_id, dest_dir, timeout=None):
        # A colony dispatch's POST /data returns an empty store tar.
        p = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
        p.write_bytes(b"")
        return p


@pytest.mark.plugin
def test_land_colony_no_store_lands_analysis_figures(tmp_path: Path, sl):
    """A multi-node composite (colony) dispatch has no per-seed store -- POST
    /data returns an empty tar. Landing must NOT crash (the found bug: it raised
    FileNotFoundError and recorded nothing). Instead it recovers the run's output
    from the analysis id-space: persists each figure under the study (so it
    survives publishing and needs no live backend / AWS creds), writes the
    analyses.json manifest pointing at those files, and records a completed run."""
    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "colony"
    study.mkdir(parents=True)
    client = _FakeColonyClient()

    run_id = sl.land_remote_run(
        study, spec_id="colony", simulation_id=2150, experiment_id="exp-colony",
        commit="c", tar_path=_make_empty_remote_tar(tmp_path), ws_root=ws_root,
        base_url="http://test", client=client, expected_seeds=1,
    )

    # Each figure persisted under the study, faithfully by its analysis + relpath.
    fig = study / "viz" / "analysis-colony-1" / "viz" / "mass_fraction__gen=0.html"
    assert fig.is_file()
    assert "analysis-colony-1" in fig.read_text()
    assert len(client.fetched) == 2

    # The analyses.json manifest points at the persisted study files.
    manifest = json.loads((ws_root / ".pbg" / "runs" / run_id / "analyses.json").read_text())
    assert manifest == [{
        "name": "analysis-colony-1",
        "written": [
            "viz/analysis-colony-1/viz/mass_fraction__gen=0.html",
            "viz/analysis-colony-1/viz/mass_fraction__gen=1.html",
        ],
        "errors": [],
    }]

    # The run is recorded as completed (recoverable), not crashed.
    conn = sqlite3.connect(str(study / "runs.db"))
    try:
        row = conn.execute("SELECT status FROM runs_meta WHERE run_id=?", (run_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "completed"


@pytest.mark.plugin
def test_land_simulation_artifacts_colony_no_store(tmp_path: Path, sl):
    """The study-less Runs-table land path (land_remote_simulation_artifacts) must
    also survive a colony's empty store tar: recover the analysis figures into the
    run folder instead of crashing on the missing store."""
    ws_root = tmp_path / "workspace"
    (ws_root / "studies").mkdir(parents=True)
    client = _FakeColonyClient()

    result = sl.land_remote_simulation_artifacts(ws_root, 2150, "run-colony-xyz", client)

    assert result["analyses"] is True
    assert result["figures"] == 2
    fig = ws_root / ".pbg" / "runs" / "run-colony-xyz" / "viz" / "analysis-colony-1" / "viz" / "mass_fraction__gen=0.html"
    assert fig.is_file()
    assert (ws_root / ".pbg" / "runs" / "run-colony-xyz" / "analyses.json").is_file()


@pytest.mark.plugin
def test_land_no_store_and_no_colony_analyses_reraises(tmp_path: Path, sl):
    """An empty store tar with NO recoverable analyses is a genuinely empty/failed
    run, not a colony -- landing must re-raise the store error rather than record
    a hollow run."""
    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "s"
    study.mkdir(parents=True)
    client = _FakeColonyClient(analyses=[])  # nothing completed to recover

    with pytest.raises((tarfile.ReadError, FileNotFoundError)):
        sl.land_remote_run(
            study, spec_id="s", simulation_id=1, experiment_id="e", commit="c",
            tar_path=_make_empty_remote_tar(tmp_path), ws_root=ws_root,
            base_url="http://test", client=client,
        )
    assert not (study / "runs.db").exists()  # no hollow run recorded


@pytest.mark.plugin
def test_land_folds_analysis_manifest_into_pbg_runs_analyses_json(tmp_path: Path, sl):
    """Once the analysis job has completed, landing (with ws_root passed) must
    fold its ``analysis.json`` into .pbg/runs/<run_id>/analyses.json -- the exact
    local artifact contract composite_flush.run_flush's own (non-remote)
    analyses dispatch already produces (``written`` = the landed output-file
    paths), so the existing Analyses button renders it with no further changes."""
    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "s"
    study.mkdir(parents=True)
    tar = _make_remote_zarr_tar_with_analysis(tmp_path)

    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=115, experiment_id="e", commit="c",
        tar_path=tar, ws_root=ws_root,
    )

    analyses_path = ws_root / ".pbg" / "runs" / run_id / "analyses.json"
    assert analyses_path.is_file()
    entries = json.loads(analyses_path.read_text())
    assert entries == [{
        "name": "doubling_time_line",
        "written": ["analyses/analysis-exp-ab12/viz/doubling_time_line__all.html"],
        "errors": [],
    }]


@pytest.mark.plugin
def test_land_without_analysis_manifest_writes_nothing(tmp_path: Path, sl):
    """If the analysis job hasn't completed by the time this run lands, landing
    the simulation output must still succeed -- and must not fabricate an
    analyses.json for output that was never actually produced."""
    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "s"
    study.mkdir(parents=True)
    tar = _make_remote_zarr_tar(tmp_path)

    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=116, experiment_id="e", commit="c",
        tar_path=tar, ws_root=ws_root,
    )

    assert not (ws_root / ".pbg" / "runs" / run_id / "analyses.json").exists()


@pytest.mark.plugin
def test_land_without_ws_root_skips_analysis_folding(tmp_path: Path, sl):
    """ws_root is optional -- existing callers that never trigger analysis for
    a landed run must be unaffected, even if a manifest happens to be present."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_with_analysis(tmp_path)

    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=117, experiment_id="e", commit="c", tar_path=tar,
    )

    assert run_id  # lands normally; no ws_root means no .pbg/runs/ write attempted


# ---------------------------------------------------------------------------
# item 84: land_remote_run cleans up remote_run_submit's own dispatch-time
# pending placeholder (run_id `remote-pending-<simulation_id>`) once the real,
# fully-landed row exists — the two can never share a run_id (generate_run_id
# embeds the call-TIME timestamp, so it's not reproducible from simulation_id
# alone), so without this cleanup a landed campaign would show TWICE in the
# Runs tab: a permanently-stuck "running" placeholder alongside the real
# completed row.
# ---------------------------------------------------------------------------

@pytest.mark.plugin
def test_land_removes_the_dispatch_time_pending_placeholder(tmp_path: Path, sl):
    from vivarium_workbench.lib import composite_runs as cr

    study = tmp_path / "study"
    study.mkdir()
    conn = cr.connect(study / "runs.db")
    try:
        cr.save_metadata(
            conn, spec_id="s", run_id="remote-pending-88",
            params={"source": "smscdk", "simulation_id": 88, "backend": "ray"},
            label="Remote dispatch (smscdk) — in progress",
            started_at=1000.0, n_steps=0,
        )
    finally:
        conn.close()

    tar = _make_remote_zarr_tar(tmp_path)
    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=88, experiment_id="e", commit="c", tar_path=tar,
    )
    assert run_id != "remote-pending-88"  # the real row uses generate_run_id's own scheme

    conn = sqlite3.connect(str(study / "runs.db"))
    try:
        pending = conn.execute(
            "SELECT 1 FROM runs_meta WHERE run_id=?", ("remote-pending-88",)
        ).fetchone()
        landed = conn.execute(
            "SELECT status FROM runs_meta WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert pending is None  # placeholder removed
    assert landed is not None and landed[0] == "completed"  # real row present


@pytest.mark.plugin
def test_land_without_a_pending_placeholder_is_unaffected(tmp_path: Path, sl):
    """Landing a simulation that was never dispatched through remote_run_submit
    (an older run, or one landed via another path) has no pending row to clean
    up — the delete is a silent no-op, landing succeeds exactly as before."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=999, experiment_id="e", commit="c", tar_path=tar,
    )
    conn = sqlite3.connect(str(study / "runs.db"))
    try:
        landed = conn.execute(
            "SELECT status FROM runs_meta WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert landed is not None and landed[0] == "completed"


@pytest.mark.plugin
def test_land_zarr_places_store_and_writes_runs_meta(tmp_path: Path, sl):
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = sl.land_remote_run(
        study,
        spec_id="v2ecoli.composites.baseline",
        simulation_id=49,
        experiment_id="exp-abc",
        commit="abc123",
        tar_path=tar,
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


@pytest.mark.plugin
def test_landed_zarr_is_discovered_by_study_charts(tmp_path: Path, sl):
    from vivarium_workbench.lib import study_charts

    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=7, experiment_id="e", commit="c", tar_path=tar
    )
    found = study_charts._latest_zarr_for_study(study)
    assert found == study / f"runs.{run_id}.zarr"


@pytest.mark.plugin
def test_land_stores_s3_uri_in_provenance(tmp_path: Path, sl):
    """s3_uri kwarg is recorded in params_json provenance."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = sl.land_remote_run(
        study,
        spec_id="v2ecoli.composites.baseline",
        simulation_id=77,
        experiment_id="exp-s3",
        commit="deadbeef",
        tar_path=tar,
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


@pytest.mark.plugin
def test_land_multiseed_keeps_every_seed(tmp_path: Path, sl):
    """Regression: landing a 2-seed dispatch must keep BOTH seeds' partitions, not
    silently discard every seed but the first (the found bug — land_remote_run's
    seed param defaulted to 0 and no caller ever overrode it)."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0, 1))
    run_id = sl.land_remote_run(
        study,
        spec_id="v2ecoli.composites.batch_baseline",
        simulation_id=114,
        experiment_id="exp-multiseed",
        commit="43cabf0",
        tar_path=tar,
    )
    zarr_dir = study / f"runs.{run_id}.zarr"
    partitions = sorted(p.name for p in zarr_dir.glob("experiment_id=*"))
    assert partitions == ["experiment_id=exp-seed00", "experiment_id=exp-seed01"]


@pytest.mark.plugin
def test_land_multiseed_single_seed_still_works(tmp_path: Path, sl):
    """A single-seed dispatch (the common case) still lands correctly under the
    new union-all-seeds logic — no regression for the 1-seed path."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0,))
    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=1, experiment_id="e", commit="c", tar_path=tar,
    )
    zarr_dir = study / f"runs.{run_id}.zarr"
    assert [p.name for p in zarr_dir.glob("experiment_id=*")] == ["experiment_id=exp-seed00"]


# --- native per-lineage zarr convention (BatchBaselineRunner/LineageProcess) -----
# Dispatched through the generic run_pbg.py runner (backlog items 26/27), a batch's
# own output is never restructured into seed_NN/store.zarr the way
# scripts/run_batch_baseline_ray.py used to -- each lineage's store keeps its native
# <experiment_id>_v<variant>_s<seed>.zarr name, and EVERY lineage in one batch shares
# the SAME experiment_id/variant (confirmed against a real fixture,
# tests/fixtures/redux_cards/v2ecoli_seed00.zarr: root -> experiment_id=X -> variant=Y
# -> lineage_seed=Z), unlike seed_NN/store.zarr's per-seed-unique top-level partition.


def _make_remote_native_lineage_tar(
    tmp_path: Path, seeds: tuple[int, ...] = (0, 1), experiment_id: str = "exp-native", variant: int = 0
) -> Path:
    """Build a tar.gz mirroring a real multi-lineage batch dispatched through the
    generic run_pbg.py runner: one independent `<experiment_id>_v<variant>_s<seed>.zarr`
    store per lineage, each sharing the SAME experiment_id=X/variant=Y prefix
    internally and differing only at lineage_seed=Z -- the shape that would have
    been silently collapsed to one lineage by a naive top-level-only union."""
    staging = tmp_path / "staging"
    for seed in seeds:
        part = (
            staging
            / f"{experiment_id}_v{variant}_s{seed}.zarr"
            / f"experiment_id={experiment_id}"
            / f"variant={variant}"
            / f"lineage_seed={seed}"
        )
        part.mkdir(parents=True)
        (part / "dry_mass.zarray").write_text('{"shape": [1]}')
        # Trivial shared group markers, repeated at every level across every lineage
        # (mirrors AsyncZarrBufferWriter always opening the same nested group path).
        store_root = staging / f"{experiment_id}_v{variant}_s{seed}.zarr"
        (store_root / "zarr.json").write_text('{"zarr_format":3}')
        (store_root / f"experiment_id={experiment_id}" / "zarr.json").write_text('{"zarr_format":3}')
        (store_root / f"experiment_id={experiment_id}" / f"variant={variant}" / "zarr.json").write_text(
            '{"zarr_format":3}'
        )
    tar_path = tmp_path / "sim_native_lineage.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return tar_path


@pytest.mark.plugin
def test_land_native_lineage_zarr_keeps_every_lineage(tmp_path: Path, sl):
    """Regression guard for the same class of bug #674 fixed once already, recurring
    one level deeper: every lineage in a batch shares experiment_id=X/variant=Y, so a
    naive top-level union would keep only the first-processed lineage_seed and
    silently drop the rest. All requested seeds must land."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_native_lineage_tar(tmp_path, seeds=(0, 1, 2))
    run_id = sl.land_remote_run(
        study,
        spec_id="v2ecoli.composites.ecoli_baseline",
        simulation_id=200,
        experiment_id="exp-native",
        commit="abc123",
        tar_path=tar,
    )
    zarr_dir = study / f"runs.{run_id}.zarr"
    lineage_seeds = sorted(
        p.name for p in zarr_dir.glob("experiment_id=*/variant=*/lineage_seed=*")
    )
    assert lineage_seeds == ["lineage_seed=0", "lineage_seed=1", "lineage_seed=2"]
    # Only ONE experiment_id=X/variant=Y ancestor exists (shared, not duplicated) —
    # confirms this is a genuine merge, not three independent copies side by side.
    assert [p.name for p in zarr_dir.glob("experiment_id=*")] == ["experiment_id=exp-native"]
    assert [p.name for p in zarr_dir.glob("experiment_id=*/variant=*")] == ["variant=0"]
    for seed in (0, 1, 2):
        leaf = zarr_dir / "experiment_id=exp-native" / "variant=0" / f"lineage_seed={seed}" / "dry_mass.zarray"
        assert leaf.is_file()


@pytest.mark.plugin
def test_land_native_lineage_zarr_single_lineage_still_works(tmp_path: Path, sl):
    """A single-lineage batch (n_seeds=1) still lands correctly — no regression."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_native_lineage_tar(tmp_path, seeds=(0,))
    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=201, experiment_id="exp-native", commit="c", tar_path=tar,
    )
    zarr_dir = study / f"runs.{run_id}.zarr"
    assert (zarr_dir / "experiment_id=exp-native" / "variant=0" / "lineage_seed=0").is_dir()


# ---------------------------------------------------------------------------
# P1-11 (audit §3.9): remote-run provenance was missing/wrong — image tag,
# composite doc hash, merged config hash, real n_steps, and (most
# importantly) the EXECUTED commit must all be recorded, sourced from
# sms-api's own response rather than inferred from the landing laptop's
# checkout. A seed-count mismatch (fewer/more seeds landed than requested)
# must be flagged rather than silently recorded as a trustworthy complete run.
# ---------------------------------------------------------------------------

@pytest.mark.plugin
def test_land_records_full_execution_provenance_from_sms_api_response(tmp_path: Path, sl):
    """A landed run's record must carry the image tag/digest, composite doc
    hash, merged config hash, real n_steps, and the SERVER-sourced commit —
    fixture values mirror what remote_run_views._resolve_execution_provenance
    reads from a real sms-api GET /simulations/{id} + simulator-registry
    response, not anything inferred locally."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)

    run_id = sl.land_remote_run(
        study,
        spec_id="v2ecoli.composites.baseline",
        simulation_id=321,
        experiment_id="exp-prov",
        # The commit sms-api's OWN simulator registry resolved for the
        # simulator_id that actually ran this simulation -- NOT the landing
        # laptop's local git HEAD (that mismatch was the found bug).
        commit="serverexecuted7",
        tar_path=tar,
        image="serverexecuted7",  # sms-api tags images <repo>:<commit>
        composite_doc_hash="sha256:composite-doc-abc123",
        merged_config_hash="sha256:merged-config-def456",
        n_steps=2700,
        expected_seeds=1,
    )

    conn = sqlite3.connect(str(study / "runs.db"))
    try:
        row = conn.execute(
            "SELECT params_json, manifest_json, n_steps, status FROM runs_meta WHERE run_id=?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    params_json, manifest_json, n_steps, status = row
    assert status == "completed"
    assert n_steps == 2700

    prov = json.loads(params_json)
    assert prov["commit"] == "serverexecuted7"
    assert prov["image"] == "serverexecuted7"
    assert prov["composite_doc_hash"] == "sha256:composite-doc-abc123"
    assert prov["merged_config_hash"] == "sha256:merged-config-def456"
    assert prov["landed_seeds"] == 1

    # The replay manifest's code_version/environments must ALSO reflect the
    # server-sourced commit -- this is the field the Runs-tab Source column
    # actually renders from, and it's what was silently overwritten with the
    # local workspace's HEAD before this fix.
    manifest = json.loads(manifest_json)
    assert manifest["code_version"]["git_sha"] == "serverexecuted7"
    assert manifest["code_version"]["image"] == "serverexecuted7"
    assert manifest["environments"][0]["commit"] == "serverexecuted7"


@pytest.mark.plugin
def test_land_manifest_commit_is_not_the_local_workspace_head(tmp_path: Path, sl):
    """Regression guard for the exact 'partly wrong' bug: passing a real
    ws_root (a git repo with its OWN, DIFFERENT HEAD) must not leak that
    local commit into the landed run's manifest -- only the server-sourced
    `commit` argument may appear there."""
    import subprocess

    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "s"
    study.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=ws_root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.test"], cwd=ws_root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws_root, check=True)
    (ws_root / "README.md").write_text("x")
    subprocess.run(["git", "add", "."], cwd=ws_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "local"], cwd=ws_root, check=True)
    local_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws_root, capture_output=True, text=True, check=True
    ).stdout.strip()

    tar = _make_remote_zarr_tar(tmp_path)
    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=322, experiment_id="e",
        commit="totally-different-server-commit", tar_path=tar, ws_root=ws_root,
    )
    conn = sqlite3.connect(str(study / "runs.db"))
    try:
        manifest_json = conn.execute(
            "SELECT manifest_json FROM runs_meta WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    manifest = json.loads(manifest_json)
    assert manifest["code_version"]["git_sha"] == "totally-different-server-commit"
    assert manifest["code_version"]["git_sha"] != local_head
    assert local_head not in manifest_json  # the local HEAD never leaks in anywhere


@pytest.mark.plugin
def test_land_seed_count_mismatch_raises_and_writes_nothing(tmp_path: Path, sl):
    """A dispatch that requested 3 seeds but only 2 landed (a real, observed
    failure mode -- a seed's own container crashed silently, or the tar was
    fetched before every seed finished writing) must be flagged, not recorded
    as a trustworthy completed run."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0, 1))

    import pytest

    with pytest.raises(sl.RemoteRunSeedCountMismatch):
        sl.land_remote_run(
            study, spec_id="s", simulation_id=323, experiment_id="exp-mismatch",
            commit="c", tar_path=tar, expected_seeds=3,
        )

    # Nothing was written: no zarr store landed, no runs.db row.
    assert not any(study.glob("runs.*.zarr"))
    db_path = study / "runs.db"
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM runs_meta").fetchone()[0]
        finally:
            conn.close()
        assert count == 0


@pytest.mark.plugin
def test_land_seed_count_match_succeeds(tmp_path: Path, sl):
    """The matching case must land normally -- the check is a guard, not a
    universal block."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0, 1))
    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=324, experiment_id="exp-match",
        commit="c", tar_path=tar, expected_seeds=2,
    )
    conn = sqlite3.connect(str(study / "runs.db"))
    try:
        status = conn.execute(
            "SELECT status FROM runs_meta WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "completed"


@pytest.mark.plugin
def test_count_parquet_rows_returns_none_without_fabricating_zero(tmp_path: Path, sl):
    """No history/*.pq files (or pyarrow unavailable) must come back as
    ``None`` -- never a fabricated 0 that a caller could mistake for a real,
    verified zero-step run."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert sl._count_parquet_rows(empty_dir) is None


@pytest.mark.plugin
def test_land_without_expected_seeds_skips_the_check(tmp_path: Path, sl):
    """expected_seeds is optional -- existing callers that don't know the
    requested count (or predate this fix) must be unaffected."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0, 1, 2))
    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=325, experiment_id="exp-nocheck",
        commit="c", tar_path=tar,
    )
    assert run_id  # lands without raising


def test_land_ptools_tsvs_copies_exports_into_run_dir(tmp_path: Path):
    """A landed remote-run tar's PTools TSV exports (**/ptools/*.tsv, e.g. from a
    GovCloud compose analysis) must be copied into .pbg/runs/<run_id>/ptools/ so
    the Omics Viewer discovers this run like a local study's exports."""
    extract_root = tmp_path / "extract"
    pt = extract_root / "cd2ms_k4" / "analyses" / "ptools_overview_multigeneration" / "ptools"
    pt.mkdir(parents=True)
    (pt / "ptools_overview_multigeneration__variant=0_seed=0.tsv").write_text("gene\tvalue\nb0001\t1.0\n")
    (pt / "ptools_overview_multigeneration__variant=0_seed=1.tsv").write_text("gene\tvalue\nb0001\t2.0\n")

    ws_root = tmp_path / "workspace"
    ws_root.mkdir()

    n = land_ptools_tsvs(extract_root, ws_root, "r1")

    assert n == 2
    dest = ws_root / ".pbg" / "runs" / "r1" / "ptools"
    landed = sorted(p.name for p in dest.glob("*.tsv"))
    assert landed == [
        "ptools_overview_multigeneration__variant=0_seed=0.tsv",
        "ptools_overview_multigeneration__variant=0_seed=1.tsv",
    ]
    assert "b0001" in (dest / landed[0]).read_text()


def test_land_ptools_tsvs_noop_when_none_present(tmp_path: Path):
    """No ptools/ exports in the tar → nothing written, count 0 (not an error)."""
    extract_root = tmp_path / "extract"
    (extract_root / "seed_00" / "store.zarr").mkdir(parents=True)
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()

    assert land_ptools_tsvs(extract_root, ws_root, "r2") == 0
    assert not (ws_root / ".pbg" / "runs" / "r2" / "ptools").exists()


# ---------------------------------------------------------------------------
# PTools exports of a run landed INTO A STUDY must be viewable: the Pathway Tools
# plugin (pbg-ptools) discovers ``<study>/**/ptools/*.tsv`` only, and the
# workbench never serves a dot-segment (``.pbg``), so the Pathway Tools server
# could not fetch a TSV from ``.pbg/runs/<run_id>/ptools/`` over HTTP either.
# Found by the 2026-11-18 test-stack proof (simulation 2087 landed from the hosted
# workbench: 12 TSVs in .pbg/runs/<id>/ptools, none offered by the viewer).
# ---------------------------------------------------------------------------


def _make_remote_zarr_tar_with_ptools(tmp_path: Path, names: tuple[str, ...]) -> Path:
    """A landed tar whose analysis output carries ``ptools/*.tsv`` exports."""
    staging = tmp_path / "staging_ptools"
    part = staging / "seed_00" / "store.zarr" / "experiment_id=exp-seed00"
    part.mkdir(parents=True)
    (part / ".zgroup").write_text('{"zarr_format":2}')
    pt = staging / "analyses" / "analysis-exp-ab12" / "ptools"
    pt.mkdir(parents=True)
    for name in names:
        (pt / name).write_text("id\tt0\nb0001\t1.0\n")
    tar_path = tmp_path / "sim_with_ptools.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return tar_path


@pytest.mark.plugin
def test_land_into_a_study_puts_ptools_tsvs_where_the_viewer_finds_and_serves_them(tmp_path: Path, sl):
    """The Pathway Tools viewer globs ``<study>/**/ptools/*.tsv`` and its server
    fetches the file over HTTP from the workbench, which refuses any ``.pbg`` path:
    so a study landing must ALSO leave the TSVs inside the study directory."""
    from vivarium_workbench.lib.static_serving import is_servable

    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "s"
    study.mkdir(parents=True)
    names = ("ptools_rna__variant=0_seed=0.tsv", "ptools_proteins__variant=0_seed=0.tsv")
    tar = _make_remote_zarr_tar_with_ptools(tmp_path, names)

    run_id = sl.land_remote_run(
        study, spec_id="s", simulation_id=2087, experiment_id="sim-2087-s", commit="c",
        tar_path=tar, ws_root=ws_root,
    )

    # What pbg-ptools globs (its on-disk contract): found under the study dir.
    found = sorted(study.glob("**/ptools/*.tsv"))
    assert [p.name for p in found] == sorted(names)
    # ... under the landed run's own folder, so two landed runs never collide.
    assert all(p.parent == study / "sim-2087-s" / "ptools" for p in found)
    # ... at a path the workbench will actually serve to the Pathway Tools server.
    assert all(is_servable(p.relative_to(ws_root).as_posix()) for p in found)
    # The run-folder copy stays (the Analyses/Runs artifacts still read it) but it is NOT servable:
    # this is why the study copy is needed, not a convenience.
    kept = ws_root / ".pbg" / "runs" / run_id / "ptools" / names[0]
    assert kept.is_file()
    assert not is_servable(kept.relative_to(ws_root).as_posix())


@pytest.mark.plugin
def test_land_into_a_study_without_ptools_exports_creates_no_ptools_dir(tmp_path: Path, sl):
    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "s"
    study.mkdir(parents=True)
    tar = _make_remote_zarr_tar(tmp_path)

    sl.land_remote_run(
        study, spec_id="s", simulation_id=2088, experiment_id="sim-2088-s", commit="c",
        tar_path=tar, ws_root=ws_root,
    )

    assert not list(study.glob("**/ptools"))


@pytest.mark.plugin
def test_land_without_ws_root_does_not_land_ptools_anywhere(tmp_path: Path, sl):
    """ws_root stays the switch for artifact landing: callers that omit it are unaffected."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_with_ptools(tmp_path, ("ptools_rna__variant=0_seed=0.tsv",))

    sl.land_remote_run(
        study, spec_id="s", simulation_id=2089, experiment_id="sim-2089-s", commit="c", tar_path=tar,
    )

    assert not list(study.glob("**/ptools"))


# ---------------------------------------------------------------------------
# Golden pins (step P2, Runs c1). ``land_remote_run`` moved verbatim from core
# ``remote_run_landing`` into the plugin's ``sms_landing``; these pin what it writes
# -- the PTools layout pbg-ptools globs (``<study>/**/ptools/*.tsv``), the bytes,
# ``analyses.json`` and the run id -- against values computed on the commit BEFORE
# the move (main @ 4a275cbe) and pasted in. A change here is a PTools-contract
# change or a run-id change (re-landing would make a duplicate row): never re-paste
# without saying why.
# ---------------------------------------------------------------------------

_GOLDEN_NOW = 1_700_000_000.0


def _make_golden_tar(tmp_path: Path) -> Path:
    """seed_00 zarr store + two analyses whose PTools TSVs sit in nested dirs + an analysis.json."""
    staging = tmp_path / "staging_golden"
    part = staging / "seed_00" / "store.zarr" / "experiment_id=exp-gold"
    part.mkdir(parents=True)
    (part / ".zgroup").write_text('{"zarr_format":2}')
    a = staging / "analyses" / "analysis-gold-a"
    (a / "ptools").mkdir(parents=True)
    (a / "ptools" / "ptools_rna__all.tsv").write_text("id\tt0\tt1\nb0001\t1.0\t2.5\nb0002\t0.0\t3.0\n")
    (a / "analysis.json").write_text(json.dumps({
        "multivariant": {"ptools_rna": {"all": {"n": 2}}, "ptools_proteins": {"all": {"error": "boom"}}},
        "status": "OK",
        "summary": {"multivariant": {"ptools_rna": "ok", "ptools_proteins": "error"}},
        "errors": [],
    }, sort_keys=True))
    nested = staging / "analyses" / "analysis-gold-b" / "out" / "ptools"
    nested.mkdir(parents=True)
    (nested / "ptools_proteins__all.tsv").write_text("id\tt0\nP0001\t7\n")
    tar_path = tmp_path / "golden.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return tar_path


def _golden_env(monkeypatch) -> None:
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.backends import records

    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", "golden-deploy")
    monkeypatch.delenv("VIVARIUM_DASHBOARD_REMOTE_DEPLOYMENT", raising=False)
    monkeypatch.setattr(cr.time, "time", lambda: _GOLDEN_NOW)
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: None)  # no network


def _sha_tree(root: Path, base: Path) -> list[tuple[str, str]]:
    import hashlib

    return sorted((p.relative_to(base).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                  for p in root.glob("**/ptools/*.tsv"))


def _golden_land_via_tail(tmp_path: Path, monkeypatch, experiment_id: str) -> tuple[str, Path]:
    """End to end through ``sms_legacy.land_tail`` (the route's SMS half) with a fake client."""
    from vivarium_workbench_sms import sms_legacy

    _golden_env(monkeypatch)
    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "gold"
    study.mkdir(parents=True)
    (study / "study.yaml").write_text("baseline: [{composite: gold-spec}]\n")
    tar = _make_golden_tar(tmp_path)

    class _Client:
        base_url = "http://golden.test"

        def __init__(self, *a, **k):
            pass

        def get_simulation(self, sid):
            return {"simulator_id": 3, "num_seeds": 1, "config": {"k": 1}}

        def simulator_commit(self, simulator_id):
            return "abc1234"

        def download_data(self, sid, dest, timeout=None):
            import shutil as _sh

            out = Path(dest) / "d.tar.gz"
            _sh.copy(tar, out)
            return out

    monkeypatch.setattr(sms_legacy, "SmsApiClient", _Client)
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: True)
    monkeypatch.setattr(sms_legacy, "load_spec", lambda p: {"baseline": [{"composite": "gold-spec"}]})
    body, code = sms_legacy.land_tail(ws_root, {"study": "gold", "simulation_id": 4242,
                                                "experiment_id": experiment_id})
    assert code == 200, body
    return body["run_id"], ws_root


_GOLDEN_RUN_ID = "gold-spec__1700000000__123cb1"
_GOLDEN_STUDY_TSVS = [
    ("sim-4242-gold/ptools/ptools_proteins__all.tsv", "73acd0c071d6b9785f57c67ee4ca667605f217e7a270e948c21a954a17f0c79c"),
    ("sim-4242-gold/ptools/ptools_rna__all.tsv", "8a0fdf3f5f87e81579ee719c84bff50d8dc2be50b608b3deb689dfd85521672a"),
]
_GOLDEN_RUN_TSVS = [
    ("ptools/ptools_proteins__all.tsv", "73acd0c071d6b9785f57c67ee4ca667605f217e7a270e948c21a954a17f0c79c"),
    ("ptools/ptools_rna__all.tsv", "8a0fdf3f5f87e81579ee719c84bff50d8dc2be50b608b3deb689dfd85521672a"),
]
_GOLDEN_ANALYSES_SHA = "59cefd7c7606ffa6e379372889651fc19d60cfe109a3fb442f2d6ff281138df6"


@pytest.mark.plugin
def test_golden_sms_landing_keeps_the_ptools_layout_bytes_and_run_id(tmp_path: Path, monkeypatch):
    import hashlib

    run_id, ws_root = _golden_land_via_tail(tmp_path, monkeypatch, "sim-4242-gold")
    study = ws_root / "studies" / "gold"
    run_dir = ws_root / ".pbg" / "runs" / run_id
    assert run_id == _GOLDEN_RUN_ID
    assert _sha_tree(study, study) == _GOLDEN_STUDY_TSVS
    assert _sha_tree(run_dir, run_dir) == _GOLDEN_RUN_TSVS
    assert hashlib.sha256((run_dir / "analyses.json").read_bytes()).hexdigest() == _GOLDEN_ANALYSES_SHA


@pytest.mark.plugin
def test_golden_sms_landing_with_an_unplain_experiment_id_makes_no_study_copy(tmp_path: Path, monkeypatch):
    run_id, ws_root = _golden_land_via_tail(tmp_path, monkeypatch, "../escape")
    study = ws_root / "studies" / "gold"
    run_dir = ws_root / ".pbg" / "runs" / run_id
    assert _sha_tree(study, study) == []
    assert not list(ws_root.glob("**/escape"))
    assert _sha_tree(run_dir, run_dir) == _GOLDEN_RUN_TSVS


_GOLDEN_DIRECT_RUN_ID = "gold-spec__1700000000__a1e41e"


@pytest.mark.plugin
def test_golden_land_remote_run_id_is_stable_for_fixed_provenance(tmp_path: Path, monkeypatch, sl):
    """The run id hashes the provenance VALUES (``cr.generate_run_id``: sorted-keys JSON + the landing second).
    Any edit to a provenance value -- ``backend``, ``source``, a renamed key -- changes it."""
    _golden_env(monkeypatch)
    study = tmp_path / "study"
    run_id = sl.land_remote_run(
        study, spec_id="gold-spec", simulation_id=77, experiment_id="exp-77", commit="c0ffee",
        tar_path=_make_golden_tar(tmp_path), image="img", merged_config_hash="h", s3_uri="s3://b/k",
    )
    assert run_id == _GOLDEN_DIRECT_RUN_ID


# ---------------------------------------------------------------------------
# One binding per moved name (step P2, Runs c1). The SMS landing lives only in
# ``vivarium_workbench_sms.sms_landing``; a stale ``monkeypatch.setattr`` on the
# core module, ``sms_legacy`` or ``remote_run_views`` then raises instead of
# silently patching a dead copy.
# ---------------------------------------------------------------------------

_MOVED_TO_SMS_LANDING = (
    "land_remote_run", "land_remote_simulation_artifacts", "RemoteRunSeedCountMismatch",
    "_detect_and_locate_all", "_merge_zarr_tree", "_count_parquet_rows",
)


def test_core_landing_binds_no_sms_seam():
    from vivarium_workbench.lib import remote_run_landing
    from vivarium_workbench.lib import remote_run_views as rrv

    gone = _MOVED_TO_SMS_LANDING + ("tarfile", "tempfile", "_time", "is_plain_name")
    assert [n for n in gone if hasattr(remote_run_landing, n)] == []
    assert not hasattr(rrv, "land_remote_run")
    for kept in ("land_composite_results", "fold_analyses", "land_ptools_tsvs"):
        assert hasattr(remote_run_landing, kept)


@pytest.mark.plugin
def test_the_sms_landing_holds_the_moved_names_and_not_the_cores():
    """The plugin's half of the check above."""
    from vivarium_workbench_sms import sms_landing, sms_legacy

    assert [n for n in _MOVED_TO_SMS_LANDING if not hasattr(sms_landing, n)] == []
    assert not hasattr(sms_legacy, "land_remote_run")
    for kept in ("land_composite_results", "fold_analyses", "land_ptools_tsvs"):
        assert not hasattr(sms_landing, kept)


@pytest.mark.plugin
def test_plugin_reads_core_landing_through_the_module():
    """No plugin module binds a core landing helper at import time (``from ...remote_run_landing import x`` at
    module level), and none binds an ``sms_landing`` name at all: each is read as ``<module>.<name>`` at call
    time, so a patch on the owning module steers every caller. Function-local from-imports run at call time, so
    they are allowed for the core module."""
    import ast

    import vivarium_workbench_sms

    bad = []
    for path in sorted(Path(vivarium_workbench_sms.__path__[0]).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            top.update(id(n) for n in ast.walk(node)
                       if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == "vivarium_workbench.lib.remote_run_landing" and id(node) in top:
                bad.append(f"{path.name}:{node.lineno} module-level from-import of remote_run_landing")
            if (node.module, node.level) in (("vivarium_workbench_sms.sms_landing", 0), ("sms_landing", 1)):
                bad.append(f"{path.name}:{node.lineno} binds an sms_landing name")
    assert bad == []
