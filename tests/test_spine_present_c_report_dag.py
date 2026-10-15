"""Investigation report: verdict-annotated study DAG + acceptance roll-up.

The report is now rendered server-side by ``lib.investigation_report`` and its
template (the old client-side ``_buildInvestigationReportHtml`` was removed). Its
verdict DAG has nodes = member studies, edges from ``parent_studies``, each badged
with the code-computed gate verdict in one of five states; plus an acceptance
roll-up. These assert the template's render source (the direct analog of the old
walkthrough.js source checks); the data-shape coverage — that the spine block is
actually populated with nodes/edges/verdicts — lives in
``test_investigation_report.py``.
"""
from __future__ import annotations

from pathlib import Path

_TPL = (
    Path(__file__).parent.parent / "vivarium_workbench" / "templates" / "investigation-report.html"
).read_text(encoding="utf-8")


def test_report_renders_verdict_annotated_study_dag():
    assert "study-verdict-dag" in _TPL
    assert "_verdictDagHtml" in _TPL
    # Nodes badged with the code-computed gate verdict.
    assert "computed_gate_verdict" in _TPL
    assert "_spineVerdictBadge" in _TPL
    # The verdict glyphs.
    assert "✅" in _TPL and "⚠" in _TPL and "⛔" in _TPL
    # Edges from the pipeline dependency structure.
    assert "parent_studies" in _TPL
    # Nodes link to their per-study report sections.
    assert "#s-" in _TPL


def test_report_renders_acceptance_gating_matrix():
    # The per-criterion roll-up and the AC gating matrix were the same rows
    # transposed; consolidated to the single gating matrix (which also flags
    # unlinked-criterion gaps). The divergence badge rides on its label.
    assert "ac-gating-matrix" in _TPL
    assert "acceptance criteria" in _TPL
    assert "acceptance-rollup" not in _TPL


def test_verdict_badge_distinguishes_five_states():
    # roll_up emits five states; the badge legend renders all five distinctly.
    assert "not_started" in _TPL and "needs_calibration" in _TPL
    assert "'◽','not evaluated'" in _TPL
    assert "'🔄','needs calibration'" in _TPL
    assert "'⚠','blocked'" in _TPL


def test_verdict_badge_surfaces_derived_counts():
    # The derived pass/skip counts are shown, so a badge reads as progress.
    assert "_spineCountLabel" in _TPL
    assert "passed" in _TPL and "skipped" in _TPL


def test_dag_uses_topological_ordering():
    # The DAG places nodes by computed depth columns (no second sort).
    assert "_verdictDagHtml" in _TPL
    assert "byDepth" in _TPL
