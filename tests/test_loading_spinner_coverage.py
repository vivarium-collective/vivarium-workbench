"""Item 70: bare '<p class="empty-state">Loading…</p>' text (or equivalent
inline textContent) was left un-migrated to ProgressTrack.loading() at several
real, user-visible call sites even after phase 1's rollout — caught only by
the user's own manual browser testing (screenshots), not by any earlier
DOM-level check. Pins that every site the user's screenshots + a follow-up
audit found is now wired to the shared spinner, with a real fallback for the
(untriggered in practice) case ProgressTrack fails to load.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WALK_JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()
EXPLORER_JS = (ROOT / "vivarium_workbench/static/explorer.js").read_text()
HTML = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()


def test_workspace_inputs_default_landing_page_has_a_real_spinner():
    # The literal repro: the default landing page (#page-workspace-inputs,
    # "Resources" in the nav) still showed plain "Loading inputs…" with zero
    # spinner — never in phase 1's scope, and the template-level fix alone
    # was insufficient: _loadInputs() clobbers it with its own hardcoded
    # plain-text placeholder before the fetch even starts (see below).
    i = HTML.index('id="inputs-api-render"')
    block = HTML[i:i + 200]
    assert 'viv-loading' in block
    assert '<p class="muted" style="font-style:italic">Loading inputs' not in block


def test_load_inputs_does_not_clobber_the_template_spinner():
    i = WALK_JS.index('function _loadInputs')
    block = WALK_JS[i:i + 400]
    assert 'ProgressTrack.loadingHtml' in block
    assert "el.innerHTML = '<p class=\"muted\" style=\"font-style:italic\">Loading inputs" not in block


def test_registry_composite_and_process_tabs_have_real_spinners():
    # Modules -> Composites / Processes: eagerly-rendered tab panels with
    # their own static template placeholder (the other 5 tabs render lazily
    # with no placeholder at all, so nothing to fix there).
    for cid in ("registry-composites-container", "registry-processes-container"):
        i = HTML.index('id="' + cid + '"')
        block = HTML[i:i + 250]
        assert 'viv-loading' in block, cid


def test_market_and_composite_editor_loading_states_use_progresstrack():
    i = WALK_JS.index("function _loadMarket(force)")
    assert 'ProgressTrack.loadingHtml' in WALK_JS[i:i + 600]
    i2 = WALK_JS.index("ce-compare-body")
    assert 'ProgressTrack.loadingHtml' in WALK_JS[i2:i2 + 250]


def test_investigation_detail_initial_and_interventions_loading_use_progresstrack():
    i = WALK_JS.index("detail.style.display = '';")
    assert 'ProgressTrack.loadingHtml' in WALK_JS[i:i + 150]
    i2 = WALK_JS.index('id="inv-interventions-host"')
    assert 'ProgressTrack.loadingHtml' in WALK_JS[i2:i2 + 150]


def test_explorer_runs_list_loading_uses_progresstrack():
    i = EXPLORER_JS.index("Loading runs")
    block = EXPLORER_JS[max(0, i - 150):i + 50]
    assert 'ProgressTrack.loadingHtml' in block
