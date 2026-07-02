# tests/test_sync_materialize.py
import subprocess
from pathlib import Path

from vivarium_workbench.lib import sync_materialize as sm


def _make_origin(path: Path) -> tuple[str, str]:
    """Create a real local git repo (acts as a clone source). Returns (url, full sha)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (path / "uv.lock").write_text("lock-contents-v1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    for a in (["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    return f"file://{path}", sha


def test_clone_checkout_lands_exact_commit(tmp_path):
    url, sha = _make_origin(tmp_path / "origin")
    dest = tmp_path / "local"
    body, status = sm.git_clone_checkout(url, sha, dest)
    assert status == 200, body
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == sha
    assert (dest / "uv.lock").is_file()


def test_verify_lockfile_matches(tmp_path):
    url, sha = _make_origin(tmp_path / "origin")
    dest = tmp_path / "local"
    sm.git_clone_checkout(url, sha, dest)
    from vivarium_workbench.lib.provenance_manifest import lockfile_hash
    expected = lockfile_hash(tmp_path / "origin")
    body, status = sm.verify_lockfile(dest, expected)
    assert status == 200, body


def test_verify_lockfile_mismatch_is_409(tmp_path):
    url, sha = _make_origin(tmp_path / "origin")
    dest = tmp_path / "local"
    sm.git_clone_checkout(url, sha, dest)
    body, status = sm.verify_lockfile(dest, "uv.lock@deadbeefcafe")
    assert status == 409
    assert "lockfile" in body["error"].lower()
