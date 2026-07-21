from pathlib import Path

from vivarium_workbench.lib import composite_subprocess as cs


def test_injects_both_ram_and_declared_parquet(tmp_path: Path):
    state = {"global_time": 0.0, "bulk": {}, "listeners": {}}
    out = cs.inject_run_emitters(
        state, spec_id="v2ecoli.composites.baseline",
        run_id="v2ecoli.composites.baseline__1__aa",
        emit_paths=["global_time"], workspace=tmp_path)
    assert "user_emitter" in out["state"]                 # RAM live view
    assert out["state"]["user_emitter"]["address"].lower().endswith("ramemitter")
    assert "declared_emitter" in out["state"]             # parquet persistence
    assert out["emitter"] == "parquet"
