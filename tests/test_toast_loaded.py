"""Every page whose scripts call ``_showToast`` loads ``toast.js`` first.

The call sites guard with ``typeof _showToast === 'function' ? _showToast(msg) : alert(msg)``. When no page
loaded the helper, every one of them silently fell back to a blocking ``alert()`` — and nothing failed. These
tests pin the load on the real served pages (live dashboard, live study page) and in the published bundle.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "_fixtures"
_CALLERS = ("sim-table.js", "walkthrough.js", "study-detail.js")


@pytest.fixture
def ws_with_study(tmp_path) -> Path:
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURES / "ws_increase_demo", ws)
    shutil.copytree(_FIXTURES / "ws_federation_collision" / "studies" / "shared", ws / "studies" / "shared")
    return ws


def _script_pos(html: str, name: str) -> int:
    """Offset of the ``<script src=...name>`` tag that loads *name*, or -1 (prose mentions don't count)."""
    m = re.search(r'<script[^>]*\bsrc="[^"]*/' + re.escape(name) + r'[?"]', html)
    return m.start() if m else -1


def _assert_toast_before_callers(html: str, page: str) -> None:
    at = _script_pos(html, "toast.js")
    assert at != -1, f"{page} does not load toast.js — its _showToast calls fall back to alert()"
    for caller in _CALLERS:
        pos = _script_pos(html, caller)
        if pos != -1:
            assert at < pos, f"{page} loads {caller} before toast.js"


def test_live_dashboard_and_study_page_load_the_toast(dashboard_client, ws_with_study):
    client = dashboard_client(workspace=ws_with_study)
    home = client.get("/")
    assert home.status_code == 200
    assert _script_pos(home.text, "sim-table.js") != -1 and _script_pos(home.text, "walkthrough.js") != -1
    _assert_toast_before_callers(home.text, "the dashboard")

    study = client.get("/studies/shared")
    assert study.status_code == 200
    assert _script_pos(study.text, "study-detail.js") != -1
    _assert_toast_before_callers(study.text, "the study page")

    js = client.get("/toast.js")
    assert js.status_code == 200 and "_showToast" in js.text


def test_published_bundle_ships_and_loads_the_toast(ws_with_study, tmp_path):
    from vivarium_workbench import publish

    out = tmp_path / "bundle"
    publish.build_bundle(ws_with_study, out)
    assert (out / "assets" / "toast.js").is_file()
    _assert_toast_before_callers((out / "index.html").read_text(), "the published home page")
    _assert_toast_before_callers((out / "studies" / "shared" / "index.html").read_text(), "the published study page")
