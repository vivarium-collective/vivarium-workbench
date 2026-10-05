"""``dirty_commit_all`` parses real ``git status --porcelain`` output (#1287).

``tests/test_git_commit_views_lib.py`` mocks ``subprocess.run`` and ``dirty_workspace`` with already-clean strings, so
it never sees the leading space real git puts on a modified-but-unstaged file (" M path"); that is how the first
path lost its first character. These tests run REAL git in a throw-away repository (only the workstream state, which
is not under test, is faked).
"""
import subprocess
from pathlib import Path

import pytest

from vivarium_workbench.lib import git_commit_views as gcv

GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]


def git(ws: Path, *args: str) -> str:
    return subprocess.run([*GIT, "-C", str(ws), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def ws(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    (root / "workspace" / "investigations" / "inv").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "workspace" / "investigations" / "inv" / "study.yaml").write_text("name: inv\n")
    (root / "docs" / "x.md").write_text("x\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    monkeypatch.setattr(gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "main"})
    return root


STUDY = "workspace/investigations/inv/study.yaml"


def test_a_modified_first_file_keeps_its_whole_path_and_scope(ws):
    """The reported case: one unstaged modification (" M workspace/...")."""
    (ws / STUDY).write_text("name: inv\nchanged: true\n")
    assert git(ws, "status", "--porcelain").startswith(" M "), "setup: real git prefixes this line with a space"
    body, code = gcv.dirty_commit_all(ws, {})
    assert code == 200
    assert body["paths"] == [STUDY]
    assert body["message"] == "chore(workspace): commit 1 pending file"
    assert STUDY in git(ws, "show", "--name-only", "--format=", "HEAD")      # the commit holds the file


@pytest.mark.parametrize("stage", [False, True], ids=["unstaged", "staged"])
def test_several_files_all_keep_their_paths(ws, stage):
    (ws / STUDY).write_text("name: inv\nchanged: true\n")
    (ws / "docs" / "x.md").write_text("changed\n")
    (ws / "docs" / "new.md").write_text("new\n")
    if stage:
        git(ws, "add", "-A")
    body, code = gcv.dirty_commit_all(ws, {})
    assert code == 200
    assert sorted(body["paths"]) == sorted([STUDY, "docs/x.md", "docs/new.md"])
    assert all(not p.startswith(("rkspace", "orkspace", "ocs", "cs/")) for p in body["paths"])


def test_an_untracked_first_file_is_unchanged(ws):
    (ws / "docs" / "new.md").write_text("new\n")
    body, code = gcv.dirty_commit_all(ws, {})
    assert code == 200 and body["paths"] == ["docs/new.md"] and body["message"] == "docs: commit 1 pending file"


def test_a_clean_tree_is_still_refused(ws):
    body, code = gcv.dirty_commit_all(ws, {})
    assert code == 409 and "clean" in body["error"]
