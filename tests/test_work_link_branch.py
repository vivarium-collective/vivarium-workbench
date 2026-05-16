"""Tests for /api/work-link-branch."""
import json
import subprocess
from pathlib import Path
import pytest


def _init_workspace(tmp_path: Path) -> Path:
    """Create a minimal git workspace + workstream state."""
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws, check=True)
    (ws / "workspace.yaml").write_text("name: test-ws\nupstream_repo: vivarium-collective/v2ecoli\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feature-branch"], cwd=ws, check=True, capture_output=True)
    # Create .pbg/state.json so load_state returns active_branch
    (ws / ".pbg").mkdir(parents=True)
    (ws / ".pbg" / "state.json").write_text(
        json.dumps({"active_branch": "feature-branch", "base": "main", "pushed": False})
    )
    return ws


def test_link_branch_requires_active_workstream(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: test-ws\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws, check=True)
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, check=True, capture_output=True)
    client = dashboard_client(workspace=ws)
    resp = client.post("/api/work-link-branch", json={})
    assert resp.status_code == 409, resp.text
    assert "active workstream" in resp.json()["error"].lower()


def test_link_branch_invalid_repo_name(tmp_path, dashboard_client):
    ws = _init_workspace(tmp_path)
    client = dashboard_client(workspace=ws)
    resp = client.post("/api/work-link-branch", json={"upstream_repo": "no-slash"})
    assert resp.status_code == 400, resp.text


def test_link_branch_refuses_overwriting_existing_origin(tmp_path, dashboard_client):
    ws = _init_workspace(tmp_path)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/foo/bar.git"],
                   cwd=ws, check=True)
    client = dashboard_client(workspace=ws)
    resp = client.post("/api/work-link-branch", json={"upstream_repo": "vivarium-collective/v2ecoli", "push": False})
    assert resp.status_code == 409, resp.text
    assert "refusing to overwrite" in resp.json()["error"]


def test_link_branch_rejects_unknown_mode(tmp_path, dashboard_client):
    ws = _init_workspace(tmp_path)
    client = dashboard_client(workspace=ws)
    resp = client.post("/api/work-link-branch", json={"upstream_repo": "vivarium-collective/v2ecoli", "mode": "bogus"})
    assert resp.status_code == 400, resp.text
    assert "mode" in resp.json()["error"].lower()
