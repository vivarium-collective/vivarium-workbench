"""Materialize a remote sms-api simulator build into a local workspace cache.

A build is a repo@commit; SP1's GET /api/v1/simulations/workspace streams it as
a gzipped tarball (GitHub's repo tarball). We download it once, extract it,
strip GitHub's single top-level `<org>-<repo>-<sha>/` dir, and cache it by
commit (immutable → reusable). The dashboard then re-points (SP2) to the cache
dir and serves the build as a full local workspace.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from vivarium_workbench.lib.sms_api_client import SmsApiError

# A git commit ref (the only non-server-controlled value that flows into a
# filesystem path) must be plain hex. This closes the one allow-list gap in the
# build-switch path: a malicious/compromised sms-api can't smuggle `../` (path
# traversal) or an empty ref into cache_dir_for. Raising SmsApiError routes
# through the handler's existing 502 path (active workspace left unchanged).
_COMMIT_RE = re.compile(r"\A[0-9a-fA-F]{4,40}\Z")

# A real workspace tarball is ~50MB and takes minutes over the SSM tunnel
# (measured ~224s); the client's 30s default would hard-fail the switch.
_DOWNLOAD_TIMEOUT_S = 600.0


def _stamp_build_meta(cache: Path, simulator_id: int, commit: str) -> None:
    """Mark a materialized cache as a remote build so the Simulations DB merges
    the deployment's runs (lib/remote_simulations.py reads this). No-clobber:
    switch-build writes a richer stamp (repo/branch/repo_url); never overwrite it."""
    meta = cache / ".viv-build.json"
    if meta.exists():
        return
    try:
        meta.write_text(json.dumps({"simulator_id": simulator_id, "commit": commit}), encoding="utf-8")
    except OSError:
        pass  # provenance stamp is best-effort, never block materialize


def build_cache_root() -> Path:
    """Root dir for materialized build workspaces (env-overridable for tests)."""
    env = os.environ.get("VIVARIUM_DASHBOARD_BUILD_CACHE")
    return Path(env) if env else Path.home() / ".pbg" / "build-cache"


def cache_dir_for(simulator_id: int, commit: str) -> Path:
    return build_cache_root() / f"sim{simulator_id}-{commit}"


def _safe_commit(commit: str) -> str:
    if not commit or not _COMMIT_RE.match(commit):
        raise SmsApiError(f"refusing unsafe/empty commit ref from sms-api: {commit!r}")
    return commit


def materialize_build(client: Any, simulator_id: int, commit: str, *, force: bool = False) -> Path:
    """Return a local workspace dir for the build, downloading+extracting once.

    Reuses the per-commit cache dir if present (immutable repo@commit). Extracts
    under the cache root (same filesystem) then os.replace()s into place, so a
    partial download never leaves a half-written cache.
    """
    commit = _safe_commit(commit)
    cache = cache_dir_for(simulator_id, commit)
    if cache.exists() and not force:
        _stamp_build_meta(cache, simulator_id, commit)
        return cache

    root = build_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".staging-sim{simulator_id}-", dir=root))
    try:
        tar_path = client.download_workspace(simulator_id, staging, timeout=_DOWNLOAD_TIMEOUT_S)
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
    _stamp_build_meta(cache, simulator_id, commit)
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
            "repo_url": v.get("git_repo_url", ""),
            "commit": commit,
            "branch": v.get("git_branch", ""),
            "created_at": v.get("created_at", ""),
            "label": f"{repo} @ {commit} (build #{sim_id})",
        })
    return {"builds": builds, "error": None}
