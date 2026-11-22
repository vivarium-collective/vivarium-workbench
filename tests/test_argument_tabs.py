"""Argument/narrative live in investigation tabs, not a graph toggle (RFC-0005).

The claim graph proved hard to read, so the synthesis/narrative products moved
to readable tabs. Verdict + Narrative were highly overlapping (both render the
same RFC-0005 claim layer), so they are consolidated into one **Findings** tab
carrying both the claims list and the ordered reading. Biology is folded into
Background as a section. The graph area is the Studies DAG again (toggle
removed). Frontend assertions are on source markers, like the other frontend
tests.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMPL = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
WALK = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()
AG = (ROOT / "vivarium_workbench/static/argument-graph.js").read_text()


def test_findings_tab_is_the_narrative_only_no_duplicate_claims_no_badge():
    # Verdict + Narrative are one "Findings" tab; the standalone tabs are gone.
    assert "id:'findings', label:'Findings'" in WALK
    assert "id:'narrative', label:'Narrative'" not in WALK
    # The duplicate claims list was dropped — only the richer narrative mount is
    # emitted (the claims mount DIV is gone; its renderer stays in argument-graph.js).
    assert '<div id="inv-verdict-claims-mount"' not in WALK
    assert "inv-narrative-mount" in WALK
    # No tab badge on Findings (the verdict status shows on the headline pill inside).
    assert "label:'Findings', badge:" not in WALK
    # The verdict-headline + narrative freshness mounts are both kept.
    assert 'data-fresh-tab="verdict"' in WALK and 'data-fresh-tab="narrative"' in WALK


def test_background_tab_comes_before_findings():
    # Owner's order: context (Background) first, then the answer (Findings).
    assert WALK.index("tabs.push({ id:'background'") < WALK.index("tabs.push({ id:'findings'")


def test_biology_is_folded_into_background_not_its_own_tab():
    assert "id:'biology', label:'Biology'" not in WALK
    # biology renders as a section inside the Background tab body
    assert "inv-brief-biology" in WALK


def test_plan_progress_tab_has_no_badge():
    # "Plan progress" stands on its own; the evaluated/total tally lives in the board.
    assert "id:'plan', label:'Plan progress', html:'<div id=\"inv-plan-mount\">'" in WALK


def test_needs_attention_lists_only_flagged_items_with_fix_links_and_copy():
    # Only flagged checks render (passing ones roll into one muted line), and the
    # list is copyable (plain text) to hand straight to Claude.
    assert "flaggedChecks" in WALK
    assert "other check" in WALK                      # the "N other checks clear" rollup
    assert "function _naCopy(" in WALK and "window._naCopy = _naCopy" in WALK
    assert "Copy the list as plain text" in WALK


def test_graph_toggle_and_its_hooks_are_gone():
    assert "gview-argument" not in TMPL and "aig-view-group" not in TMPL
    # the toggle machinery is removed from both JS files
    assert "_setGraphView" not in AG and "_setGraphView" not in WALK
    assert "_restoreStudiesDag" not in WALK and "_graphViewMode" not in WALK


def test_argument_graph_fills_the_tab_mounts():
    assert "inv-narrative-mount" in AG and "inv-verdict-claims-mount" in AG
    assert "_renderNarrative" in AG and "_renderClaims" in AG
    assert "assets/argument-graph.js" in TMPL


def test_plan_and_decisions_mounts_and_renderers():
    # Plan-progress gets the formalized synthesis spine; Decisions gets derived items.
    assert "inv-plan-mount" in WALK and "inv-decisions-mount" in WALK
    assert "_renderGates" in AG and "_renderDecisions" in AG
