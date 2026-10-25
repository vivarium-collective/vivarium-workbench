"""The dockable "Registry" browser panel + the main "Process" page focus mode.

Static assertions (no browser) guarding the wiring: the panel lists the registry
and clicking a row focuses that item as a single full card in the Process view,
with a "back to full registry" escape (mirrors investigation detail <-> list).
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"
TEMPLATE = Path(__file__).parent.parent / "vivarium_workbench" / "templates" / "index.html.j2"


def test_registry_panel_js_is_a_vivpaneldock_browser():
    js = (STATIC / "registry-panel.js").read_text()
    assert "VivPanelDock.make" in js
    assert "key: 'registry'" in js
    # it BUILDS its own list from the registry data sources
    assert "DataSource" in js and "loadRegistry" in js and "loadComposites" in js
    # clicking a row focuses that item in the main view
    assert "_showRegistryCard" in js
    # public API + popout bootstrap hook
    assert "window.RegistryPanel" in js or "global.RegistryPanel" in js
    assert "_openRegistryPanel" in js


def test_template_has_registry_panel_and_launcher():
    t = TEMPLATE.read_text()
    assert 'id="viv-registry-toggle"' in t                       # rail launcher tab
    assert 'id="viv-registry-panel"' in t                        # the dockable aside
    assert 'id="viv-registry-body"' in t                         # list host
    assert ">Registry</span>" in t                               # the panel's rail label
    assert "assets/registry-panel.js" in t                       # script registered
    # main page relabeled Registry -> Process, and gains the single-card focus host
    assert ">Process</span>" in t
    assert 'id="registry-focus-host"' in t
    assert 'class="page registry-focus"' in t                    # defaults to focus mode


def test_walkthrough_defines_focus_and_back_to_full():
    js = (STATIC / "walkthrough.js").read_text()
    assert "function _showRegistryCard" in js
    assert "window._showRegistryCard = _showRegistryCard" in js
    assert "function _registryBrowseAll" in js
    assert "window._registryBrowseAll = _registryBrowseAll" in js
    # focus mode toggles the registry-focus class on the Process page
    assert "registry-focus" in js
    # reuses the proven single-card renderers
    assert "_renderCompositeCardFull" in js and "_renderRegistryEntryFull" in js


def test_style_has_panel_and_focus_mode():
    css = (STATIC / "style.css").read_text()
    assert ".viv-registry-panel{" in css
    assert ".viv-reg-row" in css                                 # list rows
    # focus mode hides the full-list browser and shows the single-card host
    assert "#page-modules.registry-focus" in css
    assert ".registry-focus-host" in css


def test_popout_supports_registry_kind():
    js = (STATIC / "panel-dock.js").read_text()
    assert "_openRegistryPanel" in js                            # initPopout branch
