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
import warnings
from typing import Mapping

NEW_PREFIX = "VIVARIUM_WORKBENCH_"
OLD_PREFIX = "VIVARIUM_DASHBOARD_"

# The five env vars read in-repo (suffixes, without prefix).
WORKSPACE_ENV = NEW_PREFIX + "WORKSPACE"
READONLY_ENV = NEW_PREFIX + "READONLY"
DISABLE_CSRF_ENV = NEW_PREFIX + "DISABLE_CSRF"
TRUST_PROXY_ENV = NEW_PREFIX + "TRUST_PROXY"
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
