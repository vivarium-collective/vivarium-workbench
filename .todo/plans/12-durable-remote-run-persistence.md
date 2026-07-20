# Plan 12 — Durable remote runs: make sms-api dispatches first-class composite runs

## Name

Fix/Feat: remotely dispatched sms-api runs never appear in the Simulations DB
tab and do not survive a session — give them the same durable-record-first
`runs_meta` lifecycle that local composite runs already have, plus a remote
reconciler that auto-lands completed sims on boot.

Linked tasks: surfaced while executing the fully-remote variant of the demo
protocol in `demos/v2ecoli/WALKTHROUGH-local-remote-compute.md` (Plan 11's
artifact). Builds directly on the pinned-build remote-run model from #5 and the
remote-run progress UX from #7 — both of which drive the same
`remote_run_views` submit→poll→land flow this plan makes durable. Dashboard-repo
only (`vivarium-workbench`); **no sms-api change and no v2ecoli change** —
sms-api already exposes everything needed (`simulation_status`, `download_data`).

## Status: 📋 PLANNED (via `/plan`, 2026-07-20) — awaits explicit "proceed"

Mirror of the approved design at `~/.claude/plans/ancient-nibbling-kahan.md`.
Three design forks were resolved with the user before finalizing:

- **Record home** — the user rejected the either/or framing ("any simulation run
  associated with a study IS in fact a composite run; thus it should be in
  sync"). Resolved as: one row, one home (`studies/<slug>/runs.db`), reusing the
  *identical* `runs_meta` contract as a local study composite run. Sync is
  satisfied structurally by `simulations_index._discover_dbs`, which already
  unions the central and per-study DBs into one index — not by duplicating rows.
- **Auto-land = yes** — reconcile flips status *and* downloads/lands the store,
  so a run dispatched in a prior session materializes with zero clicks.
- **Scope = persistence + index gaps** — includes the three separate index bugs
  that also hide *already-completed* work (WS-5).

## Problem / context

Running the full v2ecoli demo protocol with fully-remote sms-api runs exposes
two defects:

1. Remotely dispatched runs **never appear in the Simulations DB tab**.
2. Those runs **do not persist across sessions** — a server restart, page
   reload, or tab close permanently orphans them.

Both have one root cause. The local run path is **durable-record-first**: a
`runs_meta` row is written with `status='running'` *before* work starts
(`lib/composite_test_run_views.py:119`, `lib/study_runs.py:123-125`), a pid is
recorded (`run_registry.spawn_detached` → `cr.set_pid`), and
`run_registry.reconcile_stale_runs` repairs crash-orphans on boot
(`lib/startup.py:84`).

The remote path **inverts** that contract. Its `runs_meta` row is created only
after a successful *manual* "Land results" click, and is written
already-terminal — `save_metadata` immediately followed by `complete_metadata`
with `n_steps=0` (`lib/remote_run_landing.py:81-90`).

Between dispatch and landing there is **no server-side record of any kind**:

- `remote_run_submit` (`lib/remote_run_views.py:213`) fires
  `client.run_simulation(...)`, returns `{simulation_id, phase:"running"}`, and
  writes nothing. Its own docstring asserts "durability lives in sms-api's
  Postgres, not an in-process manager" (`:119-125`) — true for the *simulation*,
  false for the *workspace's knowledge of it*.
- The `simulator_id` / `simulation_id` live solely in the browser's
  `var _remoteRunState = {}` (`static/study-detail.js:1702`). There is **no
  `localStorage` / `sessionStorage`** anywhere in that file.
- The Simulations index enumerates five on-disk sources
  (`lib/simulations_index.py:678-693`); an in-flight remote run is in none of
  them.

So an in-flight remote run is invisible *and* unrecoverable: `remote_run_land`
(`:241`) requires a caller-supplied `simulation_id` that, after a reload, exists
nowhere on the machine.

Legacy Lane A (`remote_run_jobs.manager`, `:120`) is worse still — an in-memory
`dict[str, RemoteRunJob]` with daemon worker threads, no persistence, and **no
reconcile hook in `startup.py`**. A restart mid-pipeline loses the `job_id` and
the `land` step never fires; `GET /api/remote-run-status` then 404s
(`lib/job_status_views.py:52`).

## Key facts (verified by direct inspection, not assumed)

- `runs_meta` DDL + migrated columns: `run_id, spec_id, label, params_json,
  started_at, completed_at, n_steps, status, sim_name, generation_id,
  emitter_path` + `pid, progress_step, log_path, heartbeat_at`
  (`lib/run_registry.py:32`, `lib/composite_runs.py:18-52`).
- The **Origin** column's `remote_origin` decoder (`lib/models.py:120-160`)
  requires *both* `source` and `simulation_id` present in `params_json`.
- `_discover_dbs` (`lib/simulations_index.py:80-107`) is an explicit list, not a
  glob: `.pbg/composite-runs.db`, `.pbg/default-baseline/runs.db`, and every
  study `runs.db` from `_iter_all_study_dirs` (`:44-78`) — which covers **both**
  `studies/<slug>/` and `investigations/<inv>/studies/<slug>/`, honouring
  `workspace.yaml`'s `layout:` map via `WorkspacePaths`.
- v2ecoli's `workspace.yaml` **does** relocate the layout
  (`layout.studies: workspace/studies`), so root-level `studies/` is shadowed.
- `.pbg/composite-runs.db` in v2ecoli holds 18 rows; every study `runs.db`,
  `parquet-runs/`, and `.zarr` under `workspace/studies/` is **absent** — the
  tab's remote content today is entirely those 18 demo rows.
- `copy_run_to_new_db` (`lib/composite_runs.py:754`) is a **one-shot promotion**
  primitive used only by `lifecycle_mutations` (`:276,:330`). Its plain `INSERT`
  is not idempotent — it is *not* a mirror and must not be repurposed as one.
- `_TERMINAL_OK` / `_TERMINAL_BAD` already exist (`lib/remote_run_views.py:42-43`).
- Local study runs deliberately do **not** append to `study.yaml` `runs[]` —
  "runs.db is the single source of truth" (`lib/study_runs.py:180-184`). This
  plan preserves that invariant.

### Out of scope, explicitly

- **Lane A deletion.** The in-process `RemoteRunManager`
  (`lib/remote_run_jobs.py:120`) behind `POST /api/remote-run-start`. Its own
  docstring flags it for deletion once the JS panel cut over (it has). Not on
  the demo path; a separate cleanup.
- **Lane C.** `cli.py run-remote` → `lib/remote_run.py` (the `.pbg` compose
  lane) lands to `.pbg/remote-results/results.zip` and no DB at all. Untouched.
- The dirty `pyproject.toml` / `uv.lock` in the working tree (per Plan 10).

## Design principle

**A remote run of a study is a composite run of that study.** It gets no
parallel bookkeeping lane. It goes through the same `runs_meta` lifecycle, in
the same home, as a local study composite run.

The only thing that legitimately differs is the **liveness oracle**:

| | local | remote |
|---|---|---|
| identity | `pid` | `simulation_id` |
| liveness | `_pid_alive(pid)` | `SmsApiClient.simulation_status(id)` |
| reconcile | `reconcile_stale_runs` | `reconcile_remote_runs` (new, same shape) |
| repair net | `backfill_runs.py` | (this plan's reconciler) |

Id generation, row schema, status vocabulary, reconcile-on-boot, and index
enumeration are all reused verbatim.

## Workstreams

### WS-1 — Record the dispatch (`lib/remote_run_views.py`)

Make `remote_run_submit` write a pending `runs_meta` row after a successful
sms-api submit.

- Reuse `cr.generate_run_id(spec_id, params=provenance)` + `cr.save_metadata(...)`
  — the same two calls `land_remote_run` already makes.
- Home: `study_spec.study_dir(ws_root, study) / "runs.db"`, resolved exactly as
  `remote_run_land` does (`:261`).
- `params_json` must carry `source` **and** `simulation_id` (required by
  `lib/models.py:120-160` for the Origin column). Full set: `{source,
  simulation_id, simulator_id, experiment_id, commit, branch, backend}`.
  Deliberately omit `store_path` — WS-3 sets it at land time.
- Return `run_id` alongside `simulation_id` so the client can address the row.
- Apply the `CONCURRENCY_CAP` gate the local path uses
  (`run_registry.count_running`, `composite_test_run_views.py:86`) — remote runs
  have no cap today.
- Derive `spec_id` from the study's baseline composite **identically** to
  `remote_run_land` (`:255-256`) so the pending and landed rows agree.

### WS-2 — Remote reconciler + startup hook (new `lib/remote_run_registry.py`)

Deliberate analogue of `lib/run_registry.py` — same shape, same conventions.

- `list_pending(ws_root) -> list[PendingRemoteRun]` — sweep every run DB via
  `simulations_index._discover_dbs(ws_root)` (**reuse it; do not re-glob**),
  select `status='running'`, keep rows whose `params_json` decodes a remote
  `source` + `simulation_id`.
- `reconcile_remote_runs(ws_root, client=None) -> int` — per pending row, call
  `client.simulation_status(simulation_id)` and map through the **existing**
  `_TERMINAL_OK` / `_TERMINAL_BAD` sets (lift them to a shared home; do not
  copy):
  - queued/running → leave alone
  - terminal-bad → `cr.complete_metadata(status="failed")`
  - terminal-ok → hand to the auto-land path (WS-3)
  - **`SmsApiError` → leave the row `running`; never mark failed.** A down
    tunnel or expired SSO must not destroy a good run's record. Mirrors how
    `_pid_alive` treats `PermissionError` as alive (`run_registry.py:85-87`).
- Wire into `lib/startup.py` beside `reconcile_stale_runs` (`:82-89`), in the
  same non-fatal `try/except` — boot must never block on a network call. Because
  auto-landing may download a multi-GB tar, run the sweep on a **daemon thread**
  after the readiness file is written, not inline.

### WS-3 — Idempotent landing (`lib/remote_run_landing.py`)

`land_remote_run` currently *generates* a fresh `run_id` (`:61`); with a pending
row present that would duplicate. Add an optional pre-existing id:

```python
def land_remote_run(study_dir, *, spec_id, simulation_id, experiment_id,
                    commit, tar_path, seed=0, label=None, s3_uri=None,
                    run_id: str | None = None) -> str:
```

- `run_id=None` → generate as today (manual-land path + existing tests stay
  behaviour-identical).
- `run_id` supplied → skip `save_metadata`; extract, place the store, merge
  `store_path` into the existing `params_json` (add a small `update_params`
  setter to `composite_runs.py` alongside `set_pid`/`update_progress` if none
  exists), then `cr.complete_metadata(status="completed")`.
- Write an intermediate `status='landing'` before the download, so a crash
  mid-download is distinguishable from a crash mid-simulation and the tab can
  show the download in progress. Re-entry is naturally safe — landing is already
  destructive-then-rebuild (`shutil.rmtree` + `copytree`, `:73-75`).

### WS-4 — Client resume (`api/app.py`, `static/study-detail.js`)

- Add `GET /api/remote-runs?study=<slug>` backed by
  `remote_run_registry.list_pending`, returning in-flight remote runs with
  `run_id` / `simulation_id` / `simulator_id` / phase.
- On study-detail load, hydrate `_remoteRunState` (`:1702`) from that endpoint
  instead of starting empty, and resume `_pollPhase` (`:1908`). A reload
  mid-run then reattaches rather than orphaning.
- `_submitRemoteRun` (`:1854`) stashes the `run_id` WS-1 now returns.

### WS-5 — Simulations index gaps

Three independent bugs hiding *already-completed* work; each small and
separately testable.

1. **`.pbg/parquet-runs` is never enumerated.** `_discover_parquet_hives`
   (`lib/simulations_index.py:443`) only walks `<study_dir>/parquet-runs`.
   v2ecoli has **7 real hives** under `.pbg/parquet-runs/` rendering nothing.
   Extend the enumerator to include the `.pbg` location, attributing study via
   the hive's `configuration` columns (`study_slug` / `investigation_slug`) as
   `_read_parquet_hive` (`:470`) already does.
2. **`study.yaml` runs keyed on `simulation_id` are silently dropped.**
   `_read_study_yaml_runs` (`:328`) and `_study_yaml_run_ids` (`:283`) both key
   on `run_id or name`. Entries shaped `{simulation, simulation_id, artifact,
   ...}` — e.g. **all 10** runs in
   `workspace/studies/mbp-02-population-aggregation/study.yaml` — have neither
   and vanish. Accept `simulation_id` as an id fallback and `artifact` as a
   store-path fallback.
3. **`_append_remote_simulations` is gated on `.viv-build.json`.**
   `lib/remote_simulations.py:137` returns `[]` unless the workspace is a
   materialized remote build, so a plain workspace never sees live remote sims.
   Once WS-1 lands, in-flight runs come from `runs_meta` and this read-through
   becomes a redundant second source. Dedup today is by `run_id` (`:702-745`),
   which will **not** catch a duplicate arriving under a different key. Keep the
   read-through only for sims with no local `runs_meta` row, and de-dup on
   `simulation_id`.

## Verification

All test runs **scoped** — never the bare full suite, it hangs
(`[[feedback_never_run_full_pytest]]`).

```bash
pytest tests/test_remote_run_views.py tests/test_remote_run_landing.py -x
pytest tests/test_simulations_index.py -x
pytest tests/test_backfill_runs_mirror.py -x   # drift guard on RUNS_META_DDL
pytest -k "remote or simulations" -x
```

New tests:
- `remote_run_submit` writes exactly one `running` row whose `params_json`
  decodes to a `remote_origin` under `lib/models.py`'s rules.
- `reconcile_remote_runs` against a fake `SmsApiClient`: completed → landed +
  `completed`; failed → `failed`; still-running → untouched; **`SmsApiError` →
  untouched** (the critical no-data-loss case).
- `land_remote_run(run_id=...)` updates in place, creates no second row, and is
  idempotent when called twice.
- A pending remote row appears in `build_simulations_data` with the correct
  `study_slug` and Origin.

End-to-end against the real v2ecoli workspace (tunnel up, `sms-proxy.sh -s
smsvpctest`):

1. `vivarium-workbench serve --workspace ~/vivarium-app/v2ecoli`
2. Dispatch a remote run from a study's Remote panel.
3. **Immediately** open the Simulations DB tab — the run must be listed as
   `running` with Origin = remote, *before* any landing.
4. Kill the server mid-run; confirm the row survives:
   `sqlite3 workspace/studies/<slug>/runs.db 'select run_id,status,params_json from runs_meta'`
5. Restart. The reconciler must pick the run up, auto-land it, and the tab must
   show `completed` with a `store_path` and working charts — **no clicks**.
6. Regression: repeat 2–5 with the sms-api tunnel **down**. The row must stay
   `running` and the server must boot normally with only a warning.
7. WS-5: the 7 `.pbg/parquet-runs` hives and the `mbp-02` yaml runs appear in
   the tab, and no `simulation_id` is listed twice.
