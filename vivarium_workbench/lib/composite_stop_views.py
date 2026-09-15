"""Pure builder for ``POST /api/composite-run/{run_id}/stop`` (issue #754).

Stops an in-flight detached composite run: signals the run's process group
(SIGTERM, so the worker's faulthandler dumps a traceback into its run.log) and
marks it ``cancelled``. Returns ``(body, status)`` so the FastAPI route wraps
every path in ``JSONResponse`` with the status the outcome maps to.

Kept as a thin, importable-standalone builder mirroring
``composite_test_run_views`` so the route stays a one-liner and the mapping from
``run_registry.stop_run`` outcomes to HTTP codes is unit-testable without a live
server.
"""

from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib import run_registry
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# outcome from run_registry.stop_run → HTTP status
_OUTCOME_STATUS = {
    "not_found": 404,
    "already_terminal": 200,
    "no_pid": 200,
    "dead": 200,
    "signalled": 200,
}


def stop_composite_run(ws_root: Path, run_id: str) -> tuple[dict, int]:
    """Stop the detached run ``run_id``. Returns ``(response_dict, status_code)``.

      * unknown run          → ``404`` ``{"outcome": "not_found", ...}``
      * already finished      → ``200`` ``{"outcome": "already_terminal", ...}``
      * running (signalled / no-pid / dead) → ``200`` with the terminal state

    Idempotent: stopping an already-terminal run is a 200 no-op.
    """
    run_id = (run_id or "").strip()
    if not run_id:
        return {"error": "missing run_id"}, 400
    # Plan B: a "remote-sim-<id>" run executes on GovCloud and has no local
    # process to signal (and sms-api exposes no simulation cancel). Return a
    # graceful, honest outcome — the loom's Stop detaches from a cloud run
    # rather than pretending to cancel it, and the run continues on GovCloud.
    if run_id.startswith("remote-sim-"):
        return {"run_id": run_id, "outcome": "remote-uncancellable",
                "note": "Cloud runs continue on GovCloud — track it in the Runs tab."}, 200
    db_file = WorkspacePaths.load(ws_root).pbg / "composite-runs.db"
    result = run_registry.stop_run(db_file, run_id, workspace=ws_root)
    status = _OUTCOME_STATUS.get(result.get("outcome"), 200)
    return result, status
