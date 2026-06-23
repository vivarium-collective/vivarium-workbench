"""Materialize a remote sms-api simulator build into a local workspace cache.

A build is a repo@commit; SP1's GET /api/v1/simulations/workspace streams it as
a gzipped tarball (GitHub's repo tarball). We download it once, extract it,
strip GitHub's single top-level `<org>-<repo>-<sha>/` dir, and cache it by
commit (immutable → reusable). The dashboard then re-points (SP2) to the cache
dir and serves the build as a full local workspace.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from vivarium_dashboard.lib.sms_api_client import SmsApiError


def build_cache_root() -> Path:
    """Root dir for materialized build workspaces (env-overridable for tests)."""
    env = os.environ.get("VIVARIUM_DASHBOARD_BUILD_CACHE")
    return Path(env) if env else Path.home() / ".pbg" / "build-cache"


def cache_dir_for(simulator_id: int, commit: str) -> Path:
    return build_cache_root() / f"sim{simulator_id}-{commit}"


def materialize_build(client: Any, simulator_id: int, commit: str, *, force: bool = False) -> Path:
    """Return a local workspace dir for the build, downloading+extracting once.

    Reuses the per-commit cache dir if present (immutable repo@commit). Extracts
    under the cache root (same filesystem) then os.replace()s into place, so a
    partial download never leaves a half-written cache.
    """
    cache = cache_dir_for(simulator_id, commit)
    if cache.exists() and not force:
        return cache

    root = build_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".staging-sim{simulator_id}-", dir=root))
    try:
        tar_path = client.download_workspace(simulator_id, staging)
        extract_root = staging / "extract"
        extract_root.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(extract_root, filter="data")  # noqa: S202 — trusted internal artifact

        # GitHub wraps everything in one top-level dir; lift it so the cache dir
        # is the workspace root. Fall back to the extract root if the shape is
        # unexpected (not exactly one top-level dir).
        entries = [p for p in extract_root.iterdir() if not p.name.startswith(".")]
        src = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root

        if cache.exists():
            shutil.rmtree(cache)
        os.replace(str(src), str(cache))  # same-filesystem atomic move
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return cache


def list_build_sources(client: Any) -> dict:
    """Map sms-api's simulator versions to dropdown build entries.

    Best-effort: returns {"builds": [], "error": <str>} when sms-api is
    unreachable so the dropdown degrades to Local-only.
    """
    try:
        data = client.list_simulators()
    except SmsApiError as e:
        return {"builds": [], "error": str(e)}
    builds = []
    for v in data.get("versions", []) or []:
        sim_id = v.get("database_id")
        commit = v.get("git_commit_hash", "")
        repo = (v.get("git_repo_url", "") or "").rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        builds.append({
            "simulator_id": sim_id,
            "repo": repo,
            "commit": commit,
            "branch": v.get("git_branch", ""),
            "label": f"{repo} @ {commit} (build #{sim_id})",
        })
    return {"builds": builds, "error": None}
