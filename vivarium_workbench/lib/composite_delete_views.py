"""Pure builder for ``POST /api/composite-run/{run_id}/delete``.

Removes a composite run from the Simulations DB: best-effort-stops it if still
running, deletes its ``runs_meta`` row (and writes a ``deleted`` tombstone into
the append-only run log so the JSONL fold does not resurrect it — see
``composite_runs.delete_run``), and removes the run's artifact directory under
``.pbg/runs/<run_id>/``. Returns ``(body, status)`` so the FastAPI route wraps
every path in ``JSONResponse`` with the mapped status.

Kept as a thin, importable-standalone builder mirroring ``composite_stop_views``
so the route stays a one-liner and the outcome→HTTP-code mapping is
unit-testable without a live server.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import run_registry
from vivarium_workbench.lib import simulations_index
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def delete_composite_run(ws_root: Path, run_id: str) -> tuple[dict, int]:
    """Delete the composite run ``run_id`` from the Simulations DB.

      * missing ``run_id``            → ``400``
      * unknown run (no row removed)  → ``404`` ``{"deleted": false, ...}``
      * removed                        → ``200`` ``{"deleted": true, ...}``

    Idempotent: deleting an already-absent run is a ``404`` no-op. If the run is
    still live it is signalled (SIGTERM, best-effort) before its row is removed,
    so no orphaned worker keeps writing after the row is gone.
    """
    run_id = (run_id or "").strip()
    if not run_id:
        return {"error": "missing run_id"}, 400

    paths = WorkspacePaths.load(ws_root)
    db_file = paths.pbg / "composite-runs.db"
    if not db_file.exists():
        return {"run_id": run_id, "deleted": False, "outcome": "not_found"}, 404

    # Kill a still-running worker first so it cannot write after the row is gone.
    try:
        run_registry.stop_run(db_file, run_id, workspace=ws_root)
    except Exception:
        pass

    conn = cr.connect(db_file)
    try:
        removed = cr.delete_run(conn, run_id=run_id, workspace=ws_root)
    finally:
        conn.close()

    # Remove the run's artifact dir (.pbg/runs/<run_id>/), guarded to stay inside
    # the runs-root so a crafted run_id cannot escape the workspace.
    runs_root = (paths.pbg / "runs").resolve()
    run_dir = (paths.pbg / "runs" / run_id).resolve()
    if run_dir != runs_root and runs_root in run_dir.parents and run_dir.is_dir():
        shutil.rmtree(run_dir, ignore_errors=True)

    # Bust the TTL-cached /api/simulations build so the next read reflects the
    # deletion immediately (otherwise the row lingers for up to the 15s TTL).
    if removed:
        try:
            simulations_index.clear_build_cache()
        except Exception:
            pass

    status = 200 if removed else 404
    return {"run_id": run_id, "deleted": bool(removed),
            "outcome": "deleted" if removed else "not_found"}, status
