"""Reconstructable environment fingerprint for a run's manifest.

``compute_env()`` captures just enough about the surrounding software
environment — the workspace's git HEAD (+ dirty flag + branch), key simulation
packages' versions (+ a best-effort git SHA when installed from a local
checkout), a hash of the workspace's ``uv.lock``, the Python/platform strings,
and the caller's already-computed cache fingerprint — that a rerun can tell
whether it executed under materially the same environment as the original run.

**Canonical shape (keep in sync by hand).** This produces the SAME env shape and
the SAME ``env_id`` algorithm as the engine's
``discovery_engine.study.run_registry.capture_environment`` / ``env_id`` — the
single canonical definition. The workbench keeps its own implementation (rather
than importing it) only because it pins discovery-engine from a PyPI *release*,
which may lag the engine's git main where that API landed; the key set is pinned
in ``tests/test_env_fingerprint.py`` so the two cannot silently drift. When the
pinned engine release carries ``capture_environment``, collapse this to a thin
delegation.

Every field is independently best-effort: a lookup failure degrades that one
field to ``None`` rather than raising, so environment capture can never block a
run from being recorded. ``env_id()`` folds an env dict into a short, stable,
order-independent digest so two runs' environments compare with one string
equality check instead of a deep dict diff.
"""
from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path

# The canonical package set (matches the engine's framework list + the
# workbench's domain package). Each is captured best-effort: a package not
# installed simply comes back absent rather than raising.
_SIM_PACKAGES = (
    "v2ecoli", "process-bigraph", "bigraph-schema", "bigraph-loom",
    "viva-discovery-engine", "vivarium-workbench", "viva-emitters", "viva-superpowers",
)
# dist -> import name where it isn't just dist.replace("-", "_").
_IMPORT_NAME = {"viva-discovery-engine": "discovery_engine"}


def _git_out(ws_root, *args) -> str | None:
    """Best-effort ``git -C ws_root <args>`` stdout (stripped), else None."""
    if ws_root is None:
        return None
    try:
        import subprocess
        r = subprocess.run(["git", "-C", str(ws_root), *args],
                           capture_output=True, text=True, timeout=5, check=False)
        return (r.stdout.strip() or None) if r.returncode == 0 else None
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        return None


def _workspace_dirty(ws_root) -> bool | None:
    """True/False from ``git status --porcelain`` (None if not resolvable). A
    dirty tree means the commit alone does not faithfully reproduce the run."""
    if ws_root is None:
        return None
    try:
        import subprocess
        r = subprocess.run(["git", "-C", str(ws_root), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=5, check=False)
        return bool(r.stdout.strip()) if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _package_git_sha(module_file: str | None) -> str | None:
    """Best-effort git SHA of the repo containing ``module_file`` — populated for
    an editable/local checkout, None for a wheel install."""
    if not module_file:
        return None
    return _git_out(Path(module_file).resolve().parent, "rev-parse", "HEAD")


def _sim_package_info(dist: str) -> dict:
    version = git_sha = None
    try:
        from importlib.metadata import version as _pkg_version
        version = _pkg_version(dist)
    except Exception:  # noqa: BLE001
        version = None
    try:
        import importlib
        mod = importlib.import_module(_IMPORT_NAME.get(dist, dist.replace("-", "_")))
        git_sha = _package_git_sha(getattr(mod, "__file__", None))
    except Exception:  # noqa: BLE001
        git_sha = None
    return {"version": version, "git_sha": git_sha}


def _lockfile_hash(ws_root) -> str | None:
    if ws_root is None:
        return None
    try:
        lock = Path(ws_root) / "uv.lock"
        return hashlib.sha256(lock.read_bytes()).hexdigest()[:16] if lock.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def compute_env(*, ws_root=None, cache_fingerprint=None) -> dict:
    """Best-effort snapshot of the environment a run executed under — the canonical
    shape ``{workspace_commit, dirty, branch, sim_packages:{dist:{version,git_sha}},
    lockfile_hash, python, platform, cache_fingerprint}`` (identical to the engine's
    ``capture_environment``). Every field degrades to ``None`` on failure — never
    raises. ``cache_fingerprint`` is a caller-supplied value, passed straight
    through.
    """
    sim_packages: dict[str, dict] = {}
    for dist in _SIM_PACKAGES:
        try:
            info = _sim_package_info(dist)
        except Exception:  # noqa: BLE001
            info = {"version": None, "git_sha": None}
        if info["version"] is not None or info["git_sha"] is not None:
            sim_packages[dist] = info

    try:
        py = platform.python_version()
    except Exception:  # noqa: BLE001
        py = None
    try:
        plat = platform.platform()
    except Exception:  # noqa: BLE001
        plat = None

    return {
        "workspace_commit": _git_out(ws_root, "rev-parse", "HEAD"),
        "dirty": _workspace_dirty(ws_root),
        "branch": _git_out(ws_root, "rev-parse", "--abbrev-ref", "HEAD"),
        "sim_packages": sim_packages,
        "lockfile_hash": _lockfile_hash(ws_root),
        "python": py,
        "platform": plat,
        "cache_fingerprint": cache_fingerprint,
    }


def env_id(env: dict) -> str:
    """Stable 16-hex sha256 digest of ``env`` (order-independent via sorted keys).
    The same algorithm as the engine's ``env_id`` so a run's id is identical
    whichever writer recorded its environment."""
    payload = json.dumps(env, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
