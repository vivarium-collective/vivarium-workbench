# RunBackend — durable run execution

Design spec for the **`RunBackend`** port: how a simulation run is submitted,
tracked, and landed — one interface over a detached local process and a remote
(sms-api → Batch) job, replacing today's three divergent execution paths.

Context: `docs/REFACTOR-PLAN.md` **§2A.1** (the `RunBackend` port), **§2A.2**
(a run is a *binding*), **§5B Phase 2**. Neighbours: `EnvironmentResolver` (the
interpreter/image a run executes in), `WorkspaceStore` (the source a run is
against), and `RunStore` (reads a run's outputs — a separate Phase-3 spec). A run
is **not** an env-worker query — the worker answers interactive questions; a run
is a detached job (env-worker spec §12).

Status: **proposed** (spike). Phase 2. Not yet implemented.

---

## 1. The current mess — three paths, one job

A "run" executes three different ways today, which is the audit's "two engines +
remote impls" liability:

1. **Composite runs — detached & durable (the good model).**
   `run_registry.spawn_detached` → a fully-detached `cli run-composite --request
   <file>` → `run_runner.execute` (pure: reads the request file, writes
   `studies/<slug>/runs.db`). Reconciled on startup — but *only*
   `composite-runs.db`.
2. **Study runs — synchronous & blocking (the bad model).**
   `study_runs.run_study_baseline/variant` runs **inline on the HTTP request**,
   blocking the worker up to `subprocess_timeout_s` (default **1800 s**). It
   shells the sim out (`composite_subprocess`, `python -c`) but then renders viz
   and runs **v2ecoli analyses in the HTTP process** (`render_study_visualizations`,
   `run_study_analyses`).
3. **Remote (sms-api) — in-process threaded polling (the fragile model).**
   `remote_run.run_remote` exports a `.pbg`, `SmsApiClient.compose_submit`s it,
   and **polls in a daemon `threading.Thread`** (`remote_run_jobs` /
   `run_jobs`, an in-memory `_jobs` dict). The poll loop and its state are **lost
   on restart**.

Same underlying job — *simulate, emit, (analyze, render)* — expressed three
incompatible ways. `RunBackend` is the one port; local and cloud are its two
adapters.

## 2. Goals / non-goals

**Goals**
- **No run blocks an HTTP request.** Submit returns a `run_id` immediately;
  progress is polled.
- **Runs survive a restart.** Durable state (runs.db + the JSONL run log) is the
  truth; in-process managers are only progress caches, reconciled at boot.
- **Heavy analysis runs in the *job*, never the pod** (§2A.7 / env-worker §12):
  AWS Batch for cloud, the detached subprocess for local.
- **One port, two adapters** — local (detached process) and cloud (sms-api →
  Batch) — differing only in *where* the job runs, not in the contract.
- Each adapter runs the job in the **right environment** (`EnvironmentResolver`):
  local = the per-workspace venv (not `sys.executable`); cloud = the
  `(repo, commit)` image.

**Non-goals (v1)**
- Reading run outputs — that's `RunStore` (Phase 3). `RunBackend` produces an
  `outputs_uri`; `RunStore` reads it.
- A general workflow/DAG engine — a run is one composite execution + its
  post-processing, not an arbitrary pipeline.
- Per-run autoscaling policy — that's sms-api's / Batch's concern below the port.

## 3. A run is a *binding* (§2A.2)

`submit` takes a binding, not a pile of loose args — the same triple that defines
reproducibility:

```
RunSpec {
  source_version : str          # WorkspaceStore handle's resolved commit (science version)
  composite_ref  : str          # generator ref or resolved .pbg document
  env_coordinate : str          # EnvironmentResolver: venv key / (repo,commit) image
  params         : dict         # overrides / variant parameters
  steps          : int
  emitter        : str          # parquet | xarray | sqlite (the #479 emitter work)
  analyses       : [str]        # post-run analyses to run AS PART OF THE JOB
  study_slug     : str | None   # where results land (runs.db)
}
```

**Reproducibility = `(source_version) + (env_coordinate) + (params)`** — three
pins, not one fused `repo@commit` (§2A.2). The result is a **run binding**
recorded durably: `{ RunSpec, run_id, status, outputs_uri, provenance }`. The
`provenance_manifest` already records almost exactly this.

## 4. The port

```
submit(spec: RunSpec) -> run_id            # detached local / sms-api submit; returns immediately
poll(run_id)          -> RunStatus         # from DURABLE state, never an in-mem thread
cancel(run_id)        -> None              # kill local pid / sms-api cancel
list(workspace)       -> [RunStatus]       # the runs index
```

`RunStatus = { run_id, phase: queued|running|analyzing|done|failed|orphaned,
progress?, outputs_uri?, error? }`. Outputs are read via `RunStore`, addressed by
`outputs_uri` — never returned inline.

## 5. The job's internal shape — simulate → emit → analyze → (render)

One job, whichever adapter runs it:

1. **Resolve** the composite state (against the run's `env_coordinate` — the venv
   or image; the same `build_core`/registry the env worker uses, but in the
   *job's* process, not the worker's).
2. **Simulate** `steps` ticks, **emitting** to the declared sink (parquet / zarr /
   sqlite — the #479 emitter work) → `outputs_uri`.
3. **Analyze** — run `spec.analyses` (v2ecoli `ANALYSIS_REGISTRY`) over the
   emitted output. **This is the step that must not run in the pod** — it is part
   of the job here, on Batch (cloud) or in the detached subprocess (local).
4. **Render** (eventually) — heavy viz moves here too; light preview stays an
   env-worker query (env-worker §12, "viz straddles"). Deferred; split as it comes.

The job writes results + a terminal event to the durable store; `poll` reads that.

## 6. Local adapter — the detached request-file model, generalized

Engine A's model, extended and made the *only* local path:

- **Detached**, request-file driven (`run_runner.execute` is already pure —
  reads everything from the file, survives the server). Submit writes the request
  and spawns; returns the `run_id`.
- **Runs in the per-workspace venv.** Today every spawn uses `sys.executable`;
  the local adapter instead uses **`EnvironmentResolver`'s venv interpreter** for
  the run's `source_version` (§ workspace-store §8) — so the run's dependency set
  is the *workspace's*, not the workbench's.
- **Post-processing moves into the job.** `render_study_visualizations` /
  `run_study_analyses` stop running in the HTTP worker; they run as steps 3–4 of
  the detached job. **This retires Engine B** — the synchronous, 1800-s-blocking
  study path collapses onto submit-and-poll.

## 7. Cloud adapter — sms-api → Ray → Batch, reconcilable

- **Submit** = `SmsApiClient.compose_submit(pbg, env_coordinate)` → an sms-api
  `sim_id`, stored in the run's durable row. sms-api dispatches to Ray → Batch;
  heavy analysis runs **in the Batch job**.
- **Poll is reconcilable, not a thread.** No daemon `threading.Thread`. A poller
  (or on-demand `poll`) reads durable rows in a non-terminal sms-api phase and
  **queries sms-api by the stored `sim_id`** — so it survives a restart (the
  current in-process pipeline does not). **This retires the legacy threaded
  pipeline** (the plan's "R5").
- **Land** results → `RunStore` (`outputs_uri` = the S3/artifact location).

## 8. Durability & reconciliation

- **Truth = `runs.db` + the JSONL run log** (the #479 append-only log). In-process
  job managers (`run_jobs`, `remote_run_jobs`) become thin **progress caches**,
  rebuilt from durable state — exactly what they already are for Engine A.
- **Reconcile *all* run stores at boot**, not just `composite-runs.db`: every
  study `runs.db` too (today's gap). Local: a dead-pid `running` row → `orphaned`
  (the #479 orphan-mirror path, now applied study-wide). Cloud: a non-terminal
  row with a `sim_id` → re-query sms-api → update. So a restart never strands a
  run in a false `running`.
- **Attribution:** a run is recorded with the submitting session's `Principal`
  (when auth lands); until then, anonymous. Runs are **workspace-scoped, not
  session-scoped** (§10).

## 9. Concurrency cap & scratch cleanup

- A **concurrency cap** on local detached runs (a shared backend can't spawn
  unbounded subprocesses); over-cap submits queue (a durable queue row, not an
  in-memory one). Cloud concurrency is sms-api's/Batch's to bound.
- **Scratch cleanup:** a run's request file + transient run dir are reclaimed on
  terminal status; orphaned scratch is swept at boot (pairs with WorkspaceStore's
  GC, but scoped to run scratch).

## 10. Runs are workspace-scoped, not session-scoped

A run **outlives the session** that submitted it (detached / remote, minutes to
hours). So runs belong to the **workspace** (its `runs.db` / run log), not the
`SessionRegistry` entry. A session can start a run and disconnect; the run
continues; another session on the same workspace sees it in the runs index. This
is the clean split from the env worker: the **worker is per-session and
ephemeral** (interactive queries), a **run is per-workspace and durable** (a
detached job). The submitting session is recorded only for *attribution*
(→ `Principal`), never for *ownership* of the run's lifetime.

## 11. Migration — retiring two of the three paths

The end state keeps Engine A's model and deletes the other two:

1. **Study runs (Engine B) → local `RunBackend`.** `run_study_*` becomes: build a
   `RunSpec` (with `analyses`), `submit`, return a `run_id`; the UI polls. Viz +
   analyses move into the detached job. The 1800-s HTTP block is gone.
2. **Remote threaded pipeline → cloud `RunBackend`.** Replace the daemon-thread
   poll with durable rows + a reconcilable poller. Delete `remote_run_jobs`'s
   in-memory pipeline ("R5").
3. **Composite runs (Engine A)** become the local adapter's core, extended to run
   analyses and to use the venv interpreter.

Each step is independently shippable and testable behind the port.

## 12. Open questions (deferred to implementation)

- **Run input vs. `persist`:** a run today executes against the *current
  (uncommitted)* staging area (commit-model (a)); the submitted `.pbg` captures
  that state at submit time, and provenance records `source_version` + a dirty
  flag. Strict reproducibility would `persist` first (a new `version_id`) — the
  Phase-3 refinement once the `ScientificContent` write core lands.
- **The durable run queue** shape (over-cap local submits) and its fairness across
  workspaces on a shared backend.
- **`cancel` semantics for a Batch job** (best-effort sms-api cancel; partial
  outputs?) vs. a local pid (clean kill + scratch sweep).
- **Poller cadence / backpressure** against sms-api for many in-flight cloud runs
  (a single reconciling poller vs. per-run — avoid the old thread-per-run model).
