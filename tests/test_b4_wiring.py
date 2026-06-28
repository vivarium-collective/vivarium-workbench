from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_index_loads_aig_graph_before_walkthrough():
    html = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
    assert "assets/aig-graph.js" in html
    assert html.index("assets/aig-graph.js") < html.index("assets/walkthrough.js")


def test_walkthrough_overlays_chains_as_superset_with_fallback():
    js = (ROOT / "vivarium_dashboard/static/walkthrough.js").read_text()
    # fetches the typed graph
    assert "/api/investigation-graph?investigation=" in js
    # EXTENDS the legacy renderer (superset) rather than replacing it
    assert "function _renderInvestigationDag(studies, chainsBySlug)" in js
    assert "_chainBlockHtml" in js
    # legacy renderer + its export preserved (no regression; fallback path present)
    assert "_renderInvestigationDag(d.studies || [])" in js
    assert "window._renderInvestigationDag = _renderInvestigationDag;" in js
