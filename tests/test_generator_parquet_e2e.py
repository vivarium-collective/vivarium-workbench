"""End-to-end: a GENERATOR's declared ParquetEmitter actually writes parquet.

This closes the gap that let the generator emitter bug ship. The existing
coverage tested the two halves separately — ``_select_emitter_name`` in
isolation, and ``run_with_emitter`` with a *static dict* spec — so nothing
exercised the seam where they meet:

    _select_emitter_name  reads the REGISTRY ENTRY's declared emitters -> "parquet"
    run_with_emitter      was handed `spec`, which is None for a generator
    install_default_emitters(state, None, ...) -> no-op, installs nothing

The run then wrote neither parquet NOR the ``.zarr`` it previously wrote (it no
longer took the xarray branch), while still reporting ``output_kind="parquet"``.
Silent data loss on the main Composite Explorer workflow.

This test drives the real path and asserts on FILES ON DISK rather than on the
provenance dict — the provenance said "parquet" the whole time it was broken,
which is precisely why the bug was invisible.

Uses a synthetic ``GeneratorEntry`` rather than v2ecoli's baseline so it runs in
CI and in a clean checkout (v2ecoli is a workspace repo, not a dependency).
Needs the parquet reader/writer stack, which lives in the optional ``test``
extra plus ``pbg-emitters[parquet]`` — skipped, not failed, when absent.
"""
from pathlib import Path

import pytest

from vivarium_workbench.lib import emitters, run_runner

SPEC_ID = "fake.composites.declares_parquet"


def _parquet_emitter_cls():
    """The ParquetEmitter class, or skip if its optional extra isn't installed."""
    try:
        from pbg_emitters.parquet_emitter import ParquetEmitter
    except ImportError:  # process-bigraph < 1.4.17 kept it here
        try:
            from process_bigraph.emitter import ParquetEmitter
        except ImportError:
            pytest.skip("pbg-emitters[parquet] not installed")
    return ParquetEmitter


def _counter_core():
    from bigraph_schema import allocate_core
    from process_bigraph.composite import Process

    class Counter(Process):
        config_schema: dict = {}

        def inputs(self):
            return {"value": "float"}

        def outputs(self):
            return {"value": "float"}

        def update(self, state, interval):
            return {"value": 1.0}

    core = allocate_core()
    core.register_link("Counter", Counter)
    core.register_link("ParquetEmitter", _parquet_emitter_cls())
    return core


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


@pytest.fixture
def declared_parquet_generator():
    """Register a synthetic generator that DECLARES a ParquetEmitter.

    Mirrors the shape of v2ecoli's `baseline` (the composite this branch's
    emitter work targets) without depending on that workspace being checked out.

    ``_REGISTRY`` is a *view* over process-bigraph's global composite-spec
    registry: assignment converts the GeneratorEntry into a CompositeSpec and
    registers it process-wide, and there is no ``__delitem__``. So snapshot the
    backing registry and restore it afterwards rather than deleting the key —
    otherwise this fixture leaks a fake generator into every later test.
    """
    cg = pytest.importorskip("pbg_superpowers.composite_generator")
    from process_bigraph import composite_spec as cs

    before = dict(cs.all_specs())
    entry = cg.GeneratorEntry(
        id=SPEC_ID, name="declares_parquet", description="synthetic fixture",
        parameters={}, func=lambda **kw: _doc(), module="fake.composites",
        default_n_steps=4, visualizations=[], core_extensions=[],
        emitters=[{"address": "local:ParquetEmitter", "emit": "all"}],
    )
    cg._REGISTRY[SPEC_ID] = entry
    try:
        yield entry
    finally:
        cs.clear_registry()
        for spec in before.values():
            cs.register(spec)


def _parquet_files(store: Path) -> list[Path]:
    return list(store.rglob("*.pq")) + list(store.rglob("*.parquet"))


def test_generator_declared_parquet_writes_files(tmp_path, declared_parquet_generator):
    """The whole point: real parquet files land on disk for a generator run."""
    pytest.importorskip("polars")
    pytest.importorskip("pyarrow")
    core = _counter_core()

    # Exactly what run_runner.execute does for a generator: spec is None.
    spec = None
    name = run_runner._select_emitter_name(
        spec=spec, spec_id=SPEC_ID, db_file=str(tmp_path / "x.db"))
    assert name == "parquet", "declared emitter should route to parquet"

    # Identity would be wrong to assert on: _REGISTRY round-trips the entry
    # through a CompositeSpec, so reads return an equal-but-distinct object.
    # What matters is that a declaration arrives at all (it used to be None).
    decl = run_runner._emitter_decl_source(spec, SPEC_ID)
    assert decl is not None, \
        "the generator entry must reach the parquet branch, not None"
    assert decl.emitters == declared_parquet_generator.emitters

    prov = emitters.run_with_emitter(
        name, state=_doc(), run_id="gen-run", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=core, steps=4,
        db_file=str(tmp_path / "runs.db"), spec=decl, also_sqlite_history=True)

    store = Path(prov["store_path"])
    assert store.exists(), (
        "parquet store was never created — install_default_emitters no-op'd. "
        "Note prov['output_kind'] still says 'parquet', which is what made this "
        "silent."
    )
    assert _parquet_files(store), f"no parquet files written under {store}"


def test_generator_run_is_not_silently_empty(tmp_path, declared_parquet_generator):
    """Guards the exact failure signature: provenance claims parquet while the
    store is empty. Asserting on prov alone would have passed all along."""
    pytest.importorskip("polars")
    pytest.importorskip("pyarrow")
    core = _counter_core()

    decl = run_runner._emitter_decl_source(None, SPEC_ID)
    prov = emitters.run_with_emitter(
        "parquet", state=_doc(), run_id="gen-run-2", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=core, steps=4,
        db_file=str(tmp_path / "runs.db"), spec=decl, also_sqlite_history=True)

    assert prov["output_kind"] == "parquet"
    # ...and unlike before the fix, that claim is backed by actual data.
    assert _parquet_files(Path(prov["store_path"]))


def test_static_spec_parquet_still_writes_files(tmp_path):
    """The static-spec path must be unaffected by the generator threading."""
    pytest.importorskip("polars")
    pytest.importorskip("pyarrow")
    core = _counter_core()
    spec = {"emitters": [{"address": "local:ParquetEmitter", "emit": "all"}]}

    prov = emitters.run_with_emitter(
        "parquet", state=_doc(), run_id="static-run", emit_paths=["counter_store"],
        out_dir=str(tmp_path), core=core, steps=4,
        db_file=str(tmp_path / "runs.db"), spec=spec)

    assert _parquet_files(Path(prov["store_path"]))
