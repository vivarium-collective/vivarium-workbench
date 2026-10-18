"""Study-spine reorg (Slice 1, spec §1/§3.2/§3.3/§3.4): the two study-level
bulk-download groups that Task E2 previously folded onto the Simulations tab
(analysis result files `#data-files` + the raw-simulation-data bulk
`#raw-data-list` / `#exports-downloads`) split into their own Evidence
panels: analysis result files -> Analyses (`data-kind="analyses"`), raw
simulation data -> Results (`data-kind="results"`). Same element ids so the
existing loaders (now `_loadAnalyses`, `_loadResults`, and unchanged
`_downloadAllRawExports`) bind unchanged; the loader triggers move from the
`simulate` tab-activation branch onto their own `analyses`/`results`
branches. The Readouts pointer repoints from
`_gotoStudyTab('simulate', 'exports-downloads')` to
`_gotoStudyTab('results', 'exports-downloads')`.

See docs/superpowers/specs/2026-08-16-study-spine-reorg-design.md (§3.2-3.4)
and docs/superpowers/plans/2026-08-16-study-spine-reorg.md (Task 1).
"""
from __future__ import annotations

import pathlib
import tempfile
from pathlib import Path

from vivarium_workbench.lib.study_page import render_study_detail_html

_PKG = Path(__file__).parent.parent / "vivarium_workbench"


def _render(spec_extra=None):
    spec = {"name": "s1"}
    if spec_extra:
        spec.update(spec_extra)
    with tempfile.TemporaryDirectory() as d:
        return render_study_detail_html(pathlib.Path(d), "s1", spec)


def _panel(html: str, kind: str) -> str:
    # Anchor on the panel's `id="panel-<kind>"`, not `data-kind="<kind>"` —
    # the `.study-pillar` tab BUTTON also carries `data-kind="<kind>"` and
    # appears earlier in the document, which would mis-anchor the slice.
    i = html.index('id="panel-%s"' % kind)
    nxt = html.find('class="study-tab-panel"', i + 10)
    return html[i: nxt if nxt != -1 else len(html)]


def _sim_subpanel(html: str, sub: str) -> str:
    # 5-tab layout: Results + Analysis are subpanels of the Simulations panel.
    i = html.index('id="sim-subpanel-%s"' % sub)
    # Subpanels are siblings inside #panel-simulate; slice to the next subpanel
    # or the next top-level tab panel.
    nxt = html.find('class="sim-subpanel"', i + 10)
    if nxt == -1:
        nxt = html.find('class="study-tab-panel"', i + 10)
    return html[i: nxt if nxt != -1 else len(html)]


# ---------------------------------------------------------------------------
# 5-tab layout: the analysis + raw-data groups live in the Simulations panel's
# Analysis / Results subpanels (ids preserved).
# ---------------------------------------------------------------------------

def test_analysis_files_group_in_analysis_subpanel():
    html = _render()
    analysis_sub = _sim_subpanel(html, "analysis")
    assert 'id="data-files"' in analysis_sub
    assert 'id="data-download-all"' in analysis_sub
    assert '/api/study-analysis-zip?study=s1' in analysis_sub
    runs_sub = _sim_subpanel(html, "runs")
    assert 'id="data-files"' not in runs_sub


def test_raw_data_bulk_group_in_results_subpanel():
    html = _render()
    results_sub = _sim_subpanel(html, "results")
    assert 'id="raw-data-list"' in results_sub
    assert 'id="exports-downloads"' in results_sub
    assert 'id="raw-data-download-all"' in results_sub
    assert 'onclick="_downloadAllRawExports()"' in results_sub
    runs_sub = _sim_subpanel(html, "runs")
    assert 'id="raw-data-list"' not in runs_sub


def test_simulate_panel_still_has_runs_table_and_run_detail():
    """Sanity: the runs table + run-detail mount live in the Runs subpanel."""
    html = _render()
    runs_sub = _sim_subpanel(html, "runs")
    assert 'id="study-sim-table"' in runs_sub
    assert 'id="study-run-detail"' in runs_sub


def test_simulations_subtabs_present():
    html = _render()
    sim_panel = _panel(html, "simulate")
    for sub in ("runs", "results", "analysis"):
        assert "_setSimSubtab('%s')" % sub in sim_panel
        assert 'id="sim-subpanel-%s"' % sub in sim_panel


def test_simulate_is_a_top_level_pillar():
    html = _render()
    assert 'data-kind="simulate"' in html and "_setStudyTab('simulate')" in html
    # Results/analyses are no longer top-level pillars (Visualizations is a pillar again).
    for gone in ("results", "analyses"):
        assert "_setStudyTab('%s')" % gone not in html
    assert "_setStudyTab('visualize')" in html


# ---------------------------------------------------------------------------
# JS: loaders fire from the Simulations tab activation (all subpanels load so a
# subtab switch never shows an empty box).
# ---------------------------------------------------------------------------

def _set_study_tab_body(js: str) -> str:
    i = js.index("function _setStudyTab(")
    j = js.index("window._setStudyTab", i)
    return js[i:j]


def test_loaders_trigger_on_simulate_tab_activation():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    body = _set_study_tab_body(js)
    k = body.index("kind === 'simulate'")
    block = body[k: k + 220]
    assert "_loadStudySims()" in block
    assert "_loadResults()" in block
    assert "_loadAnalyses()" in block
    # The removed per-tab branches are gone.
    assert "kind === 'analyses'" not in body
    assert "kind === 'results'" not in body


def test_loaders_no_longer_trigger_on_data_tab_activation():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    body = _set_study_tab_body(js)
    assert "kind === 'data'" not in body


# ---------------------------------------------------------------------------
# The Readouts pointer now targets the Simulations panel (Results subtab).
# ---------------------------------------------------------------------------

def test_readouts_pointer_targets_simulations_results():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    i = js.index("function _loadReadoutsDownloadPointer(")
    j = js.index("window._loadReadoutsDownloadPointer", i)
    body = js[i:j]
    assert "_gotoStudyTab(\\'simulate\\'" in body
    assert "exports-downloads" in body
    assert "_gotoStudyTab(\\'data\\'" not in body
    assert "_gotoStudyTab(\\'results\\'" not in body


def test_no_remaining_jump_targets_data_tab_for_downloads():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    assert "_gotoStudyTab(\\'data\\'" not in js
    assert "_gotoStudyTab('data'" not in js


# ---------------------------------------------------------------------------
# Out of scope: per-row run "Data" links + _showRunDetail untouched.
# ---------------------------------------------------------------------------

def test_per_row_data_links_and_show_run_detail_untouched():
    sim_table_js = (_PKG / "static" / "sim-table.js").read_text(encoding="utf-8")
    assert "/api/simulation-run-download?run_id=" in sim_table_js
    study_detail_js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    assert "function _showRunDetail(" in study_detail_js


def test_js_syntax_valid():
    import subprocess
    r = subprocess.run(
        ["node", "--check", str(_PKG / "static" / "study-detail.js")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
