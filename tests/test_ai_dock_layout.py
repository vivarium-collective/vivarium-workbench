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


def test_the_panel_publishes_its_footprint_and_defaults_to_right():
    js = (STATIC / "chat.js").read_text()
    for var in ("--viv-ai-left", "--viv-ai-right", "--viv-ai-bottom", "--viv-ai-rw"):
        assert var in js
    assert "lsGet('viv.ai.dock', 'right')" in js                            # chat + code both default to the right dock
    assert "draggable', 'false'" in js and "pointercancel" in js            # the native link drag must never strand the ghost


def test_both_panels_default_to_the_right_dock():
    assert "lsGet('viv.ai.dock', 'right')" in (STATIC / "chat.js").read_text()
    assert "defaultDock: 'right'" in (STATIC / "process-code.js").read_text()


def test_the_rail_tab_and_the_panel_are_both_called_chat():
    html = (STATIC.parent / "templates" / "index.html.j2").read_text()
    rail = html[html.index('id="viv-ai-toggle"'):][:900]
    # the LEFT-RAIL TAB is "Chat" (pairs with the "Code" tab)
    assert 'title="Chat"' in rail and '<span class="viv-rail-link-label">Chat</span>' in rail
    chat_js = (STATIC / "chat.js").read_text()
    assert "el.toggle.title = 'Chat — click to toggle" in chat_js             # the tab's runtime tooltip
    # the panel header is "Chat" too (renamed from VivaChat)
    assert 'id="viv-ai-panel" class="viv-ai-panel" hidden aria-label="Chat"' in html
    assert '<span>Chat</span><span class="vp-spacer">' in chat_js             # panel header
    assert "<span>Chat</span>'" in chat_js                                     # drag ghost


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


# ── Pop-out to a separate window (Phase 2) ──

def test_panel_dock_can_pop_a_panel_into_its_own_window():
    js = (STATIC / "panel-dock.js").read_text()
    assert "function popout" in js and "function initPopout" in js
    assert "window.open(" in js
    assert "qs.set('popout'" in js
    assert "vivSession" in js and "qs.set('session'" in js     # carries the parent tab's workspace session
    assert "viv-popout" in js                                   # marks <body> in the popped window


def test_popout_window_seeds_the_parent_session_before_session_js():
    html = TEMPLATE.read_text()
    seed = html.index("sessionStorage.setItem('viv-session-id'")
    assert html.index("assets/session.js") > seed, "the session seed must run BEFORE session.js"
    # only acts on a popout window, never a normal load
    seed_block = html[seed - 200:seed + 80]
    assert "popout" in seed_block


def test_both_panels_expose_pop_out_inside_the_dock_menu():
    # Pop-out shares the dock menu now (one "move this panel" control), not a
    # separate header button.
    chat = (STATIC / "chat.js").read_text()
    assert "VivPanelDock.popout('chat')" in chat                # reached from the chat's dock menu
    assert 'data-act="popout"' not in chat                      # the standalone header button is gone
    code_js = (STATIC / "process-code.js").read_text()
    assert "function popout" in code_js and "VivPanelDock.popout('code'" in code_js
    assert "popout: popout" in code_js                          # passed into VivPanelDock.make → dock menu
    dock = (STATIC / "panel-dock.js").read_text()
    assert 'data-act="popout"' in dock and "opts.popout" in dock   # the shared dock menu's Pop out item
    html = TEMPLATE.read_text()
    assert "ProcessCode.popout()" not in html                   # no standalone ⧉ button in the code header
    assert "ProcessCode.browseMenu(this)" in html               # the code header's new Browse button


def test_popout_body_renders_only_that_panel_full_window():
    css = (STATIC / "style.css").read_text()
    assert "body.viv-popout" in css
    block = css[css.index("body.viv-popout-chat #viv-ai-panel,"):]
    block = block[:block.index("}")]
    assert "position:fixed" in block and "inset:0" in block     # fills the popped window


# ── Unified chat/code chrome ──

def test_code_head_reuses_the_chat_panel_chrome():
    html = TEMPLATE.read_text()
    assert 'class="viv-code-head vp-head"' in html               # same header bar as the chat
    head = html[html.index('class="viv-code-head vp-head"'):][:2000]
    # three .vp-icon controls: browse, dock, close — same chrome as the chat header
    # (pop-out moved into the dock menu).
    assert head.count('class="vp-icon"') >= 3
    assert "ProcessCode.browseMenu(this)" in head and "ProcessCode.dockMenu(this)" in head and "ProcessCode.toggle()" in head
    # the pop-out external-link glyph now lives in the shared dock menu (panel-dock.js)
    assert 'd="M14 3h7v7"' in (STATIC / "panel-dock.js").read_text()
    # the code rail carries the chat's --c-* design tokens so .vp-* renders identically
    assert ".viv-code-rail" in (STATIC / "chat.css").read_text().split("--c-bg")[0]


def test_both_panels_share_one_dock_menu():
    js = (STATIC / "panel-dock.js").read_text()
    assert "function openDockMenu" in js
    assert "vp-pop vp-dock-menu" in js and "vp-pop-item" in js    # chat's popover chrome
    assert "ALLOWED.map" in js                                    # dock sides (ALLOWED defaults to C.DOCKS)
    code_js = (STATIC / "process-code.js").read_text()
    assert "function dockMenu" in code_js and "dockMenu: dockMenu" in code_js
    assert "openDockMenu: openDockMenu" in js                     # exposed on the controller


def test_code_panel_can_browse_processes_and_composites():
    """The Code header has a Browse control that lists available Processes and
    Composites and opens the pick straight into the panel — no Registry trip."""
    html = TEMPLATE.read_text()
    assert "ProcessCode.browseMenu(this)" in html                 # the header's Browse button
    js = (STATIC / "process-code.js").read_text()
    assert "function browseMenu" in js and "browseMenu: browseMenu" in js
    # it loads both lists and opens either kind via the existing loaders
    assert "/api/registry" in js and "/api/composites" in js
    assert "openProcess(" in js and "openComposite(" in js
    assert "vp-browse-menu" in (STATIC / "style.css").read_text()  # styled dropdown


def test_studies_is_a_dockable_rail_tab_like_chat_and_code():
    """The 'Studies' rail tab toggles a dockable VivPanelDock panel (like Chat /
    Code / Graph), separate from the 'Investigations' overview page, and the
    study-browser section is hosted in it."""
    html = TEMPLATE.read_text()
    # Rename: the overview page tab is now 'Investigations' (still data-page=investigations).
    assert 'data-page="investigations"' in html and '>Investigations<' in html
    # New launcher tab + panel.
    assert 'id="viv-studies-toggle"' in html, "Studies rail launcher missing"
    assert 'id="viv-studies-panel"' in html and 'id="viv-studies-body"' in html
    # Both study tabs sit at the top of the nav, under Search.
    search_i = html.index('viv-rail-search-link')
    inv_i = html.index('data-page="investigations"')
    studies_i = html.index('id="viv-studies-toggle"')
    resources_i = html.index('id="rail-resources"')
    assert search_i < inv_i < studies_i < resources_i, "Investigations+Studies not under Search"

    js = (STATIC / "studies-panel.js").read_text()
    assert "VivPanelDock.make" in js and "key: 'studies'" in js
    assert "docks: ['left', 'right', 'bottom', 'top']" in js        # docks on any side
    assert "defaultDock: 'left'" in js
    # Default-open when the workspace has studies, until the user toggles it.
    assert "hasStudies" in js and "K_USERSET" in js
    # Pop-out is wired through panel-dock for the 'studies' kind.
    assert "kind === 'studies'" in (STATIC / "panel-dock.js").read_text()


def test_docked_panels_can_shrink_narrow_then_collapse():
    """Docked side panels shrink well below the old 340px floor (down toward the
    collapse threshold) so they can be dragged narrow and collapsed."""
    core = (STATIC / "chat-core.js").read_text()
    assert "Math.min(80, cap)" in core, "side-dock min width not lowered to ~80px"
    # Flex items must allow shrinking below content width.
    css = (STATIC / "style.css").read_text()
    assert ".viv-graph-panel, .viv-code-rail, #viv-ai-panel{ min-width:0; }" in css
    assert "min-width:0;" in css.split(".viv-studies-panel{")[1].split("}")[0]


def test_collapsed_rail_shows_hover_label_flyouts():
    """A fully-collapsed rail reveals each tab's name as a button-like flyout on
    hover (and lets it escape the rail's clipping)."""
    css = (STATIC / "style.css").read_text()
    assert ".viv-rail.viv-rail-collapsed .viv-rail-link:hover > .viv-rail-link-label" in css
    assert ".viv-rail.viv-rail-collapsed{ overflow:visible; }" in css


def test_study_stepper_is_always_present_and_steps_from_none():
    """The rail study stepper is always mounted (▼ opens the first study even with
    nothing selected) rather than only appearing once a study is open."""
    js = (STATIC / "walkthrough.js").read_text()
    assert "window._renderRailStudyStepper = _renderRailStudyStepper;" in js
    # stepping with nothing selected picks the first (▼) / last (▲) study
    assert "(i < 0) ? (dir < 0 ? leaves.length - 1 : 0)" in js


def test_studies_panel_title_banner_removed():
    """The Studies panel header no longer shows a redundant 'Studies' title."""
    html = TEMPLATE.read_text()
    head = html[html.index('class="viv-studies-head'):][:400]
    assert "viv-studies-title" not in head, "Studies panel still has a title banner"


def test_popout_returns_window_and_studies_routes_to_opener():
    """popout() returns the opened window (so the launcher can stay highlighted),
    and a popped-out Studies list routes study selection to the opener window."""
    assert "return window.open(url" in (STATIC / "panel-dock.js").read_text()
    wk = (STATIC / "walkthrough.js").read_text()
    assert "window.opener._openStudyEmbeddedNewTab(name, tab)" in wk


def test_search_and_stepper_share_one_row():
    """The Studies search field and the nav stepper sit on one compact bar (the
    stepper is appended into the search head), saving a row."""
    js = (STATIC / "walkthrough.js").read_text()
    assert "searchHead.appendChild(bar)" in js
    css = (STATIC / "style.css").read_text()
    assert ".viv-studies-body .viv-rail-studies-head{" in css
    assert "display:flex" in css.split(".viv-studies-body .viv-rail-studies-head{")[1].split("}")[0]


def test_dockable_panel_headers_reveal_on_hover():
    """studies / code / chat panels drop the full-width header bar; the dock/close
    controls float top-right and appear only on hover (saves the header row)."""
    css = (STATIC / "style.css").read_text()
    for sel in (".viv-studies-panel .vp-head", ".viv-code-rail .vp-head", "#viv-ai-panel .vp-head"):
        assert sel in css, f"{sel} float rule missing"
    # hidden by default, shown on hover/focus-within
    assert "opacity:0; pointer-events:none;" in css
    assert ".viv-studies-panel:hover .vp-head" in css and ".viv-code-rail:hover .vp-head" in css and "#viv-ai-panel:hover .vp-head" in css
    # only icon controls remain (title dropped)
    assert ".viv-studies-panel .vp-head > :not(.vp-icon)" in css
