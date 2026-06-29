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


def test_intro_description_collapsed_by_default():
    html = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
    # the long description is wrapped in a collapsed <details> with a summary
    assert 'id="investigation-intro-details"' in html
    # the description container id is preserved (JS still targets it)
    assert 'id="investigation-detail-description"' in html
    i = html.index('id="investigation-intro-details"')
    j = html.index('id="investigation-detail-description"')
    assert i < j  # description lives inside the details wrapper
