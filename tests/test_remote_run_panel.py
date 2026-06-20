from pathlib import Path

from vivarium_dashboard import server

TPL = server.TEMPLATES_DIR / "study-detail.html" if hasattr(server, "TEMPLATES_DIR") else None


def _template_text():
    # study-detail.html lives next to the package templates
    p = Path(server.__file__).parent / "templates" / "study-detail.html"
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
    return (Path(server.__file__).parent / "static" / "study-detail.js").read_text(encoding="utf-8")


def test_js_has_remote_run_handlers_and_endpoints():
    js = _js_text()
    assert "_submitRemoteRun" in js
    assert "_pollRemoteRun" in js
    assert "_renderRemoteRunProgress" in js
    assert "/api/remote-run-start" in js
    assert "/api/remote-run-status" in js
    assert "window._submitRemoteRun" in js  # exposed for the inline onsubmit
    # login gate: a 401 from start must be handled explicitly
    assert "401" in js
    # poll cadence + terminal stop
    assert "2000" in js
    assert "'done'" in js or '"done"' in js


def test_rendered_study_detail_includes_remote_run_panel():
    # Render the template with a minimal spec; the panel is static markup so any
    # spec that renders should include it.
    html = server._render_study_detail_html("demo-study", {"name": "demo-study"})
    assert 'id="remote-run-form"' in html
    assert "Run on remote" in html
    assert 'id="remote-run-progress"' in html
