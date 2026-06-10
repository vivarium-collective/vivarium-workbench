from pathlib import Path
from pbg_superpowers import study_io, run_registry, study_outcomes


def test_record_runs_after_run_picks_up_db_row(tmp_path: Path):
    d = tmp_path / "studies" / "s1"; d.mkdir(parents=True)
    study_io.save_yaml_atomic(d / "study.yaml", {"name": "s1", "runs": []})
    run_registry.register_run(d / "runs.db", "run-x", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    study_outcomes.record_runs(d)              # the call the hook will make
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    assert any(r["name"] == "run-x" for r in spec["runs"])
