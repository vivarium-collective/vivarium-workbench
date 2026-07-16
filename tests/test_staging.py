"""Tests for the git staging policy (lib.staging).

Pins three properties of PR-1: (1) behavior-preservation vs the legacy
``_STAGE_PATHS`` for a default layout, (2) the layout-correctness fix, and
(3) the science / environment boundary.
"""
from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib import staging
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


# The exact set the retired work_state._STAGE_PATHS staged (default layout).
LEGACY_STAGE_PATHS = {
    "studies/", "investigations/", "models/", "scripts/",
    "workspace.yaml", "pyproject.toml", ".gitmodules", ".gitignore",
    "external/",
}


def _wp(config: dict | None = None) -> WorkspacePaths:
    return WorkspacePaths.from_config(Path("/ws"), config or {})


def test_default_layout_union_matches_legacy_stage_paths():
    """Behavior-preserving: the science+env union == the legacy hardcoded list."""
    assert set(staging.commit_pathspec(_wp())) == LEGACY_STAGE_PATHS


def test_science_and_environment_partition_the_union():
    """The two lists are disjoint and together are the whole commit pathspec."""
    wp = _wp()
    sci = set(staging.science_paths(wp))
    env = set(staging.environment_paths(wp))
    assert sci.isdisjoint(env)
    assert sci | env == set(staging.commit_pathspec(wp))


def test_boundary_environment_owns_pyproject_science_does_not():
    """§2A.4: a science commit can never touch pyproject.toml / package code."""
    wp = _wp()
    assert "pyproject.toml" in staging.environment_paths(wp)
    assert "pyproject.toml" not in staging.science_paths(wp)


def test_layout_relocated_workspace_stages_relocated_dirs():
    """The audit bug fix: a `layout:`-relocated workspace stages the real dirs.

    The legacy literal `studies/` would never match `workspace/studies/`; the
    layout-driven policy resolves it correctly.
    """
    wp = _wp({"layout": {"studies": "workspace/studies",
                         "investigations": "workspace/investigations",
                         "scripts": "workspace/scripts"}})
    sci = staging.science_paths(wp)
    env = staging.environment_paths(wp)
    assert "workspace/studies/" in sci
    assert "workspace/investigations/" in sci
    assert "workspace/scripts/" in env
    # The default literals must NOT leak through when relocated.
    assert "studies/" not in sci
    assert "scripts/" not in env


def test_existing_filters_to_on_disk(tmp_path: Path):
    (tmp_path / "studies").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    got = staging.existing(tmp_path, ["studies/", "workspace.yaml", "investigations/", "pyproject.toml"])
    assert got == ["studies/", "workspace.yaml"]
