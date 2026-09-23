"""Pure builder for the ``POST /api/investigation-run`` route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_investigation_run`` — the "run the whole investigation"
flow: it runs every simulation declared by an investigation's spec (each
composite executed once in an EMBEDDED ``python -c`` subprocess) and renders all
of its visualizations (built in-process against a freshly-built workspace core),
delegating the orchestration to ``lib.investigations.run_investigation``.

The single behavioural difference from the live handler is that the git
**commit is DEFERRED**: the legacy server wraps the orchestration in
``_active_branch_action(commit_msg, action)`` (commit-on-active-branch, with a
409-no-changes→200 special case); the FastAPI path instead runs the action
inline and returns the summary directly.  All other outcomes are reproduced
byte-identically:

  * missing name                 → ``({"error": "name is required"}, 400)``
  * ``deployment`` run target    → ``({error, name, run_target, hint}, 409)`` —
                                   added by §2A.8 workstream 8 step 2a; this
                                   route is synchronous and cannot honor it
  * core build fails             → ``({"error": "failed to build core: …"}, 500)``
  * ``InvestigationSpecError``   → ``({"error": "spec error: …"}, 400)``
  * ``FileNotFoundError``        → ``({"error": "<str(e)>"}, 404)``
  * summary carries ``"error"``  → ``({"error": err}, 400 if "spec error" else 404)``
                                   (e.g. the concurrent run-lock guard)
  * success                      → ``(summary, 200)``

The ``run_one_composite`` + ``build_and_run`` closures are moved verbatim from
the handler (``WORKSPACE`` → ``ws_root``); ``run_one_composite`` uses
``lib.json_serialize._json_default`` and the module-level ``subprocess`` so
tests monkeypatch ``investigation_run_views.subprocess.run`` and never spawn a
real composite.  No ``import server`` here.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

from vivarium_workbench.lib import study_crud_mutations
from vivarium_workbench.lib.json_serialize import _json_default


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Make the workspace's own Python package(s) importable.

    Replicates ``server._ws_add_to_sys_path`` (which uses the ``WORKSPACE``
    global) with the root threaded explicitly: insert ``ws_root`` on
    ``sys.path`` so the workspace package resolves as a top-level package.
    """
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def investigation_run(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """Run all of an investigation's simulations + render its visualizations.

    Returns ``(response_dict, code)``.  Behaviour-preserving port of
    ``_post_investigation_run`` with the ``_active_branch_action`` commit
    DEFERRED (the FastAPI path returns the summary; the live handler keeps the
    commit).  See the module docstring for the outcome table.
    """
    _ws_add_to_sys_path(ws_root)
    from vivarium_workbench.lib.investigations import (
        run_investigation, InvestigationSpecError,
    )
    from vivarium_workbench.lib.composite_lookup import (
        substitute_parameters, find_composite_path,
    )
    from vivarium_workbench.lib import composite_runs as cr

    name = study_crud_mutations._study_name_from_body(body)
    if not name:
        return {"error": "name is required"}, 400

    # §2A.8 workstream 8 step 2a: this route must not run work the user asked to
    # run on the deployment. `resolve_run_target` is item 18's authoritative
    # local-vs-deployment answer -- "never by which button happened to be
    # clicked" -- and this path never consulted it, so on a pinned deployment
    # (or any tab switched to a materialized build) every simulation ran on the
    # workbench host regardless of that choice.
    #
    # It REFUSES rather than dispatching, for a reason beyond routing: this
    # route is synchronous -- `run_investigation` orchestrates inline in the
    # request -- and a gateway-fronted deployment kills a silent request at 60 s
    # (measured on smsvpctest; prod is 600 s). So it could not carry a real
    # investigation to completion even with the target honored. The async route
    # already exists, already dispatches, and already has a UI: "Run unblocked".
    #
    # 409 mirrors `catalog_install_views`' system-deps gate -- a real pre-run
    # gate with a structured body and an actionable hint -- rather than the
    # 400/404 dispatch at the tail of this function, which classifies *failures*.
    # §A5 — converge onto run_jobs, for the investigations that CAN converge.
    #
    # The two "orchestrators" this plan set out to merge turn out to read
    # different spec shapes, which is why they never merged on their own:
    #
    #   this route            investigations/<name>/spec.yaml   (v2: composites+runs
    #                         (fallback study.yaml)              or composite+simulations)
    #   run-unblocked         investigations/<name>/investigation.yaml  (v3: members ->
    #                                                            studies/<slug>/study.yaml)
    #
    # `vwb migrate-investigations` is the one-way v2 -> v3 rewrite, and the real
    # v2ecoli build carries **11 investigations, all investigation.yaml, zero
    # spec.yaml** (checked 2026-08-28). So for every investigation anyone
    # actually has, this route's loader finds nothing and 404s — it is not a
    # rival orchestrator so much as the v2 one, still wired to a button.
    #
    # Convergence is therefore a delegation, not a merge: a v3 investigation is
    # handed to `investigation_run_unblocked` — the LIB function, per §A0's
    # "convergence should target lib functions, not routes" — and this route
    # answers 202 + job_id, the same async contract "Run unblocked" already has.
    # That is what makes the button work at all on a gateway-fronted deployment
    # (§A0.1: a synchronous route cannot outlive the ALB's idle timeout), and it
    # inherits the run-target honouring, prereq ordering and gating built in
    # A1–A3′ rather than reimplementing any of it.
    #
    # A v2 spec keeps today's synchronous behaviour, including the deployment
    # refusal below. Nothing translates a v2 spec into studies, and inventing
    # that translation here would be a migration wearing a run button.
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    inv_dir = WorkspacePaths.load(Path(ws_root)).investigations / name
    if ((inv_dir / "investigation.yaml").is_file()
            and not (inv_dir / "spec.yaml").is_file()
            and not (inv_dir / "study.yaml").is_file()):
        from vivarium_workbench.lib.run_unblocked_views import (
            investigation_run_unblocked,
        )
        return investigation_run_unblocked(ws_root, {"investigation": name})

    from vivarium_workbench.lib import remote_pinned
    if remote_pinned.resolve_run_target(Path(ws_root)) == "deployment":
        return {
            "error": "investigation resolves to the 'deployment' run target",
            "name": name,
            "run_target": "deployment",
            "hint": "POST /api/investigation-run-unblocked instead: it submits a "
                    "background job (202 + job_id, poll "
                    "/api/investigation-run-unblocked-status) and dispatches to "
                    "viva-api. This route runs inline and cannot honor a "
                    "deployment target.",
        }, 409

    # Resolve workspace package
    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

    def run_one_composite(*, spec_id, overrides, steps, sim_name, run_id, db_file,
                          state_doc=None):
        """Run one composite via subprocess. Matches _post_composite_test_run shape.

        When ``state_doc`` is provided (multi-composite path), the pre-built
        composite document is used directly; the emitter step has already been
        injected by ``inject_emitter_step``.  The SQLiteEmitter is then wired
        in by replacing the emitter address/config so the SQLite run_id/db_file
        are set correctly.

        When ``state_doc`` is None (legacy single-composite path), the composite
        is resolved from the registry by spec_id as before.
        """
        if state_doc is not None:
            # Multi-composite: state_doc already has the emitter step injected.
            # Wire the SQLiteEmitter run_id + db_file into the emitter config.
            import copy
            state_doc = copy.deepcopy(state_doc)
            state = state_doc.get("state") or {}
            emitter = state.get("emitter") or {}
            if emitter.get("_type") == "step":
                cfg = dict(emitter.get("config") or {})
                cfg["run_id"] = run_id
                cfg["db_file"] = db_file
                emitter["config"] = cfg
                emitter["address"] = "local:SQLiteEmitter"
                state["emitter"] = emitter
            state_doc["state"] = state
        else:
            # Legacy path: resolve composite from registry by spec_id.
            path = find_composite_path(ws_root, pkg, spec_id)
            if path is None:
                return {"status": "failed", "error": f"composite not found: {spec_id}"}
            text = path.read_text(encoding="utf-8")
            spec = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
            state = substitute_parameters(spec.get("state") or {},
                                          spec.get("parameters") or {},
                                          overrides)
            state = cr.inject_sqlite_emitter(state, run_id=run_id, db_file=db_file)
            state_doc = {"state": state}

        py = sys.executable
        _state_to_run = state_doc.get("state") or {}
        script = textwrap.dedent(f"""
            import json, sys, traceback
            try:
                from {pkg}.core import build_core
                from process_bigraph import Composite
                try:
                    from viva_emitters.sqlite_emitter import SQLiteEmitter
                except ImportError:  # process-bigraph < 1.4.17 (legacy location)
                    from process_bigraph.emitter import SQLiteEmitter
                core = build_core()
                core.register_link('SQLiteEmitter', SQLiteEmitter)
                composite = Composite({{'state': __import__('json').loads({json.dumps(json.dumps(_state_to_run, default=_json_default))})}}, core=core)
                composite.run({steps})
                print('@@@OK@@@')
            except Exception as e:
                print('@@@ERROR@@@')
                print(traceback.format_exc())
        """)
        try:
            result = subprocess.run([py, "-c", script], cwd=ws_root,
                                     capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            try:
                if exc.process:
                    exc.process.kill()
                    exc.process.communicate(timeout=2)
            except Exception:
                pass
            return {"status": "failed", "error": "timeout"}
        if "@@@ERROR@@@" in result.stdout:
            return {"status": "failed",
                     "error": result.stdout.split("@@@ERROR@@@", 1)[1].strip()[-500:]}
        if "@@@OK@@@" not in result.stdout:
            return {"status": "failed",
                     "error": "runner returned unexpected output"}
        return {"status": "completed"}

    # The viz-class lookup + per-viz Composite.run happen in the env worker
    # (live viz classes + core, kept out of the HTTP process). `viz_render_hooks`
    # gives run_investigation the inputs-by-class map + a worker-backed build_and_run.
    from vivarium_workbench.lib.viz_render import viz_render_hooks
    inputs_by_class, build_and_run = viz_render_hooks(ws_root)

    # Run the orchestration inline (the live handler wraps this in
    # ``_active_branch_action``; here the commit is DEFERRED and we just return
    # the summary).
    try:
        _steps_override = body.get("steps")
        summary = run_investigation(
            ws_root, name,
            run_one_composite=run_one_composite,
            inputs_by_class=inputs_by_class,
            build_and_run=build_and_run,
            steps_override=(int(_steps_override) if _steps_override else None),
        )
    except InvestigationSpecError as e:
        summary = {"error": f"spec error: {e}"}
    except FileNotFoundError as e:
        summary = {"error": str(e)}
    # The original handler routes ANY ``"error"``-keyed summary through the same
    # 400/404 dispatch — this covers both the exception cases above AND a
    # non-raising error ``run_investigation`` can RETURN (e.g. the concurrent
    # run-lock guard ``{"error": "investigation is already running", ...}``,
    # which must surface as 404, not a 200 with the raw summary).
    if isinstance(summary, dict) and "error" in summary:
        err = summary["error"]
        return {"error": err}, 400 if "spec error" in err else 404
    return summary, 200
