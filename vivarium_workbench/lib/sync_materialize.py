"""Low-level materialization primitives for `vivarium-dashboard sync`.

Each function returns (body, status). Subprocess-based, no global state.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from vivarium_workbench.lib.provenance_manifest import lockfile_hash


def git_clone_checkout(repo: str, commit: str, dest: Path) -> tuple[dict, int]:
    dest = Path(dest)
    if not repo or not commit:
        return {"error": "repo and commit are required"}, 400
    if dest.exists() and any(dest.iterdir()):
        return {"error": f"{dest} exists and is not empty"}, 409
    try:
        subprocess.run(["git", "clone", "-q", repo, str(dest)],
                       check=True, capture_output=True, text=True, timeout=600)
        subprocess.run(["git", "-C", str(dest), "checkout", "-q", commit],
                       check=True, capture_output=True, text=True, timeout=60)
    except subprocess.CalledProcessError as e:
        return {"error": f"git failed: {e.stderr or e}"}, 502
    except subprocess.TimeoutExpired:
        return {"error": "git clone/checkout timed out"}, 504
    return {"ok": True, "path": str(dest), "commit": commit}, 200


def verify_lockfile(ws_root: Path, expected: str | None) -> tuple[dict, int]:
    """Fidelity gate: the cloned uv.lock must hash to the manifest's value."""
    if not expected:
        return {"ok": True, "note": "no lockfile pinned in manifest"}, 200
    actual = lockfile_hash(Path(ws_root))
    if actual != expected:
        return {"error": f"lockfile mismatch: expected {expected}, got {actual}"}, 409
    return {"ok": True, "lockfile": actual}, 200


def run_uv_sync(ws_root: Path, timeout: int = 600) -> tuple[dict, int]:
    try:
        r = subprocess.run(["uv", "sync"], cwd=str(ws_root),
                           capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"error": "uv not found in PATH"}, 400
    except subprocess.TimeoutExpired:
        return {"error": f"uv sync timed out (>{timeout}s)"}, 504
    if r.returncode != 0:
        return {"error": r.stderr.strip() or "uv sync failed"}, 502
    return {"ok": True}, 200
