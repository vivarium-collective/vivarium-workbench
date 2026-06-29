from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_drawer_element_present():
    html = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
    assert 'id="investigation-detail-drawer"' in html
    assert 'id="investigation-detail-drawer-body"' in html


def test_drawer_wired_in_walkthrough():
    js = (ROOT / "vivarium_dashboard/static/walkthrough.js").read_text()
    assert "function _openInvestigationDrawer(" in js
    assert "_openInvestigationDrawer('study'" in js or '_openInvestigationDrawer("study"' in js
    assert "aig-claim-row" in js            # claim-row click wiring
    assert "stopPropagation" in js          # row clicks don't trigger the card
