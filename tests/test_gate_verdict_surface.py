"""Thread-A / Task 1 (A2): surface the PERSISTED gate_evaluator divergence.

The spine's ``study_verdict.write_gate_evaluator`` writes
``pipeline_gate.gate_evaluator`` carrying ``result`` / ``evaluated_by`` /
``diverges_from_authored``. The study-detail builder must surface THAT persisted
slot as ``computed_gate_verdict`` (so the frontend can render a code-vs-authored
divergence chip), rather than recomputing via ``roll_up_verdict`` which drops
``diverges_from_authored``. It only falls back to the recompute when no
persisted gate_evaluator exists.
"""
from __future__ import annotations

from pathlib import Path

import yaml
import pytest

_PKG = Path(__file__).parent.parent / "vivarium_dashboard"


_V3_BASE = {
    "schema_version": 3,
    "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
    "variants": [],
}


@pytest.fixture
def tmp_study_with_gate_evaluator(tmp_path, monkeypatch):
    """A study whose study.yaml has a PERSISTED diverging gate_evaluator.

    Authored gate_status says 'passed' but the persisted coded evaluator says
    'failed' → diverges_from_authored is True.
    """
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "diverge-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = dict(
        _V3_BASE,
        name="diverge-study",
        objective="test",
        status="in_progress",
        gate_status="passed",
        behavior_tests=[{"name": "t1"}],
        runs=[{
            "name": "r1", "status": "completed",
            "outcomes": {"t1": {"result": "FAIL"}},
        }],
        pipeline_gate={
            "gate_evaluator": {
                "result": "failed",
                "blocked_by": ["t1"],
                "evaluated_by": "code",
                "diverges_from_authored": True,
            },
        },
    )
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return "diverge-study"


def test_detail_surfaces_persisted_gate_evaluator(tmp_study_with_gate_evaluator):
    """computed_gate_verdict must carry the persisted divergence, not a recompute."""
    import vivarium_dashboard.server as srv
    spec = srv._study_detail_spec(tmp_study_with_gate_evaluator)
    assert spec is not None
    ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator") or spec.get("computed_gate_verdict")
    assert ge and ge.get("diverges_from_authored") is True
    assert ge.get("evaluated_by") == "code"


def test_computed_gate_verdict_prefers_persisted_evaluator(tmp_study_with_gate_evaluator):
    """The exposed computed_gate_verdict key carries diverges_from_authored
    sourced from the persisted slot (the recompute would drop it)."""
    import vivarium_dashboard.server as srv
    spec = srv._study_detail_spec(tmp_study_with_gate_evaluator)
    cgv = spec["computed_gate_verdict"]
    assert cgv.get("result") == "failed"
    assert cgv.get("evaluated_by") == "code"
    assert cgv.get("diverges_from_authored") is True
    # authored gate_status untouched
    assert spec.get("gate_status") == "passed"


def test_computed_gate_verdict_falls_back_when_no_persisted_slot(tmp_path, monkeypatch):
    """Without a persisted gate_evaluator, fall back to roll_up_verdict."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "plain-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = dict(
        _V3_BASE, name="plain-study", objective="test", status="in_progress",
        behavior_tests=[{"name": "t1"}],
        runs=[{"name": "r1", "status": "completed",
               "outcomes": {"t1": {"result": "PASS"}}}],
    )
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(srv, "WORKSPACE", ws)

    result_spec = srv._study_detail_spec("plain-study")
    cgv = result_spec["computed_gate_verdict"]
    assert cgv["result"] == "passed"
    assert cgv["evaluated_by"] == "code"


# ---------------------------------------------------------------------------
# Structural tests for the render layer (no JS harness — assert markup + data
# references). The chip mirrors the param-enforcement banner.
# ---------------------------------------------------------------------------

def test_walkthrough_js_renders_gate_divergence_chip():
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    # References the computed data + the divergence flag
    assert "computed_gate_verdict" in js
    assert "diverges_from_authored" in js
    # Renders a code-vs-authored chip connected to the source
    assert "sp-gate-divergence" in js
    assert "code:" in js and "authored:" in js


def test_study_detail_template_renders_gate_divergence_chip():
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    assert "computed_gate_verdict" in html
    assert "diverges_from_authored" in html
    assert "gate-divergence-chip" in html
