"""The VivaChat panel has a solid, high-contrast frame so its edge is visible against the page (every dock; the
pop-out window is frameless). The colour is the workbench's own accent (no new colour); its contrast against the panel
surface is computed here from the stylesheet itself, in both themes, against WCAG's 3:1 minimum for UI boundaries.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "vivarium_workbench" / "static"
CHAT = (STATIC / "chat.css").read_text(encoding="utf-8")
STYLE = (STATIC / "style.css").read_text(encoding="utf-8")


def _lum(hex_):
    h = hex_.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _block(css, selector_start):
    i = css.index(selector_start)
    return css[i:css.index("}", i)]


def _hex_in(text, var):
    m = re.search(rf"{re.escape(var)}\s*:\s*(?:var\([^,)]+,\s*)?(#[0-9a-fA-F]{{6}})", text)
    assert m, f"{var} not found in: {text[:200]}"
    return m.group(1)


def test_the_frame_colour_is_a_token_that_follows_the_accent_in_both_themes():
    light = _block(CHAT, ".viv-ai-panel, .vp-pop, .viv-code-rail{")
    assert re.search(r"--c-frame\s*:\s*var\(--c-accent\)", CHAT), "frame must be the workbench accent, not a new colour"
    assert "--c-accent" in light


def test_the_frame_contrasts_with_the_panel_surface_in_both_themes():
    light = _block(CHAT, ".viv-ai-panel, .vp-pop, .viv-code-rail{")
    dark = _block(CHAT, ':root[data-theme="dark"] .viv-ai-panel')
    assert _contrast(_hex_in(light, "--c-accent"), _hex_in(light, "--c-bg")) >= 3.0
    assert _contrast(_hex_in(dark, "--c-accent"), _hex_in(dark, "--c-bg")) >= 3.0


def test_the_old_hairline_border_would_not_have_passed():
    """What this guards against: the previous edge colour is invisible against the surface (< 3:1)."""
    light = _block(CHAT, ".viv-ai-panel, .vp-pop, .viv-code-rail{")
    assert _contrast(_hex_in(light, "--c-border"), _hex_in(light, "--c-bg")) < 3.0


def test_the_panel_draws_the_frame_on_top_without_changing_its_size():
    rule = _block(CHAT, ".viv-ai-panel{")
    assert re.search(r"outline\s*:\s*2px solid var\(--c-frame\)", rule)
    assert re.search(r"outline-offset\s*:\s*-2px", rule)       # inside the box: no layout shift, flex-basis untouched


def test_the_popout_window_is_frameless():
    i = STYLE.index("body.viv-popout-chat #viv-ai-panel")
    assert re.search(r"outline\s*:\s*(0|none)", STYLE[i:i + 600])
