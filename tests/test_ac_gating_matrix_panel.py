"""SP4a / Task 4: the AC → study gating-matrix panel.

The generated investigation report (`_buildInvestigationReportHtml`) gains an
AC → study gating-matrix panel: rows = acceptance criteria, columns = the
gating study (linked) + the computed result, and acceptance criteria with NO
`study:` link are FLAGGED ("no study linked — gap"). The panel renders
synchronously from `iset.acceptance_criteria` and is enriched from
`/api/linkage-index?investigation=<inv>` when the live endpoint is reachable
(tolerant — a failed/absent endpoint keeps the synchronous skeleton).

Structural (no JS harness): assert the markup/data references.
"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_workbench"
_JS = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")


def test_panel_present_and_built():
    assert "ac-gating-matrix" in _JS
    assert "_acGatingMatrixHtml" in _JS
    # Built from the authored acceptance criteria (carries the unkeyed gaps).
    assert "iset.acceptance_criteria" in _JS


def test_panel_fetches_linkage_index():
    assert "/api/linkage-index?investigation=" in _JS
    assert "ac_matrix" in _JS


def test_panel_flags_unlinked_acceptance_gap():
    assert "no study linked — gap" in _JS
    # The gap-count footnote.
    assert "have no study linked (gaps)" in _JS


def test_panel_rows_link_studies_and_show_results():
    # Linked criteria link to their per-study section + show a result badge.
    assert "#study-" in _JS
    assert "_acResultBadge" in _JS


def test_panel_inserted_into_report():
    assert "acGatingMatrixHtml" in _JS
