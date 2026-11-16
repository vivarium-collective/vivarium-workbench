"""The left rail defaults to EXPANDED and is drag-resizable from its right edge:
drag it narrower to reclaim space, and all the way in to snap into the collapsed
icon bar. The widen cap is deliberately modest so the rail can't get "too wide"
(the old 560px cap did). Static assertions (no browser) — they guard the wiring.
"""
import re
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


def test_rail_has_a_drag_resize_handle_wired_to_the_resize_logic():
    index = (TEMPLATES / "index.html.j2").read_text()
    js = (STATIC / "walkthrough.js").read_text()
    assert 'id="viv-rail-resize-handle"' in index
    assert "_vivRailResizeStart(event)" in index   # onpointerdown
    assert "_vivRailResizeReset()" in index         # ondblclick resets
    assert "window._vivRailResizeStart" in js
    assert "window._vivRailResizeReset" in js


def test_saved_width_is_clamped_and_persisted():
    js = (STATIC / "walkthrough.js").read_text()
    block = js[js.index("function _vivRailSavedWidth"):][:400]
    # Reads the persisted width and clamps it into the allowed band.
    assert "localStorage.getItem('vivarium.rail-width')" in block
    assert "Math.min(_RAIL_MAX, Math.max(_RAIL_MIN, w))" in block


def test_widen_cap_stays_modest_so_the_rail_cannot_get_too_wide():
    js = (STATIC / "walkthrough.js").read_text()
    # The max width cap is well below the old 560px that felt "too wide".
    m = re.search(r"var _RAIL_MAX\s*=\s*(\d+)\s*;", js)
    assert m, "expected a _RAIL_MAX constant in walkthrough.js"
    assert int(m.group(1)) <= 360, "widen cap must stay modest (the rail got 'too wide' at 560)"


def test_drag_only_css_is_present():
    css = (STATIC / "style.css").read_text()
    assert ".viv-rail-resize-handle{" in css
    assert "body.viv-rail-resizing-active" in css
