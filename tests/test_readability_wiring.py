from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_drawer_element_present():
    html = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
    assert 'id="investigation-detail-drawer"' in html
    assert 'id="investigation-detail-drawer-body"' in html


def test_dag_card_opens_full_study_not_drawer():
    # A DAG card click opens the full study directly; the quick-look side-card
    # drawer is no longer wired from the graph (single click, no double-click).
    js = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()
    assert "_openStudyInsideInvestigation(s.name)" in js
    assert "_openInvestigationDrawer('study', s)" not in js
    assert "aig-claim-row" in js            # claim-row rendering still present
    assert "stopPropagation" in js          # row clicks don't double-trigger the card


def test_intro_description_collapsed_by_default():
    html = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
    # the long description is wrapped in a collapsed <details> with a summary
    assert 'id="investigation-intro-details"' in html
    # the description container id is preserved (JS still targets it)
    assert 'id="investigation-detail-description"' in html
    i = html.index('id="investigation-intro-details"')
    j = html.index('id="investigation-detail-description"')
    assert i < j  # description lives inside the details wrapper
