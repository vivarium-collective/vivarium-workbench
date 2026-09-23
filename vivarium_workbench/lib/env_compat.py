"""Dual-read env-var helper for the vivarium-dashboard -> vivarium-workbench rename.

Read the new ``VIVARIUM_WORKBENCH_<NAME>`` variable first, falling back to the
deprecated ``VIVARIUM_DASHBOARD_<NAME>`` (emitting a one-time
``DeprecationWarning`` per old variable). This keeps every existing external
consumer that still exports the old env names working unchanged during the
Phase 1 deprecation window.

Remove the old-prefix fallback in Phase 3.
"""
from __future__ import annotations

import os
import tempfile
import warnings
from pathlib import Path
from typing import Mapping

NEW_PREFIX = "VIVARIUM_WORKBENCH_"
OLD_PREFIX = "VIVARIUM_DASHBOARD_"

# The five env vars read in-repo (suffixes, without prefix).
WORKSPACE_ENV = NEW_PREFIX + "WORKSPACE"
READONLY_ENV = NEW_PREFIX + "READONLY"
DISABLE_CSRF_ENV = NEW_PREFIX + "DISABLE_CSRF"
TRUST_PROXY_ENV = NEW_PREFIX + "TRUST_PROXY"
ALLOWED_ORIGINS_ENV = NEW_PREFIX + "ALLOWED_ORIGINS"
GH_CLIENT_ID_ENV = NEW_PREFIX + "GH_CLIENT_ID"
BUILD_CACHE_ENV = NEW_PREFIX + "BUILD_CACHE"

_warned: set[str] = set()


def _warn_once(old_key: str, new_key: str) -> None:
    if old_key in _warned:
        return
    _warned.add(old_key)
    warnings.warn(
        f"{old_key} is deprecated; use {new_key} instead.",
        DeprecationWarning,
        stacklevel=3,
    )


def get_env(name: str, default: str | None = None,
            *, env: Mapping[str, str] | None = None) -> str | None:
    """Read ``VIVARIUM_WORKBENCH_<name>``, else deprecated ``VIVARIUM_DASHBOARD_<name>``.

    ``name`` is the suffix (e.g. ``"WORKSPACE"``). ``env`` defaults to
    ``os.environ`` but may be any mapping (used by the CSRF predicate, which
    receives request-scoped headers-as-env in tests).
    """
    source: Mapping[str, str] = os.environ if env is None else env
    new_key = NEW_PREFIX + name
    old_key = OLD_PREFIX + name
    if new_key in source:
        return source[new_key]
    if old_key in source:
        _warn_once(old_key, new_key)
        return source[old_key]
    return default


def _nearest_existing_ancestor(path: Path) -> Path:
    """Walk up from ``path`` to the nearest dir that actually exists (``path``
    itself, or the first parent that does) — the dir whose write-permission bit
    governs whether ``path`` (and any of its not-yet-created parents) can be
    created."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path  # unreachable: the filesystem root always exists


def home_or_tmp_default(*parts: str) -> Path:
    """``Path.home().joinpath(*parts)``, falling back to a writable temp dir
    when that HOME-based default isn't writable.

    Mirrors ``env_worker._default_provision_target``: a non-root single-pod
    HeLx deployment runs this process with a HOME it can't write to (or that
    doesn't exist), so a hardcoded HOME-based cache/store default raises
    ``PermissionError`` on first use there. Prefer the HOME default when its
    nearest existing ancestor dir is writable (the common case, and unchanged
    from today); otherwise fall back to
    ``tempfile.gettempdir() / "vivarium-workbench" / *parts``. Callers keep
    their own env-var override checked first, so an explicit pin always wins
    over both of these.
    """
    home_default = Path.home().joinpath(*parts)
    try:
        writable = os.access(_nearest_existing_ancestor(home_default), os.W_OK)
    except OSError:
        writable = False
    if writable:
        return home_default
    return Path(tempfile.gettempdir()) / "vivarium-workbench" / Path(*parts)
