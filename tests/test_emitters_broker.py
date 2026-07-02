"""Unit tests for the emitter broker (``vivarium_workbench.lib.emitters``).

The broker is the SINGLE locus for ``output_kind -> reader / label / chart``
dispatch. These tests pin its contract resolution, the output_kind alias map,
the source-resolution delegation, the reader dispatch table, and the
default-emitter / label ports — proving the centralization preserves the exact
behavior of the code it replaces. Task 6 flipped the framework default emitter
from ``sqlite`` to ``xarray``; the default-resolution assertions below track that.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vivarium_workbench.lib import emitters
from vivarium_workbench.lib import comparative_viz


# ---------------------------------------------------------------------------
# output_kind + contract resolution
# ---------------------------------------------------------------------------

def test_default_emitter_constant_is_xarray():
    """Task 6 flipped the framework default from sqlite to xarray."""
    assert emitters.DEFAULT_EMITTER == "xarray"


def test_output_kind_sqlite():
    assert emitters.output_kind("sqlite") == "sqlite"


def test_output_kind_xarray_aliases_to_zarr():
    assert emitters.output_kind("xarray") == "zarr"


def test_output_kind_parquet():
    assert emitters.output_kind("parquet") == "parquet"


def test_output_kind_ram():
    assert emitters.output_kind("ram") == "ram"


def test_output_kind_unknown_falls_back_to_lowercased_name():
    assert emitters.output_kind("RABBIT") == "rabbit"


def test_resolve_contract_returns_pbg_emitters_contract():
    import pbg_emitters
    assert emitters.resolve_contract("ram") == pbg_emitters.contract_for("ram")
    assert emitters.resolve_contract("ram").output_kind == "ram"


# ---------------------------------------------------------------------------
# read_source — delegates to explorer_data._resolve_run_source
# ---------------------------------------------------------------------------

def test_read_source_detects_sqlite(tmp_path: Path):
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE history (step INTEGER)")
    conn.commit()
    conn.close()
    kind, resolved = emitters.read_source(str(db))
    assert kind == "sqlite"
    assert resolved == db


def test_read_source_detects_zarr(tmp_path: Path):
    store = tmp_path / "run.zarr"
    store.mkdir()
    kind, resolved = emitters.read_source(str(store))
    assert kind == "zarr"
    assert resolved == store


def test_read_source_missing_is_none(tmp_path: Path):
    kind, resolved = emitters.read_source(str(tmp_path / "nope.db"))
    assert kind is None and resolved is None


def test_read_source_matches_explorer_data_directly(tmp_path: Path):
    from vivarium_workbench.lib import explorer_data
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE history (step INTEGER)")
    conn.commit()
    conn.close()
    assert emitters.read_source(str(db)) == explorer_data._resolve_run_source(str(db))


# ---------------------------------------------------------------------------
# reader_for — the SINGLE kind -> trace-reader dispatch table
# ---------------------------------------------------------------------------

def test_reader_for_zarr():
    assert emitters.reader_for("zarr") is comparative_viz._extract_trace_from_zarr


def test_reader_for_sqlite():
    assert emitters.reader_for("sqlite") is comparative_viz._extract_trace


def test_reader_for_unknown_raises():
    with pytest.raises(KeyError):
        emitters.reader_for("parquet")


# ---------------------------------------------------------------------------
# Per-operation reader dispatch tables (FU3 broker-widening): the broker owns
# the kind -> reader SELECTION for list_observables / get_vector /
# get_flux_auto / get_base_fluxes; each table returns the EXISTING
# explorer_data reader (``is`` identity) and ``None`` for an unsupported kind.
# ---------------------------------------------------------------------------

def test_observable_reader_for_dispatch():
    from vivarium_workbench.lib import explorer_data as ed
    assert emitters.observable_reader_for("zarr") is ed._zarr_observables
    assert emitters.observable_reader_for("parquet") is ed._parquet_observables
    assert emitters.observable_reader_for("sqlite") is ed._sqlite_observables


def test_observable_reader_for_unknown_is_none():
    assert emitters.observable_reader_for("rabbit") is None
    assert emitters.observable_reader_for(None) is None


def test_vector_reader_for_dispatch():
    from vivarium_workbench.lib import explorer_data as ed
    assert emitters.vector_reader_for("zarr") is ed._zarr_get_vector
    assert emitters.vector_reader_for("parquet") is ed._parquet_get_vector
    assert emitters.vector_reader_for("sqlite") is ed._sqlite_get_vector


def test_vector_reader_for_unknown_is_none():
    assert emitters.vector_reader_for("rabbit") is None
    assert emitters.vector_reader_for(None) is None


def test_flux_auto_reader_for_dispatch():
    from vivarium_workbench.lib import explorer_data as ed
    assert emitters.flux_auto_reader_for("zarr") is ed._zarr_get_flux_auto
    assert emitters.flux_auto_reader_for("parquet") is ed._parquet_get_flux_auto
    assert emitters.flux_auto_reader_for("sqlite") is ed._sqlite_get_flux_auto


def test_flux_auto_reader_for_unknown_is_none():
    assert emitters.flux_auto_reader_for("rabbit") is None
    assert emitters.flux_auto_reader_for(None) is None


def test_base_flux_reader_for_dispatch():
    from vivarium_workbench.lib import explorer_data as ed
    assert emitters.base_flux_reader_for("zarr") is ed._zarr_get_base_fluxes
    assert emitters.base_flux_reader_for("parquet") is ed._parquet_get_base_fluxes
    assert emitters.base_flux_reader_for("sqlite") is ed._sqlite_get_base_fluxes


def test_base_flux_reader_for_unknown_is_none():
    assert emitters.base_flux_reader_for("rabbit") is None
    assert emitters.base_flux_reader_for(None) is None


# ---------------------------------------------------------------------------
# default_emitter — ports study_charts._emitter_choice; fallback == DEFAULT
# ---------------------------------------------------------------------------

def test_default_emitter_defaults_to_xarray():
    assert emitters.default_emitter({}, None) == "xarray"
    assert emitters.default_emitter({}, None) == emitters.DEFAULT_EMITTER


def test_default_emitter_honors_spec_runtime():
    spec = {"runtime": {"default_emitter": "parquet"}}
    assert emitters.default_emitter(spec, None) == "parquet"


def test_default_emitter_honors_spec_xarray():
    spec = {"runtime": {"default_emitter": "xarray"}}
    assert emitters.default_emitter(spec, None) == "xarray"


def test_default_emitter_reads_workspace_yaml(tmp_path: Path):
    import yaml
    ws = tmp_path
    (ws / "workspace.yaml").write_text(
        yaml.dump({"runtime": {"default_emitter": "parquet"}})
    )
    studies = ws / "studies" / "s1"
    studies.mkdir(parents=True)
    runs_db = studies / "runs.db"
    runs_db.write_text("")
    assert emitters.default_emitter({}, runs_db) == "parquet"


def test_default_emitter_unknown_value_falls_back_to_default():
    """An unrecognized declared emitter falls back to the framework default
    (xarray as of Task 6), not silently to sqlite."""
    spec = {"runtime": {"default_emitter": "rabbit"}}
    assert emitters.default_emitter(spec, None) == "xarray"
    assert emitters.default_emitter(spec, None) == emitters.DEFAULT_EMITTER


def test_default_emitter_is_case_insensitive():
    """Task-5 Minor: a mixed-case ``runtime.default_emitter`` (e.g. ``SQLite``,
    ``XArray``) resolves to its canonical lowercase name rather than falling
    through to the default."""
    assert emitters.default_emitter(
        {"runtime": {"default_emitter": "SQLite"}}, None) == "sqlite"
    assert emitters.default_emitter(
        {"runtime": {"default_emitter": "XArray"}}, None) == "xarray"
    assert emitters.default_emitter(
        {"runtime": {"default_emitter": "PARQUET"}}, None) == "parquet"


def test_default_emitter_sqlite_optout_still_works():
    """The sqlite opt-out must survive the xarray-default flip."""
    assert emitters.default_emitter(
        {"runtime": {"default_emitter": "sqlite"}}, None) == "sqlite"


# ---------------------------------------------------------------------------
# label_for_run — ports simulations_index._emitter_for_row
# ---------------------------------------------------------------------------

def test_label_for_run_parquet_source(tmp_path: Path):
    assert emitters.label_for_run({"source": "parquet"}, tmp_path) == "parquet"


def test_label_for_run_xarray_source(tmp_path: Path):
    assert emitters.label_for_run({"source": "xarray"}, tmp_path) == "xarray"


def test_label_for_run_explicit_emitter_tag(tmp_path: Path):
    assert emitters.label_for_run({"emitter": "xarray"}, tmp_path) == "xarray"


def test_label_for_run_defaults_to_sqlite(tmp_path: Path):
    assert emitters.label_for_run({"source": "runs_meta"}, tmp_path) == "sqlite"


def test_label_for_run_matches_old_emitter_for_row(tmp_path: Path):
    from vivarium_workbench.lib import simulations_index
    rows = [
        {"source": "parquet"},
        {"source": "xarray"},
        {"emitter": "parquet"},
        {"source": "study_yaml", "emitter": {"kind": "parquet"}},
        {"source": "study_yaml"},
        {"source": "runs_meta", "run_id": "r1"},
    ]
    for row in rows:
        assert emitters.label_for_run(row, tmp_path) == \
            simulations_index._emitter_for_row(tmp_path, row)


def test_normalize_emitter_name():
    assert emitters.normalize_emitter_name("  PARQUET ") == "parquet"
    assert emitters.normalize_emitter_name(None) == ""
