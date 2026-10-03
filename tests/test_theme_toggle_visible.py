"""The light/dark theme toggle in the rail footer must always be visible —
it used to be ``opacity:0`` until the footer was hovered, which hid a core
control from anyone who didn't already know it was there."""
from __future__ import annotations

import re
from pathlib import Path

import vivarium_workbench

_STATIC = Path(vivarium_workbench.__file__).parent / "static"
_HIDING = re.compile(r"opacity\s*:\s*0\s*(?:;|$)|display\s*:\s*none|visibility\s*:\s*hidden")


def test_no_rule_hides_the_theme_toggle():
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    offenders = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "viv-theme-toggle" in selector and _HIDING.search(body):
            offenders.append(f"{selector.strip()} {{{body.strip()}}}")
    assert not offenders, f"a rule hides the theme toggle: {offenders}"
