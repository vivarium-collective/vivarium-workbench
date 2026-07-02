"""Pure builders for the 2 workspace process-management POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_workspaces_start`` / ``_post_workspaces_stop``.

These 2 POSTs spawn / SIGTERM a ``vivarium-dashboard serve`` child process for a
workspace that is registered in the GLOBAL ``~/.pbg`` catalog (reached via
``pbg_superpowers.workspace_catalog`` — process-global, NOT server state).

``start`` spawns the child via ``subprocess.Popen`` (idempotent: returns the
existing URL if a live entry already exists) and polls the catalog for up to 8 s.
``stop`` SIGTERMs the running child and polls for its atexit hook to remove the
global registry entry for up to 3 s; it refuses to stop the dashboard's own
bound workspace (``ws_root``).

The ``start`` builder takes ``ws_root`` for signature consistency with ``stop``
even though it does not use it — ``stop`` uses it for the self-stop guard (the
stdlib handler's ``WORKSPACE`` global is ``ws_root`` here).

Each builder returns ``(body, status)`` — the FastAPI route wraps every path
(success AND error) in ``JSONResponse`` so the lib-returned code is preserved
verbatim.

``subprocess`` / ``os`` / ``signal`` / ``time`` / ``sys`` and
``workspace_catalog`` are module-level names reached via attribute access so
tests can monkeypatch them (``workspaces_process_views.subprocess.Popen`` /
``.os.kill`` / ``.workspace_catalog`` / ``.time``) and never spawn or kill a
real process.  No ``import server`` here.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pbg_superpowers import workspace_catalog


def workspaces_start(ws_root: Path, body: Any) -> tuple[dict, int]:
    """POST /api/workspaces/start — spawn ``vivarium-dashboard serve`` for a
    stopped workspace and poll until it registers.

    Port of ``_post_workspaces_start``.  Idempotent: returns the existing URL if
    a live entry already exists.  Returns 504 with ``log_path`` if the child
    doesn't register within 8 s.  (``ws_root`` is accepted for signature
    consistency with :func:`workspaces_stop` and is unused.)

      * missing / non-string / non-absolute ``path`` →
        ``({"error": "path must be an absolute string"}, 400)``
      * no ``workspace.yaml`` →
        ``({"error": "not a workspace (no workspace.yaml)"}, 400)``
      * not in catalog →
        ``({"error": "workspace not in catalog — Add it first"}, 400)``
      * already live → ``({"url": ..., "pid": ...}, 200)``
      * spawned + registered → ``({"url": ..., "pid": ...}, 200)``
      * timeout → ``({"error": "start_timeout", "log_path": ..., "hint": ...}, 504)``
    """
    path = body.get("path") if isinstance(body, dict) else None
    if not path or not isinstance(path, str) or not path.startswith("/"):
        return {"error": "path must be an absolute string"}, 400

    target = Path(path).expanduser().resolve()
    if not (target / "workspace.yaml").is_file():
        return {"error": "not a workspace (no workspace.yaml)"}, 400

    # Safety: only catalog paths can be spawned. Prevents the dashboard
    # from being used to launch processes against arbitrary directories.
    if not any(Path(e.get("path") or "").resolve() == target
               for e in workspace_catalog.list_workspaces()):
        return {"error": "workspace not in catalog — Add it first"}, 400

    # Idempotent: if a live entry exists, return it.
    live = workspace_catalog.find_running(target)
    if live is not None:
        return {"url": live["url"], "pid": live["pid"]}, 200

    log_path = target / ".pbg" / "server" / "start.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as logf:
        subprocess.Popen(
            [sys.executable, "-m", "vivarium_dashboard.cli",
             "serve", "--workspace", str(target)],
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            cwd=str(target),
        )

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        entry = workspace_catalog.find_running(target)
        if entry is not None:
            return {"url": entry["url"], "pid": entry["pid"]}, 200
        time.sleep(0.1)

    return {
        "error": "start_timeout",
        "log_path": str(log_path),
        "hint": f"tail {log_path}",
    }, 504


def workspaces_stop(ws_root: Path, body: Any) -> tuple[dict, int]:
    """POST /api/workspaces/stop — SIGTERM a running workspace's dashboard
    and poll for the child's atexit hook to remove the global registry entry.

    Port of ``_post_workspaces_stop``.  Refuses self-stop and uncatalogued
    paths.  Does NOT escalate to SIGKILL on timeout — returns 504 with the PID
    instead.  ``ws_root`` is the dashboard's own bound workspace (the stdlib
    handler's ``WORKSPACE`` global).

      * missing / non-string / non-absolute ``path`` →
        ``({"error": "path must be an absolute string"}, 400)``
      * not in catalog → ``({"error": "workspace not in catalog"}, 400)``
      * self-stop (``target == ws_root``) →
        ``({"error": "refusing to stop self — use the terminal: kill {pid}"}, 400)``
      * not running → ``({"error": "not running"}, 400)``
      * already dead between find + kill → ``({"ok": True}, 200)``
      * SIGTERM + deregistered → ``({"ok": True}, 200)``
      * timeout → ``({"error": "stop_timeout", "hint": ...}, 504)``
    """
    path = body.get("path") if isinstance(body, dict) else None
    if not path or not isinstance(path, str) or not path.startswith("/"):
        return {"error": "path must be an absolute string"}, 400

    target = Path(path).expanduser().resolve()

    # Catalog membership guard (same as /start).
    if not any(Path(e.get("path") or "").resolve() == target
               for e in workspace_catalog.list_workspaces()):
        return {"error": "workspace not in catalog"}, 400

    # Refuse self-stop: ws_root is the dashboard's own bound workspace,
    # already resolved by serve(). Stopping it would kill the dashboard
    # the user is currently using.
    if target == ws_root:
        entry_self = workspace_catalog.find_running(target)
        pid_self = entry_self["pid"] if entry_self else os.getpid()
        return {
            "error": f"refusing to stop self — use the terminal: kill {pid_self}"
        }, 400

    entry = workspace_catalog.find_running(target)
    if entry is None:
        return {"error": "not running"}, 400

    pid = int(entry["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Already dead between find_running and os.kill — treat as success.
        return {"ok": True}, 200

    # Poll for the child's atexit to remove the global entry.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if workspace_catalog.find_entry(target) is None:
            return {"ok": True}, 200
        time.sleep(0.1)

    return {
        "error": "stop_timeout",
        "hint": f"PID {pid} still alive; SIGKILL it manually if stuck",
    }, 504
