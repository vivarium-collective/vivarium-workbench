"""Tests for ``lib.catalog_uninstall_views`` — the catalog-module uninstall
builders (the last catalog committer).

Behaviour-preserving port of ``server.Handler._uninstall_unmanaged_or_404`` (the
extracted instance method) + ``_post_catalog_uninstall`` (the main handler) with
the ``_commit_or_run`` commit DEFERRED.

Hermetic: ``subprocess.run`` is ALWAYS monkeypatched — NO real pip/git is ever
spawned — and the lib helpers (``catalog._detect_workspace_venv_distributions``,
``workspace_deps_views.module_registry``, ``workspace_yaml`` load/save,
``pyproject_edit`` remove_dependency / remove_uv_source,
``registry.clear_registry_cache``) are monkeypatched on their source modules.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from vivarium_dashboard.lib import catalog
from vivarium_dashboard.lib import catalog_uninstall_views as views
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


class _FakeCompleted:
    def __init__(self, stdout="ok", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_no_clear(monkeypatch) -> dict:
    """Track clear_registry_cache invocations."""
    calls = {"n": 0}
    monkeypatch.setattr(registry, "clear_registry_cache",
                        lambda: calls.__setitem__("n", calls["n"] + 1))
    return calls


# ===========================================================================
# uninstall_unmanaged_or_404
# ===========================================================================

class TestUninstallUnmanaged:
    def test_not_in_venv_200_already_uninstalled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(catalog, "_detect_workspace_venv_distributions",
                            lambda ws: {})
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [])
        body, status = views.uninstall_unmanaged_or_404(tmp_path, "foo")
        assert status == 200
        assert body == {"ok": True, "already_uninstalled": True}

    def test_has_parents_409(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            catalog, "_detect_workspace_venv_distributions",
            lambda ws: {"foo": {"requires_by": ["bar", "baz"]}})
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [])
        body, status = views.uninstall_unmanaged_or_404(tmp_path, "foo")
        assert status == 409
        assert body["transitive_via"] == ["bar", "baz"]
        assert body["module"] == "foo"
        assert body["error"] == (
            "foo is required by bar, baz — uninstall the parent(s) first")

    def test_orphan_200_unmanaged(self, tmp_path, monkeypatch):
        _make_venv_pip(tmp_path)
        (tmp_path / ".venv" / "bin" / "python3").write_text("")
        # untracked external/foo checkout present
        (tmp_path / "external" / "foo").mkdir(parents=True)
        (tmp_path / "external" / "foo" / "x.py").write_text("")
        monkeypatch.setattr(
            catalog, "_detect_workspace_venv_distributions",
            lambda ws: {"foo": {"requires_by": []}})
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)  # force venv-pip path
        calls = _patch_no_clear(monkeypatch)

        captured = {}

        def _fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _FakeCompleted(stdout="Successfully uninstalled foo")
        monkeypatch.setattr(subprocess, "run", _fake_run)

        body, status = views.uninstall_unmanaged_or_404(tmp_path, "foo")
        assert status == 200
        assert body["ok"] is True
        assert body["module"] == "foo"
        assert body["install_mode"] == "unmanaged"
        # venv-pip uninstall command
        assert captured["cmd"][-3:] == ["uninstall", "-y", "foo"]
        # best-effort external rmtree happened
        assert not (tmp_path / "external" / "foo").exists()
        assert "removed external/foo/" in body["log"]
        assert calls["n"] == 1

    def test_orphan_submodule_left_in_place(self, tmp_path, monkeypatch):
        _make_venv_pip(tmp_path)
        (tmp_path / ".venv" / "bin" / "python3").write_text("")
        (tmp_path / "external" / "foo").mkdir(parents=True)
        (tmp_path / ".gitmodules").write_text(
            '[submodule "external/foo"]\n\tpath = external/foo\n')
        monkeypatch.setattr(
            catalog, "_detect_workspace_venv_distributions",
            lambda ws: {"foo": {"requires_by": []}})
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)
        _patch_no_clear(monkeypatch)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _FakeCompleted())

        body, status = views.uninstall_unmanaged_or_404(tmp_path, "foo")
        assert status == 200
        assert (tmp_path / "external" / "foo").exists()  # NOT removed
        assert "tracked submodule; left in place" in body["log"]

    def test_no_pip_uv_500(self, tmp_path, monkeypatch):
        # venv exists for detection but no pip/uv binary
        monkeypatch.setattr(
            catalog, "_detect_workspace_venv_distributions",
            lambda ws: {"foo": {"requires_by": []}})
        monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: [])
        monkeypatch.setattr(views.shutil, "which", lambda x: None)
        body, status = views.uninstall_unmanaged_or_404(tmp_path, "foo")
        assert status == 500
        assert body == {"error": "no venv pip/uv available to uninstall"}


# ===========================================================================
# catalog_uninstall (main handler)
# ===========================================================================

class TestCatalogUninstall:
    def test_missing_name_400(self, tmp_path):
        body, status = views.catalog_uninstall(tmp_path, {})
        assert status == 400
        assert body == {"error": "missing name"}

    def test_blank_name_400(self, tmp_path):
        body, status = views.catalog_uninstall(tmp_path, {"name": "  "})
        assert status == 400
        assert body == {"error": "missing name"}

    def test_not_in_imports_delegates(self, tmp_path, monkeypatch):
        _write_ws(tmp_path, {"imports": {}})
        _patch_yaml_io(monkeypatch)
        sentinel = ({"ok": True, "already_uninstalled": True, "_sentinel": 1}, 200)
        captured = {}

        def _fake(ws, name):
            captured["ws"], captured["name"] = ws, name
            return sentinel
        monkeypatch.setattr(views, "uninstall_unmanaged_or_404", _fake)

        body, status = views.catalog_uninstall(tmp_path, {"name": "foo"})
        assert (body, status) == sentinel
        assert captured["name"] == "foo"
        assert captured["ws"] == tmp_path

    def test_pypi_mode_happy(self, tmp_path, monkeypatch):
        _make_venv_pip(tmp_path)
        _write_ws(tmp_path, {"imports": {
            "foo": {"mode": "pypi", "pypi_name": "foo-pkg", "package": "foo"}}})
        _patch_yaml_io(monkeypatch)
        monkeypatch.setattr(views.shutil, "which", lambda x: None)  # venv-pip path
        calls = _patch_no_clear(monkeypatch)

        removed = {}
        monkeypatch.setattr(pyproject_edit, "remove_dependency",
                            lambda p, pkg: removed.__setitem__("dep", pkg) or True)
        captured = {}
        monkeypatch.setattr(subprocess, "run",
                            lambda cmd, **kw: captured.__setitem__("cmd", cmd)
                            or _FakeCompleted(stdout="Successfully uninstalled foo-pkg"))

        body, status = views.catalog_uninstall(tmp_path, {"name": "foo"})
        assert status == 200
        assert body["ok"] is True
        assert body["module"] == "foo"
        assert body["install_mode"] == "pypi"
        assert "log" in body
        # pyproject dep removed by pypi_name
        assert removed["dep"] == "foo-pkg"
        # pip uninstall ran on pypi_name
        assert captured["cmd"][-1] == "foo-pkg"
        # workspace.yaml imports[foo] popped
        ws_after = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
        assert "foo" not in (ws_after.get("imports") or {})
        assert calls["n"] == 1

    def test_reference_mode_happy(self, tmp_path, monkeypatch):
        _make_venv_pip(tmp_path)
        _write_ws(tmp_path, {"imports": {
            "foo": {"mode": "reference", "package": "foo", "path": "external/foo"}}})
        (tmp_path / "external" / "foo").mkdir(parents=True)
        _patch_yaml_io(monkeypatch)
        monkeypatch.setattr(views.shutil, "which", lambda x: None)
        _patch_no_clear(monkeypatch)

        removed = {"dep": [], "uv": []}
        monkeypatch.setattr(pyproject_edit, "remove_dependency",
                            lambda p, pkg: removed["dep"].append(pkg) or True)
        monkeypatch.setattr(pyproject_edit, "remove_uv_source",
                            lambda p, pkg: removed["uv"].append(pkg) or True)
        cmds = []
        monkeypatch.setattr(subprocess, "run",
                            lambda cmd, **kw: cmds.append(cmd) or _FakeCompleted())

        body, status = views.catalog_uninstall(tmp_path, {"name": "foo"})
        assert status == 200
        assert body["install_mode"] == "reference"
        assert removed["dep"] == ["foo"]
        assert removed["uv"] == ["foo"]
        # submodule deinit + git rm + pip uninstall all ran (3 subprocess calls)
        joined = [" ".join(map(str, c)) for c in cmds]
        assert any("submodule deinit" in j for j in joined)
        assert any("rm -f external/foo" in j for j in joined)
        # workspace.yaml imports[foo] popped
        ws_after = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
        assert "foo" not in (ws_after.get("imports") or {})

    def test_action_raise_500(self, tmp_path, monkeypatch):
        _make_venv_pip(tmp_path)
        _write_ws(tmp_path, {"imports": {"foo": {"mode": "pypi", "package": "foo"}}})
        # load_workspace ok for the initial read but save_workspace raises in action
        monkeypatch.setattr(workspace_yaml, "load_workspace",
                            lambda p: yaml.safe_load(Path(p).read_text()))

        def _boom(p, d):
            raise RuntimeError("disk full")
        monkeypatch.setattr(workspace_yaml, "save_workspace", _boom)
        monkeypatch.setattr(views.shutil, "which", lambda x: None)
        _patch_no_clear(monkeypatch)
        monkeypatch.setattr(pyproject_edit, "remove_dependency", lambda p, pkg: True)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _FakeCompleted())

        body, status = views.catalog_uninstall(tmp_path, {"name": "foo"})
        assert status == 500
        assert body["error"].startswith("action failed: ")
