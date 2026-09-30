"""The AI panel can dock left / right / bottom, so nothing in the shell may assume the whole
viewport belongs to the content. These pin the audit's findings (docs/ai-chat.md, "Docking"):
a viewport-height iframe that forgets the bottom dock would silently overflow the pane.
"""
import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"
TEMPLATE = Path(__file__).parent.parent / "vivarium_workbench" / "templates" / "index.html.j2"


def test_every_viewport_height_calc_in_the_template_leaves_room_for_a_bottom_dock():
    html = TEMPLATE.read_text()
    calcs = re.findall(r"calc\(100vh[^)]*\)+", html)
    assert calcs, "expected the study/composite iframes to size against the viewport"
    missing = [c for c in calcs if "--viv-ai-bottom" not in c]
    assert not missing, f"viewport-height sites that ignore the bottom dock: {missing}"


def test_maximized_card_and_code_rail_clear_the_panel_in_every_dock():
    css = (STATIC / "style.css").read_text()
    card = css[css.index(".registry-entry-full.pcard-maximized{"):]
    card = card[:card.index("}")]
    assert "--viv-ai-left" in card and "--viv-ai-right" in card and "--viv-ai-bottom" in card
    opened = css[css.index("body.pcard-maximized.viv-code-open .registry-entry-full.pcard-maximized{"):]
    opened = opened[:opened.index("}")]
    assert "--viv-ai-rw" in opened                      # code rail pinned beside a right-docked panel


def test_embeds_refit_and_focus_mode_hides_the_panel():
    assert "viv:ai-layout" in (STATIC / "walkthrough.js").read_text()      # _fitEmbedToViewport refits on dock changes
    assert "--viv-ai-bottom" in (STATIC / "composite-card.js").read_text()
    chat_css = (STATIC / "chat.css").read_text()
    assert "body.focus-mode .viv-ai-panel" in chat_css                       # a pop-out window has no room for it


def test_the_panel_publishes_its_footprint_and_defaults_to_left():
    js = (STATIC / "chat.js").read_text()
    for var in ("--viv-ai-left", "--viv-ai-right", "--viv-ai-bottom", "--viv-ai-rw"):
        assert var in js
    assert "lsGet('viv.ai.dock', 'left')" in js
    assert "draggable', 'false'" in js and "pointercancel" in js            # the native link drag must never strand the ghost
