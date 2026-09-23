"""Symlink-safe workspace file walk.

A workspace's ``.venv`` is often a *symlink* to the image/shared venv (tens of
thousands of files, ~99 installed packages). ``Path.glob``/``Path.rglob``
follow symlinks by default, so any recursive glob rooted at the workspace
(``ws_root.rglob("study.yaml")``, ``ws_root.glob("**/composites/*.py")``, ...)
silently traverses the entire venv. On a local disk that's merely wasteful; on
NFS (Azure Files, with per-op round-trips) it costs minutes per call and was
the dominant cost behind slow ``/api/registry`` and ``/api/composites`` calls.

``iter_workspace_files`` walks the tree with ``os.walk(..., followlinks=False)``
and prunes symlinked directories (and a fixed set of never-useful dir names)
*before* descending into them, so the traversal never enters ``.venv`` (symlink
or not), ``.git``, vendored JS deps, bytecode caches, or any hidden directory
-- regardless of how deep it's nested. A broken or looping symlink is never a
problem: ``followlinks=False`` means ``os.walk`` never follows a symlinked
directory in the first place, so nothing can be dereferenced into a cycle.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator, Optional

# Directory names never descended into, symlink or not. Kept in sync with the
# various per-module ``_SKIP``/``_SKIP_DIRS`` constants those callers used to
# filter these out post-hoc from a (slow, symlink-following) glob; here they're
# pruned before descent so they're never even stat'd.
_SKIP_DIR_NAMES = {".venv", ".git", "node_modules", "__pycache__"}


def iter_workspace_files(
    ws_root: "Path | str",
    *,
    suffixes: Optional[Iterable[str]] = None,
    names: Optional[Iterable[str]] = None,
) -> Iterator[Path]:
    """Yield files under ``ws_root`` without following symlinked directories.

    Directories are pruned (not descended into) when they:

    - are named ``.venv``, ``.git``, ``node_modules``, or ``__pycache__``;
    - start with ``.`` (any hidden directory, e.g. ``.pbg``, ``.claude``); or
    - are themselves a symlink (``os.path.islink``) -- catches a symlinked
      ``.venv`` even if renamed, and any other symlinked subtree.

    A file matches (and is yielded) when either ``suffixes`` or ``names`` is
    given and the file satisfies one of them; when *neither* is given, every
    file is yielded. ``suffixes`` entries may be given with or without the
    leading dot (``".py"`` or ``"py"``); ``names`` matches the exact basename
    (e.g. ``"study.yaml"``).

    Order is whatever ``os.walk`` yields (top-down, not sorted) -- callers
    that need a stable order should sort the result themselves.
    """
    root = Path(ws_root)
    suffix_set = (
        {s if s.startswith(".") else f".{s}" for s in suffixes}
        if suffixes is not None
        else None
    )
    name_set = set(names) if names is not None else None
    match_all = suffix_set is None and name_set is None

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept: list[str] = []
        for d in dirnames:
            if d in _SKIP_DIR_NAMES or d.startswith("."):
                continue
            if os.path.islink(os.path.join(dirpath, d)):
                continue
            kept.append(d)
        dirnames[:] = kept

        for fn in filenames:
            if match_all:
                yield Path(dirpath) / fn
                continue
            if suffix_set is not None and any(fn.endswith(suf) for suf in suffix_set):
                yield Path(dirpath) / fn
                continue
            if name_set is not None and fn in name_set:
                yield Path(dirpath) / fn
