"""Wiring for the investigation Details panel's per-tab auto-fit + ▼ expand.

The Details panel (#investigation-intro) shows one brief tab at a time; the tabs
differ wildly in length, so a single fixed height leaves whitespace under short
tabs and cuts off long ones. `_autofitInvIntro` sizes the panel to the ACTIVE
tab (short → natural height; tall → capped + scroll), and the ▼ expand button
(`_toggleInvIntroExpand`) removes the cap to show the whole tab inline.

walkthrough.js isn't node-requirable (full-DOM, no exports), so these are
marker/positing assertions plus a `node --check` syntax gate — the same style as
tests/test_methods_card_wiring.py for walkthrough-side behavior.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "vivarium_workbench/static"
TEMPLATES = ROOT / "vivarium_workbench/templates"


def test_walkthrough_defines_and_exports_autofit():
    js = (STATIC / "walkthrough.js").read_text()
    for fn in ("function _autofitInvIntro(", "function _autofitInvIntroIfActive(",
               "function _toggleInvIntroExpand(", "function _syncInvIntroExpandBtn("):
        assert fn in js, f"missing {fn}"
    for exp in ("window._autofitInvIntro = _autofitInvIntro;",
                "window._toggleInvIntroExpand = _toggleInvIntroExpand;"):
        assert exp in js, f"missing export {exp}"
    # The cap is a viewport fraction so tall tabs scroll instead of dominating.
    assert "_invIntroCap" in js and "innerHeight" in js


def test_tab_switch_refits_and_clears_sticky_expand():
    js = (STATIC / "walkthrough.js").read_text()
    i_fn = js.index("function _invBriefTab(")
    body = js[i_fn:i_fn + 900]
    # Switching tabs clears the sticky "show everything" and re-fits.
    assert "_invExpandedFull = false" in body
    assert "_autofitInvIntro()" in body


def test_initial_render_fits_default_tab():
    js = (STATIC / "walkthrough.js").read_text()
    # The opening render fits the default (Overview) tab right after the brief HTML.
    i = js.index("leadEl.innerHTML = _renderInvOpening(d);")
    assert "_autofitInvIntro()" in js[i:i + 500]


def test_async_tab_fills_refit_when_active():
    js = (STATIC / "walkthrough.js").read_text()
    # Needs-attention body fill re-fits if that tab is on screen.
    i_na = js.index("m.innerHTML = _aigScanBodyHtml(d);")
    assert "_autofitInvIntroIfActive('attention')" in js[i_na:i_na + 200]
    # Validation body fill likewise.
    i_vp = js.index("m.innerHTML = _vpRender(d);")
    assert "_autofitInvIntroIfActive('validation')" in js[i_vp:i_vp + 200]


def test_template_has_expand_button_wired():
    html = (TEMPLATES / "index.html.j2").read_text()
    assert 'class="bar-expand-btn"' in html
    assert "_toggleInvIntroExpand" in html
    # It must not start a drag when clicked (sits on the drag bar).
    i = html.index('class="bar-expand-btn"')
    seg = html[i - 40:i + 300]
    assert 'onpointerdown="event.stopPropagation()"' in seg


def test_style_defines_expand_button():
    css = (STATIC / "style.css").read_text()
    assert ".bar-expand-btn" in css
    assert ".bar-expand-btn.on" in css  # the expanded-state marker


def test_tab_strip_has_top_collapse_control():
    # A collapse control at the TOP (in the tab strip) so the panel can be
    # collapsed without scrolling down to the drag-bar button.
    js = (STATIC / "walkthrough.js").read_text()
    i = js.index('class="inv-brief-tabs"')
    seg = js[i:i + 1600]
    assert "inv-brief-collapse" in seg, "no top collapse control in the tab strip"
    assert "_toggleInvOverview" in seg, "top collapse control not wired to the collapse toggle"
    css = (STATIC / "style.css").read_text()
    assert ".inv-brief-collapse" in css
    assert "margin-left: auto" in css[css.index(".inv-brief-collapse"):css.index(".inv-brief-collapse") + 200]


def test_walkthrough_node_syntax():
    node = shutil.which("node")
    if not node:  # pragma: no cover
        pytest.skip("node not available")
    subprocess.run([node, "--check", str(STATIC / "walkthrough.js")], check=True)
