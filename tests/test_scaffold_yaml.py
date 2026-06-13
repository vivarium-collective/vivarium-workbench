"""Tests for vivarium_dashboard.lib.scaffold_yaml.

The helpers emit v4 study + v2 investigation YAML text with the narrative-
spine fields commented in as TODO placeholders. The tests cover three
properties:

  1. The emitted text parses as valid YAML.
  2. The parsed YAML validates against the pbg-template schemas (so a
     freshly-seeded file passes lint on day one — the comments don't
     introduce hidden invalid fields).
  3. The narrative-spine TODO markers (executive, biological_story,
     report, study_card, conclusion_verdicts, etc.) appear in the text
     body so the user can see them when they open the file.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from vivarium_dashboard.lib.scaffold_yaml import (
    v2_investigation_scaffold,
    v4_study_scaffold,
)


# ---------------------------------------------------------------------------
# Schema fixtures. Path-based — the schemas live in the sibling pbg-template
# checkout. Skip the validation suite cleanly if that checkout isn't present
# (CI without pbg-template) instead of false-failing.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pbg_template_root() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "pbg-template",
        Path.home() / "code" / "pbg-template",
    ]
    for c in candidates:
        if (c / "template" / ".pbg" / "schemas" / "study.schema.json").is_file():
            return c
    pytest.skip("pbg-template checkout not found; schema-validation tests skipped")


@pytest.fixture(scope="module")
def study_schema(pbg_template_root: Path) -> dict:
    return json.loads(
        (pbg_template_root / "template" / ".pbg" / "schemas" / "study.schema.json").read_text()
    )


@pytest.fixture(scope="module")
def inv_schema(pbg_template_root: Path) -> dict:
    return json.loads(
        (pbg_template_root / "template" / ".pbg" / "schemas" / "investigation.schema.json").read_text()
    )


# ---------------------------------------------------------------------------
# v4 study scaffold
# ---------------------------------------------------------------------------


class TestV4StudyScaffold:
    def test_parses_as_yaml_with_composite(self):
        text = v4_study_scaffold("my-study", composite="pkg.composites.foo")
        spec = yaml.safe_load(text)
        assert spec["name"] == "my-study"
        assert spec["schema_version"] == 4
        assert spec["baseline"][0]["composite"] == "pkg.composites.foo"

    def test_parses_as_yaml_without_composite(self):
        text = v4_study_scaffold("my-study")
        spec = yaml.safe_load(text)
        # Placeholder must match the schema's composite-path regex so the
        # scaffold is valid out of the box.
        assert "." in spec["baseline"][0]["composite"]
        assert spec["baseline"][0]["composite"].islower() or "_" in spec["baseline"][0]["composite"]

    def test_validates_against_v4_schema(self, study_schema):
        text = v4_study_scaffold("my-study", composite="pkg.composites.foo")
        jsonschema.validate(yaml.safe_load(text), study_schema)

    def test_validates_without_composite(self, study_schema):
        text = v4_study_scaffold("my-study")
        jsonschema.validate(yaml.safe_load(text), study_schema)

    def test_validates_with_custom_baseline_name(self, study_schema):
        text = v4_study_scaffold(
            "my-study", composite="pkg.composites.foo", baseline_name="alt"
        )
        spec = yaml.safe_load(text)
        assert spec["baseline"][0]["name"] == "alt"
        jsonschema.validate(spec, study_schema)

    @pytest.mark.parametrize("marker", [
        "schema_version: 4",
        # report/study_card/conclusion_verdicts demoted from ★author-first
        # (item 12): still present as commented target shape, but derived from
        # canonical fields so they are not hand-entry inputs.
        "report:",
        "study_card:",
        "conclusion_verdicts:",
        "★ question:",
        "★ conditions:",
        "★ behavior_tests:",
        "★ readouts:",
        "literature_anchors:",
        "design_pivot_required:",
        "biological_summary:",
        "model_change:",
        "implementation_requirements:",
        "runtime:",
        "enforced_params:",
    ])
    def test_narrative_spine_present_as_commented_todos(self, marker):
        """Each narrative section must appear in the text body — either as
        live YAML or as a commented TODO — so the user sees the target shape
        without reading docs first."""
        text = v4_study_scaffold("s", composite="pkg.composites.foo")
        assert marker in text, f"missing scaffold marker: {marker!r}"

    def test_composition_commitment_template_present(self):
        """C-COMMIT — the optional composition_commitment block is offered as a
        commented template with its full sub-shape."""
        text = v4_study_scaffold("s", composite="pkg.composites.foo")
        assert "composition_commitment:" in text
        for key in ("component_added", "deficit_addressed", "closure_gap_item",
                    "new_behavior", "invariants_required", "alternatives_excluded"):
            assert key in text, f"missing composition_commitment sub-key: {key}"

    def test_representational_claims_slot_present(self):
        """C-MODELCARD — model_change.representational_claims authored slot is
        offered as a commented template."""
        text = v4_study_scaffold("s", composite="pkg.composites.foo")
        assert "representational_claims:" in text
        # still parses (the slot is commented, not live YAML).
        import yaml as _yaml
        _yaml.safe_load(text)

    def test_wave3a_workflow_typing_markers_present(self):
        """Wave 3a — study_type (#10), next_action_type (#7) on findings, and a
        preregistered block (#18) are offered as commented templates with their
        full enum vocabulary, and the scaffold still parses as valid YAML."""
        text = v4_study_scaffold("s", composite="pkg.composites.foo")
        # #10 study_type enum — exact value list must match the contract.
        assert "study_type:" in text
        for v in ("exploratory", "confirmatory", "diagnostic",
                  "adversarial", "standard"):
            assert v in text, f"missing study_type enum value: {v}"
        # #7 next_action_type enum on the findings template.
        assert "next_action_type:" in text
        for v in ("replicate", "calibrate", "ablate", "adversarially_probe",
                  "refine_representation", "split_hypothesis",
                  "retire_hypothesis", "escalate_model"):
            assert v in text, f"missing next_action_type enum value: {v}"
        # #18 preregistered block + its sub-keys.
        assert "preregistered:" in text
        for key in ("criteria", "thresholds", "predictions", "controls",
                    "registered_at"):
            assert key in text, f"missing preregistered sub-key: {key}"
        # Still parses (the new fields are comments, not live YAML).
        yaml.safe_load(text)

    def test_prerequisite_item_documents_relation_key(self):
        """W13 — the commented pipeline_gate.prerequisites template surfaces
        the optional `relation` key + its vocabulary so authors know an edge
        can be typed (default leads-to)."""
        text = v4_study_scaffold("s", composite="pkg.composites.foo")
        assert "pipeline_gate:" in text
        assert "prerequisites:" in text
        assert "relation:" in text
        for rel in ("leads-to", "model-input", "evidence",
                    "calibrates-threshold", "refutes-alternative"):
            assert rel in text, f"missing relation vocabulary term: {rel}"
        assert "outputs_used:" in text


# ---------------------------------------------------------------------------
# v2 investigation scaffold
# ---------------------------------------------------------------------------


class TestV2InvestigationScaffold:
    def test_parses_as_yaml(self):
        text = v2_investigation_scaffold("my-inv")
        spec = yaml.safe_load(text)
        assert spec["name"] == "my-inv"
        assert spec["schema_version"] == 2
        assert spec["studies"] == []

    def test_with_overview(self):
        text = v2_investigation_scaffold("my-inv", overview="Why this matters")
        spec = yaml.safe_load(text)
        assert spec["description"].strip() == "Why this matters"

    def test_with_parent_studies(self):
        text = v2_investigation_scaffold("my-inv", parent_studies=["a", "b"])
        spec = yaml.safe_load(text)
        assert spec["studies"] == ["a", "b"]

    def test_validates_against_v2_schema(self, inv_schema):
        text = v2_investigation_scaffold("my-inv")
        jsonschema.validate(yaml.safe_load(text), inv_schema)

    def test_validates_with_all_options(self, inv_schema):
        text = v2_investigation_scaffold(
            "my-inv",
            title="My Investigation",
            overview="Brief overview here.",
            parent_studies=["study-a", "study-b"],
        )
        jsonschema.validate(yaml.safe_load(text), inv_schema)

    @pytest.mark.parametrize("marker", [
        "schema_version: 2",
        "executive:",
        "scientific_argument:",
        "biological_story:",
        "at_a_glance:",
        "how_to_read:",
        "glossary:",
        "guidelines:",
        "lead:",
    ])
    def test_narrative_spine_present_as_commented_todos(self, marker):
        text = v2_investigation_scaffold("i")
        assert marker in text, f"missing scaffold marker: {marker!r}"

    def test_object_of_evaluation_marker_present(self):
        """Wave 3a #1 — the investigation scaffold offers object_of_evaluation
        with its full enum vocabulary, and still parses as valid YAML."""
        text = v2_investigation_scaffold("i")
        assert "object_of_evaluation:" in text
        for v in ("method", "model", "hypothesis", "composition-protocol"):
            assert v in text, f"missing object_of_evaluation enum value: {v}"
        yaml.safe_load(text)
