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

_PKG = Path(__file__).parent.parent / "vivarium_workbench"


_V3_BASE = {
    "schema_version": 3,
    "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
    "variants": [],
}


@pytest.fixture
def tmp_study_with_gate_evaluator(tmp_path):
    """A study whose study.yaml has a PERSISTED diverging gate_evaluator.

    Authored gate_status says 'passed' but the persisted coded evaluator says
    'failed' → diverges_from_authored is True.
    """
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
    return ws, "diverge-study"


def test_detail_surfaces_persisted_gate_evaluator(tmp_study_with_gate_evaluator):
    """computed_gate_verdict must carry the persisted divergence, not a recompute."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec
    ws, name = tmp_study_with_gate_evaluator
    spec = load_study_detail_spec(ws, name)
    assert spec is not None
    ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator") or spec.get("computed_gate_verdict")
    assert ge and ge.get("diverges_from_authored") is True
    assert ge.get("evaluated_by") == "code"


def test_computed_gate_verdict_prefers_persisted_evaluator(tmp_study_with_gate_evaluator):
    """The exposed computed_gate_verdict key carries diverges_from_authored
    sourced from the persisted slot (the recompute would drop it)."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec
    ws, name = tmp_study_with_gate_evaluator
    spec = load_study_detail_spec(ws, name)
    cgv = spec["computed_gate_verdict"]
    assert cgv.get("result") == "failed"
    assert cgv.get("evaluated_by") == "code"
    assert cgv.get("diverges_from_authored") is True
    # authored gate_status untouched
    assert spec.get("gate_status") == "passed"


def test_computed_gate_verdict_falls_back_when_no_persisted_slot(tmp_path):
    """Without a persisted gate_evaluator, fall back to roll_up_verdict."""
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

    from vivarium_workbench.lib.study_spec import load_study_detail_spec
    result_spec = load_study_detail_spec(ws, "plain-study")
    cgv = result_spec["computed_gate_verdict"]
    assert cgv["result"] == "passed"
    assert cgv["evaluated_by"] == "code"


def test_computed_gate_verdict_carries_counts_on_persisted_path(
        tmp_study_with_gate_evaluator):
    """The persisted slot predates `counts`; the builder backfills it render-time
    (workbench#758) so the SPA can show the derivation beside the badge."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec
    ws, name = tmp_study_with_gate_evaluator
    cgv = load_study_detail_spec(ws, name)["computed_gate_verdict"]
    assert cgv["counts"] == {"total": 1, "pass": 0, "fail": 1, "skip": 0,
                             "pending": 0}


def test_computed_gate_verdict_carries_counts_on_fallback_path(tmp_path):
    """The roll_up_verdict fallback already carries `counts`; surface them.

    Uses 4 passed + 1 skipped → needs_calibration, the #758 worked example: the
    verdict alone can't distinguish it from a bare skip, but the counts can."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "cal-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = dict(
        _V3_BASE, name="cal-study", objective="test", status="in_progress",
        behavior_tests=[{"name": n} for n in ("t1", "t2", "t3", "t4", "t5")],
        runs=[{"name": "r1", "status": "completed", "outcomes": {
            "t1": {"result": "PASS"}, "t2": {"result": "PASS"},
            "t3": {"result": "PASS"}, "t4": {"result": "PASS"},
            "t5": {"result": "skip"}}}],
    )
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))

    from vivarium_workbench.lib.study_spec import load_study_detail_spec
    cgv = load_study_detail_spec(ws, "cal-study")["computed_gate_verdict"]
    assert cgv["result"] == "needs_calibration"
    assert cgv["counts"]["pass"] == 4 and cgv["counts"]["skip"] == 1


# ---------------------------------------------------------------------------
# Structural tests for the render layer (no JS harness — assert markup + data
# references). The chip mirrors the param-enforcement banner.
# ---------------------------------------------------------------------------

def test_report_template_renders_gate_divergence_chip():
    # The report (lib.investigation_report + template) renders a code-vs-authored
    # gate-divergence chip per study from computed_gate_verdict.
    tpl = (_PKG / "templates" / "investigation-report.html").read_text(encoding="utf-8")
    assert "computed_gate_verdict" in tpl
    assert "diverges_from_authored" in tpl
    assert "sp-gate-divergence" in tpl
    assert "code:" in tpl and "authored:" in tpl


def test_study_detail_template_renders_gate_divergence_chip():
    # Post study-layout reorg (#1366): the computed gate verdict is surfaced in
    # the Decide tab's "Conclusion logic — gate decision" section (its appropriate
    # home — the evaluation the conclusion rests on), server-rendered from
    # study.computed_gate_verdict with a code-vs-authored divergence chip.
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    assert "computed_gate_verdict" in html
    assert "diverges_from_authored" in html
    assert "gate-divergence-chip" in html
