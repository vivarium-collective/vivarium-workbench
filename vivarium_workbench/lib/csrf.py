"""Pure same-origin CSRF predicate shared by both HTTP servers.

The dashboard's state-mutating (POST/DELETE) surface is guarded by a STATELESS
same-origin check (no token): an ``Origin`` header, when present, must match the
request's ``Host``.  This module holds the *decision* as a pure function so the
stdlib ``server.Handler._csrf_ok`` and the FastAPI middleware share one verdict
and cannot drift.

The pure predicate does NOT read the environment or emit a response — callers
do the header reads, the env read (via :func:`is_disabled_via_env`), and the
403 emit themselves.  No ``import server`` here.
"""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlsplit


def is_request_allowed(
    origin: str | None, host: str | None, *, disabled: bool
) -> bool:
    """Return True if a state-mutating request may proceed (same-origin).

    Byte-identical to the legacy ``server.Handler._csrf_ok`` decision:

      * ``disabled`` (CSRF bypass env set) → allow.
      * ``Origin`` absent/empty            → allow.
      * ``Origin`` netloc non-empty AND == ``Host`` → allow.
      * else → deny.
    """
    if disabled:
        return True
    if not origin:
        return True
    netloc = urlsplit(origin).netloc
    return bool(netloc) and netloc == (host or "")


def is_disabled_via_env(env: Mapping[str, str]) -> bool:
    """True if the CSRF bypass escape hatch is set (``VIVARIUM_WORKBENCH_DISABLE_CSRF=1``).

    Dual-reads the deprecated ``VIVARIUM_DASHBOARD_DISABLE_CSRF`` for back-compat.
    """
    from vivarium_workbench.lib.env_compat import get_env
    return get_env("DISABLE_CSRF", env=env) == "1"
