"""Tests for lib.workspace_deps_views builders + server shim parity.

Covers:
  - build_source_builds()    : GET /api/source/builds
  - build_workspaces()       : GET /api/workspaces
  - build_system_deps_check(): GET /api/system-deps-check?name=<module>

Each builder is tested in isolation (monkeypatching its external deps), and
the Handler shim is tested by invoking the real server.Handler method via
__new__ + _json capture (mirroring the established TestServerShimParity
pattern from test_api_app.py).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

# -----------------------------------------------------------------------
# Builder isolation tests
# -----------------------------------------------------------------------


class TestBuildSourceBuilds:
    """build_source_builds() — env-based, no ws_root."""

    def test_happy_path(self, monkeypatch):
        """When sms-api returns simulators, builds list is populated."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv

        canned = {"builds": [
            {"simulator_id": 1, "repo": "v2ecoli", "commit": "abc123",
             "branch": "main", "label": "v2ecoli @ abc123 (build #1)"},
        ], "error": None}
        monkeypatch.setattr(
            "vivarium_dashboard.lib.remote_build_source.list_build_sources",
            lambda client: canned,
        )
        result = wdv.build_source_builds()
        assert result == canned
        assert isinstance(result["builds"], list)
        assert result["error"] is None

    def test_sms_api_down_returns_empty_with_error(self, monkeypatch):
        """When sms-api is unreachable, builds is [] and error has a reason."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv

        monkeypatch.setattr(
            "vivarium_dashboard.lib.remote_build_source.list_build_sources",
            lambda client: {"builds": [], "error": "connection refused"},
        )
        result = wdv.build_source_builds()
        assert result["builds"] == []
        assert result["error"] == "connection refused"

    def test_uses_sms_api_base_env(self, monkeypatch):
        """The SMS_API_BASE env var is forwarded to the SmsApiClient."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv

        seen_base: list[str] = []

        class _FakeClient:
            def __init__(self, base: str) -> None:
                seen_base.append(base)

        monkeypatch.setenv("SMS_API_BASE", "http://myproxy:9090")
        monkeypatch.setattr(
            "vivarium_dashboard.lib.sms_api_client.SmsApiClient",
            _FakeClient,
        )
        monkeypatch.setattr(
            "vivarium_dashboard.lib.remote_build_source.list_build_sources",
            lambda client: {"builds": [], "error": None},
        )
        wdv.build_source_builds()
        assert seen_base == ["http://myproxy:9090"]


class TestBuildWorkspaces:
    """build_workspaces(ws_root) — reads catalog, joins server entries."""

    def _make_ws(self, tmp_path: Path, name: str = "my-ws") -> Path:
        ws = tmp_path / name
        ws.mkdir(exist_ok=True)
        (ws / "workspace.yaml").write_text(yaml.dump({"name": name}))
        return ws

    def test_current_only_when_catalog_empty(self, tmp_path, monkeypatch):
        """With empty catalog, result has current + one 'current' workspace row."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "test-ws")
        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.list_workspaces",
            lambda: [],
        )
        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.find_entry",
            lambda path: None,
        )
        result = wdv.build_workspaces(ws)
        assert result["current"]["name"] == "test-ws"
        assert result["current"]["path"] == str(ws.resolve())
        assert len(result["workspaces"]) == 1
        row = result["workspaces"][0]
        assert row["status"] == "current"
        assert row["name"] == "test-ws"

    def test_catalog_exception_falls_back_to_current_only(self, tmp_path, monkeypatch):
        """catalog.list_workspaces() raising → still returns current-only."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "fallback-ws")

        def _raise():
            raise RuntimeError("catalog exploded")

        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.list_workspaces",
            _raise,
        )
        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.find_entry",
            lambda path: None,
        )
        result = wdv.build_workspaces(ws)
        assert result["current"]["name"] == "fallback-ws"
        rows = result["workspaces"]
        assert len(rows) == 1
        assert rows[0]["status"] == "current"

    def test_running_workspace_has_url_and_pid(self, tmp_path, monkeypatch):
        """A catalog entry with an alive PID → status='running', url+pid present."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "main-ws")

        other = tmp_path / "other-ws"
        other.mkdir()
        (other / "workspace.yaml").write_text("name: other-ws\n")

        other_path = str(other.resolve())
        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.list_workspaces",
            lambda: [{"name": "other-ws", "path": other_path}],
        )
        alive_pid = os.getpid()  # current process is always alive

        def _find_entry(path: str):
            if path == other_path:
                return {"pid": alive_pid, "url": "http://127.0.0.1:8770"}
            return None

        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.find_entry",
            _find_entry,
        )
        result = wdv.build_workspaces(ws)
        by_name = {r["name"]: r for r in result["workspaces"]}
        assert "other-ws" in by_name
        assert by_name["other-ws"]["status"] == "running"
        assert by_name["other-ws"]["url"] == "http://127.0.0.1:8770"
        assert by_name["other-ws"]["pid"] == alive_pid

    def test_stale_workspace_when_pid_dead(self, tmp_path, monkeypatch):
        """A catalog entry with a dead PID → status='stale'."""
        import subprocess as _sp
        from vivarium_dashboard.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "main-ws2")
        other = tmp_path / "stale-ws"
        other.mkdir()
        (other / "workspace.yaml").write_text("name: stale-ws\n")
        other_path = str(other.resolve())

        # Get a confirmed-dead PID.
        proc = _sp.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        dead_pid = proc.pid

        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.list_workspaces",
            lambda: [{"name": "stale-ws", "path": other_path}],
        )
        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.find_entry",
            lambda path: {"pid": dead_pid, "url": "http://127.0.0.1:9999"}
            if path == other_path else None,
        )
        result = wdv.build_workspaces(ws)
        by_name = {r["name"]: r for r in result["workspaces"]}
        assert by_name["stale-ws"]["status"] == "stale"
        assert by_name["stale-ws"]["pid"] == dead_pid

    def test_missing_path_workspace(self, tmp_path, monkeypatch):
        """A catalog entry whose path doesn't exist → status='missing'."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "main-ws3")
        ghost_path = str(tmp_path / "ghost" / "workspace")

        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.list_workspaces",
            lambda: [{"name": "ghost", "path": ghost_path}],
        )
        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.find_entry",
            lambda path: None,
        )
        result = wdv.build_workspaces(ws)
        by_name = {r["name"]: r for r in result["workspaces"]}
        assert "ghost" in by_name
        assert by_name["ghost"]["status"] == "missing"

    def test_sort_order(self, tmp_path, monkeypatch):
        """Workspaces are sorted: current → running → stopped → stale → missing."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "current-ws")

        stopped = tmp_path / "stopped-ws"
        stopped.mkdir()
        (stopped / "workspace.yaml").write_text("name: stopped-ws\n")
        stopped_path = str(stopped.resolve())

        ghost_path = str(tmp_path / "ghost-ws")  # does not exist

        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.list_workspaces",
            lambda: [
                {"name": "stopped-ws", "path": stopped_path},
                {"name": "ghost-ws", "path": ghost_path},
            ],
        )
        monkeypatch.setattr(
            "pbg_superpowers.workspace_catalog.find_entry",
            lambda path: None,
        )
        result = wdv.build_workspaces(ws)
        statuses = [r["status"] for r in result["workspaces"]]
        order = {"current": 0, "running": 1, "stopped": 2, "stale": 3, "missing": 4}
        assert statuses == sorted(statuses, key=lambda s: order.get(s, 99))


class TestBuildSystemDepsCheck:
    """build_system_deps_check(ws_root, name) — 400/404/200."""

    def _make_ws(self, tmp_path: Path) -> Path:
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "workspace.yaml").write_text("name: test-ws\n")
        # Point venv python to the real interpreter.
        venv_bin = ws / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python3").symlink_to(Path(sys.executable))
        return ws

    def _patch_registry(self, monkeypatch, ws: Path, catalog: list) -> None:
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        monkeypatch.setattr(
            wdv, "module_registry", lambda root: catalog,
        )

    def test_missing_name_returns_400(self, tmp_path, monkeypatch):
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        ws = self._make_ws(tmp_path)
        self._patch_registry(monkeypatch, ws, [])
        body, status = wdv.build_system_deps_check(ws, "")
        assert status == 400
        assert body == {"error": "name required"}

    def test_unknown_module_returns_404(self, tmp_path, monkeypatch):
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        ws = self._make_ws(tmp_path)
        self._patch_registry(monkeypatch, ws, [{"name": "other-module"}])
        body, status = wdv.build_system_deps_check(ws, "not-in-registry")
        assert status == 404
        assert "unknown module" in body["error"]
        assert "not-in-registry" in body["error"]

    def test_200_all_ok_when_no_checks(self, tmp_path, monkeypatch):
        """A module with no system_dependencies.checks passes trivially."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        ws = self._make_ws(tmp_path)
        catalog = [{"name": "pbg-trivial", "system_dependencies": {"checks": []}}]
        self._patch_registry(monkeypatch, ws, catalog)
        body, status = wdv.build_system_deps_check(ws, "pbg-trivial")
        assert status == 200
        assert body["name"] == "pbg-trivial"
        assert body["ok"] is True
        assert body["checks"] == []

    def test_200_ok_with_passing_import_check(self, tmp_path, monkeypatch):
        """A real import_check that succeeds → ok=True."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        ws = self._make_ws(tmp_path)
        catalog = [{
            "name": "pbg-passes",
            "system_dependencies": {
                "checks": [{
                    "name": "stdlib-check",
                    "description": "Always passes",
                    "import_check": "import sys",
                }]
            }
        }]
        self._patch_registry(monkeypatch, ws, catalog)
        body, status = wdv.build_system_deps_check(ws, "pbg-passes")
        assert status == 200
        assert body["ok"] is True
        assert len(body["checks"]) == 1
        assert body["checks"][0]["ok"] is True

    def test_200_failing_import_check(self, tmp_path, monkeypatch):
        """A module whose import_check fails → ok=False, reason populated."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        ws = self._make_ws(tmp_path)
        catalog = [{
            "name": "pbg-fails",
            "system_dependencies": {
                "checks": [{
                    "name": "always-missing",
                    "description": "deliberately missing",
                    "import_check": "import __definitely_not_a_module_xyz__",
                }]
            }
        }]
        self._patch_registry(monkeypatch, ws, catalog)
        body, status = wdv.build_system_deps_check(ws, "pbg-fails")
        assert status == 200
        assert body["ok"] is False
        assert body["checks"][0]["ok"] is False
        assert body["checks"][0]["reason"] is not None

    def test_platform_key_in_response(self, tmp_path, monkeypatch):
        """Response includes a valid platform string."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        ws = self._make_ws(tmp_path)
        catalog = [{"name": "pbg-plat", "system_dependencies": {"checks": []}}]
        self._patch_registry(monkeypatch, ws, catalog)
        body, status = wdv.build_system_deps_check(ws, "pbg-plat")
        assert status == 200
        assert body["platform"] in {"darwin", "linux", "windows"} or isinstance(body["platform"], str)


# -----------------------------------------------------------------------
# Server shim parity tests
# -----------------------------------------------------------------------


class TestServerShimParity:
    """Real server.Handler methods == lib builder output (parity guard)."""

    @staticmethod
    def _invoke_handler(monkeypatch, ws: Path, method_name: str, path: str = "/") -> dict:
        """Call the real stdlib Handler method, capturing (body, status)."""
        import vivarium_dashboard.server as server

        monkeypatch.setattr(server, "WORKSPACE", ws)
        handler = server.Handler.__new__(server.Handler)
        captured: dict = {}

        def _fake_json(data, code):
            captured["body"] = data
            captured["status"] = code

        handler._json = _fake_json  # type: ignore[method-assign]
        handler.path = path
        getattr(handler, method_name)()
        return captured

    def test_source_builds_shim(self, tmp_path, monkeypatch):
        """_get_source_builds delegates to build_source_builds() unchanged."""
        import vivarium_dashboard.lib.workspace_deps_views as wdv

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "workspace.yaml").write_text("name: ws\n")

        canned = {"builds": [], "error": None}
        monkeypatch.setattr(wdv, "build_source_builds", lambda: canned)

        result = self._invoke_handler(monkeypatch, ws, "_get_source_builds")
        assert result["status"] == 200
        assert result["body"] == canned

    def test_workspaces_shim(self, tmp_path, monkeypatch):
        """_get_workspaces delegates to build_workspaces() unchanged."""
        import vivarium_dashboard.lib.workspace_deps_views as wdv

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "workspace.yaml").write_text("name: ws\n")

        canned = {"current": {"name": "ws", "path": str(ws)}, "workspaces": []}
        monkeypatch.setattr(wdv, "build_workspaces", lambda root: canned)

        result = self._invoke_handler(monkeypatch, ws, "_get_workspaces")
        assert result["status"] == 200
        assert result["body"] == canned

    def test_system_deps_400_no_name(self, tmp_path, monkeypatch):
        """_get_system_deps_check returns 400 when name is absent."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "workspace.yaml").write_text("name: ws\n")
        # Patch module_registry so no catalog needed.
        import vivarium_dashboard.lib.workspace_deps_views as wdv
        monkeypatch.setattr(wdv, "module_registry", lambda root: [])

        result = self._invoke_handler(
            monkeypatch, ws, "_get_system_deps_check",
            path="/api/system-deps-check",
        )
        assert result["status"] == 400
        assert result["body"] == {"error": "name required"}

    def test_system_deps_404_unknown(self, tmp_path, monkeypatch):
        """_get_system_deps_check returns 404 when module is not in registry."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "workspace.yaml").write_text("name: ws\n")
        import vivarium_dashboard.lib.workspace_deps_views as wdv
        monkeypatch.setattr(wdv, "module_registry", lambda root: [])

        result = self._invoke_handler(
            monkeypatch, ws, "_get_system_deps_check",
            path="/api/system-deps-check?name=ghost-module",
        )
        assert result["status"] == 404
        assert "unknown module" in result["body"]["error"]

    def test_system_deps_200_trivial(self, tmp_path, monkeypatch):
        """_get_system_deps_check returns 200 with structured payload."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "workspace.yaml").write_text("name: ws\n")
        import vivarium_dashboard.lib.workspace_deps_views as wdv
        catalog = [{"name": "pbg-trivial", "system_dependencies": {"checks": []}}]
        monkeypatch.setattr(wdv, "module_registry", lambda root: catalog)

        result = self._invoke_handler(
            monkeypatch, ws, "_get_system_deps_check",
            path="/api/system-deps-check?name=pbg-trivial",
        )
        assert result["status"] == 200
        assert result["body"]["name"] == "pbg-trivial"
        assert result["body"]["ok"] is True
        assert result["body"]["checks"] == []
