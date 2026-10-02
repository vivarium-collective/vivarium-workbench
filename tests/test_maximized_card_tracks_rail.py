"""A maximized ("fill the pane") composite card is pinned with
`left: calc(var(--vw-rail-right) + …)`. That var is republished by
_positionMaximizedCard, which fires on window resize but NOT when the LEFT RAIL is
resized (drag) or collapsed — so the card used to stop tracking the menu. The fix
observes the rail's size while a card is maximized. Static assertions (no browser).
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"


def test_maximized_card_observes_the_rail_and_cleans_up():
    js = (STATIC / "composite-card.js").read_text()
    fn = js[js.index("function _toggleCardMaximize"):]
    fn = fn[:fn.index("window._toggleCardMaximize")]
    # observes the rail so a rail resize/collapse re-fits the card
    assert "ResizeObserver" in fn and ".viv-rail" in fn
    assert "_maxRailRO" in fn and ".observe(" in fn
    # and disconnects the observer when the card is restored (no leak)
    assert "_maxRailRO.disconnect()" in fn


def test_maximized_left_edge_is_driven_by_the_rail_var():
    css = (STATIC / "style.css").read_text()
    rule = css[css.index(".registry-entry-full.pcard-maximized{"):]
    rule = rule[:rule.index("}")]
    assert "--vw-rail-right" in rule   # the var the ResizeObserver keeps fresh
