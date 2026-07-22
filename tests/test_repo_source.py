"""RepoSource phase-1 staging (materialization-lifecycle §2/§5a): the git adapter
— bare-mirror cache, worktree checkout, ref resolution, failure surfacing. Uses a
LOCAL git repo as the origin, so it is offline + CI-safe (git is present in CI)."""
import shutil
import subprocess

import pytest

from vivarium_workbench.lib import repo_source as rs

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git not on PATH")


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


def _origin(root):
    """A local origin repo: commit c1 (f=v1) tagged v1.0, then c2 (f=v2) on main,
    plus a `feature` branch at c2."""
    root.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q", "-b", "main"], root)
    _run(["git", "config", "user.email", "t@t"], root)
    _run(["git", "config", "user.name", "t"], root)
    (root / "f.txt").write_text("v1")
    _run(["git", "add", "."], root)
    _run(["git", "commit", "-qm", "c1"], root)
    _run(["git", "tag", "v1.0"], root)
    (root / "f.txt").write_text("v2")
    _run(["git", "commit", "-qam", "c2"], root)
    _run(["git", "branch", "feature"], root)
    return root


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "repo-store"
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REPO_STORE", str(d))
    return d


@pytest.fixture
def origin(tmp_path):
    return str(_origin(tmp_path / "origin"))


def test_stage_branch_checks_out_head(store, origin):
    res = rs.stage(origin, "main")
    assert (res.path / "f.txt").read_text() == "v2"
    assert len(res.commit) == 40
    assert res.repo == origin


def test_stage_tag_checks_out_that_commit(store, origin):
    res = rs.stage(origin, "v1.0")
    assert (res.path / "f.txt").read_text() == "v1"          # the tagged commit


def test_stage_sha_checks_out(store, origin):
    sha = rs.stage(origin, "v1.0").commit
    res = rs.stage(origin, sha)
    assert res.commit == sha
    assert (res.path / "f.txt").read_text() == "v1"


def test_branch_and_tag_resolve_to_different_commits(store, origin):
    assert rs.stage(origin, "main").commit != rs.stage(origin, "v1.0").commit


def test_same_commit_reuses_one_worktree(store, origin):
    a = rs.stage(origin, "v1.0")
    b = rs.stage(origin, "v1.0")
    assert a.path == b.path                                  # cache hit, same staging


def test_mirror_is_cloned_once_and_reused(store, origin):
    rs.stage(origin, "main")                                 # clones the mirror
    mirrors = list((store / "mirrors").glob("*.git"))
    assert len(mirrors) == 1
    rs.stage(origin, "feature")                              # reuses it (fetch, no re-clone)
    assert list((store / "mirrors").glob("*.git")) == mirrors


def test_fetch_picks_up_new_commits(store, origin, tmp_path):
    """A second stage of a moved branch sees the new commit (mirror fetch, §2)."""
    first = rs.stage(origin, "main")
    (tmp_path / "origin" / "f.txt").write_text("v3")
    _run(["git", "commit", "-qam", "c3"], tmp_path / "origin")
    second = rs.stage(origin, "main")
    assert second.commit != first.commit
    assert (second.path / "f.txt").read_text() == "v3"


def test_unknown_ref_raises(store, origin):
    with pytest.raises(rs.RepoStagingError) as ei:
        rs.stage(origin, "no-such-ref")
    assert "not found" in str(ei.value)


def test_unreachable_repo_raises(store, tmp_path):
    with pytest.raises(rs.RepoStagingError):
        rs.stage(str(tmp_path / "does-not-exist"), "main", timeout=30)


def test_empty_repo_raises(store):
    with pytest.raises(rs.RepoStagingError):
        rs.stage("", "main")
