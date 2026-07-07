# RFC: vivarium-workbench Refactor & Production Deployment on AWS

> **Status:** Draft for discussion (Jim Schaff + Alex Patrie), 2026-07-07.
> **Type:** Target-state architecture + sequenced migration plan.
> **Companion:** This is the *forward-looking* counterpart to
> [ARCHITECTURE-DEEP-DIVE.md](ARCHITECTURE-DEEP-DIVE.md) (the code-verified
> *current-state* audit). Every "Problem" below cites a risk from that audit;
> read it first. This document proposes where we go and in what order; it does
> not change code.
>
> A design review (Jim, 2026-07-07) **resolved the foundational architecture** —
> captured in **§2A**. The remaining §2 items (tenancy, auth source, the eventual
> science/environment *repo* split) are still open. Please comment inline / in
> the tracking issue.

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

Options: **(a)** front the app with an **ALB + OIDC/Cognito** (auth happens
before the request reaches uvicorn — least app code, recommended for Option A);
**(b)** in-app OIDC/session middleware (needed for B's per-tenant authz); **(c)**
API tokens for programmatic/CLI access. The existing GitHub device-flow auth
(`lib/github_auth.py`) is for *the user's* GitHub identity to push branches — it
is **not** app auth and must not be conflated. **Recommendation:** ALB+OIDC front
door for A; add an in-app authz layer only when we do B.

### 2.3 Where the workspace lives in the cloud

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

**Still open**
- **Tenancy** (§2.1): single-tenant appliance first vs. multi-tenant.
- **Science/environment *repo* split** (Q2 target): when, and its pbg-template
  blast radius (how `build_core()` discovery changes when env is its own repo).
- **"Make work permanent" under an S3 record:** keep the branch + PR *review*
  workflow (a separable collaboration policy) or commit straight to the record?
- **Auth source of truth** (Cognito vs org IdP); first hosted users.

---

## 3. Target architecture (Option A, B-ready)

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
- **Problem:** `pbg-superpowers` imported symbol-by-symbol across 57 lib modules,
  into private `_REGISTRY`.
- **Target:** one `lib/superpowers_api.py` adapter that all call sites import
  from; never touch `_`-prefixed symbols; pin the version (not `branch=main` for
  `investigation-contracts` request models on the API surface).
- **Ship criterion:** a pbg-superpowers bump touches one file to absorb.

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

## 6. AWS specifics (Option A)

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
1. **Tenancy (§2.1):** single-tenant appliance first, or straight to multi-tenant?
   (Recommendation: appliance-first, B-ready.)
2. **Who are the first hosted users** — the two of us + a small lab, or external?
   (Sets the auth/isolation bar.)
3. **Auth source of truth** — Cognito, or an existing org IdP?
4. **Is retiring the local synchronous study-run engine for hosted acceptable**,
   i.e. hosted runs *always* go through sms-api/Batch? (Recommendation: yes.)
5. **Metadata store for B** — RDS Postgres vs DynamoDB, if/when we go multi-tenant.
6. **Do we align the deployment IaC with the existing sms-cdk repo**, or stand up
   a separate stack for the app tier?

---

## 10. Immediate next steps (regardless of the above)
- Land **Phase 0** (guardrails + security stopgaps) — pure win, unblocks safe change.
- Turn Workstreams A–G into a tracking issue / project board with the audit's
  ranked risks as the checklist.
- Prototype the **ALB+OIDC front door** against a single Fargate instance to
  de-risk Phase 1's auth model early.

## 11. What Option B (multi-tenant) additionally requires
- In-app authorization (per-tenant resource scoping), not just an auth front door.
- Completing statelessness (no per-process caches; per-request tenant context end
  to end) and clone-on-start / object-storage workspaces (§2.3 option b/c).
- `runs_meta` → a shared metadata store (§2.5); per-tenant secrets & quotas;
  noisy-neighbor controls on the run backend.
- These are increments on the A design *if* Phase 1–3 keep the "no new global
  state, per-request context" discipline — which is why the recommendation is
  appliance-first-but-B-ready rather than appliance-only.
