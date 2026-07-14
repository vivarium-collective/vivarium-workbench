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

from typing import Iterable, Mapping
from urllib.parse import urlsplit


def is_request_allowed(
    origin: str | None, host: str | None, *, disabled: bool,
    forwarded_host: str | None = None, trust_forwarded: bool = False,
    allowed_origins: Iterable[str] | None = None,
) -> bool:
    """Return True if a state-mutating request may proceed (same-origin).

    Byte-identical to the legacy ``server.Handler._csrf_ok`` decision when
    ``trust_forwarded`` is left at its default and no ``allowed_origins`` are
    configured:

      * ``disabled`` (CSRF bypass env set)       → allow.
      * ``Origin`` absent/empty                  → allow.
      * ``Origin`` exactly in ``allowed_origins`` → allow.
      * ``Origin`` netloc non-empty AND == effective host → allow.
      * else → deny.

    ``allowed_origins`` is a production-grade allowlist (à la Django
    ``CSRF_TRUSTED_ORIGINS``) for deployments where the raw ``Host`` the process
    sees can never equal the browser's ``Origin`` — e.g. an AWS ALB terminating a
    `/workbench` subpath that REWRITES the ``Host`` header AND does not emit
    ``X-Forwarded-Host`` (so ``--trust-proxy`` has nothing to consult). The
    operator declares the exact browser-facing origin(s) explicitly
    (``--allowed-origin http://localhost:8080`` / ``VIVARIUM_WORKBENCH_ALLOWED_ORIGINS``);
    an ``Origin`` that exactly matches short-circuits to allow. It is compared as
    the full origin string (scheme + netloc), so it is deterministic and
    header-independent while preserving the same-origin guard for everything else.
    An empty/None allowlist leaves the legacy behavior untouched.

    ``forwarded_host``/``trust_forwarded`` are an opt-in extension for serving
    behind a reverse proxy (e.g. an ALB terminating a `/workbench` subpath):
    when ``trust_forwarded`` is True and ``forwarded_host`` is non-empty, the
    Origin is compared against ``forwarded_host`` instead of the raw ``host``
    (which a proxy hop may have rewritten to an internal service name). This
    must never be inferred from the header's mere presence — an untrusted
    direct request could otherwise spoof ``X-Forwarded-Host`` to bypass the
    guard — hence the explicit ``trust_forwarded`` flag, set only via the
    operator-controlled ``--trust-proxy`` CLI flag / env var.
    """
    if disabled:
        return True
    if not origin:
        return True
    if allowed_origins and origin in set(allowed_origins):
        return True
    effective_host = (forwarded_host if (trust_forwarded and forwarded_host) else host) or ""
    netloc = urlsplit(origin).netloc
    return bool(netloc) and netloc == effective_host


def allowed_origins_via_env(env: Mapping[str, str]) -> list[str]:
    """Parse the configured allowlist (``VIVARIUM_WORKBENCH_ALLOWED_ORIGINS``).

    A comma-separated list of exact origins (scheme + netloc, no path), e.g.
    ``http://localhost:8080,https://demo.example.gov``. Dual-reads the deprecated
    ``VIVARIUM_DASHBOARD_ALLOWED_ORIGINS`` for back-compat. Whitespace around each
    entry is trimmed and empty entries are dropped; unset/empty → ``[]`` (guard
    unchanged).
    """
    from vivarium_workbench.lib.env_compat import get_env
    raw = get_env("ALLOWED_ORIGINS", env=env) or ""
    return [o.strip() for o in raw.split(",") if o.strip()]


def is_disabled_via_env(env: Mapping[str, str]) -> bool:
    """True if the CSRF bypass escape hatch is set (``VIVARIUM_WORKBENCH_DISABLE_CSRF=1``).

    Dual-reads the deprecated ``VIVARIUM_DASHBOARD_DISABLE_CSRF`` for back-compat.
    """
    from vivarium_workbench.lib.env_compat import get_env
    return get_env("DISABLE_CSRF", env=env) == "1"


def is_trust_proxy_via_env(env: Mapping[str, str]) -> bool:
    """True if the reverse-proxy trust flag is set (``VIVARIUM_WORKBENCH_TRUST_PROXY=1``).

    Dual-reads the deprecated ``VIVARIUM_DASHBOARD_TRUST_PROXY`` for back-compat.
    Enables trusting ``X-Forwarded-Host`` for the same-origin check — only set
    this behind a reverse proxy you control (e.g. via ``--trust-proxy``).
    """
    from vivarium_workbench.lib.env_compat import get_env
    return get_env("TRUST_PROXY", env=env) == "1"
