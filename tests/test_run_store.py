"""Tests for the canonical run-store path/kind resolution (lib/run_store.py)."""
from pathlib import Path

from vivarium_workbench.lib import run_store


def test_zarr_store_path_convention():
    p = run_store.zarr_store_path("/ws/studies/demo", "abc123")
    assert p == Path("/ws/studies/demo/runs.abc123.zarr")


def test_zarr_store_path_for_db_matches_convention():
    db = Path("/ws/studies/demo/runs.db")
    from_db = run_store.zarr_store_path_for_db(db, "abc123")
    from_dir = run_store.zarr_store_path(db.parent, "abc123")
    # The two derivations must agree for the conventional runs.db.
    assert from_db == from_dir == Path("/ws/studies/demo/runs.abc123.zarr")


def test_iter_zarr_stores_finds_only_matching_dirs(tmp_path):
    (tmp_path / "runs.aaa.zarr").mkdir()
    (tmp_path / "runs.bbb.zarr").mkdir()
    (tmp_path / "runs.ccc.zarr").write_text("not a dir")  # a file, must be skipped
    (tmp_path / "other.zarr").mkdir()                     # wrong prefix, must be skipped
    (tmp_path / "runs.db").write_text("db")               # not a zarr store
    found = {p.name for p in run_store.iter_zarr_stores(tmp_path)}
    assert found == {"runs.aaa.zarr", "runs.bbb.zarr"}


def test_iter_zarr_stores_missing_dir_is_empty():
    assert run_store.iter_zarr_stores("/no/such/study") == []


def test_detect_kind():
    assert run_store.detect_kind("/ws/studies/demo/runs.abc.zarr") == "zarr"
    assert run_store.detect_kind("/ws/studies/demo/parquet-runs/x/history") == "parquet"
    assert run_store.detect_kind("/ws/studies/demo/runs.db") == "sqlite"
    assert run_store.detect_kind(None) is None
    assert run_store.detect_kind("") is None
    assert run_store.detect_kind("s3://bucket/some/prefix") is None


def test_detect_kind_matches_simulations_index_labels():
    # simulations_index maps the store kind to its display "emitter" value; the
    # zarr -> xarray / parquet -> parquet / else -> None mapping must hold.
    label = {"zarr": "xarray", "parquet": "parquet"}
    assert label.get(run_store.detect_kind("x/runs.1.zarr")) == "xarray"
    assert label.get(run_store.detect_kind("x/parquet-runs/y")) == "parquet"
    assert label.get(run_store.detect_kind("x/runs.db")) is None
