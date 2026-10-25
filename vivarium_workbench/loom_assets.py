"""Locate the vendored bigraph-loom built bundle (``_dist``) — resiliently.

``_dist`` (the Vite build output of the vendored bigraph-loom under
``vivarium_workbench/loom/``) is a gitignored build artifact, built by
``scripts/build_loom.sh`` and baked into wheels by ``hatch_build.py``. That
covers wheel installs, but leaves two dev paths with **no bundle** — a blank
Explorer and the "viewer bundle is missing" card:

* an editable / source clone that was never built (no Node run), and
* a **git worktree**, whose fresh checkout never has the gitignored ``_dist``
  even though the MAIN checkout it was created from usually does.

This module makes the bundle resolvable in every one of those cases, so it is
"always part of this, never missing". :func:`asset_dir` resolves the first
bundle that actually exists from a candidate chain; :func:`ensure_built` (called
at serve startup) additionally *builds* one for a standalone source clone.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

#: Point this at an existing ``_dist`` to override resolution entirely (an
#: environment that supplies the bundle by other means). ``VIVARIUM_WORKBENCH_*``
#: per the project's env-var convention.
ENV_OVERRIDE = "VIVARIUM_WORKBENCH_LOOM_DIST"

_PKG = Path(__file__).resolve().parent
_VENDORED = _PKG / "loom" / "_dist"


def _has_bundle(p: Path | None) -> bool:
    """True when ``p`` looks like a built loom bundle (has ``index.html``)."""
    try:
        return p is not None and (p / "index.html").is_file()
    except OSError:
        return False


def vendored_dir() -> Path:
    """The canonical in-package location ``vivarium_workbench/loom/_dist`` — the
    build target and the path a wheel ships. Build/stale logic keys off this; it
    may not exist yet in a source checkout."""
    return _VENDORED


def _repo_root() -> Path | None:
    """The git working tree that contains this package (the dir holding ``.git``),
    walking up from the package. Works for both a normal checkout (``.git`` dir)
    and a worktree (``.git`` file)."""
    for d in [_PKG, *_PKG.parents]:
        if (d / ".git").exists():
            return d
    return None


def _main_worktree_dist() -> Path | None:
    """For a git **worktree**, the MAIN checkout's ``loom/_dist`` — so a worktree
    inherits the already-built bundle without a local rebuild.

    A worktree's ``.git`` is a FILE: ``gitdir: <main>/.git/worktrees/<name>``.
    The main checkout root is the parent of that common ``.git``; the package
    sits at the same repo-relative path there, so we map this package's path
    under the main root and append ``loom/_dist``.
    """
    root = _repo_root()
    if root is None:
        return None
    dotgit = root / ".git"
    if not dotgit.is_file():
        return None  # a normal checkout, not a worktree — vendored is the one
    try:
        line = dotgit.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = Path(line.split(":", 1)[1].strip())
    # .../<main>/.git/worktrees/<name>  →  main root is three parents up.
    try:
        main_root = gitdir.parents[2]
    except IndexError:
        return None
    try:
        rel = _PKG.relative_to(root)           # e.g. "vivarium_workbench"
    except ValueError:
        return None
    cand = main_root / rel / "loom" / "_dist"
    return cand if _has_bundle(cand) else None


def _candidates() -> list[Path]:
    """Ordered resolution candidates (first existing wins)."""
    out: list[Path] = []
    env = os.environ.get(ENV_OVERRIDE)
    if env:
        out.append(Path(env).expanduser())
    out.append(_VENDORED)
    m = _main_worktree_dist()
    if m is not None:
        out.append(m)
    return out


def asset_dir() -> Path:
    """Resolve the loom bundle directory for SERVING.

    Returns the first candidate that actually contains a built bundle
    (env override → vendored ``_dist`` → the git main-worktree's ``_dist``).
    When none exists, returns the vendored path — the place it *should* be — so
    callers' error messaging ("missing at …") still points at the build target.
    Pure and fast (a few ``is_file`` checks); does not build. Call
    :func:`ensure_built` once at startup to build one if genuinely absent.
    """
    for c in _candidates():
        if _has_bundle(c):
            return c
    return _VENDORED


def _is_stale(dist: Path) -> bool:
    """True when a source tree is present and newer than the built bundle (so a
    rebuild is warranted). False when there's no source to compare against (a
    wheel install) or the bundle is current."""
    src = _VENDORED.parent / "src"
    if not src.is_dir() or not _has_bundle(dist):
        return False
    try:
        built = (dist / "index.html").stat().st_mtime
        newest = built
        for f in src.rglob("*"):
            if f.is_file():
                try:
                    newest = max(newest, f.stat().st_mtime)
                except OSError:
                    pass
        return newest > built + 1
    except OSError:
        return False


def ensure_built(require: bool = False) -> Path:
    """Make a loom bundle available, building one if necessary. Call once at
    serve startup. Resolution order:

    1. An already-resolvable bundle (env / vendored / main-worktree) that isn't
       stale → use it as-is (the common case; worktrees land here, no build).
    2. A standalone source checkout with Node → build it into the vendored dir.
    3. Otherwise → the bundle is unavailable; warn (or raise if ``require``).

    Returns the resolved bundle dir (or the vendored path if still absent). The
    server degrades gracefully when absent (a clear "Explorer unavailable" card),
    so a missing Node toolchain never blocks startup unless ``require`` is set.
    """
    resolved = asset_dir()
    have = _has_bundle(resolved)
    # A fresh, non-stale, already-built bundle is good as-is.
    if have and not (resolved == _VENDORED and _is_stale(resolved)):
        return resolved

    loom_dir = _VENDORED.parent
    script = _PKG.parent / "scripts" / "build_loom.sh"
    can_build = (loom_dir / "package.json").is_file() and shutil.which("npm") is not None

    if can_build and script.is_file():
        print(f"loom: building the viewer bundle (one-time) → {_VENDORED} …", file=sys.stderr)
        try:
            subprocess.run(["bash", str(script)], check=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"loom: build failed ({exc}); the Composite Explorer will be "
                  f"unavailable until scripts/build_loom.sh succeeds.", file=sys.stderr)
        if _has_bundle(_VENDORED):
            return _VENDORED

    # Couldn't build (or build produced nothing). If a fallback bundle exists
    # (e.g. the main worktree's), serve that; otherwise report absence.
    if have:
        return resolved
    msg = (f"loom viewer bundle not found (expected at {_VENDORED}). "
           f"Install Node 20+ and run scripts/build_loom.sh, or set "
           f"{ENV_OVERRIDE} to an existing bundle.")
    if require:
        raise RuntimeError(msg)
    print(f"warning: {msg} The Composite Explorer will show an 'unavailable' "
          f"card; the rest of the workbench works normally.", file=sys.stderr)
    return _VENDORED
