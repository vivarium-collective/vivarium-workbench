# Consolidating run orchestration

**Status:** proposed 2026-08-27, **revised 2026-08-28** against the two API
surveys, and **kept current as steps land** — A1, A4, A2′ and B are done and
deployed to `sms-api-stanford-test` (workbench 0.3.67); see the Order section for
what each turned out to be, which differed from the plan in three of the four — see [§A0](#a0--what-the-surveys-changed-added-2026-08-28) for what
changed and why. Follows §2A.8 workstream 8 steps 1 and 2a (#952, #954, #957),
which are built and deployed to `sms-api-stanford-test`.

## The problem

The workbench has **three investigation orchestrators that disagree about where
work runs**, so a fix applied to one silently misses the others:

| path | orchestration | honors `resolve_run_target` |
|---|---|---|
| `/api/investigation-run-unblocked` | `run_jobs` manager — thread, 202 + poll | **yes**, via `study_runs` |
| `/api/investigation-run` | inline `textwrap` script + `subprocess`, **synchronous** | **no — zero references** |
| `prepare-investigation` CLI | pbg composite, blocking `StudySteps` | refuses since 2a (#957) |

`backends.resolve_run_target` is the user's own local-vs-deployment choice —
set by which workspace they picked (a session build stamps `.viv-build.json`), or
by `VIVARIUM_WORKBENCH_REMOTE_PINNED`, which `sms-api-stanford-test` sets for
*every* workspace. Measured there:

| | pin | base workspace | materialized build |
|---|---|---|---|
| hosted (`sms-api-stanford-test`) | **on** | `deployment` | `deployment` |
| laptop | off | `local` | `deployment` |

So on that deployment today, **"Run investigation" executes on the workbench host
regardless of what the user chose.** `investigation_run_views.py` contains no
reference to `resolve_run_target`, `invoke_run`, or `target`.

Step 2a (#957) closed this bypass for `investigation_steps` — the pbg composite
path — but could not see `/api/investigation-run`, which never touches
`WorkerPool`. That is the limit of the pool-as-choke-point argument: it protects
worker calls, and this path does not make one.

## What already works

`run_jobs` is closer to the destination than it looks:

| piece | state |
|---|---|
| job manager singleton, `submit()` → `job_id`, 202 | **built** |
| one daemon thread per job, per-item status, crash capture | **built** |
| status polling — `GET /api/investigation-run-unblocked-status` | **built** |
| gating — `enumerate_unblocked` | **built** (human gate, not a DAG — see A3) |
| dispatch — `study_runs` → `invoke_run` → `run_runner._execute_remote` → `/compose/v1` | **built** |
| concurrency | not built (sequential `for` in `_worker`) |
| durability | not built (in-memory dict) |

The consequence worth stating: **"auto-dispatch" needs no new machinery.**
`run_runner.execute` already branches on `req.target == "deployment"` into
`_execute_remote`, which exports the composite, submits to `/compose/v1`, polls,
and lands `results.zip` — writing the same `composite-runs.db` rows the local path
does, so browser polling is unchanged.

The blocking-vs-async question that appeared open only existed *inside* the pbg
composite, where a `StudyStep.update()` would have to hold a scheduler thread for
the life of a Batch job. Outside pbg the question dissolves: jobs are async by
construction, and a dispatched run is an item that takes longer.

## Decisions

1. **`run_jobs` is canonical.** The pbg composite stays reachable from
   `prepare-investigation` but is not the default.
2. **Durable, with reconcile on boot.** A dispatched Batch run still in flight
   after a restart is re-attached, not orphaned. *(Revised by A0b: the cheaper
   route to this is to stop holding a thread per run at all, so there is nothing
   to orphan — viva-api already holds the state, keyed by `simulation_id`.)*
3. ~~**The viva-api relay is in scope as a design**, gated on a real need before
   being built.~~ **Superseded by 4.**
4. **DECIDED 2026-08-28 — §E option (e): proxy env-worker traffic through
   sms-api**, which owns queuing, durability and status. This settles the
   intermediate tier *and* §C: the relay is no longer gated on bit-identical
   local execution, because it is now the transport for a product decision —
   putting the worker's 26 capabilities on an API every client can call.
5. **OPEN — §E option (f): a Service per env-worker Job.** *Revised twice; it is
   a genuine competitor to (e), not only a complement.* Measured: a laptop cannot
   reach a worker today (the tunnel host is not an EKS node, so ClusterIP does
   not route), **but a `NodePort` Service plus one security-group rule would do
   it** — the hosts share a VPC. Every blocker here is configuration: the RBAC
   objection that shaped the original design is a three-line Role edit, and the
   network gap is one SG rule. What (f) gives up is what (e) was chosen for:
   queuing, durability, status, and the worker's 26 capabilities as a product API
   the Atlantis CLI can call.

---

## A. Consolidate on `run_jobs`

### A0 — What the surveys changed *(added 2026-08-28)*

Reading this plan against [`workbench-api-survey.md`](workbench-api-survey.md) and
[`ui-api-consumption-survey.md`](ui-api-consumption-survey.md), then tracing the
UI and viva-api, turned up four things that revise it.

**1. `/api/investigation-run` is synchronous, and that is a second defect.**
The two orchestrators are two *buttons* with different shapes, both in
`walkthrough.js`:

| UI action | endpoint | shape |
|---|---|---|
| "Run unblocked" | `/api/investigation-run-unblocked` | async — 202 + `job_id`, polls status every 2 s into an `investigation-run-progress` panel |
| "Run" (investigation detail) | `/api/investigation-run` | **blocking fetch** — `_runInvestigation` disables the button, sets "Running…", and waits for every simulation |

The handler runs the orchestration **inline in the request**. The dev ALB's idle
timeout is **60 s** (`smsvpc-Inter-…`; prod's is 600 s). So on a gateway-fronted
deployment a synchronous route cannot complete for any real investigation
*regardless of the run target* — broken by shape, not only by routing.

**Verified on dev, 2026-08-28**, and the limit turns out to be broader than this
route. `GET /api/investigations` against a **freshly materialized build** was
measured through the tunnel:

```
client:  HTTP 504 Gateway Time-out  after 60.1 s
server:  GET /api/investigations -> 200 (126037.0 ms)
         GET /api/investigations -> 200 (172071.4 ms)
         GET /api/investigations -> 200 (200191.4 ms)
```

The server **succeeded** — at 126 s, 172 s and 200 s — while the client got a 504
at 60.1 s. (A synthetic silent request was dropped at 60.1 s too, so the ceiling
is exact and applies end-to-end through the SSM tunnel.) It is cold-start
dominated: warm retries returned 200 in 32 s, then 9.9 s.

Two consequences:

- **This is not only an orchestration problem.** *Any* endpoint slower than 60 s
  is unusable on this deployment, and the first thing a user does after switching
  to a build — list its investigations — is one of them. That deserves its own
  issue (cache/warm the discovery, or make the endpoint incremental); it is not
  fixed by anything in this plan.
- **It compounds with the run route.** A synchronous `/api/investigation-run` is
  the same failure with a far longer tail.

**Still inferred, not observed:** that `/api/investigation-run` *specifically*
exceeds 60 s. It could not be tested on dev — the base workspace is a scaffold
with zero investigations, and running one on a materialized build would execute
real simulations. The shape argument (inline orchestration + measured 60 s
ceiling) is strong, but it is an inference.

It also revises an earlier diagnosis: the 504 reported when switching workspaces
was attributed wholly to the base-path bug (#960). That bug was real and proven
— `/?workspace=…` serves PTools — but this is a **second, independent** cause of
504s in the same flow, and stopping at the first explanation missed it.

**2. That changes A1.** Plumbing dispatch through the `run_one_composite` seam
would fix the target on a route whose synchronous shape still cannot survive the
gateway — and would be discarded by A5 anyway. **Refuse instead**, mirroring the
posture #957 took: on a `deployment` target, return an error naming
"Run unblocked". Cheap, honest, not throwaway.

The laptop keeps working: with no pin and no build stamp the target is `local`
and the route behaves exactly as today. A laptop *switched to a build* resolves
`deployment` and is redirected to the async path — which is the path that
actually dispatches.

**3. The async UX already exists**, so A5 is not new UX. "Run unblocked" already
owns the 202 → poll → progress-panel idiom; converging means pointing the second
button at the same machinery.

**4. Reconcile-on-boot is well supported by viva-api.** It exposes
`GET /simulations/status/batch?ids=…` → `list[ComposeHpcRun]` — exactly one call
for all in-flight ids. The workbench's `SmsApiClient` has `compose_status(task_id)`
(singular) but **no batch wrapper**; adding one is the small piece A2 needs.

Also worth carrying: `/api/study-run-variant` has **no live UI caller**, yet is
load-bearing — `run_unblocked_views._worker` drives runs through the *lib*
function `study_runs.run_study_variant`. **Convergence should target lib
functions, not routes.** Routes are one entry point; the `lib/*_views.py` builders
are the reuse surface.

### A0b — The async model already exists, and Batch is already async *(2026-08-28)*

Two questions — *"isn't there an async version?"* and *"shouldn't this go to Batch,
which is inherently async?"* — turn out to have the same answer, and it revises
A2 and A3 more than A1.

**There is a complete async remote-run API, built and in use.** A thin-client
three-phase family, with `study-detail.js` as its caller:

| route | role |
|---|---|
| `POST /api/remote-run-build` | phase 1 — push + register the simulator build |
| `POST /api/remote-run-submit` | phase 2 — issue the run; **`202 {simulation_id, phase:"running"}`** |
| `GET /api/remote-run-poll` | on-demand status (build / run / analysis phase) |
| `POST /api/remote-run-land` | phase 3 — download + land a completed run |

`remote_run_submit` contains **zero blocking polls**, and its docstring is
explicit: it "returns as soon as sms-api hands back a `database_id` … in seconds
regardless of campaign size", because viva-api moved the per-seed submission loop
off the response path (backlog item 51). The JS panel polls for progress. This is
the correct shape, already implemented.

**But the other dispatch path throws that away.** `run_runner._execute_remote` →
`remote_run.run_remote` does:

```
sim_id = client.compose_submit(...)        # returns immediately — Batch is async
status = _poll_until_terminal(...)         # BLOCKS for the life of the job
results = client.download_compose_results(sim_id, dest)
```

So there are **two dispatch idioms**, and the plan had been consolidating onto the
wrong one:

| idiom | shape | used by |
|---|---|---|
| thin-client phases | submit → 202 → client polls → land. Never blocks. | `study-detail.js` |
| `invoke_run` → `run_runner._execute_remote` → `run_remote` | submit **then block polling** then download | `study_runs`, `composite-test-run`, and therefore `run_jobs` |

**`run_jobs` is async only at the HTTP layer.** Its `_worker` calls
`study_runs.run_study_baseline`, which blocks through `_launch_run_and_flush`'s
"full 7-stage flush tail", which for a `deployment` target blocks inside
`run_remote`. So a daemon thread is held for the entire life of a Batch job — to
babysit work AWS is already tracking.

**That reframes A2 and A3.** They were solving symptoms of holding threads:

- **A2 (durable job state + reconcile) shrinks.** You need durable thread state
  because you are holding threads. Adopt the thin-client shape — record
  `simulation_id`, return — and there is no thread to lose across a restart.
  **viva-api becomes the durable state**, and `sim_id` the handle. What survives
  from A2 is exactly the small part: persist the dispatch ref and poll it, using
  `GET /simulations/status/batch?ids=…` (§A0.4).
- **A3 (concurrency) largely dissolves.** A bounded pool exists to cap
  simultaneously-*blocked* threads. Non-blocking submits need no pool; Batch
  provides the parallelism. Dependency ordering (prereqs) remains real.

**Revised destination:** not "make `run_jobs` durable and concurrent", but
"make the run path non-blocking, the way `remote-run-submit` already is, and let
`run_jobs` track `simulation_id`s rather than threads."

Open questions this raises, which the plan should not pretend to settle:

- `run_remote`'s blocking poll also performs **`download_compose_results`** —
  landing results is a real step the thin-client family gives its own phase
  (`remote-run-land`). A non-blocking run path needs somewhere for landing to
  happen; that is what phase 3 is for, and what `run_jobs` would have to drive.
- The local (`target == "local"`) branch is genuinely synchronous — a subprocess
  on this host. Non-blocking dispatch is a *remote*-target property, so the two
  branches stop being symmetric and the job model must carry both.

### A1 — Refuse a deployment-target run on the synchronous route *(live defect)*

> **Revised 2026-08-28 by A0.** This section first proposed plumbing dispatch
> through the executor seam. Kept below for the reasoning, which still explains
> *why not to re-point the route*; the action is now the smaller refusal.

**Action:** in `run_one_composite` (or at the `investigation_run` entry), resolve
the target and refuse on `deployment`, naming `/api/investigation-run-unblocked`.
`local` is untouched.

The original analysis, still valid as to why the route must not simply be
re-pointed at `run_jobs` today:
`investigation_run` delegates to `investigations.run_investigation(ws_root, name,
run_one_composite=…, …)`, where the executor is an **injected callable** supplied
in exactly one place (`investigation_run_views`) and stubbed in
`tests/test_investigation_run_views_lib.py`.

Dispatching *through* that seam — `run_core.invoke_run` → `run_runner` instead of
the embedded subprocess script — was the original proposal and remains the
natural shape **if** this route is ever meant to keep running work itself. A0
argues it is not: the route's synchronous contract cannot survive a gateway, so
the seam is the right place to *decline*, not the right place to dispatch.

Re-pointing the whole route at `run_jobs` was considered and rejected *for now*:
it would mean re-implementing everything `run_investigation` does — the
multi-composite `state_doc` path (emitter step injected by `inject_emitter_step`),
the viz hooks (`viz_render_hooks`), spec-error handling, and the concurrent
run-lock guard that must surface as 404 — all while fixing a live defect. That
work belongs in A5, once `run_jobs` is a superset.

### A2 — Durable job state + reconcile on boot

`RunJobManager` keeps jobs in an in-memory dict with a daemon thread each; a pod
roll loses everything, and hosted pods roll on every deploy.

Persist into the existing `composite-runs.db` (which already backs run polling)
using the established additive-migration pattern — `composite_runs._NEW_COLUMNS`
plus `_migrate_runs_meta` adds nullable columns, no table rewrite:

- a `run_jobs` table: `job_id`, investigation, status, items JSON, timestamps
- on `runs_meta`, a column for the **remote dispatch ref**. `remote_run.run_remote`
  already receives `sim_id` from `client.compose_submit(...)` and nothing stores
  it; reconcile needs it.

On startup, for each non-terminal job: re-attach items whose `remote_sim_id` is
still in flight, and fail the rest with a legible "lost to a restart" reason
rather than leaving them `running` forever.

### A3 — Concurrency + dependency ordering

`run_unblocked_views._worker` is a sequential `for` over `job.items`.

- **Concurrency:** run independent items through a *bounded* pool — each
  dispatched item is a Batch job, and each local item a subprocess on the
  workbench host.
- **Dependency ordering:** `run_jobs.enumerate_unblocked` means *"required-before-run
  settings are filled in"* — a **human gate, not a DAG**. Inter-study prerequisites
  live on the pbg path (`investigation_execution._study_prereqs`, read from each
  study's `gate.prerequisites`; `StudyStep.inputs()` turns them into wires).
  Consolidating means lifting that extraction — already a standalone function —
  and re-enumerating as items complete so dependents release.

Scope: **2 of 9** investigations in v2ecoli declare prereqs (`colonies`,
`multiscale-bioprocess`). Real, but not universal — a correct sequential fallback
is acceptable if concurrency lands first.

### A4 — Auto-dispatch

Nothing to build; verify. After A1 every investigation path runs through
`study_runs` → `invoke_run` → `run_runner`, whose `deployment` branch dispatches.

### A5 — Converge the remaining orchestrators  ✅ **DONE**

> **Landed 2026-08-28**, and the shape is a *delegation*, not the merge this
> section imagined — because the premise was wrong in a way worth recording.

**The two orchestrators read different spec shapes.** That is why they never
merged on their own, and it was not visible from either one alone:

| route | reads | model |
|---|---|---|
| `/api/investigation-run` | `investigations/<n>/spec.yaml` (fallback `study.yaml`) | **v2** — `composites:`+`runs:`, or `composite:`+`simulations:` |
| `/api/investigation-run-unblocked` | `investigations/<n>/investigation.yaml` | **v3** — `members:` → `studies/<slug>/study.yaml` |

`vwb migrate-investigations` is the one-way v2 → v3 rewrite, and the real
v2ecoli build carries **11 investigations, all `investigation.yaml`, zero
`spec.yaml`** (checked 2026-08-28). So the "rival orchestrator" is the **v2**
one, still wired to a button, and for every investigation anyone actually has,
its loader finds nothing and 404s.

So convergence is: a **v3** investigation is handed to the *lib* function
`investigation_run_unblocked` (per §A0's "target lib functions, not routes") and
this route answers **202 + `job_id`** — the same async contract "Run unblocked"
already has. It inherits run-target honouring, prereq ordering and gating from
A1–A3′ instead of reimplementing any of it, and it is what makes the button work
at all on a gateway-fronted deployment (§A0.1: a synchronous route cannot outlive
the ALB idle timeout, whatever the run target).

A **v2** spec keeps today's synchronous behaviour, including A1's deployment
refusal — nothing translates a v2 spec into studies, and inventing that here
would be a migration wearing a run button. A directory holding **both** shapes
(mid-migration) keeps the v2 path: `spec.yaml` is what its loader understands,
and delegating would silently run a *different* set of simulations than the spec
the user is looking at.

Client side, `_runInvestigation` hands a 202 to `_vivPollRunProgress` — reusing
the progress panel rather than inventing a second async UX (§A0.3), so a
delegated run gets item rendering, Batch resolution and the prerequisite
re-drive for free.

Do **not** invest in making `StudySteps` non-blocking; superseded by A1–A3.

## B. Workstream 8 step 2b — declared-scale precheck  ✅ **DONE**

> **Landed 2026-08-28.** `_declared_scale_exceeds_budget` in `lib/study_runs.py`,
> called from **`launch_into_study`** — a better seam than this section proposed.
> Both `run_study_baseline` and `run_study_variant` funnel through it, it already
> resolves the target and already refuses a `deployment` one, so the check sits
> immediately after that: reached **only** on a local target, by construction,
> with no duplication at two call sites.
>
> `n_seeds × n_generations` over a budget (`VIVARIUM_WORKBENCH_LOCAL_RUN_MAX_SIMULATIONS`,
> default 50, `0` disables) returns **409** with `declared_simulations`, `budget`
> and a hint naming how to reach the deployment target. Dry runs are exempt — a
> preview declares scale without spending it. Absent or unparseable knobs never
> block: silence is not a claim of scale.

The only thing separating a small in-context run from one that must dispatch, now
that step 1 removed the transport pin that had been acting as an accidental cost
policy.

- Multiply declared `n_seeds × n_generations` (`lib/ensemble_config.py` maps
  `n_seeds` → `n_init_sims`) **before** starting; route or refuse above a
  configured threshold.
- Belongs at the **study-run entry**, before `invoke_run` — *not* at the pool.
  Real study runs never touch `WorkerPool`; `JOB_CLASS_METHODS` marks worker
  calls, which is a different seam.
- Applies only where `target == "local"`. A `deployment` target already goes to
  Batch, which is sized for it.
- Do **not** infer cost for bare composites — [`env-worker-routing.md`](env-worker-routing.md)
  §4 rules that out (a `@composite_generator` is arbitrary Python).

## C. Env-worker relay through viva-api *(design settled 2026-08-28; see §C1)*

A laptop cannot use worker-as-image today: the worker dials **out** to the
workbench, and the SSM tunnel (`AWS-StartPortForwardingSessionToRemoteHost`) is
laptop-initiated with no inbound path. Reversing it does not help — the worker has
no Service (viva-api's ServiceAccount can create Jobs, not Services), which is why
dial-back exists at all.

**Design:** viva-api becomes the rendezvous. It is in-cluster (so the worker can
dial back to *it*) and reachable from the laptop over the existing tunnel. It
binds the `DialBackListener`, starts the Job pointing at itself, and bridges bytes
between that socket and the laptop's connection.

Costs, stated plainly:

- viva-api has **no WebSocket endpoints today** — this introduces the first, plus
  ALB upgrade handling and auth for the bridge.
- Every worker call crosses SSM (the path that takes ~224 s for a tarball).
- One worker pod per laptop session.

**Payoff beyond parity:** it also fixes the laptop hybrid, where a materialized
build borrows the *base* workspace's venv — measured: a build pinned at `234dc76`
running under `v2ecoli@a08e20bd`'s dependencies, exactly what #937 warns about.
One mechanism would then cover hosted and laptop, making #937 largely redundant.

**Gate:** build if bit-identical local execution matters. If not, #937 is far
cheaper — local, fast after first sync, no moving network parts, but
platform-appropriate rather than identical.

**§E raises the stakes on this gate.** If the relay is built, option (e) there —
proxying *all* env-worker traffic through sms-api, which then owns queuing,
durability and status — becomes the natural destination, collapsing the relay,
the missing intermediate tier, and durable job state into one mechanism on a
substrate that already exists (Postgres, `JobStatus`, `ORMHpcRun`). So this is
not only a laptop question.

### C1 — The design, with §E's open questions answered *(2026-08-28)*

Written after tracing both sides. Four of §E's five open questions turn out to
have answers that fall out of what already exists; only the fifth is a real
choice.

**The contract is request/response, because the protocol already is.**
`EnvWorker.call` is JSON-RPC over length-prefixed frames, **serial by
construction** — it holds a mutex so "the next frame read is unambiguously this
call's reply". So the relay does not need a duplex stream, a WebSocket, or ALB
upgrade handling, which is what §C priced it at. It needs one endpoint:

```
POST /env-worker/workers/{job_name}/call   {method, params} -> {result} | {error}
```

viva-api holds the socket; the laptop holds nothing but a URL. The `/env-worker`
ALB rule that landed today (sms-cdk#42) is already the route.

**Q1 — where serialization lives: answered.** It is the mutex `EnvWorker.call`
*already* holds, moved to viva-api, one per held socket. Nothing new is invented;
the per-worker FIFO contract is preserved by keeping the same lock next to the
same socket. A viva-api restart drops the sockets, which is the same failure the
workbench has today — not a regression, and the reason Q1 asked.

**Q2 — task-based or sync: sync, bounded.** With dev's ceiling now 600 s
(retired today) and interactive methods measured in seconds, a synchronous call
is honest and cheaper by one round trip on exactly the calls where latency is
felt (`list_generators`). The existing `ENV_WORKER_CALL_TIMEOUT` is the bound.
Long work does not belong on this tier at all — that is §B's scale axis, and its
answer is Batch.

**Q3 — auth: nothing new for the first slice.** The worker→viva-api leg keeps
the dial-back token it already has (§5A). The client→viva-api leg inherits
whatever protects viva-api, which is a pre-existing question this does not
change — though it *does* put that question on the path of every workspace
query, which is worth stating before this reaches prod.

**Q4 — the local launcher: unchanged, and must stay so.** A laptop routing to
itself through the cloud is absurd. `LocalWorkerLauncher` keeps its socketpair;
the pool's `kind` already models the split, so this needs no work — only a test
that pins it.

**Q5 — migration order: the one real choice.** See below; it decides the shape
of the first commit, not just its sequence.

**What moves.** `DialBackListener` moves to viva-api (ported, not imported —
viva-api does not depend on the workbench package). The workbench gains a third
transport that is a thin HTTP client — no socket, no listener, no cluster access
— which is why this also fixes the laptop case §C was originally about.

**Q5 — migration order: flag-gated parallel path.** Decided 2026-08-28. The two
repos deploy independently, so a cutover has an unavoidable window in which the
workbench expects a viva-api that has not rolled — and env workers are how the
dashboard lists generators, so that window is user-visible.

```
default_launcher()
  ENV_WORKER_PROXY_BASE set     -> ProxyWorkerLauncher   (new, HTTP to viva-api)
  ENV_WORKER_ADVERTISE_HOST set -> RemoteWorkerLauncher  (today, dial-back)
  neither                       -> LocalWorkerLauncher   (subprocess)
```

Proxy is checked **first**: a site switching over may still carry
`ADVERTISE_HOST` from its dial-back configuration, and being silently shadowed
by it is a bug that presents as a hung worker.

**Status: both halves built.** viva-api ships the relay *inert* (503 until
`ENV_WORKER_RELAY_ADVERTISE_HOST` is set, from the Downward API `status.podIP`);
the workbench ships `ProxyWorkerLauncher` behind `ENV_WORKER_PROXY_BASE`. Neither
changes any existing behaviour until a deployment sets those two vars. Nothing is
enabled on dev yet — that is the next step, and it is a config change, not a
code one.

## E. The missing middle: jobs that are neither interactive nor Batch-scale

*(2026-08-28. Raised as a question — "do we need a path for intermediate jobs
>60 s, and if the worker is single-threaded, does sms-api queue them or do we add
thread pools?" — recorded with what the code actually constrains.)*

Two tiers exist and nothing sits between them:

| tier | mechanism | bound |
|---|---|---|
| interactive | env-worker call | `ENV_WORKER_CALL_TIMEOUT`, default **60 s** |
| deployment-scale | viva-api → AWS Batch | async; minutes to hours |

**sms-api cannot queue env-worker calls as things stand, because it is not on
that path.** Dial-back (§5A) means the *workbench* holds a direct socket to the
worker; viva-api only creates and deletes the Job
(`/env-worker/v1/workers`). It never sees a method call. Queueing already happens
one layer earlier, in the workbench.

**Both ends are serial by explicit design.** `env_worker._serve` is a *"Serial
request loop (spec §8): one request at a time, FIFO."* `EnvWorker.call` holds a
mutex across send+recv — *"holds the lock so the next frame read is unambiguously
this call's reply."*

**The binding constraint is monopolization, not the timeout.** The pool keys
workers `(workspace, env_key, kind)`, so a workspace has **exactly one** worker;
`ENV_WORKER_POOL_MAX=8` caps *distinct* environments, not concurrency within one.
So **any long call blocks every other call for that workspace, including
interactive ones** — an intermediate tier cannot be "an interactive call with a
bigger timeout".

### Options

**(a) Thread pool inside `env_worker`.** Requires a protocol change — serial FIFO
is spec §8 and the client's mutex assumes the next frame is its reply, so
responses would need id-multiplexing and a reader thread. Worse, the worker holds
**process-global mutable state** built lazily (`_WS_CORE`, `_VIZ_CORE`,
`_OBS_LINEAGE_AGENT_RE`) and mutates `sys.path`, all written assuming serial
execution; workspace science code (process-bigraph, numpy) adds its own
assumptions. **Highest risk.**

**(b) More than one worker per workspace.** Add an instance slot to the pool key.
No protocol change; concurrency = worker count. But each remote worker is a
**pod** (1 CPU / 2 GiB), so N-way concurrency costs N pods and the LRU cap becomes
a concurrency cap. Cheap to build, expensive to run.

**(c) Async submit/poll *over* the serial protocol.** A `submit`-shaped method
starts work in one background thread and returns a handle; a `poll` retrieves it.
Every protocol call stays fast, so the worker is never monopolized, and the FIFO
loop is preserved. Same shape `remote-run-submit` already uses one layer up
(§A0b). Cheap — but the job state lives **in the worker**, so an evicted,
idle-reaped or crashed worker loses it, and results have nowhere durable to land.

**(d) Send it to Batch anyway.** Correct for anything genuinely large; Batch
startup (image pull, queue wait) dominates a 90-second job.

**(e) Proxy every env-worker request through sms-api, and let sms-api own
queuing, durability and status.** The workbench stops holding a socket to the
worker; sms-api brokers each call.

This is the option that puts the queue where a queue can actually be durable.
sms-api already has the substrate — PostgreSQL, a `JobStatus` model,
`ORMHpcRun` / `ORMAnalysis` — and **already implements exactly this pattern** for
compose: submit → `/simulation/{id}/status` → `/simulation/{id}/results`, with
`/simulations/status/batch` for bulk polling. "Let sms-api handle it" is reuse of
a proven mechanism rather than a new one in the workbench.

**It also makes the worker's capabilities part of the product API.** This is
arguably the strongest argument for (e) and is not an implementation detail.
Today the worker's **26 capabilities** — `list_generators`, `registry_catalog`,
`discover_composites`, `run_study`, `config_to_composite`, … — are reachable
*only* through the workbench, because the workbench holds the socket. **Zero of
them appear in the Atlantis CLI**, whose commands stop at the compose surface
(`simulator_*`, `simulation_*`).

That breaks the stated EUTE model, in which the CLI, TUI and Marimo GUI are three
clients of one REST API: environment introspection and workspace-scoped execution
are currently workbench-only plumbing rather than product surface. Proxying
through sms-api turns each capability into an HTTP endpoint any client can call —
so the CLI could script these workflows, and so could CI, a notebook, or a
third-party tool. None of the other four options change this; they all leave the
socket where it is.

It also **subsumes §C rather than depending on it**. The relay was scoped as a
laptop-only bridge so a laptop could reach worker-as-image; making the proxy
universal means there is *one* path, and the laptop case falls out. And it
answers (c)'s weak spot directly: worker death or idle-reap stops losing the job,
because the record is in Postgres, not in the worker.

Costs, stated plainly:

- **sms-api becomes a hard dependency of every remote env-worker call**, where
  today it only creates the Job. An sms-api outage would become a workbench
  outage for hosted introspection — a new coupling that does not exist now.
- **A hop on every interactive call.** In-cluster that is cheap; over SSM from a
  laptop it is not, and interactive latency is the thing users feel.
- ~~**A duplex transport sms-api does not have.**~~ **Overstated — corrected
  2026-08-28.** The interactive tier needs **no WebSocket**. Because *sms-api*
  would hold the worker socket, the client→sms-api leg is plain request/response:
  `POST /env-worker/v1/workers/{name}/call {method, params}` → sms-api forwards
  over the socket → returns the result. `EnvWorkerService.start` already takes
  `callback_host` / `callback_port`, so pointing the worker at sms-api instead of
  the workbench is **configuration, not redesign**. A streaming transport is only
  needed if something wants push, and nothing does today.
- **The gateway's 60 s ceiling moves in front of every worker call.** Proxied
  calls traverse the ALB, so the measured 60 s limit (§A0) now applies to them.
  That is survivable for interactive work — `ENV_WORKER_CALL_TIMEOUT` is *also*
  60 s, so the budgets coincide — but it makes the intermediate tier
  **necessarily task-based**: a long call cannot be a long HTTP request. This
  constrains the design rather than blocking it.
- **Local subprocess workers must not proxy.** A laptop routing to itself via the
  cloud is absurd, so the local/remote asymmetry stays and the proxy is a property
  of the *remote* transport only.

**(f) Give each env-worker Job a Kubernetes Service, and dial *in*.**

*Raised in review 2026-08-28, and it corrects a framing error of mine.* Dial-back
(§5A) exists because "viva-api's ServiceAccount can create Jobs but not Services"
— which I repeatedly stated as a constraint. It is a **three-line RBAC gap, not a
law**: `kustomize/base/rbac-jobs.yaml` grants `jobs`, `jobs/status`, `configmaps`,
`pods`, `pods/log`, and adding `services: [create, get, delete]` is an edit, not
an architecture.

**What it genuinely buys — symmetry.** Today the two transports are inverted: for
a local worker the workbench connects to a subprocess it spawned; for a remote
one the *worker* connects out to the workbench. With a Service both become
"connect to an address", and the dial-back machinery disappears —
`DialBackListener`, the advertise-host, the one-time token in the first frame, and
the `ENV_WORKER_ADVERTISE_HOST`-selects-the-launcher trick. `EnvWorker.from_socket`
already exists, so the launchers would differ only in how they *obtain* an
address. The worker becomes a plain listener — though **less simple than first
claimed**: dial-back's one-time token was doing double duty, identifying *and*
authorizing. An inbound listener reachable by anything that can route to the port
still needs that check, so the token survives; what is dropped is the
connect-out choreography, not the authentication. The complexity moves into Kubernetes object lifecycle: a Service per
Job, garbage-collected via `ownerReferences` so it dies with the Job the way
`ttl_seconds_after_finished` already handles the Job itself.

**Laptop reach — measured 2026-08-28.** My first pass said a Service "does not buy
laptop reach" and treated that as settling it. Verified against `smsvpctest`, the
accurate statement is narrower: **it does not buy it *as currently configured*,
and both blockers are configuration rather than architecture.**

| fact | value |
|---|---|
| tunnel target (`SubmitNodeInstanceId`) | `i-08ed6714f3ecbc962`, `t4g.medium`, SG `smsvpctest-batch-BatchSubmitSg…` |
| its tags | CloudFormation + `Name` only — **no `aws:eks:cluster-name`, no `eks:nodegroup-name`** |
| an EKS node | `i-09567f64f405e546e`, SG `eks-cluster-sg-smsvpctest-eks-blueprint…` |
| VPC | **same** for both — `vpc-013f0c1012b271b06` |
| EKS SG ← submit SG | **no rule** |
| EKS SG, NodePort range 30000–32767 | **not open** to anything |

So:

- **ClusterIP is not routable from the tunnel host.** The submit node is not a
  cluster node, so kube-proxy does not program its iptables. This part of the
  original objection holds.
- **NodePort would work, and is two config changes away.** The hosts share a VPC,
  so they are IP-routable; what is missing is a `NodePort` Service and **one
  security-group rule** admitting the Batch submit SG to the node port range.
  That is the same weight as the three-line RBAC edit — configuration, not
  architecture.

**So (f) is a genuine competitor to (e), not only a complement.** With a suitable
exposure it delivers a laptop→worker path that is lighter than proxying
everything through sms-api, and it removes dial-back rather than relocating it.

**What choosing (f) *instead of* (e) gives up** — worth being explicit, because it
is the same list (e) was chosen for:

- **queuing and durability** — a direct path has neither; a worker that dies takes
  its work with it;
- **status** — nothing durable to poll;
- **the product API** — and this is the big one. The worker's **26 capabilities**
  stay reachable only by whoever holds a socket, so the Atlantis CLI, CI and
  notebooks still cannot call them. That was the strongest argument for (e), and
  (f) does not provide it at any exposure level.

The trade, stated plainly: **(f) buys a lighter transport and true local/remote
symmetry; (e) buys durability and an API surface.** They are not the same kind of
win, which is why the pairing below is worth preferring to either alone.

**What it costs relative to (e).** A direct workbench→worker path bypasses sms-api
entirely, so it forgoes exactly what (e) was chosen for: durable task records,
per-worker queuing, status, and reaching the worker's 26 capabilities from the
Atlantis CLI.

**(e) and (f) are not exclusive, and the combination may be the real end state.**
They address different legs:

| leg | mechanism |
|---|---|
| client (workbench, CLI, CI) → sms-api | (e) — HTTP, queued, durable, product API |
| sms-api → worker, in-cluster | **(f) — a Service, dialled in** |

In that pairing (f) replaces dial-back for the leg where it is awkward — sms-api
would no longer bind a listener and hand out its own host/port — while (e) keeps
the outside world on one durable, addressable API. Worth revisiting on those
terms rather than as a competitor to (e).

### Where this lands

**DECIDED: (e).** The reasoning below is kept because it records *why*, and
because the §C coupling it identified is what the decision turns on.

**(c) and (e) were the real candidates, and the choice was coupled to §C.**

- If the **relay is built** (§C), the transport cost is already paid, and (e) is
  then mostly "put a queue and a table behind it" — making it the natural
  destination, and collapsing three separate mechanisms (relay, intermediate
  tier, durable job state) into one.
- If the **relay is not built**, (e) is a large change to carry on its own, and
  **(c)** is the cheap tier that needs no new transport — with its durability gap
  accepted as the price.

So §C's gating question — does bit-identical local execution matter? — turns out
to decide more than the laptop case. It decides whether the intermediate tier is
a workbench feature or an sms-api one.

Either way, three things stay unsettled and should not be hand-waved: **where
results land** (the same question §A0b raises about
`download_compose_results`), **what the idle-TTL reaper must know** so it does not
reap a worker mid-job, and **which methods qualify** — the same scale axis as §B.

### What (e) means for the rest of this plan

The decision reaches back into sections written before it.

- **§C is no longer gated.** The relay is the transport for (e). Its cost is
  lower than §C assumed, since the interactive tier is request/response, not a
  duplex stream.
- **§A2′ moves repositories.** Durable job state stops being a workbench concern:
  sms-api records the task in Postgres alongside `ORMHpcRun` / `ORMAnalysis`. The
  workbench keeps `simulation_id`-shaped handles, not threads.
- **§A3′ loses most of its concurrency work.** sms-api must serialize calls
  **per worker** to honor the worker's serial FIFO contract — the queue that
  `EnvWorker.call`'s mutex provides today, moved somewhere durable. Cross-worker
  parallelism is then a scheduling property of sms-api, not a thread pool.
- **§B is unchanged but better placed.** The scale precheck still decides which
  tier work belongs in; with three tiers it now has three answers instead of two.

### Open questions (e) does not answer

Recorded so they are decided deliberately rather than by the first commit:

1. **Where the serialization point lives.** sms-api must not issue two concurrent
   calls down one worker socket. Per-worker lock, per-worker queue, or a single
   consumer per worker — each has different failure behavior when sms-api itself
   restarts.
2. **Whether interactive calls are also task-based.** Uniformity argues yes;
   latency argues no (an extra round trip on `list_generators` is felt). A split
   contract — sync under the budget, task-based over it — is more code but
   matches how the tiers actually differ.
3. ~~**Auth on the call endpoint.**~~ **DECIDED 2026-08-29 — accepted as is, for
   now.** The access-control boundary is **reachability of the AWS account**, and
   nothing finer.

   That is a real boundary rather than an absence of one, and it is worth being
   precise about what enforces it: the ALB is **internal** (`internal-alb-stack.ts`
   — no `internetFacing`), so it is reachable only from inside the VPC, and a
   laptop gets there through `aws ssm start-session
   --document-name AWS-StartPortForwardingSessionToRemoteHost`, which requires
   AWS credentials for the account. The worker↔viva-api leg keeps its dial-back
   token (§5A) on top of that.

   **What is deliberately NOT there:** individual identity, per-user
   authorization, and per-person attribution. Anyone who can reach the account
   can start and call env workers and query any workspace; audit trails
   attribute to the account, not to a person. That is an accepted short-term
   posture, not an oversight.

   **What would have to change if that stops being acceptable**, recorded so the
   decision can be revisited rather than rediscovered: this is the leg that would
   need real authn/authz, and it is now on the path of *every* workspace query —
   so retrofitting it later touches far more than the relay did. The cost of
   deferring is that the surface keeps growing under the same assumption.

   **It no longer gates (e).** Q3 was the one open question standing in front of
   option (e); it is answered.
4. **What happens to the local subprocess launcher.** It must *not* proxy — a
   laptop routing to itself through the cloud is absurd — so `LocalWorkerLauncher`
   keeps its direct socket and the two transports stay asymmetric. The pool's
   `env_key` / `kind` split already models this.
5. **Migration order.** The workbench's `RemoteWorkerLauncher` and
   `DialBackListener` both move to sms-api. Whether that is a flag-gated parallel
   path or a cutover determines whether dev can run both shapes during the change.

## D. Loose ends

- `remote_run._DEFAULT_POLL_TIMEOUT = 7200` (2 h). A study outliving it fails the
  whole investigation under the documented fail-loud policy. Revisit with A3.
- `composite_subprocess.run_composite_subprocess` spawns with **`sys.executable`**,
  not `env_resolver.resolve_interpreter`. On the slim image that interpreter cannot
  import the workspace package, so a hosted `target == "local"` run would fail
  inside the child. The pinned deployment never hits it, but runs and env workers
  pick interpreters by different rules.
- Prod is on workbench 0.3.55 / api 0.9.57 and untouched; everything here has been
  exercised only on dev.

---

## Order

1. ~~**A1** — refuse a `deployment`-target run on the synchronous route.~~
   **DONE** (#968, deployed 0.3.66). 409 naming `/api/investigation-run-unblocked`.
2. ~~**A4** — verify dispatch end-to-end.~~ **DONE**, and by a route this plan did
   not anticipate: a ~500-byte toy `.pbg` through `compose_submit` completes on
   Batch in ~1 min warm (49 s startup; 211 s cold). That also **confirmed A2′'s
   wire shape on real responses** — `sim_id`, `status`, `error_message`, with
   `pending → running → completed` and `→ failed` all observed.
3. ~~**A2′** — make the remote-target path non-blocking.~~ **DONE** (#970,
   deployed 0.3.67) — *and it was a different defect than described.* That path
   was **already** non-blocking; the job layer above it accepted only HTTP 200,
   so every successful 202 dispatch was recorded `failed` with the error text
   `"HTTP 202"` and its `simulation_id` discarded. Items are now `submitted`,
   keep the handle, and resolve via one batched status call **on read**.
4. ~~**A3′** — prereq ordering **and** the gate.~~ **DONE** (#978 ordering,
   #979 gate + re-drive, #980 the UI caller).
   *Concurrency largely dissolves with A2′*: a bounded pool exists to cap
   simultaneously-blocked threads, and Batch supplies the parallelism once
   submits stop blocking.

   Landed: `_study_prereqs` lifted out of `investigation_execution` into
   `run_jobs.study_prereqs` (that module now delegates to it, so both paths read
   prerequisites through one function), plus `order_items_by_prereqs` — a
   **stable** topological sort applied in `run_unblocked_views` before the job is
   submitted. Stability matters: **7 of 9** v2ecoli investigations declare no
   prerequisites, and declared order is exactly what the composite path's
   synthetic serial edges already produce, so those are untouched. A cycle falls
   back to declared order rather than refusing — a metadata typo must not become
   an outage, and the pbg path does not refuse either.

   **The gate, decided and built — option (c).** Ordering alone sequences only a
   **local** target, where each run blocks until it finishes. On a **deployment**
   target A2′ made dispatch return `submitted` immediately, so a dependent
   *started* while its prerequisite was mid-flight on Batch — correct order, no
   gate. Three ways to close it, and the choice mattered:

   | | shape | why not |
   |---|---|---|
   | (a) | block the worker polling until prereqs settle | reinstates the hours-long held thread **A0b** identified as the original defect |
   | (b) | release inside the status GET (`refresh_submitted` already settles there) | progress would depend on somebody keeping a browser tab open |
   | **(c)** | mark `waiting`, release by an **explicit** re-drive | chosen |

   Built: a non-terminal `waiting` status carrying the blocking study's name; a
   `_gate` in the worker that holds a dependent whose prerequisites are not all
   `done`; `RunJobManager.redrive(job_id)` re-running the *same* worker closure
   (the worker now picks up `waiting` as well as `queued`); and
   `POST /api/investigation-run-redrive`, which calls `refresh_submitted` first
   — the prerequisite that just finished is normally a `submitted` item whose
   completion is only known upstream.

   Two details that are load-bearing rather than incidental: a prerequisite is
   satisfied only when **every** item of that study is `done` (a study is its
   baseline *plus* its variants), and a dependent whose prerequisite `failed` or
   was `skipped` is **`skipped`, not `waiting`** — waiting on something that can
   never arrive is indistinguishable from a hang, and the redrive loop would spin
   on it.

   **Caller wired.** `walkthrough.js`'s run-progress poll is the natural one — it
   is already watching the same job — and it fires the re-drive **on change, not
   every tick**: the status GET resolves `submitted` items upstream, so a
   prerequisite completing on Batch surfaces here as `progress.done` increasing,
   and that edge is exactly when a re-drive can accomplish anything. Polling it
   blindly every 2 s would spawn a worker thread per tick for the life of a
   multi-hour campaign, each re-parking the same items.

   The same pass fixed a rendering gap: the progress panel's icon map predated
   both `submitted` (A2′) and `waiting` (A3′), so a dispatched Batch run and a
   gated dependent each rendered `?` — indistinguishable from a bug. Both now
   render, and the headline counts them.

   Guarded by `tests/js/test_run_redrive_poll.js`, which lifts the real
   `maybeRedrive` out of `walkthrough.js` and drives it (the file is one large
   IIFE with no exports, so extraction is the only way to execute the *shipped*
   function rather than a copy that can drift). Verified non-vacuous by deleting
   the re-drive call and confirming the test fails.
5. ~~**A5** — converge the "Run" button onto `run_jobs`.~~ **DONE** — as a
   *delegation*, because the two orchestrators turned out to read different spec
   shapes (v2 `spec.yaml` vs v3 `investigation.yaml`); the real build has zero of
   the former. v3 delegates and answers 202 + `job_id`; v2 keeps its synchronous
   path. See §A5.
6. ~~**B** — the scale precheck.~~ **DONE** (see §B). The seam is
   `launch_into_study`, not the study-run entry this plan named.
7. ~~**C** — the relay.~~ **BUILT AND LIVE ON DEV 2026-08-29** (viva-api#309 the
   relay, workbench#982 `ProxyWorkerLauncher`, viva-api#310 the config). Its two
   prerequisites had cleared the day before: the ALB routes `/compose` **and
   `/env-worker`** (sms-cdk#42) — both previously fell through to PTools, so the
   client leg did not exist — and dev's 60 s gateway ceiling is retired (600 s,
   sms-cdk#36).

   **It cost far less than this section priced it.** §C budgeted a WebSocket, ALB
   upgrade handling and a duplex bridge; none was needed, because the worker
   protocol is JSON-RPC over length-prefixed frames and *already serial by
   construction*. Request/response HTTP is a faithful carrier for it, so the
   relay is three ordinary endpoints. That also answered §E's Q1 (serialization
   lives in the mutex that already existed, moved next to the same socket).

   **Verified from a laptop through the SSM tunnel**, which is the case the relay
   exists for and which was structurally impossible before: worker started in
   **1.6 s** (`connected: true`), `list_generators` returned **33 generators**
   with the `spatio_flux = 0` health marker, and warm calls round-tripped in
   **~0.12 s**. That last number revises this section's other cost estimate —
   "every worker call crosses SSM (~224 s for a tarball)" is true of a tarball,
   not of a JSON-RPC call.

   Flag-gated, per §E Q5: `ENV_WORKER_PROXY_BASE` selects it,
   `ENV_WORKER_ADVERTISE_HOST` is left set and inert, so rollback is deleting one
   line with no image change.

A0 raises A5's value: it is not only consolidation, it is what makes the "Run"
button work at all on a gateway-fronted deployment.

---

### What a caller can actually tell apart *(measured 2026-08-30)*

The question (e) had to answer for real: **can a caller distinguish "the
simulation failed" from "the job failed"?** Answered by manufacturing a true
partial on dev — a study whose baseline succeeds while two variants fail, for two
different reasons — rather than by reading the code.

**Yes, and the discriminator is the envelope, not the payload.**

| what happened | `task.status` | `result` | `error_message` |
|---|---|---|---|
| the job died | `failed` | `null` | why, incl. `(worker pod: OOMKilled (exit 137))` |
| the science failed | `completed` | present, `errors[]` non-empty | `null` |

Inside a `completed` harvest, the *kind* of stage failure is legible too:

```jsonc
"errors": [
  { "stage": "variant:fails-missing-composite", "status": 400,
    "error": "composite '...' not found in either the @composite_generator registry OR ..." },
  { "stage": "variant:fails-bad-param", "status": 502,
    "error": "run failed",
    "traceback": "...ValueError: unknown parameter(s) for ...: ['definitely_not_a_real_kwarg']" }
],
"run_refs": [
  { "label": "monod",           "status": "completed", "n_steps": 5 },
  { "label": "fails-bad-param", "status": "failed",    "n_steps": 0 }
]
```

- **400 = never ran** (resolution failed) and has **no `run_ref` at all**.
- **502 = ran and died** and **does** have one, `status: failed`, `n_steps: 0`.

So `run_refs` counts *stages that reached a runner*, not successes — an earlier
guess that `len(run_refs)` indicated success was wrong, and the partial disproved
it.

**Five defects had to be fixed before this run was even possible**, none of which
any unit test had caught:

1. **workbench#995** — `run_composite_subprocess` interpolated `from {pkg}.core
   import build_core` without checking `pkg`, so a workspace with no
   `workspace.yaml` produced `from None.core import` and a `SyntaxError` the
   caller saw only as "could not parse run output". *(The run that hit this was
   also operator error — `/app/v2ecoli/workspace`, the layout subdir, instead of
   the root `/app/v2ecoli`. The guard is what makes that mistake say so.)*
2. **viva-api#325** — the worker's **2Gi** limit, sized when a worker only
   answered queries. The task tier made it the thing that *runs* a study; every
   composite run was `OOMKilled` within ~60 s, twice, once with no logs at all.
   Now **8Gi**, and a setting, because the ceiling belongs to the site's nodes.
3. **viva-api#325** — `"worker closed the connection"` describes an OOM kill, a
   segfault and a `kubectl delete` identically. `TaskRunner` now appends the
   pod's terminated state.
4. **viva-api#325** — the worker container sets **no locale**, so Python's
   default text encoding was ASCII and each of the workbench's ~130 unqualified
   text reads/writes broke on the first em dash. Two had been fixed at the call
   site (0.3.70, 0.3.71) before the fault was recognised as environmental;
   `PYTHONUTF8=1` fixes the class.
5. **workbench#996** — 0.3.71 carried `stderr`/`stdout`, but the 502 path emits
   `traceback`. A real partial came back as `{"error": "run failed", "status":
   502}` and nothing else.

**Still open, flagged not fixed:** the conclusion card read `"overall":
"within_tol"` on a harvest with two failed stages — the verdict is computed as
though nothing went wrong. Whether a failed stage should invalidate a verdict is
a science question, not a plumbing one, so it is recorded here rather than
guessed at.

### Step 6a — the read-shaped endpoints, and what measuring changed *(2026-08-30)*

**Done and deployed** (viva-api 0.9.69). Eight GETs under
`/env-worker/v1/relay/workers/{job}/`: `generators`, `registry`, `composites`,
`composites/full`, `visualizations`, `visualizations/inputs`, `core-snapshot`,
`reexports`. All seven exercised live against a real worker; `composites/full`
is the eighth and shares the path.

**The value is not the URL, it is that in-band failure becomes a status.**
Several worker methods answer failure inside a successful JSON-RPC `result` —
`{"__unavailable__": true}`, `{"__error__": "..."}` — which `/call` hands back
as HTTP 200. A caller who does not know the sentinel vocabulary reads a
successful response containing no data. The named endpoints map
`__unavailable__` → 501, `__not_registered__` → 404, and the five error
sentinels → 422 (the same 422 a `WorkerCallError` gets, because it is the same
event). `/call` stays raw: the workbench knows the sentinels and reads them.

**Three corrections to this plan's own arithmetic**, all from reading the code
rather than the prose:

- Not 28 methods but **27 dispatched**, and **26 declared** in `_CAPABILITIES` —
  `resolve_inner_composite_state` is dispatchable but unadvertised, so
  `initialize`'s handshake under-reports the worker's surface.
- Not "9 GET / 13 POST" but **8 / 13**: `resolve_inner_composite_state` takes
  `hops` as a *list of node paths*, so it is document-shaped and belongs to 6b.
- `list_generators` was listed as having no production caller. True of the
  workbench — and it is still the only check that proves the workspace imported,
  so it earns an endpoint as the diagnostic it actually is.

**`data_sources_provider` is excluded, and the exclusion is tested.** It takes a
caller-supplied `module:func`, imports it and calls it. In the workbench that
string comes from the workspace's own `workspace.yaml` and never from a request;
a named endpoint taking it as a parameter is arbitrary code execution on an API
with no authentication. It needs a worker-side change (read your own
`workspace.yaml`) plus §E Q3 before it gets a URL.

**A regression this produced, worth recording because of how it hid.** Factoring
the shared `_relay_call` helper out slid it *between* `@router.post(...)` and the
endpoint it belonged to, so FastAPI generated `/call` from the helper's
signature: `method` and `timeout` became query parameters and only `params`
stayed in the body. Every behavioural test passed, `make check` passed, mypy
passed. It showed in exactly one place — the generated client renamed
`RelayCallRequest` to `call_relayed_env_worker_body_type_0`, one line inside a
49-file regeneration diff. Fixed in 0.9.69 with three tests, the general one
being **no endpoint whose name starts with an underscore may be a route**.

**Not converted to an error, deliberately:** `report_core_snapshot` answers a
bad `package_path` with 200 plus a `registry_warning` and a usable `document`.
That is a designed partial — `lib/report.py` reads both — not a hidden failure,
so it stays 200.

**Still open from this step:** 6b, the 13 document-shaped POSTs.

### Step 6b — the document-shaped endpoints *(2026-08-30)*

**Done and deployed** (viva-api 0.9.70). Twelve routes over eleven worker
methods. `analysis_viewers` is **split** rather than wrapped: listing is a GET,
launching is a POST with `uid` required, because one endpoint behind an `action`
flag hides the difference between reading and invoking a contributor's callable.

Bodies use the repo's passthrough-config convention — declare only what viva-api
is authoritative about, forward the rest under `extra="allow"` — because the
meaning of a config belongs to the workspace.

**The worker has four ways of saying no and they disagree**, so the rules are
per-endpoint, not global:

| idiom | mapping |
|---|---|
| `__sentinel__` keys | the shared table from 6a |
| `{"ok": false, stage, error}` | **422** for process-template/run; a documented **200** for `viz_preview` |
| `{"status": "not_registered"}` | `viz_preview` only → 404 |
| `{"result": {error, status}}` | `analysis_viewers` launch → that status |

The fourth came from asking what a viewer launch actually returns. **It is not
HTML.** `_av_resolve_launch` invokes the contributor's callable and returns its
dict; the UI fetches that and opens the returned `{"url": ...}` in a new tab. So
the payload is navigation instructions, and 404/400/500 were all arriving as
200 — a caller could not tell "no such viewer" from "here is your link".

`validate_generated_visualization` is excluded for the same reason as
`data_sources_provider`: it interpolates caller-supplied `pkg`/`module` into a
module name, imports it, and **reloads** it when already imported.

### The registry is order-dependent, and a valid ref can 404 *(open)*

Found exercising 6b live, and it is a **worker-side** defect rather than a
viva-api one — but the named endpoints make it far easier to hit, because a
client can now call `/composite-state` as its very first request.

Measured on `/app/v2ecoli`, two fresh workers, same image:

```
worker A: /generators FIRST                   -> 33 generators, spatio_flux 0
worker B: /composite-state first, then
          /generators                         -> 53 generators, spatio_flux 19
```

And the consequence that matters:

```
cold worker: POST /composite-state {"ref": "spatio_flux…monod_kinetics"}  -> 404 not_registered
after GET /generators: the IDENTICAL request                              -> 200
```

That ref is real — a study runs it. `_list_generators` and
`_resolve_composite_state` carry the same `if not _REGISTRY:
discover_generators()` guard, so a **non-empty but incomplete** registry is the
suspect; not confirmed, and not worth a blind fix. The scan imports every
installed distribution, so "always scan" has a cost that needs measuring before
it is chosen.

This also retires a health check that has now misled twice in one day: **the
generator count proves nothing**, because it is a function of call order. What
proves the workspace imported is running something that needs
`<pkg>.core.build_core`.

### The worker is not a runner — and (e) crossed that line *(2026-08-31)*

Found while asking whether the ParCa cache is *meant* to be staged into env
workers. It is not, and the reason reaches further than the cache.

**Three independent statements say a worker does not run simulations:**

> **Protocol §12**, titled *"the worker is not a runner"*: "The env worker
> answers *interactive authoring/rendering* queries. It does **not** run
> simulations or heavy analyses — those are **jobs**… Keeping heavy compute out
> of the worker is what keeps queries bounded."

> **`env-worker-runtime.md`**: "The worker answers interactive queries only…
> **which is why a worker pod is sized for interaction: `250m`/`512Mi` requests,
> `1`/`2Gi` limits.**"

> **The Job spec's volumes**: both ephemeral, "the worker is stateless with
> respect to the scientific record… nothing here needs the PVC."

And `_parca_staging()` lives on the **Ray-on-Batch** service, feeding
`RAY_STAGE_S3`/`RAY_STAGE_DIR` to the Batch entrypoint — a mechanism the
env-worker Job has no counterpart to, by construction.

**§E option (e) step 5 routes `run_study` — which `env_worker_routing` exists
specifically to mark as job-class, i.e. *not* a worker call — into the worker.**
When every composite run then OOMKilled at 2Gi, the limit was raised to 8Gi
(viva-api#325). That was backwards: the sizing was correct and load-bearing, and
raising it muffled the design telling us the boundary had been crossed. Grepping
this plan for `§12`, "not a runner" or "interactive only" returns **nothing** —
(e) never confronted the rule it overrode.

### Two axes, and only one of them is now handled

The fix chosen was **(3) split the tier by scale** — but investigating it showed
scale is not the axis that actually broke:

| axis | question | status |
|---|---|---|
| **Scale** | how many simulations does this declare? | **handled** — `study_precheck` (workbench#1002) + a 422 at submit (viva-api#344) |
| **Environment capability** | does this composite need data the worker cannot stage? | **not handled, deliberately** |

`basal` declares `n_seeds=None, n_generations=None` → **1 simulation**, far under
the budget of 50. §B's check passes it happily, and it still cannot run in a
worker, because `ecoli_baseline` needs a ParCa cache that is staged only onto the
Batch path. Measured on dev:

```
generator build failed: Cache at 'out/cache' is stale or unversioned:
out/cache/cache_version.json missing
```

and the directory does not exist in the image at all.

**Capability is not inferable without breaking §B's own doctrine** — a
`@composite_generator` is arbitrary Python (`env-worker-routing.md` §4), and
"needs a staged cache" is something you learn by building it. So it is handled by
**failing fast with a legible error**, which is what already happens and is
arguably correct. Both new pieces say so in their docstrings, because the
temptation to make the scale check guess at capability is real.

**What the scale split actually buys**, since the check already existed inside
`launch_into_study`: a refusal that arrives there comes back as an entry in a
harvest's `errors[]` — which under this tier's own semantics reads as *the
science failed*. It did not; the work was sent to the wrong tier. The tier
deciding where work goes is what makes it a tier rather than a queue.

### Dev acceptance run *(2026-08-31)*

| Check | Result |
|---|---|
| **A2 restart honesty** — never re-run since step 5 | **Pass.** Task → `failed`, not stuck `running`: *"lost to a viva-api restart: the worker socket did not survive the process"*; boot log `settled 1 env-worker task(s) stranded by a restart` |
| Identity end to end | **Pass** — `created_by` recorded and survived the restart |
| `vwb smoke` | **Pass** — 4/4 |
| Task tier through the CLI | **Pass** — submit, poll, batch status, named reads |
| **A real whole-cell study** | **Blocked** — see the ParCa finding above |

**The arc is verified; the workload is not.** Everything exercised end to end
runs `spatio_flux` or hand-authored composites. That gap is the honest answer to
"is the test site correct", and it should not be closed by assertion.

Two known gaps got in the way during the run and remain open: **workers idle-die
after ~2 minutes** (§E's own "what the idle-TTL reaper must know so it does not
reap a worker mid-job", still unanswered), and the **order-dependent registry**.

## Where this leaves the plan *(2026-08-29)*

**Every numbered step is done**, and all of it is on **dev only**. What remains
is not in the ordered list:

- **Expose `/env-worker` through the atlantis CLI.** The relay is reachable from
  a laptop today only by hand-rolled `curl` — which is how it was verified, and
  is not a tooling experience. sms-api's CLI is where this belongs, because the
  relay is now an sms-api capability and `CLAUDE.md`'s EUTE rule is explicit that
  end-user-facing paths are exercised **through atlantis, not curl**. Roughly
  `atlantis worker start <commit>` / `call <job> <method>` / `stop <job>` /
  `list`, over the same `--base-url` the rest of the CLI uses, and then the same
  capability in the TUI and marimo GUI so the three clients stay the one workflow
  in three media. Generated-client support already exists — `make api_client`
  emitted `start_relayed_env_worker` / `call_relayed_env_worker` /
  `stop_relayed_env_worker` when #309 landed.

- **§E option (e) proper.** C built the *transport*; (e) is the rest — sms-api
  owning queuing, durability and status on a substrate that already exists
  (Postgres, `JobStatus`, `ORMHpcRun`). §E's Q2/Q4 are answered, **Q3 (auth) is
  not**, and it now sits on the path of every workspace query.
- **Retiring the dial-back path** (the second half of Q5's migration). Both
  transports are live; deleting `RemoteWorkerLauncher` is a later, deliberate
  step, not a side effect.
- **§D loose ends**, untouched: `remote_run._DEFAULT_POLL_TIMEOUT` (2 h),
  `composite_subprocess`'s `sys.executable` vs `env_resolver.resolve_interpreter`,
  stale hash-suffixed ConfigMaps.
- **Prod.** Still api 0.9.57 / workbench 0.3.55 — behind dev by everything above,
  and a large jump whenever it happens.

Steps 1–2 stand alone: they close the defect that makes "Run investigation"
ignore the deployment pin.

## Critical files

| file | role |
|---|---|
| `lib/investigation_run_views.py` | A1 — the `run_one_composite` seam; the fix lands in this closure |
| `lib/investigations.py` | A1 context — `run_investigation` takes the executor as a parameter; **do not change** |
| `lib/run_unblocked_views.py` | A3/A5 — the `_worker` loop |
| `lib/run_jobs.py` | A2/A3 — manager, `enumerate_unblocked` |
| `lib/composite_runs.py` | A2 — schema + additive migration |
| `lib/remote_run.py` | A2 (`sim_id`), D (poll timeout) |
| `lib/investigation_execution.py` | A3 — `_study_prereqs` to lift |
| `lib/study_runs.py` | B — precheck seam before `invoke_run` |
| viva-api `api/routers/env_worker.py`, `compose/env_worker_service.py` | C |

## Verification

- **A1:** on a pinned deployment (every workspace resolves `deployment`), POST
  `/api/investigation-run` and assert the run dispatches to Batch rather than
  executing on the pod — today it runs locally, and that difference *is* the test.
  `tests/test_investigation_run*.py` pin the response contract and must stay green.
- **A2:** submit a job, `kubectl rollout restart deployment/workbench`, confirm the
  job reappears with in-flight items re-attached and none stuck `running`.
- **A3:** an investigation with independent studies shows overlapping item
  timestamps; `colonies` still runs its dependents last.
- **B:** a study declaring 1000×10 with `target == "local"` refuses immediately,
  naming the dispatch path; a small study still runs.
- Per-repo CI; `bash scripts/run_js_tests.sh` for any UI touch.