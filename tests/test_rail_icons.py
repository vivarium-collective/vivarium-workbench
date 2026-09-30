"""The seven left-rail icons are multicolour (Fluent Emoji Flat, MIT), inlined in the template."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent / "vivarium_workbench"
TEMPLATE = (ROOT / "templates" / "index.html.j2").read_text()
TABS = ["workspace-inputs", "market", "modules", "investigations", "simulations", "visualizations"]


def _rail_svg(marker):
    m = next(m for m in re.finditer(r'<a [^>]*' + re.escape(marker) + r'[^>]*>', TEMPLATE)
             if "viv-rail-link" in m.group(0))
    start = TEMPLATE.index("<svg", m.end())
    return TEMPLATE[start:TEMPLATE.index("</svg>", start) + 6]


def test_each_of_the_seven_rail_tabs_has_a_multicolour_inline_glyph():
    markers = [f'data-page="{t}"' for t in TABS] + ['id="viv-ai-toggle"']
    assert len(markers) == 7
    for marker in markers:
        svg = _rail_svg(marker)
        assert "viv-rail-color" in svg, f"{marker}: not a colour glyph"
        fills = set(re.findall(r'fill="(#[0-9a-fA-F]{3,8})"', svg))
        assert len(fills) >= 2, f"{marker}: monochrome ({fills})"
        assert "currentColor" not in svg                   # they must not be tinted by the link colour
        assert 'aria-hidden="true"' in svg and 'viewBox="0 0 32 32"' in svg
        assert "id=" not in svg                             # inline SVG ids would collide across icons


def test_the_glyphs_are_distinct_and_the_icon_licence_ships_with_them():
    svgs = [_rail_svg(m) for m in [f'data-page="{t}"' for t in TABS] + ['id="viv-ai-toggle"']]
    assert len(set(svgs)) == 7
    lic = (ROOT / "static" / "ICONS-LICENSE.md").read_text()
    assert "MIT License" in lic and "Microsoft" in lic
    for glyph in ("books", "package", "puzzle-piece", "test-tube", "rocket", "bar-chart", "robot"):
        assert glyph in lic
