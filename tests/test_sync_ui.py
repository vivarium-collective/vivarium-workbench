import subprocess
from pathlib import Path
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace


def _init(ws: Path):
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (ws / "uv.lock").write_text("x\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(ws)], check=True, env=env)
    subprocess.run(["git", "-C", str(ws), "remote", "add", "origin",
                    "https://github.com/x/demo.git"], check=True, env=env)
    for a in (["add", "-A"], ["commit", "-q", "-m", "i"]):
        subprocess.run(["git", "-C", str(ws), *a], check=True, env=env, capture_output=True)


def test_manifest_contract_for_ui(tmp_path):
    """The Sync-to-local button needs repo+commit+workspace to render its command."""
    ws = tmp_path / "ws"; _init(ws)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    body = TestClient(app).get("/api/source/manifest").json()
    assert body["repo"] and body["commit"] and body["workspace"]
