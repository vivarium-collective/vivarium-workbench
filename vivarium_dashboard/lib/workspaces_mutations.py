"""Pure builders for the 3 workspace-registry POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_workspaces_add`` / ``_post_workspaces_forget`` /
``_post_workspaces_cleanup_stale``.

These 3 POSTs edit the **GLOBAL** ``~/.pbg`` workspace catalog via
``pbg_superpowers.workspace_catalog`` (already a lib; process-global, NOT server
state).  They take a ``path`` from the request body and use NO workspace /
ws_root — so the builders take ONLY ``body`` and operate on the global catalog.
There is nothing to thread through: a FastAPI call mutates the SAME ``~/.pbg``
catalog the stdlib server does.  No ``import server`` here.

Each builder returns ``(body, status)`` — the FastAPI route wraps every path
(success AND error) in ``JSONResponse`` so the lib-returned code is preserved
verbatim.

``workspace_catalog`` is imported as a module-level name and its functions are
reached via attribute access (``workspace_catalog.add(...)`` etc.) so tests can
monkeypatch ``workspaces_mutations.workspace_catalog`` with a fake and never
touch the real ``~/.pbg`` catalog.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pbg_superpowers import workspace_catalog


def workspaces_add(body: Any) -> tuple[dict, int]:
    """POST /api/workspaces/add — register an existing workspace in the catalog.

    Port of ``_post_workspaces_add``:

      * missing / non-string / non-absolute ``path`` →
        ``({"error": "path must be an absolute string"}, 400)``
      * ``workspace_catalog.add(path)`` raising ``ValueError`` →
        ``({"error": str(e)}, 400)``
      * success → ``(entry, 200)``
    """
    path = body.get("path") if isinstance(body, dict) else None
    if not path or not isinstance(path, str) or not path.startswith("/"):
        return {"error": "path must be an absolute string"}, 400
    try:
        entry = workspace_catalog.add(path)
    except ValueError as e:
        return {"error": str(e)}, 400
    return entry, 200


def workspaces_forget(body: Any) -> tuple[dict, int]:
    """POST /api/workspaces/forget — remove the catalog entry.

    Port of ``_post_workspaces_forget``.  Refuses to forget a running
    workspace; the caller must stop it first.

      * missing / non-string ``path`` → ``({"error": "path required"}, 400)``
      * ``find_running(path) is not None`` →
        ``({"error": "stop the server before forgetting"}, 409)``
      * else ``forget(path)`` → ``({"ok": True}, 200)``
    """
    path = body.get("path") if isinstance(body, dict) else None
    if not path or not isinstance(path, str):
        return {"error": "path required"}, 400
    if workspace_catalog.find_running(path) is not None:
        return {"error": "stop the server before forgetting"}, 409
    workspace_catalog.forget(path)
    return {"ok": True}, 200


def workspaces_cleanup_stale(body: Any) -> tuple[dict, int]:
    """POST /api/workspaces/cleanup-stale — remove a stale running-registry
    entry plus orphan workspace-local files.

    Port of ``_post_workspaces_cleanup_stale``.  Refuses if the PID is in fact
    alive.

      * missing / non-string ``path`` → ``({"error": "path required"}, 400)``
      * ``find_running(path) is not None`` →
        ``({"error": "server is still running"}, 409)``
      * else ``unregister_server(path)`` + best-effort unlink of
        ``<path>/.pbg/server/{server-info,server.pid}`` → ``({"ok": True}, 200)``
    """
    path = body.get("path") if isinstance(body, dict) else None
    if not path or not isinstance(path, str):
        return {"error": "path required"}, 400
    if workspace_catalog.find_running(path) is not None:
        return {"error": "server is still running"}, 409
    workspace_catalog.unregister_server(path)
    # Best-effort removal of the orphan workspace-local files.
    sdir = Path(path).expanduser().resolve() / ".pbg" / "server"
    for fname in ("server-info", "server.pid"):
        try:
            (sdir / fname).unlink()
        except FileNotFoundError:
            pass
    return {"ok": True}, 200
