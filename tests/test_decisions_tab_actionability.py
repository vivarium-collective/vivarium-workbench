"""Decisions-tab actionability: every decision entity is connected to its studies
(a clickable ref with live status + verdict) and carries the two in-tab actions —
▶ Run (re-run a study's baseline) and + Add study (seed a follow-up). Both reuse
the EXISTING global handlers + POST endpoints (_vivRunStudyFromRow →
/api/study-run-baseline, _openDagFollowupsPopover → /api/study-seed-followup), so
there is no new backend. Zone B (argument-graph.js, the discovery projection)
joins studies by name via the index walkthrough.js publishes on window.

walkthrough.js / argument-graph.js are not node-requirable (full-DOM), so these
are marker + `node --check` assertions in the repo's established style.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "vivarium_workbench/static"


def test_zone_a_study_index_and_ref_helpers():
    js = (STATIC / "walkthrough.js").read_text()
    for fn in ("function _decIndexStudies(", "function _decStudyRef(",
               "function _decRunBtn(", "function _decAddBtn(", "function _decPill("):
        assert fn in js, f"missing helper {fn}"
    # The index is published on window so Zone B (a separate module) can join on it.
    assert "window._invDecStudyIndex = idx;" in js
    assert "window._decStudyRefHtml = _decStudyRef;" in js
    # The tab builds the index BEFORE the cards render.
    assert "_decIndexStudies(d);" in js
    i = js.index("function _invDecisionTabHtml(")
    assert "_decIndexStudies(d);" in js[i:i + 400], "index must be built at the top of the tab"
    # Chips reuse the existing status + verdict meta — no parallel verdict logic.
    assert "_studyStatusMeta(s)" in js and "_verdictMeta(s)" in js


def test_zone_a_cards_wire_refs_and_run():
    js = (STATIC / "walkthrough.js").read_text()
    # Revision card links its source study and offers ▶ Run on it.
    assert "_decStudyRef(srcStudy)" in js
    assert "_decRunBtn(srcStudy)" in js
    # Proposed-input card links its related study (no longer bare text) + Run.
    assert "_decStudyRef(p.related_study)" in js
    assert "_decRunBtn(p.related_study)" in js
    # Expert card surfaces any study it names.
    assert "_invDecStudyIndex" in js and "_decStudyRef(n)" in js


def test_run_and_add_reuse_existing_handlers_no_new_endpoint():
    js = (STATIC / "walkthrough.js").read_text()
    # ▶ Run reuses the Studies-row handler; + Add reuses the follow-ups popover.
    assert "_vivRunStudyFromRow(event," in js
    assert "_openDagFollowupsPopover(" in js
    # The actionability helpers introduce NO fetch/endpoint of their own — they
    # only emit onclick strings that call the pre-existing global handlers.
    block = js[js.index("function _decIndexStudies("):js.index("function _decCardRecon(")]
    code = re.sub(r"//.*", "", block)  # drop line comments (they cite the endpoints)
    assert "apiFetch(" not in code and re.search(r"\bfetch\s*\(", code) is None, \
        "actionability helpers must not call the API directly — reuse the global handlers"


def test_zone_b_rows_link_targets_and_act():
    js = (STATIC / "argument-graph.js").read_text()
    assert "function _tgtHtml(" in js and "function _actBtn(" in js
    # Target cells join the study index walkthrough.js publishes.
    assert "window._decStudyRefHtml" in js
    # test → ▶ Run (study-run-baseline handler); branch → + Add study (seed popover).
    assert 'action === "test"' in js and "window._vivRunStudyFromRow" in js
    assert 'action === "branch"' in js and "window._openDagFollowupsPopover" in js
    # Actions are hidden in a read-only published snapshot (no backend).
    assert 'mode === "snapshot"' in js
    # The recommended-steps table carries the action column.
    assert "_actBtn(p.action, tgt)" in js
    assert "_tgtHtml(tgt)" in js


def test_zone_b_suggestions_link_parent_when_present():
    js = (STATIC / "argument-graph.js").read_text()
    # Suggestions link their parent study and offer + Add study only when a parent
    # exists (a bare awaiting-expert question has nothing to seed from).
    fn = js[js.index("function _pushSuggestions("):js.index("function _pushSuggestions(") + 700]
    assert "_tgtHtml(s.parent)" in fn
    assert 's.parent ? (" " + _actBtn("branch", s.parent))' in fn


def test_css_present():
    css = (STATIC / "style.css").read_text()
    for sel in (".dec-studyref", ".dec-pill", ".dec-act-btn", ".dec-run", ".dec-add"):
        assert sel in css, f"missing CSS {sel}"
    ag = (STATIC / "argument-graph.js").read_text()
    for sel in (".disc-act-btn", ".disc-run", ".disc-add", ".disc-tgt"):
        assert sel in ag, f"missing Zone B CSS {sel}"


def test_js_syntax():
    node = shutil.which("node")
    if not node:  # pragma: no cover
        pytest.skip("node not available")
    for f in ("walkthrough.js", "argument-graph.js"):
        subprocess.run([node, "--check", str(STATIC / f)], check=True)
