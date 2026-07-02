"""Unit tests for the Analyses-tab backend: saved-visualization discovery
and the parsimony viewer-asset resolver.

Tests focus on the pure helpers (_build_saved_visualizations,
_parsimony_viewer_dir) so no live HTTP server is required. Mirrors the
test_ptools_launch.py style.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vivarium_dashboard.lib.saved_visualizations import (
    build_saved_visualizations as _build_saved_visualizations,
    parsimony_viewer_dir as _parsimony_viewer_dir,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ws_with_saved_viz(tmp_path):
    """A workspace containing one study with a saved 3D pack (+ meta + mesh)
    and a second study with a PTools TSV export.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: testws\n")

    # Study 1: a saved parsimony 3D pack with sidecar + mesh.
    viz3d = ws / "studies" / "cell-3d" / "viz" / "3d"
    viz3d.mkdir(parents=True)
    (ws / "studies" / "cell-3d" / "study.yaml").write_text("name: cell-3d\n")
    (viz3d / "scene.pack.json").write_text(json.dumps({"placements": [], "ingredients": {}}))
    (viz3d / "scene.meta.json").write_text(json.dumps({
        "ingredients": {
            "ribosome": {"count": 100},
            "polymerase": {"count": 23},
        }
    }))
    (viz3d / "meshes").mkdir()
    (viz3d / "meshes" / "ribosome.lod0.obj").write_text("o ribosome\n")

    # Study 2: PTools TSV export, no 3D pack.
    ptools = ws / "studies" / "metabo" / "ptools"
    ptools.mkdir(parents=True)
    (ws / "studies" / "metabo" / "study.yaml").write_text("name: metabo\n")
    (ptools / "flux__p1.tsv").write_text("gene\tt1\nA\t1.0\n")
    (ptools / "expr__p1.tsv").write_text("gene\tt1\nB\t2.0\n")

    return ws


# ---------------------------------------------------------------------------
# _build_saved_visualizations
# ---------------------------------------------------------------------------

def test_discovers_saved_3d_pack(ws_with_saved_viz):
    data = _build_saved_visualizations(ws_with_saved_viz)
    saved = data["saved"]
    assert len(saved) == 1
    entry = saved[0]
    assert entry["study"] == "cell-3d"
    assert entry["name"] == "scene"
    assert entry["pack_url"] == "/studies/cell-3d/viz/3d/scene.pack.json"
    assert entry["meta_url"] == "/studies/cell-3d/viz/3d/scene.meta.json"
    # n_placed summed from the meta ingredient counts.
    assert entry["n_placed"] == 123
    assert isinstance(entry["created"], int)


def test_ptools_discovery_and_config_flag(ws_with_saved_viz):
    data = _build_saved_visualizations(ws_with_saved_viz)
    ptools = data["ptools"]
    # No ui.ptools_server_url in workspace.yaml -> not configured.
    assert ptools["configured"] is False
    studies = {s["study"]: s for s in ptools["studies"]}
    assert "metabo" in studies
    assert studies["metabo"]["n_tsvs"] == 2
    # The 3D-only study has no ptools dir, so it is absent.
    assert "cell-3d" not in studies


def test_ptools_configured_flag_reads_workspace_yaml(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: t\nui:\n  ptools_server_url: http://ptools.example.com\n")
    data = _build_saved_visualizations(ws)
    assert data["ptools"]["configured"] is True


def test_empty_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: empty\n")
    data = _build_saved_visualizations(ws)
    assert data["saved"] == []
    assert data["ptools"]["studies"] == []
    # parsimony_available is a bool reflecting the optional dep.
    assert isinstance(data["parsimony_available"], bool)


def test_pack_without_meta_sidecar(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: t\n")
    viz3d = ws / "studies" / "s" / "viz" / "3d"
    viz3d.mkdir(parents=True)
    (ws / "studies" / "s" / "study.yaml").write_text("name: s\n")
    (viz3d / "x.pack.json").write_text("{}")
    data = _build_saved_visualizations(ws)
    assert len(data["saved"]) == 1
    assert data["saved"][0]["meta_url"] is None
    assert data["saved"][0]["n_placed"] is None


# ---------------------------------------------------------------------------
# _parsimony_viewer_dir
# ---------------------------------------------------------------------------

def test_parsimony_viewer_dir_is_dir_or_none():
    """Never raises; returns a real viewer dir (with index.html) or None when
    the optional pbg_parsimony package is not installed."""
    d = _parsimony_viewer_dir()
    if d is not None:
        assert d.is_dir()
        assert (d / "index.html").is_file()
        assert (d / "viewer.js").is_file()
