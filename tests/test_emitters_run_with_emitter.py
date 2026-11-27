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
# sqlite — the default; history reads back via viva_emitters.load_history
# ---------------------------------------------------------------------------

def test_run_with_emitter_sqlite_writes_history(tmp_path):
    import viva_emitters

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

    rows = viva_emitters.load_history(db_file, "r-sqlite")
    assert len(rows) >= steps


def test_run_with_emitter_sqlite_stacks_ram_and_sqlite(tmp_path):
    """Characterization of the sqlite output_kind's emitter STACK (P2-8 /
    #754-class dedup): a declared ``emit_paths`` selection stacks a RAM
    ``user_emitter`` feeding a ``sqlite_emitter`` — the same RAM+SQLite pair the
    parquet-with-history path (``also_sqlite_history=True``) stacks. This must
    hold identically before and after de-duplicating the construction logic in
    ``run_with_emitter``."""
    db_file = str(tmp_path / "runs.db")
    prov = emitters.run_with_emitter(
        "sqlite", state=_doc(), run_id="r-stack", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=_core(), steps=3, db_file=db_file)

    assert prov["output_kind"] == "sqlite"
    st = prov["composite"].state
    assert "user_emitter" in st
    assert "sqlite_emitter" in st
    assert st["sqlite_emitter"]["address"]["data"] == "SQLiteEmitter"
    assert st["user_emitter"]["address"]["data"] == "RAMEmitter"


def test_run_with_emitter_ram_stacks_ram_only(tmp_path):
    """Characterization of the ram output_kind's emitter STACK: a declared
    ``emit_paths`` selection stacks ONLY the RAM ``user_emitter`` — no
    ``sqlite_emitter`` (unlike the ``sqlite`` and parquet-with-history kinds)."""
    prov = emitters.run_with_emitter(
        "ram", state=_doc(), run_id="r-ram", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=_core(), steps=3,
        db_file=str(tmp_path / "runs.db"))

    assert prov["output_kind"] == "ram"
    st = prov["composite"].state
    assert "user_emitter" in st
    assert st["user_emitter"]["address"]["data"] == "RAMEmitter"
    assert "sqlite_emitter" not in st


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
    """A short run must NOT silently yield an empty store. The flat-Step buffer
    (size 3) flushes on close even for a 1-emit-tick run, so a 1-tick run writes
    real ``generation=N`` data and the zarr read-side broker reads it back — the
    short run charts a real, non-empty series (no downgrade to sqlite needed).

    Regression test: the store used to read back EMPTY because the zarr trace
    reader looked for ``time_gen=N`` only on the leaf's IMMEDIATE parent, but the
    emitter nests the store path (``counter_store/value``) so ``time_gen`` sits on
    the ancestor ``lineage_seed=<s>`` partition node. See
    ``comparative_viz._extract_trace_from_zarr``."""
    pytest.importorskip("xarray")
    pytest.importorskip("zarr")
    db_file = str(tmp_path / "runs.db")
    prov = emitters.run_with_emitter(
        emitters.DEFAULT_EMITTER, state=_doc(), run_id="r-short",
        emit_paths=["counter_store"], out_dir=str(tmp_path), core=_core(),
        steps=1, db_file=db_file)

    # The short run produced a readable zarr store (NOT an empty-store fallback).
    assert prov["output_kind"] == "zarr"
    store = prov["store_path"]
    from pathlib import Path as _Path
    assert _Path(store).exists()

    # The read-side broker reads a real, non-empty series back out — the exact
    # path the charts use. An empty read here is the regression this guards.
    kind, resolved = emitters.read_source(store)
    assert kind == "zarr"
    reader = emitters.reader_for("zarr")
    times, values = reader(_Path(resolved), "counter_store/value")
    assert values, "short xarray run charted EMPTY (reader could not extract data)"
    assert len(times) == len(values)
    assert any(v > 0 for v in values)  # the counter actually advanced


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
    # Non-empty: the reader must actually extract the series. A weak list-type-only
    # assertion let a reader regression hide — the store held data but read empty
    # because time_gen lives on the ancestor partition node, not the leaf's parent.
    assert values and len(times) == len(values)
    assert any(v > 0 for v in values)  # the counter advanced


def test_run_with_emitter_zarr_fallback_uses_fresh_state(tmp_path, monkeypatch):
    """MINOR 3: the empty-STORE fall-back drives a Composite from `state` once
    (xarray) then re-drives a NEW Composite from the same `state` (sqlite). Assert
    (a) the caller's `state` is left pristine for the fresh re-run, and (b) the
    progress heartbeat double-counts (the documented, harmless re-count: the
    re-drive replays 1..steps so the max-runtime guard stays armed).

    Short runs now flush real data, so the empty-store branch no longer triggers
    naturally — force it by making the content guard report the store empty. This
    exercises the fall-back's fresh-state re-drive + double-count in isolation,
    independent of whether any real run produces an empty store."""
    pytest.importorskip("xarray")
    pytest.importorskip("zarr")
    monkeypatch.setattr(emitters, "_zarr_store_has_observable_data",
                        lambda store: False)
    db_file = str(tmp_path / "runs.db")
    state = _doc()
    seen = []
    prov = emitters.run_with_emitter(
        "xarray", state=state, run_id="r-fresh", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=_core(), steps=1, db_file=db_file,
        progress_cb=seen.append)

    assert prov["output_kind"] == "sqlite"
    # The empty result is diagnosable (not silent).
    assert prov.get("warning")
    # The caller's state dict is untouched, so the sqlite re-run starts clean.
    assert state["counter_store"]["value"] == 0.0
    # Documented harmless double-count: progress fired for the zarr drive AND the
    # sqlite re-drive (1 tick each).
    assert seen == [1, 1]
    # The fall-back sqlite store holds the run's (clean) data.
    import viva_emitters
    rows = viva_emitters.load_history(db_file, "r-fresh")
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
        from viva_emitters.parquet_emitter import ParquetEmitter
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
