"""On a phone the dockable panels (Studies/Chat/Code/Graph) can't tile — each
opens as a full-screen sheet. mobile-panels.js adds one bottom bar that launches
AND switches them (one panel at a time), shown only for panels relevant to the
current page, and the sheet shrinks to sit above it. These pin that contract.
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"
TEMPLATE = Path(__file__).parent.parent / "vivarium_workbench" / "templates" / "index.html.j2"


def test_switcher_script_is_loaded_after_the_panels_it_drives():
    html = TEMPLATE.read_text()
    assert "assets/mobile-panels.js" in html
    # Must load after the panel controllers it toggles (studies-panel + mobile-nav).
    assert html.index("assets/studies-panel.js") < html.index("assets/mobile-panels.js")
    assert html.index("assets/mobile-nav.js") < html.index("assets/mobile-panels.js")


def test_switcher_knows_every_dockable_panel_and_how_to_toggle_it():
    js = (STATIC / "mobile-panels.js").read_text()
    # Each of the four panels is represented by its panel element id...
    for panel_id in ("viv-studies-panel", "viv-ai-panel", "viv-code-rail", "viv-graph-panel"):
        assert panel_id in js, panel_id
    # ...and driven through each controller's public toggle/open API.
    assert "StudiesPanel" in js and "ProcessCode" in js and "GraphPanel" in js
    assert "viv-ai-toggle" in js                      # chat has no API → click its launcher


def test_relevance_tracks_launcher_visibility_and_the_investigations_page():
    js = (STATIC / "mobile-panels.js").read_text()
    # Live-only panels (chat/code) are shown only when their rail launcher renders,
    # so they vanish in the read-only snapshot (which display:none's those toggles).
    assert "getClientRects" in js
    assert "viv-ai-toggle" in js and "viv-code-toggle" in js and "viv-studies-toggle" in js
    # Graph has no persistent launcher: relevant only on the investigations page.
    assert 'activePage() === "investigations"' in js


def test_one_panel_at_a_time_and_a_way_back_to_content():
    js = (STATIC / "mobile-panels.js").read_text()
    assert "function openOnly" in js and "function closeAll" in js
    # Content button carries the empty key; tapping it (or the open panel) closes all.
    assert 'btnHTML("", "Content"' in js


def test_the_bar_is_phone_only_and_sheets_leave_room_for_it():
    css = (STATIC / "responsive.css").read_text()
    # Hidden on desktop (outside the breakpoint), styled inside it.
    assert ".viv-mobile-panelbar { display: none; }" in css
    assert ".viv-mobile-panelbar {" in css
    # The full-screen panel sheets stop above the 56px bar (not inset bottom 0).
    assert "calc(56px + env(safe-area-inset-bottom))" in css
    # Page content is padded so nothing hides behind the bar when it's shown.
    assert "body.viv-has-panelbar .viv-content" in css


def test_dock_stacks_reserve_no_space_on_mobile_so_main_is_not_shoved():
    # A docked panel lives in its side's .vp-stack flex item, which on desktop
    # carries the panel's width/height. On a phone the panels are fixed-overlay
    # sheets, so the stack must collapse to zero — otherwise it pushes .viv-main
    # (and the mobile topbar inside it) sideways behind the open sheet. Collapsed
    # but NOT display:none (that would hide the fixed panel child).
    css = (STATIC / "responsive.css").read_text()
    block = css[css.index(".vp-stack {"):]
    block = block[:block.index("}")]
    assert "flex: 0 0 0 !important" in block
    assert "width: 0 !important" in block and "height: 0 !important" in block
    assert "display: none" not in block
