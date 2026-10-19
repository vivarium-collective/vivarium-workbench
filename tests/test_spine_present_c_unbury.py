"""Thread-C / Task 3 (C1c): un-bury the spine-critical content.

Purpose, the behavioral-tests summary, and the multi-axis status were
collapsed-by-default (<details>), and discovery implications were buried at the
BOTTOM of the Conclusions tab. C1c surfaces them: purpose + tests-summary become
visible blocks, the status panel opens by default, and discovery implications
move ABOVE the conclusion text. Genuinely-secondary content (key assumptions,
pipeline-gate internals) stays collapsible.

Structural (no JS harness): assert the markup.
"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_workbench"
_HTML = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")


def test_purpose_is_not_collapsed_by_default():
    # Promoted to a visible block, no longer a <details>/<summary>.
    assert '<h2 class="overview-label">Purpose</h2>' in _HTML
    assert '<summary class="overview-label">Purpose</summary>' not in _HTML


def test_behavioral_tests_summary_removed_from_overview():
    # Fable A #8: the "un-bury" premise (visible-not-collapsed) is moot — the
    # Overview tests-count strip was deleted outright, not un-buried; Tests
    # owns the count. See test_study_detail_render.py for the render-level
    # absence assertion.
    assert '<h2 class="overview-label">Behavioral tests</h2>' not in _HTML
    assert '<summary class="overview-label">Behavioral tests</summary>' not in _HTML


def test_headline_status_markers_visible_by_default():
    # The dense six-gate status ladder strip was removed from the header, and the
    # gate pill + readiness summary were replaced by the two-axis Progress ×
    # Verdict markers (continuity with the investigation-graph study cards),
    # filled client-side into #study-axis-markers. These absences are HEADER-scoped:
    # the 5-tab reorg re-added a lifecycle stepper (class="status-stepper") to the
    # Decide tab (a deliberate, user-requested plan view), so assert the header
    # itself is free of the old ladder rather than the whole document.
    header = _HTML[:_HTML.index('</header>')]
    assert 'class="status-detail-panel"' not in header
    assert 'class="status-stepper"' not in header
    assert 'id="header-audit-summary"' not in header
    # The markers are now lined up INLINE with the title (the standalone
    # study-status-row wrapper was removed), still inside the header.
    assert 'id="study-axis-markers"' in header


def test_discovery_implications_elevated_above_conclusion_text():
    di = _HTML.index('id="discovery-implications-section"')
    verdicts = _HTML.index('data-narrative-card="conclusion_verdicts"')
    # Discovery implications now render at the TOP of the Decide tab, above the
    # verdict form + the synthesised conclusion text.
    assert di < verdicts


def test_secondary_content_stays_collapsible():
    # Key assumptions + pipeline-gate internals remain <details>-collapsed.
    assert '<summary class="overview-label">Key assumptions</summary>' in _HTML
    assert '<summary class="overview-label">Pipeline gate</summary>' in _HTML
