"""Subprocess-based composite/ensemble run engine extracted from server.py.

These are the ``ws_root``-parameterized public builders for the
simulation-execution cluster (study-run engine extraction, phase E1). The
legacy server.py module-level helpers (``_run_composite_subprocess``,
``_invoke_v2ecoli_workflow``, ``_strip_process_instances``) now delegate to the
corresponding functions here via thin name-shims, keeping their existing
call-sites + test imports intact and the live path byte-identical.

All simulation execution stays subprocess-based: ``run_composite_subprocess``
spawns ``python -c <script>`` (the script ``__import__``s the workspace
``pkg.core`` in a child process); ``invoke_v2ecoli_workflow`` spawns the
v2ecoli workflow console script. This module therefore does NOT import or run
``process_bigraph.Composite`` in-process — it orchestrates and shells out,
which keeps it importable standalone and flip-ready.

Functions
---------
run_composite_subprocess  → study-run / composite-test-run engine
invoke_v2ecoli_workflow   → delegated-ensemble (v2ecoli-workflow) engine
strip_process_instances   → pure state-tree helper (leaf dep of the runner)
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import yaml


def strip_process_instances(state):
    """Strip live Process/Step instances from a state tree before JSON encoding.

    Composite generators (e.g. v2ecoli's ``make_edge``) attach the live
    Python ``instance`` to the state dict alongside the serialisable
    ``address`` + ``config``. The instance can't cross a JSON boundary;
    address+config is sufficient for the child subprocess to rebuild the
    composite via ``Composite()`` + ``core.register_link``. The
    ``_inputs``/``_outputs`` schema sidecars are also dropped here — they
    come from ``instance.inputs()``/``outputs()`` and will be rederived by
    the child when it instantiates the class.

    Walks dicts and lists; leaves non-container leaves untouched. Returns a
    new tree (does not mutate the input).
    """
    if isinstance(state, dict):
        out = {}
        is_edge = state.get('_type') in ('step', 'process')
        for k, v in state.items():
            if is_edge and k in ('instance', '_inputs', '_outputs'):
                continue
            out[k] = strip_process_instances(v)
        return out
    if isinstance(state, list):
        return [strip_process_instances(v) for v in state]
    return state


def invoke_v2ecoli_workflow(cfg_path, out_dir, ws_root, timeout_s):
    """Run ``v2ecoli-workflow`` once for a delegated ensemble (SP2a).

    Mirrors :func:`run_composite_subprocess`'s timeout/return contract: runs
    ``<ws>/.venv/bin/v2ecoli-workflow --config <cfg> --out <out_dir>`` in a
    subprocess and returns ``(response_dict, status_code)``. The workflow packs
    every sweep/seed point into ONE parquet hive store under
    ``<out_dir>/parquet/…``; the caller's existing post-run ``study_outcomes.sync``
    records that one dir as a single ensemble run (no dashboard change needed).

    Does NOT touch ``run_composite_subprocess`` — this is the ensemble sibling.
    """
    ws = Path(ws_root)
    out_dir = Path(out_dir)
    run_id = out_dir.name
    exe = ws / ".venv" / "bin" / "v2ecoli-workflow"
    cmd = [str(exe), "--config", str(cfg_path), "--out", str(out_dir)]
    try:
        result = subprocess.run(cmd, cwd=str(ws), capture_output=True,
                                text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return ({"simulation_id": run_id, "error": "ensemble run timed out"}, 504)
    except FileNotFoundError:
        # Defensive: delegation_available() should have gated this, but a venv
        # missing the console script must never raise uncaught (review FIX 2).
        return ({"simulation_id": run_id,
                 "error": "v2ecoli-workflow not found in the workspace venv"}, 502)
    if result.returncode != 0:
        return ({"simulation_id": run_id, "error": "ensemble run failed",
                 "stdout": result.stdout, "stderr": result.stderr}, 502)
    return ({"simulation_id": run_id, "ensemble": True,
             "out_dir": str(out_dir), "steps": 0}, 200)


def run_composite_subprocess(ws_root, *, pkg, state, steps, db_file, run_id, spec_id,
                             label, overrides=None, sim_name=None, timeout=1800,
                             emit_paths=None, study_emitter=None,
                             study_max_generations=None,
                             study_single_daughters=None):
    """Run a resolved composite ``state`` for ``steps`` steps in a subprocess,
    persisting runs_meta + history (via an injected SQLiteEmitter) to
    ``db_file``.

    Shared by ``_post_composite_test_run`` (scratchpad db) and the study-run
    handlers (per-Study db). Does NOT clear prior rows — callers decide.

    Returns ``(response_dict, status_code)``.  ``response_dict`` always has
    ``"simulation_id"``; on success also ``"results"``, ``"viz_html"``,
    ``"steps"``.
    """
    from vivarium_dashboard.lib import composite_runs as cr

    # Are we running a registered @composite_generator? If so, the child can
    # rebuild the composite in its own process from (spec_id, overrides) —
    # no state serialization needed. This avoids the live-Process-instance
    # problem in shared partitioned-process pools (v2ecoli) and the pint
    # Quantity infinite-recursion problem in repr() that JSON-encoding the
    # parent-built state used to hit. Non-generator callers (file-based
    # composites) keep the old state-serialization path below.
    use_generator_path = False
    try:
        from pbg_superpowers.composite_generator import _REGISTRY, discover_generators
        if not _REGISTRY:
            discover_generators()
        use_generator_path = spec_id in _REGISTRY
    except ImportError:
        pass

    py = sys.executable
    import tempfile as _tempfile
    from bigraph_schema.json_codec import BigraphJSONEncoder

    if use_generator_path:
        # Pass (spec_id, overrides) as small JSON; the child builds + injects
        # the SQLiteEmitter + runs entirely in-process.
        _state_path = None
        # Read workspace-level runtime defaults: emitter selection + multi-gen cap.
        # Workspaces (e.g. v2ecoli) can opt into XArrayEmitter via
        # `runtime: { default_emitter: xarray, max_generations: N }`. Default
        # is SQLite (the dashboard's historical single-generation behaviour).
        # Per-study override (``study_emitter``) wins over the workspace
        # default — set it from the study yaml's ``runtime.emitter`` so a
        # single workspace can mix emitters by study (e.g. xarray for
        # many-sims aggregation studies, sqlite for ones needing unstructured
        # state like unique-molecule snapshots for chromosome viz).
        try:
            _ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
            _runtime = (_ws_data.get("runtime") or {}) if isinstance(_ws_data, dict) else {}
            _default_emitter = str(_runtime.get("default_emitter") or "sqlite").lower()
            _max_generations = int(_runtime.get("max_generations") or 3)
            _single_daughters = bool(_runtime.get("single_daughters") or False)
        except Exception:
            _default_emitter = "sqlite"
            _max_generations = 3
            _single_daughters = False
        if study_emitter:
            _default_emitter = str(study_emitter).lower()
        # Per-study overrides win over workspace defaults.
        if study_max_generations is not None:
            _max_generations = int(study_max_generations)
        if study_single_daughters is not None:
            _single_daughters = bool(study_single_daughters)
        # Derive a zarr store path alongside the SQLite db_file (one per run).
        _zarr_store = str(Path(db_file).with_suffix("")) + f".{run_id}.zarr"
        payload = {
            "spec_id": spec_id,
            "overrides": overrides or {},
            "run_id": run_id,
            "db_file": db_file,
            "steps": steps,
            # v2ecoli friction #14: thread the study's declared observables
            # to the child so it can populate the user_emitter schema BEFORE
            # injecting SQLiteEmitter. Without this, history.state rows are
            # just `{"_tick": <global_time>}` and every comparative viz
            # renders empty. Empty list = legacy "no observables" behavior.
            "emit_paths": list(emit_paths or []),
            # XArray opt-in (per workspace.yaml runtime.default_emitter).
            "default_emitter": _default_emitter,
            "max_generations": _max_generations,
            "single_daughters": _single_daughters,
            "zarr_store": _zarr_store,
        }
        script = textwrap.dedent(f"""
            import json, sys, traceback
            try:
                from {pkg}.core import build_core
                from process_bigraph import Composite, gather_emitter_results
                try:
                    from pbg_emitters.sqlite_emitter import SQLiteEmitter
                except ImportError:  # process-bigraph < 1.4.17 (legacy location)
                    from process_bigraph.emitter import SQLiteEmitter
                from pbg_superpowers.composite_generator import (
                    _REGISTRY, build_generator, discover_generators,
                    apply_core_extensions,
                )
                from vivarium_dashboard.lib import composite_runs as cr
                from bigraph_schema.json_codec import BigraphJSONEncoder as _BJE
                _payload = {payload!r}
                if not _REGISTRY: discover_generators()
                entry = _REGISTRY[_payload['spec_id']]
                core = build_core()
                core.register_link('SQLiteEmitter', SQLiteEmitter)
                # v2ecoli friction #16: register types/processes the composite
                # needs from packages build_core() doesn't know about (declared
                # via @composite_generator(core_extensions=[...])).
                core = apply_core_extensions(entry, core)
                doc = build_generator(entry, overrides=_payload['overrides'])
                state = doc.get('state', doc) if isinstance(doc, dict) else doc
                if _payload.get('emit_paths'):
                    state = cr.inject_emitter_for_declared_paths(state, _payload['emit_paths'])
                _use_xarray = _payload.get('default_emitter') == 'xarray'
                _view = []
                if _use_xarray:
                    # Auto-view from the study's declared observables. v0 of
                    # view_from_emit_paths is scalar-only — vector observables
                    # (monomer_counts, fork_coordinates, RNAP_coordinates, …)
                    # are skipped. If a study declares ONLY vector observables
                    # (e.g. dnaa-01 emits only listeners.monomer_counts), the
                    # auto-view is empty and the XArrayEmitter constructor
                    # would crash. In that case, fall back to SQLite for this
                    # run so the study isn't blocked.
                    from v2ecoli.library.xarray_run import (
                        run_multigen_xarray, view_from_emit_paths,
                    )
                    _view = view_from_emit_paths(_payload.get('emit_paths') or [])
                    if not _view:
                        print('[xarray-run] auto-view is empty (all declared '
                              'observables are vector / non-listeners-rooted); '
                              'falling back to SQLite emitter for this run.',
                              file=sys.stderr)
                        _use_xarray = False
                if _use_xarray:
                    # XArray multi-gen path: drive the composite externally past
                    # divisions, per-generation emitter swap; results land in a
                    # partitioned zarr store. See v2ecoli plan
                    # 2026-05-12-migrate-emitters.md task 7.x.
                    composite = Composite({{'state': state}}, core=core)
                    _md = {{
                        'experiment_id': _payload['run_id'],
                        'variant': 0,
                        'lineage_seed': 0,
                        'time_step': 1.0,
                        'max_duration': float(_payload['steps']),
                    }}
                    _xarr = run_multigen_xarray(
                        composite,
                        store_path=_payload['zarr_store'],
                        view=_view,
                        metadata_base=_md,
                        max_steps=_payload['steps'],
                        max_generations=_payload['max_generations'],
                    )
                    results = {{'zarr_store': _xarr['store'],
                               'generations': _xarr['generations'],
                               'steps': _xarr['steps']}}
                else:
                    _mg = int(_payload.get('max_generations') or 1)
                    if _mg > 1:
                        # Multi-gen: workspace-side runner drives the
                        # SQLiteEmitter externally (mirrors how the
                        # xarray branch drives XArrayEmitter). The
                        # composite does NOT get an injected emitter —
                        # the static `agents/0/...` wiring would write
                        # empty rows after division. The runner extracts
                        # the followed agent's state each chunk and
                        # calls `emitter.update` with it; on division it
                        # switches to the daughter agent_id.
                        composite = Composite({{'state': state}}, core=core)
                        from v2ecoli.library.sqlite_run import run_multigen_sqlite
                        _sq = run_multigen_sqlite(
                            composite,
                            run_id=_payload['run_id'],
                            db_file=_payload['db_file'],
                            emit_paths=_payload.get('emit_paths') or [],
                            max_steps=_payload['steps'],
                            max_generations=_mg,
                            single_daughters=bool(_payload.get('single_daughters')),
                            core=core,
                        )
                        results = {{'steps': _sq['steps'],
                                   'generations': _sq['generations']}}
                    else:
                        state = cr.inject_sqlite_emitter(
                            state, run_id=_payload['run_id'], db_file=_payload['db_file'])
                        composite = Composite({{'state': state}}, core=core)
                        cr.run_with_division(composite, _payload['steps'])
                        results = gather_emitter_results(composite)
        """).lstrip("\n")
    else:
        # Legacy path: serialize the pre-built state into a tempfile.
        # v2ecoli friction #14: parent-side injection works here because
        # the serialized state IS what the subprocess reconstructs.
        if emit_paths:
            state = cr.inject_emitter_for_paths(state, list(emit_paths))
        state = cr.inject_sqlite_emitter(state, run_id=run_id, db_file=db_file)
        state = strip_process_instances(state)
        _state_fd, _state_path = _tempfile.mkstemp(suffix=".state.json", prefix="vivarium-run-")
        try:
            with os.fdopen(_state_fd, "w") as _f:
                json.dump(state, _f, cls=BigraphJSONEncoder)
        except Exception:
            try: os.unlink(_state_path)
            except OSError: pass
            raise

        script = textwrap.dedent(f"""
            import json, sys, traceback
            try:
                from {pkg}.core import build_core
                from process_bigraph import Composite, gather_emitter_results
                try:
                    from pbg_emitters.sqlite_emitter import SQLiteEmitter
                except ImportError:  # process-bigraph < 1.4.17 (legacy location)
                    from process_bigraph.emitter import SQLiteEmitter
                from bigraph_schema.json_codec import bigraph_json_hook
                from vivarium_dashboard.lib import composite_runs as cr
                core = build_core()
                core.register_link('SQLiteEmitter', SQLiteEmitter)
                with open({_state_path!r}) as _sf:
                    _state = json.load(_sf, object_hook=bigraph_json_hook)
                composite = Composite({{'state': _state}}, core=core)
                cr.run_with_division(composite, {steps})
                results = gather_emitter_results(composite)
        """).lstrip("\n")

    # Shared tail: gather results + viz HTML + emit @@@RESULTS@@@ block.
    script += textwrap.dedent(f"""
            # Flatten tuple keys to JSON-friendly dotted strings
            out = {{}}
            for path_tuple, entries in results.items():
                key = '.'.join(str(p) for p in path_tuple)
                out[key] = entries
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
            from bigraph_schema.json_codec import BigraphJSONEncoder as _BJE
            print('@@@RESULTS@@@')
            print(json.dumps({{'results': out, 'viz_html': viz_html}}, cls=_BJE))
        except Exception as e:
            print('@@@ERROR@@@')
            print(traceback.format_exc())
    """)

    # Coordinated-generation stamp (expert-feedback A.2): tag this run with the
    # workspace's current generation so the report can flag panels from an
    # older generation as stale. No-op (None) when no generation is active.
    _generation_id = None
    try:
        from pbg_superpowers import generation as _gen
        _generation_id = _gen.current_generation_id(ws_root)
    except Exception:  # noqa: BLE001 — generation is advisory, never fatal
        _generation_id = None

    conn = cr.connect(db_file)
    try:
        try:
            cr.save_metadata(conn, spec_id=spec_id, run_id=run_id,
                             params=overrides, label=label,
                             started_at=time.time(), n_steps=steps,
                             generation_id=_generation_id)
            if sim_name is not None:
                conn.execute("UPDATE runs_meta SET sim_name=? WHERE run_id=?",
                             (sim_name, run_id))
                conn.commit()
        except sqlite3.IntegrityError:
            return ({"simulation_id": run_id,
                     "error": "duplicate run_id (rare timing collision) — retry"}, 500)
        if _generation_id is not None:
            try:
                _gen.record_run(ws_root, _generation_id,
                                study=(sim_name or label or spec_id),
                                run_id=run_id, sim_name=sim_name)
            except Exception:  # noqa: BLE001 — manifest index is best-effort
                pass

        # v2ecoli friction #10: persist the rendered script alongside runs.db
        # so "what did the dashboard actually run for this run_id?" is one cat.
        # Best-effort; never fail a run because the sidecar couldn't be written.
        try:
            _db_dir = os.path.dirname(os.path.abspath(db_file))
            _sims_dir = os.path.join(_db_dir, "sims")
            os.makedirs(_sims_dir, exist_ok=True)
            with open(os.path.join(_sims_dir, f"{run_id}.subprocess.py"), "w") as _f:
                _f.write(script)
        except OSError:
            pass

        try:
            try:
                result = subprocess.run([py, "-c", script], cwd=ws_root,
                                        capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                try:
                    if exc.process is not None:
                        exc.process.kill()
                        exc.process.communicate(timeout=2)
                except Exception:
                    pass
                cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
                return ({"simulation_id": run_id, "error": "run timed out"}, 504)
        finally:
            if _state_path is not None:
                try: os.unlink(_state_path)
                except OSError: pass

        out = result.stdout
        if "@@@ERROR@@@" in out:
            cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
            tb = out.split("@@@ERROR@@@", 1)[1].strip()
            return ({"simulation_id": run_id, "error": "run failed",
                     "traceback": tb}, 502)

        try:
            from bigraph_schema.json_codec import bigraph_json_hook
            payload = json.loads(
                out.split("@@@RESULTS@@@", 1)[1].strip(),
                object_hook=bigraph_json_hook,
            )
        except (IndexError, json.JSONDecodeError):
            cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="failed")
            return ({"simulation_id": run_id,
                     "error": "could not parse run output",
                     "stdout": out, "stderr": result.stderr}, 502)

        # Subprocess emits {results, viz_html}; older versions emitted the
        # results dict directly. Handle both for forward/backward compat.
        if isinstance(payload, dict) and "results" in payload:
            results = payload.get("results") or {}
            viz_html = payload.get("viz_html") or {}
        else:
            results = payload
            viz_html = {}

        cr.complete_metadata(conn, run_id=run_id, n_steps=steps, status="completed")
        return ({"simulation_id": run_id, "results": results,
                 "viz_html": viz_html, "steps": steps}, 200)
    finally:
        conn.close()
