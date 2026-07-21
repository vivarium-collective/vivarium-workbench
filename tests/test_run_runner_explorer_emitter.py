"""Task 5b — Composite Explorer (run_runner) honors declared Parquet, renders
live sqlite Results, and records the emitter kind.

Three behaviors are exercised:

R1  ``run_runner._select_emitter_name`` resolves the DECLARED emitter for
    BOTH static specs (spec dict) and generators (spec is None → resolve the
    generator entry). The v2ecoli ``baseline`` generator declares a
    ParquetEmitter, so the Explorer must pick ``"parquet"`` (not the workspace
    xarray default). A spec that declares nothing keeps the ``default_emitter``
    fallback.

R2  ``emitters.run_with_emitter(..., also_sqlite_history=True)`` injects the
    RAM ``user_emitter`` + ``sqlite_emitter`` in ADDITION to the parquet sink so
    the Results tab's ``history`` table is populated. The default (flag off)
    parquet path stays byte-identical (no sqlite_emitter).

R3  ``run_runner._record_run_emitter`` appends a JSONL event that folds into the
    run's record with ``emitter`` set — the Sims DB Emitter column source.

These import the v2ecoli baseline generator, so run under the v2ecoli venv:
    /Users/eranagmon/code/v2ecoli/.venv/bin/python -m pytest \
        tests/test_run_runner_explorer_emitter.py -v
"""
from pathlib import Path

import pytest

from bigraph_schema import allocate_core
from process_bigraph.composite import Process

from vivarium_workbench.lib import emitters, run_log, run_runner


# --------------------------------------------------------------------------
# Shared tiny composite (mirrors tests/test_emitters_run_with_emitter.py)
# --------------------------------------------------------------------------
class Counter(Process):
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


# --------------------------------------------------------------------------
# R1 — declared-emitter selection (generators too)
# --------------------------------------------------------------------------
def test_select_emitter_name_generator_baseline_is_parquet(tmp_path):
    """The v2ecoli baseline generator declares a ParquetEmitter → parquet,
    even though spec is None (generator) and the workspace default is xarray."""
    pytest.importorskip("pbg_superpowers")
    db = str(tmp_path / "runs.db")
    name = run_runner._select_emitter_name(
        spec=None, spec_id="v2ecoli.composites.baseline", db_file=db)
    assert name == "parquet"


def test_select_emitter_name_generator_none_declared_uses_default(tmp_path):
    """A spec_id that declares no emitter (unknown / undeclared generator)
    keeps the workspace default_emitter fallback — R1 must not change it."""
    db = str(tmp_path / "runs.db")
    name = run_runner._select_emitter_name(
        spec=None, spec_id="does.not.exist.generator", db_file=db)
    assert name == emitters.default_emitter(None, Path(db))


def test_select_emitter_name_static_spec_unchanged(tmp_path):
    """Static-spec behavior is preserved: a declared ``emitters:`` → parquet,
    an empty spec → default_emitter."""
    db = str(tmp_path / "runs.db")
    declared_spec = {"emitters": [{"address": "local:ParquetEmitter", "emit": "all"}]}
    assert run_runner._select_emitter_name(
        spec=declared_spec, spec_id="ignored", db_file=db) == "parquet"
    assert run_runner._select_emitter_name(
        spec={}, spec_id="ignored", db_file=db) == emitters.default_emitter({}, Path(db))


# --------------------------------------------------------------------------
# R2 — live sqlite Results alongside parquet (gated by also_sqlite_history)
# --------------------------------------------------------------------------
def _parquet_spec():
    return {"emitters": [{"address": "local:ParquetEmitter", "emit": "all"}]}


def _register_parquet(core):
    try:
        from pbg_emitters.parquet_emitter import ParquetEmitter
    except ImportError:  # process-bigraph < 1.4.17 legacy location
        from process_bigraph.emitter import ParquetEmitter
    core.register_link("ParquetEmitter", ParquetEmitter)


def test_run_with_emitter_parquet_also_sqlite_injects_history(tmp_path):
    """Explorer path: parquet sink + also_sqlite_history=True populates the
    sqlite history table and the composite state carries the sqlite_emitter."""
    pytest.importorskip("polars")
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    import pbg_emitters

    core = _core()
    _register_parquet(core)
    db_file = str(tmp_path / "runs.db")

    prov = emitters.run_with_emitter(
        "parquet", state=_doc(), run_id="r-explorer", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=core, steps=4, db_file=db_file,
        spec=_parquet_spec(), also_sqlite_history=True)

    assert prov["output_kind"] == "parquet"
    # The parquet sink still lands where it always did.
    assert prov["store_path"] == str(Path(tmp_path) / "parquet")
    # The composite state carries the injected sqlite + RAM emitters.
    st = prov["composite"].state
    assert "sqlite_emitter" in st
    assert "user_emitter" in st
    # And the history table is actually populated so Results renders live.
    rows = pbg_emitters.load_history(db_file, "r-explorer")
    assert len(rows) >= 4


def test_run_with_emitter_parquet_default_has_no_sqlite(tmp_path):
    """Default (flag off) parquet path is unchanged: no sqlite emitter injected,
    so existing callers (study runs) stay byte-identical."""
    pytest.importorskip("polars")
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")

    core = _core()
    _register_parquet(core)
    db_file = str(tmp_path / "runs.db")

    prov = emitters.run_with_emitter(
        "parquet", state=_doc(), run_id="r-noflag", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=core, steps=4, db_file=db_file,
        spec=_parquet_spec())

    assert prov["output_kind"] == "parquet"
    st = prov["composite"].state
    assert "sqlite_emitter" not in st
    assert "user_emitter" not in st


# --------------------------------------------------------------------------
# R3 — record the resolved emitter kind for the Sims DB
# --------------------------------------------------------------------------
def test_record_run_emitter_folds_into_run(tmp_path):
    run_runner._record_run_emitter(tmp_path, run_id="r-rec", name="parquet")
    folded = run_log.fold_runs_jsonl(tmp_path)
    assert folded["r-rec"]["emitter"] == "parquet"
