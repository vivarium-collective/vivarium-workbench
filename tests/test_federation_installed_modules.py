"""Tests for federating studies/investigations shipped INSIDE an installed module
(fix/federate-installed-module-studies).

Before this, `linked_workspaces` only scanned `<ws_root>/external/<name>/`, so a
module installed as a wheel (its studies/investigations packaged in the dist) or
editable (studies at the repo root) had its composites discovered but its studies
listed-yet-unopenable ("Study not found"). `linked_workspaces` now also surfaces a
declared import whose installed package ships a `workspace.yaml`, so listing and
detail resolve symmetrically.
"""
from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import federation as _fed


def _write(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _host_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "host"
    _write(ws / "workspace.yaml", {
        "name": "host", "imports": {"my-mod": {"package": "my_mod", "mode": "pypi"}}})
    return ws


def _patch_find_spec(monkeypatch, pkg_dir: Path) -> None:
    real = importlib.util.find_spec

    def fake(name, *a, **k):
        if name == "my_mod":
            return types.SimpleNamespace(submodule_search_locations=[str(pkg_dir)])
        return real(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


def test_wheel_shape_studies_inside_package(tmp_path, monkeypatch):
    # Wheel: workspace.yaml + studies/ live INSIDE the installed package dir.
    pkg = tmp_path / "site-packages" / "my_mod"
    _write(pkg / "workspace.yaml", {"name": "my-mod"})
    _write(pkg / "studies" / "killing-assay" / "study.yaml",
           {"name": "killing-assay", "status": "complete"})
    _patch_find_spec(monkeypatch, pkg)

    ws = _host_ws(tmp_path)
    lws = _fed.linked_workspaces(ws)
    assert [lw.repo for lw in lws] == ["my-mod"]

    # Listing surfaces the study...
    names = [s["name"] for s in _fed.federated_studies(ws)]
    assert "killing-assay" in names
    # ...and detail resolves it (was the "Study not found" path).
    found = _fed.find_federated_study(ws, "killing-assay")
    assert found is not None
    d, lw, spec_path = found
    assert d.name == "killing-assay" and spec_path.is_file()


def test_editable_shape_studies_at_repo_root(tmp_path, monkeypatch):
    # Editable: package dir's PARENT (repo root) holds workspace.yaml + studies/.
    repo = tmp_path / "repo"
    _write(repo / "workspace.yaml", {"name": "my-mod"})
    _write(repo / "studies" / "killing-assay" / "study.yaml", {"name": "killing-assay"})
    (repo / "my_mod").mkdir(parents=True, exist_ok=True)
    (repo / "my_mod" / "__init__.py").write_text("")
    _patch_find_spec(monkeypatch, repo / "my_mod")

    ws = _host_ws(tmp_path)
    assert [lw.repo for lw in _fed.linked_workspaces(ws)] == ["my-mod"]
    assert _fed.find_federated_study(ws, "killing-assay") is not None


def test_federated_investigation_from_installed_module(tmp_path, monkeypatch):
    pkg = tmp_path / "site-packages" / "my_mod"
    _write(pkg / "workspace.yaml", {"name": "my-mod"})
    _write(pkg / "investigations" / "showcase" / "investigation.yaml",
           {"name": "showcase", "studies": ["killing-assay"]})
    _write(pkg / "studies" / "killing-assay" / "study.yaml", {"name": "killing-assay"})
    _patch_find_spec(monkeypatch, pkg)

    ws = _host_ws(tmp_path)
    isets = _fed.federated_investigation_sets(ws)
    assert any(i["name"] == "showcase" for i in isets)


def test_external_wins_over_installed_on_duplicate(tmp_path, monkeypatch):
    # A module present BOTH under external/ and as an installed package resolves
    # to the external/ copy (added first; installed copy deduped by root).
    ext = tmp_path / "host" / "external" / "my-mod"
    _write(ext / "workspace.yaml", {"name": "my-mod"})
    _write(ext / "studies" / "killing-assay" / "study.yaml", {"name": "killing-assay"})
    pkg = tmp_path / "site-packages" / "my_mod"
    _write(pkg / "workspace.yaml", {"name": "my-mod"})
    _patch_find_spec(monkeypatch, pkg)

    ws = _host_ws(tmp_path)
    roots = [lw.root for lw in _fed.linked_workspaces(ws)]
    assert ext.resolve() in roots
    assert pkg.resolve() not in roots  # external/ copy wins


def test_import_without_workspace_yaml_is_ignored(tmp_path, monkeypatch):
    # A normal installed package (composites only, no shipped workspace.yaml) is
    # NOT treated as a linked workspace.
    pkg = tmp_path / "site-packages" / "my_mod"
    (pkg / "composites").mkdir(parents=True, exist_ok=True)  # no workspace.yaml
    _patch_find_spec(monkeypatch, pkg)

    ws = _host_ws(tmp_path)
    assert _fed.linked_workspaces(ws) == []


def test_no_imports_no_linked(tmp_path, monkeypatch):
    ws = tmp_path / "host"
    _write(ws / "workspace.yaml", {"name": "host"})
    assert _fed.linked_workspaces(ws) == []
