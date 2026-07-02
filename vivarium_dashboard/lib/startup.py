"""Uvicorn boot path for ``dashboard serve`` — the FastAPI entrypoint.

This replaces the retired stdlib HTTP server (``server.serve``). It reproduces
the side effects ``server.serve`` performed before accepting requests, then runs
the FastAPI app (``api.app:app``) under uvicorn:

* ``chdir`` into the workspace — in-process composite/generator builds resolve
  workspace-relative paths (e.g. ``out/cache/initial_state.json``);
* put the workspace on ``sys.path`` so its ``pbg_*`` package imports;
* register the active workspace root (the FastAPI ``get_workspace`` dependency
  reads it via ``active_workspace`` → ``_root``) and mirror it into the
  ``VIVARIUM_DASHBOARD_WORKSPACE`` env var;
* reconcile composite runs left ``running`` by a previous crash/restart;
* write ``.pbg/server/server-info`` so tests/tools can detect readiness.

No dependency on ``vivarium_dashboard.server``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def serve_fastapi(workspace: Path, port: int, host: str = "127.0.0.1") -> int:
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

    from vivarium_dashboard.lib._root import set_workspace_root
    set_workspace_root(workspace)
    os.environ["VIVARIUM_DASHBOARD_WORKSPACE"] = str(workspace)

    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
    pbg = WorkspacePaths.load(workspace).pbg

    # Repair runs left 'running' by a previous crash/restart — never block boot.
    try:
        from vivarium_dashboard.lib.run_registry import reconcile_stale_runs
        n = reconcile_stale_runs(pbg / "composite-runs.db")
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
    from vivarium_dashboard.api.app import app

    # Run the app object (not an import string) so it shares this process's
    # already-registered workspace root; disables reload, which is correct for
    # the served entrypoint.
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
