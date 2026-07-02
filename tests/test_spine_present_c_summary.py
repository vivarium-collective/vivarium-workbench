"""Thread-C / Task 1 (C1a): 'Spine at a glance' summary panel.

The study page gets a compact panel at the TOP (above the tabs) that
re-presents the spine's ALREADY-COMPUTED A+B content in one glance — verdict,
why (primary finding), acceptance, readiness, next — each row linking to its
detail tab/section. No recompute; AI-free.

Python-testable: ``_study_detail_spec`` surfaces ``spine_acceptance`` — the
owning investigation's PERSISTED ``executive.computed_acceptance`` criterion(s)
covering THIS study (pure disk read, no recompute). The render layer has no JS
harness → structural-tested (assert the markup/data references).
"""
from __future__ import annotations

from pathlib import Path

import yaml
import pytest

_PKG = Path(__file__).parent.parent / "vivarium_workbench"

_V3_BASE = {
    "schema_version": 3,
    "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
    "variants": [],
}


@pytest.fixture
def tmp_ws_with_investigation(tmp_path):
    """A nested-layout study under an investigation whose investigation.yaml
    carries a PERSISTED executive.computed_acceptance covering the study."""
    ws = tmp_path / "ws"
    inv = ws / "investigations" / "repl-inv"
    sd = inv / "studies" / "oric-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "repl-inv",
        "executive": {
            "verdict_status": "in-progress",
            "computed_acceptance": {
                "verdict_status": "failing",
                "diverges_from_authored": True,
                "criteria": [
                    {"study": "oric-study", "behavior": "oric_timing", "result": "failing"},
                    {"study": "other-study", "behavior": "ter_timing", "result": "passing"},
                ],
            },
        },
    }))
    spec = dict(
        _V3_BASE, name="oric-study", objective="test", status="in_progress",
        gate_status="passed",
        behavior_tests=[{"name": "t1"}],
        findings=[{"id": "F1", "statement": "oriC fires late",
                   "classification": "primary",
                   "evidence": {"observed": 2.1, "divergence_factor": 4.2}}],
        pipeline_gate={"gate_evaluator": {
            "result": "failed", "evaluated_by": "code",
            "diverges_from_authored": True}},
    )
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    return ws, "oric-study"


def test_detail_spec_surfaces_spine_acceptance(tmp_ws_with_investigation):
    """spine_acceptance carries the owning investigation + the criterion(s)
    covering THIS study, read from persisted computed_acceptance (no recompute)."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec
    ws, name = tmp_ws_with_investigation
    spec = load_study_detail_spec(ws, name)
    sa = spec.get("spine_acceptance")
    assert sa and sa.get("investigation") == "repl-inv"
    assert sa.get("verdict_status") == "failing"
    crits = sa.get("criteria") or []
    # Only the criterion for THIS study, not the sibling.
    assert len(crits) == 1
    assert crits[0]["study"] == "oric-study"
    assert crits[0]["behavior"] == "oric_timing"
    assert crits[0]["result"] == "failing"


def test_detail_spec_spine_acceptance_absent_without_owner(tmp_path):
    """A study with no owning investigation → spine_acceptance is None/absent
    (the panel omits the acceptance row). Never raises."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec
    ws = tmp_path / "ws"
    sd = ws / "studies" / "lonely"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump(dict(
        _V3_BASE, name="lonely", objective="x", status="planned")))
    spec = load_study_detail_spec(ws, "lonely")
    assert spec is not None
    assert not spec.get("spine_acceptance")


# ── Structural tests for the render layer (no JS harness) ──────────────────

def test_template_has_spine_summary_panel_above_tabs():
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    assert 'id="spine-summary"' in html
    assert "spine-summary" in html
    # The panel sits ABOVE the tab nav (re-presents the spine in one glance).
    assert html.index('id="spine-summary"') < html.index('class="study-tabs"')


def test_study_detail_js_renders_spine_summary():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    assert "_renderSpineSummary" in js
    # Verdict row reuses the computed gate verdict + the code-vs-authored signal.
    assert "computed_gate_verdict" in js
    assert "diverges_from_authored" in js
    # Why row = the primary finding statement + its divergence_factor.
    assert "findings" in js
    assert "divergence_factor" in js
    # Acceptance row reuses the surfaced spine_acceptance (links to investigation).
    assert "spine_acceptance" in js
    # Next row = top next_action / follow-up.
    assert "next_action" in js
    # Each row links to its detail tab/section (re-presentation, not a rewrite).
    assert "_setStudyTab(" in js
    assert "#readiness-panel" in js
