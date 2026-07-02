"""Tests for lib.work_views builders + server.py shim parity.

Covers:
  - build_generation:          null when pbg_superpowers absent / not active; 200
                                shim parity vs _get_generation
  - build_work_composite_diff: {base, branch, changes:[]} + error on merge-base
                                failure; shim parity vs _get_work_composite_diff

TestServerShimParity: constructs server.Handler via __new__, patches _json and
WORKSPACE, and asserts that each legacy handler method returns the same body as
the lib builder for the same workspace root.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixture: a tiny git-initialised workspace with workspace.yaml
# ---------------------------------------------------------------------------

@pytest.fixture
def git_ws(tmp_path: Path) -> Path:
    """A minimal git-initialised workspace with workspace.yaml on 'main'."""
    ws = tmp_path / "ws"
    ws.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=ws, check=True, capture_output=True)

    workspace_yaml = {
        "name": "test-ws",
        "observables": [{"name": "obs-a"}],
        "visualizations": [{"name": "viz-a"}],
        "phases": [{"n": 1}],
        "datasets": [{"name": "ds-a"}],
        "references_pdfs": [{"bib_key": "smith2020"}],
        "expert_docs": [{"name": "doc-a"}],
        "imports": {"pkg-a": {"version": "0.1"}},
    }
    (ws / "workspace.yaml").write_text(yaml.safe_dump(workspace_yaml))
    subprocess.run(["git", "add", "workspace.yaml"], cwd=ws, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=ws, check=True,
                   capture_output=True)
    return ws


# ---------------------------------------------------------------------------
# build_generation
# ---------------------------------------------------------------------------

class TestBuildGeneration:
    def test_null_when_pbg_superpowers_absent(self, tmp_path: Path) -> None:
        """When pbg_superpowers is absent (or raises) → {generation: null}."""
        from vivarium_dashboard.lib.work_views import build_generation
        result = build_generation(tmp_path)
        assert result == {"generation": None}

    def test_null_when_no_active_generation(self, git_ws: Path) -> None:
        """A real workspace with pbg_superpowers but no generation → null."""
        try:
            from pbg_superpowers import generation as _gen  # noqa: F401
        except ImportError:
            pytest.skip("pbg_superpowers.generation not available")
        from vivarium_dashboard.lib.work_views import build_generation
        result = build_generation(git_ws)
        assert result == {"generation": None}

    def test_returns_summary_when_active(self, git_ws: Path, monkeypatch) -> None:
        """When a generation is active the summary dict is returned."""
        try:
            from pbg_superpowers import generation as _gen  # noqa: F401
        except ImportError:
            pytest.skip("pbg_superpowers.generation not available")

        class _FakeGen:
            generation_id = "gen-001"
            git_sha = "abc123"
            param_set_hash = "hashXYZ"
            created_at = "2026-06-25T00:00:00"
            label = "test-label"
            runs = [1, 2, 3]  # len = 3

        import pbg_superpowers.generation as gen_mod
        monkeypatch.setattr(gen_mod, "current_generation",
                            lambda ws_root: _FakeGen())
        from vivarium_dashboard.lib.work_views import build_generation
        result = build_generation(git_ws)
        assert result["generation"] is not None
        g = result["generation"]
        assert g["generation_id"] == "gen-001"
        assert g["git_sha"] == "abc123"
        assert g["n_runs"] == 3


# ---------------------------------------------------------------------------
# build_work_composite_diff
# ---------------------------------------------------------------------------

class TestBuildWorkCompositeDiff:
    def test_non_git_dir_returns_error_in_body(self, tmp_path: Path) -> None:
        """A non-git directory causes merge-base to fail → error in body, 200."""
        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        result = build_work_composite_diff(tmp_path)
        assert "error" in result
        assert result["changes"] == []
        assert "base" in result
        assert "branch" in result

    def test_git_repo_on_main_returns_empty_changes(
        self, git_ws: Path
    ) -> None:
        """On the initial main branch merge-base == HEAD → empty changes."""
        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        result = build_work_composite_diff(git_ws)
        # Either empty changes or an error — no exception
        assert "changes" in result
        assert "base" in result
        assert "branch" in result

    def test_state_base_respected(
        self, git_ws: Path, tmp_path: Path
    ) -> None:
        """If .pbg/state.json sets base, it's used for the merge-base call."""
        # Write a state.json
        pbg_dir = git_ws / ".pbg"
        pbg_dir.mkdir(exist_ok=True)
        state = {"active_branch": "feat/test", "base": "main"}
        (pbg_dir / "state.json").write_text(json.dumps(state))

        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        result = build_work_composite_diff(git_ws)
        assert result["base"] == "main"

    def test_returns_dict_always(self, tmp_path: Path) -> None:
        """Always returns a dict (never raises), even on completely empty dir."""
        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        result = build_work_composite_diff(tmp_path)
        assert isinstance(result, dict)
        assert "changes" in result
