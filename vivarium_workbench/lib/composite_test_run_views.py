"""Pure builder for the ``POST /api/composite-test-run`` route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_composite_test_run``.  The handler starts a DETACHED
composite run: it writes a run-request JSON file + a ``runs_meta`` row, spawns
the ``run-composite`` CLI detached via :func:`run_registry.spawn_detached`, and
returns ``202 {run_id, status: "running"}`` immediately (the browser then polls
``/api/composite-run/<id>/status``).

The builder returns ``(body, status)`` so the FastAPI route wraps every path in
``JSONResponse`` (preserving the non-200 codes — 400 / 429 / 500 — verbatim).
No ``import server`` here.

``composite_runs`` (as ``cr``) and ``run_registry`` are bound at module level so
tests monkeypatch ``cr.generate_run_id`` (a fixed id), ``run_registry.
count_running`` (0 / ≥ cap), and ``run_registry.spawn_detached`` (a fake pid, or
to raise) and never spawn a real subprocess.

The workspace root is threaded explicitly as ``ws_root`` (replacing the server
``WORKSPACE`` global / ``workspace_paths()`` helper) so the module stays
importable standalone and flip-ready.  ``_ws_add_to_sys_path`` is replicated
inline (the workspace's own ``pbg_<slug>`` package must be importable when the
detached CLI is later spawned).  The legacy server.py handler keeps its inline
logic for now — the dedup happens at the flip.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import yaml

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import run_registry
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Make the workspace's own Python package(s) importable.

    Replicates ``server._ws_add_to_sys_path`` (which uses the ``WORKSPACE``
    global) with the root threaded explicitly: insert ``ws_root`` on ``sys.path``
    so the workspace package (e.g. ``pbg_chromosome_rep1``) resolves as a
    top-level package.
    """
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def composite_test_run(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Start a detached composite run. Returns ``(response_dict, status_code)``.

    Behaviour-preserving port of ``_post_composite_test_run`` (body
    ``{id, overrides?, steps?, label?, emit_paths?}``):

      * missing ``id``                 → ``({"error": "missing id"}, 400)``
      * at concurrency cap             → ``({"error": "too many runs in
        progress — wait for one to finish"}, 429)``
      * spawn failure                  → ``({"error": f"spawn failed: {e}",
        "run_id": run_id}, 500)`` (after ``complete_metadata(status="failed")``)
      * happy path                     → ``({"run_id": run_id,
        "status": "running"}, 202)``
    """
    _ws_add_to_sys_path(ws_root)
    from vivarium_workbench.lib.composite_runs import auto_label

    spec_id = (body.get("id") or "").strip()
    overrides = body.get("overrides") or {}
    steps = int(body.get("steps") or 5)
    label = (body.get("label") or "").strip() or auto_label(overrides)
    emit_paths = body.get("emit_paths") or []
    if not isinstance(emit_paths, list):
        emit_paths = []
    if not spec_id:
        return {"error": "missing id"}, 400

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or (
        "pbg_" + ws_data.get("name", "").replace("-", "_"))
    db_file = str(WorkspacePaths.load(ws_root).pbg / "composite-runs.db")

    if run_registry.count_running(db_file) >= run_registry.CONCURRENCY_CAP:
        return (
            {"error": "too many runs in progress — wait for one to finish"},
            429,
        )

    from vivarium_workbench.lib import run_core
    from vivarium_workbench.lib.remote_pinned import is_pinned_enabled
    # B (demo blocker): when this workbench is configured for remote runs
    # (VIVARIUM_WORKBENCH_REMOTE_PINNED — the operator's declaration that it
    # dispatches to an sms-api deployment), route the Composites-tab Run to the
    # 'deployment' target (compose-on-Batch via remote_run.run_remote) instead of
    # spawning the full model in this lightweight pod. The prod workspace has no
    # `.viv-build.json`, so run_target_for would otherwise resolve to 'local'.
    # Scoped to THIS call site — run_target_for (and thus composite_resolve, which
    # needs a .viv-build.json simulator_id) is left untouched. Local dev (env
    # unset) passes target=None and falls through to run_target_for → 'local'.
    target = "deployment" if is_pinned_enabled() else None
    try:
        plan = run_core.invoke_run(ws_root, spec_id=spec_id, config=overrides,
                                   db_path=db_file, label=label, n_steps=steps,
                                   target=target)
    except run_core.RunTargetUnavailable as e:
        return {"error": str(e)}, 409
    run_id = plan.run_id
    run_dir = WorkspacePaths.load(ws_root).pbg / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_rel = str((run_dir / "run.log").relative_to(ws_root))
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps({
        "run_id": run_id,
        "spec_id": spec_id,
        "pkg": pkg,
        "workspace": str(ws_root),
        "overrides": overrides,
        "steps": steps,
        "emit_paths": emit_paths,
        "db_file": db_file,
        "log_path": log_rel,
        # SP-D2: which target the detached runner dispatches to (local subprocess
        # vs. sms-api /compose/v1). `run_target_for` stamps 'deployment' for a
        # materialized remote build (.viv-build.json), 'local' otherwise.
        "target": plan.target,
    }), encoding="utf-8")

    conn = cr.connect(db_file)
    try:
        # SP-B: runs are durable — no prune-to-20 eviction. Deletion is an
        # explicit Sim-DB action (composite_runs.delete_run), not auto-eviction.
        cr.save_metadata(conn, spec_id=spec_id, run_id=run_id,
                         params=overrides, label=label,
                         started_at=time.time(), n_steps=steps,
                         log_path=log_rel, workspace=ws_root)
        try:
            pid = run_registry.spawn_detached(
                request_path, workspace=ws_root,
                log_path=run_dir / "run.log")
        except Exception as e:  # noqa: BLE001 — surface the spawn failure
            cr.complete_metadata(conn, run_id=run_id, n_steps=0,
                                 status="failed", workspace=ws_root)
            return {"error": f"spawn failed: {e}", "run_id": run_id}, 500
        cr.set_pid(conn, run_id=run_id, pid=pid)
    finally:
        conn.close()

    return {"run_id": run_id, "status": "running"}, 202
