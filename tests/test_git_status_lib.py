"""Parity and unit tests for vivarium_dashboard.lib.git_status.

Every test builds a hermetic git repo in ``tmp_path`` (no touches to the real
repo).  The primary assertion is that the lib builder returns the expected dict
shape; secondary assertions compare lib-builder output to logic parity.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard.lib import git_status as gs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _git(ws: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in *ws*."""
    return subprocess.run(
        ["git", "-C", str(ws), *args],
        capture_output=True, text=True, check=check,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Tiny hermetic git repo: one commit on main."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("hello\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


@pytest.fixture()
def repo_with_branch(repo: Path) -> Path:
    """Hermetic repo with a second commit on a feature branch."""
    _git(repo, "checkout", "-b", "feature/x")
    (repo / "new_file.py").write_text("# new\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add new file")
    _git(repo, "checkout", "main")
    return repo


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_has_origin_remote_false(self, repo: Path) -> None:
        assert gs.has_origin_remote(repo) is False

    def test_stale_branch_threshold_default(self) -> None:
        assert gs.stale_branch_threshold() == 20

    def test_stale_branch_threshold_env(self, monkeypatch) -> None:
        monkeypatch.setenv("PBG_STALE_BRANCH_THRESHOLD", "5")
        assert gs.stale_branch_threshold() == 5

    def test_stale_branch_threshold_env_invalid(self, monkeypatch) -> None:
        monkeypatch.setenv("PBG_STALE_BRANCH_THRESHOLD", "notanint")
        assert gs.stale_branch_threshold() == 20

    def test_commits_behind_zero(self, repo: Path) -> None:
        # No origin, local base only
        cb, ref = gs.commits_behind(repo, "main", "main")
        assert cb == 0  # same ref → 0

    def test_commits_behind_feature_vs_main(self, repo_with_branch: Path) -> None:
        # feature/x is ahead of main by 1 commit, so main is 0 behind feature/x
        # But feature/x is 0 behind main (it branched from main HEAD)
        cb, ref = gs.commits_behind(repo_with_branch, "feature/x", "main")
        assert cb == 0  # feature/x already contains all of main

    def test_dirty_workspace_clean(self, repo: Path) -> None:
        result = gs.dirty_workspace(repo)
        assert result.strip() == ""

    def test_dirty_workspace_with_untracked(self, repo: Path) -> None:
        (repo / "untracked.txt").write_text("dirty\n")
        result = gs.dirty_workspace(repo)
        assert "untracked.txt" in result

    def test_dirty_workspace_excludes_reports(self, repo: Path) -> None:
        (repo / "reports").mkdir()
        (repo / "reports" / "foo.html").write_text("report\n")
        result = gs.dirty_workspace(repo)
        assert "reports/" not in result

    def test_dirty_workspace_excludes_out(self, repo: Path) -> None:
        (repo / "out").mkdir()
        (repo / "out" / "cache.json").write_text("{}\n")
        result = gs.dirty_workspace(repo)
        assert "out/" not in result

    def test_dirty_workspace_excludes_pbg(self, repo: Path) -> None:
        (repo / ".pbg").mkdir()
        (repo / ".pbg" / "state.json").write_text("{}\n")
        result = gs.dirty_workspace(repo)
        assert ".pbg/" not in result

    def test_submodule_paths_no_gitmodules(self, repo: Path) -> None:
        assert gs.submodule_paths(repo) == set()

    def test_is_generated_path(self) -> None:
        assert gs.is_generated_path("reports/foo.html")
        assert gs.is_generated_path("out/cache.json")
        assert gs.is_generated_path(".pbg/state.json")
        assert not gs.is_generated_path("studies/dnaa/spec.yaml")


# ---------------------------------------------------------------------------
# build_git_status
# ---------------------------------------------------------------------------

class TestBuildGitStatus:
    def test_not_a_git_repo(self, tmp_path: Path) -> None:
        """Non-git dir → returns the default result dict (no crash)."""
        result = gs.build_git_status(tmp_path)
        assert isinstance(result, dict)
        assert result["branch"] is None
        assert result["push_state"] == "no_origin"

    def test_git_repo_no_origin(self, repo: Path) -> None:
        result = gs.build_git_status(repo)
        assert result["branch"] == "main"
        assert result["push_state"] == "no_origin"  # no origin configured
        assert result["upstream_repo"] is None
        assert result["gh_available"] in (True, False)  # bool, not None
        assert result["has_active_workstream"] is False

    def test_includes_all_expected_keys(self, repo: Path) -> None:
        result = gs.build_git_status(repo)
        expected_keys = {
            "upstream_repo", "branch", "push_state", "ahead", "behind",
            "branch_url", "repo_url", "pr_number", "pr_url", "base",
            "ahead_of_base", "dirty_count", "compare_url", "pr_state",
            "gh_available", "has_active_workstream",
        }
        assert expected_keys.issubset(result.keys())

    def test_dirty_count_zero_no_origin(self, repo: Path) -> None:
        """Without an origin remote, build_git_status returns early before
        computing dirty_count (matches original _get_git_status behaviour)."""
        (repo / "dirty.txt").write_text("change\n")
        result = gs.build_git_status(repo)
        # Returns early after origin check fails → dirty_count stays at default 0
        assert result["dirty_count"] == 0


# ---------------------------------------------------------------------------
# build_work_status
# ---------------------------------------------------------------------------

class TestBuildWorkStatus:
    def test_no_state_file(self, repo: Path) -> None:
        result = gs.build_work_status(repo)
        assert result == {"active": False}

    def test_with_active_state(self, repo_with_branch: Path) -> None:
        pbg_dir = repo_with_branch / ".pbg"
        pbg_dir.mkdir()
        state = {
            "active_branch": "feature/x",
            "base": "main",
            "pushed": False,
        }
        (pbg_dir / "state.json").write_text(json.dumps(state))

        result = gs.build_work_status(repo_with_branch)
        assert result["active"] is True
        assert result["branch"] == "feature/x"
        assert result["base"] == "main"
        assert isinstance(result["commits_ahead"], int)
        assert isinstance(result["commits_behind"], int)
        assert isinstance(result["stale"], bool)
        assert "pr_number" in result

    def test_inactive_missing_keys(self, repo: Path) -> None:
        result = gs.build_work_status(repo)
        assert result == {"active": False}
        assert "branch" not in result


# ---------------------------------------------------------------------------
# build_branch_staleness
# ---------------------------------------------------------------------------

class TestBuildBranchStaleness:
    def test_with_branch_param(self, repo: Path) -> None:
        result = gs.build_branch_staleness(repo, branch="main", base="main")
        assert result["branch"] == "main"
        assert isinstance(result["commits_behind"], int)
        assert isinstance(result["stale"], bool)
        assert "stale_threshold" in result

    def test_auto_detect_branch(self, repo: Path) -> None:
        """When branch=None, auto-detects HEAD (should be 'main')."""
        result = gs.build_branch_staleness(repo, branch=None, base="main")
        assert result["branch"] == "main"

    def test_raises_no_branch_error_in_non_git(self, tmp_path: Path) -> None:
        """Non-git dir + no branch param → NoBranchError (→ HTTP 400)."""
        with pytest.raises(gs.NoBranchError):
            gs.build_branch_staleness(tmp_path, branch=None)

    def test_feature_branch_staleness(self, repo_with_branch: Path) -> None:
        result = gs.build_branch_staleness(repo_with_branch, branch="feature/x", base="main")
        assert result["branch"] == "feature/x"
        # feature/x has all of main's commits, so commits_behind==0
        assert result["commits_behind"] == 0
        assert result["stale"] is False


# ---------------------------------------------------------------------------
# build_dirty_status
# ---------------------------------------------------------------------------

class TestBuildDirtyStatus:
    def test_clean_repo(self, repo: Path) -> None:
        result = gs.build_dirty_status(repo)
        assert result["count"] == 0
        assert result["files"] == []

    def test_with_modified_file(self, repo: Path) -> None:
        (repo / "README.md").write_text("modified\n")
        result = gs.build_dirty_status(repo)
        assert result["count"] >= 1
        paths = [f["path"] for f in result["files"]]
        assert "README.md" in paths

    def test_files_have_status_and_path(self, repo: Path) -> None:
        (repo / "new.txt").write_text("new\n")
        result = gs.build_dirty_status(repo)
        for f in result["files"]:
            assert "status" in f
            assert "path" in f

    def test_raises_on_non_git_dir(self, tmp_path: Path) -> None:
        """git status --check=True fails in a non-git dir → CalledProcessError."""
        import subprocess
        with pytest.raises(subprocess.CalledProcessError):
            gs.build_dirty_status(tmp_path)


# ---------------------------------------------------------------------------
# list_branches
# ---------------------------------------------------------------------------

class TestListBranches:
    def test_no_stage_branches(self, repo: Path) -> None:
        result = gs.list_branches(repo)
        assert result["branches"] == []
        assert result["current"] == "main"

    def test_with_stage_branch(self, repo: Path) -> None:
        _git(repo, "checkout", "-b", "stage/feature-a")
        (repo / "feature_a.py").write_text("# a\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "feature a")
        _git(repo, "checkout", "main")

        result = gs.list_branches(repo)
        names = [b["name"] for b in result["branches"]]
        assert "stage/feature-a" in names
        b = next(b for b in result["branches"] if b["name"] == "stage/feature-a")
        assert "sha" in b["last_commit"]
        assert "subject" in b["last_commit"]
        assert isinstance(b["ahead_of_main"], int)

    def test_current_field(self, repo: Path) -> None:
        result = gs.list_branches(repo)
        assert result["current"] == "main"

    def test_non_git_dir_returns_error(self, tmp_path: Path) -> None:
        result = gs.list_branches(tmp_path)
        assert "error" in result


# ---------------------------------------------------------------------------
# build_branch_diff
# ---------------------------------------------------------------------------

class TestBuildBranchDiff:
    def test_valid_branch(self, repo_with_branch: Path) -> None:
        result = gs.build_branch_diff(repo_with_branch, "feature/x")
        assert result["branch"] == "feature/x"
        assert isinstance(result["log"], str)
        assert isinstance(result["diff_stat"], str)

    def test_branch_log_shows_commit(self, repo_with_branch: Path) -> None:
        result = gs.build_branch_diff(repo_with_branch, "feature/x")
        # feature/x has one commit not on main
        assert "add new file" in result["log"]

    def test_invalid_branch_name_raises(self, repo: Path) -> None:
        with pytest.raises(ValueError):
            gs.build_branch_diff(repo, "../evil")

    def test_empty_branch_raises(self, repo: Path) -> None:
        with pytest.raises(ValueError):
            gs.build_branch_diff(repo, "")

    def test_dotdot_in_branch_raises(self, repo: Path) -> None:
        with pytest.raises(ValueError):
            gs.build_branch_diff(repo, "feat..evil")
