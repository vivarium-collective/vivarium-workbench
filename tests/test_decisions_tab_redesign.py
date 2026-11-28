"""The Decisions tab redesign: one uniform "Awaiting your decision" queue (Zone A),
a merged "Recommended next steps" list (Zone B), and a collapsed "Background audit"
(Zone C). Presentation only — every actionable card keeps its handler class,
data-attributes and <textarea>, so the POST contracts are untouched.

walkthrough.js / argument-graph.js are not node-requirable (full-DOM), so these
are marker + `node --check` assertions in the repo's established style.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "vivarium_workbench/static"


def test_zone_a_unified_card_builders_and_shell():
    js = (STATIC / "walkthrough.js").read_text()
    for fn in ("function _decCardShell(", "function _decCardRecon(",
               "function _decCardRevision(", "function _decCardProposed(",
               "function _decCardExpert("):
        assert fn in js, f"missing {fn}"
    # One unified header replaces the old per-section headers.
    assert "Awaiting your decision" in js
    for gone in ("ENGINE DECISIONS AWAITING YOU", "PROPOSED INPUTS AWAITING REVIEW",
                 "Expert decisions steering this campaign"):
        assert gone not in js, f"old Decisions section header still present: {gone!r}"


def test_zone_a_preserves_handler_contracts():
    js = (STATIC / "walkthrough.js").read_text()
    # Each card builder keeps the class its POST handler queries + the data-attrs.
    assert "'eng-decision dec-k-reconcile" in js and 'data-ekind="reconciliation"' in js
    assert "'eng-decision dec-k-revision'" in js and 'data-ekind="revision"' in js
    assert "'eng-decision dec-k-input'" in js and 'data-ekind="proposed-input"' in js
    assert "'inv-decision dec-k-expert'" in js and 'data-index="' in js and 'data-question="' in js
    # The four POST handlers remain wired on the cards' action buttons.
    for h in ("_resolveEngineReconciliation(this)", "_resolveEngineRevision(this,'approve')",
              "_resolveProposedInput(this,'accept')", "_resolveInvDecision(this,"):
        assert h in js, f"handler onclick missing: {h}"
    # The handlers themselves are unchanged (still present + exported).
    assert "function _postEngineDecision(" in js
    assert "window._invDecisionTabHtml = _invDecisionTabHtml;" in js


def test_zone_b_merges_the_three_opportunity_blocks():
    js = (STATIC / "argument-graph.js").read_text()
    assert "Recommended next steps" in js
    # The old redundant section headers are gone (merged into one list).
    assert "Most valuable to investigate" not in js
    assert "Decide — the next action" not in js and "Decide \\u2014 the next action" not in js


def test_zone_c_folds_derived_items_into_collapsed_audit():
    js = (STATIC / "argument-graph.js").read_text()
    i = js.index("function _renderDecisions(")
    body = js[i:i + 1600]
    assert '<details class="ag-audit">' in body, "derived items not wrapped in a collapsed <details>"
    assert "Background audit" in body
    # CSS for the collapsed disclosure exists.
    assert ".ag-audit{" in js and ".ag-audit>summary" in js


def test_js_syntax():
    node = shutil.which("node")
    if not node:  # pragma: no cover
        pytest.skip("node not available")
    for f in ("walkthrough.js", "argument-graph.js"):
        subprocess.run([node, "--check", str(STATIC / f)], check=True)
