"""Single source of truth for the active workspace + cache-invalidation registry.

This is a thin facade over :mod:`vivarium_dashboard.lib._root` (the live
``_WS_ROOT`` global). It does NOT fork the root state — there remains exactly
one ``_WS_ROOT``; the getters/setters here simply delegate to ``_root``.

It also owns a callback registry so each workspace-keyed cache module can
register its ``clear_cache`` at import time. On a workspace switch the stdlib
server (and, eventually, the FastAPI app) calls :func:`invalidate` to fire
every registered callback in one place, instead of hard-coding the list of
lib caches inside ``server._invalidate_workspace_caches``.

Import direction is one-way: this module imports only ``_root``; the cache
modules import ``active_workspace`` (never the reverse), so there is no cycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import _root


def get_workspace_root() -> Path | None:
    """Return the explicitly-registered workspace root, or None (delegates)."""
    return _root.get_workspace_root()


def set_workspace_root(path: Path | str) -> None:
    """Register the active workspace root (delegates to ``_root``)."""
    _root.set_workspace_root(path)


# ---------------------------------------------------------------------------
# Cache-invalidation callback registry
# ---------------------------------------------------------------------------
_CLEAR_CBS: list[Callable[[], None]] = []


def register_clear_cb(fn: Callable[[], None]) -> None:
    """Register a cache-clear callback. Idempotent: registering the same
    callable twice (e.g. on module re-import) keeps a single entry."""
    if fn not in _CLEAR_CBS:
        _CLEAR_CBS.append(fn)


def invalidate() -> None:
    """Fire every registered cache-clear callback (workspace switch)."""
    for fn in list(_CLEAR_CBS):
        fn()


def _registered_cbs() -> list[Callable[[], None]]:
    """Test helper: snapshot of the registered callbacks."""
    return list(_CLEAR_CBS)
