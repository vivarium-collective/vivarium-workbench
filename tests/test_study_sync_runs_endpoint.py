from pathlib import Path
from pbg_superpowers import study_io, run_registry
from vivarium_dashboard.lib import lifecycle_mutations


def test_sync_runs_for_test(tmp_path: Path):
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    d = tmp_path / "studies" / "s1"; d.mkdir(parents=True)
    study_io.save_yaml_atomic(d / "study.yaml", {"name": "s1", "runs": []})
    run_registry.register_run(d / "runs.db", "r1", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    resp, code = lifecycle_mutations.study_sync_runs(tmp_path, {"study": "s1"})
    assert code == 200
    assert resp["summary"]["added"] == 1
