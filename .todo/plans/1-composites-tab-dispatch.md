# Plan — Composition-native simulation for any pbg-template workspace

Status: **RESCOPED for the 2026-07-22 demo. Demo items A/B/C LANDED + committed (`37b35b5`); the
SP-D2 `study_runs` 409 regression found + fixed (`742e9ee`); branch is CI-GREEN under Jim's #482
full-suite gate. N3 (dirty `/workspace` → `git_pip_url`) CONFIRMED on the prod pod and RESOLVED-BY-
DESIGN → dispatch item D = option C (LOCKED, impl pending — see Decision record). Aligned with Jim's
newest doc `sms-api/docs/DESIGN-workspace-decoupling.md`. Phase 1 (PR-B, sms-api, blockers B1-B5)
owned by Jim, not deployed; deploy SERIALIZED (sms-api first). Phase 4 study work CUT (post-demo).**
Branch: `fix/composites-tab-dispatch` (PR-A, workbench — pushed to origin); `fix/compose-batch-driver-swap` (PR-B, sms-api — owned by Jim)
Owner: Alex (vivarium-workbench). Jim owns sms-api.
Repos: `vivarium-workbench` (PR-A), `sms-api` (PR-B + overlays), `pbg-template`/`sms-cdk` as needed
Deploy target: **`sms-api-stanford` / smscdk (PRODUCTION)** — direct, not stanford-test-first (Alex 2026-07-21). Deploy SERIALIZED: sms-api to prod first, then workbench; ping Jim before bumping the workbench `newTag`.

## ⚠️ DEMO HANDOFF supersedes the plan below (2026-07-21)
Authoritative for the demo: `sms-api/docs/HANDOFF-ALEX-WORKBENCH.md` (+ `PRE-DEMO-MASTER-PLAN.md`).
Demo 2026-07-22: v2ecoli `baseline` runs on smscdk **from the workbench UI**, prod `sms-api-stanford`.

**Alex's demo items (this branch) — A/B/C LANDED (`37b35b5`); D = the N3 fix (option C, impl pending):**
1. **A ✅** — SP-D stub killed: `run_core.invoke_run` returns `RunPlan(target="deployment")` (no raise);
   `run_runner._execute_remote` delegates to `remote_run.run_remote`.
2. **B ✅** — `composite_test_run_views` passes `target="deployment"` when `is_pinned_enabled()`
   (prod `REMOTE_PINNED=1`), else `None` → local dev unchanged. (Chosen over stamping `.viv-build.json`.)
3. **C ✅** — `steps` threaded → `compose_submit(interval_time=N)`, clamped `0..1000` in `run_remote`
   (sms-api hard-caps `interval_time`, which IS the steps channel; 2700 would 400).
4. **D ⏳ (N3 fix, LOCKED = option C)** — `run_remote` must NOT call `git_pip_url` on the pinned prod
   pod (`/workspace` is dirty-by-design → raises). Derive the pip URL from `resolve_pinned_build`
   instead. Last demo-path code gap on the workbench side. **See Decision record below.**

**CUT for the demo (do NOT start — post-demo, branch `fix/composites-tab-dispatch-phase4-runforms`):**
the full Phase 4 study work below (run-form unification, persistence/rehydration, G1/drop `run_parca`,
reconciliation §3.13) + gates 1/3/4/5. Highest-regression-risk, zero demo value (handoff §5).
Note: the PR-A transport `apiUrl()` work (§3.3) is likely REDUNDANT with 0.3.1's `_base_path_shim`.

## ✅ Decision record — dispatch mechanism (2026-07-21, LOCKED)

**Context.** Items A/B/C landed, but a live check of the prod pod (`workbench-67f7cbbf68-mglcf`,
`sms-api-stanford`/smscdk) found `/workspace` **DIRTY** (`M reports/index.html`, `M workspace.yaml`
— the workbench writes its own served workspace at runtime, by design; HEAD is pushed to
`origin/main` at `a08e20bd`). Our dispatch chain `invoke_run(target="deployment")` →
`run_runner.execute` (`:538`) → `_execute_remote` (`:489`) → `remote_run.run_remote` (`:505`) →
`git_pip_url` (`remote_run.py:129`, **unconditional**) RAISES on a dirty tree (`:47-54`) → the demo
Run fails BEFORE reaching sms-api (blocker **N3**). Jim's newest doc `DESIGN-workspace-decoupling.md`
§3 independently confirms *"git in the pod is broken for our purposes"* — which SUPERSEDES his 12:02
handoff §3-A (*"wire to run_remote/git_pip_url"*). So the fix follows his newer decoupling intent.

**D1 — N3 fix = OPTION C (LOCKED).** In `remote_run.run_remote`, when `remote_pinned.pinned_config()`
is set, derive the pip URL from sms-api's already-resolved **built** commit instead of local git:
- reuse `remote_pinned.resolve_pinned_build(client, cfg.repo_url, cfg.branch)` — the SAME resolver the
  pinned "Run on remote" study card already uses (`remote_run_views.py:177-207`) → `{commit, …}`;
- `pip_url = git+<repo_url>.git@<commit>`; unpinned (local dev) → `git_pip_url(ws_root)` as today;
- `.pbg` export still reads the local on-disk workspace (a read — no git-clean needed).
Rationale: no pod-git dependency (Jim's decoupling thesis); pins **by construction** to the commit
sms-api built + keyed its ParCa cache by (`a08e20bd`, image==code); reuses an existing resolver (no
new primitive); `run_remote` stays the single dispatch path; `NoPinnedBuildError` is a clean failure.
Doc-vs-code can diverge only if a composite is edited on the pod without commit/rebuild — that IS
"pinned" semantics (run the built code), fine for the demo (unedited baseline). **Rejected:** A
(fragile path-allowlist); B (keep pushed-check / drop clean-check — still touches broken pod-git);
D (ensemble `/api/v1/simulations` — vEcoli-hardwired, can't run a `.pbg`); F (server-side resolve —
crosses Jim's frozen sms-api contract).

**D2 — the gate = `is_pinned_enabled()` (LOCKED).** Option C REQUIRES `pinned_config()` to resolve,
so the gate is **structurally necessary**, not a semantic stretch: pinned-on (prod, `REMOTE_PINNED=1`)
→ C dispatches remote; pinned-off (local dev) → `git_pip_url`/local. Matches Jim's §3-B "seed of the
origin selector" (a choice, not a hard flip). Already implemented in `composite_test_run_views`.

**D3 — reconcile endpoints (Jim's decoupling-doc open decision D3 — Alex is a NAMED decider,
"Needs Alex/Eran").** Answer: reconcile composite runs against the **compose** endpoints
(`compose_status`) — they're absent from `/api/v1/simulations` (ensemble-only). Join on
`simulation_database_id`; one-way sms-api→local; never delete local-only runs (Jim's D4). POST-DEMO;
matches §3.13. Action = drop this into Jim's decision table when convenient (non-blocking).

**North star (F′, infinite-time ideal — NOT this plan; logged for direction).** The mature end-state
references a **built simulator by ID** and runs the composite INSIDE its image with **no runtime code
install at all** (image==code, cache keyed by the same id) — extending the ensemble `simulator_id`
pattern to `/compose/v1`. Two legitimate regimes the ideal serves BOTH of: (i) pinned/reproducible →
build-ID reference (F′); (ii) general/live (uncommitted or cross-workspace composite) → runtime code
delivery with **server-side** resolution (F, beats client-side C on DRY-across-clients + races +
single-source-of-truth). F/F′ need an sms-api contract change (Jim's side), so out of scope here;
**C is F′'s constrained projection onto "workbench-only, frozen contract, this week,"** and folds
naturally into Jim's decoupling plan ("the build is the reproducible unit").

**CI-gate + alignment status.** Branch already contains #482 (merge `5e06a3b`); full gate
(`scripts/pytest_gate.sh`, run with `PYTHONUTF8=1` + xdist) = **3259 passed, 2 failed**,
`known_failures.txt` UNTOUCHED (Jim's rule: only remove). The 2 failures are LOCAL-ENV-ONLY
parquet-emitter-default drift (`test_registry_default_emitter`, `test_run_runner_explorer_emitter` —
no emitter/registry code in our branch; CI baseline 3236-clean → green on CI). Our 8 new test files
all pass. Scope seams with Jim's decoupling doc are clean (it's POST-DEMO Phase-6 infra, claims the
SP2 half-switch §4.5, consumes our compose substrate via D3).

## What's next POST-DEMO (Phase 4 study work — deferred)

**→ Phase 4 is PARTIALLY done** — config-derived Origin landed (see log); the remaining 3 items are large,
interrelated, and all touch the study Simulations remote-run path (they converge on G1). Best done as a
focused pass, ideally after PR-B deploys so they're e2e-verifiable. Remaining (branch
`fix/composites-tab-dispatch-phase4-runforms`, see Phase 4 in §5):
1. Unify the study Simulations sub-tab's TWO run forms (local `btn-run-baseline`/`btn-variant-run`,
   remote `#remote-run-form`; `study-detail.html:924,966,1269`) into ONE origin selector
   (Local / Remote:<deployment>), reading origins from `/api/remote-run-config` (now carries `deployment`).
2. Reuse `save_metadata(status='running')` to write the durable `runs_meta` row at submit for REMOTE
   runs too; rehydrate in-flight remote runs on load by polling `client.compose_status`.
   [Origin part — DONE, see log.]
3. **G1 convergence** (deferred from Phase 2): converge the legacy `remote_run_jobs.py`
   `run_simulation(…, run_parca, …)` study path onto `run_remote`; drop `run_parca`.
4. Reconciliation state (§3.13): treat a persistent 404 from `compose_status` on a believed-`running`
   row as a distinct "submission may have failed" state, not infinite "running".

**Deferred within Phase 2 (do in Phase 4, where the run-form unification lives):**
- **G1 convergence** — converge the legacy `remote_run_jobs.py` `run_simulation(…, run_parca, …)` study
  path onto `run_remote`. Left for Phase 4 (§5) because it's coupled to unifying the study's two run
  forms + dropping `run_parca`; doing it now would touch the study surface prematurely.
- **Viz-landing of remote results** — `_execute_remote` currently lands `results.zip` into the run dir
  and marks the run completed, but does NOT yet unpack it into a viewable emitter store (zarr/parquet →
  charts). Marked as a follow-on in the function docstring; needs the results-contract to be verified
  against a live PR-B deploy first anyway.

**sms-api (PR-B) remaining before it's deploy-ready — LIKELY OWNED BY COWORKER (Alex 2026-07-21):**
- (a) Reconcile/commit the `job_backend` default fix (done in working tree, see log; not pushed).
- (b) Version bump (release protocol: `version.py` + `pyproject.toml` + overlays).
- (c) Deploy to `sms-api-stanford`/smscdk + run the §9 gate-1 generality test (never run against real
  Batch yet). (d) Verify the Ray-on-Batch entrypoint runs a generic 1-node `RAY_JOB_CMD` + syncs
  `RAY_OUT_DIR→RAY_OUT_S3` (assumed, unverified). (e) DB-integration tests (Docker was down locally).

**sms-api (PR-B) remaining before it's deploy-ready — LIKELY OWNED BY COWORKER (Alex 2026-07-21):**
- (a) Reconcile/commit the `job_backend` default fix (done in working tree, see log; not pushed).
- (b) Version bump (release protocol: `version.py` + `pyproject.toml` + overlays).
- (c) Deploy to `sms-api-stanford`/smscdk + run the §9 gate-1 generality test (never run against real
  Batch yet). (d) Verify the Ray-on-Batch entrypoint runs a generic 1-node `RAY_JOB_CMD` + syncs
  `RAY_OUT_DIR→RAY_OUT_S3` (assumed, unverified). (e) DB-integration tests (Docker was down locally).

## Progress log

- **2026-07-21 — Phase 0 (repro & confirm) COMPLETE.** Verified against live code:
  SP-D stub confirmed (`run_core.py:38-41` raises for `target=="deployment"`; `run_remote`
  wired only to `cli.py`, never `invoke_run`). Transport escape confirmed
  (`configure-run.js:160`, `walkthrough.js:4365` + siblings hardcode root-absolute `/api/…`;
  `data-source.js:32-34` `_base()`/`DataSource.basePath()` exists, proven at
  `walkthrough.js:1473`, but NOT applied to composite-explore run paths). Branch
  `fix/composites-tab-dispatch` pre-exists but is effectively empty (2 unrelated files).
- **2026-07-21 — Phase 1 (sms-api / PR-B) CODE-COMPLETE on `fix/compose-batch-driver-swap`.**
  `make check` green; 108 non-container tests pass (5 new). **NOT deployed or verified against real
  Batch/Postgres** — see "What's next" (a)-(e). See "Phase 1" below for per-item landing notes.
  **Correction to the plan's framing:** §3.2's "shared runner image" is NOT needed — the prebuilt
  workspace image (v2ecoli's own `v2ecoli:<commit>`, which already bundles process-bigraph +
  pbg-emitters) is reused as-is; the generic `run_pbg.py` source is embedded into the Batch job
  command via a heredoc (same trick `container_def.build_pbg_def` already uses for SLURM). Zero new
  ECR repo / sms-cdk change. The one genuinely-new schema need (SLURM int job-id vs. Batch UUID) was
  met by mirroring the EXISTING `ORMHpcRun.job_id_ext`/`job_backend` pattern onto `ORMComposeHpcRun`,
  not a new primitive.
- **2026-07-21 — `job_backend` default reconciled (branch working tree, uncommitted).** A coworker
  is editing sms-api concurrently and committed onto this same branch (`b031b48f`, `b9c766e4`),
  aligning ORM `server_default` + migration ongoing-default to `'ray'`. The remaining correctness bug
  — the migration was backfilling EXISTING legacy-SLURM rows with `'ray'`/`'batch'` (would make the
  monitor poll Batch for a SLURM int id) — was fixed in `alembic/.../e5a7c9d10f21_*.py` by splitting
  the one-time backfill (existing rows → `'slurm'`) from the ongoing default (`'ray'`, matching the
  ORM). Migration-file-only edit to avoid clobbering the coworker's in-flight ORM/deps edits. `make
  check` still green. **Not pushed** — coordinate with coworker first (shared branch).
- **2026-07-21 — Handoff note (Alex):** the coworker will *most likely* own the rest of the sms-api
  repo work (deploy, version bump, gate-1 verification). Claude to hold on sms-api and proceed to
  Phase 2 (workbench) next.
- **2026-07-21 — Phase 2 (vivarium-workbench / PR-A) CODE-COMPLETE on `fix/composites-tab-dispatch`.**
  138 relevant tests pass (7 new); 2 failures are PRE-EXISTING on the branch (confirmed by stashing —
  `test_remote_run_panel::…routes_to_visualizations`, `test_visualization_endpoints::…generator_id`),
  unrelated to this work. Landed:
  - **Item 1 (SP-D stub killed):** `run_core.invoke_run` no longer raises `RunTargetUnavailable` for the
    `deployment` target — returns a `RunPlan(target="deployment")`. The run-request now carries
    `"target"`, and `run_runner.execute` branches on it: `_execute_remote` delegates to the already-built
    `remote_run.run_remote` (export .pbg → `/compose/v1` → poll → download), writing the SAME
    `composite-runs.db` status rows so the browser's existing `/api/composite-run/<id>/status` polling
    works unchanged. **One launch path, one persistence path, one poll path** — only compute moves.
  - **Item 2 (transport):** confirmed the workbench IS served `--base-path /workbench`
    (`kustomize/base/workbench/workbench.yaml:127`), so the global `_base_path_shim` (monkeypatches
    `window.fetch`) already prefixes fetch-based `/api/` calls — but added explicit belt-and-suspenders:
    new `DataSource.apiUrl()` helper + routed every composite-explore run/resolve/status call in
    `walkthrough.js` + `configure-run.js` through it (composes safely with the shim — no double-prefix).
    Regression test `test_composite_explore_base_path.py`. `node --check` clean on all 3 JS files.
  - **Item 3 (n_steps):** `run_remote(…, n_steps=)` → `compose_submit(interval_time=n_steps)` (sms-api's
    `interval_time` IS the step count → `run_pbg.py -n`).
  - **Item 4 (origin@HEAD):** already satisfied — `run_remote` uses `git_pip_url(ws_root)` (clean+pushed
    → `git+<origin>@<sha>`); no hardcoded pin remains on the compose path.
  - **Item 5 (§3.12 version-pin):** new `remote_run.workspace_pinned_deps()` reads the workspace `uv.lock`
    for framework packages (git-sourced → `name @ git+url@sha`, PyPI → `name==ver`) and appends them to
    `extra_pip_deps`; defensive (`[]` on missing/malformed lock).
  - **NOT e2e-verifiable** until PR-B is deployed (no live `/compose/v1` to hit) — unit-level correctness
    only. G1 convergence + remote-results viz-landing deferred (see "What's next").
- **2026-07-21 — Phase 3 (characterization surfacing) CODE-COMPLETE on `fix/composites-tab-dispatch`.**
  Both items are pure frontend wiring against EXISTING endpoints — no new backend:
  - **Outputs:** the composite-explore view now folds in emitted observables via `GET /api/observables?ref=`
    (`_ceLoadCharacterization` in `walkthrough.js`, called from `_ceFetch`'s resolve-success path; renders
    `leaves` + `catalogs` into a new `#ce-outputs` panel in `index.html.j2`).
  - **Wall-time:** surfaced from the last COMPLETED run's `runs_meta` timing (`completed_at - started_at`)
    via the existing `GET /api/composite-runs?spec_id=`, keyed by the current param-signature (exact-match
    preferred, else most-recent-completed, else "unknown (no completed run yet)"). Reuses the existing
    `started_at`/`completed_at`/`n_steps` columns — no `timing_summary()` capture needed, since those
    columns already record the wall-clock the plan wanted. Rendered into `#ce-walltime`.
  - New tests: `test_composite_characterization.py` (template containers + endpoint wiring). `node --check`
    clean. Degrades quietly in snapshot mode. The 35 failures in the full workbench suite are PRE-EXISTING
    on the branch (confirmed by stashing — all `test_study_*` rendering, unrelated to this work).
- **2026-07-21 — Phase 4 PARTIAL: config-derived Origin DONE on `fix/composites-tab-dispatch`.**
  De-hardcoded `"smsvpctest"` → the config-derived deployment name (the plan's "truthful config-derived
  Origin"), end-to-end:
  - `remote_pinned.remote_deployment_name()` reads `VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT` (same env pattern
    as `pinned_config`), default `"smsvpctest"` (upgrade-safe fallback).
  - `remote_run_landing.py` records `provenance["source"]` + the run label from it (was hardcoded).
  - `GET /api/remote-run-config` now carries `deployment` in both pinned + stock modes.
  - `study-detail.js` `_initRemoteRunPinned` labels the run card "Run on remote (<deployment>)" truthfully.
  - Tests updated/added in `test_remote_run_pinned.py` (40 pass). `node --check` clean.
  - **Remaining Phase 4 (form unification, durable-at-submit remote persistence + rehydration, G1
    convergence, reconciliation state §3.13) NOT done** — large + interrelated (all converge on G1),
    best as a focused pass once PR-B is deployed for e2e. See "What's next".

- **2026-07-21 — DEMO BLOCKER B + item C cap DONE on `fix/composites-tab-dispatch`.** Closed the
  "prod Run spawns a LOCAL subprocess" blocker + the missing step-cap (handoff §3 items B & C; items A
  and the steps→compose_submit plumbing were already landed in Phase 2). Two surgical, ecosystem-native
  changes — reuse existing config, no new concept:
  - **Item B (target routing):** `composite_test_run_views.composite_test_run` now passes
    `target="deployment"` to `invoke_run` when `remote_pinned.is_pinned_enabled()` (the EXISTING
    `VIVARIUM_WORKBENCH_REMOTE_PINNED` gate the prod pod already sets — `workbench.yaml:155`), else
    `None`. Scoped to this ONE call site: `run_target_for` is untouched, so `composite_resolve` (which
    needs a `.viv-build.json` `simulator_id`) is unaffected. Prod (pinned on, no `.viv-build.json`) →
    remote compose dispatch; local dev (env unset) → `run_target_for` → `local`, unchanged. Chosen over
    stamping `.viv-build.json` (handoff's non-preferred option — routes ALL runs, incl. resolve, remote).
  - **Item C (step cap):** `remote_run.run_remote` clamps `n_steps` to `[0, 1000]` right before
    `compose_submit` — the boundary that owns sms-api's `interval_time` 0..1000 contract
    (`compose.py:121-122` 400s outside it). Placed there (not at the call site) so both the detached-runner
    and CLI `run-remote` paths are protected; local runs never hit this path, so their step counts stay
    unbounded.
  - **Tests (6 new, all green; 78 in the run/remote suite pass):** `test_composite_test_run_views_lib.py`
    — `test_pinned_mode_routes_to_deployment_target`, `test_pinned_mode_off_stays_local`;
    `test_C2_roundtrip.py` — `test_run_remote_clamps_steps` (parametrized 5000/2700/20/-3 →
    1000/1000/20/0).
  - **Still NOT e2e-verified** — no live `/compose/v1` until PR-B deploys. Deploy serialized AFTER
    sms-api is on prod; ping Jim before bumping the workbench `newTag`. Alex's demo work now CODE-COMPLETE.

- **2026-07-21 — 🔴 CONFIRMED DEMO BLOCKER (N3): the committed fix will FAIL on the prod pod.**
  Verified live against `workbench-67f7cbbf68-mglcf` (`sms-api-stanford`/smscdk, image 0.3.1):
  `/workspace` is a **DIRTY** git checkout — `git status --porcelain` returns
  `M reports/index.html` + `M workspace.yaml` (the workbench re-renders `reports/index.html`
  at serve time and touches `workspace.yaml` via dashboard actions — it writes its own served
  workspace **by design**, per vivarium-workbench CLAUDE.md). HEAD is on `origin/main` (pushed ✓),
  remote is `github.com/vivarium-collective/v2ecoli.git`.
  **Impact:** our fix routes Composites-tab Run → `invoke_run(target="deployment")` →
  `run_runner.execute` (`:538`) → `_execute_remote` (`:489`) → `remote_run.run_remote` (`:505`) →
  `git_pip_url` (`remote_run.py:129`, **unconditional**), which **raises `RuntimeError` on a dirty
  tree** (`remote_run.py:47-54`). So the Run fails BEFORE reaching sms-api — a full-red demo path.
  **Root cause:** `git_pip_url`'s clean-tree gate is incompatible with a *running* workbench pod,
  which dirties its served workspace continuously. The dirty files are **render artifacts, NOT part
  of the pip-installable package** — the installed code is fully pinned by `git+origin@<HEAD>` and
  HEAD is pushed, so the clean gate rejects a state that is actually reproducible.
  **This is also a gap in the handoff:** §3-A directed wiring to `run_remote`, and §3-B directed
  forcing `target="deployment"`; together they produce a Run that fails at `git_pip_url` on the real
  pinned pod — the handoff didn't account for the dirty-by-design workspace.
  **Fix options (decision pending — Jim):**
  - **(C, recommended)** Pinned/deployment mode derives the pip URL from `remote_pinned`'s
    sms-api-resolved built commit (`git+<REMOTE_REPO_URL>@<resolved_commit>`) — **no local git at
    all**. Honors why pinned mode exists (`workbench.yaml:152-153`: sidestep the unwritable/protected
    `/workspace`), and pins to the exact commit sms-api already BUILT + has a ParCa cache for (B1/B2
    converge on `a08e20bd8`). Most native; slightly more code.
  - **(B, minimal)** Add a pinned-aware code path that keeps the **pushed** check but drops the
    **clean** check, producing `git+origin@<HEAD>` from the pod's pushed HEAD. Smallest diff.
  - **(A, rejected)** Relax `git_pip_url` to ignore specific server-generated paths — fragile,
    workspace-specific.
  Until this lands, the demo Run cannot dispatch remotely on prod. NOT yet fixed.

- **2026-07-21 — ALIGNMENT with Jim's latest ecosystem work + CI-gate status.**
  Fresh-eyes cross-check against Jim's most recent contributions:
  - **Jim's newest doc `sms-api/docs/DESIGN-workspace-decoupling.md` (14:13, AFTER the 12:02
    handoff) independently corroborates N3:** §3 states *"git in the pod is currently broken for
    our purposes — `git -C /workspace` fails … diff/push-back impossible until `safe.directory` is
    configured."* Our N3 (dirty `/workspace` → `git_pip_url` raises) is the same root truth from the
    run-dispatch side. PVC confirmed a git checkout of v2ecoli@`a08e20bd`; prod runs-db still SQLite.
  - **The two docs conflict, and the newer wins:** handoff §3-A ("wire deployment target →
    `run_remote`", which uses `git_pip_url`) depends on pod-local git that his §3 (newer) calls
    broken. So aligning "with Jim overall" ⇒ follow the newer decoupling intent. **This is the
    decisive argument for N3 fix option C** (pinned-mode pip URL from sms-api's resolved built
    commit, no local git) over B — C is the demo-path expression of his decoupling thesis, and pins
    to the commit he already built + cached ParCa for.
  - **Scope seams match:** the decoupling doc is POST-DEMO, dev-only (`smsvpctest`), explicitly "the
    infra half of Phase 6" — exactly what this plan §0/§5 defers; it claims the SP2 half-switch fix
    (§4.5) that this plan §8 leaves adjacent. Its open decision **D3** ("which sms-api endpoint(s) to
    reconcile against — compose endpoints likely; needs Alex/Eran") consumes THIS plan's compose
    substrate (compose-runs.db + `compose_status` + §3.13). No collision; we build his prerequisite.
  - **CI gate (Jim's PR #482, `ci/full-test-suite-gate`) — branch is GREEN.** Branch already contains
    #482 (via merge `5e06a3b`). Full gate run (`scripts/pytest_gate.sh`, UTF-8 mode, xdist):
    **3259 passed, 2 failed**, `known_failures.txt` UNTOUCHED (Jim's rule: only remove). The 2
    failures are LOCAL-ENV-ONLY parquet-emitter-default drift (`test_registry_default_emitter`,
    `test_run_runner_explorer_emitter`) — our branch touches no emitter/registry code and CI's
    baseline is 3236-clean, so they pass in CI. **One real regression was found + fixed:**
    `test_study_run_baseline_on_remote_build_409` — SP-D2 removed `invoke_run`'s `RunTargetUnavailable`
    raise, which `study_runs.py` (baseline + 2 variant paths, un-converged G1) relied on for its 409;
    restored an explicit `plan.target == "deployment"` guard (commit `742e9ee`). Our 8 new test files
    all pass.

## 0. Design principles (Alex)
- **The composite is the modular, self-describing unit.** The **Composites tab characterizes** it (params, outputs/observables, wiring, measured wall-time) so you know how to use it in an existing/new study or investigation. It is not a production-sim launcher.
- **Composability is native to process-bigraph.** Studies compose composites; composites compose composites. Dependencies like `parca → baseline` are **native composition**, not workbench orchestration or a DSL.
- **Rely on native solutions** (process-bigraph, sms-api, pbg-template): **surface and wire** them; never reimplement composition, introspection, or run. **Prefer the smallest change that reuses an existing pattern over adopting a new primitive** — e.g. the ParCa cache reuses sms-api's existing commit-keyed S3 pattern (`parca_cache_uri` + stage-to-local), not a new `default_state_ref` contract. Smaller + native + reuses-an-existing-pattern is the default bar for every choice below, provided it stays production-grade and reproducible.
- **Modular, concise, flexible, scalable.**
- **Ecosystem context — this plan is one link in an existing chain, not an isolated fix.** Five repos: `sms-api` (GovCloud k8s deployment: workbench + api + ptools compute backend), `sms-cdk` (IaC for the `smscdk`/`smsvpctest` namespaces + the `sms-proxy.sh` SSM tunnel), `vivarium-workbench` (this repo — thin UI/orchestration over *a* workspace), `pbg-template` (the workspace **interface contract** — schemas, layout), `v2ecoli` (**use-case #1 only, never the design target**). **Grand target design (Alex):** spin up `sms-proxy` and create new workspaces or switch between them, remotely. This plan does **not** build that — it builds the **remote-compute slice** the grand design depends on: given whatever workspace is already active (local checkout, or a locally-materialized remote build), run its composites' compute on smscdk. It continues an existing spec lineage (`docs/superpowers/specs/`): three-plane architecture → remote-run thin-client → commit-agnostic workspace-switch (SP1 sms-api workspace export / SP2 local re-pointing / SP3 remote-build materialize+switch) → commit-agnostic remote builds → unified-run-core (SP-A/B/C landed, **SP-D1 deployment-resolve landed — this plan is SP-D2**) → remote-sourced-dashboard (WS1-4). Keep that chain intact in phrasing/decisions below rather than re-deriving it.
- **Generalization is the hinge to the grand design, not a bonus.** The infra layer has *zero* multi-workspace support today (verified): `sms-api/kustomize/base/workbench/workbench.yaml` runs exactly one single-replica pod per namespace (`replicas: 1`) against one RWO EBS PVC (`workbench-pvc.yaml`), seeded **once** from a baked-in v2ecoli image copy (a conditional `seed-workspace` init container), with `VIVARIUM_WORKBENCH_REMOTE_REPO_URL` hardcoded to v2ecoli (`workbench.yaml:157-158`); the internal ALB has one `workbenchTargetGroup` (`internal-alb-stack.ts:112`), no `/workbench/<id>` routing, no EFS, no workspace registry. **Closing that gap is out of scope here** — but this plan's choices are what let it close later without rework: dispatch by pushed `origin@HEAD` (N3) means compose-on-Batch already works for *any* workspace's **code**, not just the baked-in one — that generality is about repo-to-backend **routing**, not about **execution trust**. It does NOT mean "no security gate": §3.11 locks in enforcing the existing (currently dead) `PBAllowList`/`compose_allow_list` mechanism precisely because opening routing to any workspace, on shared Batch infra with a broad IAM grant (§8), makes an execution-side allowlist load-bearing rather than optional. Don't let a v2ecoli-shaped shortcut creep back into Phases 1-4, and don't let "generalized" get read as "unguarded."

---

## 1. The bug + defects
- Composites tab → Explore → parameterize → **Run → 404** on `sms-api-stanford`/smscdk (via the SSM tunnel `localhost:8080/workbench`).
- **(a)** study Simulations sub-tab has TWO run forms (local + remote) → should be ONE.
- **(b)** remote runs don't persist across session/navigation.
- **Requirement:** generalized + production-grade for **ANY composite in ANY pbg-template workspace**; v2ecoli is use-case #1.

## 2. Two-level root cause
1. **Transport:** the Composites-tab run posts root-absolute `/api/composite-test-run`; under the co-tenant ALB it misroutes to sms-api → 404 (`report.py:444` documents the class; base-path machinery exists but this surface escapes it).
2. **Execution never built:** even reaching the workbench, `run_core.invoke_run` **raises `RunTargetUnavailable("… not available yet (SP-D)")`** for the deployment target (`lib/run_core.py:35-41`); `/api/composite-test-run` only runs locally (in the lightweight pod). Remote composite execution is a stub.

## 3. What's native (LEVERAGE) vs what's missing (BUILD)

**Native — process-bigraph / pbg_superpowers (surface & wire, don't rebuild):**
- **Composition:** `Composite` *is* a `Process` (`process_bigraph/composite.py:1024`); `_type:'composite'` is a registered edge link (`types/process.py:223`); `bridge` wires inner ports to outer stores; the **Step network** orders dependencies from wiring (`build_step_network`, `composite.py:1275`). **`default_state_ref` + `regenerate_default_state`** (`composite_spec.py:227-254`) is the framework's own "build the heavy artifact once, reference thereafter" — its docstring **cites v2ecoli parca/baseline**.
- **Introspection:** static `CompositeSpec.to_dict()`/`discover_all` (params, emitters, viz, requires, `default_n_steps`); resolved I/O via `Composite.inputs()/outputs()` (`composite.py:2016-2022`) + `collect_input_ports` (`emitter.py:57-75`); observable leaves/catalogs via `available_observables` (`lib/observables_views.py:209-254`); **measured wall-time** via `timing_summary()`/`TimingSummary` (`composite.py:147-191, 2463-2489`).
- **Run:** `run_pbg.py` runs *any* document (`Composite(doc).run(steps)`) — nested/composed included; export captures nesting recursively (`lib/pbg_export.py`).

**Missing / to build:**
1. **Remote composite execution — client side already exists; server side is SLURM-only today (verified).** `remote_run.run_remote` (`remote_run.py:84-164`) already exports the `.pbg` (`export_composite_pbg`, `pbg_export.py:105`) and does `compose_submit` → poll → download; `run_core.invoke_run` (`run_core.py:35-44`) just needs to call it instead of raising. **But** `POST /compose/v1/simulation/run` on the sms-api side is wired to exactly ONE `ComposeSimulationService` implementation — `ComposeSimulationServiceHpc` (SLURM via SSH) — instantiated unconditionally in `_init_compose_subsystem` (`sms_api/dependencies.py:384-411`), with no backend branch at all. Stanford (`smscdk`/`smsvpctest`) has **no SLURM configured** (no `SLURM_SUBMIT_HOST` in either Stanford overlay) — hitting `/compose/v1/simulation/run` there today raises `RuntimeError("SSHSessionService 'SSHTarget.SLURM' not initialized")` **inside a `BackgroundTasks` callback, after the client already got a 200** (`sms_api/compose/handlers.py:104-119`) — a silent failure, not the 409 the plan's root-cause #2 describes. Replace the SP-D stub by delegating to the already-built `run_remote` (dashboard side), AND give the compose subsystem a Ray/Batch-backed implementation (server side — see the new item below); converge the parallel v2ecoli-shaped dashboard path (`remote_run_jobs.py` → `run_simulation(…, run_parca, …)`) onto it (G1). Net code goes *down* on the dashboard side; the sms-api side needs one new class, not zero.
2. **Generic composite on Stanford/Batch = a new `ComposeSimulationService` implementation, registered the same way the ensemble path already registers its per-backend services — not a brand-new dispatch mechanism.** `run_pbg.py` (`sms_api/compose/run_pbg.py`, 45 lines: `python run_pbg.py <doc> -o <dir> -n <steps>` → `Composite(doc).run(steps)`) already runs *any* composite — today invoked only from `ComposeSimulationServiceHpc._build_run_command` on SLURM via a Singularity `%runscript` (`container_def.py:build_pbg_def`). **The reusable pattern already exists one module over**, for the *ensemble* sim path: `_init_simulation_service` (`dependencies.py:203-252`) builds a `dict[ComputeBackend, SimulationService]` registry — `registry[ComputeBackend.RAY] = SimulationServiceRay(...)` gated on `if settings.ray_mnp_queue:` — and `compute_backend_for_repo(repo_url)` / `get_simulation_service_for_repo` (`config.py:283`, `dependencies.py:110-117`) route a request to the right backend, falling back to the deployment default (`get_job_backend()`) for any repo not in the known map. `_init_compose_subsystem` (`dependencies.py:384-411`) has **none of this** — it hardcodes one `ComposeSimulationServiceHpc()`. **The fix mirrors the ensemble path exactly:** add `ComposeSimulationServiceRay(ComposeSimulationService)` (implements the same 2-method ABC — `submit_simulation_job`, `build_container`, `compose/simulation_service.py:33-46` — reusing `SimulationServiceRay`'s per-commit image build + PAT clone (`_build_command:347`), job-def registration (`_ensure_mnp_job_def:121`), submit (`_submit_mnp:162`), S3 results sync (`_results_s3_uri:112`), and Batch-keyed status (`get_job_status:533`), swapping only the one v2ecoli-specific seam — `RAY_JOB_CMD`/`_sim_command:288` — for a generic `_compose_command() = python run_pbg.py <doc> -o $RAY_OUT_DIR -n <steps>`), then build a `dict[ComputeBackend, ComposeSimulationService]` registry in `_init_compose_subsystem` the same way (`if settings.ray_mnp_queue: registry[ComputeBackend.RAY] = ComposeSimulationServiceRay(...)`) with SLURM kept as the default-backend entry. **No new dispatch concept, no new job-def/queue (no sms-cdk change), no new results path, no separate auth** — the registry-and-fallback pattern, the executor, and the auth model are all reused verbatim; only the compose subsystem's init gains the same shape `_init_simulation_service` already has.
3. **Transport correctness** — Composites-tab run must reach the workbench under `/workbench`. Reuse the existing `_base()`/`DataSource.basePath` helper (`data-source.js:32-34,170`), today applied only in snapshot mode — extend it to live mode as `apiUrl()` and route the un-prefixed `/api/` calls (the `#page-composite-explore` set at `walkthrough.js:3933,4020,4365,4533` + `configure-run.js:68`) through it. Generalize the existing helper; don't invent a new shim.
4. **Characterization surfacing** — fold `available_observables` (outputs) into the composite view by calling the **already-built** `GET /api/observables` (`app.py:1774`; `build_observables` `observables_views.py:209` already shares the composite-resolve build path). Wall-time: reuse the native `Composite.timing_summary()`/`TimingSummary` (`process_bigraph/composite.py:2463,147`) captured into the **existing** `runs_meta` columns (`started_at`/`completed_at`/`n_steps`, `composite_runs.py:24-52`) — no new cache structure.
5. **steps/duration transport** — `run_remote` ships only `.pbg`+deps, not run length. `run_pbg.py` **already accepts `-n <steps>`**; plumb `n_steps`/`interval_time` through `compose_submit` → the existing runner CLI arg.
6. **Unified run form** + **durable-at-submit persistence** + **truthful config-derived Origin** — persistence is half-native: `save_metadata` **already** writes a durable `status='running'` row at submit for local runs (`composite_runs.py:118-139`); remote runs only persist at land (`remote_run_landing.py:78-92`). Reuse `save_metadata` for the remote path too, and derive Origin from the existing env-driven `remote_pinned.PinnedConfig` pattern (`remote_pinned.py:42-57`) instead of hardcoded `"smsvpctest"`.
7. **Study composition (native, thin)** — today a study = 1 baseline composite + param variants (`study_variants.py:41`); composition is a declarative study-DAG (`pipeline_gate.prerequisites`) with no execution piping, and `parca` is a `run_parca` boolean. Because composition is native at the composite-document layer, the change is thin: reference composed composites, drop `run_parca` — not a custom orchestrator.
8. **Durable ParCa cache (READ side) — reuse the existing S3 pattern, no v2ecoli change.** `baseline`'s `cache_dir` (`baseline.py:533`) is a local path fed to `load_cache_bundle` (`core.py:139`); sms-api already stores the cache content-addressed at `RayLayout.parca_cache_uri(commit)` (`data_layout.py:78`) via `FileService.download_file`/`upload_file`. The compose-on-Batch runner stages that S3 cache to a local scratch dir and points `cache_dir` at it — exactly the legacy Ray path's hand-off ("both the ParCa job and the sim job derive the same URI, no runtime wiring"). `default_state_ref` is the framework-blessed variant but is **NOT required**; a transparent commit-keyed runner cache is already durable, computed-once, and reused. **No v2ecoli authoring change, no new workspace-facing contract.**
9. **Results + status — reused, not built (was a "BUILD", now dissolved).** Riding `ComposeSimulationServiceRay` (§3.2), results already sync to S3 (`_results_s3_uri:112` + the entrypoint zarr sync) and status is already Batch-keyed (`get_job_status:533`), read back through `observable_reader.py`. The SLURM SCP path (`compose.py:201-209`) is bypassed — no results branch. **Residual (the one real cost of reuse) — LOCKED decision (verified, not left open):** `run_pbg.py` (`sms_api/compose/run_pbg.py:24-30`) writes one `final_state.json` snapshot via `composite.serialize_state()`; `observable_reader.py` expects zarr/parquet timeseries. Resolve by making `run_pbg.py` emit through `pbg-emitters` — confirmed already `pip install`ed into every compose container today (`container_def.py:48`), just never invoked — **not** by teaching the reader a `final_state.json` format (that would fork the results contract permanently instead of converging on the one the framework already ships). This is a single shared fix in `run_pbg.py` that benefits the **existing SLURM compose path too** (it has the identical unwired-emitter gap today), so it's not Ray/Batch-specific work — do it once, upstream of the backend split in item 2.
10. **ParCa cache — write/stage hand-off is inherited, not built (was a "BUILD", now dissolved).** `SimulationServiceRay` already runs a parca command that writes `PARCA_CACHE_DIR` → synced to S3 commit-keyed (`_parca_command:244`, `_cache_s3_uri:93`) and stages it into the sim (`:252`, "exactly what the sim stages"). In the native model the `parca` composite is just another `RAY_JOB_CMD`; the compute-once/commit-keyed/reuse hand-off comes for free. **Gap dissolved.**
11. **Execution allowlist — wire EXISTING dead code, don't invent a new gate (verified: `PBAllowList` and its table already exist, unenforced).** `sms_api/compose/models.py:226` already defines `PBAllowList(allow_list: list[str])`; `sms_api/compose/tables_orm.py:296` already provisions a `compose_allow_list` table; `run_compose_simulation` (`handlers.py:78`) already threads `pb_allow_list: PBAllowList` through the dispatch path. **Both current call sites hardcode `PBAllowList(allow_list=[])`** (`handlers.py:149,184`) and nothing reads it — `extra_pip_deps` (arbitrary strings, including `git+https://...`) flow straight into `pip install` (`container_def.py:44-56`) with zero validation today, for the SLURM path too. This matters more once Phase 1 generalizes execution to "any pushed `origin@HEAD`, no repo-to-backend allowlist" (§5, N3): that phrase is about **routing** generality (don't gate which repos get to run *somewhere*), not about **execution security** — those are different axes, and the plan must not conflate them. Fix: populate `allow_list` from the already-provisioned `compose_allow_list` table (seeded with the org(s)/hosts the deployment operator trusts — at minimum `github.com/vivarium-collective/*`, extendable) and enforce it in `_dispatch_compose_job` before any `pip install` of `extra_pip_deps`. This is the load-bearing mitigation for item 13 below (shared IAM), not an optional hardening pass — LOCKED as in-scope for Phase 1, not deferred.
12. **Version pinning for the shared runner image — reuse the existing `extra_pip_deps` lever, don't invent a pinning system.** `container_def.py:44` does `pip install --no-cache-dir process-bigraph bigraph-schema pbg-emitters` with no version pins — the compose image always floats to whatever's latest on PyPI at container-build time. A workspace pinned to a specific `process-bigraph`/`pbg_superpowers` version (its own `uv.lock`) could silently run against a mismatched framework version. Fix: `remote_run.run_remote` (dashboard side) already appends one `git+origin@sha` entry to `extra_pip_deps` (`remote_run.py:144`) — read the workspace's own pinned `process-bigraph`/`pbg_superpowers` version constraints from its lockfile and append them as additional `extra_pip_deps` entries (e.g. `process-bigraph==X.Y.Z`) through the exact same already-wired mechanism. No new pinning concept, no sms-api change.
13. **Submit/status reconciliation — the "fails bare past the response boundary" gap (verified, genuinely new but must stay minimal).** `submit_simulation`/the compose-run route returns 200 with a `simulation_database_id` **before** `perform_job` (a `BackgroundTasks` callback) ever runs (`handlers.py:104-119`); if `build_container` or `submit_simulation_job` throws inside that callback, no `ComposeHpcRun`/status row is ever inserted (`_dispatch_compose_job`, `handlers.py:196-243`), so `GET /simulation/{id}/status` 404s **forever**, indistinguishable from "not started yet." This directly undermines Phase 4's persistence design (§5): a dashboard `runs_meta` row written `status='running'` at submit (§3.6) could describe a job sms-api never actually created a record for. Minimal fix, not an outbox/queue system: (a) sms-api inserts the status row **synchronously**, before returning 200, so the 404-forever case can't occur (small, in-scope for Phase 1's driver-swap since it touches the same dispatch function); (b) the dashboard's rehydration poll (§5 Phase 4) treats a persistent 404 on a known-submitted run as its own reconciliation state ("submission may have failed silently — retry or mark failed") rather than polling indefinitely as if it were still running.

## 4. The design — three separated concerns

### A. Composites tab = characterization (surface native introspection)
Make Explore answer "what does this composite need, emit, and cost" so you can reach for it in a study:
- Params (types/defaults/choices/**descriptions/units** where available) — from `CompositeSpec`.
- **Outputs/observables** — fold `available_observables` leaves/catalogs into the composite view (call `/api/observables` from `#page-composite-explore`, or add to `composite-resolve`).
- Wiring — the loom iframe (native), transport-fixed.
- **Measured wall-time** — a light characterization test-run records `timing_summary()` into `composite-runs.db` keyed by param-signature; surface the last/estimated cost. (`/api/composite-test-run` stays the *characterization* run, now runnable locally **or** remotely.)

### B. Study / Investigation = composition + production
- **Composition is a property of composite documents — so the workbench stays thin (no composition engine, no DSL, no orchestrator).** A study references a composite that may itself be composed (native nesting / `default_state_ref` / wired Steps + Step-network ordering). `parca → baseline` is native composition authored at the composite layer, not a `run_parca` flag.
- **Production runs** go through the native compose path (§C) at full document fidelity — a composed document is just a bigger `.pbg`, run by `run_pbg.py` unchanged.
- **The thin workbench change:** run composite documents natively; let a study reference/select a (possibly composed) composite; **drop the `run_parca` boolean** and the execution-less study-DAG in favor of native composition. Included, not deferred — thin *because* it's native.

### C. Platform = run any document on smscdk (native, generalized)
- **Implement `invoke_run` deployment target** = the template-standard native path: export composite → `.pbg` (recursive, nesting-safe) + `extra_pip_deps=[git+<origin>@<sha>]` → **`/compose/v1`** → `run_pbg.py`. Replaces the SP-D stub. Works for ANY composite/workspace by construction.
- **compose-on-Batch (sms-api):** `ComposeSimulationServiceRay` (§3.2/§6 — the name `ComposeSimulationServiceBatch` was considered and scrapped; this rides the existing Ray-on-Batch MNP queue, not a new Batch construct) running the generic `run_pbg.py` in a **shared runner image** (`pip install git+origin@sha`, version-pinned per §3.12) on the existing Ray/Batch queue; Batch-keyed status; **S3 results reusing the observables/charts plumbing** via the `pbg-emitters` fix (§3.9). No v2ecoli assumptions in the routing (those live only in the legacy `/api/v1/simulations` Ray path, left untouched) — but execution IS gated by the allowlist (§3.11); "generalized routing" and "unguarded execution" are not the same thing (§0).
- **Durable native artifacts — reuse the existing S3 cache pattern.** The ParCa cache is stored content-addressed at `RayLayout.parca_cache_uri(commit)` (existing `FileService` plumbing) — computed once, reproducible, durable, reused across runs/sessions. The Batch runner stages it to a local dir and points `cache_dir` at it (the legacy Ray path's exact hand-off). **No pre-seeding, no bespoke cache, no `default_state_ref` adoption, no v2ecoli change.** If a future artifact's identity ever depends on more than the commit, extend the key to `…/<commit>/<param_hash>/` — a one-line change to the existing helper, not a new primitive.
- **Code delivery (N3):** run the workspace's own **pushed `origin@HEAD`**; precondition = pushed + clean, else a clear error. No in-pod push, no per-workspace image, no `REMOTE_REPO_URL` hardcoding. "Pinned" retires into the shared runner image + native `default_state_ref` caching. **⚠️ REVISED 2026-07-21 (option C — see Decision record):** the pinned prod pod's `/workspace` is dirty-by-design, so the "clean" precondition cannot hold there. In **pinned mode** the commit is obtained from `remote_pinned.resolve_pinned_build` (sms-api's built commit) and shipped as `git+origin@<that commit>` — same `git+origin@<sha>` delivery, commit sourced from the build registry, no local-git clean/pushed check; the resolved repo == the pinned pod's own origin, so they coincide. **Local dev (unpinned)** keeps the `git_pip_url` clean+pushed path. The repo-agnostic "any workspace" generality the original N3 wanted is the general-regime (F) north-star (Decision record), deferred to Phase 6.
- **Transport correctness, unified form, durable-at-submit persistence + truthful Origin, steps/duration transport** — as in §3.3, 3.5, 3.6.

## 5. Phases

**Phase 0 — repro & confirm** (blocking, no code): tunnel repro; confirm the SP-D stub + base-path escape; confirm the served workspace's pushed state.

> **Deploy target (Alex 2026-07-21): `sms-api-stanford` / smscdk stack (PRODUCTION) directly** — the stanford-test-first step in the original §5/§7/§9 phrasing is superseded; those references below are stale and should be read as "stanford / smscdk". A coworker is editing sms-api concurrently (shares this branch) — coordinate before push/deploy.

**Phase 1 (sms-api / PR-B) — generic composite as a driver-swap on the existing Batch executor** [critical path] — **✅ LANDED on `fix/compose-batch-driver-swap`** (make check green, 108 non-container tests pass):
- **✅ `ComposeSimulationServiceRay` (§3.2):** DONE — `sms_api/compose/simulation_service_ray.py`. Implements the `ComposeSimulationService` ABC, wrapping an instance of `SimulationServiceRay` and reusing its `_ensure_mnp_job_def`/`_submit_mnp`/Batch client verbatim. `_compose_command()` = `aws s3 cp <doc>` → heredoc-embed `run_pbg.py` → `python run_pbg.py <doc> -o $RAY_OUT_DIR -n <steps>`. **Deviation from plan (better):** does NOT build a new image — targets the prebuilt workspace image `<ray_ecr_repository>:<compose_ray_image_tag>` (new setting `compose_ray_image_tag`, default `latest`), which already carries process-bigraph + pbg-emitters. `requires_container_build=False` so the dispatch skips the singularity build+wait; `get_job_status()` maps Batch `describe_jobs` → `ComposeJobStatus`. Uploaded doc staged to S3 via the existing `FileService`.
- **✅ Registry:** DONE — `_init_compose_subsystem` (`dependencies.py`) now builds `dict[ComputeBackend, ComposeSimulationService]` exactly like `_init_simulation_service`: Ray registered when `ray_mnp_queue` set, SLURM as the default-backend entry, default resolved via `get_job_backend()`. `ComposeJobMonitor` gained a `sim_registry` arg and backend-splits polling: SLURM via squeue/SSH, Ray/Batch via each service's `get_job_status` (`describe_jobs`) — no SSH needed. **This is the actual Stanford silent-500 fix.**
- **✅ Allowlist (§3.11):** DONE — new `AllowListDatabaseService`/`AllowListORMExecutor` reads the `compose_allow_list` table; seeded once at startup from `models.DEFAULT_COMPOSE_ALLOW_LIST` (the old hardcoded router list) via `seed_if_empty` (never overwrites operator-curated rows). `run_compose_simulation` calls `_check_allow_list` synchronously → 403 before any `pip install`. Fixed the curated call sites (`run_compose_v2ecoli` was self-blocking its own trusted origin). 3 new tests.
- **✅ Output-emitter gap (§3.9):** DONE, but the real bug was bigger — `run_pbg.py`'s `Composite(document)` had NO `core=` at all, which `bigraph_schema.Edge.__init__` hard-rejects (`"must provide a core"`), so the generic runner was broken for any real document. Fixed by building a core (`allocate_core()` + `process_bigraph.register_types()`) with `pbg_emitters`' `ParquetEmitter`/`SQLiteEmitter`/`XArrayEmitter` links registered (mirrors `v2ecoli/core.py`). A document that wires its own emitter step now resolves + writes zarr/parquet; `final_state.json` kept as the always-present fallback. **NOT auto-injecting** an emitter into arbitrary documents (needs per-doc port knowledge) — the document/workspace declares its own emitter step, which is the native contract.
- **⏳ Version-pin the runner image (§3.12):** DEFERRED to Phase 2 — this item lives on the **dashboard side** (`remote_run.py:144` appends to `extra_pip_deps`), so it belongs with the workbench work, not PR-B. No sms-api change needed for it.
- **✅ Submit/status reconciliation (§3.13):** DONE — `run_compose_simulation` inserts the `ComposeHpcRun` status row synchronously before returning 200 (was in the background task); `_dispatch_compose_job` updates that same row (`update_hpcrun_dispatch`) instead of a second insert; a `perform_job` throw flips it to FAILED (`mark_hpcrun_failed`). No more 404-forever.
- **✅ Deployment advertising:** DONE — `/health` now returns `deployment_namespace` + `compute_backend` alongside `docs`/`version`.
- **✅ Schema (job-id generalization, newly-scoped during exec):** SLURM job ids are ints, Batch/Ray ids are UUID strings. Added `job_id_ext: str | None` + `job_backend: str` to `ORMComposeHpcRun`/`ComposeHpcRun`, mirroring the EXISTING `ORMHpcRun` pattern (not a new primitive). Idempotent Alembic migration `e5a7c9d10f21` (`ADD COLUMN IF NOT EXISTS`, chained off `d3f9a1c72b84`) + the required `db_reconcile` fingerprint marker (6th) + updated `test_db_reconcile.py` vectors. Also added `bigraph_schema`/`pbg_emitters` to the mypy `ignore_missing_imports` override (they're container-only deps, `run_pbg.py` never executes in the sms-api process — only its source is read + embedded).
- **⏳ Namespace targeting (UPDATED per Alex 2026-07-21):** deploy target is **`sms-api-stanford` / smscdk stack (PRODUCTION)** directly — NOT stanford-test first. Not yet deployed; code only. **Concurrency note:** a coworker is editing `sms-api` in parallel and has committed onto this same branch (`b031b48f`, `b9c766e4` — the `job_backend` default alignment); coordinate before pushing further / deploying.

**Phase 2 (vivarium-workbench / PR-A) — native remote execution + transport**:
- Wire `run_core.invoke_run` (`run_core.py:35-44`) to the **already-built** `remote_run.run_remote` (`remote_run.py:84-164`: clean+pushed → `export_composite_pbg` → `compose_submit` → poll → download) — kill the SP-D stub; converge the legacy dashboard remote path (`remote_run_jobs.py` `run_simulation(…, run_parca, …)`) onto it (G1).
- Base-path transport correctness by **extending the existing `_base()` helper** (`data-source.js:32-34`) to live mode (`apiUrl()`) and routing the composite-explore `/api/` calls (`walkthrough.js:3933,4020,4365,4533`; `configure-run.js:68`) through it; regression test: no un-prefixed `/api/` escapes under `/workbench`.
- Transport steps/duration by plumbing `n_steps` through `compose_submit` → the existing `run_pbg.py -n` arg.
- Dispatch code delivery as `git+origin@<commit>` (N3). **⚠️ REVISED 2026-07-21 (option C):** pinned mode sources `<commit>` from `remote_pinned.resolve_pinned_build` (no local git — the pinned pod's `/workspace` is dirty-by-design); unpinned/local dev keeps the `git_pip_url` clean+pushed check (`remote_run.py:28-81`). See Decision record.

**Phase 3 (vivarium-workbench / PR-A) — characterization surfacing**:
- Fold outputs into the Composites-tab view by calling the **existing** `GET /api/observables` (`app.py:1774`; `build_observables` already shares the composite-resolve build path) from `#page-composite-explore` — frontend wiring, no new backend.
- Capture the native `Composite.timing_summary()` (`process_bigraph/composite.py:2463`) into the **existing** `runs_meta` timing columns (`started_at`/`completed_at`/`n_steps`, `composite_runs.py:24-52`) keyed by param-signature; surface wall-time. Params show docs/units where available.

**Phase 4 (vivarium-workbench / PR-A) — one run surface + persistence**:
- Unify the study's two run forms (local `btn-run-baseline`/`btn-variant-run`, remote `#remote-run-form`; `study-detail.html:924,966,1269`) into one origin selector (Local / Remote:smscdk), shared with the Composites-tab dispatch; the selector reads available origins from the existing `/api/remote-run-config` surface.
- Reuse the existing `save_metadata(status='running')` (`composite_runs.py:118-139`) to write the durable `runs_meta` row at submit for **remote** runs too (today they persist only at land, `remote_run_landing.py:78-92`); rehydrate in-flight remote runs on load by polling the existing `client.compose_status` for `running` rows (the only net-new loop — `reconcile_stale_runs` currently only orphans dead PIDs, `run_registry.py:119-142`) and merging live over durable; **config-derived Origin** via the existing env-driven `remote_pinned.PinnedConfig` pattern (`remote_pinned.py:42-57`) — add `VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT`, replacing hardcoded `"smsvpctest"` (`remote_run_landing.py:58`).
- **Reconciliation state (§3.13):** treat a persistent 404 from `compose_status` on a `runs_meta` row we believe is `running` as its own surfaced state ("submission may have failed on the deployment") distinct from `running`/`completed`/`failed` — Phase 1's synchronous status-row-insert (above) narrows this window but doesn't eliminate every race (e.g. a submit that never reaches sms-api at all), so the polling loop must not treat "not found" as "still running" forever.

**Phase 5 (PR-C overlay)**: bump workbench image; set `REMOTE_DEPLOYMENT=smscdk`; drop `REMOTE_REPO_URL`. Optional pbg-template: standard `docker/` runner recipe.

**Phase 6 — workspace creation/hosting (separate plan, not started):** the infra-layer half of the grand design (§0) — multi-workspace hosting, self-service workspace creation, switching between *remote* (not locally-materialized) workspaces. Not scoped here; needs its own design pass (EFS vs. per-workspace pods vs. baked images, K8s multi-tenancy, a workspace registry). This plan (SP-D2 + compose-on-Batch) is a prerequisite for it.

**Study composition (native, thin — in PR-A):** reference composed composites; drop `run_parca`; durable ParCa cache via the existing commit-keyed S3 pattern (`parca_cache_uri` + stage-to-local). No orchestrator, no `default_state_ref` adoption, no v2ecoli-repo change.

**Sequencing:** PR-B → deploy → PR-A (Phases 2-4 + native study composition) → PR-C. No hotfix (D5).

## 6. Decisions
**LOCKED:** D5 (no hotfix, one converged change); GENERALIZED requirement; G1 **(refined) — the universal runner and `/api/v1/simulations` ride the SAME Batch executor and registry-selection pattern, with a different job command.** Verified: `/compose/v1/*` today is wired to exactly one `ComposeSimulationServiceHpc` instance (no registry at all, `dependencies.py:384-411`) — the ensemble path's `_init_simulation_service` registry (`dependencies.py:203-252`) is what gets extended to compose, not something already shared between the two. Generic `run_pbg.py` is the driver for any composite; the vEcoli ensemble driver is the specialization. We do NOT converge on the `/api/v1/simulations` *endpoint* (it's vEcoli-hardwired — `simulator_id`/`config_filename`/`num_generations`/`run_parca`, `sms.py:185-301`; its `composite` param is a 2-value engine enum, not a composite document), but we DO reuse the Batch *machinery* beneath it. Implementation shape for `ComposeSimulationServiceRay` (extract `SimulationServiceRay`'s image-build/job-def/submit/results/status seams into shared helpers vs. one class satisfying both ABCs) is an implementation-time call, not re-litigated here — either way, zero sms-cdk change, same underlying Batch executor. N2 (S3 + observables results); N3 (dispatch `git+origin@<commit>` — **routing** is unrestricted by repo identity; **execution** is gated by the allowlist in §3.11, a distinct axis. **REVISED — option C:** in pinned mode the commit is sourced from `resolve_pinned_build`, not the local git clean+pushed check — the pinned pod is dirty-by-design; the repo-agnostic general regime is the F north-star, deferred. See Decision record). **SCRAPPED:** N1 pre-stage DSL (ParCa is a native composite; cache-write/stage inherited from the Batch executor); a **separate** `ComposeSimulationServiceBatch` + new Batch job-def/queue (superseded by the driver-swap reuse above); converging on the `/api/v1/simulations` endpoint itself.

**RESOLVED — production-grade native (Alex rejected cut-corner options):**
- **P1 → Composition is native; the workbench stays thin.** No MVP/roadmap split, no composition engine. A study references a (possibly composed) composite; composition lives in composite documents; `run_parca` + the execution-less study-DAG are dropped for native composition. Included in PR-A.
- **P2 → `baseline`'s ParCa cache reuses the existing commit-keyed S3 pattern** (`RayLayout.parca_cache_uri` + `FileService` stage-to-local) — the same hand-off the legacy Ray path already uses (computed once, reproducible, reused). **No pre-seeding, no v2ecoli authoring change, no `default_state_ref` adoption.** `cache_dir` stays exactly as authored; the runner materializes the S3 cache to a local dir before the run. `default_state_ref` remains available as a later framework-blessed refinement, not a prerequisite. Extend the S3 key with a param hash only if a param ever changes the baked artifact — a one-line helper change.
- **P3 → characterization runs are capability-aware:** cheap composites characterize locally, heavy ones on smscdk; the durable characterization (`timing_summary` wall-time, `available_observables` outputs) is captured from the run and cached by param-signature.
- **P4 → composite output format is `pbg-emitters`, not a new reader format (§3.9, locked).** Rejected: teaching `observable_reader.py` a second `final_state.json` shape — that permanently forks the results contract instead of converging on the mechanism the framework and the image already ship.
- **P5 → execution allowlist is in-scope for Phase 1, not deferred hardening (§3.11, locked).** Rejected: shipping "any pushed origin, no allowlist" as the production posture — the existing `PBAllowList`/`compose_allow_list` table gets wired now, both because it's a two-line reuse of dead code and because it's the only mitigation for the shared `RayBatchJobRole` S3 grant (§8).

## 7. Test plan
- A **non-v2ecoli** fixture workspace run end-to-end through compose-on-Batch (proves generality).
- Base-path: no un-prefixed `/api/` escapes under `/workbench`.
- Native leverage: characterization surfaces `available_observables` + a cached `timing_summary`; a composed document (two wired composites) runs via `run_pbg.py` unchanged.
- Persistence: Origin=`smscdk`, survives reload/nav/restart.
- **Allowlist enforcement (§3.11):** a compose submit whose `extra_pip_deps`/origin is NOT in `compose_allow_list` is rejected before any `pip install`/container run — negative test, not just the happy path.
- **Output shape (§3.9):** a compose run's results are readable by `observable_reader.py` unchanged (zarr/parquet via `pbg-emitters`), on both the SLURM and Ray/Batch backends.
- **Reconciliation (§3.13):** simulate a background-task dispatch failure (fake a `build_container`/`submit_simulation_job` throw) and confirm the status row still exists (sms-api side) and the dashboard surfaces a distinguishable "submission failed" state rather than polling `running` forever.
- Manual: tunnel repro on `sms-api-stanford`/smscdk (PRODUCTION — per Alex 2026-07-21, deploying straight to prod, not stanford-test first).

## 8. Risks
- **PR-B is a new `ComposeSimulationServiceRay` + registry-extension on the existing Batch executor, not new backend work** — reuses image-build/job-def/S3-sync/status/ParCa-hand-off from `SimulationServiceRay`; the critical path is `_compose_command()` + the registry extension (§3.2) + the output-emitter fix (§3.9) + the allowlist wiring (§3.11). The 404/silent-500 stays until PR-B ships+deploys (accepted, D5).
- **SP-D was a known-unbuilt stub** — remote composite exec is net-new, not a regression fix.
- **Study composition touches the study model** — dropping `run_parca` and referencing composed composites must not break existing single-composite/variant studies; native composition is additive, migrate cleanly.
- **ParCa cache reuse is commit-keyed** — correct as long as nothing in baseline's runtime knobs changes what ParCa bakes into the bundle (true for v2ecoli today: ParCa output is commit-determined). If that changes, extend the S3 key with a param hash. No v2ecoli change and no `default_state_ref` needed for the durable, reproducible dependency.
- **Wall-time is measured, not estimated** — first characterization run is uncached (cold cost shown as "unknown").
- **The one real coupling — composite output format** (§3.9, now a locked decision, not an open "or"): `run_pbg.py` writes `final_state.json` but `observable_reader.py` expects zarr/parquet timeseries. Resolved by wiring the already-installed `pbg-emitters`. The Ray-shape gate (`sms.py:76-97`) + `seed_store_uri` (`data_layout.py:65`) are the only other v2ecoli-coupled bits.
- **Widened blast radius on shared infra, verified (§3.11/§3.13):** `RayBatchJobRole` grants `sharedBucket.grantReadWrite` to *any* job on the queue (`ray-batch-stack.ts:124`) — today that's only ever vetted first-party repos; generalizing to "any pushed `origin@HEAD`" with `extra_pip_deps: git+<any-origin>` and **zero enforcement of the existing `PBAllowList`** (both call sites hardcode it empty, `handlers.py:149,184`) would mean arbitrary public-repo code runs with read/write to the same bucket other tenants'/commits' ParCa caches and results live in. **This is why §3.11's allowlist wiring is LOCKED in-scope for Phase 1, not a follow-up** — re-scoping IAM per-workspace (narrower buckets/prefixes) is a bigger sms-cdk change and stays out of scope; the allowlist is the accepted, minimal mitigation.
- **No auth on `/compose/v1/*` or `/api/v1/simulations` today** (verified: no `Depends(auth)`/API key anywhere in `compose.py`/`sms.py`, only CORS middleware) — accepted as the existing risk model (reachable only via the authenticated SSM tunnel, not public internet); this plan doesn't change that posture, but doesn't get to claim "production-grade" credit for auth it doesn't have either — namespace targeting (§5) and the allowlist (§3.11) are the actual controls in place.
- **No cost/size guard beyond the existing fixed `ray_num_nodes` MNP shape** — every compose-on-Ray job uses the same fixed-size cluster the ensemble path uses (inherited, not missing), but there's no per-request runtime cap or submission rate limit. Accepted for MVP (internal tool, tunnel-gated, single operator) — flag as a real gap if this ever gets exposed more broadly, not solved here (avoid scope creep).
- **No version pinning for `process-bigraph`/`bigraph-schema`/`pbg-emitters` in the shared image today** (verified, `container_def.py:44` — unpinned `pip install`); §3.12 threads the workspace's own lockfile-pinned versions through the existing `extra_pip_deps` mechanism.
- Push precondition (N3): unpushed/dirty in-pod edits error by design.
- Cross-repo coordination; CSRF allowlist covers the run POST origin.
- **Adjacent, not in scope — don't conflate:** SP1 (sms-api workspace export), spec'd 2026-06-23 as an SP3 dependency, **EXISTS and is wired end-to-end (verified 2026-07-21)** — `sms_api/api/routers/sms.py:137 export_simulator_workspace` streams `GET /api/v1/simulations/workspace` as `workspace-sim{id}-{commit}.tar.gz`, consumed by the workbench's `remote_build_source.materialize_build` → `client.download_workspace` → extract → `active_workspace.switch_workspace` (the Scope=Remote → `switch-build` flow). So SP3's remote-build switch **is** backed; the earlier "doesn't appear to exist" note was stale. Not on this plan's critical path, but a green light for the Phase-6 grand design. Separately, `/api/source/switch` (SP2, local workspace re-pointing) is a known **half-switch** — re-points `WORKSPACE`/caches but leaves CWD/`sys.path`/`sys.modules` stale (`ARCHITECTURE-DEEP-DIVE.md:223,272`) — a workspace-*switch* bug, distinct from this plan's transport 404 (§1) and SP-D2 execution gap (§2).

## 9. Definition of done (MVP acceptance)

MVP scope = **the remote-compute slice for the currently-active workspace** (local checkout or locally-materialized remote build). Multi-workspace hosting/creation/switching is Phase 6, explicitly out (§0, §5).

Ship gates, in order:
1. **Generality gate (proves the platform):** the §7 non-v2ecoli fixture composite runs end-to-end via compose-on-Batch on smscdk (on `sms-api-stanford` / PRODUCTION — per Alex 2026-07-21) — submit → Batch-keyed status → **S3 results** read back through `observable_reader.py`. Exercises §3.1/§3.2/§3.9, *not* the ParCa glue.
2. **Transport gate:** Composites-tab Explore → parameterize → Run reaches the workbench under `/workbench` (no un-prefixed `/api/` escape); the original 404 is gone.
3. **Characterization gate:** the composite view surfaces `available_observables` outputs + a measured `timing_summary` wall-time (cold = "unknown", warm = cached by param-signature).
4. **Persistence gate:** an `Origin=smscdk` run shows a durable `runs_meta` row **at submit**, and an in-flight remote run rehydrates across reload/nav/server-restart (live merged over durable); a background-task dispatch failure surfaces as a distinguishable reconciliation state (§3.13), not an infinite "running" poll.
5. **One-surface gate:** the study Simulations sub-tab has exactly one run form with an origin selector (Local / Remote:smscdk); `run_parca` and the execution-less study-DAG are gone; existing single-composite/variant studies still run.
6. **Security gate (new — closes the widened-blast-radius risk in §8):** a compose submit whose origin is not in `compose_allow_list` is rejected before container execution (§3.11); `deployment_namespace`/`compute_backend` are visible on `/health` (§ Phase 1) so a deployment's backend identity is no longer guesswork.
7. **Use-case-#1 gate (headline demo):** v2ecoli `baseline` runs to completion on smscdk. The ParCa cache write/stage hand-off is **inherited** from the Batch executor (§3.10) — what remains is running the `parca` composite then `baseline`, riding the already-resolved output-emitter fix (§3.9). Smaller than gates 1–6 now, not larger.

**MVP = gates 1–6** (generalized remote compose via the registry-extension + a new `ComposeSimulationServiceRay`, characterized + persisted + reconciled, one surface, allowlist-enforced). **Gate 7 = the v2ecoli demo on top**, and it's no longer the scope-unknown it looked like before the reuse pivot.
