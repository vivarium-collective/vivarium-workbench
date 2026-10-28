"""Tests for ``POST /api/iset-close``
(``lib.iset_close_views.iset_close``).

Phase 2.1h (rewire-first): this endpoint wraps
``discovery_engine.investigation_close.close_investigation`` unchanged — the
plugin still renders the report, derives contributors, stamps the YAML, and
commits/PRs; only the caller (the workbench, on behalf of ``/viva-investigation
close``) moves. These tests exercise the lib builder directly (the same
"endpoint test calls the lib fn" idiom as ``test_study_readout_migrate_endpoint``)
against a git fixture mirroring the plugin's own ``test_investigation_close``.
All cases use ``no_pr`` + ``skip_report`` so no ``gh`` / report renderer is
needed.
"""
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import iset_close_views as views


def _git(ws: Path, *args: str, env: dict | None = None) -> str:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    proc = subprocess.run(
        ["git", "-C", str(ws), *args],
        capture_output=True, text=True, check=True, env=full_env,
    )
    return proc.stdout


@pytest.fixture
def ws_with_investigation(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("schema_version: 2\nname: ws\n")
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.name", "Initial Author")
    _git(ws, "config", "user.email", "init@example.com")
    _git(ws, "add", "workspace.yaml")
    _git(ws, "commit", "-q", "-m", "init")

    _git(ws, "checkout", "-q", "-b", "my-inv")
    inv_dir = ws / "investigations" / "my-inv"
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "name": "my-inv", "title": "My Investigation",
        "status": "planning", "studies": [],
    }))
    _git(ws, "add", "investigations/my-inv/investigation.yaml")
    _git(ws, "commit", "-q", "-m", "feat(investigation): scaffold my-inv",
         env={"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.com",
              "GIT_COMMITTER_NAME": "Alice", "GIT_COMMITTER_EMAIL": "alice@example.com"})
    return ws


def test_missing_slug_400(ws_with_investigation):
    body, status = views.iset_close(ws_with_investigation, {})
    assert status == 400
    assert "slug" in body["error"]


def test_unknown_branch_404(ws_with_investigation):
    body, status = views.iset_close(
        ws_with_investigation, {"slug": "no-such-inv", "no_pr": True, "skip_report": True}
    )
    assert status == 404
    assert "not found" in body["error"]


def test_dry_run_writes_nothing_returns_result(ws_with_investigation):
    inv_yaml = ws_with_investigation / "investigations" / "my-inv" / "investigation.yaml"
    before = inv_yaml.read_text()

    body, status = views.iset_close(
        ws_with_investigation,
        {"slug": "my-inv", "dry_run": True, "no_pr": True, "skip_report": True},
    )

    assert status == 200
    assert body["slug"] == "my-inv"
    assert body["branch"] == "my-inv"
    assert body["dry_run"] is True
    assert "contributors" in body and "actions" in body
    # dry-run: investigation.yaml untouched, status still planning.
    assert inv_yaml.read_text() == before
    assert yaml.safe_load(before)["status"] == "planning"


def test_write_stamps_yaml_and_commits(ws_with_investigation):
    inv_yaml = ws_with_investigation / "investigations" / "my-inv" / "investigation.yaml"

    body, status = views.iset_close(
        ws_with_investigation,
        {"slug": "my-inv", "no_pr": True, "skip_report": True},
    )

    assert status == 200
    assert body["dry_run"] is False
    spec = yaml.safe_load(inv_yaml.read_text())
    assert spec["status"] == "closed"
    assert "closed_at" in spec
    # a close commit landed on the branch.
    log = _git(ws_with_investigation, "log", "--oneline", "-1")
    assert "close" in log.lower()


def test_equivalence_with_direct_close_call(tmp_path):
    """The endpoint result must match calling
    ``investigation_close.close_investigation`` directly (dry-run path)."""
    pbg_close = pytest.importorskip("discovery_engine.investigation_close")

    # Build two identical fixtures.
    def build(root):
        ws = root / "ws"
        ws.mkdir(parents=True)
        (ws / "workspace.yaml").write_text("schema_version: 2\nname: ws\n")
        _git(ws, "init", "-q", "-b", "main")
        _git(ws, "config", "user.name", "A"); _git(ws, "config", "user.email", "a@e.com")
        _git(ws, "add", "workspace.yaml"); _git(ws, "commit", "-q", "-m", "init")
        _git(ws, "checkout", "-q", "-b", "my-inv")
        d = ws / "investigations" / "my-inv"; d.mkdir(parents=True)
        (d / "investigation.yaml").write_text(yaml.safe_dump({
            "schema_version": 2, "name": "my-inv", "status": "planning", "studies": [],
        }))
        _git(ws, "add", "."); _git(ws, "commit", "-q", "-m", "feat(investigation): scaffold my-inv",
             env={"GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@e.com",
                  "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@e.com"})
        return ws

    ws1 = build(tmp_path / "ep")
    body, status = views.iset_close(
        ws1, {"slug": "my-inv", "dry_run": True, "no_pr": True, "skip_report": True}
    )
    assert status == 200

    ws2 = build(tmp_path / "direct")
    direct = pbg_close.close_investigation(
        ws2, "my-inv", dry_run=True, auto_pr=False, skip_report=True
    ).to_dict()

    assert body["slug"] == direct["slug"]
    assert body["branch"] == direct["branch"]
    assert body["dry_run"] == direct["dry_run"]
    assert [c.get("name") for c in body["contributors"]] == [c.get("name") for c in direct["contributors"]]
