"""Locate the vendored bigraph-loom built bundle (``_dist``).

``vivarium_workbench/loom/_dist`` is the Vite build output of the vendored
bigraph-loom viewer. It is a **gitignored build artifact**, not source: a wheel
ships it (the hatch build hook runs ``scripts/build_loom.sh``), but a fresh
``git worktree`` / editable checkout does **not** have it until Node runs.

The ecosystem serves worktree previews with ``PYTHONPATH=<worktree>`` prepended
(so ``import vivarium_workbench`` resolves to the worktree source), which
*shadows* the installed wheel that does have a built ``_dist``. A resolver that
blindly returned its own ``loom/_dist`` therefore pointed at an empty directory,
and every composite graph view broke with "viewer bundle is missing".

:func:`asset_dir` is the single resolution point every consumer imports. It now
returns the first location that actually holds a built bundle, in priority
order:

1. the ``VIVARIUM_WORKBENCH_LOOM_DIST`` env override (explicit pin, e.g. CI);
2. this package's own ``loom/_dist`` when it is built (the normal wheel case,
   and a source tree whose owner ran ``scripts/build_loom.sh``);
3. an installed/other copy of ``vivarium_workbench`` found on ``sys.path`` /
   the site dirs whose ``loom/_dist`` is built (the shadowed-wheel case above);
4. a sibling git worktree of this source tree whose ``loom/_dist`` is built
   (e.g. the canonical checkout, when the worktree's own venv is editable too);
5. failing all of that, this package's own (empty) ``loom/_dist`` path — so the
   diagnostic page and ``doctor`` point at the canonical expected location and
   ``loom_bundle_present()`` reports False rather than raising.

Falling back is announced once on stderr, so a version-skewed preview bundle is
visible rather than silent.
"""
from __future__ import annotations

import os
import site
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

#: Environment escape hatch — an absolute path to a built ``_dist`` dir that wins
#: over every other candidate (when it actually contains ``index.html``).
_ENV_OVERRIDE = "VIVARIUM_WORKBENCH_LOOM_DIST"

_RELATIVE = ("vivarium_workbench", "loom", "_dist")  # <import-root>/vivarium_workbench/loom/_dist

_warned_fallback = False


def _own_dist() -> Path:
    """This package's own (possibly unbuilt) ``loom/_dist`` directory."""
    return Path(__file__).resolve().parent / "loom" / "_dist"


def _has_bundle(dist_dir: Path) -> bool:
    """True when ``dist_dir`` holds a built bundle (its ``index.html`` entry)."""
    try:
        return (dist_dir / "index.html").is_file()
    except OSError:
        return False


def _candidate_roots() -> list[str]:
    """Import roots to scan for *other* installed copies of the package.

    ``sys.path`` plus the interpreter's site-packages dirs — the installed wheel
    lives in one of these even when a ``PYTHONPATH`` worktree shadows it earlier
    on ``sys.path``.
    """
    roots: list[str] = list(sys.path)
    for getter in (site.getsitepackages, lambda: [site.getusersitepackages()]):
        try:
            roots.extend(getter())
        except Exception:  # noqa: BLE001 — some environments disable site
            pass
    return roots


def _installed_copies(own: Path) -> Iterator[Path]:
    """Yield built ``loom/_dist`` dirs from other copies of the package on disk."""
    try:
        own_resolved = own.resolve()
    except OSError:
        own_resolved = own
    seen: set[Path] = set()
    for root in _candidate_roots():
        if not root:
            continue
        cand = Path(root).joinpath(*_RELATIVE)
        try:
            cand_resolved = cand.resolve()
        except OSError:
            continue
        if cand_resolved == own_resolved or cand_resolved in seen:
            continue
        seen.add(cand_resolved)
        if _has_bundle(cand):
            yield cand


@lru_cache(maxsize=1)
def _sibling_worktree_dist() -> Optional[Path]:
    """A built ``loom/_dist`` from a sibling git worktree of this source tree.

    Covers the case where the served worktree's own venv is an editable install
    of that same (unbuilt) worktree, so there is no installed copy to borrow — the
    canonical checkout (or another worktree) usually has a built bundle. Strictly
    best-effort: swallows every error and returns None when git is unavailable.
    Cached because it shells out; a restart picks up a newly-built sibling.
    """
    own = _own_dist()
    try:
        own_resolved = own.resolve()
    except OSError:
        own_resolved = own
    try:
        proc = subprocess.run(
            ["git", "-C", str(own.parent), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        wt = Path(line[len("worktree "):].strip())
        cand = wt.joinpath(*_RELATIVE)
        try:
            if cand.resolve() == own_resolved:
                continue
        except OSError:
            continue
        if _has_bundle(cand):
            return cand
    return None


def _warn_fallback_once(resolved: Path, own: Path) -> None:
    global _warned_fallback
    if _warned_fallback:
        return
    _warned_fallback = True
    print(
        f"warning: the loom bundle (loom/_dist) is not built in this source tree "
        f"({own}); serving the bundle found at {resolved}. For an exact-match "
        f"preview of local loom changes, build it with scripts/build_loom.sh and "
        f"restart.",
        file=sys.stderr,
    )


def _reset_fallback_warning() -> None:
    """Test hook: clear the one-shot fallback-warning latch."""
    global _warned_fallback
    _warned_fallback = False


def asset_dir() -> Path:
    """Absolute path to the built bigraph-loom bundle directory (``_dist``).

    Resolves to the first location that actually holds a built bundle (see the
    module docstring for the priority order), falling back to this package's own
    ``loom/_dist`` path when nothing is built anywhere. Always returns a path
    named ``_dist``; the caller still checks ``index.html`` exists (via
    ``loom_bundle_present()``) before relying on it.
    """
    own = _own_dist()

    env = os.environ.get(_ENV_OVERRIDE)
    if env:
        pinned = Path(env).expanduser()
        if _has_bundle(pinned):
            return pinned

    if _has_bundle(own):
        return own

    for cand in _installed_copies(own):
        _warn_fallback_once(cand, own)
        return cand

    sibling = _sibling_worktree_dist()
    if sibling is not None:
        _warn_fallback_once(sibling, own)
        return sibling

    return own
