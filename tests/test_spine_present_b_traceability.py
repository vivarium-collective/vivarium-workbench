"""Thread-B / Task 2 (B1): finding ↔ test ↔ run ↔ band traceability.

Structural tests (no JS harness): the report's `_renderFinding` and the study
page's finding cards must render `evidence.from_test` / `from_run` as clickable
anchors (not plain <code>), surface the dropped computed `divergence_factor`
and `provenance.run_ids` (linked), and inline the cited test's pass_if band.
"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_dashboard"


def test_report_finding_traceability():
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    # from_test is anchored via the prefix helper, not dead <code>.
    assert "_traceLink" in js
    assert "'#test-'" not in js  # built via the prefix helper, not literal
    assert "_traceLink('test'" in js  # the TEST reference is still anchored
    assert "finding-traceability" in js
    # The headline computed number is surfaced.
    assert "divergence_factor" in js
    assert "finding-divergence" in js
    assert "vs expected" in js
    # provenance.run_ids surfaced + the cited test's pass_if band inlined.
    assert "run_ids" in js
    assert "pass_if-band" in js
    # report test cards are anchor targets for from_test.
    assert "id=\"test-" in js or "'test-'" in js
    # The report has NO per-run rows, so run references must NOT be dangling
    # anchors — they are plain <code>, never href="#run-".  (The study page
    # keeps its #run- anchors; that is asserted separately.)
    assert 'href="#run-' not in js
    assert "'#run-'" not in js
    assert "#run-" not in js
    # ...while the resolvable #test- anchors are kept (built by the helper).
    assert "prefix + '-'" in js


def test_study_page_finding_traceability():
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    # from_test / from_run are clickable anchors to the test/run cards.
    assert 'href="#bt-{{ _ftok }}"' in html
    assert 'href="#run-{{ _rtok }}"' in html
    # The study page DOES emit the matching anchor targets, so its #run-/#bt-
    # links resolve (unlike the report, which has no per-run rows).
    assert 'id="run-' in html
    assert 'id="bt-' in html
    # divergence_factor + provenance.run_ids surfaced; pass_if band inlined.
    assert "divergence_factor" in html
    assert "finding-divergence" in html
    assert "run_ids" in html
    assert "pass_if-band" in html
