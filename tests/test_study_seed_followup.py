"""Tests for seeding child studies from a parent's follow-ups.

Covers both source forms of seed_followup_study:
  - legacy follow_up_studies[idx]
  - richer discovery_implications.followup_study_proposals (by id + by index),
    which sets a parent_studies edge with relation: leads-to.
"""
from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.lib.study_seed import seed_followup_study


@pytest.fixture
def _ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: ws\ncreated: \"2026-05-16\"\n"
        "plugin_version: 0.6.1\npackage_path: pkg\n"
    )
    (ws / "investigations").mkdir()
    (ws / "studies").mkdir()
    return ws


def _write_parent(ws: Path, name: str, **fields) -> Path:
    p = ws / "studies" / name / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 4,
        "name": name,
        "status": "ran",
        "baseline": [{"name": "b", "composite": "x"}],
        **fields,
    }
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def _proposals():
    return [
        {
            "id": "fup-sweep",
            "title": "Sweep dnaA copy number",
            "study_type": "parameter_sweep",
            "source_trigger": "low_confidence",
            "target_mechanism_elements": ["dnaA->oriC", "dnaA-titration"],
            "proposed_experiment": "Vary dnaA copy number 0.5x-2x.",
            "expected_information_gain": "high",
            "required_inputs": ["calibrated dnaA Kd"],
        },
        {
            "id": "fup-extend",
            "title": "Extend model with SeqA",
            "study_type": "model_extension",
            "source_trigger": "missing_interaction",
        },
    ]


def test_seed_legacy_follow_up_studies(_ws):
    """Back-compat: seeding from follow_up_studies[idx] still works."""
    _write_parent(_ws, "p1", follow_up_studies=[
        {"title": "old follow-up", "kind": "new", "why": "because"},
    ])
    new_name = seed_followup_study(_ws, "p1", 0)
    child = yaml.safe_load(
        (_ws / "studies" / new_name / "study.yaml").read_text())
    assert child["parent_studies"] == ["p1"]
    assert child["seeded_from"]["parent"] == "p1"


def test_seed_from_proposal_by_id_sets_leads_to_edge(_ws):
    _write_parent(_ws, "p1", discovery_implications={
        "followup_study_proposals": _proposals(),
    })
    new_name = seed_followup_study(_ws, "p1", proposal_id="fup-sweep")
    child = yaml.safe_load(
        (_ws / "studies" / new_name / "study.yaml").read_text())

    # Parent edge with the leads-to relation (both fields).
    assert child["parent_studies"] == [{"study": "p1", "relation": "leads-to"}]
    assert child["pipeline_gate"]["prerequisites"] == [
        {"study": "p1", "relation": "leads-to"}]

    # Proposal content carried onto the child.
    assert child["study_type"] == "parameter_sweep"
    assert child["target_mechanism_elements"] == ["dnaA->oriC", "dnaA-titration"]
    assert child["required_inputs"] == ["calibrated dnaA Kd"]
    assert child["seeded_from"]["proposal_id"] == "fup-sweep"
    assert child["seeded_from"]["source"] == \
        "discovery_implications.followup_study_proposals"
    assert child["status"] == "planned"
    assert child["phase"] == "Design"


def test_seed_from_proposal_by_index(_ws):
    _write_parent(_ws, "p1", discovery_implications={
        "followup_study_proposals": _proposals(),
    })
    new_name = seed_followup_study(_ws, "p1", proposal_idx=1)
    child = yaml.safe_load(
        (_ws / "studies" / new_name / "study.yaml").read_text())
    assert child["study_type"] == "model_extension"
    assert child["seeded_from"]["proposal_id"] == "fup-extend"
    assert child["parent_studies"] == [{"study": "p1", "relation": "leads-to"}]


def test_seed_proposal_unknown_id_raises(_ws):
    _write_parent(_ws, "p1", discovery_implications={
        "followup_study_proposals": _proposals(),
    })
    with pytest.raises(KeyError):
        seed_followup_study(_ws, "p1", proposal_id="does-not-exist")


def test_seed_proposal_adds_child_to_parent_investigation(_ws):
    """The seeded child must be appended to investigations listing the parent
    so it shows up in the DAG view."""
    _write_parent(_ws, "p1", discovery_implications={
        "followup_study_proposals": _proposals(),
    })
    inv = _ws / "investigations" / "inv" / "investigation.yaml"
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text("name: inv\nstudies:\n  - p1\n")

    new_name = seed_followup_study(_ws, "p1", proposal_id="fup-sweep")
    studies = yaml.safe_load(inv.read_text())["studies"]
    assert "p1" in studies
    assert new_name in studies
