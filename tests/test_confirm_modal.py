"""The shared non-blocking confirm/alert modal (issue #1618 / #1051).

Native confirm()/alert() freeze the event loop and hang browser automation.
study-detail.js already used a module-local modal; confirm-modal.js lifts it to
a shared global so walkthrough.js's authoring confirmations (catalog install,
study run) stop falling back to native confirm(). These tests pin:
  * the helper is loaded before walkthrough.js on the served pages + in the bundle,
  * the two named authoring flows route through the gate rather than confirm().
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = Path(__file__).parent / "_fixtures"


@pytest.fixture
def ws_with_study(tmp_path) -> Path:
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURES / "ws_increase_demo", ws)
    shutil.copytree(_FIXTURES / "ws_federation_collision" / "studies" / "shared",
                    ws / "studies" / "shared")
    return ws


def _script_pos(html: str, name: str) -> int:
    m = re.search(r'<script[^>]*\bsrc="[^"]*/' + re.escape(name) + r'[?"]', html)
    return m.start() if m else -1


def _assert_modal_before_walkthrough(html: str, page: str) -> None:
    at = _script_pos(html, "confirm-modal.js")
    assert at != -1, f"{page} does not load confirm-modal.js"
    wt = _script_pos(html, "walkthrough.js")
    if wt != -1:
        assert at < wt, f"{page} loads walkthrough.js before confirm-modal.js"


# --- the helper is loaded + served -----------------------------------------

def test_live_pages_load_the_confirm_modal(inproc_client, ws_with_study):
    client = inproc_client(workspace=ws_with_study)
    home = client.get("/")
    assert home.status_code == 200
    _assert_modal_before_walkthrough(home.text, "the dashboard")
    js = client.get("/confirm-modal.js")
    assert js.status_code == 200
    assert "_confirmModal" in js.text and "_alertModal" in js.text


def test_published_bundle_ships_the_confirm_modal(ws_with_study, tmp_path):
    from vivarium_workbench import publish

    out = tmp_path / "bundle"
    publish.build_bundle(ws_with_study, out)
    assert (out / "assets" / "confirm-modal.js").is_file()
    _assert_modal_before_walkthrough((out / "index.html").read_text(),
                                     "the published home page")


# --- the authoring confirmations route through the non-blocking gate --------

def _walkthrough() -> str:
    return (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def _fn(js: str, name: str) -> str:
    i = js.index(name)
    j = js.find("\n  window._", i + 1)
    return js[i: j] if j != -1 else js[i: i + 2000]


def test_confirm_gate_prefers_modal_falls_back_to_native():
    js = _walkthrough()
    i = js.index("function _confirmGate")
    gate = js[i: i + 300]
    assert "window._confirmModal" in gate
    assert "window.confirm(message)" in gate  # graceful fallback


def test_catalog_install_uses_the_gate_not_native_confirm():
    fn = _fn(_walkthrough(), "function _proceedWithCatalogInstall")
    assert "_confirmGate(" in fn
    assert "if (!confirm(" not in fn


def test_study_run_uses_the_gate_not_native_confirm():
    fn = _fn(_walkthrough(), "window._vivRunStudyFromRow = function")
    assert "_confirmGate(" in fn
    assert "if (!confirm(" not in fn
