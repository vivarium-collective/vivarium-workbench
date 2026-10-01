"""The left-rail study list is rebuilt per event (filter keystroke, pin, drag,
investigation switch). On large workspaces (e.g. sms-ecoli) that rebuild was the
root of the general sluggishness. These pin the surgical fixes. Static assertions
(no browser) — they guard the mechanism, not pixel timing.
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"


def _walkthrough():
    return (STATIC / "walkthrough.js").read_text()


def test_study_filter_shows_hides_instead_of_rebuilding():
    js = _walkthrough()
    start = js.index("window._filterRailStudies = function")
    block = js[start:start + 400]
    assert "_railStudyQuery" in block                           # query recorded
    assert "_applyRailStudyFilter()" in block                   # filters in place
    assert "_renderRailInvestigationGroups" not in block        # NOT a rebuild per keystroke
    # the in-place filter toggles a hidden class on existing rows (no innerHTML churn)
    assert "classList.toggle('viv-rail-hidden'" in js
    # and reuses the existing match engine over the stamped per-row haystack
    assert "data-rail-hay" in js and "_tokensMatch(" in js


def test_membership_resolution_is_indexed_not_quadratic():
    js = _walkthrough()
    assert "_studyByName[s.name] = s" in js                     # build the index once
    assert "return _studyByName[slug]" in js                    # O(1) lookup per membership slug
    # the old O(N^2) per-slug scan is gone
    assert "_investigations.find(function(s) { return s.name === slug" not in js


def test_reorder_dragover_is_coalesced_to_one_reflow_per_frame():
    js = _walkthrough()
    start = js.index("function _wireSortable")
    block = js[start:start + 2600]
    assert "requestAnimationFrame(" in block and "cancelAnimationFrame(" in block


def test_off_screen_study_rows_use_content_visibility():
    css = (STATIC / "style.css").read_text()
    assert "content-visibility: auto" in css
    assert "contain-intrinsic-size" in css
