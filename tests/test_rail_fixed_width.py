"""The left rail defaults to EXPANDED (labelled sidebar) and has a FIXED width —
it is no longer drag-resizable. Static assertions (no browser): they guard the
behavior, replacing the retired drag-perf test whose feature was removed.
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent / "vivarium_workbench"
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"


def test_rail_defaults_to_expanded_on_first_visit():
    js = (STATIC / "walkthrough.js").read_text()
    block = js[js.index("function _vivRestoreRailState"):][:600]
    # First visit (no saved value) → NOT collapsed; only an explicit '1' collapses.
    assert "var collapsed = false;" in block
    assert "localStorage.getItem('vivarium.rail-collapsed') === '1'" in block


def test_rail_width_is_a_fixed_constant():
    js = (STATIC / "walkthrough.js").read_text()
    block = js[js.index("function _vivRailSavedWidth"):][:260]
    assert "return _RAIL_NORMAL;" in block  # no persisted/dragged width override


def test_rail_has_no_drag_resize_handle_or_logic():
    js = (STATIC / "walkthrough.js").read_text()
    index = (TEMPLATES / "index.html.j2").read_text()
    css = (STATIC / "style.css").read_text()
    # The drag handle element + its handlers are gone.
    assert "viv-rail-resize-handle" not in index
    assert "_vivRailResizeStart" not in js
    assert "_vivRailResizeReset" not in js
    # And the drag-only CSS is gone.
    assert "viv-rail-resizing" not in css
    assert "viv-rail-resize-handle" not in css
