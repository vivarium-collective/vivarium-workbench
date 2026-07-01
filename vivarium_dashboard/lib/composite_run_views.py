"""HTTP-free builders for the composite-run read routes.

These are the library seam behind four dashboard GET routes:

  * ``GET /api/composite-runs?spec_id=X``            → :func:`build_composite_runs`
  * ``GET /api/composite-run/{run_id}``              → :func:`build_composite_run`
  * ``GET /api/composite-run/{run_id}/state?step=N`` → :func:`build_composite_run_state`
  * ``GET /api/composite-run/{run_id}/status``       → :func:`build_composite_run_status`

All read from the workspace's ``.pbg/composite-runs.db`` via ``lib.composite_runs``.
Pure ``ws_root``-parameterised functions: NO ``import server`` (docstring reference only).

The FastAPI app (``api/app.py``) imports these builders directly; ``server.py``'s
``_get_composite_run*`` shims delegate here rather than duplicating the logic.
"""
from __future__ import annotations

import json
from pathlib import Path

from vivarium_dashboard.lib import composite_runs as cr
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def _db_file(ws_root: Path) -> Path:
    """Resolve the composite-runs database path for a workspace."""
    return WorkspacePaths.load(ws_root).pbg / "composite-runs.db"


def build_composite_runs(
    ws_root: Path, spec_id: str | None
) -> tuple[dict, int]:
    """Worker for ``GET /api/composite-runs?spec_id=X``.

    Returns ``(payload_dict, http_status)``:

    * HTTP 400 ``{"runs": [], "error": "missing spec_id"}`` — when ``spec_id``
      is absent or empty (mirrors ``_get_composite_runs`` line 8092).
    * HTTP 200 ``{"runs": []}`` — when the ``.pbg/composite-runs.db`` file is
      absent (first-launch, no runs yet).
    * HTTP 200 ``{"runs": [...]}`` — run list for the given spec, newest first.
    """
    if not spec_id:
        return {"runs": [], "error": "missing spec_id"}, 400
    db = _db_file(ws_root)
    if not db.is_file():
        return {"runs": []}, 200
    conn = cr.connect(db)
    try:
        runs = cr.query_runs(conn, spec_id=spec_id)
    finally:
        conn.close()
    return {"runs": runs}, 200


def build_composite_run(ws_root: Path, run_id: str) -> tuple[dict, int]:
    """Worker for ``GET /api/composite-run/{run_id}``.

    Returns ``(payload_dict, http_status)``:

    * HTTP 404 ``{"error": "no run database"}`` — db absent.
    * HTTP 404 ``{"error": "run not found"}`` — trajectory is empty (no history
      rows for ``run_id``).
    * HTTP 200 ``{"run_id": ..., "trajectory": [...]}`` — success.
    """
    db = _db_file(ws_root)
    if not db.is_file():
        return {"error": "no run database"}, 404
    conn = cr.connect(db)
    try:
        trajectory = cr.query_run(conn, run_id=run_id)
    finally:
        conn.close()
    if not trajectory:
        return {"error": "run not found"}, 404
    return {"run_id": run_id, "trajectory": trajectory}, 200


def build_composite_run_state(
    ws_root: Path, run_id: str, step: int
) -> tuple[dict, int]:
    """Worker for ``GET /api/composite-run/{run_id}/state?step=N``.

    The **caller** is responsible for parsing ``step`` from the query string
    and returning HTTP 400 on ``ValueError`` — this function only handles the
    database lookup (``step`` is already an ``int`` here).

    Returns ``(payload_dict, http_status)``:

    * HTTP 404 ``{"error": "no run database"}`` — db absent.
    * HTTP 404 ``{"error": "state not found for run+step"}`` — step not in history.
    * HTTP 200 ``{"run_id": ..., "step": ..., "state": {...}}`` — success.
    """
    db = _db_file(ws_root)
    if not db.is_file():
        return {"error": "no run database"}, 404
    conn = cr.connect(db)
    try:
        state = cr.query_run_state(conn, run_id=run_id, step=step)
    finally:
        conn.close()
    if state is None:
        return {"error": "state not found for run+step"}, 404
    return {"run_id": run_id, "step": step, "state": state}, 200


def build_composite_run_status(ws_root: Path, run_id: str) -> tuple[dict, int]:
    """Worker for ``GET /api/composite-run/{run_id}/status``.

    Returns ``(payload_dict, http_status)``:

    * HTTP 404 ``{"error": "no run database"}`` — db absent.
    * HTTP 404 ``{"error": "run not found"}`` — ``run_id`` not in ``runs_meta``.
    * HTTP 200 ``{run_id, status, progress_step, n_steps, heartbeat_at,
      [log_path, error] | [viz_html]}`` — success.

    Terminal-state enrichment (mirrors ``_get_composite_run_status`` lines
    8198–8212):

    * ``failed`` / ``orphaned``: if ``log_path`` is in meta, adds ``log_path``
      to the response and (when the file exists) reads the last 2 000 chars as
      the ``error`` excerpt.
    * ``completed``: reads ``viz_html`` from
      ``.pbg/runs/<run_id>/viz.json`` when the file exists; skipped on
      ``JSONDecodeError``.
    """
    ws_root = Path(ws_root)
    db = _db_file(ws_root)
    if not db.is_file():
        return {"error": "no run database"}, 404
    conn = cr.connect(db)
    try:
        meta = cr.query_run_meta(conn, run_id=run_id)
    finally:
        conn.close()
    if meta is None:
        return {"error": "run not found"}, 404

    resp: dict = {
        "run_id": run_id,
        "status": meta["status"],
        "progress_step": meta.get("progress_step") or 0,
        "n_steps": meta.get("n_steps"),
        "heartbeat_at": meta.get("heartbeat_at"),
    }
    if meta["status"] in ("failed", "orphaned"):
        log_rel = meta.get("log_path")
        if log_rel:
            resp["log_path"] = log_rel
            log_full = ws_root / log_rel
            if log_full.is_file():
                resp["error"] = log_full.read_text(encoding="utf-8")[-2000:]
    elif meta["status"] == "completed":
        wp = WorkspacePaths.load(ws_root)
        run_dir = wp.pbg / "runs" / run_id
        viz_file = run_dir / "viz.json"
        if viz_file.is_file():
            try:
                resp["viz_html"] = json.loads(viz_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        analyses_file = run_dir / "analyses.json"
        has_analyses = False
        if analyses_file.is_file():
            content = analyses_file.read_text(encoding="utf-8").strip()
            has_analyses = content not in ("", "[]")
        resp["has_analyses"] = has_analyses
        resp["has_report"] = (run_dir / "report.html").is_file()
        resp["downloadable"] = True
    else:
        resp["downloadable"] = False
    return resp, 200
