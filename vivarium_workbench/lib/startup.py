"""Uvicorn boot path for ``dashboard serve`` — the FastAPI entrypoint.

This replaces the retired stdlib HTTP server (``server.serve``). It reproduces
the side effects ``server.serve`` performed before accepting requests, then runs
the FastAPI app (``api.app:app``) under uvicorn:

* ``chdir`` into the workspace — in-process composite/generator builds resolve
  workspace-relative paths (e.g. ``out/cache/initial_state.json``);
* put the workspace on ``sys.path`` so its ``pbg_*`` package imports;
* register the active workspace root (the FastAPI ``get_workspace`` dependency
  reads it via ``active_workspace`` → ``_root``) and mirror it into the
  ``VIVARIUM_WORKBENCH_WORKSPACE`` env var;
* reconcile composite runs left ``running`` by a previous crash/restart;
* write ``.pbg/server/server-info`` so tests/tools can detect readiness.

No dependency on ``vivarium_workbench.server``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


class _BasePathStripMiddleware:
    """Serve the app under a URL prefix when the upstream proxy forwards the
    FULL path (does NOT strip it) — e.g. an AWS ALB path rule ``/workbench/*``.

    Strips ``base_path`` from the front of the request path for route matching
    and records it as ``root_path`` (for URL generation). Requests that do NOT
    carry the prefix pass through unchanged — the ``/health`` target-group check
    and ``/bigraph-loom/*`` (which the ALB routes to this service *unprefixed*).
    Lifespan and other non-HTTP scopes pass straight through.
    """

    def __init__(self, app, base_path: str):
        self.app = app
        self.base_path = base_path

    async def __call__(self, scope, receive, send):
        bp = self.base_path
        if bp and scope.get("type") in ("http", "websocket"):
            path = scope.get("path", "")
            if path == bp or path.startswith(bp + "/"):
                scope = dict(scope)
                scope["path"] = path[len(bp):] or "/"
                scope["root_path"] = bp
        await self.app(scope, receive, send)


def serve_fastapi(workspace: Path, port: int, host: str = "127.0.0.1", base_path: str = "") -> int:
    """Boot the FastAPI dashboard app under uvicorn against ``workspace``.

    ``host`` defaults to loopback; pass ``0.0.0.0`` to bind every interface
    (required inside a container whose published port must be reachable).
    Blocks until the server stops; returns 0 on clean shutdown.
    """
    workspace = Path(workspace).resolve()

    # Configure structured logging so the access-log middleware actually emits.
    # basicConfig is a no-op if the root logger is already configured.
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # CWD = workspace root (see module docstring).
    os.chdir(workspace)
    if str(workspace) not in sys.path:
        sys.path.insert(0, str(workspace))

    from vivarium_workbench.lib._root import set_workspace_root
    set_workspace_root(workspace)
    os.environ["VIVARIUM_WORKBENCH_WORKSPACE"] = str(workspace)

    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    pbg = WorkspacePaths.load(workspace).pbg

    # Repair runs left 'running' by a previous crash/restart — never block boot.
    try:
        from vivarium_workbench.lib.run_registry import reconcile_stale_runs
        n = reconcile_stale_runs(pbg / "composite-runs.db", workspace=workspace)
        if n:
            print(f"reconciled {n} stale composite run(s) on startup")
    except Exception as e:  # noqa: BLE001
        print(f"warning: run reconcile failed: {e}", file=sys.stderr)

    # Readiness file (parity with the retired stdlib server).
    advertise_host = "127.0.0.1" if host == "0.0.0.0" else host
    try:
        info_dir = pbg / "server"
        info_dir.mkdir(parents=True, exist_ok=True)
        (info_dir / "server-info").write_text(json.dumps({
            "port": port,
            "host": advertise_host,
            "bind_host": host,
            "url": f"http://{advertise_host}:{port}",
            "pid": os.getpid(),
            "screen_dir": str(info_dir / "content"),
            "state_dir": str(info_dir / "state"),
        }), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"warning: writing server-info failed: {e}", file=sys.stderr)

    import uvicorn
    from vivarium_workbench.api.app import app

    # Record the CONFIGURED prefix on the app so routes reached *unprefixed* can
    # still resolve it. The middleware only sets a per-request ``root_path`` when
    # it actually strips the prefix — but the ALB routes ``/bigraph-loom/*`` to
    # this service unprefixed, so that path has no ``root_path``. The loom asset
    # route needs the prefix there to inject its base-path shim (its bundle calls
    # a root-absolute ``/api/...`` that would otherwise be routed to sms-api).
    app.state.base_path = base_path

    # Under a base path the ALB forwards the FULL /workbench/... path (no strip),
    # so wrap the app to strip the prefix for route matching AND record it as the
    # per-request root_path (which index_shell reads to base-path the render).
    # The middleware is the SOLE source of root_path — do NOT also set it on
    # uvicorn, or the access log double-counts the prefix. No-op when empty.
    served = _BasePathStripMiddleware(app, base_path) if base_path else app

    # Run the app object (not an import string) so it shares this process's
    # already-registered workspace root; disables reload, which is correct for
    # the served entrypoint.
    #
    # proxy_headers/forwarded_allow_ips: honor X-Forwarded-* when present so the
    # ASGI scope reflects the real client scheme/host behind the ALB/SSM tunnel.
    # (Not the CSRF fix — an AWS ALB omits X-Forwarded-Host, so the allowlist is
    # what actually admits the browser origin; this just keeps request metadata
    # honest for logging and any forwarded-aware code.)
    uvicorn.run(
        served, host=host, port=port, log_level="info",
        proxy_headers=True, forwarded_allow_ips="*",
    )
    return 0
