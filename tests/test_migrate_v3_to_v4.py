"""Tests for v3 → v4 study spec migration (adds tests/references/implementation_tasks)."""
from vivarium_workbench.lib.spec_migration import migrate_v3_to_v4


def test_migrate_v3_to_v4_adds_empty_tests_block():
    spec = {"schema_version": 3, "name": "s", "baseline": [], "variants": []}
    out = migrate_v3_to_v4(spec)
    assert out["schema_version"] == 4
    assert out["tests"] == {
        "auto_discover": True,
        "data_source": "latest_run",
        "pytest_args": [],
        "last_results": None,
    }


def test_migrate_v3_to_v4_adds_empty_references_and_implementation_tasks():
    spec = {"schema_version": 3, "name": "s", "baseline": []}
    out = migrate_v3_to_v4(spec)
    assert out["references"] == []
    assert out["implementation_tasks"] == ""


def test_migrate_v3_to_v4_preserves_all_existing_fields():
    spec = {
        "schema_version": 3,
        "name": "s",
        "objective": "x",
        "baseline": [{"name": "b", "composite": "c", "params": {}}],
        "variants": [{"name": "v", "base_composite": "b", "parameter_overrides": {"r": 2.0}}],
        "interventions": [{"name": "i", "description": "d"}],
        "runs": [{"run_id": "r1", "variant": None, "composite": "b", "label": "", "status": "completed", "n_steps": 100}],
        "visualizations": [{"name": "vz", "address": "local:V", "config": {}}],
        "conclusion": "yes",
    }
    out = migrate_v3_to_v4(spec)
    assert out["schema_version"] == 4
    assert out["objective"] == "x"
    assert out["baseline"] == spec["baseline"]
    assert out["variants"] == spec["variants"]
    assert out["interventions"] == spec["interventions"]
    assert out["runs"] == spec["runs"]
    assert out["visualizations"] == spec["visualizations"]
    assert out["conclusion"] == "yes"


def test_migrate_v3_to_v4_idempotent():
    spec_v4 = {
        "schema_version": 4,
        "name": "s",
        "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [],
        "implementation_tasks": "",
    }
    out = migrate_v3_to_v4(spec_v4)
    assert out == spec_v4  # identity


def test_migrate_v3_to_v4_preserves_existing_tests_block():
    spec = {
        "schema_version": 3,
        "name": "s",
        "baseline": [],
        "tests": {"auto_discover": False, "data_source": "first_run", "pytest_args": ["-k", "foo"], "last_results": {"passed": 1, "failed": 0, "skipped": 0, "duration_s": 0.1, "timestamp": "2026-05-15T18:42:00Z"}},
    }
    out = migrate_v3_to_v4(spec)
    assert out["tests"]["auto_discover"] is False
    assert out["tests"]["data_source"] == "first_run"
    assert out["tests"]["pytest_args"] == ["-k", "foo"]
    assert out["tests"]["last_results"]["passed"] == 1


def test_migrate_v3_to_v4_skips_non_v3_spec():
    spec = {"schema_version": 2, "name": "s"}
    out = migrate_v3_to_v4(spec)
    assert out["schema_version"] == 2  # untouched
