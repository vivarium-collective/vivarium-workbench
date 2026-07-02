"""Behavioral tests for the uniform write path ``emitters.run_with_emitter``.

Every emitter is injected as a process-bigraph Step and the Composite drives it
via ``run(N)``. These tests use a tiny in-process composite (a Counter writing
one scalar store) and assert each output_kind's store is produced and reads back
through the SAME broker the read side uses (``read_source`` / ``reader_for``).
"""
import pytest

from bigraph_schema import allocate_core
from process_bigraph.composite import Process

from vivarium_workbench.lib import emitters


class Counter(Process):
    """Minimal process: increments a scalar store by 1 each tick."""

    config_schema = {}

    def inputs(self):
        return {"value": "float"}

    def outputs(self):
        return {"value": "float"}

    def update(self, state, interval):
        return {"value": 1.0}


def _doc():
    return {
        "counter": {
            "_type": "process",
            "address": "local:Counter",
            "config": {},
            "inputs": {"value": ["counter_store", "value"]},
            "outputs": {"value": ["counter_store", "value"]},
            "interval": 1.0,
        },
        "counter_store": {"value": 0.0},
    }


def _core():
    core = allocate_core()
    core.register_link("Counter", Counter)
    return core


# ---------------------------------------------------------------------------
# sqlite — the default; history reads back via pbg_emitters.load_history
# ---------------------------------------------------------------------------

def test_run_with_emitter_sqlite_writes_history(tmp_path):
    import pbg_emitters

    db_file = str(tmp_path / "runs.db")
    steps = 5
    seen = []
    prov = emitters.run_with_emitter(
        "sqlite", state=_doc(), run_id="r-sqlite", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=_core(), steps=steps, db_file=db_file,
        progress_cb=seen.append)

    assert prov["output_kind"] == "sqlite"
    assert prov["store_path"] == db_file
    assert prov["steps"] == steps
    # progress_cb saw exactly [1..steps].
    assert seen == list(range(1, steps + 1))

    rows = pbg_emitters.load_history(db_file, "r-sqlite")
    assert len(rows) >= steps


def test_run_with_emitter_default_name_is_xarray(tmp_path):
    """The framework DEFAULT is xarray as of Task 6 — a default run with a
    non-empty selection writes a zarr store that actually CONTAINS data.

    Asserting only ``output_kind == "zarr"`` is too weak: the broker returns
    that even for an EMPTY store. This asserts the scalar series round-trips
    (non-empty, advancing), so an empty-store regression is caught.
    """
    pytest.importorskip("xarray")
    pytest.importorskip("zarr")
    db_file = str(tmp_path / "runs.db")
    prov = emitters.run_with_emitter(
        emitters.DEFAULT_EMITTER, state=_doc(), run_id="r-def",
        emit_paths=["counter_store"], out_dir=str(tmp_path), core=_core(),
        steps=6, db_file=db_file)
    assert prov["output_kind"] == "zarr"

    store = prov["store_path"]
    from pathlib import Path as _Path
    assert _Path(store).exists()

    # The store must hold real data — read the counter leaf array directly.
    import xarray as xr
    dt = xr.open_datatree(str(store), engine="zarr")
    series = []
    for group in dt.groups:
        if group.endswith("counter_store/value"):
            ds = dt[group].ds
            for var_name in ds.data_vars:
                series = [float(x) for x in ds[var_name].values.ravel().tolist()]
                break
    assert series, "default xarray run wrote an EMPTY store (no data leaves)"
    assert any(v > 0 for v in series)  # the counter actually advanced


def test_run_with_emitter_short_run_does_not_silently_empty(tmp_path):
    """A run too short to fill the xarray buffer must NOT silently yield an empty
    store. A 1-emit-tick run deterministically under-fills the flat-Step buffer
    (the close-time final flush asserts ``not include_static``) → an empty
    ``.zarr``; the broker's content guard fires and falls back to sqlite with a
    diagnosable warning. (Regression test for Finding 1: that AssertionError used
    to be swallowed, leaving an empty store + empty charts with no user-visible
    error.)"""
    pytest.importorskip("xarray")
    pytest.importorskip("zarr")
    db_file = str(tmp_path / "runs.db")
    prov = emitters.run_with_emitter(
        emitters.DEFAULT_EMITTER, state=_doc(), run_id="r-short",
        emit_paths=["counter_store"], out_dir=str(tmp_path), core=_core(),
        steps=1, db_file=db_file)

    # Guard fired: fell back to a readable sqlite store with a recorded warning.
    assert prov["output_kind"] == "sqlite"
    assert prov["store_path"] == db_file
    assert "warning" in prov and prov["warning"]

    # The fall-back store holds the run's data (so the run is NOT empty).
    import pbg_emitters
    rows = pbg_emitters.load_history(db_file, "r-short")
    series = [r.get("counter_store_value") for r in rows]
    assert len(rows) >= 1
    assert any((v or 0) > 0 for v in series)

    # The empty/partial zarr store was cleaned up (not left to be mis-resolved).
    assert not (tmp_path / "r-short.zarr").exists()


# ---------------------------------------------------------------------------
# xarray — a .zarr store the read-side broker resolves + charts
# ---------------------------------------------------------------------------

def test_run_with_emitter_xarray_writes_zarr(tmp_path):
    pytest.importorskip("xarray")
    pytest.importorskip("zarr")

    prov = emitters.run_with_emitter(
        "xarray", state=_doc(), run_id="r-xarray",
        emit_paths=["counter_store/value"], out_dir=str(tmp_path), core=_core(),
        steps=6, db_file=str(tmp_path / "runs.db"))

    assert prov["output_kind"] == "zarr"
    store = prov["store_path"]
    assert store.endswith(".zarr")

    from pathlib import Path
    assert Path(store).exists()

    # The read-side broker resolves the store as zarr and hands back the zarr
    # trace reader, which runs without error (charts) on the flat-Step store.
    kind, resolved = emitters.read_source(store)
    assert kind == "zarr"
    assert Path(resolved) == Path(store)

    reader = emitters.reader_for("zarr")
    times, values = reader(Path(store), "counter_store/value")
    assert isinstance(times, list) and isinstance(values, list)


def test_run_with_emitter_zarr_fallback_uses_fresh_state(tmp_path):
    """MINOR 3: the empty-STORE fall-back drives a Composite from `state` once
    (xarray) then re-drives a NEW Composite from the same `state` (sqlite). Assert
    (a) the caller's `state` is left pristine for the fresh re-run, and (b) the
    progress heartbeat double-counts (the documented, harmless re-count: the
    re-drive replays 1..steps so the max-runtime guard stays armed)."""
    pytest.importorskip("xarray")
    pytest.importorskip("zarr")
    db_file = str(tmp_path / "runs.db")
    state = _doc()
    seen = []
    # steps=1 deterministically under-fills the flat-Step buffer → empty store →
    # the zarr branch (which already drove `steps` ticks) falls back to sqlite.
    prov = emitters.run_with_emitter(
        "xarray", state=state, run_id="r-fresh", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=_core(), steps=1, db_file=db_file,
        progress_cb=seen.append)

    assert prov["output_kind"] == "sqlite"
    # The caller's state dict is untouched, so the sqlite re-run starts clean.
    assert state["counter_store"]["value"] == 0.0
    # Documented harmless double-count: progress fired for the zarr drive AND the
    # sqlite re-drive (1 tick each).
    assert seen == [1, 1]
    # The fall-back sqlite store holds the run's (clean) data.
    import pbg_emitters
    rows = pbg_emitters.load_history(db_file, "r-fresh")
    assert len(rows) >= 1


def test_run_with_emitter_xarray_empty_paths_falls_back_to_sqlite(tmp_path):
    pytest.importorskip("xarray")

    db_file = str(tmp_path / "runs.db")
    prov = emitters.run_with_emitter(
        "xarray", state=_doc(), run_id="r-fallback", emit_paths=[],
        out_dir=str(tmp_path), core=_core(), steps=3, db_file=db_file)

    # Empty view → auto-fall-back to the default sqlite store.
    assert prov["output_kind"] == "sqlite"
    assert prov["store_path"] == db_file


# ---------------------------------------------------------------------------
# parquet — install_default_emitters convention + flush
# ---------------------------------------------------------------------------

def test_run_with_emitter_parquet_writes_store(tmp_path):
    pytest.importorskip("polars")
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")

    from pathlib import Path

    # A composite declaring a parquet default sink via the emitter convention.
    spec = {
        "emitters": [
            {"address": "local:ParquetEmitter", "emit": "all"},
        ],
    }
    core = _core()
    try:
        from pbg_emitters.parquet_emitter import ParquetEmitter
    except ImportError:  # process-bigraph < 1.4.17 (legacy location)
        from process_bigraph.emitter import ParquetEmitter
    core.register_link("ParquetEmitter", ParquetEmitter)

    prov = emitters.run_with_emitter(
        "parquet", state=_doc(), run_id="r-parquet", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=core, steps=4,
        db_file=str(tmp_path / "runs.db"), spec=spec)

    assert prov["output_kind"] == "parquet"
    assert prov["store_path"] == str(Path(tmp_path) / "parquet")
    assert prov["steps"] == 4
