from pathlib import Path

import vivarium_workbench
from vivarium_workbench.lib import static_serving
from vivarium_workbench.lib.study_page import render_study_detail_html

TPL = static_serving.TEMPLATES_DIR / "study-detail.html"


def _template_text():
    # study-detail.html lives next to the package templates
    p = Path(vivarium_workbench.__file__).parent / "templates" / "study-detail.html"
    return p.read_text(encoding="utf-8")


def test_runs_tab_has_no_remote_run_form():
    """Task 8 (Simulations declutter): the remote-run (smsvpctest) form/panel
    was removed from the Simulate tab by design — launching now lives
    entirely in the header buttons (#study-reproduce /
    #study-run-current-spec). Its markup must be fully absent from the
    template source. (The thin-client JS handlers were left dormant in
    study-detail.js pending a decision on whether remote-run gets a header
    entry point; Fable A #7 resolved that by deleting them as dead code —
    see test_js_has_no_remote_run_handlers below.)"""
    t = _template_text()
    assert 'id="remote-run-form"' not in t
    assert 'id="remote-run-panel"' not in t
    assert 'id="remote-run-progress"' not in t
    assert 'onsubmit="return _submitRemoteRun(event)"' not in t
    assert "Run on remote" not in t  # the old panel heading/button label


def _js_text():
    return (Path(vivarium_workbench.__file__).parent / "static" / "study-detail.js").read_text(encoding="utf-8")


def test_js_has_no_remote_run_handlers():
    """Fable A #7 (study-design-fable-pass §1.1-I / §6 #7): the WS1 two-phase
    remote-run thin client (build → poll → submit → poll → land) had zero
    live callers once its DOM anchors (#remote-run-panel/-btn/-progress) were
    removed from study-detail.html by Task 8 — `_initRemoteRunPinned` was
    still invoked from the template but its own `getElementById('remote-run-
    panel')` guard made it an unconditional no-op. Deleted as dead code,
    replacing the presence-assertions this test used to make (see git
    history for the prior `test_js_has_remote_run_handlers_and_endpoints`).

    The docstring above left one thing open: "pending a decision on whether
    remote-run gets a header entry point." That decision is now made —
    #study-run-current-spec (the SAME single header button, not a new one;
    items 18/19 are about eliminating a run-button choice, not adding one
    back) is mode-aware: remote-pinned deployments dispatch via the minimal
    `_dispatchCurrentSpecBaseline`/`_dispatchRemotePinned` pair and
    `/api/remote-run-submit`, everything else keeps the local-engine path.
    This does NOT resurrect the old bloated thin client — none of the
    functions below came back, and `/api/remote-run-build`/`-land`/`-poll`
    are still unused; only `/api/remote-run-submit` (+`-config`, to read
    pinned status) is now a legitimate, deliberate caller."""
    # The remote launch UI now lives in sms-launch.js (quarantined SMS UI);
    # check the two files together so neither reintroduces the old client.
    js = _js_text() + (Path(vivarium_workbench.__file__).parent / "static" / "sms-launch.js").read_text(encoding="utf-8")
    for name in (
        "_submitRemoteRun", "_pollBuild", "_pollRun", "_submitRun",
        "_landRemoteRun", "_renderRemoteRunProgress", "_initRemoteRunPinned",
        "_renderRemoteRunProgressLegacy", "_rrDeriveStages", "_remoteRunState",
    ):
        assert name not in js, f"{name} should have been deleted as dead code"
    # the OLD thin-client's other endpoints are still unused (backend routes
    # may still exist for other callers — not asserted here)
    assert "/api/remote-run-build" not in js
    assert "/api/remote-run-land" not in js
    assert "/api/remote-run-poll" not in js
    # /api/remote-run-submit is now a deliberate, minimal caller (see above) —
    # confirm it's the mode-aware dispatch, not the old thin client, calling it
    assert "_dispatchCurrentSpecBaseline" in js
    assert "_dispatchRemotePinned" in js
    assert "/api/remote-run-submit" in js


def test_rendered_study_detail_has_no_remote_run_panel():
    # Same contract as test_runs_tab_has_no_remote_run_form, exercised
    # through the real render path (render_study_detail_html) rather than
    # reading the template source directly.
    html = render_study_detail_html(Path("/"), "demo-study", {"name": "demo-study"})
    assert 'id="remote-run-form"' not in html
    assert "Run on remote" not in html
    assert 'id="remote-run-progress"' not in html


def _walkthrough_js_text():
    return (Path(vivarium_workbench.__file__).parent / "static" / "walkthrough.js").read_text(encoding="utf-8")


def test_study_detail_js_has_run_hash_handler():
    """study-detail.js must contain _applyRunHash and handle #run- fragments."""
    js = _js_text()
    assert "_applyRunHash" in js
    assert "'#run-'" in js or '"#run-"' in js
    assert "_setStudyTab" in js


def test_walkthrough_js_sim_row_opens_study_results():
    """walkthrough.js must route study-bearing runs to /studies/<slug>#run-<id>."""
    js = _walkthrough_js_text()
    assert "'/studies/'" in js or '"/studies/"' in js or "'/studies/' +" in js or '"/studies/" +' in js


def test_view_run_button_routes_to_visualizations_not_dead_route():
    """The per-run View button must open the Visualizations tab, NOT the dead
    /composite-explorer route (which 404s -> blank page)."""
    js = _js_text()
    assert "btn-view-run" in js
    assert "/composite-explorer?run_id=" not in js  # the broken target is gone
    assert "_setStudyTab('visualizations')" in js or '_setStudyTab("visualizations")' in js
