"""Tests for work_state.load_state_or_adopt_current().

Adopts the current git HEAD as the workstream when state.json has no
active_branch — so workstream-gated endpoints work on branches created
outside the dashboard (git worktree add, manual git checkout -b, etc.)
without requiring the user to click "Start workstream" first.
"""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


def _init_repo(ws: Path, branch: str = "main") -> None:
    ws.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", branch], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws, check=True)
    (ws / "workspace.yaml").write_text("name: test-ws\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, check=True, capture_output=True)


def _patch_root(ws: Path):
    """Patch workspace_root() so work_state reads from this fixture."""
    return patch("vivarium_dashboard.lib.work_state.workspace_root", return_value=ws)


def test_adopts_current_feature_branch_when_state_empty(tmp_path):
    from vivarium_dashboard.lib import work_state

    ws = tmp_path / "ws"
    _init_repo(ws, branch="main")
    subprocess.run(["git", "checkout", "-b", "feat/x"], cwd=ws, check=True, capture_output=True)

    with _patch_root(ws):
        state = work_state.load_state_or_adopt_current()
        assert state["active_branch"] == "feat/x"
        assert state["base"] == "main"
        assert state["adopted"] is True
        # Persisted to .pbg/state.json
        assert json.loads((ws / ".pbg" / "state.json").read_text())["active_branch"] == "feat/x"


def test_skips_when_on_main(tmp_path):
    from vivarium_dashboard.lib import work_state

    ws = tmp_path / "ws"
    _init_repo(ws, branch="main")

    with _patch_root(ws):
        state = work_state.load_state_or_adopt_current()
        assert state == {}, "should refuse to adopt main as workstream"
        assert not (ws / ".pbg" / "state.json").exists()


def test_skips_when_on_master(tmp_path):
    from vivarium_dashboard.lib import work_state

    ws = tmp_path / "ws"
    _init_repo(ws, branch="master")

    with _patch_root(ws):
        state = work_state.load_state_or_adopt_current()
        assert state == {}


def test_idempotent_when_state_already_has_active_branch(tmp_path):
    from vivarium_dashboard.lib import work_state

    ws = tmp_path / "ws"
    _init_repo(ws, branch="main")
    subprocess.run(["git", "checkout", "-b", "feat/x"], cwd=ws, check=True, capture_output=True)
    (ws / ".pbg").mkdir()
    (ws / ".pbg" / "state.json").write_text(
        json.dumps({"active_branch": "feat/preexisting", "base": "main", "pushed": True})
    )

    with _patch_root(ws):
        state = work_state.load_state_or_adopt_current()
        # Returns existing state, does NOT overwrite with git HEAD.
        assert state["active_branch"] == "feat/preexisting"
        assert state["pushed"] is True
        assert "adopted" not in state


def test_no_op_outside_git_repo(tmp_path):
    from vivarium_dashboard.lib import work_state

    ws = tmp_path / "not-a-repo"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: not-a-repo\n")

    with _patch_root(ws):
        state = work_state.load_state_or_adopt_current()
        assert state == {}


def test_marks_pushed_true_when_local_matches_origin(tmp_path):
    from vivarium_dashboard.lib import work_state

    # Bare "remote" repo
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)

    ws = tmp_path / "ws"
    _init_repo(ws, branch="main")
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=ws, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feat/y"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "feat/y"], cwd=ws, check=True, capture_output=True)

    with _patch_root(ws):
        state = work_state.load_state_or_adopt_current()
        assert state["active_branch"] == "feat/y"
        assert state["pushed"] is True


def test_marks_pushed_false_when_branch_has_local_only_commits(tmp_path):
    from vivarium_dashboard.lib import work_state

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)

    ws = tmp_path / "ws"
    _init_repo(ws, branch="main")
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=ws, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feat/z"], cwd=ws, check=True, capture_output=True)
    # Local-only commit, never pushed.
    (ws / "added.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-m", "local"], cwd=ws, check=True, capture_output=True)

    with _patch_root(ws):
        state = work_state.load_state_or_adopt_current()
        assert state["active_branch"] == "feat/z"
        assert state["pushed"] is False
