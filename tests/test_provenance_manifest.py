import json
import subprocess
from pathlib import Path

from vivarium_dashboard.lib import provenance_manifest as pm


def _init_git_repo(ws: Path, origin_url: str) -> str:
    """Init a git repo at ws with one commit and an origin remote. Returns full SHA."""
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (ws / "uv.lock").write_text("lock-contents-v1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    run = lambda *a: subprocess.run(["git", "-C", str(ws), *a], check=True,
                                    capture_output=True, text=True, env={**env})
    subprocess.run(["git", "init", "-q", str(ws)], check=True, env={**env})
    run("remote", "add", "origin", origin_url)
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    return subprocess.run(["git", "-C", str(ws), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def test_build_manifest_reads_git_and_lockfile(tmp_path):
    ws = tmp_path / "ws"
    sha = _init_git_repo(ws, "https://github.com/vivarium-collective/demo.git")
    m = pm.build_manifest(ws)
    assert m["commit"] == sha            # FULL sha, not short
    assert m["branch"] in ("main", "master")
    assert m["repo"] == "https://github.com/vivarium-collective/demo"  # .git stripped
    assert m["workspace"] == "demo"
    assert m["lockfile"].startswith("uv.lock@") and len(m["lockfile"]) == len("uv.lock@") + 12
    assert m["results"] == {"runs": []}  # no runs in a bare workspace
    assert m["simulator_id"] is None


def test_build_manifest_prefers_viv_build_json(tmp_path):
    ws = tmp_path / "ws"
    _init_git_repo(ws, "https://github.com/x/local.git")
    (ws / ".viv-build.json").write_text(json.dumps({
        "simulator_id": 42, "repo": "v2ecoli", "branch": "feat/x",
        "commit": "abc123def456", "repo_url": "https://github.com/vivarium-collective/v2ecoli",
    }))
    m = pm.build_manifest(ws)
    assert m["commit"] == "abc123def456"          # from build meta, not git HEAD
    assert m["repo"] == "https://github.com/vivarium-collective/v2ecoli"
    assert m["simulator_id"] == 42


from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace


def test_manifest_route(tmp_path):
    ws = tmp_path / "ws"
    _init_git_repo(ws, "https://github.com/x/demo.git")
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    client = TestClient(app)
    r = client.get("/api/source/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["workspace"] == "demo"
    assert body["lockfile"].startswith("uv.lock@")
