from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "vivarium_dashboard/static/walkthrough.js").read_text()


def test_card_double_click_opens_full_study():
    assert "node.ondblclick = function()" in JS
    # the dblclick handler opens the full study and dismisses the drawer
    i = JS.index("node.ondblclick = function()")
    block = JS[i:i + 400]
    assert "_openStudyInsideInvestigation(s.name)" in block
    assert "investigation-detail-drawer" in block  # dismiss the quick-look drawer
    # single-click still opens the quick-look drawer (unchanged)
    assert "_openInvestigationDrawer('study', s)" in JS
