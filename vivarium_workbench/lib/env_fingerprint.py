"""Reconstructable environment fingerprint for a run's manifest.

``compute_env()`` captures just enough about the surrounding software
environment — the workspace's git HEAD, key simulation packages' versions
(+ a best-effort git SHA when installed from a local checkout), a hash of
the workspace's ``uv.lock``, the Python/platform strings, and the caller's
already-computed cache fingerprint — that a rerun can tell whether it
executed under materially the same environment as the original run.

Every field is independently best-effort: a lookup failure degrades that
one field to ``None`` rather than raising, so environment capture can never
block a run from being recorded (reproducible-rerun-spine Task 2, item 2 /
G1). ``env_id()`` folds an env dict into a short, stable, order-independent
digest so two runs' environments can be compared with one string equality
check instead of a deep dict diff.
"""
from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path

# Packages whose version + provenance materially affect a sim run's
# reproducibility. Best-effort: any package not installed (or not
# importable under its import name) simply comes back {version: None,
# git_sha: None} rather than raising.
_SIM_PACKAGES = ("v2ecoli", "process-bigraph", "bigraph-schema", "viva_superpowers")


def _workspace_commit(ws_root) -> str | None:
    """Best-effort git HEAD of ``ws_root`` (mirrors the code_version lookup
    in ``composite_runs.build_run_manifest``)."""
    if ws_root is None:
        return None
    try:
        import subprocess
        out = subprocess.run(
            ["git", "-C", str(ws_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        return None


def _package_git_sha(module_file: str | None) -> str | None:
    """Best-effort git SHA of the repo containing ``module_file``. A package
    installed editable / from a local checkout carries provenance this way;
    a wheel install has no ``.git`` and degrades to ``None``."""
    if not module_file:
        return None
    try:
        import subprocess
        repo_dir = str(Path(module_file).resolve().parent)
        out = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        return None


def _sim_package_info(name: str) -> dict:
    version = None
    try:
        from importlib.metadata import version as _pkg_version
        version = _pkg_version(name)
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        version = None
    git_sha = None
    try:
        import importlib
        mod = importlib.import_module(name.replace("-", "_"))
        git_sha = _package_git_sha(getattr(mod, "__file__", None))
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        git_sha = None
    return {"version": version, "git_sha": git_sha}


def _lockfile_hash(ws_root) -> str | None:
    if ws_root is None:
        return None
    try:
        lock = Path(ws_root) / "uv.lock"
        if not lock.is_file():
            return None
        return hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        return None


def compute_env(*, ws_root=None, cache_fingerprint=None, packages=None) -> dict:
    """Best-effort snapshot of the environment a run executed under.

    Every field independently degrades to ``None`` on failure — this must
    never raise or block a run from being recorded. ``cache_fingerprint`` is
    a caller-supplied, already-computed value (e.g. v2ecoli's
    ``run_condition_multigen_parquet.cache_fingerprint()`` short hash);
    passed straight through rather than recomputed here, since only the
    caller knows which cache directory produced the run.

    ``packages`` is the set of distributions to capture versions/SHAs for.
    When ``None`` it is the built-in :data:`_SIM_PACKAGES` (byte-for-byte
    back-compat). A workspace that declares a named environment
    (``environments.yaml``) passes that environment's members here — a
    v2ecoli-shaped default list would otherwise miss, say, viva-pulse's
    ``viva_pulse``/``protobuf``, making the fingerprint blind to the very
    packages whose pins make that workspace reproducible.
    """
    names = list(packages) if packages is not None else list(_SIM_PACKAGES)
    sim_packages = {}
    for name in names:
        try:
            sim_packages[name] = _sim_package_info(name)
        except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
            sim_packages[name] = {"version": None, "git_sha": None}

    try:
        py = platform.python_version()
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        py = None
    try:
        plat = platform.platform()
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        plat = None

    return {
        "workspace_commit": _workspace_commit(ws_root),
        "sim_packages": sim_packages,
        "lockfile_hash": _lockfile_hash(ws_root),
        "python": py,
        "platform": plat,
        "cache_fingerprint": cache_fingerprint,
    }


def env_id(env: dict) -> str:
    """Stable 16-hex sha256 digest of ``env``.

    Order-independent: ``json.dumps(..., sort_keys=True)`` sorts keys at
    every nesting level, so passing the same env dict with keys (at any
    level) reordered yields the same id.
    """
    payload = json.dumps(env, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
