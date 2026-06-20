from vivarium_dashboard import server

TPL = server.TEMPLATES_DIR / "study-detail.html" if hasattr(server, "TEMPLATES_DIR") else None


def _template_text():
    # study-detail.html lives next to the package templates
    from pathlib import Path
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
