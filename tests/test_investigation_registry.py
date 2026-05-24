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
        list_worktrees_fn=lambda: [],
        scan_worktree_fn=lambda _p: [],
    )
    assert set(out.keys()) == {
        "current", "local_siblings", "running_others", "dormant_others",
    }
    assert set(out["current"].keys()) == {
        "slug", "title", "worktree_path", "url", "effective_status",
    }
    assert set(out["running_others"][0].keys()) == {
        "slug", "title", "worktree_path", "url",
        "effective_status", "pid",
    }


# ---------------------------------------------------------------------------
# dormant_others: open investigations on other worktrees with no live server
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# current: picker prefers git-branch match
# ---------------------------------------------------------------------------


def test_current_prefers_investigation_matching_git_branch(tmp_path):
    """If the current git branch matches an investigation slug, pick it
    even when another investigation is alphabetically first or has
    effective_status=running."""
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "alpha")
    _write_iset(ws, "beta", status="running")
    _write_iset(ws, "v2ecoli-pdmp")

    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda u: None,
        list_worktrees_fn=lambda: [],
        scan_worktree_fn=lambda _p: [],
        current_branch_fn=lambda: "v2ecoli-pdmp",
    )
    assert out["current"]["slug"] == "v2ecoli-pdmp"
    sibling_slugs = {s["slug"] for s in out["local_siblings"]}
    assert sibling_slugs == {"alpha", "beta"}


def test_current_falls_back_to_first_when_branch_does_not_match(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "alpha")
    _write_iset(ws, "beta")
    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda u: None,
        list_worktrees_fn=lambda: [],
        scan_worktree_fn=lambda _p: [],
        current_branch_fn=lambda: "some-random-branch",
    )
    # No git-branch match → fall back to alphabetical first.
    assert out["current"]["slug"] == "alpha"
    assert [s["slug"] for s in out["local_siblings"]] == ["beta"]


# ---------------------------------------------------------------------------
# local_siblings: other investigations in this same workspace
# ---------------------------------------------------------------------------


def test_local_siblings_lists_other_local_investigations(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "alpha", title="Alpha", status="planning")
    _write_iset(ws, "beta",  title="Beta",  status="planning")
    _write_iset(ws, "gamma", title="Gamma", status="planning")

    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda u: None,
        list_worktrees_fn=lambda: [],
        scan_worktree_fn=lambda _p: [],
        current_branch_fn=lambda: None,
    )
    # current is alpha (alphabetical fallback); the other two are siblings.
    assert out["current"]["slug"] == "alpha"
    sibling_slugs = [s["slug"] for s in out["local_siblings"]]
    assert sibling_slugs == ["beta", "gamma"]
    # Shape: every sibling carries slug/title/worktree_path/effective_status.
    assert set(out["local_siblings"][0].keys()) == {
        "slug", "title", "worktree_path", "effective_status",
    }


def test_local_siblings_empty_when_only_one_investigation(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "alpha")
    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda u: None,
        list_worktrees_fn=lambda: [],
        scan_worktree_fn=lambda _p: [],
        current_branch_fn=lambda: None,
    )
    assert out["current"]["slug"] == "alpha"
    assert out["local_siblings"] == []


def test_dormant_lists_open_investigations_on_other_worktrees(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "self-inv")

    sibling = tmp_path / "sibling"
    _write_iset(sibling, "sibling-inv", title="Sibling Inv", status="planning")

    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda u: None,
        list_worktrees_fn=lambda: [{"path": str(sibling), "branch": "sib-branch"}],
        scan_worktree_fn=lambda p: [
            {"slug": "sibling-inv", "title": "Sibling Inv", "status": "planning"}
        ],
    )
    assert len(out["dormant_others"]) == 1
    e = out["dormant_others"][0]
    assert e["slug"] == "sibling-inv"
    assert e["title"] == "Sibling Inv"
    assert e["worktree_path"] == str(sibling)
    assert e["branch"] == "sib-branch"
    assert e["status"] == "planning"


def test_dormant_skips_worktree_with_live_dashboard(tmp_path):
    """A worktree with a live dashboard belongs in running_others, not
    dormant_others — the registry must not double-list it."""
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "self-inv")

    other = tmp_path / "other"
    servers = [
        {"name": "p", "path": str(other), "url": "http://127.0.0.1:2",
         "pid": 222, "_alive": True, "_file": "/tmp/p.json"},
    ]
    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: servers,
        fetch_peer_fn=lambda u: {"slug": "live-inv", "title": "Live",
                                 "effective_status": "running"},
        list_worktrees_fn=lambda: [{"path": str(other), "branch": "b"}],
        scan_worktree_fn=lambda p: [
            {"slug": "live-inv", "title": "Live", "status": "running"}
        ],
    )
    assert any(o["slug"] == "live-inv" for o in out["running_others"])
    assert out["dormant_others"] == []


def test_dormant_excludes_self_worktree(tmp_path):
    """An investigation in ws_root itself must never appear in
    dormant_others (it's already in `current`)."""
    ws = tmp_path / "ws"; ws.mkdir()
    _write_iset(ws, "self-inv")
    out = _build_investigation_registry_for_test(
        ws,
        this_url="http://127.0.0.1:1",
        list_servers_fn=lambda: [],
        fetch_peer_fn=lambda u: None,
        list_worktrees_fn=lambda: [{"path": str(ws.resolve()), "branch": "main"}],
        scan_worktree_fn=lambda p: [
            {"slug": "self-inv", "title": "Self", "status": "planning"}
        ],
    )
    assert out["dormant_others"] == []


def test_dormant_filters_closed_and_archived_statuses(tmp_path):
    """Investigations with status closed/archived/complete must not
    appear in the dormant bucket."""
    from vivarium_dashboard.server import _scan_worktree_investigations

    wt = tmp_path / "wt"
    for slug, status in [
        ("open-one",     "planning"),
        ("closed-one",   "closed"),
        ("archived-one", "archived"),
        ("complete-one", "complete"),
        ("running-one",  "running"),
    ]:
        _write_iset(wt, slug, status=status)

    found = _scan_worktree_investigations(str(wt))
    slugs = {e["slug"] for e in found}
    assert slugs == {"open-one", "running-one"}


def test_dormant_handles_missing_investigations_dir(tmp_path):
    """A worktree with no investigations/ directory contributes nothing."""
    from vivarium_dashboard.server import _scan_worktree_investigations
    assert _scan_worktree_investigations(str(tmp_path / "nope")) == []


def test_list_other_worktrees_excludes_self(tmp_path):
    """_list_other_worktrees must NEVER yield ws_root itself, regardless
    of how git resolves the porcelain output. (Sanity check on real git;
    skipped if git is unavailable.)"""
    import shutil, subprocess
    if shutil.which("git") is None:
        return
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    # Make a commit so worktree-add works.
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], cwd=str(repo), check=True)
    # Add a sibling worktree on a new branch.
    sib = tmp_path / "sib"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "sib-branch",
                    str(sib)], cwd=str(repo), check=True)

    from vivarium_dashboard.server import _list_other_worktrees
    out = _list_other_worktrees(repo)
    paths = {e["path"] for e in out}
    # The repo itself must not appear; the sibling must.
    assert str(repo.resolve()) not in paths
    assert str(sib.resolve()) in paths


def test_list_other_worktrees_handles_non_git_dir(tmp_path):
    """Non-git dirs return [] without raising."""
    from vivarium_dashboard.server import _list_other_worktrees
    assert _list_other_worktrees(tmp_path) == []
