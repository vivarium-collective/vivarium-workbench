"""Tests for ``lib.investigation_create_views.investigation_create``.

Behaviour-preserving port of ``server.Handler._post_investigation_create``
(scaffold a new investigation directory) with the ``_active_branch_action``
commit DEFERRED — the builder runs the scaffold inline and returns
``{ok, name}`` directly.

Hermetic: a tmp ws_root, NO real git. The composite-source resolver
(``investigation_migrate._resolve_composite_source_or_generate``) and the v4
scaffold emitter (``scaffold_yaml.v4_study_scaffold``) are monkeypatched so no
registry / pbg-superpowers import is required.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.lib import investigation_create_views as views
from vivarium_dashboard.lib import investigation_migrate
from vivarium_dashboard.lib import scaffold_yaml


def _make_ws(tmp_path: Path, *, name: str = "demo-ws") -> Path:
    (tmp_path / "workspace.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_missing_name_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.investigation_create(ws, {})
    assert status == 400
    assert body == {"error": "name is required"}


def test_blank_name_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.investigation_create(ws, {"name": "   "})
    assert status == 400
    assert body == {"error": "name is required"}


def test_bad_name_regex_400(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.investigation_create(ws, {"name": "bad name!"})
    assert status == 400
    assert body == {"error": "name must match [a-zA-Z0-9_-]+"}


def test_already_exists_studies_dir_409(tmp_path):
    ws = _make_ws(tmp_path)
    (ws / "studies" / "dup").mkdir(parents=True)
    body, status = views.investigation_create(ws, {"name": "dup"})
    assert status == 409
    assert body == {"error": "investigation 'dup' already exists"}


def test_already_exists_investigations_dir_409(tmp_path):
    ws = _make_ws(tmp_path)
    (ws / "investigations" / "dup2").mkdir(parents=True)
    body, status = views.investigation_create(ws, {"name": "dup2"})
    assert status == 409
    assert body == {"error": "investigation 'dup2' already exists"}


# ---------------------------------------------------------------------------
# source resolution 404
# ---------------------------------------------------------------------------

def test_source_not_found_404(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)

    def _boom(ref, root):
        raise FileNotFoundError("nope")

    monkeypatch.setattr(
        investigation_migrate, "_resolve_composite_source_or_generate", _boom)
    body, status = views.investigation_create(
        ws, {"name": "inv-x", "source": "pkg.composites.missing"})
    assert status == 404
    assert body == {"error": "source composite not found: nope"}


def test_source_value_error_404(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)

    def _boom(ref, root):
        raise ValueError("bad ref")

    monkeypatch.setattr(
        investigation_migrate, "_resolve_composite_source_or_generate", _boom)
    body, status = views.investigation_create(
        ws, {"name": "inv-y", "source": "garbage"})
    assert status == 404
    assert body == {"error": "source composite not found: bad ref"}


# ---------------------------------------------------------------------------
# the three scaffold shapes
# ---------------------------------------------------------------------------

def test_generator_shape_writes_study_yaml(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)

    def _gen(ref, root):
        return (None, True, "base")

    canned = "schema_version: 4\nname: inv-gen\n"

    def _scaffold(name, *, composite=None, baseline_name=None, created=None):
        assert composite == "pkg.composites.base"
        assert baseline_name == "base"
        return canned

    monkeypatch.setattr(
        investigation_migrate, "_resolve_composite_source_or_generate", _gen)
    monkeypatch.setattr(scaffold_yaml, "v4_study_scaffold", _scaffold)

    body, status = views.investigation_create(
        ws, {"name": "inv-gen", "source": "pkg.composites.base"})
    assert status == 200
    assert body == {"ok": True, "name": "inv-gen"}

    inv_dir = ws / "studies" / "inv-gen"
    assert (inv_dir / "study.yaml").read_text() == canned
    assert (inv_dir / "data" / ".keep").exists()
    # generator shape writes NO spec.yaml + NO composites sidecar
    assert not (inv_dir / "spec.yaml").exists()
    assert not (inv_dir / "composites").exists()


def test_source_path_shape_copies_sidecar_and_writes_spec_yaml(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    src = tmp_path / "source_base.yaml"
    src.write_text("# baseline composite doc\nkey: value\n")

    def _yaml_src(ref, root):
        return (src, False, "base")

    monkeypatch.setattr(
        investigation_migrate, "_resolve_composite_source_or_generate", _yaml_src)

    body, status = views.investigation_create(
        ws, {"name": "inv-src", "source": "pkg.composites.base"})
    assert status == 200
    assert body == {"ok": True, "name": "inv-src"}

    inv_dir = ws / "studies" / "inv-src"
    assert (inv_dir / "data" / ".keep").exists()
    # sidecar copied verbatim
    sidecar = inv_dir / "composites" / "base.yaml"
    assert sidecar.read_text() == src.read_text()
    # v2-shape spec.yaml
    assert not (inv_dir / "study.yaml").exists()
    spec = yaml.safe_load((inv_dir / "spec.yaml").read_text())
    assert spec == {
        "name": "inv-src",
        "description": "",
        "composites": [
            {
                "name": "base",
                "source": "pkg.composites.base",
                "document": "./composites/base.yaml",
            }
        ],
        "simulations": [
            {
                "name": "baseline",
                "composite": "base",
                "kind": "single",
                "overrides": {},
                "steps": 10,
            }
        ],
        "observables": [],
        "visualizations": [],
        "status": "planned",
    }


def test_blank_shape_writes_stub_spec_yaml(tmp_path):
    ws = _make_ws(tmp_path)
    body, status = views.investigation_create(ws, {"name": "inv-blank"})
    assert status == 200
    assert body == {"ok": True, "name": "inv-blank"}

    inv_dir = ws / "studies" / "inv-blank"
    assert (inv_dir / "data" / ".keep").exists()
    assert not (inv_dir / "study.yaml").exists()
    expected_stub = (
        "name: inv-blank\n"
        "description: \"\"\n"
        "\n"
        "composites: []\n"
        "\n"
        "simulations: []\n"
        "\n"
        "observables: []\n"
        "\n"
        "visualizations: []\n"
        "\n"
        "status: planned\n"
    )
    assert (inv_dir / "spec.yaml").read_text() == expected_stub


# ---------------------------------------------------------------------------
# deferred-commit: action raise → 500
# ---------------------------------------------------------------------------

def test_action_failure_500(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)

    def _gen(ref, root):
        return (None, True, "base")

    def _boom(name, *, composite=None, baseline_name=None, created=None):
        raise RuntimeError("scaffold boom")

    monkeypatch.setattr(
        investigation_migrate, "_resolve_composite_source_or_generate", _gen)
    monkeypatch.setattr(scaffold_yaml, "v4_study_scaffold", _boom)

    body, status = views.investigation_create(
        ws, {"name": "inv-fail", "source": "pkg.composites.base"})
    assert status == 500
    assert body == {"error": "action failed: scaffold boom"}


def test_no_real_git_no_commit(tmp_path):
    """The deferred-commit port must NOT create a git repo / commit."""
    ws = _make_ws(tmp_path)
    body, status = views.investigation_create(ws, {"name": "inv-nogit"})
    assert status == 200
    assert not (ws / ".git").exists()
