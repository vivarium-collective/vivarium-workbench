from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_drawer_element_present():
    html = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
    assert 'id="investigation-detail-drawer"' in html
    assert 'id="investigation-detail-drawer-body"' in html


def test_dag_card_opens_full_study_not_drawer():
    # A DAG node click toggles expand/collapse; the full study is opened from the
    # node's inline button (guarded with stopPropagation so it doesn't also
    # toggle). The quick-look side-card drawer is no longer wired from the graph.
    js = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()
    # Click expands at higher detail (_toggleExpand) and opens the study at the
    # lowest detail (names-only, band 0); the ↗ button opens it at higher detail.
    assert "node.onclick = _namesOnly" in js
    assert "_toggleExpand" in js
    assert "event.stopPropagation(); _openStudyInsideInvestigation(" in js
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
