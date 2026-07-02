"""Thread-C / Task 4 (C2): investigation verdict DAG + acceptance narrative.

The generated investigation report (`_buildInvestigationReportHtml`) gains a
verdict-annotated study DAG — nodes = member studies, edges from
`parent_studies`/`pipeline_gate.prerequisites`, each badged with its
`computed_gate_verdict.result` (✅/⚠/⛔) — and a one-paragraph acceptance
roll-up connecting the studies into the investigation's verdict (from
`computed_acceptance`). Reuses the existing topological ordering; no recompute.

Structural (no JS harness): assert the markup/data references.
"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_workbench"
_JS = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")


def test_report_renders_verdict_annotated_study_dag():
    assert "study-verdict-dag" in _JS
    # Nodes badged with the code-computed gate verdict.
    assert "computed_gate_verdict" in _JS
    assert "_spineVerdictBadge" in _JS
    # The three verdict glyphs.
    assert "✅" in _JS and "⚠" in _JS and "⛔" in _JS
    # Edges from the pipeline dependency structure.
    assert "parent_studies" in _JS
    # Nodes link to their per-study report sections.
    assert "#study-" in _JS


def test_report_renders_acceptance_rollup_paragraph():
    assert "acceptance-narrative" in _JS
    # Reuses the spine-computed acceptance (no recompute).
    assert "computed_acceptance" in _JS
    assert "acceptance criteria" in _JS


def test_dag_reuses_existing_topological_ordering():
    # The DAG consumes the already-computed `ordered` / `depthMap` (not a new
    # second sort) — the helper references depthMap.
    assert "_verdictDagHtml" in _JS
    assert "depthMap" in _JS
