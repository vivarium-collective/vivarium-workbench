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
importable standalone.  ``_ws_add_to_sys_path`` is replicated
inline (the workspace's own ``pbg_<slug>`` package must be importable when the
detached CLI is later spawned).  This builder is the sole implementation;
``api/app.py`` calls it directly.

Where a run goes is the selected backend's answer (``runs.dispatch_composite``,
step P2 Builds B2, docs/backend-adapters.md §2.1): the SMS cloud dispatch (a
build's pre-built image, the opt-in compose path) is the plugin's
``vivarium_workbench_sms.cloud_dispatch``; this module names no backend but
compose-api (core), whose launched runs get its manifest stamp.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import run_registry
from vivarium_workbench.lib.path_safety import plain_name
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


from vivarium_workbench.lib.workspace_paths import add_ws_to_sys_path as _ws_add_to_sys_path


# Step count above which a run gets a non-blocking "this may be heavy" warning
# in the launch response (full-state snapshots per step add up).
_HEAVY_RUN_STEPS = 250


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
    # float: a temporal composite may run a fractional duration (e.g. 2.5); an
    # int here truncated it. A whole number stays whole (JSON 5 → 5.0 → runs 5).
    steps = float(body.get("steps") or 5)
    label = (body.get("label") or "").strip() or auto_label(overrides)
    emit_paths = body.get("emit_paths") or []
    if not isinstance(emit_paths, list):
        emit_paths = []
    # Loom save-point fork: a full captured state to start this run FROM.
    seed_state = body.get("seed_state") or {}
    if not isinstance(seed_state, dict):
        seed_state = {}
    # Config declaration surface: a composite-run body may declare analyses
    # and/or visualizations to auto-run on flush (study-shaped, possibly
    # scale-grouped -- NOT flattened here; ephemeral_study.merge_declarations
    # does that downstream). The documented shape is a scale-grouped DICT
    # (e.g. {"single": [...], "multigeneration": [...]}), which
    # merge_declarations._flatten_analyses already accepts directly -- so
    # both a list AND a dict must pass through unchanged here. Only a
    # genuinely invalid type (string, number, etc.) degrades to the empty
    # shape, so the Task 5 merge is a no-op.
    declared_analyses = body.get("analyses") or []
    if not isinstance(declared_analyses, (list, dict)):
        declared_analyses = []
    declared_visualizations = body.get("visualizations") or []
    if not isinstance(declared_visualizations, (list, dict)):
        declared_visualizations = []
    declared_results = {
        "analyses": declared_analyses,
        "visualizations": declared_visualizations,
    }
    if not spec_id:
        return {"error": "missing id"}, 400
    # The id becomes the run id, and the run id becomes a directory name under .pbg/runs/.
    plain_name(spec_id, "composite id")
    # A scaffolded study.yaml carries ``composite: replace_me.composites.placeholder``
    # (scaffold_yaml.py) as a sentinel the author is meant to REPLACE with a real
    # composite id. It never names a runnable composite: dispatching it spawns a
    # detached run that can only fail to build, and until it does the run shows as
    # a phantom "running" row with no backing model (the placeholder-run bug).
    # Refuse it here, at the entry point and BEFORE any runs_meta row is written,
    # so a placeholder can never become a run at all.
    if spec_id.startswith("replace_me."):
        return {
            "error": (
                f"spec_id {spec_id!r} is the scaffold placeholder, not a real "
                "composite -- replace it with a registered composite id before "
                "running."
            )
        }, 400

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or (
        "pbg_" + ws_data.get("name", "").replace("-", "_"))
    db_file = str(WorkspacePaths.load(ws_root).pbg / "composite-runs.db")

    if run_registry.count_running(db_file) >= run_registry.CONCURRENCY_CAP:
        return (
            {"error": "too many runs in progress — wait for one to finish"},
            429,
        )

    from vivarium_workbench.lib import backends, run_core
    from vivarium_workbench.lib.backends.provider import Detached

    # Where the run goes (step P2 Builds B2): the backend that runs this workspace answers the launch itself
    # (``runs.dispatch_composite``) -- a run it started or a refusal ``(body, status)``, ``Detached`` for the
    # detached deployment runner, or ``None`` for a local run. The card no longer branches on which backend it is.
    # An explicit cloud run (``run_target: "deployment"``, the Environment picker) that no backend selected goes to
    # the ``unrouted`` facet, as the detached runner's does (step P2 Runs b); with none (providers off) it is refused.
    build_ref = None
    target = "local"
    chosen = backends.select(ws_root)
    if chosen is None and str(body.get("run_target") or "").strip() == "deployment":
        ub = backends.unrouted()
        if ub is None or ub.unrouted is None:
            return {"error": "no remote backend was named (serve --backend-base-url)", "reason": "no-backend",
                    "run_target": "deployment"}, 409
        out = ub.unrouted.dispatch_composite(ws_root, body, spec_id=spec_id, overrides=overrides,
                                             emit_paths=emit_paths, steps=steps)
    else:
        if chosen is None:
            # wb#1630: a backend the operator named that cannot be reached is refused (APIError 502), never
            # replaced by a local run.
            chosen = backends.active(ws_root)
        out = (chosen.runs.dispatch_composite(ws_root, body, spec_id=spec_id, overrides=overrides,
                                              emit_paths=emit_paths, steps=steps)
               if chosen is not None else None)
    if isinstance(out, tuple):
        return out
    if isinstance(out, Detached):
        target, build_ref = "deployment", out.build_ref
    # else: out is None → a local run (target stays "local").
    on_compose = chosen is backends.COMPOSE
    try:
        plan = run_core.invoke_run(ws_root, spec_id=spec_id, config=overrides,
                                   db_path=db_file, label=label, n_steps=steps,
                                   target=target)
    except run_core.RunTargetUnavailable as e:
        return {"error": str(e)}, 409
    run_id = plan.run_id
    run_dir = WorkspacePaths.load(ws_root).pbg / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        log_rel = str((run_dir / "run.log").relative_to(ws_root))
    except ValueError:
        # WorkspacePaths.pbg may resolve outside ws_root when VIVARIUM_PBG_DIR
        # is set (viva-workspace#1 — redirects .pbg off a network-mounted
        # workspace root onto local/ephemeral storage). Store the absolute
        # path instead: the one consumer that reads this back
        # (composite_run_views.py's ``ws_root / log_rel``) resolves correctly
        # either way, since pathlib discards the left operand of ``/`` when
        # the right operand is already absolute.
        log_rel = str(run_dir / "run.log")
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps({
        "run_id": run_id,
        "spec_id": spec_id,
        "pkg": pkg,
        "workspace": str(ws_root),
        "overrides": overrides,
        "steps": steps,
        "emit_paths": emit_paths,
        "seed_state": seed_state,
        "declared_results": declared_results,
        "db_file": db_file,
        "log_path": log_rel,
        # SP-D2: which target the detached runner dispatches to: a local subprocess, or the
        # deployment (``remote_run.run_remote`` -> the selected backend's ``runs.run_composite``).
        "target": plan.target,
        # A build reference the backend's ``dispatch_composite`` returned (``Detached.build_ref``),
        # carried unread to ``runs.run_composite``. None on every other path.
        "build_ref": build_ref,
    }), encoding="utf-8")

    # Reproducibility manifest (spec Part A): the composite path's full replay
    # record — params here IS the full effective config for this launch (no
    # separate baseline layer at this call site), so it doubles as both the
    # override-delta (params_json, unchanged) and the manifest's full params.
    # emitter is not yet resolved at launch time (that happens later, inside
    # the detached run) so it's recorded None here; runtime has no study
    # block on the composite path.
    manifest = cr.build_run_manifest(
        origin="composite", spec_id=spec_id, params=overrides, n_steps=steps,
        emitter=None, emit_paths=emit_paths, runtime={}, pkg=pkg,
        ws_root=ws_root,
    )
    # Where it runs, recorded in the manifest -- never in params, which a re-run sends back as the composite's
    # inputs. The run lands into this workspace's store, so without this its Origin would read "local".
    if plan.target == "deployment":
        from vivarium_workbench.lib.backends import config as backend_cfg

        manifest["ran_on"] = backend_cfg.deployment_name()
        if on_compose:
            # compose-api runs its environment's image, never this checkout; the image is recorded when the run lands.
            from vivarium_workbench.lib import remote_run_compose

            remote_run_compose.stamp_manifest(manifest, None)

    conn = cr.connect(db_file)
    try:
        # SP-B: runs are durable — no prune-to-20 eviction. Deletion is an
        # explicit Sim-DB action (composite_runs.delete_run), not auto-eviction.
        cr.save_metadata(conn, spec_id=spec_id, run_id=run_id,
                         params=overrides, label=label,
                         started_at=time.time(), n_steps=steps,
                         log_path=log_rel, workspace=ws_root, manifest=manifest)
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

    resp = {"run_id": run_id, "status": "running"}
    # Non-blocking cost heads-up: the loom writes a full-state snapshot per step,
    # so a long run can produce a large snapshot DB and a slow trajectory load.
    # The run still starts — snapshots are decimated for display and the run
    # self-terminates if the snapshot DB exceeds its 1 GiB budget — but a warning
    # lets the user cut the run short or pass explicit emit_paths first.
    if steps > _HEAVY_RUN_STEPS:
        resp["warning"] = (
            f"Long run ({int(steps)} steps): the loom captures a full-state snapshot "
            f"per step, so the snapshot DB can grow large and loading the full "
            f"trajectory is slower. Frames are decimated for display and the run "
            f"self-terminates if snapshots exceed 1 GiB. For a lighter, faster run use "
            f"fewer steps or pass explicit emit_paths."
        )
    return resp, 202
