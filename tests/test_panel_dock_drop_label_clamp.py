"""The dockable-panel drop label must stay on-screen.

When dragging a dockable workbench panel, a pill label ("Stack above" / "Stack
below" / "Stack left" / "Stack right" / "New row" / "New column") appears centered
on the insert line via ``transform: translate(-50%,-50%)``. A line along a panel
edge near the viewport border pushed half the pill off-screen, so "Stack above" /
"Stack below" were clipped every time. The render path now clamps the label's
center so the whole pill stays within the viewport.

Static assertion over the source (no browser).
"""
from pathlib import Path

import vivarium_workbench


def test_drop_label_is_clamped_within_viewport():
    js = (Path(vivarium_workbench.__file__).parent / "static" / "panel-dock.js").read_text(
        encoding="utf-8"
    )
    anchor = js.index("lblEl.textContent = txt;")
    block = js[anchor:anchor + 900]
    # the label center is clamped against both the horizontal and vertical bounds,
    # accounting for the pill's measured half-size
    assert "getBoundingClientRect()" in block
    assert "innerWidth" in block and "innerHeight" in block
    assert "_lb.width / 2" in block and "_lb.height / 2" in block
    assert "Math.min(Math.max(" in block
