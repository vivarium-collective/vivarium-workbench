"""The browser-tab favicon is the Vivarium mark.

The server renders a PNG data-URI ``<link rel="icon">`` into the page head, and
``session-status.js`` restores that same mark for the "ready" state (swapping to
the materializing/failed marks only while a workspace env is preparing/failed).
These pin that wiring so the tab icon doesn't silently regress to a lettermark.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "vivarium_workbench"
TEMPLATE = ROOT / "templates" / "index.html.j2"
SESSION_JS = ROOT / "static" / "session-status.js"


def test_template_ships_a_png_favicon_link():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert 'rel="icon"' in html, "no favicon <link> in the page head"
    assert 'type="image/png"' in html
    assert 'href="data:image/png;base64,' in html, "favicon is not an inline PNG mark"


def test_session_status_ready_favicon_reuses_the_template_mark():
    js = SESSION_JS.read_text(encoding="utf-8")
    # The ready state captures the server-rendered <link rel=icon>, not a hardcoded V.
    assert "_initialIcon" in js
    assert "ready: _initialIcon ||" in js
