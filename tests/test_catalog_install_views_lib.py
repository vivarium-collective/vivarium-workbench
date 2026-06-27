"""Tests for ``lib.catalog_install_views.catalog_install`` — the catalog-module
install builder (the biggest handler in the migration).

Behaviour-preserving port of ``server.Handler._post_catalog_install`` with the
``_commit_or_run`` commit DEFERRED (the install subprocess + the workspace.yaml
mutation both run inline inside ``action()``; a raised ``action`` maps to the
live ``{"error": "action failed: …"}, 500``).

Hermetic: ``subprocess.run`` is ALWAYS monkeypatched — NO real install is ever
spawned — and the lib helpers (``workspace_deps_views.module_registry`` /
``platform_key`` / ``check_system_dep``, ``install_errors.diagnose``,
``workspace_yaml`` load/save, ``pyproject_edit`` add_dependency / add_uv_source,
``registry.clear_registry_cache``) are monkeypatched on their source modules.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from vivarium_dashboard.lib import catalog_install_views as views
from vivarium_dashboard.lib import install_errors
from vivarium_dashboard.lib import pyproject_edit
from vivarium_dashboard.lib import registry
from vivarium_dashboard.lib import workspace_deps_views
from vivarium_dashboard.lib import workspace_yaml


def _write_ws(tmp_path: Path, data: dict) -> Path:
    (tmp_path / "workspace.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


def _make_venv_pip(tmp_path: Path) -> None:
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin" / "pip").write_text("")


def _patch_yaml_io(monkeypatch) -> None:
    monkeypatch.setattr(workspace_yaml, "load_workspace",
                        lambda p: yaml.safe_load(Path(p).read_text()))
    monkeypatch.setattr(workspace_yaml, "save_workspace",
                        lambda p, d: Path(p).write_text(yaml.safe_dump(d, sort_keys=False)))


def _patch_pyproject_noops(monkeypatch) -> None:
    monkeypatch.setattr(pyproject_edit, "add_dependency", lambda *a, **k: True)
    monkeypatch.setattr(pyproject_edit, "add_uv_source", lambda *a, **k: True)


# ===========================================================================
# Validation
# ===========================================================================

class TestValidation:
    def test_missing_name_400(self, tmp_path):
        body, status = views.catalog_install(tmp_path, {})
        assert status == 400
        assert body == {"error": "missing name"}

    def test_blank_name_400(self, tmp_path):
        body, status = views.catalog_install(tmp_path, {"name": "  "})
        assert status == 400
        assert body == {"error": "missing name"}

    def test_not_in_catalog_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [])
        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 404
        assert body == {"error": "module 'foo' not in catalog"}


# ===========================================================================
# System-dependency 409 gate
# ===========================================================================

class TestSystemDepsGate:
    def _entry(self) -> dict:
        return {
            "name": "foo",
            "source": "https://example.com/foo.git",
            "ref": "main",
            "system_dependencies": {
                "checks": [{
                    "name": "ipopt",
                    "description": "IPOPT solver",
                    "import_check": "import ipopt",
                    "install": {"darwin": {"commands": ["brew install ipopt"]}},
                    "notes": "needs homebrew",
                }],
            },
        }

    def test_unmet_returns_409_with_missing_shape(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workspace_deps_views, "module_registry",
                            lambda ws: [self._entry()])
        monkeypatch.setattr(workspace_deps_views, "platform_key", lambda: "darwin")
        monkeypatch.setattr(workspace_deps_views, "check_system_dep",
                            lambda check, venv_py: (False, "No module named ipopt"))

        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 409
        assert body["error"] == "unmet system dependencies"
        assert body["name"] == "foo"
        assert body["platform"] == "darwin"
        assert body["hint"].startswith("POST again with skip_system_deps_check=true")
        assert body["missing"] == [{
            "name": "ipopt",
            "description": "IPOPT solver",
            "reason": "No module named ipopt",
            "install": {"commands": ["brew install ipopt"]},
            "notes": "needs homebrew",
        }]

    def test_skip_flag_bypasses_gate(self, tmp_path, monkeypatch):
        # Same unsatisfied check, but skip_system_deps_check=true → proceed to
        # install. Use a PyPI entry so the path is a single pip subprocess.
        entry = self._entry()
        entry["pypi_name"] = "foo-pkg"
        _write_ws(tmp_path, {"name": "ws", "imports": {}})
        _make_venv_pip(tmp_path)
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(workspace_deps_views, "check_system_dep",
                            lambda check, venv_py: (False, "missing"))
        monkeypatch.setattr(views.shutil, "which", lambda x: None)  # force venv pip
        monkeypatch.setattr(views.subprocess, "run",
                            lambda cmd, **kw: subprocess.CompletedProcess(
                                cmd, 0, stdout="installed", stderr=""))
        _patch_yaml_io(monkeypatch)
        _patch_pyproject_noops(monkeypatch)
        monkeypatch.setattr(registry, "clear_registry_cache", lambda: None)

        body, status = views.catalog_install(
            tmp_path, {"name": "foo", "skip_system_deps_check": True})
        assert status == 200
        assert body["ok"] is True


# ===========================================================================
# Happy paths
# ===========================================================================

class TestPyPiInstall:
    def test_happy_pypi_200_mutates_and_clears(self, tmp_path, monkeypatch):
        entry = {
            "name": "foo",
            "source": "https://pypi.org/foo",
            "ref": "1.2.3",
            "description": "the foo pkg",
            "pypi_name": "foo-pkg",
        }
        _write_ws(tmp_path, {"name": "ws", "imports": {}})
        _make_venv_pip(tmp_path)
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)  # force venv pip

        captured = {}
        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: captured.update(cmd=cmd, cwd=kw.get("cwd"))
            or subprocess.CompletedProcess(cmd, 0, stdout="install ok", stderr=""))
        _patch_yaml_io(monkeypatch)
        _patch_pyproject_noops(monkeypatch)
        cleared = {"n": 0}
        monkeypatch.setattr(registry, "clear_registry_cache",
                            lambda: cleared.update(n=cleared["n"] + 1))

        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 200
        assert body == {"ok": True, "module": "foo",
                        "install_mode": "pypi", "log": "install ok"}
        # pip command targets the pypi_name (not a local path).
        assert captured["cmd"] == [
            str(tmp_path / ".venv" / "bin" / "pip"), "install", "foo-pkg"]
        assert cleared["n"] == 1
        # workspace.yaml mutation ran inline (deferred commit): pypi mode entry.
        saved = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
        assert saved["imports"]["foo"]["mode"] == "pypi"
        assert saved["imports"]["foo"]["pypi_name"] == "foo-pkg"
        assert saved["imports"]["foo"]["installed"] is True


class TestGitSubmoduleInstall:
    def test_happy_git_200_mutates_and_clears(self, tmp_path, monkeypatch):
        entry = {
            "name": "foo",
            "source": "https://example.com/foo.git",
            "ref": "main",
            "description": "git foo",
            "package": "pbg_foo",
        }
        _write_ws(tmp_path, {"name": "ws", "imports": {}})
        _make_venv_pip(tmp_path)
        # Pre-create external/foo so the submodule-add step is skipped — only the
        # editable pip install subprocess runs.
        (tmp_path / "external" / "foo").mkdir(parents=True)
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)

        cmds = []
        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: cmds.append(cmd)
            or subprocess.CompletedProcess(cmd, 0, stdout="editable install ok", stderr=""))
        _patch_yaml_io(monkeypatch)
        _patch_pyproject_noops(monkeypatch)
        cleared = {"n": 0}
        monkeypatch.setattr(registry, "clear_registry_cache",
                            lambda: cleared.update(n=cleared["n"] + 1))

        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 200
        assert body["ok"] is True
        assert body["module"] == "foo"
        assert body["install_mode"] == "git"
        assert body["log"] == "editable install ok"
        assert cleared["n"] == 1
        # Only the editable pip install ran (submodule add skipped).
        assert len(cmds) == 1
        assert cmds[0][:3] == [str(tmp_path / ".venv" / "bin" / "pip"), "install", "-e"]
        assert cmds[0][-1] == str((tmp_path / "external" / "foo").resolve())
        saved = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
        assert saved["imports"]["foo"]["mode"] == "reference"
        assert saved["imports"]["foo"]["path"] == "external/foo"
        assert saved["imports"]["foo"]["package"] == "pbg_foo"
        assert saved["imports"]["foo"]["installed"] is True

    def test_git_runs_submodule_add_when_absent(self, tmp_path, monkeypatch):
        entry = {
            "name": "foo", "source": "https://example.com/foo.git", "ref": "main",
        }
        _write_ws(tmp_path, {"name": "ws", "imports": {}})
        _make_venv_pip(tmp_path)
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)

        cmds = []

        def _fake_run(cmd, **kw):
            cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(views.subprocess, "run", _fake_run)
        _patch_yaml_io(monkeypatch)
        _patch_pyproject_noops(monkeypatch)
        monkeypatch.setattr(registry, "clear_registry_cache", lambda: None)

        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 200
        # Two subprocess calls: git submodule add, then pip install -e.
        assert cmds[0][:3] == ["git", "submodule", "add"]
        assert cmds[1][:3] == [str(tmp_path / ".venv" / "bin" / "pip"), "install", "-e"]


# ===========================================================================
# No installer available (pre-action 500)
# ===========================================================================

class TestNoInstaller:
    def test_pypi_no_installer_500(self, tmp_path, monkeypatch):
        entry = {"name": "foo", "source": "s", "ref": "r", "pypi_name": "foo-pkg"}
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)  # no uv, no pip
        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert body == {"error": "neither pip nor uv available"}

    def test_git_no_installer_500(self, tmp_path, monkeypatch):
        entry = {"name": "foo", "source": "s", "ref": "r"}
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)
        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert body == {"error": "neither pip nor uv available"}


# ===========================================================================
# Install failure (deferred-commit 500 mapping)
# ===========================================================================

class TestInstallFailure:
    def test_pip_nonzero_500_with_log_and_diagnosis(self, tmp_path, monkeypatch):
        entry = {"name": "foo", "source": "s", "ref": "r", "pypi_name": "foo-pkg"}
        _write_ws(tmp_path, {"name": "ws", "imports": {}})
        _make_venv_pip(tmp_path)
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)
        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="ERROR: could not find foo-pkg"))
        diag = install_errors.InstallDiagnosis(
            category="not_found", summary="missing", suggestion="check name",
            raw_excerpt="ERROR")
        monkeypatch.setattr(install_errors, "diagnose", lambda log: diag)
        cleared = {"n": 0}
        monkeypatch.setattr(registry, "clear_registry_cache",
                            lambda: cleared.update(n=cleared["n"] + 1))

        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert body["error"].startswith("action failed: pip install from PyPI failed:")
        assert "could not find foo-pkg" in body["log"]
        assert body["install_mode"] == "pypi"
        assert body["diagnosis"] == diag.as_dict()
        # Registry cache invalidated even on failure.
        assert cleared["n"] == 1

    def test_pip_nonzero_500_no_diagnosis(self, tmp_path, monkeypatch):
        entry = {"name": "foo", "source": "s", "ref": "r", "pypi_name": "foo-pkg"}
        _write_ws(tmp_path, {"name": "ws", "imports": {}})
        _make_venv_pip(tmp_path)
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)
        monkeypatch.setattr(
            views.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"))
        monkeypatch.setattr(install_errors, "diagnose", lambda log: None)
        monkeypatch.setattr(registry, "clear_registry_cache", lambda: None)

        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert "diagnosis" not in body
        assert body["install_mode"] == "pypi"

    def test_pip_timeout_500_no_log(self, tmp_path, monkeypatch):
        # subprocess raises before log_holder is populated → live leaves the 500
        # un-enriched (no log/install_mode/diagnosis added).
        entry = {"name": "foo", "source": "s", "ref": "r", "pypi_name": "foo-pkg"}
        _write_ws(tmp_path, {"name": "ws", "imports": {}})
        _make_venv_pip(tmp_path)
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [entry])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)

        def _raise(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 180)

        monkeypatch.setattr(views.subprocess, "run", _raise)
        monkeypatch.setattr(registry, "clear_registry_cache", lambda: None)

        body, status = views.catalog_install(tmp_path, {"name": "foo"})
        assert status == 500
        assert body == {"error": "action failed: pip install from PyPI timed out after 180s"}
