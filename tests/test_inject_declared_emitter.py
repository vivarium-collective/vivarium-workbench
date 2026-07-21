import v2ecoli.composites.baseline  # noqa: F401  # self-registers the generator

from vivarium_workbench.lib import composite_runs


def test_baseline_declares_parquet(tmp_path):
    state, kind = composite_runs.inject_declared_emitter(
        {}, spec_id="v2ecoli.composites.baseline",
        run_id="v2ecoli.composites.baseline__1__aa",
        out_dir=str(tmp_path / "parquet-runs"))
    assert kind == "parquet"
    node = state["declared_emitter"]
    assert node["_type"] == "step"
    assert node["address"].lower().endswith("parquetemitter")
    assert node["config"]["out_dir"].endswith("parquet-runs")
    # emit-all schema derived from declared paths
    assert set(node["config"]["emit"]) >= {"global_time", "bulk", "listeners"}


def test_no_declared_emitter_is_noop():
    state, kind = composite_runs.inject_declared_emitter(
        {"x": 1}, spec_id="nonexistent.spec", run_id="r", out_dir="/tmp/p")
    assert kind is None
    assert state == {"x": 1}


def test_empty_registry_triggers_discover_generators(monkeypatch, tmp_path):
    """Regression test for the missing-guard finding: inject_declared_emitter
    must call ``discover_generators()`` when ``_REGISTRY`` starts out empty,
    mirroring every other ``_REGISTRY.get(spec_id)`` call site in this
    codebase (see run_runner.py, composite_flush.py, study_run_state.py).
    Fakes the whole ``pbg_superpowers.composite_generator`` module (same
    technique as test_resolve_composite_source_or_generate.py) so this stays
    independent of v2ecoli's real registry state / import side effects.
    """
    import sys
    import types

    calls = []
    fake_registry: dict = {}
    fake_entry = types.SimpleNamespace(
        name="baseline",
        emitters=[{"address": "local:vivarium_workbench.lib.emitters.ParquetEmitter"}],
    )

    def spy_discover(*args, **kwargs):
        calls.append(True)
        fake_registry["v2ecoli.composites.baseline"] = fake_entry
        return dict(fake_registry)

    def fake_install_default_emitters(state, source, *, run_id=None,
                                       out_dir=None, core=None):
        assert source is fake_entry, "guard must call discover_generators() before .get()"
        new_state = dict(state)
        new_state["emitter"] = {
            "_type": "step",
            "address": "local:vivarium_workbench.lib.emitters.ParquetEmitter",
            "config": {"out_dir": str(out_dir)},
        }
        return new_state

    fake_mod = types.SimpleNamespace(
        _REGISTRY=fake_registry,
        discover_generators=spy_discover,
        install_default_emitters=fake_install_default_emitters,
    )
    monkeypatch.setitem(sys.modules, "pbg_superpowers", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pbg_superpowers.composite_generator", fake_mod)

    state, kind = composite_runs.inject_declared_emitter(
        {}, spec_id="v2ecoli.composites.baseline", run_id="r",
        out_dir=str(tmp_path))

    assert calls, "discover_generators() was not invoked for an empty registry"
    assert kind == "parquet"
    assert state["declared_emitter"]["_type"] == "step"
