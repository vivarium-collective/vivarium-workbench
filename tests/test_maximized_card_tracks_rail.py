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
    # observes the rail so a rail resize/collapse tracks the card
    assert "_maxRailRO" in fn and ".observe(" in fn
    # during a rail-size change the card is moved with a compositor-only transform
    # (no reflow of the heavy maximized content), and the real re-fit is debounced
    # until the rail SETTLES — this is what keeps the rail drag smooth.
    assert "card.style.transform = 'translateX(" in fn
    assert "setTimeout(" in fn and "_publishRailRight()" in fn
    # the transform is cleared and the observer disconnected on restore (no leak)
    assert "_maxRailRO.disconnect()" in fn
    assert "'transform'" in fn


def test_maximized_left_edge_is_driven_by_the_rail_var():
    css = (STATIC / "style.css").read_text()
    rule = css[css.index(".registry-entry-full.pcard-maximized{"):]
    rule = rule[:rule.index("}")]
    assert "--vw-rail-right" in rule   # the var the ResizeObserver keeps fresh
