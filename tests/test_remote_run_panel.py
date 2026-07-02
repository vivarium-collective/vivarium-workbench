from pathlib import Path

import vivarium_workbench
from vivarium_workbench.lib import static_serving
from vivarium_workbench.lib.study_page import render_study_detail_html

TPL = static_serving.TEMPLATES_DIR / "study-detail.html"


def _template_text():
    # study-detail.html lives next to the package templates
    p = Path(vivarium_workbench.__file__).parent / "templates" / "study-detail.html"
    return p.read_text(encoding="utf-8")


def test_runs_tab_has_remote_run_form():
    t = _template_text()
    assert 'id="remote-run-form"' in t
    assert 'onsubmit="return _submitRemoteRun(event)"' in t
    assert 'name="num_generations"' in t
    assert 'name="num_seeds"' in t
    assert 'name="run_parca"' in t
    assert 'id="remote-run-progress"' in t
    assert "Run on remote" in t  # the panel heading/button label


def _js_text():
    return (Path(vivarium_workbench.__file__).parent / "static" / "study-detail.js").read_text(encoding="utf-8")


def test_js_has_remote_run_handlers_and_endpoints():
    js = _js_text()
    # WS1 two-phase thin-client handlers (build → poll → submit → poll → land)
    assert "_submitRemoteRun" in js
    assert "_pollBuild" in js
    assert "_pollRun" in js
    assert "_submitRun" in js
    assert "_landRemoteRun" in js
    assert "_renderRemoteRunProgress" in js
    # the four thin-client endpoints
    assert "/api/remote-run-build" in js
    assert "/api/remote-run-submit" in js
    assert "/api/remote-run-land" in js
    assert "/api/remote-run-poll" in js
    # exposed for inline onsubmit/onclick
    assert "window._submitRemoteRun" in js
    assert "window._landRemoteRun" in js
    # login gate + network handling
    assert "401" in js
    assert ".catch(" in js
    # phase transitions: build 'built' → submit; run 'done' → land; 'failed' stops
    assert "'built'" in js or '"built"' in js
    assert "'done'" in js or '"done"' in js
    assert "'failed'" in js or '"failed"' in js
    # transient-tunnel tolerance + queued label + reachable=false handling + sim surfaced
    assert "consecutiveErrors" in js
    assert "Queued" in js
    assert "reachable" in js
    assert "simulation_id" in js


def test_rendered_study_detail_includes_remote_run_panel():
    # Render the template with a minimal spec; the panel is static markup so any
    # spec that renders should include it.
    html = render_study_detail_html(Path("/"), "demo-study", {"name": "demo-study"})
    assert 'id="remote-run-form"' in html
    assert "Run on remote" in html
    assert 'id="remote-run-progress"' in html


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
