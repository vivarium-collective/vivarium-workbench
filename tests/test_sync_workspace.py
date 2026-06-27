# tests/test_sync_workspace.py
import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard.lib import sync_workspace as sw


def _make_origin(path: Path) -> tuple[str, str]:
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


@pytest.fixture
def _no_real_uv(monkeypatch):
    monkeypatch.setattr(sw.sync_materialize, "run_uv_sync", lambda ws, **k: ({"ok": True}, 200))


@pytest.fixture
def _capture_catalog(monkeypatch):
    added = {}
    def _add(path, name=None, package=None):
        added["path"] = str(path); added["name"] = name
        return {"path": str(path), "name": name}
    monkeypatch.setattr(sw, "_catalog_add", _add)
    return added


def test_sync_from_manifest_happy_path(tmp_path, _no_real_uv, _capture_catalog):
    url, sha = _make_origin(tmp_path / "origin")
    from vivarium_dashboard.lib.provenance_manifest import lockfile_hash
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": lockfile_hash(tmp_path / "origin"), "results": {"runs": []}}
    dest = tmp_path / "local"
    body, status = sw.sync_from_manifest(manifest, dest)
    assert status == 200, body
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == sha
    assert _capture_catalog["path"] == str(dest.resolve())


def test_sync_aborts_on_lockfile_mismatch(tmp_path, _no_real_uv, _capture_catalog):
    url, sha = _make_origin(tmp_path / "origin")
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": "uv.lock@deadbeefcafe", "results": {"runs": []}}
    body, status = sw.sync_from_manifest(manifest, tmp_path / "local")
    assert status == 409
    assert _capture_catalog == {}  # never registered a mismatched workspace


def test_post_sync_runs_only_when_flagged(tmp_path, _no_real_uv, _capture_catalog):
    url, sha = _make_origin(tmp_path / "origin")
    from vivarium_dashboard.lib.provenance_manifest import lockfile_hash
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": lockfile_hash(tmp_path / "origin"), "results": {"runs": []},
                "post_sync": ["touch POST_SYNC_RAN"]}
    dest = tmp_path / "local"
    # default: post_sync NOT run
    sw.sync_from_manifest(manifest, dest)
    assert not (dest / "POST_SYNC_RAN").exists()


def test_post_sync_runs_when_enabled(tmp_path, _no_real_uv, _capture_catalog):
    url, sha = _make_origin(tmp_path / "origin")
    from vivarium_dashboard.lib.provenance_manifest import lockfile_hash
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": lockfile_hash(tmp_path / "origin"), "results": {"runs": []},
                "post_sync": ["touch POST_SYNC_RAN"]}
    dest = tmp_path / "local"
    sw.sync_from_manifest(manifest, dest, run_post_sync=True)
    assert (dest / "POST_SYNC_RAN").exists()
