# What runs in an env worker, and what must be dispatched

**Status: design, not implemented** (REFACTOR-PLAN §2A.8 workstream 8). Written
after the image-as-worker arc landed and the first real launches exposed how the
question is usually asked backwards.

Companions: [`env-worker-runtime.md`](env-worker-runtime.md) is what exists today;
§2A.7 draws the line this proposes to enforce; §2A.8 is the hosted-environment
decision.

---

## The problem

`get_pool().call(...)` has **25 call sites** carrying **19 distinct methods**.
Some are interactive lookups; some run simulations. On a hosted deployment the
worker is a pod sized for interaction — `250m`/`512Mi` requested, `1`/`2Gi`
limit — and a real study on this system declares:

```
item35-closing-pilot-8d50ff0-2x10    n_seeds:2     n_generations:10  →      20 sims
item1-baseline-1000x10               n_seeds:1000  n_generations:10  →  10,000 sims
```

Both live in the same repo, in the same `study.yaml` shape, reached through the
same entrypoints. The first belongs in a worker. The second is an OOMKill.

Wiring the pool to the remote launcher without addressing this would route the
second into a 2 GiB pod. **The routing question must be answered first.**

## What can and cannot be known before running

Three cases, and they are genuinely different:

| | cost signal | can we know a priori? |
|---|---|---|
| **Study / baseline runs** | `n_seeds` × `n_generations`, declared in `study.yaml` | **Yes, exactly.** No inference — the spec says so. |
| **Bare composite runs** | `steps` (`run_composite`), `interval` (`run_process`) | **No.** `steps` bounds the *loop*, not the per-step cost: 5 steps of `reference_demo_x2y2` and 5 of `ecoli_baseline` differ by orders of magnitude. |
| **Everything else** (`registry_catalog`, `attach_process_docs`, …) | — | **Not needed.** These are lookups by construction. |

A `@composite_generator` is arbitrary Python, so for the middle row there is no
predicate to write. **Any "is this expensive?" heuristic keyed on the composite
reference would be wrong in both directions, and wrong silently.** That is the
one option this design rules out.

The system has already collided with this. `env_worker_pool`'s own comment records
a multi-generation `ecoli_baseline` at "default 2700 steps, ~minutes" blowing the
60 s call timeout → `EnvWorkerUnavailable` → respawn → fail; the resolution was to
make the timeout **config-overridable so such workloads can raise it**. So a cost
bound already exists — it is just expressed as a ceiling to be raised rather than
a signal to dispatch.

## Proposal — invert the question

Stop asking "is this expensive?" and state what the worker is *for*, so violations
are loud instead of fatal.

### 1. The worker's budget is its contract

§2A.7 already says the worker answers **interactive** queries and that heavy work
is a **job**. Make the limits say the same thing: the call timeout and the pod's
memory ceiling *are* the cost policy — stated as limits, not predictions. Anything
exceeding them is, by definition, not an interactive query.

### 2. Classify by **method** at the pool, not by call site

`WorkerPool.call(workspace, method, params)` sees the method name and is the single
choke point all 25 sites pass through. §2A.7's distinction is method-shaped
(`registry_catalog` vs `run_study`), so encode it there.

> **Corrected 2026-08-27 — see §2.1 below.** As first
> implemented this routed job-class methods to a **local worker** and interactive
> ones to the deployment's launcher. That was the wrong axis, and it could not work
> on a hosted deployment at all. `is_job_class` survives; what it selects does not.

`is_job_class` marks the calls a **scale precheck** inspects (§4). It does not
choose a transport.

**Two corrections to that classification (2026-08-27, #957), both from having read
the call sites instead of the method names:**

- **`run_process` is not job-class.** `env_worker._run_process` builds one class
  from the registry, fills its input ports and runs a **single `update()`**,
  degrading to `{ok: False, stage, error}` rather than raising. That is the
  Composite Explorer's "try this process" probe. It was classified on the `run_`
  prefix.
- **`JOB_CLASS_METHODS` is not one class but two.** See §3: the methods governed by
  the *run target* are a strict subset of the ones governed by *scale*.

### 2.1 Why transport is not the axis

"Local" meant *a subprocess inside the workbench pod*. That was trivially right
when one workbench process was bound to one workspace, and stopped being right
once contexts became switchable.

A session now gets its **own exclusive workspace dir** (`materialize_session_build`)
stamped with one `(simulator_id, commit)`. So the active context already supplies
both halves of an environment:

| half | is |
|---|---|
| data | that session's dir on the PVC |
| computation | the worker-as-image named by its build stamp |

They are two halves of one thing — which is exactly what the pool keys on
(`env_key` → `image:<commit>`). A subprocess in the workbench pod is not "local" to
that context at all; it is a *different* environment that happened to be nearby.

It also could not work. The pod's interpreter cannot import the workspace package
(#932's slim image), and the 0.3.57 bridge that let a venv-less build borrow the
base workspace's venv died when the base became a scaffold. Every job-class call on
a hosted deployment raised `workspace has no .venv` — advice pointing at the one
thing §2A.8 removed. Running job-class work in the build's own image is not a
compromise: it is the same image the simulation itself runs in.

So there are **two independent axes**, and the original step 1 conflated them:

| axis | decided by | values |
|---|---|---|
| transport | deployment topology | subprocess (laptop) / worker-as-image (hosted) |
| scale | the work itself | run in-context / dispatch to viva-api → Batch |

Transport follows topology for every method, restoring the rule
`env_worker_launcher` already states — *"selected by deployment topology, not
preference"*. Scale is what sends a 1000-seed study to Batch, and it is the only
thing that should.

The audit gap is unchanged and still real: of the 8 job-class sites, 4 have been
traced, and `investigation_steps` dispatches `run_study` to the worker with **no
run-target gate at all**. What changed is that an untraced site now lands in a
correctly-provisioned environment that may be too small, rather than in one that
cannot run the code at all.

### 3. Honor the run target that already exists

`backends.resolve_run_target` (backlog item 18) is already **the** authoritative
local-vs-deployment answer, and its docstring is emphatic: every run entrypoint must
resolve it the same way, *"never by which button happened to be clicked"*. It returns
`"deployment"` when the workspace carries `.viv-build.json` (the workspace picker
stamped a materialized build) **or** the deployment sets
`VIVARIUM_WORKBENCH_REMOTE_PINNED`.

This section exists because the original proposal buried it under "What this does not
fix" as leftover application work. It is not leftover — it is the **first** thing to
get right, and it needed no inference at all. Measured 2026-08-27:

| | pin | base workspace | materialized build |
|---|---|---|---|
| hosted (`sms-api-stanford-test`) | **on** | `deployment` | `deployment` |
| laptop | off | `local` | `deployment` |

So on a pinned deployment **every** study run was bypassing the target, and on a
laptop it bypassed the moment you switched to a build: you pick a remote build
expecting the deployment, and the study runs in a worker.

**Which methods this governs is narrower than job-class.** `RUN_ENTRYPOINT_METHODS =
{run_study}`. `run_study_analyses` and `run_investigation_analysis` are post-run
analysis over output that already exists — heavy, therefore job-class, but heaviness
is §4's axis, not a question of where a *run* executes. Gating them here would stop
post-run analysis outright on any pinned deployment.

**Refuse before dispatching.** #957 raises on `"deployment"` rather than dispatching,
because dispatching from inside a `process_bigraph` Step is a genuine design
question — does the Step block on a Batch job, or does the investigation composite
become async? Raising honors the choice (work does not silently run in the wrong
place) and leaves that decision visible instead of guessed.

### 4. Precheck declared scale where it exists

On the study path, multiply `n_seeds × n_generations` **before** starting and
route or refuse above a configured threshold. Exact, cheap, and it catches the
10,000-sim case *up front* — failing after forty minutes is far worse than failing
immediately.

### 5. Make the budget failure say what to do

Where scale is not declared, the timeout is the only honest mechanism — but it
should surface as *"this exceeded the interactive budget; dispatch it via …"*
rather than `EnvWorkerUnavailable`, which reads as an infrastructure fault and
invites raising the ceiling again.

## Explicitly not doing

- **Guessing cost from the composite reference.** No allow-list of "cheap"
  composites, no name matching.
- **Papering over an undispatched heavy path by routing it somewhere plausible.**
  Silently running `item1-baseline-1000x10` anywhere is the outcome to prevent.

## What this does not fix

Routing is not dispatch. **§3 closed the bypass; it did not build the dispatch.**
`investigation_steps` — the known instance — now refuses on a `deployment` target
instead of silently running in a worker (#957), but nothing yet *sends* that study
to viva-api. An investigation on a pinned deployment stops rather than dispatching.

Still open here:

- **Auto-dispatch from inside a composite Step**, which needs the blocking-vs-async
  decision named in §3 before it can be written.
- **The 4 untraced job-class call sites.** `run_study_analyses` and
  `run_investigation_analysis` are not run entrypoints, so they are not bypassing
  the run target — but they are unaudited for scale.

Backlog item 18 records the same class of failure once before: *"a deployment-wide
pin with no session build silently fell through to a local subprocess on the
study-run path."* That is why §3 leads with refusal — **a loud refusal is what makes
the remaining application work discoverable rather than silent.**

## Open question — let the author declare it

The knowledge lives with whoever wrote the composite or study. A declared
execution class (`interactive` / `job`), carried the way `analyses` and
`observables` already are, would replace inference with a fact. That is a schema
change and a separate decision — noted here because every mechanism above is a
proxy for it.

## Suggested order

1. ~~Method classification at the pool~~ — **done, then corrected**
   (0.3.61 → 0.3.63, #952/#954). Transport follows deployment topology for every
   method (§2.1); `is_job_class` marks what a precheck will inspect.
2. **a.** ~~Honor the run target~~ — **done** (0.3.64, #957), §3. Deployed to
   `sms-api-stanford-test`, where the pin makes it apply to every workspace.
   Subsumes what was step 4 below, for run entrypoints.
   **b. Declared-scale precheck** (§4) — **not started, and now load-bearing.**
   With transport no longer standing in for a cost policy, this is the only thing
   separating a small in-context run from a 10,000-sim run that must dispatch.
3. Budget-failure message (§5)
4. ~~Audit job-class call sites for run-target resolution~~ — folded into 2a for
   run entrypoints; what remains is auditing the analysis methods for **scale**,
   which is 2b's problem, not the run target's.
5. *(separate)* author-declared execution class

> **Heads-up on numbering.** §2.1 of this document ("why transport is not the axis")
> is unrelated to **step 2a** ("honor the run target"). The former was briefly
> numbered `2a` and renamed to remove the collision.
