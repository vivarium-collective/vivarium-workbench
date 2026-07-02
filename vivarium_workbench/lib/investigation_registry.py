"""Pure builder for ``GET /api/investigation-registry`` (Pass C cross-worktree view).

This is the clean library seam that both the legacy stdlib handler
(``server.Handler._get_investigation_registry``) and the FastAPI route
(``api/app.py``) call.  It has NO dependency on ``vivarium_workbench.server`` —
the helper functions that used to live in ``server.py`` (peer probing, worktree
scanning, the iset-summary default) are vendored here so the typed seam can
build the registry without importing the stdlib module.

The pure entry point is :func:`build_investigation_registry`.  Every external
effect (server listing, peer HTTP, ``git worktree list``, filesystem scan, the
current git branch) is injectable so the function is unit-testable without
network, subprocess, or git I/O.
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml as _yaml

from vivarium_workbench.lib import investigation_status as _invstatus
from vivarium_workbench.lib.investigations_index import (
    _count_runs_for_study as _ii_count_runs_for_study,
    _http_get_json,
)

# Peer-probe cache: each running dashboard registers itself in
# ~/.pbg/servers/*.json; we HTTP-probe each peer's /api/investigation-summaries and cache the
# result for a few seconds to avoid hammering peers on every sidebar render.
_REGISTRY_TTL_S = 5.0
_registry_cache: dict[str, tuple[float, dict]] = {}

# Investigation statuses that should NOT surface in the cross-worktree sidebar.
# Anything else (planning, running, planned, in_progress, blank…) is treated as
# "open" and listed.  Aligns with /pbg-investigation close, which stamps
# ``status: closed``.
_INVESTIGATION_STATUS_HIDDEN_FROM_SIDEBAR = frozenset({
    "closed", "archived", "complete",
})


def peer_current_investigation(url: str) -> dict | None:
    """Query a peer dashboard's /api/investigation-summaries and pick a current Investigation.

    Heuristic: peer-side ``/api/investigation-summaries`` returns every Investigation in the
    peer's workspace. We pick the one whose ``effective_status`` is "running"
    if present; otherwise the first entry. Returns a slim
    ``{slug, title, effective_status}`` dict, or None if the peer has no
    investigations or didn't respond.  Cached for ``_REGISTRY_TTL_S`` seconds.
    """
    cached = _registry_cache.get(url)
    now = time.time()
    if cached and now - cached[0] < _REGISTRY_TTL_S:
        return cached[1] or None
    data = _http_get_json(url.rstrip("/") + "/api/investigation-summaries")
    out: dict | None
    if not data or not isinstance(data.get("investigations"), list):
        out = None
    else:
        invs = data["investigations"]
        running = next(
            (i for i in invs if i.get("effective_status") == "running"),
            None,
        )
        chosen = running or (invs[0] if invs else None)
        if chosen:
            out = {
                "slug":             chosen.get("name"),
                "title":            chosen.get("title", chosen.get("name")),
                "effective_status": chosen.get("effective_status"),
            }
        else:
            out = None
    _registry_cache[url] = (now, out or {})
    return out


def list_other_worktrees(ws_root: Path) -> list[dict]:
    """Return ``[{path, branch}]`` for every git worktree of ``ws_root``'s
    repo EXCEPT ``ws_root`` itself.

    Uses ``git worktree list --porcelain`` from inside ``ws_root``. Returns
    an empty list if ``ws_root`` is not a git checkout, or git is missing,
    or the command fails. Never raises.
    """
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(ws_root),
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    out: list[dict] = []
    cur: dict = {}
    self_resolved = str(ws_root.resolve())
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            if cur and cur.get("path") and cur["path"] != self_resolved:
                out.append(cur)
            cur = {"path": line[len("worktree "):].strip(), "branch": None}
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            cur["branch"] = ref.split("/")[-1] if ref else None
        elif line.startswith("detached"):
            cur["branch"] = None
    if cur and cur.get("path") and cur["path"] != self_resolved:
        out.append(cur)
    return out


def scan_worktree_investigations(worktree_path: str) -> list[dict]:
    """Walk ``<worktree>/investigations/*/investigation.yaml`` off disk
    and return slim summaries: ``[{slug, title, status}, ...]``.

    Used to surface dormant investigations whose dashboards are not
    running. Returns an empty list on any I/O error or invalid YAML.
    Never raises. Skips entries whose ``status`` matches
    ``_INVESTIGATION_STATUS_HIDDEN_FROM_SIDEBAR``.
    """
    root = Path(worktree_path) / "investigations"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        spec_file = child / "investigation.yaml"
        if not spec_file.is_file():
            continue
        try:
            # Force utf-8 — Path.read_text(encoding="utf-8") defaults to locale
            # encoding, which crashed on ASCII locales when a sibling worktree's
            # investigation.yaml contained UTF-8 chars (e.g. → in titles).
            data = _yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, _yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        slug = data.get("name") or child.name
        status = (data.get("status") or "").strip().lower()
        if status in _INVESTIGATION_STATUS_HIDDEN_FROM_SIDEBAR:
            continue
        out.append({
            "slug":   slug,
            "title":  data.get("title") or slug,
            "status": data.get("status"),
        })
    return out


def default_iset_summary(ws_root: Path) -> list[dict]:
    """Default local-investigation summary, byte-identical to the stdlib path.

    Mirrors ``server._build_iset_summary_for_test``: delegates to
    ``lib.investigation_status.build_iset_summary`` with the runs-presence
    signal sourced from each study's runs.db / spec runs.
    """
    return _invstatus.build_iset_summary(
        ws_root,
        study_has_runs=lambda s, spec: _ii_count_runs_for_study(ws_root, s, spec) > 0,
    )


def _default_current_branch(ws_root: Path):
    try:
        from vivarium_workbench.lib.work_state import _current_git_branch
        return _current_git_branch(ws_root)
    except Exception:
        return None


def build_investigation_registry(
    ws_root: Path,
    this_url: str,
    *,
    list_servers_fn=None,
    fetch_peer_fn=None,
    list_worktrees_fn=None,
    scan_worktree_fn=None,
    current_branch_fn=None,
) -> dict:
    """Pure function backing GET /api/investigation-registry.

    Injectable hooks keep the helper testable without filesystem, HTTP,
    subprocess, or git I/O.

    Returns four buckets:

      - ``current``         — this dashboard's chosen Investigation.
                              Picked by (in priority order):
                                1. investigation whose ``name`` matches the
                                   current git branch (Investigation ≡ branch
                                   convention), then
                                2. any investigation with
                                   ``effective_status == "running"``, then
                                3. the first investigation alphabetically.
      - ``local_siblings``  — every OTHER investigation in THIS workspace.
      - ``running_others``  — peer dashboards' chosen Investigations
                              (one per live peer), via HTTP probe of
                              each peer's ``/api/investigation-summaries``.
      - ``dormant_others``  — open Investigations on OTHER worktrees
                              that do NOT have a running dashboard, read
                              directly off disk, deduplicated by slug.

    Contract: previously-existing keys retain their exact shape.
    """
    if list_servers_fn is None:
        try:
            from pbg_superpowers import workspace_catalog
            list_servers_fn = workspace_catalog.list_servers
        except Exception:
            list_servers_fn = lambda: []
    if fetch_peer_fn is None:
        fetch_peer_fn = peer_current_investigation
    if list_worktrees_fn is None:
        list_worktrees_fn = lambda: list_other_worktrees(ws_root)
    if scan_worktree_fn is None:
        scan_worktree_fn = scan_worktree_investigations
    if current_branch_fn is None:
        current_branch_fn = lambda: _default_current_branch(ws_root)

    # All local investigations in this workspace, picked apart into
    # current + siblings. Selection order: git-branch match > running >
    # alphabetical first.
    invs = default_iset_summary(ws_root)
    chosen_idx: int | None = None
    if invs:
        cur_branch = current_branch_fn() or ""
        # Strip the canonical "investigation/" prefix so an investigation
        # slug ("dnaa-replication") matches its conventional branch
        # ("investigation/dnaa-replication").
        cur_branch_slug = cur_branch.removeprefix("investigation/") if cur_branch else ""
        if cur_branch_slug:
            for i, iv in enumerate(invs):
                if iv.get("name") == cur_branch_slug:
                    chosen_idx = i
                    break
        if chosen_idx is None:
            for i, iv in enumerate(invs):
                if iv.get("effective_status") == "running":
                    chosen_idx = i
                    break
        if chosen_idx is None:
            chosen_idx = 0

    if chosen_idx is not None:
        chosen = invs[chosen_idx]
        current = {
            "slug":             chosen.get("name"),
            "title":            chosen.get("title", chosen.get("name")),
            "worktree_path":    str(ws_root.resolve()),
            "url":              this_url,
            "effective_status": chosen.get("effective_status"),
        }
    else:
        current = {
            "slug":             None,
            "title":            None,
            "worktree_path":    str(ws_root.resolve()),
            "url":              this_url,
            "effective_status": None,
        }

    # local_siblings — everything else in this workspace.
    siblings: list[dict] = []
    if invs:
        for i, iv in enumerate(invs):
            if i == chosen_idx:
                continue
            siblings.append({
                "slug":             iv.get("name"),
                "title":            iv.get("title", iv.get("name")),
                "worktree_path":    str(ws_root.resolve()),
                "effective_status": iv.get("effective_status"),
            })

    # Running-others: every server record that does NOT point at this
    # worktree path AND has a live PID.
    this_path = str(ws_root.resolve())
    others: list[dict] = []
    running_paths: set[str] = set()
    for entry in list_servers_fn():
        if entry.get("path") == this_path:
            continue
        if not entry.get("_alive", False):
            continue
        url = entry.get("url") or ""
        if not url:
            continue
        peer = fetch_peer_fn(url)
        if peer is None:
            continue
        others.append({
            "slug":             peer.get("slug"),
            "title":            peer.get("title"),
            "worktree_path":    entry.get("path"),
            "url":              url,
            "effective_status": peer.get("effective_status"),
            "pid":              entry.get("pid"),
        })
        if entry.get("path"):
            running_paths.add(entry["path"])

    # Dormant-others: open investigations on OTHER worktrees that do NOT
    # have a live dashboard. Deduped by slug across worktrees, with each
    # unique investigation carrying a `variants` list.
    dormant_by_slug: dict[str, dict] = {}
    for wt in list_worktrees_fn():
        wt_path = wt.get("path")
        if not wt_path or wt_path == this_path or wt_path in running_paths:
            continue
        for inv in scan_worktree_fn(wt_path):
            slug = inv.get("slug")
            if not slug:
                continue
            variant = {
                "worktree_path": wt_path,
                "branch":        wt.get("branch"),
                "status":        inv.get("status"),
            }
            bucket = dormant_by_slug.setdefault(slug, {
                "slug":     slug,
                "title":    inv.get("title"),
                "variants": [],
            })
            bucket["variants"].append(variant)
            if not bucket.get("title") and inv.get("title"):
                bucket["title"] = inv.get("title")

    dormant: list[dict] = []
    for slug, bucket in sorted(dormant_by_slug.items()):
        variants = bucket["variants"]

        # Pick the canonical variant: branch == slug first, then first
        # alphabetical by branch name (None branches sort last).
        def _variant_sort_key(v: dict) -> tuple:
            br = v.get("branch") or ""
            return (br != slug, br or "￿")

        variants_sorted = sorted(variants, key=_variant_sort_key)
        primary = variants_sorted[0]
        dormant.append({
            "slug":          slug,
            "title":         bucket["title"] or slug,
            "worktree_path": primary["worktree_path"],
            "branch":        primary["branch"],
            "status":        primary["status"],
            "variants":      variants_sorted,
        })

    return {
        "current":         current,
        "local_siblings":  siblings,
        "running_others":  others,
        "dormant_others":  dormant,
    }


def derive_this_url(ws_root: Path, host: str | None) -> str:
    """Derive this dashboard's own URL for the registry's ``current`` entry.

    The server doesn't know its own URL up front; prefer the ~/.pbg/servers
    record written on boot (via ``workspace_catalog.find_running``), then fall
    back to a best-effort ``http://{Host header}``.  Mirrors the stdlib
    handler's derivation exactly.
    """
    this_url = ""
    try:
        from pbg_superpowers import workspace_catalog
        rec = workspace_catalog.find_running(ws_root)
        if rec:
            this_url = rec.get("url") or ""
    except Exception:
        pass
    if not this_url:
        this_url = f"http://{host or '127.0.0.1'}"
    return this_url
