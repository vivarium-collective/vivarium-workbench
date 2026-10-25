"""The Studies | Argument | Narrative graph-view toggle is wired (RFC-0005).

study-detail/walkthrough JS isn't node-requirable as a whole, so — like the other
frontend tests — assert the source markers that wire the toggle: the toolbar
buttons, the registered script, the restore hook, and the two renderers.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMPL = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
WALK = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()
AG = (ROOT / "vivarium_workbench/static/argument-graph.js").read_text()


def test_toolbar_has_the_three_view_buttons():
    for vid in ("gview-studies", "gview-argument", "gview-narrative"):
        assert vid in TMPL
    assert "window._setGraphView" in TMPL
    assert "assets/argument-graph.js" in TMPL


def test_walkthrough_exposes_the_studies_restore_hook():
    assert "window._restoreStudiesDag" in WALK


def test_argument_graph_defines_toggle_and_both_renderers():
    assert "window._setGraphView" in AG
    assert "_renderArgument" in AG and "_renderNarrative" in AG
    # Argument reuses the DAG hosts; Narrative marks audit gaps inline.
    assert "investigation-dag-edges" in AG and "ag-gap" in AG
    # falls back to Studies on any failure (never leaves the graph broken)
    assert AG.count('_setGraphView("studies")') >= 2
