import json
import sqlite3
import tarfile
from pathlib import Path

from vivarium_workbench.lib.remote_run_landing import (
    RemoteRunSeedCountMismatch,
    _count_parquet_rows,
    land_ptools_tsvs,
    land_remote_run,
)


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


def test_land_folds_analysis_manifest_into_pbg_runs_analyses_json(tmp_path: Path):
    """Once the analysis job has completed, landing (with ws_root passed) must
    fold its ``analysis.json`` into .pbg/runs/<run_id>/analyses.json -- the exact
    local artifact contract composite_flush.run_flush's own (non-remote)
    analyses dispatch already produces (``written`` = the landed output-file
    paths), so the existing Analyses button renders it with no further changes."""
    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "s"
    study.mkdir(parents=True)
    tar = _make_remote_zarr_tar_with_analysis(tmp_path)

    run_id = land_remote_run(
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


def test_land_without_analysis_manifest_writes_nothing(tmp_path: Path):
    """If the analysis job hasn't completed by the time this run lands, landing
    the simulation output must still succeed -- and must not fabricate an
    analyses.json for output that was never actually produced."""
    ws_root = tmp_path / "workspace"
    study = ws_root / "studies" / "s"
    study.mkdir(parents=True)
    tar = _make_remote_zarr_tar(tmp_path)

    run_id = land_remote_run(
        study, spec_id="s", simulation_id=116, experiment_id="e", commit="c",
        tar_path=tar, ws_root=ws_root,
    )

    assert not (ws_root / ".pbg" / "runs" / run_id / "analyses.json").exists()


def test_land_without_ws_root_skips_analysis_folding(tmp_path: Path):
    """ws_root is optional -- existing callers that never trigger analysis for
    a landed run must be unaffected, even if a manifest happens to be present."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_with_analysis(tmp_path)

    run_id = land_remote_run(
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

def test_land_removes_the_dispatch_time_pending_placeholder(tmp_path: Path):
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
    run_id = land_remote_run(
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


def test_land_without_a_pending_placeholder_is_unaffected(tmp_path: Path):
    """Landing a simulation that was never dispatched through remote_run_submit
    (an older run, or one landed via another path) has no pending row to clean
    up — the delete is a silent no-op, landing succeeds exactly as before."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = land_remote_run(
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
    from vivarium_workbench.lib import study_charts

    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = land_remote_run(
        study, spec_id="s", simulation_id=7, experiment_id="e", commit="c", tar_path=tar
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


def test_land_multiseed_keeps_every_seed(tmp_path: Path):
    """Regression: landing a 2-seed dispatch must keep BOTH seeds' partitions, not
    silently discard every seed but the first (the found bug — land_remote_run's
    seed param defaulted to 0 and no caller ever overrode it)."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0, 1))
    run_id = land_remote_run(
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


def test_land_multiseed_single_seed_still_works(tmp_path: Path):
    """A single-seed dispatch (the common case) still lands correctly under the
    new union-all-seeds logic — no regression for the 1-seed path."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0,))
    run_id = land_remote_run(
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


def test_land_native_lineage_zarr_keeps_every_lineage(tmp_path: Path):
    """Regression guard for the same class of bug #674 fixed once already, recurring
    one level deeper: every lineage in a batch shares experiment_id=X/variant=Y, so a
    naive top-level union would keep only the first-processed lineage_seed and
    silently drop the rest. All requested seeds must land."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_native_lineage_tar(tmp_path, seeds=(0, 1, 2))
    run_id = land_remote_run(
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


def test_land_native_lineage_zarr_single_lineage_still_works(tmp_path: Path):
    """A single-lineage batch (n_seeds=1) still lands correctly — no regression."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_native_lineage_tar(tmp_path, seeds=(0,))
    run_id = land_remote_run(
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

def test_land_records_full_execution_provenance_from_sms_api_response(tmp_path: Path):
    """A landed run's record must carry the image tag/digest, composite doc
    hash, merged config hash, real n_steps, and the SERVER-sourced commit —
    fixture values mirror what remote_run_views._resolve_execution_provenance
    reads from a real sms-api GET /simulations/{id} + simulator-registry
    response, not anything inferred locally."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)

    run_id = land_remote_run(
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


def test_land_manifest_commit_is_not_the_local_workspace_head(tmp_path: Path):
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
    run_id = land_remote_run(
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


def test_land_seed_count_mismatch_raises_and_writes_nothing(tmp_path: Path):
    """A dispatch that requested 3 seeds but only 2 landed (a real, observed
    failure mode -- a seed's own container crashed silently, or the tar was
    fetched before every seed finished writing) must be flagged, not recorded
    as a trustworthy completed run."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0, 1))

    import pytest

    with pytest.raises(RemoteRunSeedCountMismatch):
        land_remote_run(
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


def test_land_seed_count_match_succeeds(tmp_path: Path):
    """The matching case must land normally -- the check is a guard, not a
    universal block."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0, 1))
    run_id = land_remote_run(
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


def test_count_parquet_rows_returns_none_without_fabricating_zero(tmp_path: Path):
    """No history/*.pq files (or pyarrow unavailable) must come back as
    ``None`` -- never a fabricated 0 that a caller could mistake for a real,
    verified zero-step run."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert _count_parquet_rows(empty_dir) is None


def test_land_without_expected_seeds_skips_the_check(tmp_path: Path):
    """expected_seeds is optional -- existing callers that don't know the
    requested count (or predate this fix) must be unaffected."""
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar_multiseed(tmp_path, seeds=(0, 1, 2))
    run_id = land_remote_run(
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
