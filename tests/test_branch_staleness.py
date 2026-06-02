"""Tests for stale-branch detection.

Addresses friction note 2026-05-27 #5: long-running investigation branches
drift from main; the dashboard should warn before the eventual merge produces
"trivial but tedious" conflicts.

Surface:
  - GET /api/branch-staleness[?branch=X&base=Y]
  - GET /api/work-status now includes `commits_behind`, `stale`,
    `stale_threshold`, `behind_ref`.
  - Helpers: `_commits_behind(branch, base)` and `_stale_branch_threshold()`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _init_workspace(tmp_path: Path, commits_behind: int = 0) -> Path:
    """Create a workspace with a feature branch N commits behind main.

    Layout:
      main:           c0 c1 c2 ... cN  (latest)
      feature-branch: c0               (created from c0, never advanced)

    So `git rev-list --count feature-branch..main` returns N.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws, check=True)
    (ws / "workspace.yaml").write_text("name: test-ws\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-m", "c0"], cwd=ws, check=True, capture_output=True)
    # Branch feature-branch from c0 BEFORE main advances.
    subprocess.run(["git", "checkout", "-b", "feature-branch"], cwd=ws, check=True, capture_output=True)
    # Now advance main by N commits.
    subprocess.run(["git", "checkout", "main"], cwd=ws, check=True, capture_output=True)
    for i in range(commits_behind):
        (ws / f"main-{i}.txt").write_text(str(i))
        subprocess.run(["git", "add", "."], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-m", f"main-c{i+1}"], cwd=ws, check=True, capture_output=True)
    # Leave feature-branch checked out — that's what dashboards see.
    subprocess.run(["git", "checkout", "feature-branch"], cwd=ws, check=True, capture_output=True)
    (ws / ".pbg").mkdir()
    return ws


# ---------------------------------------------------------------------------
# Helper-function tests
# ---------------------------------------------------------------------------


def test_commits_behind_zero_when_at_tip(tmp_path, monkeypatch):
    """branch == main → 0 behind."""
    ws = _init_workspace(tmp_path, commits_behind=0)
    monkeypatch.chdir(ws)
    import vivarium_dashboard.server as srv
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    n, ref = srv._commits_behind("feature-branch", "main")
    assert n == 0
    assert ref == "main"  # no origin/, falls back to local


def test_commits_behind_counts_main_advances(tmp_path, monkeypatch):
    """5 commits land on main while feature-branch sits → 5 behind."""
    ws = _init_workspace(tmp_path, commits_behind=5)
    monkeypatch.chdir(ws)
    import vivarium_dashboard.server as srv
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    n, ref = srv._commits_behind("feature-branch", "main")
    assert n == 5
    assert ref == "main"


def test_commits_behind_degrades_gracefully(tmp_path, monkeypatch):
    """Unknown base ref → (0, '') rather than raising."""
    ws = _init_workspace(tmp_path, commits_behind=0)
    monkeypatch.chdir(ws)
    import vivarium_dashboard.server as srv
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    n, ref = srv._commits_behind("feature-branch", "nope-not-a-real-base")
    assert n == 0
    assert ref == ""


def test_stale_threshold_default_is_20():
    import vivarium_dashboard.server as srv
    assert srv._stale_branch_threshold() == 20


def test_stale_threshold_env_override(monkeypatch):
    """PBG_STALE_BRANCH_THRESHOLD overrides the default."""
    monkeypatch.setenv("PBG_STALE_BRANCH_THRESHOLD", "5")
    import vivarium_dashboard.server as srv
    assert srv._stale_branch_threshold() == 5


def test_stale_threshold_clamps_to_positive(monkeypatch):
    """PBG_STALE_BRANCH_THRESHOLD=0 clamps to 1 (zero would always be stale)."""
    monkeypatch.setenv("PBG_STALE_BRANCH_THRESHOLD", "0")
    import vivarium_dashboard.server as srv
    assert srv._stale_branch_threshold() == 1


def test_stale_threshold_garbage_falls_back(monkeypatch):
    """Garbage env value → fall back to the default 20."""
    monkeypatch.setenv("PBG_STALE_BRANCH_THRESHOLD", "abc")
    import vivarium_dashboard.server as srv
    assert srv._stale_branch_threshold() == 20


# ---------------------------------------------------------------------------
# /api/branch-staleness endpoint
# ---------------------------------------------------------------------------


def test_branch_staleness_endpoint_returns_zero_for_fresh_branch(tmp_path, dashboard_client):
    """Fresh branch at main's tip → 0 behind, not stale."""
    ws = _init_workspace(tmp_path, commits_behind=0)
    client = dashboard_client(ws)
    resp = client.get("/api/branch-staleness?branch=feature-branch&base=main")
    assert resp.status_code == 200
    body = resp.json()
    assert body["branch"] == "feature-branch"
    assert body["base"] == "main"
    assert body["commits_behind"] == 0
    assert body["stale"] is False
    assert body["stale_threshold"] == 20


def test_branch_staleness_endpoint_flags_stale(tmp_path, dashboard_client):
    """25 commits behind main + threshold=20 → stale: True."""
    ws = _init_workspace(tmp_path, commits_behind=25)
    client = dashboard_client(ws)
    resp = client.get("/api/branch-staleness?branch=feature-branch&base=main")
    assert resp.status_code == 200
    body = resp.json()
    assert body["commits_behind"] == 25
    assert body["stale"] is True


def test_branch_staleness_endpoint_defaults_branch_to_current_head(tmp_path, dashboard_client):
    """No ?branch= → uses git branch --show-current (the active checkout)."""
    ws = _init_workspace(tmp_path, commits_behind=3)
    client = dashboard_client(ws)
    resp = client.get("/api/branch-staleness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["branch"] == "feature-branch"
    assert body["base"] == "main"  # default
    assert body["commits_behind"] == 3
