"""Tests for ``lib.install_views`` — the two venv / system-install builders.

Behaviour-preserving ports of ``server.Handler._post_system_deps_install`` and
``_post_import_install`` (the latter with the ``_active_branch_action`` commit
DEFERRED — the builder runs the workspace.yaml mutation inline).

Hermetic: ``subprocess.run`` is ALWAYS monkeypatched — NO real install is ever
spawned — and the lib helpers (``workspace_deps_views.module_registry`` /
``platform_key`` / ``check_system_dep``, ``install_errors.diagnose``,
``workspace_yaml.load_workspace`` / ``save_workspace``,
``registry.clear_registry_cache``) are monkeypatched on their source modules.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import install_errors
from vivarium_workbench.lib import install_views as views
from vivarium_workbench.lib import registry
from vivarium_workbench.lib import workspace_deps_views
from vivarium_workbench.lib import workspace_yaml


def _write_ws(tmp_path: Path, data: dict) -> Path:
    (tmp_path / "workspace.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


# ===========================================================================
# system_deps_install
# ===========================================================================

class TestSystemDepsInstall:
    def test_missing_name_400(self, tmp_path):
        body, status = views.system_deps_install(tmp_path, {"check_names": ["c"]})
        assert status == 400
        assert body == {"error": "name + check_names required"}

    def test_missing_check_names_400(self, tmp_path):
        body, status = views.system_deps_install(tmp_path, {"name": "mod"})
        assert status == 400
        assert body == {"error": "name + check_names required"}

    def test_unknown_module_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [])
        body, status = views.system_deps_install(
            tmp_path, {"name": "mod", "check_names": ["c"]})
        assert status == 404
        assert body == {"error": "unknown module: mod"}

    def test_happy_all_log_branches(self, tmp_path, monkeypatch):
        # A module declaring 4 named checks; the 5th requested check is unknown.
        entry = {
            "name": "mod",
            "system_dependencies": {
                "checks": [
                    {"name": "ok_check", "install": {"darwin": {"commands": ["echo hi"]}}},
                    {"name": "fail_check", "install": {"darwin": {"commands": ["false"]}}},
                    {"name": "timeout_check", "install": {"darwin": {"commands": ["sleep 999"]}}},
                    {"name": "nospec_check", "install": {"linux": {"commands": ["apt-get x"]}}},
                ],
            },
        }
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(workspace_deps_views, "platform_key", lambda: "darwin")
        monkeypatch.setattr(
            workspace_deps_views, "check_system_dep",
            lambda check, venv_py: (True, None))

        def _fake_run(cmd, **kwargs):
            assert kwargs.get("shell") is True
            assert kwargs.get("timeout") == 600
            if cmd == "echo hi":
                return subprocess.CompletedProcess(cmd, 0, stdout="OUT", stderr="")
            if cmd == "false":
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ERR")
            if cmd == "sleep 999":
                raise subprocess.TimeoutExpired(cmd, 600)
            raise AssertionError(f"unexpected cmd {cmd!r}")

        monkeypatch.setattr(views.subprocess, "run", _fake_run)

        check_names = ["ok_check", "fail_check", "timeout_check", "nospec_check", "unknown_check"]
        body, status = views.system_deps_install(
            tmp_path, {"name": "mod", "check_names": check_names})

        assert status == 200
        assert body["ok"] is False
        log = body["log"]
        assert log == [
            {"check_name": "ok_check", "command": "echo hi",
             "returncode": 0, "stdout_tail": "OUT", "stderr_tail": ""},
            {"check_name": "fail_check", "command": "false",
             "returncode": 1, "stdout_tail": "", "stderr_tail": "ERR"},
            {"check_name": "timeout_check", "command": "sleep 999",
             "returncode": -1, "error": "timeout (600s)"},
            {"check_name": "nospec_check", "returncode": -1,
             "error": "no install spec for platform darwin"},
            {"check_name": "unknown_check", "returncode": -1, "error": "unknown check"},
        ]
        # recheck skips the unknown check (not in by_name) → 4 entries.
        assert body["recheck"] == [
            {"name": "ok_check", "ok": True, "reason": None},
            {"name": "fail_check", "ok": True, "reason": None},
            {"name": "timeout_check", "ok": True, "reason": None},
            {"name": "nospec_check", "ok": True, "reason": None},
        ]

    def test_all_ok_when_install_succeeds(self, tmp_path, monkeypatch):
        entry = {
            "name": "mod",
            "system_dependencies": {
                "checks": [{"name": "c", "install": {"darwin": {"commands": ["echo ok"]}}}],
            },
        }
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(workspace_deps_views, "platform_key", lambda: "darwin")
        monkeypatch.setattr(
            workspace_deps_views, "check_system_dep",
            lambda check, venv_py: (True, None))
        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
        body, status = views.system_deps_install(
            tmp_path, {"name": "mod", "check_names": ["c"]})
        assert status == 200
        assert body["ok"] is True
        assert body["recheck"] == [{"name": "c", "ok": True, "reason": None}]


# ===========================================================================
# import_install
# ===========================================================================

class TestImportInstall:
    def test_missing_name_400(self, tmp_path):
        body, status = views.import_install(tmp_path, {})
        assert status == 400
        assert body == {"error": "missing name"}

    def test_not_registered_404(self, tmp_path):
        _write_ws(tmp_path, {"name": "ws", "imports": {}})
        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 404
        assert body == {"error": "import 'foo' not registered"}

    def test_no_target_400(self, tmp_path):
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {}}})
        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 400
        assert body["error"].startswith("no install target")

    def test_path_missing_404(self, tmp_path):
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "nope"}}})
        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 404
        assert body["error"].startswith("path does not exist:")
        assert str((tmp_path / "nope").resolve()) in body["error"]

    def test_picker_prefers_venv_pip(self, tmp_path, monkeypatch):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "pip").write_text("")
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "pkg"}}})

        captured = {}

        def _fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")

        monkeypatch.setattr(views.subprocess, "run", _fake_run)
        monkeypatch.setattr(workspace_yaml, "load_workspace",
                            lambda p: yaml.safe_load(Path(p).read_text()))
        monkeypatch.setattr(workspace_yaml, "save_workspace",
                            lambda p, d: Path(p).write_text(yaml.safe_dump(d, sort_keys=False)))
        monkeypatch.setattr(registry, "clear_registry_cache", lambda: None)

        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 200
        assert captured["cmd"][0] == str(tmp_path / ".venv" / "bin" / "pip")
        assert captured["cmd"][1:4] == ["install", "-e", str(pkg)]

    def test_picker_uv_fallback(self, tmp_path, monkeypatch):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "python3").write_text("")  # no pip
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "pkg"}}})

        captured = {}
        monkeypatch.setattr(views.shutil, "which", lambda x: "/usr/bin/uv")
        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: captured.update(cmd=cmd)
            or subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
        monkeypatch.setattr(workspace_yaml, "load_workspace",
                            lambda p: yaml.safe_load(Path(p).read_text()))
        monkeypatch.setattr(workspace_yaml, "save_workspace",
                            lambda p, d: Path(p).write_text(yaml.safe_dump(d, sort_keys=False)))
        monkeypatch.setattr(registry, "clear_registry_cache", lambda: None)

        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 200
        assert captured["cmd"][:2] == ["/usr/bin/uv", "pip"]
        assert "--python" in captured["cmd"]
        assert str(pkg) in captured["cmd"]

    def test_picker_neither_500(self, tmp_path, monkeypatch):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "pkg"}}})
        monkeypatch.setattr(views.shutil, "which", lambda x: None)
        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert "neither .venv/bin/pip nor `uv` found" in body["error"]

    def test_timeout_500(self, tmp_path, monkeypatch):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "pip").write_text("")
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "pkg"}}})

        def _raise(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 120)

        monkeypatch.setattr(views.subprocess, "run", _raise)
        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert body["error"].endswith("install timed out after 120s")

    def test_generic_error_500(self, tmp_path, monkeypatch):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "pip").write_text("")
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "pkg"}}})

        def _raise(cmd, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(views.subprocess, "run", _raise)
        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert body == {"error": "install error: boom"}

    def test_nonzero_returncode_500_with_diagnosis(self, tmp_path, monkeypatch):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "pip").write_text("")
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "pkg"}}})

        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="some failure log"))
        diag = install_errors.InstallDiagnosis(
            category="x", summary="s", suggestion="do y", raw_excerpt="r")
        monkeypatch.setattr(install_errors, "diagnose", lambda log: diag)

        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert body["error"] == "install failed"
        assert "some failure log" in body["log"]
        assert body["diagnosis"] == diag.as_dict()

    def test_nonzero_returncode_500_no_diagnosis(self, tmp_path, monkeypatch):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "pip").write_text("")
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "pkg"}}})

        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="x"))
        monkeypatch.setattr(install_errors, "diagnose", lambda log: None)
        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert "diagnosis" not in body

    def test_happy_mutates_workspace_and_clears_cache(self, tmp_path, monkeypatch):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "pip").write_text("")
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "pkg"}}})

        cleared = {"n": 0}
        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 0, stdout="installed ok", stderr=""))
        monkeypatch.setattr(workspace_yaml, "load_workspace",
                            lambda p: yaml.safe_load(Path(p).read_text()))
        monkeypatch.setattr(workspace_yaml, "save_workspace",
                            lambda p, d: Path(p).write_text(yaml.safe_dump(d, sort_keys=False)))
        monkeypatch.setattr(registry, "clear_registry_cache",
                            lambda: cleared.update(n=cleared["n"] + 1))

        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 200
        assert body == {"ok": True, "log": "installed ok"}
        assert cleared["n"] == 1

        # The workspace.yaml mutation ran inline (deferred commit): the import is
        # now marked installed=True with the resolved absolute install_path.
        saved = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
        assert saved["imports"]["foo"]["installed"] is True
        assert saved["imports"]["foo"]["install_path"] == str(pkg)

    def test_target_override_beats_entry_path(self, tmp_path, monkeypatch):
        pkg = tmp_path / "override"
        pkg.mkdir()
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "pip").write_text("")
        _write_ws(tmp_path, {"name": "ws", "imports": {"foo": {"path": "pkg"}}})

        captured = {}
        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: captured.update(cmd=cmd)
            or subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
        monkeypatch.setattr(workspace_yaml, "load_workspace",
                            lambda p: yaml.safe_load(Path(p).read_text()))
        monkeypatch.setattr(workspace_yaml, "save_workspace",
                            lambda p, d: Path(p).write_text(yaml.safe_dump(d, sort_keys=False)))
        monkeypatch.setattr(registry, "clear_registry_cache", lambda: None)

        body, status = views.import_install(tmp_path, {"name": "foo", "target": "override"})
        assert status == 200
        assert str(pkg) in captured["cmd"]
        saved = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
        assert saved["imports"]["foo"]["install_path"] == str(pkg)

    def test_url_target_skips_path_resolution(self, tmp_path, monkeypatch):
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "pip").write_text("")
        _write_ws(tmp_path, {
            "name": "ws",
            "imports": {"foo": {"path": "git+https://example.com/x.git"}},
        })
        captured = {}
        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: captured.update(cmd=cmd)
            or subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
        monkeypatch.setattr(workspace_yaml, "load_workspace",
                            lambda p: yaml.safe_load(Path(p).read_text()))
        monkeypatch.setattr(workspace_yaml, "save_workspace",
                            lambda p, d: Path(p).write_text(yaml.safe_dump(d, sort_keys=False)))
        monkeypatch.setattr(registry, "clear_registry_cache", lambda: None)

        body, status = views.import_install(tmp_path, {"name": "foo"})
        assert status == 200
        # URL passed through verbatim (not resolved to a filesystem path).
        assert captured["cmd"][-1] == "git+https://example.com/x.git"
