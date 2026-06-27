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

from vivarium_dashboard.lib import core_builder
from vivarium_dashboard.lib import study_crud_mutations
from vivarium_dashboard.lib.json_serialize import _json_default


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
    from vivarium_dashboard.lib.investigations import (
        run_investigation, InvestigationSpecError,
    )
    from vivarium_dashboard.lib.composite_lookup import (
        substitute_parameters, find_composite_path,
    )
    from vivarium_dashboard.lib import composite_runs as cr

    name = study_crud_mutations._study_name_from_body(body)
    if not name:
        return {"error": "name is required"}, 400

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

    # Build the visualization registry. We need the workspace's core for
    # the Visualization class lookup; import the workspace package and
    # build a fresh core here (in-process, no subprocess needed).
    try:
        core, registry = core_builder.build_viz_registry(ws_root, pkg)
    except Exception as e:
        return {"error": f"failed to build core: {e}"}, 500

    def build_and_run(viz_doc, registry_arg):
        """Production hook: build a Composite from viz_doc, run 1 step,
        return the output_store's html string.
        """
        from process_bigraph import Composite
        composite = Composite({'state': viz_doc}, core=core)
        composite.run(1)
        state = composite.state
        html = state.get('output_store')
        if isinstance(html, dict):
            html = html.get('value') or html.get('_value') or ''
        return html if isinstance(html, str) else ''

    # Run the orchestration inline (the live handler wraps this in
    # ``_active_branch_action``; here the commit is DEFERRED and we just return
    # the summary).
    try:
        summary = run_investigation(
            ws_root, name,
            run_one_composite=run_one_composite,
            core_registry=registry,
            build_and_run=build_and_run,
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
