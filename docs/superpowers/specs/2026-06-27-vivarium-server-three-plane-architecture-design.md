# Vivarium Server: Three-Plane Architecture — Design

**Date:** 2026-06-27
**Status:** Design (approved in brainstorm; implementation plan to follow)
**Author:** Eran Agmon (with Claude)

## 1. Context

The vivarium-dashboard has grown from a local authoring tool into the front
door of the v2ecoli / process-bigraph ecosystem: it reads git-committed
content (investigations, studies, runs, composites, registry, simulations DB),
triggers simulation runs, and displays results. Recent work made it
FastAPI-native, added a read-only mode flag, in-process workspace switching,
and a commit-agnostic "build via sms-api" path.

We now need a deliberate hosting architecture so that:

- the public can browse v2ecoli results (read-only, no compute) at a stable URL;
- a private, compute-enabled deployment runs large batches on **AWS GovCloud**
  via **sms-api**, evaluated against baselines, with results that **never leave**
  the GovCloud VPC;
- a **public sms-api on our HPC** can later back public runs;
- developers keep a fast **local** loop for new processes, composites, datasets,
  and investigations, promoting finished work by committing it;
- the whole thing is modern, robust, and easily deployable.

This spec defines that architecture. It supersedes the "private → public bridge"
idea from earlier discussion: **there is no bridge.** Each plane is
self-contained.

### Related prior designs (build on, do not duplicate)

- `2026-06-10-read-only-online-dashboard-design.md` — snapshot/read-only viewer.
- `2026-06-18-dashboard-remote-runs-design.md` — sms-api run path, IRSA S3 read.
- `2026-06-23-commit-agnostic-remote-builds-design.md` — build `repo@commit`.
- `2026-06-26-remote-run-thin-client-design.md` — thin-client run surface.

## 2. Goals / Non-Goals

### Goals

1. **One codebase, three configurations.** The dashboard differs across
   deployments by *configuration only* (env), never by code fork.
2. **sms-api as the portable compute control plane.** The dashboard talks to a
   configurable sms-api endpoint; which endpoint it points at determines the
   compute substrate *and* the privacy boundary.
3. **Structural isolation.** The public plane has no network path to GovCloud,
   by construction — not by policy.
4. **Reproducible round-trip by commit.** A single provenance manifest
   (`repo@commit` + lockfile + result-store pointers) is the portable unit in
   *both* directions: **sync-to-local** materializes a remote dashboard's exact
   state for local development, and **promotion** pushes a commit that sms-api
   builds (`repo@commit`) for reproducible large-batch runs. View remote → sync
   local → run exactly → extend → commit → available remote is the core loop.
5. **Easily deployable.** A single container image + 12-factor config + IaC +
   CI/CD per plane. Stateless servers over object store + git.

### Non-Goals

- **No private → public bridge.** GovCloud results never reach the public page.
  This is a hard boundary, not a deferred feature.
- **No public compute yet.** The public plane is read-only until the HPC sms-api
  is ready. (Wiring it is a config change, designed for here, built later.)
- **No multi-tenancy within a plane** in v1. Each plane serves a single
  workspace/result-set context.

## 3. Core Architecture

Three planes, one dashboard image, sms-api as the swappable backend.

```
        LOCAL                  PUBLIC                      PRIVATE
   ┌────────────┐        ┌────────────────┐         ┌──────────────────┐
   │ dashboard  │        │ dashboard      │         │ dashboard + SSO  │
   │ full       │        │ READONLY (now) │         │ full             │
   └─────┬──────┘        └───────┬────────┘         └────────┬─────────┘
         │                       │ (later)                   │
         ▼                       ▼                           ▼
   local engine          ┌──────────────┐            ┌──────────────┐
                         │ sms-api @ HPC │            │ sms-api @     │
                         │ (public)      │            │ GovCloud      │
                         │ SLURM backend │            │ Batch + Ray   │
                         └──────────────┘            └──────────────┘
                          PUBLIC results              PRIVATE results
                          (HPC + committed)           (never leave VPC)
```

**Unifying abstraction:** `dashboard → sms-api(endpoint) → scheduler → results`.
No results cross between planes. Each plane is self-contained: its dashboard,
its sms-api (or none), its result store.

### Plane matrix

| Aspect | Local | Public | Private (GovCloud) |
|---|---|---|---|
| Dashboard mode | full | `READONLY` now → run-enabled later | full |
| Auth | none | none → gating TBD (Open Q1) | Stanford SSO (ALB OIDC) |
| sms-api endpoint | none (local engine) | HPC sms-api (when ready) | GovCloud sms-api |
| Compute backend | local engine | SLURM @ HPC | AWS Batch + Ray |
| Data source | local checkout | public result store | private v2ecoli + S3 |
| Result visibility | local | public | private, never exported |
| Network to GovCloud | no | **never (structural)** | in-VPC |
| Hosting | laptop / mini | commercial PaaS (Open Q2 for sms-api repo) | ECS Fargate in GovCloud VPC |

## 4. Components

### 4.1 Dashboard image (single artifact, config-parameterized)

One Docker image of `vivarium-dashboard`. Behavior is selected entirely by
environment / config:

| Env / config | Meaning |
|---|---|
| `VIVARIUM_DASHBOARD_READONLY` | strip mutating/compute routes (`_apply_readonly_filter`) |
| `VIVARIUM_DASHBOARD_WORKSPACE` | workspace root to serve |
| `VIVARIUM_SMS_API_URL` | sms-api endpoint (unset → no remote compute) |
| `VIVARIUM_RESULT_STORE` | git + object-store location for results |
| `VIVARIUM_AUTH_MODE` | `none` \| `oidc` (+ OIDC issuer/client config) |
| `VIVARIUM_S3_*` / IRSA | result-bucket read access (private plane) |

Principles:

- **Stateless.** The server holds no durable state; it reads from git + object
  store. Any plane redeploys and scales horizontally without migration.
- **12-factor.** All deployment differences are config; the image is identical
  across planes (down to the digest).
- **Read-only is a filter, not a build.** The public plane runs the same image
  with `READONLY=1`; the filter we built keeps GETs + a mutation whitelist.
  When the public plane later gains HPC compute, that whitelist expands to the
  run routes (gated per Open Q1) — still no code fork.

### 4.2 Public plane (live read-only → HPC-backed later)

- The dashboard image with `READONLY=1`, **no `SMS_API_URL`** (now), serving the
  *public result store*: v2ecoli git content + published zarr/parquet artifacts
  mirrored to a **public object bucket** (R2 or commercial-account S3).
- Serves investigations, studies, runs, composites, registry, simulations-DB
  browsing — all read. Every mutating/compute route is stripped.
- **No network path to GovCloud, ever.** Hosted in a *separate account/provider*
  so the air-gap is structural. Even a full compromise reaches nothing private.
- **Hosting:** commercial container PaaS (Fly.io or Render) — managed TLS domain,
  deploy-on-push, cheap, off the AWS/GovCloud accounts. (AWS ECS in a commercial
  account is the one-vendor alternative.)
- **Later:** set `VIVARIUM_SMS_API_URL = <HPC public sms-api>` → the public plane
  gains run capability backed by HPC SLURM. Public results then include HPC runs
  plus committed results. Gating of who may run is Open Q1.

### 4.3 Private plane (GovCloud)

- The full dashboard image (no readonly), `AUTH_MODE=oidc` → **Stanford SSO**,
  `SMS_API_URL = <GovCloud sms-api>`, running **inside the GovCloud VPC**.
- **Topology:** ECS Fargate service in the GovCloud VPC, behind the existing ALB
  (add a listener rule / target group) with **OIDC authentication at the ALB**
  → Stanford SSO. The deployed server reaches sms-api in-VPC — **no SSM tunnel**
  (the tunnel stays a developer convenience, not a server dependency).
- **Compute:** drives sms-api → Ray on **AWS Batch MNP** → **zarr-on-S3** (the
  existing run path). Reads results from S3 via **IRSA** (roadmap Phase 2). This
  is where `RemoteRunManager` + the launch panel (Phase 3) land as the private
  run surface.
- **Baselines:** batch runs land as study runs; the v2ecoli comparison/grader
  infra evaluates them. "Evaluate against baselines" reuses existing capability
  wired to the private run loop.
- Serves the **private** v2ecoli repo/branch — distinct from the public one.
- **Results never leave the VPC.** No export job, no public mirror.

### 4.4 Local loop

- The same image in **full mode** against a local v2ecoli checkout — today's
  authoring experience. Build/test new processes, composites, datasets,
  investigations; run small jobs on the local engine.
- The "local version" is a *run configuration*, not a separate tool. This keeps
  everything single-sourced.

### 4.5 sms-api (portable compute control plane)

- **One API surface, multiple deployments.** sms-api is deployed to a *target*
  (GovCloud / HPC); the dashboard is configured with its URL.
- **GovCloud sms-api:** AWS Batch + Ray backend, private, behind the GovCloud
  ALB (routes restricted to `/api /core /docs /ws /health /version
  /openapi.json /home`).
- **HPC sms-api (future, public):** **SLURM** backend — the natural home for the
  existing-but-unwired `compose`/SLURM path. Public, fronted with appropriate
  rate-limit/quota when public compute opens (Open Q1).
- Whether HPC support is a **backend plugin in the same sms-api repo** (config +
  scheduler adapter) or a **separate service** is Open Q2. The architecture
  prefers one repo with a scheduler-adapter seam (Batch-adapter / SLURM-adapter)
  so the API contract is single-sourced.

### 4.6 Promotion: local → remote (git push → sms-api build)

- **The commit is the unit of promotion.** Commit new work to a branch in the
  target v2ecoli repo, push; sms-api builds `repo@commit` (a pinned, reproducible
  simulator build); the dashboard switches to that build (`source/switch-build`,
  already implemented) and runs large batches against it.
- Reproducibility is pinned by the sms-api build, not by the dashboard.
- Promotion targets are *per plane*: push to the private repo → GovCloud sms-api
  build (private batches); push to the public repo → HPC sms-api build (public
  batches, when ready). Same mechanism, different endpoint.

### 4.7 Provenance manifest (the portable unit)

The thing that makes both the pull (sync-to-local) and push (promotion)
directions exact and symmetric. For whatever a dashboard is showing, it can
emit a small **provenance manifest**:

```jsonc
{
  "repo": "https://github.com/vivarium-collective/v2ecoli",
  "commit": "9d2acad…",                 // exact pin
  "lockfile": "uv.lock@9d2acad",          // pinned dependency closure
  "workspace": "investigations/…",        // which workspace within the repo
  "registry": { /* resolved process/composite registry manifest */ },
  "results": {                            // pointers, NOT the data
    "store": "r2://v2ecoli-public/…",     // public bucket (public plane)
    "runs": ["runs.<id>.zarr", …]          // lazily fetched on view
  }
}
```

- **Code + deps are pinned** (commit + lockfile) → exact reproduction on re-run.
- **Derived caches are regenerated, not shipped** (e.g. ParCa cache via
  `build_cache.py`) — deterministic from the commit, so they don't belong in
  the manifest.
- **Result data is referenced, not embedded** — fetched lazily from the result
  store when viewed; re-running regenerates it. (Per the "git workspace + lazy
  data" decision.)

The manifest is the single source of truth for "what exact state is this?" and
drives both §4.6 (promotion: build remote from the manifest's `repo@commit`) and
§4.8 (sync: materialize local from the same manifest).

### 4.8 Round-trip: sync-to-local (reproduction)

The pull direction, git-native. Closes the loop the user wants:
**view remote → sync local → run exactly → extend → commit → available remote.**

1. **View remote.** Any dashboard (public read-only included) exposes a
   **"Sync to local"** affordance on what it's showing → serves the provenance
   manifest (§4.7) + a copy-paste command.
2. **Sync to local.** A local action — `vivarium sync <manifest-url>` (CLI) or
   the local dashboard consuming the manifest — does, on the user's machine:
   - `git clone`/`fetch` the repo and check out the exact `commit`;
   - materialize the env from the **pinned lockfile** (`uv sync`);
   - regenerate derived caches (`build_cache.py`) — *reproduced*, not downloaded;
   - register the checkout in the local **workspace catalog** so it appears in
     the switcher.
   Result artifacts are **not** pulled eagerly; they're fetched lazily from the
   manifest's result store when a specific run is viewed (cached locally), or
   regenerated by re-running.
3. **Run exactly.** The local dashboard (full mode) runs the synced workspace.
   *Exactness contract:* identical `commit` + identical `lockfile` ⇒ identical
   code + dependency closure ⇒ identical behavior on re-run. Caches are derived
   deterministically; data is regenerated. No artifact shipping required for
   fidelity.
4. **Add to it.** New processes / composites / datasets / investigations,
   developed locally against the reproduced state.
5. **Commit.** Normal git on the synced checkout.
6. **Available remote.** `git push` → promotion (§4.6): sms-api builds the new
   `repo@commit`, the remote dashboard switches to it (`source/switch-build`).
   The push-back closes the round-trip.

**Symmetry:** sync-to-local is the *inverse* of build-via-sms-api. The former
materializes a workspace on your laptop from `repo@commit`; the latter
materializes a build on the remote from `repo@commit`. Both consume the same
provenance manifest, so the source-switch / provenance machinery is shared, not
duplicated.

**Public-plane note:** the public dashboard is read-only and holds no compute,
but it *can* still emit a manifest and serve lazily-fetchable public result
artifacts — so "view public → sync local → reproduce" works with no credentials
and no GovCloud path. Push-back from such a sync targets the **public** repo
(→ HPC sms-api when ready), never GovCloud.

## 5. Data Flow

**Public (now):** browser → public dashboard (readonly) → public result store
(git + public bucket). No compute, no GovCloud.

**Public (later):** browser → public dashboard → HPC sms-api → SLURM → public
result store → dashboard renders.

**Private:** authenticated browser (SSO) → GovCloud dashboard → GovCloud sms-api
→ Batch+Ray → zarr-on-S3 → dashboard reads via IRSA → renders + baseline grade.

**Local:** browser → local dashboard (full) → local engine → local store.

**Promotion (push):** local commit → `git push` → sms-api(target) builds
`repo@commit` → dashboard `source/switch-build` → batch run.

**Sync-to-local (pull):** view remote → manifest (`repo@commit` + lockfile +
result pointers) → local `git clone@commit` + `uv sync` (pinned) +
`build_cache.py` (regenerate) → register in workspace catalog → run locally;
result artifacts fetched lazily from the manifest's result store on view. The
two directions share the manifest — sync-to-local is the inverse of the build.

## 6. Deployability ("modern, robust, easily deployable")

- **One image, 12-factor config** — mode/auth/compute/data all via env; no
  per-deployment code forks.
- **Stateless servers over object store + git** — redeploy/scale without
  migration.
- **IaC:**
  - Private: extend **sms-cdk** with the dashboard ECS service (ALB listener
    rule + OIDC + IRSA S3 read + sms-api wiring).
  - Public: a small separate stack (Fly/Render config or commercial ECS).
  - HPC sms-api: deployment recipe for the HPC target (SLURM adapter).
- **CI/CD:** build image on tag → push to registry → deploy each plane from its
  own pipeline. Image digest is identical across planes.
- **Config validation:** the server validates its env at boot (pydantic settings
  model) and refuses to start on an incoherent combination (e.g.
  `READONLY=0` with no auth on a public-bound host).

## 7. Security / Isolation Model

- **Structural air-gap:** public plane lives in a separate account/provider with
  no route, credential, or DNS path to GovCloud. Isolation is a property of the
  topology, not a firewall rule someone could relax.
- **Private plane auth at the edge:** ALB OIDC → Stanford SSO; the app trusts the
  ALB-injected identity. Defense in depth: app also enforces the readonly/mutation
  split where relevant.
- **Least privilege to data:** private plane reads S3 via IRSA scoped to the
  result bucket/prefix; no long-lived keys in the image.
- **Public compute (when it lands):** rate-limit + per-identity quota + job
  sandboxing on the HPC sms-api before anonymous runs are allowed (Open Q1).
- **CSRF / same-origin** guard on mutations stays on for any plane that exposes
  them.

## 8. Testing Strategy

- **Config matrix tests:** boot the image under each plane's env; assert the
  route set (readonly filter applied/not), auth mode, and sms-api wiring match
  the plane. One parameterized test per row of the plane matrix.
- **Isolation test:** assert the public configuration exposes *no* route or
  client that can reach a GovCloud/sms-api endpoint when `SMS_API_URL` is unset.
- **Promotion test:** `git push` → build `repo@commit` → `source/switch-build`
  → run, against a fake sms-api, asserting the commit is the pinned unit.
- **Round-trip fidelity test:** emit a manifest from a known workspace state →
  `vivarium sync` into a clean dir (`clone@commit` + `uv sync` + `build_cache`)
  → re-run → assert byte/semantic-identical output vs the source state. The
  contract is "same commit + same lockfile ⇒ same behavior"; the test guards it.
- **Manifest symmetry test:** the same manifest feeds both `sync-to-local` and
  `build-via-sms-api`; assert both resolve to the same `repo@commit` + lockfile.
- **Lazy-data test:** a synced workspace renders a run by fetching only the
  referenced artifact from the result store on demand (not eagerly), and a
  re-run regenerates it without needing the fetch.
- **sms-api adapter tests:** the Batch and SLURM adapters satisfy the same
  scheduler-adapter contract (submit / poll / fetch-results), tested against a
  stub scheduler.
- **Deploy smoke tests:** post-deploy health check per plane (`/health`,
  a known investigation renders, readonly planes 405 on a mutation).

## 9. Open Decisions

1. **Public-run gating (Open Q1).** When the public plane gains HPC compute, do
   anonymous users run, or is it the same "internal run" gate backed by HPC?
   Sets whether quotas/sandboxing are day-one or deferred. *Lean:* gated first
   (reuse the internal-run gate), open later behind quotas.
2. **HPC sms-api packaging (Open Q2).** Same sms-api repo with a SLURM
   scheduler-adapter + HPC deploy config, or a separate service? *Lean:* one repo,
   scheduler-adapter seam, so the API contract is single-sourced.
3. **Public-plane hosting (minor).** Fly.io/Render (isolated, fast, cheap) vs.
   AWS ECS in a commercial account (one vendor). *Lean:* Fly/Render, precisely to
   keep the public surface off the AWS accounts.

## 10. Phasing (suggested milestones)

1. **M1 — Public live read-only, hosted.** Image + readonly config + public
   result store + PaaS deploy + domain/TLS. (Mostly assembled already; this is
   productionizing the read-only server we hardened.)
2. **M2 — Private GovCloud deployment.** sms-cdk dashboard ECS service + ALB OIDC
   (Stanford SSO) + IRSA S3 read + GovCloud sms-api wiring. RemoteRunManager run
   surface live behind auth.
3. **M3 — Round-trip pipeline.** The provenance manifest (§4.7) + both
   directions: **sync-to-local** (`vivarium sync` — view remote → manifest →
   clone@commit + `uv sync` + cache rebuild → register + run locally, lazy data)
   and **promotion** (git push → sms-api build → switch → batch → baseline grade).
   The full "view remote → reproduce → extend → commit → available remote" loop,
   documented and repeatable, with the fidelity contract under test.
4. **M4 — HPC public sms-api.** SLURM scheduler-adapter + HPC deploy; point the
   public plane at it; resolve Open Q1 gating. Public-repo push-back from a sync
   then reaches public compute.

## 11. Glossary / existing pieces this builds on

- **Read-only filter** — `_apply_readonly_filter` keeps GETs + a mutation
  whitelist; `VIVARIUM_DASHBOARD_READONLY` env + `ui-config.readonly`.
- **Workspace switch** — in-process re-point (`/api/source/switch {path}`).
- **Build via sms-api** — `repo@commit` builds + `/api/source/switch-build`.
- **Provenance manifest** — portable `repo@commit` + lockfile + result-store
  pointers; the shared unit driving both sync-to-local and build-via-sms-api.
- **Sync-to-local** — `vivarium sync <manifest>`: clone@commit + `uv sync` +
  cache rebuild + workspace-catalog register; the inverse of build-via-sms-api.
- **RemoteRunManager** — dashboard run surface for sms-api (Phase 3).
- **IRSA S3 read** — least-privilege result-bucket access on GovCloud (Phase 2).
- **sms-cdk** — CDK IaC for the GovCloud stack.
- **GovCloud run path** — v2ecoli → Ray → AWS Batch MNP → XArray-zarr-on-S3.
