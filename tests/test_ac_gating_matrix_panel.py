"""Investigation report: the acceptance-criteria gating matrix is NOT rendered.

The report used to render an acceptance-criteria → study gating matrix (plus an
authored acceptance-criteria list) beneath the investigation graph. Both were
removed from the report (HTML + PDF) on request. The spine DATA that computes the
matrix (``linkage_index.ac_gating_matrix``, embedded in the report payload) is
UNCHANGED — only the rendering was removed; see ``test_investigation_report.py``
(``test_spine_verdict_dag_acceptance_and_matrix``) for the data-shape coverage.

These assert the matrix is gone from the report's render source.
"""
from __future__ import annotations

from pathlib import Path

_TPL = (
    Path(__file__).parent.parent / "vivarium_workbench" / "templates" / "investigation-report.html"
).read_text(encoding="utf-8")


def test_ac_gating_matrix_not_rendered_in_report():
    # The matrix table + its builder + its helpers are gone.
    assert "ac-gating-matrix" not in _TPL
    assert "_acGatingMatrixHtml" not in _TPL
    assert "_acResultBadge" not in _TPL
    # The user-facing matrix strings are gone.
    assert "no study linked — gap" not in _TPL
    assert "have no study linked (gaps)" not in _TPL


def test_authored_acceptance_criteria_list_not_rendered():
    assert "authoredAcList" not in _TPL
    assert "Acceptance criteria (authored)" not in _TPL


def test_spine_graph_still_rendered():
    # Removing the matrix must not take the investigation graph with it.
    assert "function renderSpine" in _TPL
    assert "The investigation graph" in _TPL
