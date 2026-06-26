"""Pure builders for the 5 GitHub device-flow auth routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_auth_github_start`` / ``_get_auth_github_poll`` /
``_get_auth_github_status`` / ``_post_auth_github_logout`` /
``_get_auth_github_orgs``.

Each handler is already a THIN wrapper around an existing
:mod:`vivarium_dashboard.lib.github_auth` function, and the device-flow session
state lives in that module's process-global singletons (``_PENDING_FLOWS`` /
``_CACHED_SESSION``).  So there is nothing to extract: a FastAPI poll/logout
mutates the SAME ``github_auth`` state the stdlib server uses.  These builders
just reproduce each handler's status-code mapping, returning ``(body, status)``
— the FastAPI route wraps every path (success AND error) in ``JSONResponse`` so
the lib-returned code is preserved verbatim.  No ``import server`` here.

The ``github_auth`` functions are reached via module-attribute access
(``github_auth.start_device_flow()`` etc.) rather than a ``from ... import``
binding, so tests can monkeypatch ``auth_views.github_auth.<fn>`` with canned
returns and never touch real GitHub.

``body`` is accepted+ignored by :func:`auth_start` / :func:`auth_logout`
(matching the handlers, whose ``body`` argument is unused).
"""

from __future__ import annotations

from typing import Optional

from vivarium_dashboard.lib import github_auth


def auth_start(body: Optional[dict] = None) -> tuple[dict, int]:
    """POST /api/auth/github/start — initiate the Device Flow.

    Port of ``_post_auth_github_start``:

      * ``{"error": "no_client_id"}`` → 503 (deployment not configured)
      * other ``{"error": ...}``      → 502 (GitHub-side failure)
      * success                       → 200

    ``body`` is ignored (the handler takes a body but never reads it).
    """
    result = github_auth.start_device_flow()
    if "error" in result:
        code = 503 if result["error"] == "no_client_id" else 502
        return result, code
    return result, 200


def auth_poll(flow_id: str) -> tuple[dict, int]:
    """GET /api/auth/github/poll?flow_id=<uuid> — poll the token endpoint.

    Port of ``_get_auth_github_poll``:

      * missing/blank ``flow_id`` → ``({"status": "error", "detail":
        "missing_flow_id"}, 400)``
      * else ``poll_device_flow(flow_id)`` mapped to an HTTP code the client can
        use without parsing JSON: ``{ok: 200, pending: 202, expired: 410,
        denied: 403}`` with any other/unknown status → 400.
    """
    flow_id = (flow_id or "").strip()
    if not flow_id:
        return {"status": "error", "detail": "missing_flow_id"}, 400
    result = github_auth.poll_device_flow(flow_id)
    code = {
        "ok": 200, "pending": 202, "expired": 410, "denied": 403,
    }.get(result.get("status"), 400)
    return result, code


def auth_status() -> tuple[dict, int]:
    """GET /api/auth/github/status — current session (never the token). Always 200.

    Port of ``_get_auth_github_status``.
    """
    return github_auth.status_payload(), 200


def auth_logout(body: Optional[dict] = None) -> tuple[dict, int]:
    """POST /api/auth/github/logout — clear in-memory session + keyring entry.

    Port of ``_post_auth_github_logout``: always ``({"ok": True}, 200)``.
    ``body`` is ignored (the handler takes a body but never reads it).
    """
    github_auth.logout()
    return {"ok": True}, 200


def auth_orgs() -> tuple[dict, int]:
    """GET /api/auth/github/orgs — user's personal namespace + orgs.

    Port of ``_get_auth_github_orgs``:

      * ``{"error": "unauthenticated"}`` → 401
      * other ``{"error": ...}``         → 502
      * success                          → 200
    """
    result = github_auth.list_orgs()
    if "error" in result:
        code = 401 if result["error"] == "unauthenticated" else 502
        return result, code
    return result, 200
