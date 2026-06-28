"""Unit tests for the parquet reader path in lib.study_charts.

Builds a synthetic hive-partitioned parquet dataset matching the layout
v2ecoli's ParquetEmitter writes (see v2ecoli PR #80):

    <study>/parquet-runs/<experiment_id>/history/experiment_id=<e>/variant=0/
        lineage_seed=0/generation=0/agent_id=0/000000.pq

with flattened column names like ``listeners__mass__cell_mass`` and a
``global_time`` column.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# This whole module exercises the parquet reader path, which needs polars
# (an optional dep). Skip the module cleanly when it isn't installed.
pl = pytest.importorskip("polars")

from vivarium_dashboard.lib.study_charts import (
    _emitter_choice,
    _extract_paths_from_parquet,
    _latest_parquet_for_study,
)


def _write_hive(study_dir: Path, experiment_id: str = "exp001") -> Path:
    """Create a single-shard hive parquet tree and return the hive root."""
    leaf = (
        study_dir
        / "parquet-runs"
        / experiment_id
        / "history"
        / f"experiment_id={experiment_id}"
        / "variant=0"
        / "lineage_seed=0"
        / "generation=0"
        / "agent_id=0"
    )
    leaf.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(
        {
            "global_time": [0.0, 1.0, 2.0, 3.0, 4.0],
            "listeners__mass__cell_mass": [1.0, 1.1, 1.2, 1.3, 1.4],
            "listeners__monomer_counts": [
                [10, 20, 30],
                [11, 21, 31],
                [12, 22, 32],
                [13, 23, 33],
                [14, 24, 34],
            ],
        }
    )
    df.write_parquet(leaf / "000000.pq")
    return study_dir / "parquet-runs" / experiment_id / "history"


def test_latest_parquet_for_study_picks_history_dir(tmp_path: Path):
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    hive = _write_hive(study_dir, "expA")
    found = _latest_parquet_for_study(study_dir)
    assert found is not None
    assert found == hive


def test_latest_parquet_for_study_returns_none_when_missing(tmp_path: Path):
    study_dir = tmp_path / "studies" / "empty"
    study_dir.mkdir(parents=True)
    assert _latest_parquet_for_study(study_dir) is None


def test_latest_parquet_for_study_picks_most_recent(tmp_path: Path):
    import os
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    old = _write_hive(study_dir, "expOld")
    new = _write_hive(study_dir, "expNew")
    # Force expNew's parent (the <experiment_id> dir) to be more recently mtime'd
    os.utime(old.parent, (1_700_000_000, 1_700_000_000))
    os.utime(new.parent, (1_800_000_000, 1_800_000_000))
    found = _latest_parquet_for_study(study_dir)
    assert found == new


def test_extract_paths_from_parquet_scalar_column(tmp_path: Path):
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    hive = _write_hive(study_dir)
    specs = [("listeners.mass.cell_mass", None)]
    out = _extract_paths_from_parquet(hive, specs)
    assert ("listeners.mass.cell_mass", None) in out
    xs, ys = out[("listeners.mass.cell_mass", None)]
    assert xs == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert ys == pytest.approx([1.0, 1.1, 1.2, 1.3, 1.4])


def test_extract_paths_from_parquet_array_column_index(tmp_path: Path):
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    hive = _write_hive(study_dir)
    # Index 1 of [10,20,30] family is 20, 21, 22, 23, 24 (DuckDB 1-indexed via idx+1)
    specs = [("listeners.monomer_counts", 1)]
    out = _extract_paths_from_parquet(hive, specs)
    xs, ys = out[("listeners.monomer_counts", 1)]
    assert xs == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert ys == pytest.approx([20.0, 21.0, 22.0, 23.0, 24.0])


def test_extract_paths_from_parquet_missing_column_empty(tmp_path: Path):
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    hive = _write_hive(study_dir)
    specs = [("listeners.does.not.exist", None)]
    out = _extract_paths_from_parquet(hive, specs)
    assert out[("listeners.does.not.exist", None)] == ([], [])


def test_extract_paths_from_parquet_subsamples_long_runs(tmp_path: Path):
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    leaf = (
        study_dir
        / "parquet-runs"
        / "expBig"
        / "history"
        / "experiment_id=expBig"
        / "variant=0"
        / "lineage_seed=0"
        / "generation=0"
        / "agent_id=0"
    )
    leaf.mkdir(parents=True)
    n = 5000
    pl.DataFrame(
        {
            "global_time": [float(i) for i in range(n)],
            "listeners__mass__cell_mass": [float(i) for i in range(n)],
        }
    ).write_parquet(leaf / "000000.pq")
    hive = study_dir / "parquet-runs" / "expBig" / "history"
    out = _extract_paths_from_parquet(hive, [("listeners.mass.cell_mass", None)])
    xs, ys = out[("listeners.mass.cell_mass", None)]
    # Subsampling caps at ~max_points (default 200). Allow some slack.
    assert 100 <= len(xs) <= 300, f"expected ~200 points, got {len(xs)}"
    assert xs[0] == 0.0
    # Monotonic increasing time
    assert all(xs[i] < xs[i + 1] for i in range(len(xs) - 1))


def test_emitter_choice_accepts_parquet_from_spec():
    spec = {"runtime": {"default_emitter": "parquet"}}
    assert _emitter_choice(spec, None) == "parquet"


def test_emitter_choice_accepts_parquet_from_workspace(tmp_path: Path):
    ws = tmp_path / "ws"
    studies = ws / "studies" / "demo"
    studies.mkdir(parents=True)
    runs_db = studies / "runs.db"
    runs_db.touch()
    (ws / "workspace.yaml").write_text("runtime:\n  default_emitter: parquet\n")
    assert _emitter_choice({}, runs_db) == "parquet"


def test_emitter_choice_finds_workspace_yaml_in_nested_layout(tmp_path: Path):
    # v2ecoli-style nested layout: <ws>/workspace/studies/<slug>/runs.db, with
    # the workspace.yaml at the repo root <ws>/. The old fixed
    # `.parent.parent.parent` math looked at <ws>/workspace/workspace.yaml
    # (which doesn't exist) and fell back to sqlite, silently ignoring the
    # declared xarray emitter — so zarr runs (incl. landed remote runs) never
    # rendered. Resolution must walk up to the nearest ancestor workspace.yaml.
    ws = tmp_path / "ws"
    studies = ws / "workspace" / "studies" / "demo"
    studies.mkdir(parents=True)
    runs_db = studies / "runs.db"
    runs_db.touch()
    (ws / "workspace.yaml").write_text("runtime:\n  default_emitter: xarray\n")
    assert _emitter_choice({}, runs_db) == "xarray"


def test_emitter_choice_default_still_sqlite():
    assert _emitter_choice({}, None) == "sqlite"
