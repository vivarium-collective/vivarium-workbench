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
