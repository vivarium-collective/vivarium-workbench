"""Symlinked ``.venv`` must never be traversed.

On an NFS deployment the workspace ``.venv`` is a symlink to the shared image
venv (tens of thousands of files). ``Path.glob``/``rglob`` follow symlinks, so
the pre-fix code traversed the whole venv on every registry/composites scan
(minutes per call). These tests build a tiny workspace with a symlinked
``.venv`` pointing at a directory full of *decoy* ``study.yaml``/composite
files, and assert the walk helper (and the three call sites that used to glob
directly) finds only the real workspace files, never the decoys.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from vivarium_workbench.lib import composites_query, registry
from vivarium_workbench.lib.composite_study_stats import (
    _iter_study_yamls,
    composite_study_stats,
)
from vivarium_workbench.lib.process_study_stats import (
    _composite_files,
    process_study_stats,
)
from vivarium_workbench.lib.workspace_walk import iter_workspace_files


def _build_workspace(tmp_path: Path) -> Path:
    """A workspace with one real study + one real composite, and a symlinked
    ``.venv`` whose target holds decoys of both."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()

    # Real, in-workspace files.
    study_dir = ws_root / "studies" / "foo"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        "composite: my_composite\nruns: []\n", encoding="utf-8"
    )

    comp_dir = ws_root / "pbg_x" / "composites"
    comp_dir.mkdir(parents=True)
    (comp_dir / "c.py").write_text(
        "@composite_generator(name=\"my_composite\")\n"
        "def c():\n"
        "    return RealProcess()\n",
        encoding="utf-8",
    )

    # Decoys: a real directory (outside ws_root, so it isn't reached except via
    # the .venv symlink) holding study.yaml + composites/*.py that must NEVER
    # surface if the symlink is (correctly) not traversed.
    venv_target = tmp_path / "shared-venv"
    decoy_study_dir = venv_target / "somepkg" / "studies" / "decoy"
    decoy_study_dir.mkdir(parents=True)
    (decoy_study_dir / "study.yaml").write_text(
        "composite: decoy_composite\nruns: []\n", encoding="utf-8"
    )
    decoy_comp_dir = venv_target / "somepkg" / "composites"
    decoy_comp_dir.mkdir(parents=True)
    (decoy_comp_dir / "decoy.py").write_text(
        "@composite_generator(name=\"decoy_composite\")\n"
        "def decoy():\n"
        "    return DecoyProcess()\n",
        encoding="utf-8",
    )

    (ws_root / ".venv").symlink_to(venv_target, target_is_directory=True)
    return ws_root


def test_iter_workspace_files_skips_symlinked_venv_by_suffix(tmp_path):
    ws_root = _build_workspace(tmp_path)
    found = {str(p) for p in iter_workspace_files(ws_root, suffixes=(".py",))}
    assert str(ws_root / "pbg_x" / "composites" / "c.py") in found
    assert not any(".venv" in p for p in found)
    assert not any("decoy" in p for p in found)


def test_iter_workspace_files_skips_symlinked_venv_by_name(tmp_path):
    ws_root = _build_workspace(tmp_path)
    found = {str(p) for p in iter_workspace_files(ws_root, names=("study.yaml",))}
    assert str(ws_root / "studies" / "foo" / "study.yaml") in found
    assert not any(".venv" in p for p in found)
    assert not any("decoy" in p for p in found)


def test_iter_workspace_files_does_not_descend_generic_symlinked_dir(tmp_path):
    ws_root = tmp_path / "ws2"
    ws_root.mkdir()
    (ws_root / "real.py").write_text("x = 1\n", encoding="utf-8")

    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "hidden.py").write_text("y = 2\n", encoding="utf-8")
    (ws_root / "linked").symlink_to(target, target_is_directory=True)

    found = {p.name for p in iter_workspace_files(ws_root, suffixes=(".py",))}
    assert found == {"real.py"}


def test_iter_workspace_files_never_hangs_on_symlink_loop(tmp_path):
    ws_root = tmp_path / "ws3"
    ws_root.mkdir()
    (ws_root / "real.py").write_text("x = 1\n", encoding="utf-8")
    # A directory symlink pointing back at an ancestor would loop forever if
    # followed; followlinks=False must make this a no-op prune, not a hang.
    (ws_root / "loop").symlink_to(ws_root, target_is_directory=True)

    found = {p.name for p in iter_workspace_files(ws_root, suffixes=(".py",))}
    assert found == {"real.py"}


def test_composite_study_stats_iter_study_yamls_excludes_venv_decoy(tmp_path):
    ws_root = _build_workspace(tmp_path)
    found = {str(p) for p in _iter_study_yamls(ws_root)}
    assert str(ws_root / "studies" / "foo" / "study.yaml") in found
    assert not any("decoy" in p for p in found)


def test_composite_study_stats_counts_real_study_not_decoy(tmp_path):
    ws_root = _build_workspace(tmp_path)
    out = composite_study_stats(ws_root, ["my_composite", "decoy_composite"])
    assert "my_composite" in out
    assert out["my_composite"]["studies"] == 1
    assert "decoy_composite" not in out


def test_process_study_stats_composite_files_excludes_venv_decoy(tmp_path):
    ws_root = _build_workspace(tmp_path)
    files = _composite_files(ws_root)
    found = {str(p) for p in files}
    assert str(ws_root / "pbg_x" / "composites" / "c.py") in found
    assert not any("decoy" in p for p in found)


def test_process_study_stats_only_credits_real_process(tmp_path):
    ws_root = _build_workspace(tmp_path)
    procs = [
        {"address": "pbg_x.composites.c.RealProcess", "name": "RealProcess"},
        {"address": "somepkg.composites.decoy.DecoyProcess", "name": "DecoyProcess"},
    ]
    out = process_study_stats(ws_root, procs)
    assert "pbg_x.composites.c.RealProcess" in out
    assert "somepkg.composites.decoy.DecoyProcess" not in out


def test_registry_annotate_use_counts_ignores_venv_decoy(tmp_path):
    ws_root = _build_workspace(tmp_path)
    data = {
        "processes": [
            {"address": "pbg_x.composites.c.RealProcess", "name": "RealProcess"},
            {"address": "somepkg.composites.decoy.DecoyProcess", "name": "DecoyProcess"},
        ]
    }
    registry._annotate_use_counts(data, ws_root)
    by_addr = {p["address"]: p for p in data["processes"]}
    assert by_addr["pbg_x.composites.c.RealProcess"]["composite_uses"] >= 1
    assert by_addr["somepkg.composites.decoy.DecoyProcess"]["composite_uses"] == 0


# ---------------------------------------------------------------------------
# Fix 3 / Fix 6 — env-configurable TTLs
# ---------------------------------------------------------------------------

def test_registry_ttl_reads_env_var(monkeypatch):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_REGISTRY_TTL", raising=False)
    monkeypatch.delenv("VIVARIUM_DASHBOARD_REGISTRY_TTL", raising=False)
    assert registry._registry_ttl() == pytest.approx(3600.0)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REGISTRY_TTL", "42")
    assert registry._registry_ttl() == pytest.approx(42.0)


def test_composites_ttl_reads_env_var(monkeypatch):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_COMPOSITES_TTL", raising=False)
    monkeypatch.delenv("VIVARIUM_DASHBOARD_COMPOSITES_TTL", raising=False)
    assert composites_query._composites_ttl() == pytest.approx(3600.0)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_COMPOSITES_TTL", "17")
    assert composites_query._composites_ttl() == pytest.approx(17.0)
