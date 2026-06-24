import subprocess
from pathlib import Path
from vivarium_dashboard import server


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_git_branch_commit_resolves(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "checkout", "-q", "-b", "feat/x")
    _git(tmp_path, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "m")
    branch, commit = server._git_branch_commit(str(tmp_path))
    assert branch == "feat/x"
    assert len(commit) >= 4 and commit.isalnum()


def test_git_branch_commit_non_git(tmp_path):
    assert server._git_branch_commit(str(tmp_path)) == ("", "")
