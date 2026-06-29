from pathlib import Path
import pytest
from vivarium_dashboard.lib import run_core
from vivarium_dashboard.lib.run_core import RunTargetUnavailable

def test_target_local_without_viv_build(tmp_path):
    assert run_core.run_target_for(tmp_path) == "local"

def test_target_deployment_with_viv_build(tmp_path):
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    assert run_core.run_target_for(tmp_path) == "deployment"

def test_invoke_run_local_returns_plan_with_run_id(tmp_path):
    plan = run_core.invoke_run(tmp_path, spec_id="pkg.composites.x",
                               config={"k": 2}, db_path=tmp_path / "runs.db", label="L", n_steps=5)
    assert plan.run_id.startswith("pkg.composites.x__")
    assert plan.target == "local" and plan.config == {"k": 2} and plan.label == "L"

def test_invoke_run_deployment_raises(tmp_path):
    (tmp_path / ".viv-build.json").write_text("{}")
    with pytest.raises(RunTargetUnavailable):
        run_core.invoke_run(tmp_path, spec_id="x", config={}, db_path=tmp_path / "r.db")
