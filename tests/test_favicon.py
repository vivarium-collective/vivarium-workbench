"""The browser-tab favicon is the Vivarium mark, shipped as a real static file.

A ``data:`` URI favicon is unreliable for the TAB icon (Safari garbles/ignores
it, and the host-keyed favicon cache can't be busted without a URL). So the mark
is a real ``static/vivarium-favicon.png`` referenced by relative, versioned path,
with the full rel set. ``session-status.js`` captures that mark for the "ready"
state and swaps to inline-SVG marks only while a workspace env is preparing/failed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "vivarium_workbench"
TEMPLATE = ROOT / "templates" / "index.html.j2"
SESSION_JS = ROOT / "static" / "session-status.js"
FAVICON = ROOT / "static" / "vivarium-favicon.png"


def test_favicon_png_asset_is_shipped():
    assert FAVICON.is_file(), "static/vivarium-favicon.png missing"
    assert FAVICON.stat().st_size > 1000, "favicon png looks empty"


def test_template_ships_a_static_png_favicon_rel_set():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert 'rel="icon" type="image/png" href="assets/vivarium-favicon.png' in html
    assert 'rel="shortcut icon"' in html
    assert 'rel="apple-touch-icon"' in html
    # Must NOT ship a fragile data: URI as the resting favicon.
    assert 'rel="icon" type="image/png" href="data:' not in html


def test_session_status_ready_favicon_reuses_the_template_mark():
    js = SESSION_JS.read_text(encoding="utf-8")
    assert "_initialIcon" in js
    assert "ready: _initialIcon ||" in js


def test_set_favicon_treats_non_svg_marks_as_png():
    js = SESSION_JS.read_text(encoding="utf-8")
    # Only the inline-SVG status marks are svg+xml; the file-URL logo is png.
    assert 'link.type = "image/svg+xml";' not in js
    assert 'data:image/svg' in js
