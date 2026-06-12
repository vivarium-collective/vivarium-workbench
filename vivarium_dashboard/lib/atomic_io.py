"""Single-source atomic text write for the dashboard.

The write-to-a-sibling-``.tmp``-then-``os.replace`` pattern was duplicated
across ``spec_migration``, ``simulations_index``, and ``server.py``. This is the
one place it lives now. Dashboard-local on purpose — no ``pbg_superpowers``
dependency, so the dashboard keeps running standalone (matching the
self-contained ``composite_lookup``/``investigations`` lib convention).
"""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path | str, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    Writes to a sibling ``<path>.tmp`` then ``os.replace``s it into place — the
    replace is atomic on POSIX, so a concurrent reader never observes a
    half-written file. The temp file is cleaned up if the write fails.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
