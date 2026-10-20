from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_drawer_element_present():
    html = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
    assert 'id="investigation-detail-drawer"' in html
    assert 'id="investigation-detail-drawer-body"' in html


def test_dag_card_selects_then_opens_not_drawer():
    # A DAG node click SELECTS the study (first click) and OPENS it (second click
    # on the selected node). "Show full card" now EXPANDS the card IN PLACE (full
    # Asks/Finds/plan grow downward from the anchored top); the "Open study →"
    # affordance lives INLINE inside that expansion — NOT a floating pop-out and
    # NOT the quick-look side-card drawer (both are gone / unwired from the graph).
    js = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()
    assert "node.onclick = _aigNodeClick" in js
    assert "window._aigSelected" in js
    assert "_openStudyInsideInvestigation(s.name)" in js   # second click opens
    assert "aig-full-details" in js                         # inline expansion block
    assert "aig-open-study" in js                           # open-study button is inline
    assert "_aigShowCardPopout" not in js                   # floating pop-out removed
    assert "aig-card-popout" not in js                      # ...and its modal element
    assert "_openInvestigationDrawer('study', s)" not in js
    assert "aig-claim-row" in js            # claim-row rendering still present
    assert "stopPropagation" in js          # row clicks don't double-trigger the card


def test_intro_is_inquiry_brief():
    html = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
    # The opening is no longer a collapsed <details>; it's an always-visible
    # inquiry brief rendered by JS into #investigation-detail-description.
    assert 'id="investigation-intro-details"' not in html
    assert 'id="investigation-detail-description"' in html
    assert 'inv-brief-host' in html  # the brief's mount point
