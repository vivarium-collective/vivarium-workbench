"""Thread-C / Task 2 (C1b): de-duplicate follow-ups (3->1) + outcomes.

Follow-ups appeared in THREE places (Overview, the Conclusions follow_up_studies
block, and discovery_implications.followup_study_proposals). C1b keeps ONE
canonical surface — the Conclusions/Decide tab — where authored follow-ups and
discovered proposals render together, clearly distinguished and de-duplicated by
id; the Overview occurrence becomes a short link to it. The Conclusions "latest
run outcomes" raw per-test table defers to the canonical Tests tab (thread B).

Structural (no JS harness): assert the markup/data references.
"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_dashboard"
_HTML = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")


def test_overview_follow_ups_defer_to_conclusions():
    """The Overview follow-up occurrence is a short link to the canonical
    Decide/Conclusions surface — NOT a triplicated card list with seed buttons.
    The overview-specific seed button (data-followup-idx) is gone."""
    # The overview block links to the canonical surface.
    head = _HTML.index("Follow-up studies")
    overview_region = _HTML[head:head + 700]
    assert "_setStudyTab('conclusions')" in overview_region
    # The overview-specific seed button is removed (it lived only in Overview).
    assert "data-followup-idx" not in _HTML


def test_follow_ups_distinguished_authored_vs_discovered():
    """The canonical Conclusions surface labels the two field families:
    authored follow_up_studies vs discovered followup_study_proposals."""
    assert "(authored)" in _HTML
    assert "(discovered)" in _HTML


def test_discovered_proposals_deduped_by_id_against_authored():
    """Discovered proposals whose id matches an authored follow-up are skipped
    (no duplicate card)."""
    assert "_fup_ids" in _HTML
    # The proposals loop filters by the authored-id set.
    assert "followup_study_proposals if" in _HTML or "not in _fup_ids" in _HTML


def test_conclusions_outcomes_defer_to_tests_tab():
    """The Conclusions 'latest run outcomes' no longer dumps the raw per-test
    k:v table — it defers to the canonical Tests tab (thread B)."""
    idx = _HTML.index('<h3 class="overview-label">Latest run outcomes')
    region = _HTML[idx:idx + 1500]
    assert "_setStudyTab('tests')" in region
    # The raw per-test outcomes table loop is gone from this region.
    assert "_latest.outcomes.items()" not in region


def test_verdict_form_kept_in_conclusions():
    """The Decide-phase 3-track verdict form is kept, but post-consolidation
    (item 6) each track's *result* is COMPUTED (a read-only badge derived from
    the gate evaluator / run status / finding tiers) rather than a hand-entered
    select; the authored part is the per-track *basis* rationale. So the form
    surfaces all three tracks as computed badges plus basis inputs."""
    # Result is now a computed badge, not an editable `.result` input.
    assert 'data-verdict-track="regression_compatibility"' in _HTML
    assert 'data-verdict-track="biological_validation"' in _HTML
    assert 'data-verdict-track="explanatory_gain"' in _HTML
    # The authored basis inputs remain for each track.
    assert "conclusion_verdicts.regression_compatibility.basis" in _HTML
    assert "conclusion_verdicts.biological_validation.basis" in _HTML
    # The editable result selects were removed.
    assert "conclusion_verdicts.regression_compatibility.result" not in _HTML
