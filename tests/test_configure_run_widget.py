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

def test_configure_run_routing_and_persist_present():
    js = _js()
    assert "_wireRun" in js
    # context-aware routing
    assert "/api/composite-test-run" in js          # ad-hoc
    assert "/api/study-run-baseline" in js or "/api/study-run-variant" in js  # study
    assert "_ctx()" in js or "ctxState" in js        # reads {target, study}
    assert "'study'" in js or '"study"' in js
    # durable persist actions
    assert "/api/save-run-as-variant" in js
    assert "/api/run-delete" in js
    # tolerant polling (WS1 pattern)
    assert "consecutiveErrors" in js or "setTimeout" in js
    assert ".catch(" in js
