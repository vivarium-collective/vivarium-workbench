"""Emit a *provenance manifest* for a workspace.

The manifest is the portable unit of the round-trip loop: it pins exactly what
a dashboard is showing (repo + full commit + uv.lock hash + result-store
pointers) so the state can be (a) materialized locally by `vivarium-dashboard
sync` and (b) rebuilt remotely by build-via-sms-api. Pure / ws_root-parameterized.
"""
from __future__ import annotations

import hashlib

from vivarium_workbench.lib.git_io import git_text
from pathlib import Path


def _git(ws_root: Path, *args: str) -> str:
    return git_text(ws_root, *args)


def git_full_commit(ws_root: Path) -> str:
    return _git(Path(ws_root), "rev-parse", "HEAD")


def git_branch(ws_root: Path) -> str:
    return _git(Path(ws_root), "rev-parse", "--abbrev-ref", "HEAD")


def git_origin_url(ws_root: Path) -> str:
    url = _git(Path(ws_root), "remote", "get-url", "origin")
    return url[:-4] if url.endswith(".git") else url


def lockfile_hash(ws_root: Path) -> str | None:
    lf = Path(ws_root) / "uv.lock"
    if not lf.is_file():
        return None
    digest = hashlib.sha256(lf.read_bytes()).hexdigest()[:12]
    return f"uv.lock@{digest}"


def _result_pointers(ws_root: Path) -> dict:
    """Best-effort: distinct result store_paths for this workspace's runs."""
    try:
        from vivarium_workbench.lib.simulations_index import list_simulations
        data = list_simulations(Path(ws_root))
        rows = data.get("simulations", []) if isinstance(data, dict) else (data or [])
        stores = []
        for row in rows:
            sp = row.get("store_path")
            if sp and sp not in stores:
                stores.append(sp)
        return {"runs": stores}
    except Exception:
        return {"runs": []}


def build_manifest(ws_root: Path) -> dict:
    from vivarium_workbench.lib import backends

    ws_root = Path(ws_root)
    build = backends.session_build(ws_root)
    if build is not None and not build.readable:
        build = None

    if build is not None:
        # Build workspace: repo/commit/branch come from the recorded build metadata.
        # NOTE: `lockfile` below is always taken from the on-disk uv.lock (see
        # lockfile_hash call), not from the lockfile at repo@commit.  For a
        # freshly materialized build these are identical.  If the on-disk lock
        # is later re-synced (e.g. `uv lock --upgrade`) it can diverge and
        # cause a false 409 on a subsequent `vivarium-dashboard sync`.
        repo = build.repo_url or ""
        commit = build.commit or ""
        branch = build.branch or ""
    else:
        repo = git_origin_url(ws_root)
        commit = git_full_commit(ws_root)
        branch = git_branch(ws_root)

    try:
        from vivarium_workbench.lib.workspace_deps_views import read_workspace_name
        name = read_workspace_name(ws_root)
    except Exception:
        name = ws_root.name

    return {
        "repo": repo,
        "commit": commit,
        "branch": branch,
        "workspace": name,
        "lockfile": lockfile_hash(ws_root),
        "results": _result_pointers(ws_root),
        "simulator_id": build.id if build is not None else None,
    }
