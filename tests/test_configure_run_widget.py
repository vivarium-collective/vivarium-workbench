from pathlib import Path
from vivarium_dashboard import server

def _js():
    return (Path(server.__file__).parent / "static" / "configure-run.js").read_text(encoding="utf-8")

def test_configure_run_form_generation_present():
    js = _js()
    assert "window.ConfigureRun" in js
    assert "function mount" in js or "mount:" in js
    assert "_buildConfigForm" in js and "_collectOverrides" in js
    assert "/api/composite-resolve" in js
    # type-driven inputs: number for float/int, checkbox for bool, text for string
    assert "'number'" in js or '"number"' in js
    assert "checkbox" in js
    # handles null/empty parameters without crashing
    assert "parameters || {}" in js or "|| {}" in js
    # collects overrides with type casting
    assert "parseFloat" in js or "Number(" in js
    assert "parseInt" in js
