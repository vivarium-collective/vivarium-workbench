# tests/test_b4_wiring.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_index_loads_aig_graph_before_walkthrough():
    html = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
    assert "assets/aig-graph.js" in html
    assert html.index("assets/aig-graph.js") < html.index("assets/walkthrough.js")


def test_walkthrough_swaps_callsite_with_fallback():
    js = (ROOT / "vivarium_dashboard/static/walkthrough.js").read_text()
    assert "/api/investigation-graph?investigation=" in js
    assert "_renderAigGraph" in js
    # old renderer kept as fallback + still exported
    assert "window._renderInvestigationDag = _renderInvestigationDag;" in js
