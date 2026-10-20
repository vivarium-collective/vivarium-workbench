"""LR<->TB investigation-graph orientation: auto-heuristic + manual toggle wiring.

walkthrough.js isn't node-requirable as a whole (no module.exports, needs a full
DOM) so _renderInvestigationDag's transpose logic is verified here by marker
presence (mirrors tests/test_b4_wiring.py's style for the same file). The pure
`chooseGraphOrientation` heuristic IS node-requirable (lives in aig-graph.js,
which already exports _layoutOptsForBand etc.) and has its own logic test at
tests/js/test_choose_graph_orientation.js (run with `node tests/js/...`).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()
AIG_JS = (ROOT / "vivarium_workbench/static/aig-graph.js").read_text()
HTML = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()


def dag_render_fn():
    """The _renderInvestigationDag function body, up to the next top-level fn."""
    i = JS.index("function _renderInvestigationDag(studies, chainsBySlug, studyEdges)")
    j = JS.index("window._renderInvestigationDag = _renderInvestigationDag;", i)
    return JS[i:j]


def test_choose_graph_orientation_helper_exists_and_is_exported():
    assert "function chooseGraphOrientation(depthCounts)" in AIG_JS
    assert "global.chooseGraphOrientation = chooseGraphOrientation;" in AIG_JS
    assert "chooseGraphOrientation: chooseGraphOrientation" in AIG_JS
    # heuristic branches per spec: shallow/wide -> TB, deep/narrow -> LR
    assert "return 'TB'" in AIG_JS
    assert "return 'LR'" in AIG_JS
    assert "maxDepth <= 2" in AIG_JS
    assert "maxBreadth > maxDepth" in AIG_JS
    assert "maxDepth > maxBreadth" in AIG_JS


def test_render_dag_computes_and_applies_orientation():
    fn = dag_render_fn()
    # AUTO default comes from the container aspect (_aigAutoOrient); the pure shape
    # helper is only the last-resort fallback when nothing can be measured.
    assert "_aigAutoOrient()" in fn
    assert "window.chooseGraphOrientation" in fn
    assert "depthCounts" in fn
    # A transient in-memory manual override wins over auto when present.
    assert "_aigManualOrient === 'LR' || _aigManualOrient === 'TB'" in fn
    # both orientation branches are present in the layout + edge-drawing passes
    assert "orient === 'TB'" in fn
    assert fn.count("orient === 'TB'") >= 3, "expected TB branch in layout AND edge-drawing code"


def test_orientation_is_auto_by_aspect_with_in_memory_override():
    # Orientation is no longer persisted to localStorage — it is AUTO from the
    # container aspect, with a transient in-memory manual override.
    assert "aig-orientation" not in JS                    # no persistence key
    assert "function _graphOrientationKey" not in JS
    assert "function _getStoredGraphOrientation" not in JS
    # Aspect-derived auto: dock side (L/R → TB, T/B → LR) + inline wide → LR.
    assert "function _aigAutoOrient()" in JS
    assert "dock === 'left' || dock === 'right'" in JS and "return 'TB'" in JS
    assert "dock === 'top' || dock === 'bottom'" in JS and "return 'LR'" in JS
    assert "availW >= availH ? 'LR' : 'TB'" in JS
    # Manual override: in-memory, not persisted; exposed as before.
    assert "var _aigManualOrient = null" in JS
    assert "function _setGraphOrientation(o)" in JS
    assert "_aigManualOrient = o" in JS
    assert "localStorage" not in JS.split("function _setGraphOrientation(o)")[1].split("\n  function ")[0]
    assert "window._setGraphOrientation = _setGraphOrientation;" in JS
    # A dock switch re-autos (clears the override); helper exposed for graph-panel.js.
    assert "function _setGraphOrientationForDock(o)" in JS
    assert "window._setGraphOrientationForDock = _setGraphOrientationForDock;" in JS
    # _resetGraphOrientation kept (used on dock close) but no longer touches storage.
    assert "function _resetGraphOrientation()" in JS
    assert "window._resetGraphOrientation = _resetGraphOrientation;" in JS


def test_manual_toggle_control_wired_in_template():
    assert 'id="aig-orient-lr"' in HTML
    assert 'id="aig-orient-tb"' in HTML
    assert "window._setGraphOrientation('LR')" in HTML
    assert "window._setGraphOrientation('TB')" in HTML
    # No separate "auto" reset button — the dock switch IS the reset to auto.
    assert 'id="aig-orient-auto"' not in HTML
    assert "window._resetGraphOrientation()" not in HTML


def test_tb_edge_endpoints_use_vertical_flow_not_horizontal():
    fn = dag_render_fn()
    i = fn.index("Edges (drawn after positions are known)")
    edges = fn[i:]
    # TB connects parent bottom -> child top (y-based), LR connects
    # parent right -> child left (x-based) — both must be present.
    assert "y1 = pos[pn].y + pos[pn].h;" in edges  # TB: parent's bottom edge
    assert "x1 = pos[pn].x + CARD_W;" in edges     # LR: parent's right edge
