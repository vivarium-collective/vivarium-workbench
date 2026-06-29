"""Pure builder for the ``POST /api/investigation-run-one`` route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_investigation_run_one``.  The handler powers the
"Duplicate run" flow: it takes an existing run's params (tweaked in a modal),
resolves the investigation's baseline composite (v2 ``variants[]`` sidecar OR a
legacy top-level ``composite`` registry lookup), substitutes parameters, injects
a SQLiteEmitter, saves a ``runs_meta`` row, runs the composite once in an
EMBEDDED ``python -c`` subprocess, parses the ``@@@RESULTS@@@``/``@@@ERROR@@@``
markers, persists each rendered viz HTML under
``<inv>/viz/<run_id>/<safe>.html``, and appends to the investigation's
``runs.db``.

The builder returns ``(body, status)`` so the FastAPI route wraps every path in
``JSONResponse``.  Only validation failures are 400 / 404; a composite that
FAILS to run still returns **200** with ``{"ok": False, ...}`` (the run row is
marked ``failed``).  No ``import server`` here.

The workspace root is threaded explicitly as ``ws_root`` (replacing the server
``WORKSPACE`` global) so the module stays importable standalone and flip-ready.
``subprocess`` is referenced at module level so tests monkeypatch
``investigation_run_one_views.subprocess.run`` and never run a real composite.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

from vivarium_dashboard.lib import study_crud_mutations
from vivarium_dashboard.lib import study_spec
from vivarium_dashboard.lib.json_serialize import _json_default


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


def investigation_run_one(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Run a single ad-hoc composite execution. Returns ``(response_dict, code)``.

    Behaviour-preserving port of ``_post_investigation_run_one`` (body
    ``{investigation|study|name, sim_name?, overrides?, steps?, label?}``):

      * missing investigation         → ``({"error": "investigation required"}, 400)``
      * spec.yaml missing             → ``({"error": "spec.yaml not found"}, 404)``
      * InvestigationSpecError        → ``({"error": str(e)}, 400)``
      * v2 baseline variant missing   → ``({"error": "baseline variant not
        found: …"}, 404)``
      * v2 sidecar missing            → ``({"error": "composite sidecar not
        found: …"}, 404)``
      * legacy composite not found    → ``({"error": "composite not found: …"}, 404)``
      * neither variants nor composite → ``({"error": "spec has neither
        'variants' (v2) nor 'composite' (legacy)"}, 400)``
      * run SUCCEEDED                 → ``({"ok": True, "run_id", "investigation",
        "sim_name", "viz_html"}, 200)``
      * run FAILED                    → ``({"ok": False, "run_id", "error"}, 200)``
    """
    _ws_add_to_sys_path(ws_root)
    from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError
    from vivarium_dashboard.lib.composite_lookup import substitute_parameters, find_composite_path
    from vivarium_dashboard.lib import composite_runs as cr

    inv = study_crud_mutations._study_name_from_body(body)
    sim_name = (body.get("sim_name") or "").strip() or "ad-hoc"
    overrides = body.get("overrides") or {}
    steps = int(body.get("steps") or 10)
    if not inv:
        return {"error": "investigation required"}, 400

    spec_path = study_spec.study_spec_path(ws_root, inv)
    if not spec_path.is_file():
        return {"error": "spec.yaml not found"}, 404
    try:
        spec = load_spec(spec_path)
    except InvestigationSpecError as e:
        return {"error": str(e)}, 400

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

    # Resolve which composite to run. v2 studies have `baseline` + `variants[]`
    # with each variant carrying a `document: ./composites/<name>.yaml`
    # sidecar that is the single source of truth (already merged + frozen
    # at create time). Legacy specs use a single top-level `composite` key
    # and resolve via the workspace registry.
    composite_name = None
    composite_doc = None  # raw {state, parameters, ...} dict OR a flat state dict
    inv_dir = study_spec.study_dir(ws_root, inv)
    if "variants" in spec:
        # v2 study shape: prefer baseline; if absent, the first declared variant.
        variants = spec.get("variants") or []
        baseline_name = spec.get("baseline") or (variants[0].get("name") if variants else None)
        variant_entry = None
        for v in variants:
            if v.get("name") == baseline_name:
                variant_entry = v
                break
        if variant_entry is None:
            return {"error": f"baseline variant not found: {baseline_name!r}"}, 404
        composite_name = variant_entry.get("name") or baseline_name
        sidecar_rel = variant_entry.get("document") or f"./composites/{composite_name}.yaml"
        sidecar_path = (inv_dir / sidecar_rel).resolve()
        if not sidecar_path.is_file():
            return {"error": f"composite sidecar not found: {sidecar_path}"}, 404
        text = sidecar_path.read_text(encoding="utf-8")
        composite_doc = (json.loads(text) if sidecar_path.suffix.lower() == ".json"
                          else yaml.safe_load(text)) or {}
    elif spec.get("composite"):
        # Legacy single-composite shape: resolve via workspace registry.
        composite_name = spec["composite"]
        path = find_composite_path(ws_root, pkg, composite_name)
        if path is None:
            return {"error": f"composite not found: {composite_name}"}, 404
        text = path.read_text(encoding="utf-8")
        composite_doc = (json.loads(text) if path.suffix.lower() == ".json"
                          else yaml.safe_load(text)) or {}
    else:
        return (
            {"error": "spec has neither 'variants' (v2) nor 'composite' (legacy)"},
            400,
        )

    # Two sidecar shapes coexist in the wild:
    #   1. `{state: {...}, parameters: {...}}`  — file-spec composites
    #   2. `{...}`  — flat state dict from @composite_generator outputs
    # composite-test-run handles both (see line ~4775); mirror that here.
    if isinstance(composite_doc, dict) and "state" in composite_doc \
            and isinstance(composite_doc["state"], dict):
        state = substitute_parameters(composite_doc.get("state") or {},
                                       composite_doc.get("parameters") or {},
                                       overrides)
    else:
        # Flat state dict: no parameter substitution layer to apply,
        # overrides are best-effort applied at the top level only.
        state = dict(composite_doc) if isinstance(composite_doc, dict) else {}
        for k, v in (overrides or {}).items():
            if k in state:
                state[k] = v
    db_file = str(study_spec.study_dir(ws_root, inv) / "runs.db")
    run_id = cr.generate_run_id(composite_name, overrides)
    state = cr.inject_sqlite_emitter(state, run_id=run_id, db_file=db_file)

    # Ensure the DB exists + the runs_meta table has sim_name column
    import sqlite3 as _sql
    conn = cr.connect(db_file)
    try:
        conn.execute("ALTER TABLE runs_meta ADD COLUMN sim_name TEXT")
        conn.commit()
    except _sql.OperationalError:
        pass

    label = body.get("label") or f"ad-hoc {sim_name}"
    import time as _time
    cr.save_metadata(conn, spec_id=composite_name, run_id=run_id,
                      params=overrides, label=label, started_at=_time.time(),
                      n_steps=steps)
    conn.execute("UPDATE runs_meta SET sim_name=? WHERE run_id=?", (sim_name, run_id))
    conn.commit()
    conn.close()

    py = sys.executable
    script = textwrap.dedent(f"""
        import json, sys, traceback
        try:
            from {pkg}.core import build_core
            from process_bigraph import Composite
            try:
                from pbg_emitters.sqlite_emitter import SQLiteEmitter
            except ImportError:  # process-bigraph < 1.4.17 (legacy location)
                from process_bigraph.emitter import SQLiteEmitter
            core = build_core()
            core.register_link('SQLiteEmitter', SQLiteEmitter)
            composite = Composite({{'state': __import__('json').loads({json.dumps(json.dumps(state, default=_json_default))})}}, core=core)
            composite.run({steps})
            # Gather rendered viz HTML, if pbg_superpowers is importable.
            viz_html = {{}}
            try:
                from pbg_superpowers.visualization import render_results
                rendered = render_results(composite)
                for path_tuple, payload in rendered.items():
                    key = '.'.join(str(p) for p in path_tuple)
                    viz_html[key] = payload
            except Exception:
                viz_html = {{}}
            print('@@@RESULTS@@@')
            print(json.dumps({{'viz_html': viz_html}}, default=str))
        except Exception:
            print('@@@ERROR@@@')
            print(traceback.format_exc())
    """)
    result = subprocess.run([py, "-c", script], cwd=ws_root,
                             capture_output=True, text=True, timeout=300)
    conn = cr.connect(db_file)
    try:
        if "@@@RESULTS@@@" in result.stdout:
            # Parse the viz_html block. Persist each viz's html to disk at
            # investigations/<inv>/viz/<run_id>/<viz_path_safe>.html so the
            # dashboard's static-file handler can serve it.
            viz_html_resp = {}
            try:
                payload = json.loads(
                    result.stdout.split("@@@RESULTS@@@", 1)[1].strip()
                )
                viz_html = payload.get("viz_html") or {}
            except (IndexError, json.JSONDecodeError):
                viz_html = {}

            if viz_html:
                viz_dir = inv_dir / "viz" / run_id
                viz_dir.mkdir(parents=True, exist_ok=True)
                for viz_key, viz_payload in viz_html.items():
                    # Sanitise key for filesystem use: replace any '.', '/', or
                    # other separators with '_'.
                    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", viz_key).strip("_") or "viz"
                    html_str = ""
                    if isinstance(viz_payload, dict):
                        html_str = viz_payload.get("html") or ""
                    elif isinstance(viz_payload, str):
                        html_str = viz_payload
                    out_path = viz_dir / f"{safe}.html"
                    try:
                        out_path.write_text(html_str if isinstance(html_str, str) else str(html_str))
                        rel_path = out_path.relative_to(ws_root)
                        viz_html_resp[safe] = {
                            "html": html_str if isinstance(html_str, str) else "",
                            "path": str(rel_path),
                        }
                    except OSError:
                        # Best-effort persistence; still include the HTML inline.
                        viz_html_resp[safe] = {
                            "html": html_str if isinstance(html_str, str) else "",
                            "path": "",
                        }

            cr.complete_metadata(conn, run_id=run_id, n_steps=steps, status="completed")
            return {"ok": True, "run_id": run_id,
                    "investigation": inv, "sim_name": sim_name,
                    "viz_html": viz_html_resp}, 200
        else:
            cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
            err = result.stdout.split("@@@ERROR@@@", 1)[-1].strip()[-500:] \
                  if "@@@ERROR@@@" in result.stdout else "unknown error"
            return {"ok": False, "run_id": run_id, "error": err}, 200
    finally:
        conn.close()
