"""Orchestrate `vivarium-dashboard sync`: manifest -> registered local workspace.

The inverse of build-via-sms-api. Steps:
  1. clone repo@commit into dest
  2. verify the cloned uv.lock hash equals the manifest's (fidelity gate)
  3. uv sync (materialize the pinned env)
  4. optionally run declared post_sync commands (cache rebuild) — opt-in only
  5. register the checkout in the workspace catalog
Returns (body, status). Pure except for the filesystem/catalog side effects it
is explicitly asked to perform.

Security note: post_sync executes manifest-declared (remote-authored) shell
commands. This is intentionally gated behind run_post_sync=True (default False)
so callers must explicitly opt in to executing untrusted commands.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from vivarium_workbench.lib import sync_materialize


def _catalog_add(path, name=None, package=None) -> dict:
    """Thin seam over the workspace catalog (monkeypatched in tests)."""
    from pbg_superpowers import workspace_catalog
    return workspace_catalog.add(path, name=name)


def sync_from_manifest(manifest: dict, dest: Path, run_post_sync: bool = False) -> tuple[dict, int]:
    dest = Path(dest).resolve()
    repo = manifest.get("repo") or ""
    commit = manifest.get("commit") or ""

    body, status = sync_materialize.git_clone_checkout(repo, commit, dest)
    if status != 200:
        return body, status

    body, status = sync_materialize.verify_lockfile(dest, manifest.get("lockfile"))
    if status != 200:
        return body, status  # do NOT register a workspace that failed the fidelity gate

    body, status = sync_materialize.run_uv_sync(dest)
    if status != 200:
        return body, status

    if run_post_sync:
        for cmd in manifest.get("post_sync", []) or []:
            try:
                subprocess.run(cmd, cwd=str(dest), shell=True, check=True,
                               capture_output=True, text=True, timeout=1800)
            except subprocess.CalledProcessError as e:
                return {"error": f"post_sync command failed: {cmd}: {e.stderr or e}"}, 502
            except subprocess.TimeoutExpired:
                return {"error": f"post_sync command timed out: {cmd}"}, 504

    try:
        entry = _catalog_add(dest, name=manifest.get("workspace"))
    except Exception as e:
        return {"error": f"materialized but catalog register failed: {e}",
                "path": str(dest)}, 207

    return {"ok": True, "path": str(dest), "commit": commit, "entry": entry}, 200
