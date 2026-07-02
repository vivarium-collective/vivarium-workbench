"""Hermetic tests for ``lib.investigation_registry`` — the pure builder behind
GET /api/investigation-registry (the clean seam extracted from server.py).

Every external effect (server listing, peer HTTP, git worktrees, current
branch) is injected, so these never touch the network, subprocess, or git.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib import investigation_registry as ir


def _write_iset(ws: Path, slug: str, *, title=None, status="planning") -> None:
    d = ws / "investigations" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "investigation.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "name": slug,
        "title": title or slug,
        "status": status,
        "studies": [],
        "acceptance_criteria": [],
    }))


def test_build_registry_empty_workspace_no_peers(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    out = ir.build_investigation_registry(
        ws,
        this_url="http://127.0.0.1:9",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda url: None,
    )
    assert set(out.keys()) == {
        "current", "local_siblings", "running_others", "dormant_others",
    }
    assert out["current"]["slug"] is None
    assert out["current"]["worktree_path"] == str(ws.resolve())
    assert out["current"]["url"] == "http://127.0.0.1:9"
    assert out["running_others"] == []


def test_build_registry_current_and_siblings(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "alpha")
    _write_iset(ws, "beta")
    _write_iset(ws, "gamma")
    out = ir.build_investigation_registry(
        ws,
        this_url="http://127.0.0.1:9",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda url: None,
        list_worktrees_fn=lambda: [],
        scan_worktree_fn=lambda _p: [],
        current_branch_fn=lambda: None,
    )
    # Alphabetical-first fallback selects the current; the rest are siblings.
    assert out["current"]["slug"] == "alpha"
    assert [s["slug"] for s in out["local_siblings"]] == ["beta", "gamma"]


def test_build_registry_running_others_hermetic(tmp_path):
    """running_others is built ENTIRELY from injected hooks — no real network."""
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "alpha")
    servers = [
        {"name": "self", "path": str(ws.resolve()), "url": "http://127.0.0.1:1",
         "pid": 1, "_alive": True},
        {"name": "peer", "path": str(tmp_path / "peer"),
         "url": "http://127.0.0.1:2", "pid": 222, "_alive": True},
    ]
    out = ir.build_investigation_registry(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: servers,
        fetch_peer_fn=lambda url: {"slug": "peer-iset", "title": "Peer",
                                   "effective_status": "running"},
        list_worktrees_fn=lambda: [],
        scan_worktree_fn=lambda _p: [],
    )
    assert len(out["running_others"]) == 1
    o = out["running_others"][0]
    assert o["url"] == "http://127.0.0.1:2"
    assert o["slug"] == "peer-iset"
    assert o["pid"] == 222


def test_derive_this_url_prefers_catalog_record(tmp_path, monkeypatch):
    from pbg_superpowers import workspace_catalog
    monkeypatch.setattr(workspace_catalog, "find_running",
                        lambda ws: {"url": "http://10.0.0.5:8771"})
    assert ir.derive_this_url(tmp_path, "ignored:9") == "http://10.0.0.5:8771"


def test_derive_this_url_falls_back_to_host_header(tmp_path, monkeypatch):
    from pbg_superpowers import workspace_catalog
    monkeypatch.setattr(workspace_catalog, "find_running", lambda ws: None)
    assert ir.derive_this_url(tmp_path, "myhost:1234") == "http://myhost:1234"
    # No host at all -> loopback default.
    assert ir.derive_this_url(tmp_path, None) == "http://127.0.0.1"


def test_scan_worktree_investigations_filters_closed(tmp_path):
    wt = tmp_path / "wt"
    for slug, status in [("open-one", "planning"), ("closed-one", "closed"),
                         ("archived-one", "archived"), ("running-one", "running")]:
        _write_iset(wt, slug, status=status)
    found = ir.scan_worktree_investigations(str(wt))
    assert {e["slug"] for e in found} == {"open-one", "running-one"}


def test_scan_worktree_investigations_missing_dir(tmp_path):
    assert ir.scan_worktree_investigations(str(tmp_path / "nope")) == []


def test_list_other_worktrees_non_git_dir(tmp_path):
    assert ir.list_other_worktrees(tmp_path) == []
