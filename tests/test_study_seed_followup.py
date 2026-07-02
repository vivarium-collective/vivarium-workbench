"""Tests for seeding child studies from a parent's follow-ups.

Covers both source forms of seed_followup_study:
  - legacy follow_up_studies[idx]
  - richer discovery_implications.followup_study_proposals (by id + by index),
    which sets a parent_studies edge with relation: leads-to.
"""
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib.study_seed import seed_followup_study


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
    # The parent edge is written to the canonical pipeline_gate.prerequisites
    # in the dict form carrying the leads-to relation (W13); the legacy
    # parent_studies field is no longer written.
    assert child["pipeline_gate"]["prerequisites"] == [
        {"study": "p1", "relation": "leads-to"}]
    assert "parent_studies" not in child
    assert child["seeded_from"]["parent"] == "p1"


def test_seed_from_proposal_by_id_sets_leads_to_edge(_ws):
    _write_parent(_ws, "p1", discovery_implications={
        "followup_study_proposals": _proposals(),
    })
    new_name = seed_followup_study(_ws, "p1", proposal_id="fup-sweep")
    child = yaml.safe_load(
        (_ws / "studies" / new_name / "study.yaml").read_text())

    # Parent edge with the leads-to relation, in the canonical location; the
    # legacy parent_studies field is no longer written.
    assert child["pipeline_gate"]["prerequisites"] == [
        {"study": "p1", "relation": "leads-to"}]
    assert "parent_studies" not in child

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
    assert child["pipeline_gate"]["prerequisites"] == [
        {"study": "p1", "relation": "leads-to"}]
    assert "parent_studies" not in child


def test_seed_proposal_unknown_id_raises(_ws):
    _write_parent(_ws, "p1", discovery_implications={
        "followup_study_proposals": _proposals(),
    })
    with pytest.raises(KeyError):
        seed_followup_study(_ws, "p1", proposal_id="does-not-exist")


def _write_parent_with_finding(ws: Path, name: str) -> Path:
    return _write_parent(ws, name, findings=[{
        "id": "F-01",
        "kind": "biological",
        "status": "contradicts",
        "statement": "v2ecoli underestimates the DnaA-ATP fraction ~5x.",
        "next_action": (
            "Calibrate the DARS reactivation rate to match the literature "
            "ATP fraction in range 0.20-0.30."
        ),
        "evidence": {"from_test": "dnaA-atp-fraction"},
        "explanation": "ATP-loading is gated by DARS-mediated reactivation.",
    }])


def test_seed_from_finding_delegates_to_pbg(_ws):
    """A finding with a next_action seeds STANDALONE (no pre-existing
    proposal) by delegating to the pbg seed mechanism; the parent finding
    is stamped seeded_study."""
    _write_parent_with_finding(_ws, "p1")
    new_name = seed_followup_study(_ws, "p1", finding_id="F-01")
    child = yaml.safe_load(
        (_ws / "studies" / new_name / "study.yaml").read_text())
    assert child["seeded_from"]["finding"] == "F-01"
    assert child["seeded_from"]["study"] == "p1"
    assert child["purpose"]["question"]
    # Parent finding stamped.
    parent = yaml.safe_load((_ws / "studies" / "p1" / "study.yaml").read_text())
    f = next(f for f in parent["findings"] if f["id"] == "F-01")
    assert f["seeded_study"] == new_name


def test_seed_from_finding_adds_investigation_backlink(_ws):
    """The finding-seeded child is appended to investigations listing the
    parent (the dashboard's back-link, preserved through delegation)."""
    _write_parent_with_finding(_ws, "p1")
    inv = _ws / "investigations" / "inv" / "investigation.yaml"
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text("name: inv\nstudies:\n  - p1\n")
    new_name = seed_followup_study(_ws, "p1", finding_id="F-01")
    studies = yaml.safe_load(inv.read_text())["studies"]
    assert new_name in studies


def test_post_study_seed_followup_accepts_finding_id(_ws):
    from vivarium_workbench.lib.lifecycle_mutations import study_seed_followup as _post_study_seed_followup_for_test
    _write_parent_with_finding(_ws, "p1")
    body, code = _post_study_seed_followup_for_test(
        _ws, {"parent": "p1", "finding_id": "F-01"})
    assert code == 200, body
    assert (_ws / "studies" / body["new_slug"] / "study.yaml").is_file()
    assert body["new_study_name"] == body["new_slug"]


def test_post_study_seed_followup_legacy_still_works(_ws):
    """The legacy followup_idx path still routes (no finding_id)."""
    from vivarium_workbench.lib.lifecycle_mutations import study_seed_followup as _post_study_seed_followup_for_test
    _write_parent(_ws, "p1", follow_up_studies=[
        {"title": "old follow-up", "kind": "new", "why": "because"},
    ])
    body, code = _post_study_seed_followup_for_test(
        _ws, {"parent": "p1", "followup_idx": 0})
    assert code == 200, body
    assert body["new_study_name"]


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


# ---------------------------------------------------------------------------
# Wave 3a #19 — a failing study seeds a typed (diagnostic) child.
# ---------------------------------------------------------------------------

def test_seed_legacy_threads_study_type(_ws):
    """study_type passed to seed_followup_study is stamped on the seeded child
    (legacy path — built locally, so the typing is exercised end-to-end)."""
    _write_parent(_ws, "p1", follow_up_studies=[
        {"title": "diagnose the failure", "kind": "new", "why": "it failed"},
    ])
    new_name = seed_followup_study(_ws, "p1", 0, study_type="diagnostic")
    child = yaml.safe_load(
        (_ws / "studies" / new_name / "study.yaml").read_text())
    assert child["study_type"] == "diagnostic"


def test_post_study_seed_followup_accepts_study_type(_ws):
    """The endpoint threads study_type=diagnostic through to the seeded child
    (critique #19 — the failed-study seed button passes it). Uses the legacy
    followup path so the test does not depend on the pbg/ruamel finding path."""
    from vivarium_workbench.lib.lifecycle_mutations import study_seed_followup as _post_study_seed_followup_for_test
    _write_parent(_ws, "p1", follow_up_studies=[
        {"title": "diagnose the failure", "kind": "new", "why": "it failed"},
    ])
    body, code = _post_study_seed_followup_for_test(
        _ws, {"parent": "p1", "followup_idx": 0, "study_type": "diagnostic"})
    assert code == 200, body
    child = yaml.safe_load(
        (_ws / "studies" / body["new_slug"] / "study.yaml").read_text())
    assert child["study_type"] == "diagnostic"
