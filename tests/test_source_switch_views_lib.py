"""Tests for the pure source-switch builder (``lib.source_switch_views``).

Reproduces the stdlib ``_post_source_switch`` validation byte-identically and
applies the lib-side switch (``active_workspace.switch_workspace``): set
``lib._root`` + invalidate the lib caches.  No ``WORKSPACE`` global, no
server-local caches (those stay in ``server``).
"""
from __future__ import annotations

import pytest

from vivarium_dashboard.lib import _root
from vivarium_dashboard.lib import active_workspace
from vivarium_dashboard.lib import source_switch_views


@pytest.fixture(autouse=True)
def _reset_root():
    saved = _root.get_workspace_root()
    _root._WS_ROOT = None
    yield
    _root._WS_ROOT = saved


def test_missing_path_400():
    body, status = source_switch_views.source_switch({})
    assert status == 400
    assert body == {"error": "missing 'path'"}

    body, status = source_switch_views.source_switch({"path": "   "})
    assert status == 400
    assert body == {"error": "missing 'path'"}


def test_unregistered_path_400(tmp_path, monkeypatch):
    from pbg_superpowers import workspace_catalog

    monkeypatch.setattr(workspace_catalog, "list_workspaces", lambda: [])
    p = str(tmp_path / "nope")
    body, status = source_switch_views.source_switch({"path": p})
    assert status == 400
    # Byte-identical legacy message (uses the RAW path, not the resolved one).
    assert body == {"error": f"{p!r} is not a registered workspace"}


def test_happy_path_repoints_and_invalidates(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: w\n")
    from pbg_superpowers import workspace_catalog

    monkeypatch.setattr(
        workspace_catalog, "list_workspaces",
        lambda: [{"path": str(ws), "name": "w"}],
    )

    fired = []
    active_workspace.register_clear_cb(lambda: fired.append(True))

    body, status = source_switch_views.source_switch({"path": str(ws)})

    assert status == 200
    assert body == {"ok": True, "source": {"path": str(ws), "name": "w"}}
    # lib._root re-pointed to the resolved registered path.
    assert _root.get_workspace_root() == ws.resolve()
    # invalidate() fired the registered cache-clear callbacks.
    assert fired, "switch_workspace must call active_workspace.invalidate()"
