from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def test_card_single_click_opens_full_study():
    """A DAG card is opened with a SINGLE click straight to the full study view —
    no double-click, and no quick-look side-card drawer (both removed)."""
    # single click opens the full study
    i = JS.index("node.onclick = function()")
    block = JS[i:i + 200]
    assert "_openStudyInsideInvestigation(s.name)" in block
    # no double-click handler
    assert "node.ondblclick" not in JS
    # the graph no longer opens the quick-look side-card drawer
    assert "_openInvestigationDrawer('study', s)" not in JS
