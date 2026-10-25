"""Argument/narrative live in investigation tabs, not a graph toggle (RFC-0005).

The claim graph proved hard to read, so the synthesis/narrative products moved
to readable tabs: a top-level Narrative tab next to Background, and a claims list
in the Verdict tab. The graph area is the Studies DAG again (toggle removed).
Frontend assertions are on source markers, like the other frontend tests.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMPL = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
WALK = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()
AG = (ROOT / "vivarium_workbench/static/argument-graph.js").read_text()


def test_narrative_is_a_top_level_tab_after_background():
    assert "id:'narrative', label:'Narrative'" in WALK
    assert "inv-narrative-mount" in WALK
    # the Narrative tab is pushed after the Background tab (sits next to it)
    assert WALK.index("id:'background'") < WALK.index("id:'narrative'")


def test_verdict_tab_carries_a_claims_mount():
    assert "inv-verdict-claims-mount" in WALK


def test_graph_toggle_and_its_hooks_are_gone():
    assert "gview-argument" not in TMPL and "aig-view-group" not in TMPL
    # the toggle machinery is removed from both JS files
    assert "_setGraphView" not in AG and "_setGraphView" not in WALK
    assert "_restoreStudiesDag" not in WALK and "_graphViewMode" not in WALK


def test_argument_graph_fills_the_tab_mounts():
    assert "inv-narrative-mount" in AG and "inv-verdict-claims-mount" in AG
    assert "_renderNarrative" in AG and "_renderClaims" in AG
    assert "assets/argument-graph.js" in TMPL
