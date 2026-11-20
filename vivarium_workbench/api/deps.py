"""Request dependencies shared by every route module.

These are the per-request seams a route declares with ``Depends(...)``. They live here, not in
``api/app.py``, so that a router module (for example a backend plugin's ``Provider.router()``, see
docs/backend-adapters.md §2.2) can declare routes exactly the way ``app.py`` does without importing
``app.py``, which will import that router. ``app.py`` re-exports these names, and both modules hand out
the SAME function objects, so ``app.dependency_overrides[get_workspace] = ...`` (tests/conftest.py
``inproc_client``) applies to routes declared in either place.

This module must never import ``vivarium_workbench.api.app`` (an import-linter contract in
pyproject.toml enforces it).

What a router does NOT need from here, because ``create_app`` applies it app-wide, to included routers
too: the CSRF/origin guard, the Host guard and the session middleware (middleware), the plain-identifier
guard (an app-level dependency), and the ``{"error": ...}`` envelope (exception handlers;
``lib.errors.APIError`` raises it).

``create_app`` includes such a router (``api.app._include_provider_routers``, step P2 Runs a2) BEFORE the SPA
catch-all route (``/{rel:path}``), which otherwise answers its GETs first, and before ``_apply_readonly_filter``. A
read-only deployment keeps only the router's POST routes the provider declares (``ProviderRouter.readonly_allowed``,
merged into ``app.state.readonly_allowed_mutations``); the others are dropped there. A route that clashes with one
the app already serves, or a declared path that is not the router's own, makes ``create_app`` raise.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request

from vivarium_workbench.lib import session_registry
from vivarium_workbench.lib.workspace_context import WorkspaceContext


def get_workspace_context(request: Request = None) -> "WorkspaceContext":
    """Resolve the per-request ``WorkspaceContext`` — the single seam a request
    operates through (session → its workspace + ports).

    ``request`` is FastAPI-injected when used as a dependency; a direct call
    (unit tests) passes ``None`` → an unbound session → the process default
    workspace. So cookie-less clients (curl, the CLI, the urllib test harness)
    and unbound browser sessions are byte-identical to before this seam existed.
    See ``lib/workspace_context.py`` and ``docs/session-registry.md``.
    """
    from vivarium_workbench.lib import workspace_context
    key = session_key_of(request)
    return workspace_context.resolve(key)


def session_key_of(request: "Request | None") -> "str | None":
    """The request's session key — the middleware-stashed value (available even
    on a first request), falling back to the raw ``X-VW-Session`` header then the
    cookie, or ``None`` for a direct/identity-less call."""
    if request is None:
        return None
    key = getattr(request.state, "session_key", None)
    return (key
            or request.headers.get(session_registry.SESSION_HEADER)
            or request.cookies.get(session_registry.SESSION_COOKIE))


def get_workspace(request: Request = None) -> Path:
    """Resolve the workspace root for a request (overridable in tests via
    dependency_overrides). Thin shim over :func:`get_workspace_context` — the
    routing now flows through the per-session context, but a bound session
    resolves to its bound path and an unbound one to the process default (the
    prior ``active_workspace`` root, else the ``VIVARIUM_WORKBENCH_WORKSPACE``
    env var / cwd), so existing behavior is preserved.
    """
    return get_workspace_context(request).ws_root
