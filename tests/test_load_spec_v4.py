import pytest
import yaml
import pathlib
from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError


def test_load_spec_v3_yaml_returns_v4_in_memory(tmp_path):
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "test-study",
        "baseline": [{"name": "b", "composite": "pkg.c", "params": {}}],
        "variants": [],
        "interventions": [],
        "runs": [],
        "visualizations": [],
        "conclusion": "",
        "objective": "",
        "parent_studies": [],
    }))
    spec = load_spec(spec_path)
    assert spec["schema_version"] == 4
    assert spec["tests"]["auto_discover"] is True
    assert spec["tests"]["data_source"] == "latest_run"
    assert spec["references"] == []
    assert spec["implementation_tasks"] == ""


_V3_STUDY_BASE = {
    "schema_version": 3,
    "name": "test-study",
    "baseline": [{"name": "b", "composite": "pkg.c", "params": {}}],
    "variants": [],
    "interventions": [],
    "runs": [],
    "visualizations": [],
    "conclusion": "",
    "objective": "",
    "parent_studies": [],
}


def test_v4_validation_error_includes_reserved_field_hint_references(tmp_path):
    """A v3 spec with references as a dict (not the v4-required list) should raise
    InvestigationSpecError with a message that mentions 'reserved by schema_version 4',
    so the user understands this is a v3→v4 auto-migration collision, not a v3 bug.
    """
    spec = dict(_V3_STUDY_BASE)
    spec["references"] = {"paper1": "doi:10.1000/xyz", "paper2": "doi:10.1001/abc"}
    spec_path = tmp_path / "study.yaml"
    spec_path.write_text(yaml.safe_dump(spec))

    with pytest.raises(InvestigationSpecError, match="reserved by schema_version 4"):
        load_spec(spec_path)


def test_v4_validation_error_includes_reserved_field_hint_implementation_tasks(tmp_path):
    """A v3 spec with implementation_tasks as a list (not the v4-required string) should
    include the reserved-field hint in the error message.
    """
    spec = dict(_V3_STUDY_BASE)
    spec["implementation_tasks"] = ["task one", "task two"]
    spec_path = tmp_path / "study.yaml"
    spec_path.write_text(yaml.safe_dump(spec))

    with pytest.raises(InvestigationSpecError, match="reserved by schema_version 4"):
        load_spec(spec_path)


def test_load_spec_v4_yaml_passes_through(tmp_path):
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "test-study",
        "baseline": [{"name": "b", "composite": "pkg.c", "params": {}}],
        "variants": [],
        "interventions": [],
        "runs": [],
        "visualizations": [],
        "conclusion": "",
        "objective": "",
        "parent_studies": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [],
        "implementation_tasks": "",
    }))
    spec = load_spec(spec_path)
    assert spec["schema_version"] == 4
