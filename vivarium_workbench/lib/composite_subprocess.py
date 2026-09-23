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
import textwrap
import time
from pathlib import Path

from vivarium_workbench.lib import run_store

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
        is_edge = state.get("_type") in ("step", "process")
        for k, v in state.items():
            if is_edge and k in ("instance", "_inputs", "_outputs"):
                continue
            out[k] = strip_process_instances(v)
        return out
    if isinstance(state, list):
        return [strip_process_instances(v) for v in state]
    return state


def inject_run_emitters(
    state: dict,
    *,
    spec_id: str,
    run_id: str,
    emit_paths: list[str] | None,
    workspace,
    db_file,
) -> dict:
    """Inject the live RAM emitter (from the UI's explicit-path selection)
    AND, when the composite declares one, its default Parquet emitter for
    persistence.

    The RAM ``user_emitter`` always goes in — it feeds the Composite
    Explorer's live Results view regardless of how the run is persisted.
    The declared emitter (Task 4's ``inject_declared_emitter``) is
    ADDITIVE: when a composite declares one (e.g. the v2ecoli baseline's
    Parquet emitter), it is injected alongside the RAM emitter and its
    kind (``"parquet"``) is what gets recorded for the run. When nothing
    is declared, falls back to the legacy ``inject_sqlite_emitter`` path
    so undeclared composites keep working exactly as before.

    Returns ``{"state": <mutated state>, "emitter": <kind str>}``.
    """
    from vivarium_workbench.lib import composite_runs as cr

    state = cr.inject_emitter_for_paths(state, list(emit_paths or []))
    out_dir = Path(workspace) / ".pbg" / "parquet-runs"
    state, declared_kind = cr.inject_declared_emitter(
        state, spec_id=spec_id, run_id=run_id, out_dir=out_dir
    )
    if declared_kind is None:
        # Legacy path: no declared emitter — persist via SQLite as before,
        # using the caller's db_file (e.g. <study_dir>/runs.db), NOT a
        # fresh workspace-scratchpad path — the Study Runs tab reads
        # history back out of the caller's db_file.
        state = cr.inject_sqlite_emitter(state, run_id=run_id, db_file=db_file)
        return {"state": state, "emitter": "sqlite"}
    return {"state": state, "emitter": declared_kind}


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
        result = subprocess.run(
            cmd, cwd=str(ws), capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return ({"simulation_id": run_id, "error": "ensemble run timed out"}, 504)
    except FileNotFoundError:
        # Defensive: delegation_available() should have gated this, but a venv
        # missing the console script must never raise uncaught (review FIX 2).
        return (
            {
                "simulation_id": run_id,
                "error": "v2ecoli-workflow not found in the workspace venv",
            },
            502,
        )
    if result.returncode != 0:
        return (
            {
                "simulation_id": run_id,
                "error": "ensemble run failed",
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            502,
        )
    return (
        {
            "simulation_id": run_id,
            "ensemble": True,
            "out_dir": str(out_dir),
            "steps": 0,
        },
        200,
    )


def run_index_slugs_from_db_path(db_file) -> tuple[str | None, str | None]:
    """(study_slug, investigation_slug) for a run's ``runs.db`` path.

    A study run's db is ``.../studies/<slug>/runs.db`` (root layout) or
    ``.../investigations/<inv>/studies/<slug>/runs.db`` (nested). Used to tag
    the run's ``.pbg/runs.jsonl`` index record at write time so the per-study
    view can find it without a study.yaml cross-reference backfill. Returns
    ``(None, None)`` for a non-study db (e.g. ``.pbg/composite-runs.db``).
    """
    from vivarium_workbench.lib.simulations_index import _study_slug_from_db_path

    study_slug = _study_slug_from_db_path(str(db_file))
    inv_slug = None
    parts = str(db_file).replace("\\", "/").split("/")
    if "investigations" in parts:
        i = len(parts) - 1 - parts[::-1].index("investigations")
        if i + 1 < len(parts):
            inv_slug = parts[i + 1]
    return study_slug, inv_slug


def run_composite_subprocess(
    ws_root,
    *,
    pkg,
    state,
    steps,
    db_file,
    run_id,
    spec_id,
    label,
    overrides=None,
    sim_name=None,
    timeout=1800,
    emit_paths=None,
    study_emitter=None,
    study_max_generations=None,
    study_single_daughters=None,
    manifest=None,
    reran_from=None,
):
    """Run a resolved composite ``state`` for ``steps`` steps in a subprocess,
    persisting runs_meta + history (via an injected SQLiteEmitter) to
    ``db_file``.

    Shared by ``_post_composite_test_run`` (scratchpad db) and the study-run
    handlers (per-Study db). Does NOT clear prior rows — callers decide.

    ``manifest`` (optional) is the complete per-run replay manifest (Part A,
    ``composite_runs.build_run_manifest``) built by the caller (e.g.
    ``study_runs.launch_into_study``); threaded straight through to
    ``cr.save_metadata`` so it lands in the run's ``runs_meta.manifest_json``
    row for ``rerun.resolve_rerun_target`` to replay later.

    ``reran_from`` (reproducible-rerun-spine Task 4) is the ORIGINAL run_id
    this run reproduces, when ``study_runs.launch_into_study`` was called by
    ``rerun.run_rerun``. This is the STUDY-origin completion path (unlike
    ``run_runner.execute``, which only handles composite-origin runs and
    already got its own ``reran_from``/``verify_reproduction`` wiring in
    Task 3) — study-origin reruns are the dominant path (baseline/variant/
    study-reproduce), so without this wiring here a study reproduce would
    never actually verify anything. Once this run's own
    ``result_fingerprint`` is computed + stored below, a present
    ``reran_from`` triggers ``rerun.verify_reproduction(ws_root, reran_from,
    run_id)`` — best-effort, mirroring ``run_runner.execute``'s tail: any
    failure there is swallowed so verification never fails an otherwise-
    successful run.

    Returns ``(response_dict, status_code)``.  ``response_dict`` always has
    ``"simulation_id"``; on success also ``"results"``, ``"viz_html"``,
    ``"steps"``.
    """
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    # Both script templates below open with `from {pkg}.core import build_core`
    # and then call `build_core()` -- the workspace package's core is what
    # registers its processes and types, so there is no running a composite
    # without it.
    #
    # `pkg` is allowed to be None upstream, deliberately: study_runs degrades it
    # to None so a workspace.yaml-less caller (a hermetic test, an early
    # rerun-replay context) "must never block the launch". That is right for
    # callers that never reach a subprocess. It is not right here, and nothing
    # checked -- so None was interpolated into the source and the child died on
    #
    #     File "<string>", line 3
    #         from None.core import build_core
    #     SyntaxError: invalid syntax
    #
    # which reached the caller as "could not parse run output". Observed on a
    # real deployment: /app/v2ecoli/workspace has studies/ and investigations/
    # but no workspace.yaml, so every study run there failed this way and said
    # nothing about why.
    #
    # Refuse at the seam that knows, with the fact the caller needs: which
    # workspace, and what is missing from it.
    if not pkg:
        return (
            {
                "simulation_id": run_id,
                "error": (
                    f"workspace {ws_root} has no package: its workspace.yaml is "
                    "missing, or declares neither `package_path` nor `name`. A "
                    "composite run imports `<package>.core.build_core`, so it "
                    "cannot proceed without one."
                ),
            },
            400,
        )

    # Emitter kind actually persisted for this run — recorded on runs_meta /
    # the JSONL run log below. Only the legacy (non-generator) path resolves
    # this synchronously here (Task 5); the generator path's emitter choice
    # is resolved inside the child script (default_emitter / xarray / sqlite)
    # and isn't known back in the parent process, so it stays unrecorded for
    # now.
    run_emitter_kind = None

    # reproducible-rerun-spine Task 3 (G4), fix round 1: this is the STUDY-
    # origin completion path (study_runs._launch_run_and_flush ->
    # run_composite_subprocess) — separate from, and previously not covered
    # by, run_runner.execute's result_fingerprint wiring (that one only runs
    # for composite-origin runs). Resolve fingerprint_fields once (manifest,
    # falling back to emit_paths — same precedence run_runner.execute uses)
    # and a run_dir (mirrors run_runner.execute's `.pbg/runs/<run_id>/`
    # convention, even though nothing else writes there for a study-origin
    # run today) so the child script below — which alone has the live
    # `composite.state` after this subprocess's own composite finishes
    # running — can snapshot the declared fields, and this parent (after the
    # subprocess exits) can hash + store them the same way run_runner.execute
    # does.
    fingerprint_fields = (manifest or {}).get("fingerprint_fields") or list(
        emit_paths or []
    )
    run_dir = str(WorkspacePaths.load(Path(ws_root)).pbg / "runs" / run_id)

    # Are we running a registered @composite_generator? If so, the child can
    # rebuild the composite in its own process from (spec_id, overrides) —
    # no state serialization needed. This avoids the live-Process-instance
    # problem in shared partitioned-process pools (v2ecoli) and the pint
    # Quantity infinite-recursion problem in repr() that JSON-encoding the
    # parent-built state used to hit. Non-generator callers (file-based
    # composites) keep the old state-serialization path below.
    use_generator_path = False
    try:
        from process_bigraph.composite_generator import _REGISTRY, discover_generators

        if spec_id not in _REGISTRY:
            discover_generators()
        use_generator_path = spec_id in _REGISTRY
    except ImportError:
        pass

    # Plan §D / API-survey seam #2: this used to be a bare `sys.executable`
    # while env workers resolved an interpreter for the same workspace, so one
    # workspace could be served by two different environments. resolve_run_
    # interpreter closes that — and does NOT simply defer to the env worker's
    # rule, because a run child imports vivarium_workbench itself and a
    # workspace venv generally does not carry it. See its docstring.
    from vivarium_workbench.lib import env_resolver as _env_resolver

    py = _env_resolver.resolve_run_interpreter(ws_root)
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
            _ws_data = (
                yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
                or {}
            )
            _runtime = (
                (_ws_data.get("runtime") or {}) if isinstance(_ws_data, dict) else {}
            )
            # Emitter NAME selection is centralized in the broker (Task 4/5):
            # resolve runtime.default_emitter through emitters.default_emitter so
            # every write path agrees on the default. Task 6 shipped: the global
            # default is now "xarray", so a workspace lacking
            # runtime.default_emitter resolves to "xarray" here. The child script
            # gates that xarray write path on v2ecoli being importable (review
            # CRITICAL 2) — the generic flat-Step xarray study-run loop is the
            # deferred Task 3 — so a non-v2ecoli workspace falls back to sqlite.
            # max_generations / single_daughters stay local — they drive the
            # v2ecoli colony/lineage run loop the broker does not own.
            from vivarium_workbench.lib import emitters as _emitters

            _default_emitter = _emitters.default_emitter({"runtime": _runtime}, None)
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
        # Derive a zarr store path alongside the SQLite db_file (one per run)
        # via the canonical run-store convention (<study>/runs.<run_id>.zarr).
        _zarr_store = str(run_store.zarr_store_path_for_db(Path(db_file), run_id))
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
            import json, os, sys, traceback
            try:
                from {pkg}.core import build_core
                from process_bigraph import Composite, gather_emitter_results
                try:
                    from viva_emitters.sqlite_emitter import SQLiteEmitter
                except ImportError:  # process-bigraph < 1.4.17 (legacy location)
                    from process_bigraph.emitter import SQLiteEmitter
                from process_bigraph.composite_generator import (
                    _REGISTRY, build_generator, discover_generators,
                    apply_core_extensions,
                )
                from vivarium_workbench.lib import composite_runs as cr
                from bigraph_schema.json_codec import BigraphJSONEncoder as _BJE
                _payload = {payload!r}
                if _payload['spec_id'] not in _REGISTRY: discover_generators()
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
                # v2ecoli applicability gate (review CRITICAL 2): the xarray
                # study-run write path is wired ONLY for v2ecoli (its
                # colony/multigen loop in v2ecoli.library.xarray_run). Wiring
                # the generic flat-Step xarray emitter into composite_subprocess
                # is the DEFERRED Task 3 (it needs a division-aware drive loop).
                # So gate xarray on v2ecoli being importable, NOT on
                # default_emitter alone — otherwise a study run on a non-v2ecoli
                # workspace whose GLOBAL default is now "xarray" (Task 6) hits an
                # unconditional v2ecoli import and the subprocess ImportErrors.
                # A generic run instead falls through to the single-generation
                # sqlite branch below (writes sqlite, SUCCEEDS).
                try:
                    import v2ecoli as _v2ecoli  # noqa: F401
                    _v2ecoli_available = True
                except ImportError:
                    _v2ecoli_available = False
                _mg = int(_payload.get('max_generations') or 1)
                _use_xarray = (
                    _payload.get('default_emitter') == 'xarray' and _v2ecoli_available)
                # FU2b: generic SINGLE-generation xarray. A non-v2ecoli workspace
                # whose default resolves to xarray now writes a real zarr store
                # via the broker's flat-Step XArrayEmitter
                # (emitters.run_with_emitter), instead of silently falling back to
                # sqlite. SCOPE: single generation only (max_generations <= 1).
                # Multi-generation non-v2ecoli runs still take the sqlite path
                # below — there is NO generic division-aware xarray drive yet, so
                # retiring the gated v2ecoli xarray branch fully would need that
                # drive loop (a separate, larger feature).
                _use_generic_xarray = (
                    _payload.get('default_emitter') == 'xarray'
                    and not _v2ecoli_available and _mg <= 1)
                if (_payload.get('default_emitter') == 'xarray'
                        and not _v2ecoli_available and not _use_generic_xarray):
                    print('[xarray-run] multi-generation generic xarray awaits a '
                          'division-aware drive; falling back to sqlite '
                          '(v2ecoli not importable in this workspace).',
                          file=sys.stderr)
                _view = []
                if _use_xarray:
                    # Auto-view from the study's declared observables. v0 of
                    # view_from_emit_paths is scalar-only — vector observables
                    # (monomer_counts, fork_coordinates, RNAP_coordinates, …)
                    # are skipped. If a study declares ONLY vector observables
                    # (e.g. dnaa-01 emits only listeners.monomer_counts), the
                    # auto-view is empty and the XArrayEmitter constructor
                    # would crash. In that case, fall back to SQLite for this
                    # run so the study isn't blocked. Defense-in-depth: even
                    # with v2ecoli importable, guard the specific submodule
                    # import so a packaging gap can't crash the run.
                    try:
                        from v2ecoli.library.xarray_run import (
                            run_multigen_xarray, view_from_emit_paths,
                        )
                    except ImportError:
                        print('[xarray-run] generic xarray study-runs await '
                              'Task 3; falling back to sqlite '
                              '(v2ecoli.library.xarray_run unavailable).',
                              file=sys.stderr)
                        _use_xarray = False
                if _use_xarray:
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
                elif _use_generic_xarray:
                    # FU2b: generic single-generation flat-Step XArray write.
                    # Mirror run_runner.execute's run_with_emitter call, building
                    # from the already-built state/core/emit_paths. The broker
                    # injects XArrayEmitter as a flat Step, drives the composite,
                    # writes a partitioned zarr store under out_dir, and
                    # AUTO-FALLS-BACK to sqlite for an empty view (a composite
                    # with no emittable observable) — so this NEVER blocks a run.
                    from vivarium_workbench.lib import emitters as _emitters
                    _out_dir = os.path.dirname(os.path.abspath(_payload['db_file']))
                    # CRITICAL: pin the store to the STUDY convention
                    # (<study>/runs.<run_id>.zarr == _payload['zarr_store']) so the
                    # study read-path can find it. study_run_state.zarr_store_for_sim
                    # resolves <db_stem>.<run_id>.zarr and study_charts globs
                    # runs.*.zarr; the default <out_dir>/<run_id>.zarr name would
                    # be unresolvable (None) → empty charts. store_path overrides it.
                    _prov = _emitters.run_with_emitter(
                        'xarray', state=state, run_id=_payload['run_id'],
                        emit_paths=_payload.get('emit_paths') or [],
                        out_dir=_out_dir, core=core, steps=_payload['steps'],
                        db_file=_payload['db_file'], spec=None,
                        store_path=_payload['zarr_store'])
                    composite = _prov.get('composite')
                    if _prov.get('output_kind') == 'zarr':
                        results = {{'zarr_store': _prov.get('store_path'),
                                   'output_kind': 'zarr',
                                   'steps': _prov.get('steps')}}
                    else:
                        # Empty-view (or under-filled-buffer) auto-fall-back to
                        # sqlite inside the broker — read the sqlite history.
                        results = gather_emitter_results(composite)
                else:
                    if _mg > 1 and _v2ecoli_available:
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
        # Task 5: inject BOTH the live RAM emitter (for the Explorer's
        # Results view) and, when the composite declares one, its default
        # Parquet emitter for persistence — falling back to SQLite only
        # when nothing is declared.
        _res = inject_run_emitters(
            state,
            spec_id=spec_id,
            run_id=run_id,
            emit_paths=emit_paths,
            workspace=ws_root,
            db_file=db_file,
        )
        state = _res["state"]
        run_emitter_kind = _res["emitter"]
        state = strip_process_instances(state)
        _state_fd, _state_path = _tempfile.mkstemp(
            suffix=".state.json", prefix="vivarium-run-"
        )
        try:
            # Explicit encoding here too. BigraphJSONEncoder escapes non-ASCII
            # today (\u2014 rather than the character), so this write happens to
            # be locale-safe -- but that is a property of an encoder in another
            # package, not of this call. Stating utf-8 makes it true here.
            with os.fdopen(_state_fd, "w", encoding="utf-8") as _f:
                json.dump(state, _f, cls=BigraphJSONEncoder)
        except Exception:
            try:
                os.unlink(_state_path)
            except OSError:
                pass
            raise

        script = textwrap.dedent(f"""
            import json, sys, traceback
            try:
                from {pkg}.core import build_core
                from process_bigraph import Composite, gather_emitter_results
                try:
                    from viva_emitters.sqlite_emitter import SQLiteEmitter
                except ImportError:  # process-bigraph < 1.4.17 (legacy location)
                    from process_bigraph.emitter import SQLiteEmitter
                from bigraph_schema.json_codec import bigraph_json_hook
                from vivarium_workbench.lib import composite_runs as cr
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
            # Gather rendered viz HTML, if viva_superpowers is importable.
            viz_html = {{}}
            try:
                from process_bigraph.visualization import render_results
                rendered = render_results(composite)
                for path_tuple, payload in rendered.items():
                    key = '.'.join(str(p) for p in path_tuple)
                    viz_html[key] = payload
            except Exception:
                viz_html = {{}}
            # reproducible-rerun-spine Task 3 (G4), fix round 1: snapshot this
            # run's declared fingerprint_fields from the just-completed
            # composite's live state — only available HERE, in the child;
            # the parent (after this subprocess exits) reads it back via
            # result_fingerprint.fingerprint_run(). Best-effort: swallowed so
            # a snapshot failure never turns a successful run into a reported
            # @@@ERROR@@@.
            try:
                from vivarium_workbench.lib import result_fingerprint as _rfp
                _rfp.write_snapshot({run_dir!r}, composite.state, {fingerprint_fields!r})
            except Exception:
                pass
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
        from vivarium_workbench.lib import generation as _gen

        _generation_id = _gen.current_generation_id(ws_root)
    except Exception:  # noqa: BLE001 — generation is advisory, never fatal
        _generation_id = None

    conn = cr.connect(db_file)
    try:
        # Tag the run's JSONL index record with the study (and investigation) it
        # belongs to, derived from the db_file path (studies/<slug>/runs.db).
        # Without this the 'started' event logs study_slug=None and the
        # per-study run list can only find the run via a study.yaml
        # cross-reference backfill — the root of the "study: None" records in
        # .pbg/runs.jsonl.
        _study_slug, _inv_slug = run_index_slugs_from_db_path(db_file)
        try:
            cr.save_metadata(
                conn,
                spec_id=spec_id,
                run_id=run_id,
                params=overrides,
                label=label,
                started_at=time.time(),
                n_steps=steps,
                generation_id=_generation_id,
                workspace=ws_root,
                emitter=run_emitter_kind,
                study_slug=_study_slug,
                investigation_slug=_inv_slug,
                manifest=manifest,
            )
            if sim_name is not None:
                conn.execute(
                    "UPDATE runs_meta SET sim_name=? WHERE run_id=?", (sim_name, run_id)
                )
                conn.commit()
        except sqlite3.IntegrityError:
            return (
                {
                    "simulation_id": run_id,
                    "error": "duplicate run_id (rare timing collision) — retry",
                },
                500,
            )
        if _generation_id is not None:
            try:
                _gen.record_run(
                    ws_root,
                    _generation_id,
                    study=(sim_name or label or spec_id),
                    run_id=run_id,
                    sim_name=sim_name,
                )
            except Exception:  # noqa: BLE001 — manifest index is best-effort
                pass

        # v2ecoli friction #10: persist the rendered script alongside runs.db
        # so "what did the dashboard actually run for this run_id?" is one cat.
        # Best-effort; never fail a run because the sidecar couldn't be written.
        try:
            _db_dir = os.path.dirname(os.path.abspath(db_file))
            _sims_dir = os.path.join(_db_dir, "sims")
            os.makedirs(_sims_dir, exist_ok=True)
            # encoding="utf-8" is load-bearing, not tidiness. Without it this
            # uses the LOCALE default, which in a container is ascii -- and the
            # generated script carries non-ASCII (this module alone has 42 lines
            # using em-dashes and arrows, which reach the script through its
            # f-string templates). That raised
            #     UnicodeEncodeError: 'ascii' codec can't encode '\u2014'
            # on every hosted run.
            with open(
                os.path.join(_sims_dir, f"{run_id}.subprocess.py"),
                "w",
                encoding="utf-8",
            ) as _f:
                _f.write(script)
        except Exception:  # noqa: BLE001
            # Was `except OSError`, which does NOT catch UnicodeEncodeError (a
            # ValueError). So the guard that exists to make this write
            # best-effort let through the one error it actually threw, and a
            # DEBUG CONVENIENCE killed the real study run.
            #
            # Nothing below this write depends on it: it saves a copy of the
            # script for post-mortem reading. A best-effort artifact must never
            # be able to fail the work it is describing, so the guard now covers
            # anything.
            pass

        try:
            try:
                result = subprocess.run(
                    [py, "-c", script],
                    cwd=ws_root,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                try:
                    if exc.process is not None:
                        exc.process.kill()
                        exc.process.communicate(timeout=2)
                except Exception:
                    pass
                cr.complete_metadata(
                    conn, run_id=run_id, n_steps=0, status="failed", workspace=ws_root
                )
                return ({"simulation_id": run_id, "error": "run timed out"}, 504)
        finally:
            if _state_path is not None:
                try:
                    os.unlink(_state_path)
                except OSError:
                    pass

        out = result.stdout
        if "@@@ERROR@@@" in out:
            cr.complete_metadata(
                conn, run_id=run_id, n_steps=0, status="failed", workspace=ws_root
            )
            tb = out.split("@@@ERROR@@@", 1)[1].strip()
            return (
                {"simulation_id": run_id, "error": "run failed", "traceback": tb},
                502,
            )

        try:
            from bigraph_schema.json_codec import bigraph_json_hook

            payload = json.loads(
                out.split("@@@RESULTS@@@", 1)[1].strip(),
                object_hook=bigraph_json_hook,
            )
        except (IndexError, json.JSONDecodeError):
            cr.complete_metadata(
                conn, run_id=run_id, n_steps=0, status="failed", workspace=ws_root
            )
            return (
                {
                    "simulation_id": run_id,
                    "error": "could not parse run output",
                    "stdout": out,
                    "stderr": result.stderr,
                },
                502,
            )

        # Subprocess emits {results, viz_html}; older versions emitted the
        # results dict directly. Handle both for forward/backward compat.
        if isinstance(payload, dict) and "results" in payload:
            results = payload.get("results") or {}
            viz_html = payload.get("viz_html") or {}
        else:
            results = payload
            viz_html = {}

        # reproducible-rerun-spine Task 3 (G4), fix round 1: the child already
        # wrote this run's observables.json snapshot (see the shared tail
        # script above, which has the only live composite.state); compute +
        # store result_fingerprint from it the same way run_runner.execute
        # does for composite-origin runs. Best-effort — never blocks an
        # otherwise-successful run.
        try:
            from vivarium_workbench.lib import result_fingerprint as rfp

            fingerprint = rfp.fingerprint_run(run_dir, fingerprint_fields)
            cr.set_result_fingerprint(conn, run_id=run_id, fingerprint=fingerprint)
        except Exception:
            pass

        # Record where this run emitted so downstream readers (comparison_cards'
        # run-store resolution over the real-worker verdict path; capability
        # derivation) can find the store without a lazy on-read backfill. The
        # canonical convention is <study>/runs.<run_id>.zarr (== the child's
        # store_path pin above); only record it if it actually landed on disk.
        _emitter_path = None
        try:
            from vivarium_workbench.lib import run_store as _run_store

            _cand = _run_store.zarr_store_path_for_db(Path(db_file), run_id)
            if Path(_cand).exists():
                _emitter_path = str(_cand)
        except Exception:  # noqa: BLE001 — best-effort; never blocks completion
            pass
        cr.complete_metadata(
            conn,
            run_id=run_id,
            n_steps=steps,
            status="completed",
            workspace=ws_root,
            emitter_path=_emitter_path,
        )

        # reproducible-rerun-spine Task 4: this run itself is a recorded
        # reproduction of an earlier one — verify the two result_fingerprints
        # now that both are stored. See the ``reran_from`` docstring note
        # above for why this lives here rather than only in
        # run_runner.execute (which never sees study-origin runs).
        if reran_from:
            try:
                from vivarium_workbench.lib import rerun as rerun_mod

                rerun_mod.verify_reproduction(ws_root, reran_from, run_id)
            except Exception:
                pass

        return (
            {
                "simulation_id": run_id,
                "results": results,
                "viz_html": viz_html,
                "steps": steps,
            },
            200,
        )
    finally:
        conn.close()
