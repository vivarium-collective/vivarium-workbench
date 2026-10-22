import pytest
import yaml
import pathlib
from vivarium_workbench.lib.investigations import load_spec, InvestigationSpecError


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


def test_load_spec_promotes_study_card_string_to_dict(tmp_path):
    """study_card authored as a plain string (a common scaffold-template
    mistake) silently renders nothing — the dashboard walkthrough reads
    sc.goal / sc.mechanism / etc. on a dict. load_spec auto-promotes
    string -> {goal: string} so the authored prose surfaces in the Goal row."""
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "stringy-card",
        "baseline": [{"name": "b", "composite": "pkg.c", "params": {}}],
        "study_card": "This study tests the X mechanism under Y conditions.",
    }))
    spec = load_spec(spec_path)
    assert isinstance(spec["study_card"], dict)
    assert spec["study_card"]["goal"] == "This study tests the X mechanism under Y conditions."


def test_load_spec_leaves_study_card_dict_intact(tmp_path):
    """Already-dict study_card MUST NOT be touched — preserves authored
    goal/mechanism/why_before_next/etc. fields exactly."""
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "dicty-card",
        "baseline": [{"name": "b", "composite": "pkg.c", "params": {}}],
        "study_card": {
            "goal": "Test X",
            "mechanism": "via Y",
            "why_before_next": "Z gates downstream",
        },
    }))
    spec = load_spec(spec_path)
    assert spec["study_card"]["goal"] == "Test X"
    assert spec["study_card"]["mechanism"] == "via Y"
    assert spec["study_card"]["why_before_next"] == "Z gates downstream"


def test_load_spec_v3_redesign_path_keeps_tests_as_list(tmp_path):
    """v3 spec with conditions: block + list tests[] must NOT have tests[]
    rewritten to a dict by migrate_v3_to_v4, or the v4-redesign validator
    rejects it with 'tests must be a list' and the study appears INVALID."""
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "redesign-study",
        "question": "Does X work?",
        "conditions": {
            "baseline": {"composite": "pkg.composites.x"},
            "variants": [],
            "model_settings": [],
        },
        "tests": [{"name": "t1", "description": "X works."}],
    }))
    spec = load_spec(spec_path)
    assert spec["schema_version"] == 4
    # tests[] must remain a list — the v4-redesign validator demands it.
    assert isinstance(spec["tests"], list)
    assert spec["tests"][0]["name"] == "t1"


def test_v4_behavior_tests_projection_carries_classification(tmp_path):
    """The legacy ``behavior_tests`` projection (what the report's
    walkthrough.js renders) must carry each test's ``classification`` through,
    or every v4 test renders as "unclassified" in the investigation report.
    Regression for the showcase studies rendering UNCLASSIFIED chips."""
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "classified-study",
        "question": "Does X work?",
        "status": "complete",
        "conditions": {
            "baseline": {"composite": "pkg.composites.x"},
            "variants": [],
            "model_settings": [],
        },
        "tests": [
            {"name": "t-primary", "classification": "primary",
             "question": "Is it primary?",
             "measure": {"kind": "condition_count"},
             "pass_if": {"op": "==", "value": 51}},
            {"name": "t-decision", "classification": "decision",
             "question": "Reviewer to decide?",
             "measure": {"kind": "reviewer_decision"},
             "pass_if": {"op": "reviewer_selected_variant"}},
        ],
    }))
    spec = load_spec(spec_path)
    bt = spec["behavior_tests"]
    by_name = {t["name"]: t for t in bt}
    assert by_name["t-primary"]["classification"] == "primary"
    assert by_name["t-decision"]["classification"] == "decision"


def _v3_with_baseline(tmp_path, baseline_entry):
    spec = dict(_V3_STUDY_BASE)
    spec["baseline"] = [baseline_entry]
    p = tmp_path / "study.yaml"
    p.write_text(yaml.safe_dump(spec))
    return p


def test_load_spec_v3_baseline_step(tmp_path):
    """A study baseline may reference a bare process-bigraph Step directly
    (baseline.step) instead of a full composite — the workbench runs either."""
    p = _v3_with_baseline(tmp_path, {"name": "b", "step": "local:pkg.mod.MyStep"})
    spec = load_spec(p)
    assert spec["baseline"][0]["step"] == "local:pkg.mod.MyStep"


def test_load_spec_v3_baseline_process(tmp_path):
    """A study baseline may reference a bare Process directly (baseline.process)."""
    p = _v3_with_baseline(tmp_path, {"name": "b", "process": "local:pkg.mod.MyProcess"})
    spec = load_spec(p)
    assert spec["baseline"][0]["process"] == "local:pkg.mod.MyProcess"


def test_load_spec_v3_baseline_requires_composite_step_or_process(tmp_path):
    """A baseline entry with none of composite/step/process is rejected."""
    p = _v3_with_baseline(tmp_path, {"name": "b"})
    with pytest.raises(InvestigationSpecError, match="composite.*step.*process"):
        load_spec(p)


def test_load_spec_v4_multi_arm_baseline_list_accepted(tmp_path):
    # A multi-arm study: conditions.baseline is a LIST of arms, each with a
    # composite. load_spec must accept it (not raise), and study_interface
    # returns the headline (first) arm's composite.
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "multi-arm-study",
        "question": "Does X behave across arms?",
        "conditions": {"baseline": [
            {"name": "arm-a", "composite": "pkg.a"},
            {"name": "arm-b", "composite": "pkg.b"}]},
    }))
    spec = load_spec(spec_path)  # must not raise
    from vivarium_workbench.lib.study_spec import study_interface
    assert study_interface(spec)["composite"] == "pkg.a"


def test_load_spec_v4_multi_arm_armless_arm_rejected(tmp_path):
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "multi-arm-armless",
        "question": "Does X?",
        "conditions": {"baseline": [
            {"name": "arm-a", "composite": "pkg.a"},
            {"name": "arm-b"}]},
    }))
    with pytest.raises(InvestigationSpecError, match="composite"):
        load_spec(spec_path)
