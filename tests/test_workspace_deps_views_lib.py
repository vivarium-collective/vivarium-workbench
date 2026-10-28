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
        from vivarium_workbench.lib import workspace_deps_views as wdv

        canned = {"builds": [
            {"simulator_id": 1, "repo": "v2ecoli", "commit": "abc123",
             "branch": "main", "label": "v2ecoli @ abc123 (build #1)"},
        ], "error": None}
        monkeypatch.setattr(
            "vivarium_workbench.lib.remote_build_source.list_build_sources",
            lambda client: canned,
        )
        result = wdv.build_source_builds()
        assert result == canned
        assert isinstance(result["builds"], list)
        assert result["error"] is None

    def test_sms_api_down_returns_empty_with_error(self, monkeypatch):
        """When sms-api is unreachable, builds is [] and error has a reason."""
        from vivarium_workbench.lib import workspace_deps_views as wdv

        monkeypatch.setattr(
            "vivarium_workbench.lib.remote_build_source.list_build_sources",
            lambda client: {"builds": [], "error": "connection refused"},
        )
        result = wdv.build_source_builds()
        assert result["builds"] == []
        assert result["error"] == "connection refused"

    def test_uses_sms_api_base_env(self, monkeypatch):
        """The SMS_API_BASE env var is forwarded to the SmsApiClient."""
        from vivarium_workbench.lib import workspace_deps_views as wdv

        seen_base: list[str] = []

        class _FakeClient:
            def __init__(self, base: str) -> None:
                seen_base.append(base)

        # Clear the canonical name: _sms_api_base reads VIVA_API_BASE first, and
        # conftest's _isolate_viva_api_base sets both. This test exercises the
        # legacy alias specifically (cf. test_env_worker_launcher, same pattern).
        monkeypatch.delenv("VIVA_API_BASE", raising=False)
        monkeypatch.setenv("SMS_API_BASE", "http://myproxy:9090")
        monkeypatch.setattr(
            "vivarium_workbench.lib.sms_api_client.SmsApiClient",
            _FakeClient,
        )
        monkeypatch.setattr(
            "vivarium_workbench.lib.remote_build_source.list_build_sources",
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

    def _make_git_ws(self, tmp_path: Path, dirname: str, ws_name: str, origin_url: str) -> Path:
        """A workspace whose `workspace.yaml` `name` may differ from its real
        git remote — same shape as sms-ecoli (name: v2ecoli, real repo
        CovertLabEcoli/sms-ecoli), for item 54 regression coverage."""
        import subprocess as _sp
        ws = tmp_path / dirname
        ws.mkdir(exist_ok=True)
        (ws / "workspace.yaml").write_text(yaml.dump({"name": ws_name}))
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        run = lambda *a: _sp.run(["git", "-C", str(ws), *a], check=True,
                                  capture_output=True, text=True, env={**env})
        _sp.run(["git", "init", "-q", str(ws)], check=True, env={**env})
        run("remote", "add", "origin", origin_url)
        run("add", "-A")
        run("commit", "-q", "-m", "init")
        return ws

    def test_current_only_when_catalog_empty(self, tmp_path, monkeypatch):
        """With empty catalog, result has current + one 'current' workspace row."""
        from vivarium_workbench.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "test-ws")
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.list_workspaces",
            lambda: [],
        )
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.find_entry",
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
        from vivarium_workbench.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "fallback-ws")

        def _raise():
            raise RuntimeError("catalog exploded")

        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.list_workspaces",
            _raise,
        )
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.find_entry",
            lambda path: None,
        )
        result = wdv.build_workspaces(ws)
        assert result["current"]["name"] == "fallback-ws"
        rows = result["workspaces"]
        assert len(rows) == 1
        assert rows[0]["status"] == "current"

    def test_running_workspace_has_url_and_pid(self, tmp_path, monkeypatch):
        """A catalog entry with an alive PID → status='running', url+pid present."""
        from vivarium_workbench.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "main-ws")

        other = tmp_path / "other-ws"
        other.mkdir()
        (other / "workspace.yaml").write_text("name: other-ws\n")

        other_path = str(other.resolve())
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.list_workspaces",
            lambda: [{"name": "other-ws", "path": other_path}],
        )
        alive_pid = os.getpid()  # current process is always alive

        def _find_entry(path: str):
            if path == other_path:
                return {"pid": alive_pid, "url": "http://127.0.0.1:8770"}
            return None

        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.find_entry",
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
        from vivarium_workbench.lib import workspace_deps_views as wdv

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
            "discovery_engine.workspace_catalog.list_workspaces",
            lambda: [{"name": "stale-ws", "path": other_path}],
        )
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.find_entry",
            lambda path: {"pid": dead_pid, "url": "http://127.0.0.1:9999"}
            if path == other_path else None,
        )
        result = wdv.build_workspaces(ws)
        by_name = {r["name"]: r for r in result["workspaces"]}
        assert by_name["stale-ws"]["status"] == "stale"
        assert by_name["stale-ws"]["pid"] == dead_pid

    def test_missing_path_workspace(self, tmp_path, monkeypatch):
        """A catalog entry whose path doesn't exist → status='missing'."""
        from vivarium_workbench.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "main-ws3")
        ghost_path = str(tmp_path / "ghost" / "workspace")

        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.list_workspaces",
            lambda: [{"name": "ghost", "path": ghost_path}],
        )
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.find_entry",
            lambda path: None,
        )
        result = wdv.build_workspaces(ws)
        by_name = {r["name"]: r for r in result["workspaces"]}
        assert "ghost" in by_name
        assert by_name["ghost"]["status"] == "missing"

    def test_sort_order(self, tmp_path, monkeypatch):
        """Workspaces are sorted: current → running → stopped → stale → missing."""
        from vivarium_workbench.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "current-ws")

        stopped = tmp_path / "stopped-ws"
        stopped.mkdir()
        (stopped / "workspace.yaml").write_text("name: stopped-ws\n")
        stopped_path = str(stopped.resolve())

        ghost_path = str(tmp_path / "ghost-ws")  # does not exist

        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.list_workspaces",
            lambda: [
                {"name": "stopped-ws", "path": stopped_path},
                {"name": "ghost-ws", "path": ghost_path},
            ],
        )
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.find_entry",
            lambda path: None,
        )
        result = wdv.build_workspaces(ws)
        statuses = [r["status"] for r in result["workspaces"]]
        order = {"current": 0, "running": 1, "stopped": 2, "stale": 3, "missing": 4}
        assert statuses == sorted(statuses, key=lambda s: order.get(s, 99))

    def test_repo_uses_real_git_remote_not_stale_workspace_yaml_name(self, tmp_path, monkeypatch):
        """Item 54 (Chris Long / cplong): two checkouts sharing a stale
        `workspace.yaml` `name` (a real forked repo that never renamed it, e.g.
        sms-ecoli forked from v2ecoli and still declares `name: v2ecoli`) must
        NOT be merged into one `repo` group — `.repo` must reflect the real git
        remote, distinct per checkout, while `.name` stays whatever
        `workspace.yaml` says (unaffected — a different field, different
        consumers, see the item's own backlog history for why `name` itself
        can't just be changed)."""
        from vivarium_workbench.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "current-ws")
        fork = self._make_git_ws(
            tmp_path, "sms-ecoli-checkout", "v2ecoli",
            "https://github.com/CovertLabEcoli/sms-ecoli.git",
        )
        upstream = self._make_git_ws(
            tmp_path, "v2ecoli-checkout", "v2ecoli",
            "https://github.com/vivarium-collective/v2ecoli.git",
        )
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.list_workspaces",
            lambda: [
                {"name": "v2ecoli", "path": str(fork.resolve())},
                {"name": "v2ecoli", "path": str(upstream.resolve())},
            ],
        )
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.find_entry",
            lambda path: None,
        )
        result = wdv.build_workspaces(ws)
        by_path = {r["path"]: r for r in result["workspaces"]}
        fork_row = by_path[str(fork.resolve())]
        upstream_row = by_path[str(upstream.resolve())]

        # Same `name` (workspace.yaml is genuinely unchanged) ...
        assert fork_row["name"] == "v2ecoli"
        assert upstream_row["name"] == "v2ecoli"
        # ... but real, distinct repo identity — no more merged "Repo: v2ecoli" group.
        assert fork_row["repo"] == "sms-ecoli"
        assert upstream_row["repo"] == "v2ecoli"
        assert fork_row["repo"] != upstream_row["repo"]

    def test_repo_falls_back_to_name_when_no_git_remote(self, tmp_path, monkeypatch):
        """A catalog entry with no git remote (or not a git repo at all) still
        gets a usable, non-empty `.repo` — degrades to `name`, same tolerant
        shape as `read_workspace_name`, never an empty picker entry."""
        from vivarium_workbench.lib import workspace_deps_views as wdv

        ws = self._make_ws(tmp_path, "plain-ws")
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.list_workspaces",
            lambda: [],
        )
        monkeypatch.setattr(
            "discovery_engine.workspace_catalog.find_entry",
            lambda path: None,
        )
        result = wdv.build_workspaces(ws)
        row = result["workspaces"][0]
        assert row["name"] == "plain-ws"
        assert row["repo"] == "plain-ws"


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
        from vivarium_workbench.lib import workspace_deps_views as wdv
        monkeypatch.setattr(
            wdv, "module_registry", lambda root: catalog,
        )

    def test_missing_name_returns_400(self, tmp_path, monkeypatch):
        from vivarium_workbench.lib import workspace_deps_views as wdv
        ws = self._make_ws(tmp_path)
        self._patch_registry(monkeypatch, ws, [])
        body, status = wdv.build_system_deps_check(ws, "")
        assert status == 400
        assert body == {"error": "name required"}

    def test_unknown_module_returns_404(self, tmp_path, monkeypatch):
        from vivarium_workbench.lib import workspace_deps_views as wdv
        ws = self._make_ws(tmp_path)
        self._patch_registry(monkeypatch, ws, [{"name": "other-module"}])
        body, status = wdv.build_system_deps_check(ws, "not-in-registry")
        assert status == 404
        assert "unknown module" in body["error"]
        assert "not-in-registry" in body["error"]

    def test_200_all_ok_when_no_checks(self, tmp_path, monkeypatch):
        """A module with no system_dependencies.checks passes trivially."""
        from vivarium_workbench.lib import workspace_deps_views as wdv
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
        from vivarium_workbench.lib import workspace_deps_views as wdv
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
        from vivarium_workbench.lib import workspace_deps_views as wdv
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
        from vivarium_workbench.lib import workspace_deps_views as wdv
        ws = self._make_ws(tmp_path)
        catalog = [{"name": "pbg-plat", "system_dependencies": {"checks": []}}]
        self._patch_registry(monkeypatch, ws, catalog)
        body, status = wdv.build_system_deps_check(ws, "pbg-plat")
        assert status == 200
        assert body["platform"] in {"darwin", "linux", "windows"} or isinstance(body["platform"], str)
