"""A dockable panel resizes down to a usable MINIMUM and then resists — it never shrinks into an
unusable sliver, and a normal resize can't trip the collapse-to-strip by accident.

This pins the fix for the Discovery-cycle panel feeling "glitchy": because it docks solo on the
right, its edge drag could collapse it (`canCollapse`), and without a resize floor it slivered down
to ~80px of clipped content before snapping to the 16px strip. The floor + the COLLAPSE_AT<MIN_OPEN
invariant make collapse a deliberate shove to within ~48px of the dock edge, matching the standard
Studies/Registry feel (which rarely collapse-on-drag only because they dock stacked, not solo).
"""
import re
from pathlib import Path

PANEL_DOCK = Path(__file__).parent.parent / "vivarium_workbench" / "static" / "panel-dock.js"


def _min_open(js: str) -> tuple[int, int]:
    m = re.search(r"var MIN_OPEN = isVertical\(dock\) \? (\d+) : (\d+)", js)
    assert m, "initResize must define a per-axis MIN_OPEN resize floor"
    return int(m.group(1)), int(m.group(2))  # (vertical, side)


def test_resize_resists_at_a_minimum_instead_of_slivering():
    js = PANEL_DOCK.read_text()
    vert, side = _min_open(js)
    # a usable floor — a panel narrower than this shows only clipped content
    assert side >= 120 and vert >= 100
    # the clamp substitutes the floor whenever the dragged size falls below it
    assert "raw < MIN_OPEN ? MIN_OPEN : raw" in js


def test_collapse_is_a_deliberate_shove_well_inside_the_floor():
    js = PANEL_DOCK.read_text()
    m = re.search(r"var COLLAPSE_AT = (\d+)", js)
    assert m, "panel-dock must define COLLAPSE_AT"
    collapse_at = int(m.group(1))
    vert, side = _min_open(js)
    # collapse fires only when the edge is dragged well past the resist floor, so a normal
    # resize (which bottoms out at the floor) can never trip it.
    assert collapse_at < min(vert, side)
    # and it is still gated on the solo-occupant check (collapsing one of a split is ambiguous)
    assert "canCollapse && raw < COLLAPSE_AT" in js
