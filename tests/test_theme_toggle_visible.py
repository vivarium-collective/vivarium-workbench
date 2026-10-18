"""The light/dark theme toggle in the rail footer is HOVER-REVEALED (per an
explicit user request): hidden by default to keep the footer uncluttered, and
shown on hover of the footer (or when the toggle itself is keyboard-focused).

This test guards that the hiding is always PAIRED with a reveal rule, so the
control is never *permanently* hidden / unreachable (the earlier failure mode),
and that it is hidden via opacity (recoverable) rather than display:none /
visibility:hidden.
"""
from __future__ import annotations

import re
from pathlib import Path

import vivarium_workbench

_STATIC = Path(vivarium_workbench.__file__).parent / "static"


def test_theme_toggle_is_hover_revealed_not_permanently_hidden():
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    # A reveal rule must exist: footer hover OR toggle focus restores it.
    assert re.search(r"\.viv-rail-theme-footer:hover\s+\.viv-theme-toggle", css) or \
        re.search(r"\.viv-theme-toggle:focus-visible", css), \
        "theme toggle is hidden with no hover/focus reveal rule — it would be unreachable"
    # It must not be hard-hidden (display:none / visibility:hidden), which a plain
    # :hover reveal cannot restore cleanly.
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "viv-theme-toggle" in selector:
            flat = body.replace(" ", "")
            assert "display:none" not in flat and "visibility:hidden" not in flat, \
                f"theme toggle hard-hidden (use opacity so hover can reveal it): {selector.strip()}"
