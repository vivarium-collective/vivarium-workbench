"""Behavioural parity tests for ``lib.workspaces_mutations`` (3 builders).

Each builder is a pure port of a stdlib workspace-registry handler that edits
the GLOBAL ``~/.pbg`` catalog via ``pbg_superpowers.workspace_catalog``.  Every
test monkeypatches ``workspaces_mutations.workspace_catalog`` with a fake so the
real ``~/.pbg`` catalog is NEVER touched, and asserts the exact ``(body, status)``
the legacy handlers returned (incl. the cleanup-stale orphan-file unlink).
"""

from __future__ import annotations

import types

import pytest

from vivarium_workbench.lib import workspaces_mutations as wm


def _fake_catalog(**overrides):
    """A stand-in ``workspace_catalog`` module with no-op defaults.

    Override individual functions via kwargs; records calls in ``.calls``.
    """
    calls: list[tuple] = []

    def add(path):
        calls.append(("add", path))
        return {"name": "ws", "path": path}

    def find_running(path):
        calls.append(("find_running", path))
        return None

    def forget(path):
        calls.append(("forget", path))

    def unregister_server(path):
        calls.append(("unregister_server", path))

    ns = types.SimpleNamespace(
        add=overrides.get("add", add),
        find_running=overrides.get("find_running", find_running),
        forget=overrides.get("forget", forget),
        unregister_server=overrides.get("unregister_server", unregister_server),
        calls=calls,
    )
    return ns


# ---------------------------------------------------------------------------
# workspaces_add
# ---------------------------------------------------------------------------
class TestWorkspacesAdd:
    def test_missing_path_400(self, monkeypatch):
        monkeypatch.setattr(wm, "workspace_catalog", _fake_catalog())
        assert wm.workspaces_add({}) == (
            {"error": "path must be an absolute string"}, 400)

    def test_non_string_path_400(self, monkeypatch):
        monkeypatch.setattr(wm, "workspace_catalog", _fake_catalog())
        assert wm.workspaces_add({"path": 123}) == (
            {"error": "path must be an absolute string"}, 400)

    def test_relative_path_400(self, monkeypatch):
        monkeypatch.setattr(wm, "workspace_catalog", _fake_catalog())
        assert wm.workspaces_add({"path": "relative/ws"}) == (
            {"error": "path must be an absolute string"}, 400)

    def test_non_dict_body_400(self, monkeypatch):
        monkeypatch.setattr(wm, "workspace_catalog", _fake_catalog())
        assert wm.workspaces_add(None) == (
            {"error": "path must be an absolute string"}, 400)

    def test_add_value_error_400(self, monkeypatch):
        def _raise(path):
            raise ValueError("not a workspace")

        monkeypatch.setattr(wm, "workspace_catalog", _fake_catalog(add=_raise))
        assert wm.workspaces_add({"path": "/abs/ws"}) == (
            {"error": "not a workspace"}, 400)

    def test_happy_200(self, monkeypatch):
        fake = _fake_catalog(add=lambda p: {"name": "demo", "path": p})
        monkeypatch.setattr(wm, "workspace_catalog", fake)
        body, status = wm.workspaces_add({"path": "/abs/ws"})
        assert status == 200
        assert body == {"name": "demo", "path": "/abs/ws"}


# ---------------------------------------------------------------------------
# workspaces_forget
# ---------------------------------------------------------------------------
class TestWorkspacesForget:
    def test_missing_path_400(self, monkeypatch):
        monkeypatch.setattr(wm, "workspace_catalog", _fake_catalog())
        assert wm.workspaces_forget({}) == ({"error": "path required"}, 400)

    def test_non_string_path_400(self, monkeypatch):
        monkeypatch.setattr(wm, "workspace_catalog", _fake_catalog())
        assert wm.workspaces_forget({"path": 5}) == ({"error": "path required"}, 400)

    def test_running_409(self, monkeypatch):
        fake = _fake_catalog(find_running=lambda p: {"pid": 1234})
        monkeypatch.setattr(wm, "workspace_catalog", fake)
        assert wm.workspaces_forget({"path": "/abs/ws"}) == (
            {"error": "stop the server before forgetting"}, 409)

    def test_happy_200_calls_forget(self, monkeypatch):
        fake = _fake_catalog()
        monkeypatch.setattr(wm, "workspace_catalog", fake)
        body, status = wm.workspaces_forget({"path": "/abs/ws"})
        assert (body, status) == ({"ok": True}, 200)
        assert ("forget", "/abs/ws") in fake.calls


# ---------------------------------------------------------------------------
# workspaces_cleanup_stale
# ---------------------------------------------------------------------------
class TestWorkspacesCleanupStale:
    def test_missing_path_400(self, monkeypatch):
        monkeypatch.setattr(wm, "workspace_catalog", _fake_catalog())
        assert wm.workspaces_cleanup_stale({}) == ({"error": "path required"}, 400)

    def test_non_string_path_400(self, monkeypatch):
        monkeypatch.setattr(wm, "workspace_catalog", _fake_catalog())
        assert wm.workspaces_cleanup_stale({"path": []}) == (
            {"error": "path required"}, 400)

    def test_running_409(self, monkeypatch):
        fake = _fake_catalog(find_running=lambda p: {"pid": 999})
        monkeypatch.setattr(wm, "workspace_catalog", fake)
        assert wm.workspaces_cleanup_stale({"path": "/abs/ws"}) == (
            {"error": "server is still running"}, 409)

    def test_happy_200_unlinks_orphan_files(self, monkeypatch, tmp_path):
        # Real tmp workspace with the orphan server files present.
        sdir = tmp_path / ".pbg" / "server"
        sdir.mkdir(parents=True)
        info = sdir / "server-info"
        pid = sdir / "server.pid"
        info.write_text("stale")
        pid.write_text("1234")

        fake = _fake_catalog()
        monkeypatch.setattr(wm, "workspace_catalog", fake)
        body, status = wm.workspaces_cleanup_stale({"path": str(tmp_path)})

        assert (body, status) == ({"ok": True}, 200)
        assert ("unregister_server", str(tmp_path)) in fake.calls
        # Best-effort unlink removed both orphan files.
        assert not info.exists()
        assert not pid.exists()

    def test_happy_200_no_orphan_files(self, monkeypatch, tmp_path):
        # No .pbg/server dir at all → the FileNotFoundError is swallowed.
        fake = _fake_catalog()
        monkeypatch.setattr(wm, "workspace_catalog", fake)
        body, status = wm.workspaces_cleanup_stale({"path": str(tmp_path)})
        assert (body, status) == ({"ok": True}, 200)
        assert ("unregister_server", str(tmp_path)) in fake.calls
