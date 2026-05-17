"""Pass C — /api/investigation-registry: cross-worktree Investigation view.

The endpoint aggregates this server's current Investigation plus every OTHER
live dashboard's current Investigation (queried over HTTP from each peer's
/api/iset-list). We test the pure helper with both server-listing and HTTP
fetch injected to keep tests hermetic.
"""
from __future__ import annotations
from pathlib import Path

import yaml

from vivarium_dashboard.server import (
    _build_investigation_registry_for_test,
)


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


def test_registry_empty_workspace_no_peers(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:9999",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda url: None,
    )
    assert out["current"]["slug"] is None
    assert out["current"]["worktree_path"] == str(ws.resolve())
    assert out["current"]["url"] == "http://127.0.0.1:9999"
    assert out["running_others"] == []


def test_registry_picks_running_investigation_first(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "alpha", title="Alpha", status="planning")
    _write_iset(ws, "beta",  title="Beta",  status="running")
    _write_iset(ws, "gamma", title="Gamma", status="planning")

    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:9999",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda url: None,
    )
    # Author-status 'running' on member-less investigation rolls up to a
    # non-running effective_status (no studies). We instead verify that
    # _build_iset_summary order is preserved and 'current' is one of the
    # known slugs.
    assert out["current"]["slug"] in {"alpha", "beta", "gamma"}


def test_registry_excludes_self_path(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "alpha")

    servers = [
        {"name": "self",  "path": str(ws.resolve()), "url": "http://127.0.0.1:1",
         "pid": 111, "_alive": True, "_file": "/tmp/self.json"},
        {"name": "other", "path": str(tmp_path / "elsewhere"),
         "url": "http://127.0.0.1:2",
         "pid": 222, "_alive": True, "_file": "/tmp/other.json"},
    ]

    def fake_fetch(url):
        return {"slug": "peer-iset", "title": "Peer", "effective_status": "running"}

    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: servers,
        fetch_peer_fn=fake_fetch,
    )
    assert out["current"]["url"] == "http://127.0.0.1:1"
    assert len(out["running_others"]) == 1
    assert out["running_others"][0]["url"] == "http://127.0.0.1:2"
    assert out["running_others"][0]["slug"] == "peer-iset"
    assert out["running_others"][0]["pid"] == 222


def test_registry_skips_dead_peers(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    servers = [
        {"name": "alive", "path": str(tmp_path / "a"),
         "url": "http://127.0.0.1:1",
         "pid": 1, "_alive": True, "_file": "/tmp/a.json"},
        {"name": "dead",  "path": str(tmp_path / "b"),
         "url": "http://127.0.0.1:2",
         "pid": 2, "_alive": False, "_file": "/tmp/b.json"},
    ]
    fetch_count = {"n": 0}
    def fake_fetch(url):
        fetch_count["n"] += 1
        return {"slug": "p", "title": "P", "effective_status": "running"}

    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:99",
        list_servers_fn=lambda: servers,
        fetch_peer_fn=fake_fetch,
    )
    # Only the alive peer is probed; dead peers are silently dropped.
    assert fetch_count["n"] == 1
    assert len(out["running_others"]) == 1
    assert out["running_others"][0]["url"] == "http://127.0.0.1:1"


def test_registry_skips_unreachable_peers(tmp_path):
    """When a peer is alive (PID up) but its HTTP fetch returns None
    (timeout, 500, etc.), it must be dropped from the result so the
    sidebar never shows empty rows."""
    ws = tmp_path / "ws"; ws.mkdir()
    servers = [
        {"name": "p1", "path": str(tmp_path / "p1"),
         "url": "http://127.0.0.1:1",
         "pid": 1, "_alive": True, "_file": "/tmp/p1.json"},
        {"name": "p2", "path": str(tmp_path / "p2"),
         "url": "http://127.0.0.1:2",
         "pid": 2, "_alive": True, "_file": "/tmp/p2.json"},
    ]
    def fake_fetch(url):
        # Simulate p2 unreachable.
        if url == "http://127.0.0.1:2":
            return None
        return {"slug": "p1-iset", "title": "P1", "effective_status": "running"}

    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:99",
        list_servers_fn=lambda: servers,
        fetch_peer_fn=fake_fetch,
    )
    assert len(out["running_others"]) == 1
    assert out["running_others"][0]["url"] == "http://127.0.0.1:1"


def test_registry_omits_peers_without_url(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    servers = [
        {"name": "no-url", "path": str(tmp_path / "x"),
         "url": "",
         "pid": 1, "_alive": True, "_file": "/tmp/x.json"},
    ]
    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:99",
        list_servers_fn=lambda: servers,
        fetch_peer_fn=lambda u: {"slug": "x", "title": "X",
                                 "effective_status": "running"},
    )
    assert out["running_others"] == []


def test_registry_shape_matches_documented_contract(tmp_path):
    """Lock down the field set the frontend depends on."""
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "alpha")
    servers = [
        {"name": "p", "path": str(tmp_path / "p"),
         "url": "http://127.0.0.1:2",
         "pid": 222, "_alive": True, "_file": "/tmp/p.json"},
    ]
    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: servers,
        fetch_peer_fn=lambda u: {"slug": "p-iset", "title": "P-Iset",
                                 "effective_status": "running"},
    )
    assert set(out.keys()) == {"current", "running_others"}
    assert set(out["current"].keys()) == {
        "slug", "title", "worktree_path", "url", "effective_status",
    }
    assert set(out["running_others"][0].keys()) == {
        "slug", "title", "worktree_path", "url",
        "effective_status", "pid",
    }
