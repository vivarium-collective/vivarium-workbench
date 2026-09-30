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


def test_the_panel_and_its_rail_tab_are_called_viva():
    html = (STATIC.parent / "templates" / "index.html.j2").read_text()
    rail = html[html.index('id="viv-ai-toggle"'):][:900]
    assert 'title="VivaChat"' in rail and '<span class="viv-rail-link-label">VivaChat</span>' in rail
    assert 'id="viv-ai-panel" class="viv-ai-panel" hidden aria-label="VivaChat"' in html
    chat_js = (STATIC / "chat.js").read_text()
    assert '<span>VivaChat</span><span class="vp-spacer">' in chat_js         # panel header
    assert "<span>VivaChat</span>'" in chat_js                                 # drag ghost
    assert "el.toggle.title = 'VivaChat — click to toggle" in chat_js          # the rail tab's tooltip (set at runtime)


# ── The process-code panel is a dockable panel too, built on the same shared engine ──

def test_shared_panel_dock_engine_exists_and_reuses_the_dock_math():
    js = (STATIC / "panel-dock.js").read_text()
    assert "VivPanelDock" in js and "function make" in js
    assert "'--viv-' + key + '-' + suffix" in js                # per-key footprint vars on <html>
    for call in ("V('left')", "V('right')", "V('rw')", "V('bottom')"):
        assert call in js
    assert "C.dropZone" in js and "C.clampDock" in js           # reuses chat-core's dock math
    assert "draggable', 'false'" in js and "pointercancel" in js   # same anti-strand guards as the chat


def test_process_code_panel_is_dockable_via_the_shared_engine():
    js = (STATIC / "process-code.js").read_text()
    assert "VivPanelDock.make" in js
    assert "key: 'code'" in js
    assert "resizeHandle: 'viv-code-resize-handle'" in js
    assert "dragHandles: ['.viv-code-head']" in js


def test_code_rail_has_a_left_rail_launcher_and_no_right_edge_tab():
    html = TEMPLATE.read_text()
    assert 'id="viv-code-toggle"' in html                       # launcher lives on the left nav rail
    assert 'id="viv-code-edge"' not in html                     # the right-edge tab is gone
    assert "viv-code-collapsed" not in html
    assert 'id="viv-code-rail" class="viv-code-rail" aria-label="Process code" hidden' in html


def test_code_rail_docks_in_css_and_layout_clears_a_bottom_code_dock():
    css = (STATIC / "style.css").read_text()
    assert ".viv-code-rail[hidden]" in css
    assert '.viv-code-rail[data-dock="left"]' in css
    assert '.viv-code-rail[data-dock="bottom"]' in css
    # only a RIGHT-docked rail is pinned fixed beneath a maximized card
    assert 'body.pcard-maximized.viv-code-open .viv-code-rail[data-dock="right"]' in css
    # every viewport-height iframe leaves room for a bottom-docked code panel too
    # (full expression, incl. nested var() parens, up to the style-attr delimiter)
    html = TEMPLATE.read_text()
    calcs = re.findall(r"calc\(100vh[^;\"']*", html)
    missing = [c for c in calcs if "--viv-code-bottom" not in c]
    assert not missing, f"100vh sites that ignore a bottom code dock: {missing}"
    # embeds refit when ANY dockable panel (chat OR code) changes
    assert "viv:panel-layout" in (STATIC / "walkthrough.js").read_text()
    assert "--viv-code-bottom" in (STATIC / "composite-card.js").read_text()


def test_dock_core_loads_before_the_panels_that_build_on_it():
    html = TEMPLATE.read_text()
    i_core = html.index("assets/chat-core.js")
    i_dock = html.index("assets/panel-dock.js")
    i_code = html.index("assets/process-code.js")
    assert i_core < i_dock < i_code, "chat-core + panel-dock must load before process-code.js"
