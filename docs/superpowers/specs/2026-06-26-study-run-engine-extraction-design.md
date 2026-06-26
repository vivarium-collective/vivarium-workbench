# Study-run engine extraction design (FastAPI Phase C-state, final cluster)

**Date:** 2026-06-26
**Status:** approved (direction), ready to plan
**Context:** the vivarium-dashboard FastAPI strangler-fig migration has ported ~all routes
except the **simulation-execution cluster**. Those routes are blocked because their logic runs
actual simulations and lives as server-only module-level functions + one Handler instance method
not yet in `lib/`. This spec defines the extraction that unblocks them — after which the routes
port via the same pattern every other route used (pure `ws_root`-parameterized `lib/` builder; live
server handler left byte-identical until the flip).

## The reframe (from the code map)

The "engine" is NOT new architecture — it is a cluster of **already-pure, already-`ws_root`-seam-backed
functions** that simply live in `server.py` and must move to `lib/`:

- `server._post_study_run_baseline_for_test(ws_root, body)` (server.py:2346) and
  `server._post_study_run_variant_for_test(ws_root, body)` (server.py:3011) are the two orchestrators.
  Both take `ws_root` explicitly and use **no `self`** — they are ready to move verbatim.
- `server._render_investigation_comparative_visualisations(self, inv_slug, iset, job)` (server.py:9187)
  is the one Handler **instance method** blocker — but it uses **no `self.*`** (only `workspace_paths()`,
  `_zarr_store_for_sim()`, and `lib.comparative_viz`), so converting it to a module-level function is
  mechanical.
- They pull a **transitive web of module-level helpers**: `_run_composite_subprocess` (3638),
  `_invoke_v2ecoli_workflow` (3604), `_resolve_study_baseline_state` (2233), `_render_study_visualizations`
  (2752), `_run_post_run_scripts` (2535), `_run_study_analyses` (2630), `_investigation_emitter_for_study`
  (2112), `_zarr_store_for_sim` (2710), `_study_name_from_body` (1968), `_study_spec_file` (645), plus
  `_collect_study_observables` (2146, already being lifted) and the `_post_study_run_all_baselines_for_test`
  aggregator. (The implementer MUST re-walk the call graph from the two orchestrators + the instance
  method to enumerate the EXACT closure — line numbers drift.)

All simulation execution is **subprocess-based** (`_run_composite_subprocess` spawns `python -c <script>`;
`_invoke_v2ecoli_workflow` spawns the v2ecoli workflow). So the extracted `lib/` code does NOT import or
run `process_bigraph.Composite` in-process — it orchestrates and shells out. This keeps the lib
importable standalone and flip-ready.

Many dependencies are **already in `lib/`** (reuse, do not re-extract): `composite_runs`, `investigations`
(incl. `run_investigation`), `comparative_viz` (`render_comparative_time_series`), `run_jobs` (manager +
`enumerate_unblocked`), `ensemble_config`, `spec_migration`, `study_tests`, `composite_lookup`.

## Decisions (user, 2026-06-26)

1. **Incremental, leaf-up extraction** — several small behavior-preserving PRs (leaf helpers → orchestrators
   → instance method → then route ports), each moving code verbatim to `lib/` with a server **name-shim** so
   call-sites + tests stay unchanged, `server.py` byte-identical in behavior, full parity tests, independent
   review. NOT one wholesale move.
2. **Several focused `lib/` modules** (by responsibility), NOT one mega-module.

## Target `lib/` layout

| Module | Functions (server → lib name) |
|--------|------------------------------|
| `lib/composite_subprocess.py` | `run_composite_subprocess`, `invoke_v2ecoli_workflow` (+ any tiny script-template helpers they own) |
| `lib/study_run_state.py` | `resolve_study_baseline_state`, `investigation_emitter_for_study`, `zarr_store_for_sim` (state/emitter/store resolution) |
| `lib/study_run_post.py` | `render_study_visualizations`, `run_post_run_scripts`, `run_study_analyses` (the post-run side-effect stages) |
| `lib/study_runs.py` | `run_study_baseline` (was `_post_study_run_baseline_for_test`), `run_study_variant`, `run_study_all_baselines` (the orchestrators) |
| `lib/comparative_runs.py` | `render_investigation_comparative_visualisations` (the ex-instance-method) |

Tiny shared spec helpers (`study_name_from_body`, `study_spec_file`) go to an existing home
(`lib/study_spec.py`) if not already there. Reuse `lib/comparative_viz.py` (do not duplicate its renderer).

## The extraction pattern (per function, every PR)

For each function moved:
- **Move the body verbatim** into its target `lib/` module, parameterized on `ws_root` (replace the
  `WORKSPACE` global and `workspace_paths()` with `WorkspacePaths.load(ws_root)` / explicit `ws_root`).
  NO `import server`. Reuse already-lib deps.
- **server.py keeps a thin NAME-SHIM** delegating to the lib function (e.g. `def _run_composite_subprocess(
  *a, **k): return _composite_subprocess.run_composite_subprocess(WORKSPACE, *a, **k)`), so every existing
  call-site and every test import is unchanged and the **live path stays byte-identical**. (This is the
  established seam pattern from the GET batches; the dedup — deleting the shim — happens at the flip.)
- The two orchestrators keep their `_for_test` server name-shims too (tests import
  `_post_study_run_baseline_for_test`).
- For `_render_investigation_comparative_visualisations`: lib gets a module-level
  `render_investigation_comparative_visualisations(ws_root, inv_slug, iset, job)`; server keeps the instance
  method as a 1-line shim `def _render_…(self, inv_slug, iset, job): return _comparative_runs.render_…(
  WORKSPACE, inv_slug, iset, job)` so the `_post_investigation_run_unblocked` call-site is unchanged.

**Behavior-preserving invariant (every PR):** `git diff origin/main -- server.py` shows only shim bodies
(no changed logic); the moved bodies are byte-identical (modulo `WORKSPACE`→`ws_root`); the full existing
run/study test suite stays green. Each extraction PR ports NO routes.

## Phased plan

**Extraction phases (no route ports):**
- **E1 — `lib/composite_subprocess.py`:** move `run_composite_subprocess` + `invoke_v2ecoli_workflow`; server
  name-shims. (The subprocess script templates move with them.) Parity tests: the runner produces the same
  command/script + handles timeout/returncode identically (monkeypatch `subprocess.run`).
- **E2 — `lib/study_run_state.py`:** move `resolve_study_baseline_state` + `investigation_emitter_for_study`
  + `zarr_store_for_sim`; server shims. Parity tests on state/store resolution.
- **E3 — `lib/study_run_post.py`:** move `render_study_visualizations` + `run_post_run_scripts` +
  `run_study_analyses`; server shims. Parity tests (monkeypatch the heavy bits).
- **E4 — `lib/study_runs.py`:** move the two (three with all-baselines) orchestrators; server keeps the
  `_for_test` name-shims. They now call the E1–E3 lib functions. Parity tests: a full baseline/variant run
  with the subprocess monkeypatched → identical response dict + runs.db writes + viz side effects.
- **E5 — `lib/comparative_runs.py`:** convert the instance method to a module-level function; server keeps a
  1-line instance-method shim. Parity test on the comparative-viz render (monkeypatch the renderer).

**Route-port phases (each = the normal FastAPI route batch; CSRF already in place):**
- **P1 — trivial shim routes:** `study-run-baseline`, `study-run-variant`, `study-run-all-baselines`,
  `study-tests-run`, `run-tests`. Each FastAPI route calls the now-lib orchestrator / `lib.study_tests` /
  an extracted pytest runner directly; server handler untouched. (`run-tests` needs a small
  `lib.study_tests`-adjacent extraction of its inline `subprocess pytest`.)
- **P2 — `composite-test-run`:** detached-subprocess submission (returns 202); port after confirming its
  run-registry spawn is lib-reachable.
- **P3 — `investigation-run-one`:** single-composite run + viz persist; mirrors the baseline/variant path,
  now lib-backed.
- **P4 — `investigation-run`:** uses `lib.investigations.run_investigation` (already lib) + the in-process
  core/viz-registry build — extract a small `lib/core_builder.py` (`build_core_for_pkg(ws_root, pkg)` /
  `build_viz_registry(ws_root, pkg)`) it shares with the viz-preview work, then port.
- **P5 — `investigation-run-unblocked`:** submits to `run_jobs.manager` with an inline `_worker` closure that
  calls the E4 orchestrators + the E5 comparative renderer. Port last: move the `_worker` to a lib builder
  (`lib/run_jobs_workers.py` or alongside the route) submitting to the same manager singleton (like
  `remote-run-start` in C-state-3c). Now fully lib-backed.

## Testing strategy

- Extraction PRs: **parity tests that drive the real moved function** with the subprocess / heavy I/O
  monkeypatched, asserting byte-identical outputs (response dicts, the subprocess command/script, runs.db
  writes, viz file writes) AND that the server name-shim returns the same thing the lib function does. NEVER
  run a real simulation in tests — monkeypatch `subprocess.run` / `_invoke_v2ecoli_workflow`.
- Route PRs: the established FastAPI route tests (happy + error paths, `_in_openapi`, `JSONResponse` status
  preservation, monkeypatched engine). The async routes (run-unblocked) assert the manager-submit contract
  (study/items + a callable), not a real run.
- Keep the full existing run/study suite green at every step (`test_study_run*`, `test_investigation_run*`,
  `test_composite_runs*`, `test_comparative*`, `test_run_jobs*`).

## Constraints (unchanged from the migration)

- **Behavior-preserving**; the live stdlib path stays byte-identical until the flip (server shims only).
- **No `lib → server` import.** Extracted modules are pure (`ws_root`-parameterized); they reuse other lib.
- **Python-first, AI-free. No new deps. No unrelated refactoring.** Subprocess execution stays subprocess
  (the lib does not run Composite in-process).
- Each PR independently tested, scoped-mypy clean, generate_ts in sync, reviewed before merge.

## Risks / watch-items

- **Helper-closure completeness:** the implementer MUST re-walk the call graph from the orchestrators + the
  instance method to capture the EXACT transitive set before moving — a missed helper left in server that the
  moved lib code calls would create a `lib → server` edge. Move the whole closure or shim every boundary.
- **Subprocess script templates:** `_run_composite_subprocess` embeds a python script as a string that
  `__import__`s the workspace `pkg.core` in a child process — move the template verbatim; it references the
  workspace by `cwd`/path, not by the server module, so it ports cleanly. Verify the child still resolves
  the workspace package (cwd + sys.path) identically.
- **runs.db / emitter side effects:** the orchestrators write metadata + viz files; parity tests must assert
  these are byte-identical, with the subprocess faked to produce a deterministic store.
- **`_post_study_run_*_for_test` are test seams:** keep the server name-shims so the many tests importing them
  stay green; do NOT rename in the extraction PRs.
- **FastAPI-vs-live divergence at the flip only:** these orchestrators DO the run synchronously (and the
  async ones submit to the manager); unlike the metadata committers there is no "deferred commit" — the
  FastAPI route runs the same engine. So behavior parity here is exact (no deferred-side-effect divergence).
- **`run_parca` / `model_dump` gotcha:** request models that feed `body.get(key, non-None-default)` builders
  must use `exclude_none=True` (the C-state-3c lesson) so omitted optionals keep their legacy default.
```
