"""The chat assets are cache-busted with the rest of the dashboard's assets.

`lib/report.py` stamps asset URLs with a version derived from asset mtimes; the
chat files must be part of it, or editing chat.js alone would be served stale
from the browser cache.
"""
import os
import re
import shutil
from pathlib import Path

from vivarium_workbench.lib import report

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_CHAT_ASSETS = ("chat.css", "chat-core.js", "chat.js", "ai-login.js", "ai-models.js")


def _render(ws):
    html_path = report.render_dashboard(ws)
    return Path(html_path).read_text(encoding="utf-8"), Path(html_path).parent


def _versions(html):
    return {a: re.search(rf'assets/{re.escape(a)}\?v=([^"]+)"', html).group(1) for a in _CHAT_ASSETS}


def test_chat_assets_carry_the_version_stamp(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURE, ws)
    html, _ = _render(ws)
    versions = _versions(html)
    assert len(set(versions.values())) == 1          # one shared stamp
    assert all(v for v in versions.values())


def test_stamp_changes_when_a_chat_asset_changes(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURE, ws)
    html1, out = _render(ws)
    # Make chat.js the newest file in the rendered assets, then re-stamp without
    # letting the renderer re-copy (it would reset mtimes): only the stamp logic runs.
    monkeypatch.setattr(shutil, "copy2", lambda src, dst, *a, **k: dst)
    monkeypatch.setattr(shutil, "copyfile", lambda src, dst, *a, **k: dst)
    js = out / "assets" / "chat.js"
    future = int(js.stat().st_mtime) + 5000
    os.utime(js, (future, future))
    html2, _ = _render(ws)
    monkeypatch.undo()
    assert _versions(html1)["chat.js"] != _versions(html2)["chat.js"]
