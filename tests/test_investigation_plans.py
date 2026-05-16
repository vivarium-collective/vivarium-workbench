"""Tests for investigation_plans: load_plan, validators, status derivation."""
import yaml
from pathlib import Path
import pytest
from vivarium_dashboard.lib.investigation_plans import (
    load_plan, save_plan, InvestigationPlanError, derive_study_status,
)


def _write_plan(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_load_plan_minimal(tmp_path):
    p = tmp_path / "investigations" / "demo" / "investigation.yaml"
    _write_plan(p, {
        "schema_version": 1, "name": "demo",
        "studies": [{"study": "s1"}],
    })
    plan = load_plan(p)
    assert plan["name"] == "demo"
    assert plan["studies"][0]["study"] == "s1"


def test_load_plan_rejects_missing_schema_version(tmp_path):
    p = tmp_path / "investigations" / "demo" / "investigation.yaml"
    _write_plan(p, {"name": "demo", "studies": []})
    with pytest.raises(InvestigationPlanError, match="schema_version"):
        load_plan(p)


def test_load_plan_rejects_unknown_gate(tmp_path):
    p = tmp_path / "investigations" / "demo" / "investigation.yaml"
    _write_plan(p, {
        "schema_version": 1, "name": "demo",
        "studies": [{"study": "s1", "gate": "bogus"}],
    })
    with pytest.raises(InvestigationPlanError, match="gate"):
        load_plan(p)


def test_load_plan_rejects_duplicate_study(tmp_path):
    p = tmp_path / "investigations" / "demo" / "investigation.yaml"
    _write_plan(p, {
        "schema_version": 1, "name": "demo",
        "studies": [{"study": "s1"}, {"study": "s1"}],
    })
    with pytest.raises(InvestigationPlanError, match="duplicate"):
        load_plan(p)


def test_save_plan_atomic(tmp_path):
    p = tmp_path / "investigations" / "demo" / "investigation.yaml"
    _write_plan(p, {"schema_version": 1, "name": "demo", "studies": [{"study": "s1"}]})
    plan = load_plan(p)
    plan["objective"] = "new objective"
    save_plan(p, plan)
    assert "new objective" in p.read_text()


def test_derive_study_status_planned_when_no_evidence(tmp_path):
    study_dir = tmp_path / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "s1", "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [], "implementation_tasks": "",
    }))
    status = derive_study_status(tmp_path, "s1", prev_satisfied_gate=True)
    assert status == "planned"


def test_derive_study_status_complete_when_tests_pass_and_run_exists(tmp_path):
    study_dir = tmp_path / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "s1", "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [],
                  "last_results": {"passed": 3, "failed": 0, "skipped": 0, "duration_s": 0.1, "timestamp": "2026-05-15T18:42:00Z"}},
        "references": [], "implementation_tasks": "",
    }))
    import sqlite3
    conn = sqlite3.connect(study_dir / "runs.db")
    conn.execute("CREATE TABLE runs_meta (run_id TEXT)")
    conn.execute("INSERT INTO runs_meta VALUES ('r1')")
    conn.commit(); conn.close()
    status = derive_study_status(tmp_path, "s1", prev_satisfied_gate=True)
    assert status == "complete"


def test_derive_study_status_blocked_when_prev_gate_unsatisfied(tmp_path):
    study_dir = tmp_path / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "s1", "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [], "implementation_tasks": "",
    }))
    status = derive_study_status(tmp_path, "s1", prev_satisfied_gate=False)
    assert status == "blocked"


def test_derive_study_status_in_progress_when_runs_but_tests_failing(tmp_path):
    study_dir = tmp_path / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "s1", "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [],
                  "last_results": {"passed": 1, "failed": 2, "skipped": 0, "duration_s": 0.1, "timestamp": "2026-05-15T18:42:00Z"}},
        "references": [], "implementation_tasks": "",
    }))
    import sqlite3
    conn = sqlite3.connect(study_dir / "runs.db")
    conn.execute("CREATE TABLE runs_meta (run_id TEXT)")
    conn.execute("INSERT INTO runs_meta VALUES ('r1')")
    conn.commit(); conn.close()
    status = derive_study_status(tmp_path, "s1", prev_satisfied_gate=True)
    assert status == "in-progress"


def test_derive_study_status_zero_tests_cannot_satisfy_gate(tmp_path):
    # All-zeros last_results: 0 passed AND 0 failed AND 0 skipped → tests not run / no tests → not complete.
    study_dir = tmp_path / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "s1", "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [],
                  "last_results": {"passed": 0, "failed": 0, "skipped": 0, "duration_s": 0.0, "timestamp": "x"}},
        "references": [], "implementation_tasks": "",
    }))
    import sqlite3
    conn = sqlite3.connect(study_dir / "runs.db")
    conn.execute("CREATE TABLE runs_meta (run_id TEXT)")
    conn.execute("INSERT INTO runs_meta VALUES ('r1')")
    conn.commit(); conn.close()
    status = derive_study_status(tmp_path, "s1", prev_satisfied_gate=True)
    assert status == "in-progress"  # has runs, no tests → not complete
