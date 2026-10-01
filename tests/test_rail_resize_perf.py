"""The left-rail width drag must stay smooth even when the rail holds a large study
list (e.g. sms-ecoli). Writing --rail-w on every mousemove forces a synchronous reflow
per event; on a big rail that stutters. These pin the fix: the drag coalesces its style
writes to one per animation frame, and the study list's layout is contained during the
drag. Static assertions (no browser) — they guard the mechanism, not pixel timing.
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"


def test_rail_resize_drag_is_coalesced_to_one_write_per_frame():
    js = (STATIC / "walkthrough.js").read_text()
    start = js.index("function _vivRailResizeStart")
    block = js[start:start + 2000]
    # the move handler defers the width write to rAF instead of writing per mousemove
    assert "requestAnimationFrame(_flush)" in block
    assert "cancelAnimationFrame" in block                      # cancelled + flushed on mouse-up
    # the actual style write happens in the rAF flush, not directly in _move
    move = block[block.index("function _move"):block.index("function _up")]
    assert "_vivRailApplyWidth" not in move, "width write must be deferred to the rAF flush, not done per mousemove"


def test_study_list_layout_is_contained_during_the_drag():
    css = (STATIC / "style.css").read_text()
    assert "body.viv-rail-resizing-active #viv-rail-studies-section{ contain:layout; }" in css
