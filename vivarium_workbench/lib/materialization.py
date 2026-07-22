"""Managed environment materialization — provision a per-coordinate venv via
``uv sync`` (``docs/materialization-lifecycle.md`` §2/§5/§9).

**This slice — §9(b): the synchronous primitive.** A coordinate-keyed venv store
plus a single synchronous ``uv sync`` (long timeout). This is the minutes-scale
"phase 2" step: given a workspace *source* (a checkout with a ``pyproject.toml`` /
``uv.lock``), build a venv **outside** the checkout — keyed by an environment
coordinate so the same source reuses one venv — and return its interpreter.
``uv sync`` also provisions the interpreter the project *requires* (§2b: uv fetches
the managed CPython from ``requires-python`` / ``.python-version``), so the venv's
Python is the workspace's, not the workbench's.

Deliberately **not** in this slice (later, per §9 c/d): making it asynchronous with
a ``MATERIALIZING`` session state + progress polling (§3/§4), cross-session dedup
of an in-flight sync (§5), restart reconcile + GC (§7), and the ``RepoSource`` clone
seam / S3 cache (§2/§5a). The **in-place local** path (§2a) is unchanged and does
**not** route here — a dev checkout keeps using its own ``.venv`` (see
``env_resolver``); this module is the *managed* provisioning primitive.

Coordinate keying (§5/§10 open question): keyed by ``(resolved source path, uv.lock
content)`` for now — correct (no false sharing between two checkouts whose lock
pins editable path deps to different locations), at the cost of not yet
deduplicating a venv across two sources with an identical lock. Pure-lock-hash
dedup arrives with the canonical-staging managed path (§5).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from vivarium_workbench.lib.env_compat import get_env

# Long timeout — a ``uv sync`` on a v2ecoli-scale repo is minutes, a different cost
# class from the env-worker 60 s query timeout (lifecycle §1). Config-overridable.
_DEFAULT_TIMEOUT_S = 900.0

# venv interpreter relative paths — POSIX first, then Windows (mirrors env_resolver).
_VENV_INTERPRETERS = ("bin/python", "Scripts/python.exe")


class MaterializationError(Exception):
    """A managed environment build failed (lifecycle §6). ``tail`` carries the
    ``uv`` output tail — the actionable part a user needs to fix a lockfile."""

    def __init__(self, message: str, *, tail: str = ""):
        super().__init__(message)
        self.tail = tail


def store_root() -> Path:
    """The venv store directory (§5) — coordinate-keyed venvs live under here.
    Override with ``VIVARIUM_WORKBENCH_VENV_STORE``; defaults under the user cache."""
    override = get_env("VENV_STORE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "vivarium-workbench" / "venvs"


def environment_coordinate(source: Path) -> str:
    """Stable venv cache key for a workspace *source*.

    A hash of the resolved source path + its ``uv.lock`` bytes (else
    ``pyproject.toml`` bytes, else just the path). The lock is the truer key — the
    venv is a pure function of it (§5) — but is scoped to the source path here so
    two checkouts with editable path-dep locks never share a venv (§10)."""
    src = Path(source).resolve()
    h = hashlib.sha256(str(src).encode("utf-8"))
    for name in ("uv.lock", "pyproject.toml"):
        f = src / name
        if f.is_file():
            h.update(b"\0")
            h.update(f.read_bytes())
            break
    return h.hexdigest()[:16]


def _venv_python(venv_dir: Path) -> "Path | None":
    for rel in _VENV_INTERPRETERS:
        cand = venv_dir / rel
        if cand.is_file():
            return cand
    return None


def cached_interpreter(coordinate: str) -> "str | None":
    """The interpreter for an already-materialized coordinate, or ``None`` — the
    fast path (§5): a present venv skips ``uv sync`` entirely."""
    py = _venv_python(store_root() / coordinate)
    return str(py) if py is not None else None


def cached_interpreter_for(source: Path) -> "str | None":
    """Cached managed interpreter for a workspace *source* (its coordinate), or
    ``None``. A workspace with no ``pyproject.toml`` is not a managed project."""
    src = Path(source)
    if not (src / "pyproject.toml").is_file():
        return None
    return cached_interpreter(environment_coordinate(src))


def materialize(source: Path, *, timeout: float = _DEFAULT_TIMEOUT_S) -> str:
    """Provision (or reuse) the venv for ``source``'s coordinate; return its
    interpreter path.

    - **No ``pyproject.toml``** → behavior-preserving: return ``sys.executable``
      (nothing to sync; matches today's shared-env default).
    - **Cached** (a venv for the coordinate exists) → return its interpreter, no
      ``uv sync`` (§5 fast path).
    - **Otherwise** → ``uv sync`` the source into a store venv under a long
      timeout, provisioning the required interpreter too (§2b), and return it.

    Raises :class:`MaterializationError` (with the ``uv`` tail) on a resolution /
    build failure or timeout (§6)."""
    src = Path(source).resolve()
    if not (src / "pyproject.toml").is_file():
        return sys.executable

    # A managed source ships a uv.lock, so the coordinate is lock-stable across
    # calls (idempotent cache hits). A lockless source is the degenerate case:
    # `uv sync` writes a uv.lock, shifting the coordinate — so its first
    # materialize won't cache-hit on a later call. Managed clones always carry a
    # lock, so this is not the real path.
    coordinate = environment_coordinate(src)
    cached = cached_interpreter(coordinate)
    if cached is not None:
        return cached

    venv_dir = store_root() / coordinate
    venv_dir.parent.mkdir(parents=True, exist_ok=True)

    # Direct uv at the store venv (outside the checkout, so an in-place dev
    # checkout is never mutated). `--frozen` installs from the existing lock
    # (reproducible) when one is present; without a lock, let uv resolve.
    import os
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv_dir))
    cmd = ["uv", "sync"]
    if (src / "uv.lock").is_file():
        cmd.append("--frozen")
    try:
        proc = subprocess.run(
            cmd, cwd=str(src), env=env,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise MaterializationError(
            f"environment build timed out after {int(timeout)}s", tail="") from e
    except FileNotFoundError as e:  # uv not installed
        raise MaterializationError("uv not found on PATH", tail="") from e

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise MaterializationError("environment build failed", tail=tail)

    py = _venv_python(venv_dir)
    if py is None:
        raise MaterializationError(
            "uv sync completed but no interpreter was found in the venv", tail="")
    return str(py)
