# RFC: vivarium-workbench Refactor & Production Deployment on AWS

> **Status:** Draft for discussion (Jim Schaff + Alex Patrie), 2026-07-07.
> **Type:** Target-state architecture + sequenced migration plan.
> **Companion:** This is the *forward-looking* counterpart to
> [ARCHITECTURE-DEEP-DIVE.md](ARCHITECTURE-DEEP-DIVE.md) (the code-verified
> *current-state* audit). Every "Problem" below cites a risk from that audit;
> read it first. This document proposes where we go and in what order; it does
> not change code.
>
> Design reviews (Jim + Alex, 2026-07-07) **resolved the foundational
> architecture** (**§2A**), the **deployment & storage** model (**§2B**), and the
> **demo + rollout strategy** (**§5C**). What remains genuinely open is small — the
> eventual science/environment *repo* split, and "make work permanent under an S3
> record." This RFC is now **demo-driven**: a customer demo is ~2 weeks out, so the
> near-term plan (§5C) is deliberately low-risk deployment of the *current* code,
> with the structural refactor sequenced *after* the internal demo and validated on
> a persistent Dev site. Please comment inline / in the tracking issue.

---

## 0. TL;DR

The workbench today is an excellent **single-user, single-workspace, localhost
desktop tool** whose skeleton is production-shaped (dependency-injected lib
layer, real module boundaries, an AI-free server enforced by a test) but whose
edges assume trusted-localhost-forever. Taking it to a hosted AWS deployment is
**not a rewrite** — it's a focused campaign against five load-bearing
assumptions, in an order where each phase ships something real:

1. **Identity** — add authentication + authorization (today: none).
2. **Statelessness** — remove the process-global workspace root + `os.chdir` so
   more than one worker / workspace can be served (today: hard-wired singleton).
3. **Durable run execution** — unify the two ad-hoc run engines onto one
   detached, queue-backed job model that survives restarts and scales on AWS.
4. **Cloud-native storage** — put workspaces and run outputs where a fleet can
   reach them (git + object storage), not on one box's local disk.
5. **Contract & frontend hardening** — verify the frontend, decompose the god
   files, and make the "typed contract" real.

The **single most important decision** (which changes almost everything
downstream) is the tenancy model — §2.1. This RFC recommends **single-tenant
hosted appliance first, designed so multi-tenant is reachable later**, and
sequences the work accordingly.

---

## 1. Goals & non-goals

**Goals**
- Serve the workbench to remote users over the network, safely.
- Support long-running simulations at cloud scale (reuse the existing `sms-api`
  remote-compute plane → Ray/AWS Batch).
- Keep the git-backed audit trail and the YAML-as-source-of-truth model intact —
  they are the product's core value, not incidental.
- Preserve the local-first developer experience (`vivarium-workbench serve` on a
  laptop must keep working; hosted is an *additional* deployment target).
- Make each phase independently shippable and reversible.

**Non-goals (for this campaign)**
- Rewriting the domain model, the render pipeline, or the science.
- Replacing FastAPI, the vanilla-JS frontend framework choice, or process-bigraph.
- A public multi-tenant SaaS on day one (kept *reachable*, not *built*).
- Changing the `pbg-superpowers` / `pbg-template` contract (only the coupling
  *shape* on our side).

---

## 2. Decisions we need to make first

These fork the whole plan. Each states the options, the tradeoff, and a
recommended default so we can move; treat the recommendation as a strawman.

### 2.1 Tenancy model — **the pivotal decision**

| Option | What it means | Cost | When it's right |
|---|---|---|---|
| **A. Single-tenant appliance** (recommended first) | One container/instance serves **one** workspace for **one** user or small trusted team; "the localhost app, hosted." Multiple users → multiple instances. | Low — most current global-state assumptions survive; auth can be a thin front door (ALB/OIDC). | Small collaborating team; internal/lab deployment; fastest path to "it runs on AWS." |
| **B. Multi-tenant service** | One fleet serves **many** users & workspaces; per-request tenant isolation, shared compute. | High — forces §5 (statelessness) and §7 (storage) to completion, plus authz, quotas, per-tenant secrets. | A real product offering to external users. |

**Recommendation:** ship **A first**, but make every Phase-1/2 refactor
*multi-tenant-compatible* (per-request workspace context, no new global state),
so B is an increment, not a second rewrite. The lib layer is already ~95 modules
`ws_root`-parameterized — B is genuinely reachable. This RFC's phases are written
for A-with-B-in-mind; §11 marks where B needs extra work.

### 2.2 Authentication & authorization model

> **⚠ Superseded by §2B (2026-07-07).** For single-tenant behind the existing
> AWS/VPC/tunnel perimeter, **app auth drops out of the near-term plan** — the
> perimeter is the control. The options below apply only when we expose a *live*
> instance outside the perimeter or go multi-tenant. See §2B.4.

Options: **(a)** front the app with an **ALB + OIDC/Cognito** (auth happens
before the request reaches uvicorn — least app code, recommended for Option A);
**(b)** in-app OIDC/session middleware (needed for B's per-tenant authz); **(c)**
API tokens for programmatic/CLI access. The existing GitHub device-flow auth
(`lib/github_auth.py`) is for *the user's* GitHub identity to push branches — it
is **not** app auth and must not be conflated. **Recommendation:** ALB+OIDC front
door for A; add an in-app authz layer only when we do B.

### 2.3 Where the workspace lives in the cloud

> **⚠ Superseded by §2B (2026-07-07).** Resolved: the demo uses a **private EBS
> `gp3` PVC** (POSIX, for git/SQLite) — *not* FSx, *not* s3fs (which lacks the
> locking/rename git & SQLite need). Sim results couple to services **only through
> S3**; the S3-native store comes later via sms-api's `FileService`. See §2B.3.

Options: **(a)** EFS-mounted git working copy per instance (closest to today's
model, works for A); **(b)** clone-on-start from a git remote + object storage
for run outputs (stateless, needed for B); **(c)** fully object-storage-backed
workspace (largest change). **Recommendation:** (a) for A; design the run-output
path for S3 now (see §6) so (b) is incremental.

### 2.4 Run execution backend

The `sms-api` plane already submits pinned `repo@commit` builds to **Ray → AWS
Batch → zarr/parquet on S3** and lands results back as local runs. **Recommend
making that the primary production run path** and retiring the local synchronous
subprocess engine for hosted deployments (keep it for laptop use). This turns
"two run engines" (audit §5) from a liability into the migration target.

### 2.5 Metadata store

Today run metadata is per-workspace SQLite (`runs_meta`). For A this is fine on
EFS. For B (many concurrent writers, cross-workspace queries) it likely needs a
real store (RDS/Postgres or DynamoDB). **Recommendation:** keep SQLite for A;
abstract `runs_meta` access behind a repository interface now (small) so a store
swap for B is localized.

---

## 2A. Converged design (resolved in review, 2026-07-07)

This section supersedes the looser framing elsewhere in the RFC. The design
review settled the *shape* of the system; the phases below build toward it.

### 2A.1 Ports & adapters — the seam is enumerable, not emergent

Local vs. cloud is **not "two modes"** (branching logic scattered across 150
modules — the thing the audit already flagged with `if READONLY`/`if SMS_API_BASE`/
`if snapshot`). It is **one architecture with two adapter sets** behind a small,
fixed set of **ports**. The domain (studies, investigations, verdicts, rendering)
depends only on the ports and never knows which world it runs in; the choice is
**one wiring decision at the composition root**.

The entire surface that differs between deployments is ~5 ports:

| Port | Local adapter | Cloud adapter (later) | Replaces today |
|---|---|---|---|
| **AuthoredRecord** — versioned science system-of-record (write study/investigation/decision/reference/run-binding; `snapshot()→version_id`; history; diff) | local `git` | git-as-engine, durable remote in S3/CodeCommit | `work_state.active_branch_action`, `git_status` |
| **EnvironmentResolver** — resolve an opaque env **coordinate** → a runnable environment | venv / `build_core()` | sms-api `repo@commit` image build | (implicit in the workspace repo today) |
| **RunBackend** — submit / poll / land a run | detached subprocess | sms-api → Ray → Batch | Engine A + Engine B + 3 remote impls |
| **RunStore** — read run outputs | SQLite/zarr on disk | zarr/parquet on S3 | `simulations_index`, `run_store`, emitters |
| **Principal** — who is acting | anonymous / local | OIDC principal | (none; GitHub device-flow is separate) |
| **WorkspaceStore** — lifecycle of working areas (`materialize(source)→handle`, `list`, `discard`, `persist→artifact`) | out-of-repo staging folder + git worktrees off a bare mirror | sms-api PVC + artifact store | `_root` global + manual `../other-repo` checkout |
| **WorkspaceContext** — per-request binding of a session → its workspace + ports | in-proc `SessionRegistry` | same (behind ALB) | `_WS_ROOT` global + global `invalidate()` |

*(Naming: **AuthoredRecord** is the write/versioning **core** of the broader
**`ScientificContent`** port — read + write + versioning over the record; see the
§5A refinement. **WorkspaceStore** + **WorkspaceContext** were added 2026-07-21 —
see §2A.6. **EnvironmentResolver** is realized as a per-session warm **env
worker** (a workspace-venv subprocess; the HTTP process imports no workspace
Python) — see §2A.7.)*

**Success test:** a human or an AI can list everything that differs between local
and cloud on one screen (these ports). Today they cannot.

### 2A.2 What is versioned vs. what is referenced

The "system of record" is **not one fused store**. It decomposes:

- **Scientific record** (AuthoredRecord) — investigations, studies, decisions,
  references, composite **specs**, and **run bindings**. Small, YAML, authored,
  diffable. *This* is what git-as-engine versions. Restoring a version restores
  the science and its pointers.
- **Execution environment** (EnvironmentResolver) — the Python package(s),
  process code, simulators, lockfile. **Referenced by an immutable coordinate,
  never stored in the record.** Materialized as a venv (local) or image (cloud).
  It owns its own lifecycle — you can upgrade a simulator and re-run an old study
  against it *because* the study references, not owns, the engine.
- **Run outputs** (RunStore) — large zarr/parquet, addressed by URI, referenced
  from the record (never committed).

A **run is a binding**: `{ study@ver, composite_ref, env_id, params, outputs_uri,
provenance }`. Reproducibility = (science version) + (env coordinate) + (params) —
three pins, not one fused `repo@commit`. This is *more* honest than today's model
and it's already latent (the `provenance_manifest` records exactly this).

**Composite-code boundary rule:** a `.composite.yaml` spec is science (in the
record); a `@composite_generator` is engine (in the environment). To keep the
record *readable* without importing the environment, a generator's **output is
snapshotted into the record as a spec** — you only need the environment to
*re-execute*, not to *read*.

### 2A.3 git-as-engine, S3-as-durable-host, interface abstracted to version-ids

We keep **git as the engine** (its content-addressing gives the `repo@commit`
reproducibility contract that sms-api and the sync round-trip already depend on —
too valuable and too cross-repo to reinvent). But the **AuthoredRecord interface
leaks no git-isms** — callers see `version_id`/`snapshot`/`history`, never "SHA"
or "push". So on AWS the *durable host* can become S3/CodeCommit (GitHub optional,
a mirror at most) by swapping the adapter, and a future move to a fully S3-native
or event-sourced store is an adapter change the domain never sees.

### 2A.4 Enforcing the science/environment boundary (three layers)

Path-allow-listing inside one repo is a **convention, not a boundary**. Strength,
weakest → strongest:

1. **Interface shape (primary, always).** The AuthoredRecord port has domain
   operations only and **no "write arbitrary path" method** — there is no API to
   cross the boundary. Environment changes go through EnvironmentResolver, which
   never commits into the record.
2. **Store/IAM boundary (target).** When we split science and environment into two
   repos, the workbench service role has *write* to the science repo, *read-only*
   to the environment repo. In git-as-a-service, repo-level IAM is first-class;
   path-level gating is not — so the real boundary is a *repo* boundary.
3. **Path allow-list (transitional, for a fused repo).** While science + env share
   one repo, the AuthoredRecord adapter stages only science paths (a config-driven
   allow-list resolved through `workspace_paths` — formalizing today's *hardcoded*
   pathspec, which the audit flagged). This is the weak-but-adequate bridge until
   the split.

### 2A.5 Decisions log

**Resolved**
- **Ports & adapters**, seam enforced by an import-linter rule, not convention.
- **git-as-engine**; AuthoredRecord interface abstracted to opaque `version_id`s;
  S3/CodeCommit as the durable host on AWS, GitHub optional.
- **Three-store decoupling:** AuthoredRecord + EnvironmentResolver + RunStore,
  with a **run as the binding** across them.
- **Environment = single repo for now** (Q1). Multi-repo simulators are aspirational
  and, when needed, reconciled **offline** into one merged repo/branch — so the
  runtime always resolves *exactly one* environment. Keep the env coordinate
  **opaque** so multi-repo is later a widening, not a breaking change.
- **First phase keeps the workspace as a single *fused* repo** (science + env
  together, Q2 deferred) — enforce the boundary by interface shape + path
  allow-list (§2A.4 layers 1 + 3). No repo split, no pbg-template change, no cloud.
- **Tenancy = single-tenant appliance first** (was open) — B-ready, but the
  near-term target is one instance / one workspace.
- **Auth deferred** (was open) — behind the existing AWS/VPC/tunnel perimeter,
  single-tenant needs no app auth (§2B.4); the `Principal` port is
  attribution/future-proofing, not security. Auth re-enters only at multi-tenant
  or exposure of a *live* instance outside the perimeter.
- **Deployment & storage settled** (§2B) — EKS peer of sms-api; private EBS PVC;
  results couple to services only through S3.
- **Rollout settled** (§5C) — dev/prod split; continuous small PRs; guardrails
  first; agent-coded, dual-lens-reviewed increments on persistent EKS staging.
- **v2ecoli ⇄ workbench cycle broken via `pbg-ptools`** (2026-07-11, see §4.F.2) —
  extract the PTools Omics Viewer into a new leaf distribution `pbg-ptools`
  (`pbg-ptools → workbench`, healthy); v2ecoli keeps the TSV *producers* and drops
  the workbench dep. Breaks the packaging cycle; does **not** remove the deeper
  workbench→v2ecoli code coupling (three guarded `import v2ecoli` run/analysis
  sites) — that is the §2A `RunBackend`/`EnvironmentResolver` port work.
- **Session-multiplexed workspaces** (2026-07-21) — the backend serves many
  concurrent sessions, each routed to its own workspace (the user sees one at a
  time). Split into a **`WorkspaceStore`** (working-area lifecycle: materialize an
  immutable `(repo, ref)` source → a mutable staging area; `persist` → artifact)
  and a per-request **`WorkspaceContext`** resolved from a session key. Kills
  process-global `_root`/`os.chdir` and makes caches workspace-keyed — a **Phase-1
  prerequisite**, one that lands *before/independent of* auth (session key ≠
  `Principal`). Isolation is *across* workspaces only; concurrent access to the
  *same* workspace is a non-goal for now. See §2A.6.
- **Compute-environment isolation** (2026-07-21) — a code map found **~16
  in-process request-path sites** importing workspace Python
  (`build_core`/`_REGISTRY`/`v2ecoli`); the `sys.modules` one-version-per-process
  rule makes one HTTP process unable to host two workspace envs, so **process
  isolation is forced**. Resolved: a **warm, session-owned env worker** (a
  workspace-venv subprocess) answers *interactive* env queries
  (`list_generators` / `resolve_composite_state` / …) while the HTTP process
  imports no workspace Python — the `EnvironmentResolver` port made concrete.
  **Local-first adapter** = clone + `uv sync` → venv (cloud = sms-api
  `(repo, commit)` image behind the same surface, later). **Heavy analysis (and
  eventually heavy viz) is a job output** (AWS Batch cloud / detached local),
  never in the pod — retiring the synchronous in-process study-run
  post-processing. Drops `v2ecoli` / `3.12.12` from the workbench lock. See §2A.7.

**Still open (small)**
- **Relax the workbench Python pin after `EnvironmentResolver`.** The demo track
  adds `[tool.uv] environments = ["python_full_version == '3.12.12'"]` to
  `pyproject.toml` because the `demo` extra pulls v2ecoli (which pins
  `==3.12.12`) as an in-process editable dep, while the workbench itself is
  `>=3.11`; without the pin a universal `uv lock` can't solve the 3.11 slice.
  Accepted short-term (pre-demo). Broaden back to `>=3.11` once the
  `EnvironmentResolver` port (§2A) resolves the runtime env over a boundary
  instead of importing v2ecoli in-process — at which point the workbench's lock
  no longer inherits v2ecoli's interpreter pin.
- **Science/environment *repo* split** (Q2 target): when, and its pbg-template
  blast radius (how `build_core()` discovery changes when env is its own repo).
- **"Make work permanent" under an S3 record:** keep the branch + PR *review*
  workflow (a separable collaboration policy) or commit straight to the record?

### 2A.6 Session-multiplexed workspaces — the `WorkspaceStore` + `WorkspaceContext` seam

**Requirement (2026-07-21).** The backend serves **many concurrent sessions** —
different frontend clients, each bound to its own workspace; the server routes
every request to that session's workspace and keeps them consistent. **A user
sees one workspace at a time**, but the *process* holds many bindings at once.
This is session-multiplexing, not one-user-many-views.

**Non-goal (for now).** Isolation is *across* workspaces (session A can never
disturb session B). Two sessions writing the *same* staging area concurrently is
out of scope — no intra-workspace locking / merge. In practice each session gets
its own materialized staging area, so this doesn't arise.

**Consequence — the process-global root must die, and this is the Phase-1 gate.**
Today `lib/_root._WS_ROOT` is a single global `Path`; "switching"
(`active_workspace.switch_workspace`) mutates it and fires a *global*
`invalidate()` that clears every cache, and the run path `os.chdir`s. Under
multi-session this is a correctness bug, not merely a >1-worker one: two requests
for different workspaces racing on one global root (and one CWD) corrupt each
other **even in a single worker**. So this requirement makes killing the global
root a hard prerequisite — and, crucially, one that is **independent of auth**
(below).

**Two ports, cleanly split.** A workspace decomposes into *where the working area
lives + how it came to be* (lifecycle) and *the science content within it*
(record). Keep them separate rather than overloading `ScientificContent`:

- **`WorkspaceStore`** — the lifecycle of working areas: `materialize(source) →
  WorkspaceHandle`, `list()`, `discard(handle)`, and later `persist(handle) →
  artifact_version`. A `source` is an immutable coordinate `(repo, ref)`; a
  `WorkspaceHandle` is `{ staging_path, source_version, … }`. **Local adapter:** an
  out-of-repo staging folder (e.g. `~/.vivarium-workbench/workspaces/<id>`),
  materialized as a **git worktree off a bare mirror** — cheap per-version
  checkouts, not full clones, which matters at v2ecoli scale. **Cloud adapter:**
  the **sms-api persistent volume**, materialized into a pod-mounted folder,
  `persist` backed by sms-api's artifact store. This is the concrete form of the
  `WorkspaceContext` port §2A.1 named, shaped by the deployment reality — and it
  collapses today's "hand-check-out `../other-repo` and point at it" into "the
  tool materializes a workspace from a source."
- **`ScientificContent`** (existing; `AuthoredRecord` is its write core) operates
  *within* a handle's staging area. `for_workspace(handle)` binds it to
  `handle.staging_path` — nearly today's `for_workspace(ws_root)`, just fed by the
  store instead of a hand-checked-out path. It never learns how the area was
  materialized; the *provenance* (`derived-from source_version`) is recorded by the
  **store** at materialize time.

**`WorkspaceContext` resolved per request from a session.** A `SessionRegistry`
maps an opaque **session key** — a token the frontend carries, *not* a `Principal`
— to the session's `WorkspaceHandle`. Middleware resolves the session key →
`WorkspaceContext { handle, ports }` and threads it exactly as `ws_root` is
threaded today (95 modules ready; ~13 still read the global). Caches become
**workspace-keyed**; `switch` rebinds *this session's* handle (materializing a new
source if needed) and invalidates *only that workspace's* cache slice — never the
global sweep, which would trample other live sessions. `/api/source/switch`
becomes "rebind this session," and `active_workspace.invalidate()`'s all-caches
semantics retire.

**Session-routing decouples from auth.** Phase 1 originally bundled per-request
`WorkspaceContext` with the `Principal`/OIDC front door. This requirement
separates them: **which** workspace a request targets (session key → handle) is
orthogonal to **who** is acting (`Principal`). Session-multiplexing is needed
regardless of auth and can land *before* the auth front door; auth (§2B.4) stays
deferred, and the session key is a routing token, not an identity claim.

**Staging model + the boundary reframing.** The staging area is a *mutable
materialization of an immutable source version*. Edits accumulate uncommitted —
this is exactly commit-model **(a)** (§2A.5, the §5A refinement) — and the
**`persist` step** (staging → durable artifact) becomes the natural home for the
§2A.4 science/environment boundary: snapshot the science paths into a new artifact
whose parent is `source_version`, enforcing the boundary *at persist*, not
per-mutation. So "(a)-until-Phase-3" is not a compromise — staging gives it a
clean persist seam, and a run's reproducibility triple (§2A.2) reads directly off
the handle: `(source_version + staging edits) + env coordinate + params`.

**New state the server owns, and its risks.** The `SessionRegistry` and the
store's **materialization manifest** (which handles exist on the volume, each
`derived-from` which source) are real persistent state — a restart must re-find
them, and abandoned staging areas need **GC**. Provenance must be recorded at
materialize time to stay trustworthy (staging is freely editable). None is exotic,
but none emerges for free.

**Sequencing — this re-sequences, it is not additive to Phase 0.**
`WorkspaceStore` + session-routed `WorkspaceContext` (killing the global root,
workspace-keyed caches, per-session switch) becomes the **spine of Phase 1**, and
the `ScientificContent` write core lands *on top of* a handle rather than a raw
path. Phase 1's exit criterion gains: *N concurrent sessions on distinct
workspaces, no cross-talk; `switch` rebinds one session only.*

### 2A.7 Compute-environment isolation — `EnvironmentResolver` as a per-session env worker

**The coupling (code-level map, 2026-07-21).** The workbench HTTP process imports
workspace-specific Python — `build_core()`, the process-global generator
`_REGISTRY`, and `import v2ecoli` — in **~16 in-process request-path sites**
(`/api/registry` catalog and composite-state render are the *only* two that
already shell out; study runs are worse — **synchronous**, blocking the HTTP
worker up to 1800s while v2ecoli analyses + viz render in-process). This is not
cleanupable in place: `sys.modules` holds **one version of
`v2ecoli`/`process_bigraph`/`pbg_<project>` per interpreter**, and the generator
registry is a single process-global (`process_bigraph.composite_spec._REGISTRY`,
last-writer-wins across every installed workspace). So **one HTTP process
physically cannot host two workspace environments** — process isolation is
*forced*, not chosen, and it is exactly what makes per-workspace dependency /
Python-pin differences satisfiable, which one shared interpreter never can.

**Decision — a warm, session-owned env worker.** Each session (its §2A.6
`WorkspaceContext`) owns a **long-lived subprocess** — the *env worker* — running
the workspace's own interpreter and holding that workspace's `build_core()` /
`_REGISTRY` / imports **in its own process**. The HTTP process becomes pure
orchestration + UI: it imports **no** workspace Python, holds **no** `_REGISTRY`,
and serves request paths by **querying the session's worker** (JSON over stdio /
a local socket). This is the concrete form of the `EnvironmentResolver` port
(§2A.1). **Warm** (vs. spawn-per-query) because `build_core()` costs ~1–3s — a
per-session worker pays it once; the `SessionRegistry` entry becomes
`{ workspace_handle, env_worker }`, and a `switch` tears down and re-materializes
the worker for the new source. The bulk of the compute-env decoupling is
relocating those ~16 in-process sites behind this worker's query surface.

**Query surface — interactive only; heavy compute is a job.** The env worker
answers *authoring / rendering* queries: `list_generators`,
`resolve_composite_state(ref)` (the Explorer), light viz / observable
introspection. It is **not** where simulations or heavy analyses run — those are
**jobs** (`RunBackend`, Phase 2): simulate + analyze + (eventually) render →
durable artifacts read back via `RunStore`. **Heavy analysis never runs in the
pod / HTTP worker.** For AWS runs the job is **AWS Batch** (analysis is part of
the job); locally the job is the detached run subprocess. This retires today's
synchronous study-run post-processing (`study_runs.run_study_*` running v2ecoli
analyses + viz in-process — a coupling *and* a durability liability): analyses
move into the job. **Viz straddles** — light preview may stay an env-worker
query, heavy post-run rendering moves into the job; split it *as it comes*, don't
pre-design.

**Decision — local-first adapter.** `EnvironmentResolver` resolves an opaque
`(repo, ref)` env coordinate → a runnable environment. **Local adapter first:**
clone + `uv sync` → a per-workspace venv (materialized alongside the §2A.6
`WorkspaceStore` staging area); the env worker runs `<venv>/bin/python`. **Cloud
adapter later:** the sms-api-built **image for `(repo, commit)`** as the
environment's source of truth, queried behind the *same* surface (RPC to sms-api,
or the image itself run as the worker). Local-first keeps local integration
simple and shrinks the cloud step to one adapter swap. Bonus: the workbench
process stops importing the workspace env entirely, so **`v2ecoli` and the
`==3.12.12` pin leave the workbench's own lock** — resolving the §2A.5 "relax the
Python pin" item.

**Sequencing.** Spans **Phase 1** (the env worker + interactive queries; its
lifecycle owned by the `WorkspaceContext`, §2A.6) → **Phase 2** (the job owns
simulate + analyze, retiring the synchronous engine — shared work with
`RunBackend`), and *completes* the `EnvironmentResolver` port. The cloud
`(repo, commit)`-image adapter behind the same surface is **Phase 3**.

---

## 2B. Deployment & storage (resolved 2026-07-07)

Supersedes §2.2, §2.3, and the §3/§6 ECS/Fargate framing. Grounded in the
existing infra (`../sms-api`, `../sms-cdk`): an **EKS cluster on GovCloud**,
services as Deployment+Service composed by **Kustomize** overlays, ALB via
**`TargetGroupBinding`**, ghcr images, IRSA, and a shared internal ALB reached by
tunnel.

### 2B.1 Where it runs — a peer of sms-api on the existing EKS cluster
The workbench deploys as **another Deployment+Service in the existing EKS
cluster**, not ECS/Fargate (which would duplicate the K8s stack). It reuses the
sms-api pattern verbatim: a Kustomize `base` + per-env overlay
(`vivarium-workbench-stanford` / `-test`), a pinned ghcr image, a
`TargetGroupBinding` onto the existing internal ALB (reached via the same tunnel),
sealed secrets. The **one new infra piece** is a target group + ALB listener rule
for the workbench — a small `sms-cdk` addition.

### 2B.2 sms-api is the workbench's only cloud backend — in-cluster
`SMS_API_BASE` points at the **in-cluster service DNS**
(`http://api.<ns>.svc.cluster.local:8000`), not a `localhost:8080` tunnel — which
removes the single most fragile thing in the run path. The workbench calls sms-api
over HTTP for builds, run submit/status, and result download, so it needs **no
direct S3 access and no IRSA** — sms-api owns all S3/Batch credentials.

### 2B.3 Storage — private POSIX volume now, S3-native later
Resolved after ruling out two traps:
- **No shared filesystem** (FSx-for-Lustre dropped) and **no git/SQLite on s3fs**
  (s3fs lacks the locking/rename semantics git and SQLite require — a corruption
  risk).
- **Sim results and services never share a filesystem — they couple only through
  S3.** The workbench does **not** mount the compute's results volume.

So:
- **Demo:** the workbench gets its **own private EBS `gp3` PVC** (RWO) for its
  workspace (git/YAML/SQLite) + caches (venv, ParCa ~175 MB, `~/.pbg/build-cache`).
  POSIX-safe, durable across restarts; single replica → global state / `os.chdir`
  is fine. (EFS is an acceptable temporary alternative if cross-AZ rescheduling
  matters; slower for SQLite, so EBS is the default.)
- **Run outputs:** fetched from **S3 via sms-api's download** (the existing
  `remote_run_landing` path) into the pod's private cache, then rendered. S3 is the
  only coupling; no code change.
- **Target (post-demo):** S3-native via the `WorkspaceFs`/`RunStore` ports,
  implemented with **sms-api's tested `common/storage/FileService`** (S3 /
  Qumulo-S3 / GCS backends) — reused, not hand-rolled.

### 2B.4 Auth — deferred behind the existing perimeter
Authentication, authorization, and perimeter are three different things. For
single-tenant the **perimeter is already provided by AWS account isolation + VPC +
tunnels** (the same way sms-api is reached), so the workbench runs **auth-free as a
private peer of sms-api** — the accepted trade-off is flat trust inside the VPC,
identical to sms-api's posture, with git history making destructive actions
revertible. **Authorization** (capabilities, read-only gradations) is a
*multi-tenant* concern. **Read-only** is a capability posture; the truly-safe
public artifact is the **static published bundle** (no server). The `Principal`
port is worth introducing for *attribution* (so commits aren't all
`pbg-template@local`), not security. Auth re-enters only at multi-tenant or
exposure of a *live* instance outside the perimeter.

---

## 3. Target architecture (Option A, B-ready)

> **⚠ Superseded by §2B (2026-07-07).** The diagram below (ECS/Fargate + ALB+OIDC
> front door) predates the decision to deploy on the **existing EKS cluster** as a
> peer of sms-api with no app auth. Kept for history; the real target is §2B.

```
                    ┌─────────────────────────────────────────────┐
   Browser ───────► │  ALB + OIDC (Cognito)   ← the auth front door │
                    └───────────────┬─────────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────────┐
                    │  ECS/Fargate service: vivarium-workbench     │
                    │   • FastAPI (stateless-per-request ws context)│
                    │   • static SPA assets (or served from S3/CDN) │
                    └───────┬───────────────────────┬──────────────┘
             workspace files│                        │ run submit / status
                            ▼                        ▼
                    ┌──────────────┐        ┌──────────────────────┐
                    │ git remote + │        │  sms-api control plane│
                    │ EFS/obj store│        │  → Ray → AWS Batch    │
                    │ (YAML+audit) │        │  → zarr/parquet on S3  │
                    └──────────────┘        └──────────┬───────────┘
                                                        │ results land back
                                                        ▼
                                              ┌──────────────────────┐
                                              │ S3 run outputs +      │
                                              │ runs_meta (SQLite→RDS)│
                                              └──────────────────────┘
   Secrets: AWS Secrets Manager (GitHub app token, OIDC client secret, sms-api creds)
   CI/CD:   GitHub Actions → ECR image → ECS deploy;  IaC: CDK/Terraform
```

---

## 4. Workstreams (each tied to an audit risk)

Each is scoped so it can land as its own PR series. **P#** = audit risk number
(from ARCHITECTURE-DEEP-DIVE §12).

### A. Identity & access — *blocks all network exposure* (audit P2)
- **Problem:** no authentication on an API that spawns processes, writes files,
  and pushes to git; CSRF guard is DNS-rebinding-bypassable; `--host 0.0.0.0` is
  a documented flag. `READONLY=1` isn't actually read-only.
- **Target:** ALB+OIDC front door (§2.2); in-app, treat an authenticated
  principal as required for all mutating routes; fix `READONLY` to be a true
  deny-by-default filter (whitelist reads only, not run-launch/delete/switch);
  add a real `Host`/origin allowlist to the CSRF middleware; scrub the
  traceback-to-client leak in `study_detail_route`.
- **Ship criterion:** the app refuses unauthenticated mutations; a pen-test of
  the readonly deployment finds no compute/destructive route reachable.

### B. Statelessness & workspace context — *blocks >1 worker* (audit P5)
- **Problem:** process-global `_root._WS_ROOT`/`_WS_PATHS`, module-level caches,
  `os.chdir(workspace)` at startup, and a `/api/source/switch` that only
  half-switches (stale CWD/`sys.path`). One process = one workspace.
- **Target:** thread a per-request workspace context object (the lib layer is
  already ~95 modules `ws_root`-ready — finish the last ~13 that read the global;
  eliminate `os.chdir` by making the ~44 remaining hardcoded `ws_root / "studies"`
  joins resolve through `workspace_paths`). Make caches keyed by workspace, not
  process-global. Delete or correctly implement `/api/source/switch`.
- **Ship criterion:** two workers can serve concurrently without cross-talk;
  `os.chdir` is gone; an import-linter/layering test prevents regression.

### C. Durable run execution — *cloud scale* (audit P4)
- **Problem:** two run engines; the durable study-run path is a synchronous,
  uncapped, restart-fatal `python -c` subprocess never reconciled on restart.
- **Target:** one job abstraction. For hosted, route study runs through the
  `sms-api` → Batch/Ray path (§2.4); for laptop, keep a *detached* (not
  in-request) local engine unified with Engine A's request-file model. Add
  restart reconciliation for study `runs.db` (not just `composite-runs.db`), a
  concurrency cap, and job cleanup. Collapse the three overlapping remote-run
  implementations to the thin-client one (the promised "R5" deletion).
- **Ship criterion:** a study run survives a server restart; no run blocks an
  HTTP request; N concurrent runs are bounded and observable.

### D. Cloud-native storage (audit §3, §7 of the deep-dive)
- **Problem:** workspaces + `runs.db` + zarr/parquet live on one box's disk.
- **Target:** git remote for the workspace YAML/audit trail; EFS (A) or
  clone-on-start (B) for the working copy; S3 for run outputs (the sms-api
  landing path already produces S3-native stores); abstract `runs_meta` behind a
  repository interface (§2.5). Fix the `emitter_path` DDL drift and the three
  divergent `runs_meta` schemas while we're in there.
- **Ship criterion:** an instance can be destroyed and recreated with no data
  loss; run outputs are addressable by URL.

### E. Contract & frontend hardening (audit P1, P6)
- **Problem:** god files (`walkthrough.js` 15.7k, `app.py` 6.1k/206 routes,
  `models.py` 85% `extra="allow"`), an unverified frontend (the one JS test is
  broken), generated TS types nobody consumes, a leaky live-vs-snapshot seam.
- **Target:** split `app.py` into `APIRouter` modules by the existing OpenAPI
  tags; tighten the hottest `models.py` payloads to declared fields + add
  `response_model` on mutation routes; decompose `walkthrough.js` per-page (the
  smaller modules prove it's feasible) and stand up a real JS test harness;
  actually consume `domain.generated.d.ts` via `@ts-check`/jsconfig; route all
  live-vs-snapshot fetches through `data-source.js`. Adopt the `APIError`
  envelope mechanism that currently has zero raisers.
- **Ship criterion:** frontend has CI-run tests; no single source file > ~1.5k
  lines; type-check passes over the client.

### F. Companion coupling (audit P3)

**F.1 — pbg-superpowers**
- **Problem:** `pbg-superpowers` imported symbol-by-symbol across 57 lib modules,
  into private `_REGISTRY`.
- **Target:** one `lib/superpowers_api.py` adapter that all call sites import
  from; never touch `_`-prefixed symbols; pin the version (not `branch=main` for
  `investigation-contracts` request models on the API surface).
- **Ship criterion:** a pbg-superpowers bump touches one file to absorb.

**F.2 — the v2ecoli ⇄ workbench cycle (resolved 2026-07-11)**

There were **two** arrows, and they formed a hard packaging cycle:

- `workbench[demo]` → `v2ecoli` (optional extra, `[tool.uv.sources]` path), and
- `v2ecoli` → `vivarium-workbench` (`[project.dependencies]` + a `[tool.uv.sources]`
  git URL still spelled with the **old repo name** `vivarium-dashboard.git@main`).

The cycle made `uv sync --extra demo` fail with *"conflicting URLs for
vivarium-workbench: file://… (editable) vs git+…vivarium-dashboard.git@main"*.

**Diagnosis — the two arrows are not symmetric:**

*The v2ecoli → workbench arrow is thin and removable.* v2ecoli's core has **zero**
code imports of the workbench. The only edge is `v2ecoli/workbench_viewers.py` — a
contribution to the workbench's **generic, name-agnostic viewer seam**
(`lib/analysis_viewers.py`, which discovers `<pkg>.workbench_viewers.get_viewers`
on the workspace package + every installed `pbg-*` distribution). That file ships
the **Pathway Tools Omics Viewer** and lazily imports two workbench helpers
(`study_spec.study_dir`, `workspace_paths.WorkspacePaths`) with graceful fallback.

*The workbench → v2ecoli arrow is deeper and is NOT packaging.* Beyond the `demo`
extra (correctly commented *"NOT a runtime dependency"*), the workbench **core
imports `v2ecoli` by name in three guarded sites** — this is the real coupling and
it is the **run/analysis execution path**, not a display widget:

| Site | Imports | Role |
|---|---|---|
| `lib/study_run_post.py` | `v2ecoli.workflow.analysis.ANALYSIS_REGISTRY` | resolve an analysis's `scale` when running a study |
| `lib/composite_subprocess.py` | `import v2ecoli`, `.library.xarray_run`, `.library.sqlite_run` | run the multigen simulation |
| `lib/visualization_classes.py` | `v2ecoli.workflow.analyses` + `ANALYSIS_REGISTRY` | register v2ecoli analyses as viz classes |

All three are `try/except ImportError`-guarded — the workbench degrades gracefully
(returns an error, skips the section, or falls through to a generic sqlite run) —
so it does not *hard*-depend, but it carries **v2ecoli-specific knowledge in the
generic core**. These three are exactly what §2A's `RunBackend` +
analysis-discovery (`EnvironmentResolver`) ports abstract.

**Decision — extract `pbg-ptools` (a new leaf distribution).** The Omics Viewer is
generic PTools logic (glob `**/ptools/*.tsv` → build an EcoCyc Omics-Viewer URL);
nothing in it imports v2ecoli. Move it out of v2ecoli into a **new `pbg-ptools`
repo** (peer of `pbg-copasi`/`pbg-parsimony`), package `pbg_ptools/`, module
`pbg_ptools/workbench_viewers.py` exposing `get_viewers(ws_root)`, with
`dependencies = ["vivarium-workbench"]`. The workbench discovers it purely by it
being pip-installed (the `pbg-*` distribution scan), independent of which workspace
is served, and it **self-gates** on `ui.ptools_server_url` in `workspace.yaml` (a
dormant built-in for non-PTools workspaces).

- **Producer / viewer split (the clean seam):** v2ecoli **keeps** the *producers*
  (`v2ecoli/workflow/analyses/ptools_*.py`, which write the TSVs — they are bound
  to v2ecoli's sim data model). `pbg-ptools` takes the *viewer*. They already
  communicate **only through the on-disk `**/ptools/*.tsv` contract**, never a
  Python import — so splitting them across repos costs nothing.
- **Arrows after:** `pbg-ptools → vivarium-workbench` (leaf → host, healthy);
  `v2ecoli → nothing workbench-related`; workbench core stays generic. The two
  lazy imports in the viewer become **direct** (it now legitimately depends on its
  host). Install-path notes: add `pbg-ptools` to the workbench `demo` extra, and
  the combined Docker image's `uv pip install --no-deps .` must install
  `pbg-ptools` explicitly (`--no-deps` would skip it).
- **v2ecoli cleanup:** delete `v2ecoli/workbench_viewers.py`; drop
  `vivarium-workbench` from `[project.dependencies]` **and** `[tool.uv.sources]`
  (the stale `vivarium-dashboard.git` URL). `publish-dashboard.yml` self-installs
  the workbench, so it is unaffected. Remaining old-name (`vivarium-dashboard`)
  hits are cosmetic (README/docs/generated bundles/`study.yaml` comments) — a
  separate rename-hygiene sweep, non-blocking.

**Scope boundary — what `pbg-ptools` does and does NOT achieve.** It breaks the
**packaging cycle** and removes the one v2ecoli-side plugin, so after it the
workbench names v2ecoli **nowhere in `pyproject.toml`**. It does **not** make the
workbench v2ecoli-independent: the three guarded `import v2ecoli` sites above
remain. Full independence ("switch between multiple execution environments") is the
§2A port work (Phase 2 `RunBackend` + Phase 3 `EnvironmentResolver`), a larger
lift. `pbg-ptools` is the clean **first step**: it breaks the cycle and turns the
viewer seam into a real dogfooded example of the plugin story the ports generalize.
- **Ship criterion:** `uv sync --extra demo` resolves with no URL conflict;
  v2ecoli has zero functional references to the workbench; the PTools card renders
  from `pbg-ptools` on a PTools-configured workspace and is absent otherwise.

### G. Config & the three planes (audit §3 of the deep-dive)
- **Problem:** `SMS_API_BASE` unprefixed and defaulting to `localhost:8080` in
  two duplicated copies; no boot-time config validation; planes distinguished by
  scattered conditionals.
- **Target:** one typed settings object (pydantic-settings) validated at boot;
  fold `SMS_API_BASE` into the `env_compat` namespace; make "remote unset → no
  remote routes" structural, not runtime-failure.

---

## 5. Sequencing (phased, each shippable)

> Dependencies: **A and B are the gate** — nothing goes on a network until A
> lands, and B unblocks real cloud topology. C–G can then parallelize.

| Phase | Theme | Contents | Exit criterion |
|---|---|---|---|
| **0. Boundary seed + guardrails** (see **§5A**) | Make the boundary real, safely | `AuthoredRecord` port + local git adapter; config-driven science-path allow-list (fused repo); import-linter rule; + companion guardrails (JS test harness; security stopgaps; pin `investigation-contracts`) | AuthoredRecord is the only writer to the science record; lint rule green; allow-list layout-driven; no behavior change |
| **1. Identity + statelessness** | Make it hostable | Workstreams **A** + **B**; ALB+OIDC front door; per-request workspace context; kill `os.chdir` | One authenticated user reaches a hosted single-tenant instance; two workers don't cross-talk |
| **2. Durable runs** | Make it scale | Workstream **C**; study runs via sms-api/Batch for hosted; restart reconciliation; collapse remote-run impls | A study run survives restart and runs on Batch |
| **3. Cloud storage** | Make it durable | Workstream **D**; git remote + S3 run outputs; `runs_meta` repository interface | Instance is cattle, not a pet |
| **4. Hardening** | Make it maintainable | Workstreams **E** + **F** + **G**; god-file splits; typed contract; superpowers adapter; typed settings | No file > ~1.5k lines; client type-checks; config validated at boot |
| **5. (optional) Multi-tenant** | Make it a service | §11 items: in-app authz, per-tenant isolation, metadata store swap, quotas | Many tenants on one fleet |

Phase 0 is small and pure-win; do it regardless of the §2 decisions. Phases 1–2
are the real "get it on AWS" work. Phase 5 only if we choose Option B. The
concrete, agreed definition of Phase 0 is **§5A**.

---

## 5A. Phase 0 — concrete definition (single fused repo, behavior-preserving)

**Framing.** Two repos are in play; only one changes. The **workspace repo** (the
user's data: `studies/`/`investigations/` *and* the `pbg_<project>` package) stays
**one fused repo** — no split, no pbg-template change, no user migration. The
refactor lives entirely in the **vivarium-workbench** tool and is invisible to any
workspace. Goal: make the science/environment boundary *real structure* while
behavior is byte-for-byte unchanged. This is debt-paydown that stands alone even
if AWS never happens.

**In scope**
1. **`AuthoredRecord` port + one local git adapter.** A narrow write API for the
   science system-of-record — domain operations only (`write_study`,
   `record_decision`, `append_run_binding`, `snapshot()→version_id`, `history`,
   `diff`), **no "write arbitrary path" method** (§2A.4 layer 1). The local adapter
   wraps today's exact behavior (`work_state.active_branch_action` + `git_status`);
   `version_id`s are opaque (git SHAs underneath, never surfaced).
2. **Config-driven science-path allow-list.** Replace the *hardcoded* staging
   pathspec in `active_branch_action` (`["studies/", "investigations/", …]` — an
   audit-flagged bug that breaks under a custom `layout:`) with an allow-list
   *derived through `workspace_paths`*. In the fused repo this list **is** the
   boundary (§2A.4 layer 3): a science mutation can never touch `pyproject.toml`
   or package code. Fixes the audit bug as a side effect.
3. **One guardrail (`import-linter` rule).** Only the AuthoredRecord adapter may
   import git/subprocess-for-commit; domain modules cannot reach around it. Green
   while the app still does exactly what it does today.

Companion guardrails from the generic Phase-0 (safe, independent): wire the broken
JS test + a minimal harness; security stopgaps (localhost-default + loud
`0.0.0.0` warning, fix the `READONLY` whitelist, scrub the traceback leak); pin
`investigation-contracts`.

**Explicitly deferred (this is what makes Phase 0 "least destructive")**
- No splitting the workspace into two repos; no pbg-template change (§2A.4 layer 2
  and the Q2 repo split come later).
- No cloud/S3/IAM/CodeCommit adapters; no auth; no server-side hooks (local git only).
- No unifying the two run engines (the genuinely invasive change — Phase 2).
- No `EnvironmentResolver`/`RunStore`/`RunBackend`/`Principal` ports yet — they are
  *named* (§2A.1) but Phase 0 builds only the one port fully reasoned through.
- No frontend decomposition.

**Enforcement recap (fused repo):** interface shape (no cross-boundary API) + path
allow-list at the adapter. No IAM, no repo boundary — those arrive only with the
opt-in split.

**Risk & verification.** The one load-bearing change is routing the existing commit
path through the new adapter — behavior-preserving by construction (same git
commands, same pathspec *content*, just sourced from the layout config). Gated by
the existing suite plus a new unit test asserting the allow-list rejects
environment paths.

**Exit criterion.** AuthoredRecord is the only writer to the science record; the
import-linter rule is green; the allow-list is layout-driven; all existing tests
pass with no behavior change.

**Refinement (2026-07-16, from a code-level dependency map — see issue #471).**
A read-only discovery over the write/read surfaces sharpened three things:
- **Naming:** `AuthoredRecord` is the *write/versioning core* of a broader
  **`ScientificContent`** port (read + write + versioning over the record); reads
  fold onto the same interface incrementally after the write core.
- **Three categories, not two.** The staging boundary decomposes into
  **science** (`studies/`, `investigations/`, `references/`, decisions),
  **compute-environment** (`pyproject.toml`, `models/`, `scripts/`, package code,
  lockfile — the deferred `ComputeEnvironment` domain), and **deployment /
  integration bindings** (`ui.ptools_server_url` et al. — URLs to hosted external
  singletons; belong to a deployment-config layer, *neither* port). That third
  bucket is #471's "env portability" concern (`stanford` vs `stanford-vpc-test`);
  `workspace.yaml` is a three-way straddler and eventually wants `ui.*` lifted
  out.
- **First step landed:** `lib/staging.py` — one layout-driven policy with owned
  `science_paths()` + `environment_paths()` lists — routed through
  `work_state.active_branch_action`, fixing the layout-blind allow-list bug while
  the science+env union preserves the legacy `_STAGE_PATHS` set.
- **Port established (read surface):** `lib/ports/scientific_content.py`
  (`ScientificContent` Protocol) + `lib/adapters/scientific_content.py`
  (`LocalGitScientificContent` + a composition-root `for_workspace()` factory).
  The `/api/git-status`, `/api/work-status`, `/api/dirty-status` routes now read
  through the port (behavior-preserving). Scoped to **reads** — `status`,
  `work_status`, `dirty_status`, `head_version` (opaque version id).
- **Deferred to a decision — the write/commit core.** The FastAPI app *defers*
  commits (mutations write uncommitted; versioning is a user-initiated commit-all
  / push), so `active_branch_action`'s scoped-commit is a **live-server-only**
  pattern. The write core therefore turns on a **commit-model fork**:
  **(a)** deferred + commit-all (today's reality; the science/env boundary is not
  enforced at commit time) vs **(b)** scoped-per-mutation commits (what §2A.4
  assumes; a real UX change). `snapshot`/write verbs are intentionally absent from
  the port until (a)/(b) is chosen.
- **Not worth doing** (superseded): routing `dirty_commit_all` /
  `remote_commit_and_push` through the *allow-list* — they are genuine commit-all
  escape hatches (a deny-list belongs there), and the ParCa-cache sweep they
  risked is already mitigated by `.gitignore` (`out/`). Left as intentional
  deny-list paths.
- **Layering gate landed:** `import-linter` in CI (`[tool.importlinter]`) with a
  `forbidden` contract keeping `lib.ports` a pure interface layer (no adapter /
  no git-impl imports) — so mypy verifies adapter↔port *conformance* (structurally,
  at the `for_workspace() -> ScientificContent` boundary, plus a `TYPE_CHECKING`
  conformance guard in the adapter) while import-linter verifies the *direction*
  (nothing reaches around the port).
- **Next:** the write core once the commit-model fork is settled; as more ports
  land, extend the contract to "domain must not import `lib.adapters`."

---

## 5B. Deferred phases — rough roadmap (how each realizes the ports)

Sketch-level, not a spec — enough to see the trajectory. The through-line: **each
phase introduces or completes one port from §2A, always local-adapter-first
(behavior-preserving), with the cloud adapter added later, and every new seam
gets its own import-linter rule.** Phase 0 (§5A) built `AuthoredRecord`; the rest:

### Phase 1 — Identity + per-request context *(introduces `Principal`; realizes `WorkspaceContext`)*
- **Where it goes:** one hosted instance, reachable by an authenticated user, and
  safe to run with >1 worker. Still single fused repo, still local run engine.
- **How (rough):** introduce a `WorkspaceContext` object that carries the ports +
  the `Principal`, and thread it **per request** exactly the way `ws_root` is
  already threaded (95 modules are ready; finish the ~13 that still read the
  global `_root`). Remove `os.chdir`; make the module caches workspace-keyed. Put
  an **ALB+OIDC front door** ahead of uvicorn to populate `Principal`; commit
  attribution becomes the principal (retiring `pbg-template@local`). Fix/replace
  the half-done `/api/source/switch`.
- **Exit:** two workers, no cross-talk; `os.chdir` gone; mutations require an
  authenticated principal; `READONLY` is genuinely read-only.
- **Refinement (2026-07-21, see §2A.6):** the per-request-context work is now
  shaped as a **`WorkspaceStore`** (materialize an immutable source → a mutable
  staging area; `persist` → artifact) plus a **`WorkspaceContext`** resolved from
  a **session key**, since the backend session-multiplexes many clients each on
  its own workspace. Two consequences for this phase: (1) session-routing is
  **decoupled from auth** — the workspace-binding half can land *before* the OIDC
  front door (session key ≠ `Principal`); (2) `switch` + cache invalidation become
  **per-session**, retiring the global `active_workspace.invalidate()`. Exit
  criterion gains: *N concurrent sessions on distinct workspaces, no cross-talk;
  `switch` rebinds one session only.*

### Phase 2 — Durable run execution *(introduces `RunBackend`)*
- **Where it goes:** runs survive a restart and can scale out; the two-engines
  liability is resolved.
- **How (rough):** define the `RunBackend` port. **Local adapter** = unify both of
  today's engines onto Engine A's request-file/**detached** model and retire the
  in-request `python -c` engine. **Cloud adapter** = the sms-api → Ray → Batch
  thin-client path (delete the legacy threaded pipeline — the promised "R5").
  Add restart reconciliation for study `runs.db` (today only `composite-runs.db`
  is reconciled), a concurrency cap, and scratch cleanup.
- **Exit:** a study run survives a server restart; runs execute on Batch; no run
  blocks an HTTP request.

### Phase 3 — Cloud storage + the science/environment repo split *(completes `AuthoredRecord` cloud adapter, `EnvironmentResolver`, `RunStore`; executes Q2)*
- **Where it goes:** the instance becomes cattle (destroy/recreate, no data loss),
  and the boundary graduates from path-allow-list to **repo/IAM-enforced**.
- **How (rough):** `AuthoredRecord` cloud adapter = **git-as-engine with the
  durable remote in S3/CodeCommit** (GitHub optional; interface still opaque
  version-ids). `RunStore` cloud adapter over S3; `EnvironmentResolver` cloud
  adapter = sms-api build images. **Execute the science/environment split (Q2):**
  science record repo (workbench writes) vs. environment repo (read-only), with
  the **env coordinate** now a first-class field on run bindings. This is the
  phase that touches **pbg-template** (how `build_core()` discovery changes when
  the environment is its own repo) — coordinate it there.
- **Also resolve here:** the "**make work permanent under an S3 record**"
  question — keep the branch + PR *review* workflow (separable collaboration
  policy) or commit straight to the record.
- **Exit:** instance recreatable with no data loss; boundary is a repo/IAM
  boundary; environment pinned by immutable coordinate; reproducibility =
  (science version) + (env coordinate) + (params).

### Phase 4 — Contract & maintainability hardening *(no new ports; pays down god-files/coupling)*
- **How (rough):** split `app.py` into `APIRouter`s by the existing OpenAPI tags;
  decompose `walkthrough.js` per page (**stand up the frontend test harness
  first** — Phase 0 companion — since the audit's one JS test is broken); split
  `investigations.py` / `single_study_report.py`; tighten the hottest `models.py`
  payloads + actually consume the generated TS types; the one `superpowers_api.py`
  adapter (§F); typed pydantic-settings validated at boot (§G).
- **Exit:** no source file > ~1.5k lines; client type-checks; config validated at
  boot; a pbg-superpowers bump touches one file.

### Phase 5 — Multi-tenant *(optional; completes `Principal`/authz + isolation)*
- **How (rough):** in-app authorization (per-tenant resource scoping) on top of the
  auth front door; per-request tenant context end to end (the Phase-1 discipline
  makes this an increment); `runs_meta` → a shared store (RDS/Dynamo); per-tenant
  secrets, quotas, and noisy-neighbor controls on the run backend.
- **Exit:** many tenants on one fleet.

**Dependency shape:** 0 → 1 is the gate (nothing hosted until identity +
per-request context land). 2, 3, 4 can then largely parallelize (2 needs 1's
context; 3 needs 2's `RunStore` shape; 4 is independent). 5 only if we choose
multi-tenant (§2.1, still open).

---

## 5C. Demo track & rollout strategy (resolved 2026-07-07)

Context: an **internal demo ~1 week out** and a **customer demo ~2 weeks out**,
intense work between. The near-term plan is deliberately low-risk: deploy the
**current code as a fixed appliance**; reserve the structural refactor for *after*
the internal demo, validated on a persistent Dev site.

### 5C.1 Demo track (Week 1 → internal demo) — additive only, no app-code surgery
- **Day 0–1:** confirm the demo narrative; **prove the in-cluster integration
  first** (workbench pod → in-cluster sms-api → land a run) — the single most
  likely thing to break.
- **Week 1:** Dockerfile (workbench + the v2ecoli workspace deps, `linux/amd64` →
  ghcr); Kustomize base + Dev + Prod overlays (private EBS PVC, in-cluster
  `SMS_API_BASE`, `TargetGroupBinding`); the one `sms-cdk` target-group add; verify
  author→commit + remote-run end-to-end on **AWS Dev**. Static published bundle as
  a fallback.
- **Steer the live demo to the remote (sms-api) run path**, away from the fragile
  local synchronous engine; pre-seed runs so nothing time-sensitive can hang.
- **Internal demo = a full dry run on Dev**, then **promote the validated image tag
  to Prod**.

### 5C.2 The dev/prod split — the mechanism that lets the refactor start early
Two overlays with independent image tags: **Dev** (persistent staging, where
refactor increments continuously deploy) and **Prod** (the customer demo). During
the customer-demo window **Prod is pinned to the known-good tag while Dev iterates
freely** — so the refactor track can open right after the *internal* demo without
endangering the *customer* demo.

### 5C.3 Rollout of the refactor (post internal-demo) — strangler-style
Reuses the approach that already worked here (the stdlib→FastAPI migration):
incremental, behavior-preserving, continuously merged to main and **deployed to Dev
each time**. Sequence:
1. **Guardrails** — import-linter layering gate + revive the JS test harness
   (makes later refactors safe *before* touching god-files).
2. **`WorkspaceContext` + `AuthoredRecord`** — the per-request threading spine + the
   first port (resume `refactor/phase0-authored-record`).
3. **Remaining ports incrementally** — `RunBackend` (unify the two engines), then
   `RunStore`/`EnvironmentResolver` (S3-native via sms-api's `FileService`).
4. **Larger structural lifts** — science/env split, run-engine unification,
   god-file/frontend decomposition — *after* the ports exist and are enforced.

### 5C.4 Working model
- **Continuous small PRs to main**, each behavior-preserving, import-linter green,
  auto-deployed to Dev — never a long-lived refactor branch.
- **Agents do the coding step-by-step; humans hand-review each increment** with two
  lenses: **Alex** (does it preserve app behavior?) and **Jim** (is it the right
  structural move? — objectivity / problem-spotting). Infra is shared (both know
  it).
- **Persistent EKS Dev staging throughout** — every increment is observed in the
  real cluster, not just unit-tested.
- **Adapter selection at one composition root** so incomplete cloud adapters never
  affect the running deployment; **promotion Dev→Prod** is a manual image-tag bump
  of a validated build.

---

## 6. AWS specifics (Option A)

> **⚠ Superseded by §2B / §5C (2026-07-07).** The bullets below assume
> ECS/Fargate + an ALB+OIDC front door + a `runs_meta`→RDS path. The resolved
> target is an **EKS Deployment peer of sms-api** (Kustomize + `TargetGroupBinding`),
> a **private EBS PVC**, **no app auth** (perimeter), and **no direct S3/IRSA** in
> the workbench. Still-valid bits (Secrets Manager, CloudWatch logging via the
> existing structured logs, `/health` for the target group) carry over.

- **Compute:** ECS/Fargate service (1 task/tenant for A). Container = the
  existing app image; SPA assets either in-container or pushed to S3+CloudFront.
- **Auth:** ALB with an OIDC action (Cognito user pool, or the org's IdP).
- **Runs:** existing sms-api control plane (Ray→AWS Batch); results land in S3.
  This is the strongest reason the refactor is tractable — the hard part
  (distributed sim execution) already exists and is well-factored.
- **Workspace storage:** EFS access point per instance for the git working copy;
  git remote (GitHub) as the durable audit trail; S3 for run artifacts.
- **State/metadata:** SQLite on EFS for A → RDS Postgres for B.
- **Secrets:** AWS Secrets Manager (GitHub App token, OIDC client secret, sms-api
  creds). Note: the sms-api client currently has **no auth** (relies on an SSM
  tunnel) — hardening that boundary is a prerequisite for exposing it beyond the
  tunnel.
- **IaC + CI/CD:** CDK or Terraform for the stack; GitHub Actions → ECR → ECS
  deploy. (An sms-cdk repo already exists for the compute plane — align with it.)
- **Observability:** the app already has structured access logging + X-Request-ID;
  ship those to CloudWatch; add health/readiness endpoints (a `/health` route
  exists) for ALB target-group checks.

---

## 7. Explicitly out of scope / deferred
- Rewriting the domain model or render pipeline (audit's god-modules
  `investigations.py`, `single_study_report.py` get *split*, not redesigned).
- Replacing the vanilla-JS frontend with a framework (decompose first; reevaluate
  after Phase 4).
- The dual-`schema_version: 4` disambiguation and two study-dir resolvers — real
  debt (audit §4), but data-model cleanup, not deployment-critical; schedule
  alongside Phase 4.

---

## 8. Risks of the refactor itself
- **Statelessness (Phase 1) is the riskiest change** — it touches many modules
  and the `os.chdir` removal can surface hidden CWD-relative assumptions. Mitigate
  with the import-linter gate and the layering test from Phase 0, and by landing
  it behind the existing single-workspace behavior first.
- **sms-api coupling is folklore-driven** (response-shape sniffing, known-broken
  filters, no version handshake). Making it the primary run path (Phase 2) means
  hardening that contract — budget for it.
- **Auth in front of a tool that assumed no auth** can break the GitHub
  device-flow UX; keep the two identity concepts separate (app-auth vs the user's
  GitHub identity for pushes).
- **Frontend decomposition without tests is dangerous** — Phase 0's test harness
  must precede Phase 4's `walkthrough.js` split.

---

## 9. Open questions for Jim + Alex

**Resolved since first draft** (see §2A.5, §2B, §5C): tenancy (single-tenant
first), auth (deferred behind the perimeter), IaC (reuse `sms-cdk` — the workbench
is a peer of sms-api), and the run path (hosted runs go through sms-api; steer the
demo there). Remaining:

1. **Demo narrative** — confirm the must-show (working assumption: author/adjust a
   study → launch a remote sms-api run → results land & render, on AWS).
2. **Owner of the one `sms-cdk` change** (workbench target group + ALB listener).
3. **Science/environment *repo* split** (Q2) — when, and its pbg-template blast
   radius. (Post-demo.)
4. **"Make work permanent" under an S3 record** — keep branch+PR review, or commit
   straight to the record? (Post-demo.)
5. **Metadata store for B** — RDS Postgres vs DynamoDB, if/when multi-tenant.

---

## 10. Immediate next steps
- **Demo track (Week 1, §5C.1):** Dockerfile + Kustomize base/Dev/Prod overlays +
  the `sms-cdk` target-group add; prove the in-cluster sms-api integration Day 1;
  deploy to AWS Dev; internal-demo dry run; promote to Prod.
- **Land the doc PRs** (#454 audit → then #455 RFC) as the shared reference.
- **Refactor track opens after the internal demo (§5C.3):** guardrails first, then
  `WorkspaceContext` + `AuthoredRecord`, continuous small PRs auto-deployed to Dev.

## 11. What Option B (multi-tenant) additionally requires
- In-app authorization (per-tenant resource scoping), not just an auth front door.
- Completing statelessness (no per-process caches; per-request tenant context end
  to end) and clone-on-start / object-storage workspaces (§2.3 option b/c).
- `runs_meta` → a shared metadata store (§2.5); per-tenant secrets & quotas;
  noisy-neighbor controls on the run backend.
- These are increments on the A design *if* Phase 1–3 keep the "no new global
  state, per-request context" discipline — which is why the recommendation is
  appliance-first-but-B-ready rather than appliance-only.
