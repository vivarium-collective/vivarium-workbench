"""Study-run orchestrators extracted from server.py.

These are the ``ws_root``-parameterized study-run engine entrypoints (study-run
engine extraction, phase E4) — the orchestration stage that drives a study run
end to end: resolve the study spec, build/resolve the baseline (or variant)
composite state, dispatch the simulation subprocess (or delegate an ensemble
sweep to v2ecoli-workflow), then fire the post-run side-effects (render viz,
post-run scripts, analyses, outcome sync). The legacy server.py
``_post_study_run_*_for_test`` functions now delegate to the functions here via
thin name-shims, keeping their route handlers + test imports intact and the live
path byte-identical (the route handlers call ``_post_study_run_*_for_test(WORKSPACE,
body)``, so ``ws_root`` is ``WORKSPACE`` on the live path).

None of these functions import ``server``. They reuse the already-extracted lib
engine pieces — ``composite_subprocess`` (E1), ``study_run_state`` (E2),
``study_run_post`` (E3) — plus existing lib helpers (``study_spec``,
``lifecycle_mutations``, ``investigations``, ``study_crud_mutations``,
``composite_runs``, ``ensemble_config``, ``spec_migration``). The workspace root
is threaded explicitly as ``ws_root`` (replacing the server ``WORKSPACE`` global)
so the module stays importable standalone and flip-ready.

Functions
---------
run_study_baseline       → run a study's baseline composite + post-run stages
run_study_variant        → run a variant (single-run) or delegate an ensemble sweep
"""

from __future__ import annotations

import json
import sys

import yaml

from vivarium_workbench.lib import composite_subprocess
from vivarium_workbench.lib import lifecycle_mutations
from vivarium_workbench.lib import run_core
from vivarium_workbench.lib import study_run_post
from vivarium_workbench.lib import study_run_state
from vivarium_workbench.lib import study_spec
from vivarium_workbench.lib.study_crud_mutations import _study_name_from_body


def _resolve_study_dir(ws_root, name):
    """Resolve a study's directory honoring the workspace ``layout:`` map.

    Uses :class:`WorkspacePaths` (nested investigations/<inv>/studies/<s> and
    relocated layouts), falling back to the classic flat ``studies/<name>`` /
    ``investigations/<name>`` paths for legacy studies whose dir lacks a
    ``study.yaml`` (so WorkspacePaths' study.yaml gate skips them).
    """
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    try:
        return WorkspacePaths.load(ws_root).study_dir(name)
    except FileNotFoundError:
        flat = ws_root / "studies" / name
        return flat if flat.is_dir() else ws_root / "investigations" / name


def run_study_baseline(ws_root, body):
    """Run a Study's baseline composite. Returns (response_dict, status_code).

    Body:
      study:     <name>  (or `name`/`investigation`)
      composite: <baseline-entry name>  (optional; default = baseline[0].name)
      steps:     <int>   (optional; overrides params.n_steps; default 5)
    """
    from vivarium_workbench.lib import composite_runs as cr

    name = _study_name_from_body(body)
    if not name:
        return {"error": "missing study"}, 400
    # Resolve study dir from ws_root so _for_test callers don't need WORKSPACE patched.
    study_dir = _resolve_study_dir(ws_root, name)
    sf = study_spec.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    # Auto-migrate legacy v2-shape specs (baseline: <str>, variants: [...]) to
    # the v3 list shape this handler expects. In-memory only; doesn't rewrite
    # the file. Keeps legacy investigations/spec.yaml usable.
    from vivarium_workbench.lib.spec_migration import migrate_v2_to_v3
    spec = migrate_v2_to_v3(spec)
    # v4-redesign projection: synthesises legacy fields (baseline list,
    # variants list, behavior_tests, simulation_set) from a v4 conditions
    # block. Idempotent on v3 (no-op when conditions is absent).
    if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
        from vivarium_workbench.lib.investigations import _project_v4_redesign_to_legacy_view
        spec = _project_v4_redesign_to_legacy_view(spec)
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return {"error": "study has no baseline composites"}, 400

    requested = (body.get("composite") or "").strip()
    if requested:
        entry = next((b for b in baseline if isinstance(b, dict) and b.get("name") == requested),
                     None)
        if entry is None:
            return {"error": f"baseline composite {requested!r} not found"}, 404
    else:
        entry = baseline[0]
    spec_id = entry.get("composite")
    if not spec_id:
        return {"error": f"baseline entry {entry.get('name')!r} has no composite"}, 400

    params = dict(entry.get("params") or {})
    params_n_steps = params.pop("n_steps", None)
    generator_overrides = params

    # I1: overlay request-body overrides on top of baseline params so the
    # form config (Configure & Run widget) is honored end-to-end.
    generator_overrides.update(body.get("overrides") or {})
    # Also honor body steps for the run_id and full_params.
    if body.get("steps"):
        params_n_steps = int(body["steps"])

    # Compute full_params / db_file / label early so the remote-build guard
    # can fire before the expensive workspace.yaml read + state resolution.
    full_params = dict(generator_overrides)
    if params_n_steps is not None:
        full_params["n_steps"] = params_n_steps
    db_file = str(study_dir / "runs.db")
    label = entry.get("name") or "baseline"
    try:
        plan = run_core.invoke_run(ws_root, spec_id=spec_id, config=full_params,
                                   db_path=db_file, label=label, n_steps=params_n_steps)
    except run_core.RunTargetUnavailable as e:
        return {"error": str(e)}, 409
    run_id = plan.run_id

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
    # XArrayEmitter buffers ~hundreds of ticks before flushing, so the legacy
    # 5-tick default produces empty zarr stores. Workspaces declare a sensible
    # baseline run length via runtime.default_n_steps; we fall back to 5 only
    # if neither the body, the study yaml, nor the workspace specifies one
    # (preserves the legacy quick-smoke behaviour for SQLite workspaces).
    _runtime = (ws_data.get("runtime") or {}) if isinstance(ws_data, dict) else {}
    ws_default_n_steps = _runtime.get("default_n_steps")
    steps = int(body.get("steps") or params_n_steps or ws_default_n_steps or 5)

    if body.get("dry_run"):
        return {
            "dry_run": True,
            "request": {
                "spec_id": spec_id,
                "overrides": generator_overrides,
                "steps": steps,
                "run_id": run_id,
                "db_file": db_file,
            },
        }, 200

    state, err = study_run_state.resolve_study_baseline_state(ws_root, pkg, spec_id, generator_overrides)
    if err is not None:
        return err, 400
    # v2ecoli friction #6: subprocess timeout from study yaml so a 3600-step
    # baseline isn't killed by the 120s default. Per-study override.
    runtime_cfg = (spec.get("runtime") or {}) if isinstance(spec.get("runtime"), dict) else {}
    timeout_s = int(runtime_cfg.get("subprocess_timeout_s") or 1800)
    # v2ecoli friction #14: derive emit_paths from spec observables so the
    # injected SQLiteEmitter captures real biology, not just ticks.
    emit_paths = cr.collect_emit_paths_from_spec(spec)
    # Per-study overrides — all win over workspace defaults. Emitter precedence:
    # study runtime.emitter > investigation runtime.default_emitter > workspace.
    study_emitter = runtime_cfg.get("emitter") or study_run_state.investigation_emitter_for_study(ws_root, spec.get("name"))
    study_max_generations = runtime_cfg.get("max_generations")
    study_single_daughters = runtime_cfg.get("single_daughters")
    response, code = composite_subprocess.run_composite_subprocess(
        ws_root,
        pkg=pkg, state=state, steps=steps, db_file=db_file,
        run_id=run_id, spec_id=spec_id, label=label, sim_name=label,
        overrides=generator_overrides, timeout=timeout_s,
        emit_paths=emit_paths, study_emitter=study_emitter,
        study_max_generations=study_max_generations,
        study_single_daughters=study_single_daughters,
    )
    if code == 200:
        # F2: do NOT append to study.yaml.runs[] — the runs_meta row
        # written by _run_composite_subprocess (via composite_runs.save_metadata)
        # IS the canonical record. The Runs tab reads runs.db directly via
        # _read_runs_db_for_study + _enrich_runs_with_meta; appending here
        # would duplicate the same fact in two places and let them drift.
        #
        # Render canonical viz: composite defaults from
        # @composite_generator(visualizations=...) merged with Study-declared
        # ones (Study wins on name collision). Writes HTML under
        # <study_dir>/viz/. Per-viz errors absorbed; others still render.
        viz_files, viz_errors = study_run_post.render_study_visualizations(
            ws_root, study_dir, spec, spec_id,
        )
        if viz_files:
            response.setdefault("viz_files", []).extend(viz_files)
        if viz_errors:
            response.setdefault("viz_errors", []).extend(viz_errors)
        # post_run_scripts: study-yaml-declared scripts to invoke after the
        # auto-render dispatch. Pattern for hand-rolled render scripts that
        # don't fit the @Visualization class registry (e.g. chromosome-state
        # snapshotters that run their own sim and write HTML directly).
        # Schema:
        #   post_run_scripts:
        #   - path: scripts/render_chromosome_timeline.py
        #     args: ["--study", "dnaa-02", "--spec", "...", "--steps", "600"]
        #     timeout_s: 1800
        script_files, script_errors = study_run_post.run_post_run_scripts(spec, ws_root)
        if script_files:
            response.setdefault("post_run_script_files", []).extend(script_files)
        if script_errors:
            response.setdefault("post_run_script_errors", []).extend(script_errors)
        # Post-run analysis hook: run spec.analyses[] steps over the parquet emitter
        # output.  Synchronous (runs before this HTTP response returns) so the
        # analysis outputs are on disk by the time the client refreshes.
        analysis_files, analysis_errors = study_run_post.run_study_analyses(
            study_dir, spec, run_id, ws_root)
        if analysis_files:
            response.setdefault("analysis_files", []).extend(analysis_files)
        if analysis_errors:
            response.setdefault("analysis_errors", []).extend(analysis_errors)
        try:
            from pbg_superpowers import study_outcomes
            study_outcomes.sync(study_dir)  # record runs + compute outcomes
        except Exception as exc:  # never fail a successful run on a record error
            print(f"[study_outcomes] sync failed: {exc}", file=sys.stderr)
        # Feedback-friction: capture this run's effective parameters onto
        # runs[].provenance.params (guarded; no-op on older pbg_superpowers).
        # Runs AFTER study_outcomes.sync so the runs[] entry exists to attach to.
        try:
            from pbg_superpowers import run_params
            captured = run_params.capture_run_params(
                full_params, overrides=generator_overrides)
            run_params.write_run_params(
                study_dir, run_id, captured, source="dashboard-runner")
        except Exception as exc:
            print(f"[run_params] capture failed: {exc}", file=sys.stderr)
        # Feedback-friction: auto-evaluate the study's behavior tests against the
        # just-completed run so per-study test pills stop showing pending
        # (guarded; SAFE DEFAULT — never stamps canonical).
        try:
            from pbg_superpowers import auto_evaluate
            auto_evaluate.evaluate_on_run_completion(study_dir, run_id, ws_root=ws_root)
        except Exception as exc:  # never fail a successful run on an eval error
            print(f"[auto_evaluate] failed: {exc}", file=sys.stderr)
        lifecycle_mutations._sync_parent_investigation(ws_root, study_dir)  # SP1: roll up to investigation
    return response, code


def run_study_variant(ws_root, body):
    """Run a Study variant (baseline + param overrides). Returns (response_dict, status_code).

    Body:
      study:   <name>
      variant: <variant name>
    Resolves the variant's `base_composite` against the study's `baseline[]`,
    layers `parameter_overrides` on top of that entry's `params`, and runs.

    SP2a: a variant declaring `kind: sweep` / `kind: seeds` is an ENSEMBLE — it
    is DELEGATED to v2ecoli-workflow (which packs every grid point into ONE
    parquet hive store), not executed as N independent dashboard subprocesses.
    """
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.ensemble_config import (
        build_workflow_config, delegation_available, is_delegatable_sweep,
    )

    name = _study_name_from_body(body)
    variant_name = (body.get("variant") or "").strip()
    if not name or not variant_name:
        return {"error": "missing study or variant"}, 400
    # Resolve study dir from ws_root (honors layout:; supports standalone tests
    # without monkeypatching WORKSPACE).
    study_dir = _resolve_study_dir(ws_root, name)
    sf = study_spec.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    # Auto-migrate legacy v2-shape specs to v3 list shape (see run-baseline).
    from vivarium_workbench.lib.spec_migration import migrate_v2_to_v3
    spec = migrate_v2_to_v3(spec)
    # v4-redesign projection: synthesises legacy fields (baseline list,
    # variants list, behavior_tests, simulation_set) from a v4 conditions
    # block. Idempotent on v3 (no-op when conditions is absent).
    if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
        from vivarium_workbench.lib.investigations import _project_v4_redesign_to_legacy_view
        spec = _project_v4_redesign_to_legacy_view(spec)
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return {"error": "study has no baseline composites"}, 400

    variant = next((v for v in (spec.get("variants") or [])
                    if isinstance(v, dict) and v.get("name") == variant_name), None)
    if variant is None:
        return {"error": f"variant {variant_name!r} not found"}, 404

    # Variant resolution: a variant may either
    #   (a) point at its own ``composite`` directly (v4 redesign — a
    #       variant can use a different generator than the baseline), or
    #   (b) reference a baseline entry by name via ``base_composite``,
    #       inheriting its composite + params (legacy v3 shape).
    # Direct composite wins when present.
    direct_composite = (variant.get("composite") or "").strip()
    if direct_composite:
        spec_id = direct_composite
        params: dict = {}  # no baseline params inheritance — variant is standalone
    else:
        base_name = (variant.get("base_composite") or "").strip()
        if base_name:
            entry = next((b for b in baseline
                          if isinstance(b, dict) and b.get("name") == base_name), None)
            if entry is None:
                return {"error": f"variant base_composite {base_name!r} not in baseline"}, 404
        else:
            entry = baseline[0]
        spec_id = entry.get("composite")
        if not spec_id:
            return {"error": f"baseline entry {entry.get('name')!r} has no composite"}, 400
        params = dict(entry.get("params") or {})

    overrides = variant.get("parameter_overrides") or variant.get("params") or {}
    params.update(overrides)

    params_n_steps = params.pop("n_steps", None)
    generator_overrides = params

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
    # Same workspace-level default as the baseline path — see comment there.
    _runtime = (ws_data.get("runtime") or {}) if isinstance(ws_data, dict) else {}
    ws_default_n_steps = _runtime.get("default_n_steps")
    steps = int(body.get("steps") or params_n_steps or ws_default_n_steps or 5)

    # Hoisted dry-run guard: fires before the if/else branch split and before any
    # invoke_run / workflow call so there are zero side effects (no DB write, no
    # subprocess, no out/ directory).  generate_run_id is a pure function — it
    # only hashes (spec_id, params); it does NOT touch runs.db.
    if body.get("dry_run"):
        full_params = dict(generator_overrides)
        if params_n_steps is not None:
            full_params["n_steps"] = params_n_steps
        run_id = cr.generate_run_id(spec_id, full_params)
        return {
            "dry_run": True,
            "request": {
                "spec_id": spec_id,
                "overrides": generator_overrides,
                "steps": steps,
                "run_id": run_id,
                "db_file": str(study_dir / "runs.db"),
            },
        }, 200

    kind = variant.get("kind")
    if kind in ("sweep", "seeds"):
        # Review FIX 1: branch on the variant being an ENSEMBLE first. A
        # `kind: sweep`/`kind: seeds` variant is NEVER silently single-run as a
        # baseline — if it is not delegatable (bare-key sweep, missing/zero
        # n_seeds) it must error CLEARLY rather than ignore the declared sweep.
        if not is_delegatable_sweep(variant):
            if kind == "seeds":
                return ({"error": "kind: seeds requires n_seeds >= 1"}, 422)
            # kind == "sweep" — empty or bare-key (non-"<proc>.<key>") targets.
            sweep_over = variant.get("sweep_over") or {}
            if not sweep_over:
                return ({"error": "kind: sweep requires a non-empty sweep_over "
                         "of '<process>.<key>' targets"}, 422)
            bad = [k for k in sweep_over if "." not in str(k)]
            return ({"error": "sweep targets must be '<process>.<key>' "
                     f"(got bare keys: {bad})"}, 422)
        # SP2a delegation: hand the whole ensemble to v2ecoli-workflow once. It
        # packs all sweep/seed points into ONE parquet hive store under
        # out/<run_id>/, which the post-run sync records as a single run. We do
        # NOT resolve/build the composite here (no _resolve_study_baseline_state)
        # — the workflow engine builds every branch itself.
        if not delegation_available(ws_root):
            return ({"error": "ensemble sweep/seeds runs require a v2ecoli "
                     "workspace (v2ecoli-workflow) with `<proc>.<key>` sweep "
                     "targets; this workspace cannot delegate"}, 422)
        full_params = dict(generator_overrides)
        if params_n_steps is not None:
            full_params["n_steps"] = params_n_steps
        try:
            plan = run_core.invoke_run(ws_root, spec_id=spec_id, config=full_params,
                                       db_path=study_dir / "runs.db", label=variant_name,
                                       n_steps=params_n_steps)
        except run_core.RunTargetUnavailable as e:
            return {"error": str(e)}, 409
        run_id = plan.run_id
        runtime_cfg = (spec.get("runtime") or {}) if isinstance(spec.get("runtime"), dict) else {}
        timeout_s = int(runtime_cfg.get("subprocess_timeout_s") or 1800)
        out_dir = study_dir / "out" / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        # experiment_id == run_id so the packed store + the recorded run align.
        cfg = build_workflow_config(variant, run_id, str(out_dir))
        cfg_path = out_dir / "config.json"
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        response, code = composite_subprocess.invoke_v2ecoli_workflow(
            str(cfg_path), out_dir, ws_root, timeout_s)
    else:
        full_params = dict(generator_overrides)
        if params_n_steps is not None:
            full_params["n_steps"] = params_n_steps

        db_file = str(study_dir / "runs.db")
        try:
            plan = run_core.invoke_run(ws_root, spec_id=spec_id, config=full_params,
                                       db_path=db_file, label=variant_name, n_steps=params_n_steps)
        except run_core.RunTargetUnavailable as e:
            return {"error": str(e)}, 409
        run_id = plan.run_id

        state, err = study_run_state.resolve_study_baseline_state(ws_root, pkg, spec_id, generator_overrides)
        if err is not None:
            return err, 400
        # v2ecoli friction #6: per-study subprocess timeout.
        runtime_cfg = (spec.get("runtime") or {}) if isinstance(spec.get("runtime"), dict) else {}
        timeout_s = int(runtime_cfg.get("subprocess_timeout_s") or 1800)
        # v2ecoli friction #14: thread observables to the subprocess (same as
        # baseline path) so variant runs also capture biology in history.state.
        emit_paths = cr.collect_emit_paths_from_spec(spec)
        # Per-study overrides — see baseline path for rationale. Emitter precedence:
        # study runtime.emitter > investigation runtime.default_emitter > workspace.
        study_emitter = runtime_cfg.get("emitter") or study_run_state.investigation_emitter_for_study(ws_root, spec.get("name"))
        study_max_generations = runtime_cfg.get("max_generations")
        study_single_daughters = runtime_cfg.get("single_daughters")
        response, code = composite_subprocess.run_composite_subprocess(
            ws_root,
            pkg=pkg, state=state, steps=steps, db_file=db_file,
            run_id=run_id, spec_id=spec_id, label=variant_name,
            sim_name=variant_name, overrides=generator_overrides,
            timeout=timeout_s, emit_paths=emit_paths,
            study_emitter=study_emitter,
            study_max_generations=study_max_generations,
            study_single_daughters=study_single_daughters,
        )
    # F2: no _append_study_run — the runs_meta row is the canonical record;
    # see the matching note in run-baseline above.
    if code == 200:
        # Same canonical-viz + post-run-scripts dispatch as the baseline path
        # so variants also refresh chromosome viz etc.
        viz_files, viz_errors = study_run_post.render_study_visualizations(
            ws_root, study_dir, spec, spec_id,
        )
        if viz_files:
            response.setdefault("viz_files", []).extend(viz_files)
        if viz_errors:
            response.setdefault("viz_errors", []).extend(viz_errors)
        script_files, script_errors = study_run_post.run_post_run_scripts(spec, ws_root)
        if script_files:
            response.setdefault("post_run_script_files", []).extend(script_files)
        if script_errors:
            response.setdefault("post_run_script_errors", []).extend(script_errors)
        # Post-run analysis hook: mirrors baseline path — run spec.analyses[] steps.
        analysis_files, analysis_errors = study_run_post.run_study_analyses(
            study_dir, spec, run_id, ws_root)
        if analysis_files:
            response.setdefault("analysis_files", []).extend(analysis_files)
        if analysis_errors:
            response.setdefault("analysis_errors", []).extend(analysis_errors)
        try:
            from pbg_superpowers import study_outcomes
            study_outcomes.sync(study_dir)  # record runs + compute outcomes
        except Exception as exc:  # never fail a successful run on a record error
            print(f"[study_outcomes] sync failed: {exc}", file=sys.stderr)
        # Feedback-friction: capture this run's effective parameters onto
        # runs[].provenance.params (guarded; no-op on older pbg_superpowers).
        # Runs AFTER study_outcomes.sync so the runs[] entry exists to attach to.
        try:
            from pbg_superpowers import run_params
            captured = run_params.capture_run_params(
                full_params, overrides=generator_overrides)
            run_params.write_run_params(
                study_dir, run_id, captured, source="dashboard-runner")
        except Exception as exc:
            print(f"[run_params] capture failed: {exc}", file=sys.stderr)
        # Feedback-friction: auto-evaluate the study's behavior tests against the
        # just-completed run so per-study test pills stop showing pending
        # (guarded; SAFE DEFAULT — never stamps canonical).
        try:
            from pbg_superpowers import auto_evaluate
            auto_evaluate.evaluate_on_run_completion(study_dir, run_id, ws_root=ws_root)
        except Exception as exc:  # never fail a successful run on an eval error
            print(f"[auto_evaluate] failed: {exc}", file=sys.stderr)
        lifecycle_mutations._sync_parent_investigation(ws_root, study_dir)  # SP1: roll up to investigation
    return response, code
