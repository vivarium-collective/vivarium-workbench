"""Regression: viz Build (`find_study_dir`) resolves `layout:`-relocated studies.

`render_one` (the discovery-cycle **Build** button, via `_render_hook_for`) looked
up the study with `ws_root / "studies" / slug`, ignoring the workspace `layout:`
map. On a workspace that relocates studies to `workspace/studies` (the default for
new viva-template workspaces), every Build failed with
`no study.yaml for '<slug>'` even though the study was right there. Now it resolves
through `WorkspacePaths`, so both the flat and the layout-mapped shapes work.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib.viz_build import find_study_dir


def _make_study(studies_root: Path, slug: str) -> Path:
    sdir = studies_root / slug
    sdir.mkdir(parents=True)
    (sdir / "study.yaml").write_text(yaml.safe_dump({"name": slug}), encoding="utf-8")
    return sdir


def test_resolves_layout_mapped_studies(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "name: demo\nlayout:\n  studies: workspace/studies\n", encoding="utf-8")
    sdir = _make_study(ws / "workspace" / "studies", "distance-immigration")
    # the layout-mapped study is found (pre-fix this returned None -> Build failed)
    assert find_study_dir(ws, "distance-immigration") == sdir
    # a literal ws/studies/<slug> does NOT exist, proving the layout was honored
    assert not (ws / "studies" / "distance-immigration").exists()


def test_still_resolves_flat_studies(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")  # no layout -> flat default
    sdir = _make_study(ws / "studies", "flat-study")
    assert find_study_dir(ws, "flat-study") == sdir


def test_missing_study_returns_none(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    assert find_study_dir(ws, "nope") is None
