"""active_branch_action must never stage large untracked artifact dirs."""
import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard.lib import work_state


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A minimal workspace git repo on a stage/* branch with work_state set."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(["init"], ws)
    _git(["config", "user.email", "t@t"], ws)
    _git(["config", "user.name", "t"], ws)
    (ws / "workspace.yaml").write_text("name: test\n")
    (ws / "reports").mkdir()
    (ws / "reports" / "index.html").write_text("<html></html>")
    _git(["add", "-A"], ws)
    _git(["commit", "-m", "init"], ws)
    _git(["checkout", "-b", "stage/test"], ws)
    from vivarium_dashboard.lib._root import set_workspace_root
    set_workspace_root(ws)
    # Point work_state at this repo's active branch.
    monkeypatch.setattr(work_state, "load_state",
                        lambda: {"active_branch": "stage/test"})
    monkeypatch.setattr(work_state, "save_state", lambda state: None)
    return ws


def test_untracked_out_dir_is_not_committed(repo):
    # A huge untracked artifact dir appears, exactly like the ParCa cache.
    (repo / "out").mkdir()
    (repo / "out" / "cache").mkdir()
    (repo / "out" / "cache" / "big.bin").write_text("x" * 1000)

    def action():
        (repo / "studies").mkdir(exist_ok=True)
        (repo / "studies" / "new.yaml").write_text("k: v\n")

    resp, code = work_state.active_branch_action(repo, "test commit", action)
    assert code == 200, resp
    # The commit contains studies/new.yaml but NOT anything under out/.
    files = _git(["show", "--name-only", "--format=", "HEAD"], repo).stdout
    assert "studies/new.yaml" in files
    assert "out/" not in files
