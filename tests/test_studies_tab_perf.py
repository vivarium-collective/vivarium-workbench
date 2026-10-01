"""The Studies tab + investigation drill-in rebuilt and re-scanned the whole study
set on each keystroke/render — the same O(N^2) `.find`-in-loop / no-debounce pattern
the left rail already fixed. These pin the main-content equivalents. Static
assertions (no browser): they guard the mechanism, not pixel timing.
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"
TEMPLATE = Path(__file__).parent.parent / "vivarium_workbench" / "templates" / "index.html.j2"


def _js():
    return (STATIC / "walkthrough.js").read_text()


def test_shared_identity_memoized_indexes_exist():
    js = _js()
    for fn in ("function _studyByName()", "function _isetByName()", "function _investigationForStudyMap()"):
        assert fn in js, f"missing shared index {fn}"
    # rebuild only when the underlying array reference changes (identity-keyed)
    assert "!== _sbnSrc" in js and "!== _ibnSrc" in js and "!== _ifsSrc" in js


def test_membership_lookups_are_o1_not_find_in_loops():
    js = _js()
    # the per-slug Array.find hotspots are gone from these functions
    assert "_investigations || []).find(function(s) { return s.name === slug" not in js
    # and the reverse lookup no longer scans every iset
    assert "_investigationForStudyMap()[slug]" in js
    assert "_isetByName()[inv]" in js and "_isetByName()[invName]" in js


def test_both_main_content_filters_are_debounced():
    js = _js()
    # Investigations filter: a debounced input wrapper calls the (immediate) filter
    assert "function _filterInvestigationsInput()" in js
    inp = js[js.index("function _filterInvestigationsInput()"):][:260]
    assert "setTimeout(" in inp and "clearTimeout(" in inp
    assert 'oninput="_filterInvestigationsInput()"' in TEMPLATE.read_text()
    # Studies-grid search: the delegated handler debounces the grid re-render
    seg = js[js.index("id === 'investigations-search'"):][:300]
    assert "setTimeout(" in seg and "clearTimeout(" in seg


def test_investigation_dag_is_memoized_by_identity():
    js = _js()
    assert "function _memoInvestigationDag(" in js
    assert "_memoInvestigationDag(window._investigations)" in js   # render uses the memo
    assert "!== _dagSrc" in js
