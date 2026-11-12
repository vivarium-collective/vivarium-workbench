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

from vivarium_workbench.lib import github_auth
from vivarium_workbench.lib.env_compat import get_env, home_or_tmp_default

# Long timeout — a ``uv sync`` on a v2ecoli-scale repo is minutes, a different cost
# class from the env-worker 60 s query timeout (lifecycle §1). Config-overridable.
_DEFAULT_TIMEOUT_S = 900.0

# venv interpreter relative paths — POSIX first, then Windows (mirrors env_resolver).
_VENV_INTERPRETERS = ("bin/python", "Scripts/python.exe")

# Completion marker: written only after a `uv sync` fully succeeds. A cache hit
# requires it, so a partial venv left by an interrupted/killed sync (a `bin/python`
# but no marker) is re-synced, never served as a broken interpreter (§7 robustness).
_MARKER = ".vwb-materialized"


class MaterializationError(Exception):
    """A managed environment build failed (lifecycle §6). ``tail`` carries the
    ``uv`` output tail — the actionable part a user needs to fix a lockfile."""

    def __init__(self, message: str, *, tail: str = ""):
        super().__init__(message)
        self.tail = tail


# What git prints, with prompts off (as uv runs it), when GitHub asks for a credential it does not
# have: either none reached it, or GitHub answered 401 to the one it sent. Real GitHub, probed
# 2026-11-12: the same line with no header and with a bogus-token header, so the message is chosen
# from whether `materialize` handed git a credential, not from git's text. Matched on the github.com
# host only: the credential is scoped to it, so another host's line is not ours to explain. The toast
# headlines the message and scrolls the tail, where this line sits under the download list.
_GITHUB_NEEDS_CREDENTIAL = "could not read Username for 'https://github.com'"
_NO_CREDENTIAL = (
    "environment build failed: the workbench has no GitHub sign-in or token to give git for a private "
    "dependency. Sign in with GitHub, or have the operator set VIVARIUM_WORKBENCH_GH_TOKEN (if it is "
    "set, GitHub rejected it or could not be reached to check it)")
_CREDENTIAL_REFUSED = (
    "environment build failed: GitHub refused the workbench's credential for a private git dependency "
    "(the token is expired, revoked or invalid): sign in again, or replace VIVARIUM_WORKBENCH_GH_TOKEN")


def _build_failed_message(output: str, *, credential_given: bool) -> str:
    if _GITHUB_NEEDS_CREDENTIAL not in output:
        return "environment build failed"
    return _CREDENTIAL_REFUSED if credential_given else _NO_CREDENTIAL


def store_root() -> Path:
    """The venv store directory (§5) — coordinate-keyed venvs live under here.
    Override with ``VIVARIUM_WORKBENCH_VENV_STORE``; defaults under the user
    cache, falling back to a writable temp dir when HOME isn't writable (e.g. a
    non-root single-pod HeLx deployment — see ``home_or_tmp_default``)."""
    override = get_env("VENV_STORE")
    if override:
        return Path(override)
    return home_or_tmp_default(".cache", "vivarium-workbench", "venvs")


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
    """The interpreter for an already-**completed** coordinate, or ``None`` — the
    fast path (§5): a present, marker-complete venv skips ``uv sync``. A partial
    venv (interrupted sync — a ``bin/python`` but no completion marker) returns
    ``None`` so it is re-synced rather than served broken."""
    venv_dir = store_root() / coordinate
    if not (venv_dir / _MARKER).is_file():
        return None
    py = _venv_python(venv_dir)
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
    # UV_CACHE_DIR: without it, uv falls back to its own default (~/.cache/uv)
    # regardless of where UV_PROJECT_ENVIRONMENT points — a second HOME write
    # that store_root()'s own writable-temp-dir fallback doesn't cover on its
    # own. Keep uv's download cache under the same store root so it inherits
    # the same fallback.
    # git_https_env: a workspace can pin a PRIVATE git dependency (sms-ecoli pins
    # the private v2ecoli); uv runs git with prompts disabled and the container has
    # no credential helper, so the signed-in GitHub session must reach it here.
    git_env = github_auth.git_https_env()
    env = dict(
        os.environ,
        **git_env,
        UV_PROJECT_ENVIRONMENT=str(venv_dir),
        UV_CACHE_DIR=str(store_root() / ".uv-cache"),
    )
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
        output = proc.stderr or proc.stdout or ""
        # The tail goes to the browser: scrub the credential handed to git above.
        tail = github_auth.mask_token(output[-2000:])
        raise MaterializationError(
            _build_failed_message(output, credential_given=bool(git_env)), tail=tail)

    py = _venv_python(venv_dir)
    if py is None:
        raise MaterializationError(
            "uv sync completed but no interpreter was found in the venv", tail="")
    # Mark complete only now — a later `cached_interpreter` trusts this venv.
    (venv_dir / _MARKER).write_text("ok\n", encoding="utf-8")
    return str(py)
