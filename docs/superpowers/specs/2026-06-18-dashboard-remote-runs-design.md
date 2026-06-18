# Dashboard-triggered v2ecoli runs on smsvpctest (remote runs)

**Date:** 2026-06-18
**Status:** Design — awaiting implementation plan
**Repos touched:** `vivarium-dashboard` (primary), `sms-api`, `sms-cdk`

## Problem

Today, running a v2ecoli simulation on the Stanford GovCloud deployment
(`smsvpctest`) is a manual, multi-tool chore: push code to GitHub, drive the
`atlantis` CLI through build → run → poll → fetch, and inspect outputs by hand.
We want this as a **one-click flow from the vivarium-dashboard**: log in, pick
parameters, launch, watch progress, and see results land as a normal study run.

The remote compute is **v2ecoli on the Ray backend** (AWS Batch multi-node-parallel
transient Ray cluster), which emits an **XArray (zarr) store to S3** (a Parquet
emitter may also be used). sms-api is reachable only via an SSM tunnel at
`http://localhost:8080` (no public endpoint).

## Goal / non-goals

**Goal (v1):** From the dashboard, a logged-in user triggers a v2ecoli run on
`smsvpctest` and the run lands as a **study run** whose results render through the
existing study/investigation chart pipeline.

**Non-goals (v1):**
- Public / hosted-dashboard access to sms-api (stays tunnel-only; server-side proxy).
- A separate post-simulation analysis-module step (the "analysis flush" is the
  XArray emitter flushing to S3, not a distinct analysis run).
- Running arbitrary process-bigraph documents via the SLURM-only `compose/` path.
- Making the compose endpoints work on K8s/Batch.

## Key facts established during design

- **Access model:** local dashboard + server-side proxy. The dashboard's Python
  server calls sms-api over the SSM tunnel (`localhost:8080`). No CORS, no public
  endpoint. (sms-api has `allow_origins=["*"]` and no auth, but we still route
  server-side.)
- **Backend auto-selection:** `sms_api/dependencies.py::get_simulation_service_for_repo`
  routes `v2ecoli → Ray`, `vEcoli → Batch`. `SimulationServiceRay`
  (`sms_api/simulation/simulation_service_ray.py`) submits AWS Batch MNP jobs and
  captures zarr/XArray to S3 (`SIM_OUT_DIR = .pbg/runs/phase0-xarray`,
  `_results_s3_uri = s3://{s3_work_bucket}/{s3_output_prefix}/{experiment_id}/`).
- **Emitter:** XArray (zarr) by default (`run_phase0_xarray_ensemble.py`,
  `--chunk` = flush interval, `config.ray_chunk`). A ParquetEmitter may also be
  used — the results endpoint must be emitter-format-aware.
- **ALB routing:** `sms-cdk/lib/internal-alb-stack.ts` routes only
  `/api`, `/core`, `/docs`, `/ws`, `/health`, `/version`, `/openapi.json`, `/home`
  to the API target group; everything else falls through to PTools. New endpoints
  MUST live under `/api/*` to avoid an ALB change. (`/compose/*` is NOT routed —
  one reason the compose path is unusable here today.)
- **Dashboard seams that already exist:**
  - GitHub **device-flow login** (`lib/github_auth.py`, `static/github-login.js`)
    — stores a token in the keyring (`repo read:org write:packages`), injects
    `GH_TOKEN`/`GITHUB_TOKEN` into subprocesses.
  - **`RunJobManager`** (`lib/run_jobs.py`) — in-process background-thread job
    registry for local subprocess runs, with a poll endpoint. The model to mirror.
  - Study runs persist to `studies/<slug>/runs.db` via a **SQLiteEmitter**; the
    chart pipeline reads from that store.
  - `publish.py` writes `smsApiBase` to `config.json` (currently empty, client-side
    snapshot use).

## Architecture

```
Browser (dashboard UI, GitHub login)
   │  POST /api/remote-run-start            (returns job_id, 202)
   │  GET  /api/remote-run-status?job_id=   (poll)
   ▼
Dashboard Python server
   ├─ RemoteRunManager (background thread; mirrors RunJobManager)
   │     1. push v2ecoli branch  (existing git machinery + GH_TOKEN)
   │     2. POST /core/v1/simulator/upload   → simulator_id ; poll build status
   │     3. POST /api/v1/simulations         → simulation_id (Ray auto-selected;
   │           observables param = emitter config / emitted states)
   │     4. GET  /api/v1/simulations/{id}/status   (poll to terminal)
   │     5. GET  /api/v1/simulations/{id}/observables  (NEW; read S3 emitter)
   │     6. write run record + observables into studies/<slug>/runs.db (SQLiteEmitter)
   │
   └─ sms-api client  ──SSM tunnel──▶  sms-api @ localhost:8080  ──▶  AWS Batch MNP (Ray)
                                                                      └─ XArray/zarr → S3
```

Server reads `smsApiBase` from a **server setting**, defaulting to
`http://localhost:8080`.

## Components

### 1. `RemoteRunManager` (dashboard, new — `lib/remote_run_jobs.py`)
Mirrors `RunJobManager`: a `submit(...)` that starts a background thread running
the 6-step pipeline, a `get(job_id)` returning `{job_id, status, steps[], error}`,
and an in-process registry. Each step has its own status (`pending|running|done|failed`)
and optional message, so the UI can show a four-stage progress strip
(push → build → run → results).

### 2. sms-api client (dashboard, new — `lib/sms_api_client.py`)
A thin `httpx`/`requests` wrapper around the sms-api endpoints the pipeline calls,
parameterized by `smsApiBase`. Mirrors the proven sequence in sms-api's
`E2EDataService` (`app/app_data_service.py`) but only the subset we need. Fails
fast with a clear "remote unreachable — is the tunnel up?" error.

### 3. New sms-api endpoint (sms-api — `sms_api/api/routers/`)
Under `/api/v1/` (ALB-routed, no sms-cdk routing change):
- `GET /api/v1/simulations/{id}/observables/index`
  → `{ observables: [{name, dims, shape}], store: "zarr"|"parquet" }`
- `GET /api/v1/simulations/{id}/observables?names=a,b&...`
  → `{ name: [[t, value], ...], ... }`

Reads the experiment's S3 emitter store. **Emitter-format-aware:** inspect the
experiment's S3 prefix, open zarr (XArray) or Parquet accordingly, slice the
requested observables, return JSON. Uses the api pod's IRSA role for S3 reads.
Bounded responses (the index lets the dashboard request only what it charts; raw
bulk download stays on the existing `POST /data`).

### 4. Dashboard server endpoints (`vivarium_dashboard/server.py`)
- `POST /api/remote-run-start` — body `{study, repo_url, branch, num_generations,
  num_seeds, n_steps, run_parca, observables?}` → `{job_id}` (202). Gated: requires
  an authenticated GitHub session (`github_auth.current_session()`); 401 otherwise.
- `GET /api/remote-run-status?job_id=` — returns the job dict (or recent jobs if no
  id), matching the existing `investigation-run-unblocked-status` shape.

### 5. Dashboard UI (`static/`)
A **"Run on remote (smsvpctest)"** panel within the study view, visible only when
logged in (reuse `github-login.js` state). Inputs: repo/branch (default v2ecoli),
`num_generations`, `num_seeds`, `n_steps`, `run_parca`, and an **observables /
emitter-config selector** (which emitted states to capture — controls compaction).
Launch → `remote-run-start`; progress via the existing poll pattern with the
four-stage strip. On completion the run appears in the study's normal runs list and
its charts render from `runs.db`.

### 6. Landing as a study run (dashboard)
The `simulation_id` is the durable reference handle: the server creates a run record
in `studies/<slug>/runs.db` (same path local runs use) that stores remote provenance
— `simulation_id`, `experiment_id`, commit SHA, backend (`ray`), and the S3 results
URI — keyed by `simulation_id` so results can be (re)queried at any time. It then
fetches the emitted observables via the new endpoint and writes them through the
**SQLiteEmitter**, so the existing chart/observable pipeline renders them with no
special-casing. The stored `simulation_id` also lets the study later re-fetch
additional observables (within whatever the emitter config captured) without re-running.

*Alternative considered:* keep results remote and query the sms-api endpoint
on-demand at chart render time (no local copy). Rejected for v1 because it bypasses
the existing chart pipeline and makes the study view depend on a live tunnel.

## sms-cdk changes
- **No ALB change** — endpoint lives under `/api/*`, already routed.
- **IRSA S3 read:** verify the api pod's role can `s3:GetObject`/`s3:ListBucket`
  on the XArray output prefix (`s3_output_prefix`) in the shared bucket; add a
  policy statement only if missing.
- **Operational (docs only):** document the `kubectl port-forward` fallback for
  when the ALB target flakes to `Target.Timeout` (per sms-api CLAUDE.md), since the
  tunnel is the integration's lifeline.

## Data flow / identifiers
`commit SHA → simulator_id → simulation_id → experiment_id → S3 results URI`.
The dashboard job threads these through and persists them on the study run record.

## Error handling
- Per-step status + error captured in job state; surfaced in the progress strip.
- Tunnel down / sms-api unreachable → fail fast with an actionable message.
- Build or sim failure → job `failed` with the upstream error text; no study run is
  written.
- Long build/sim → never blocks a request; everything is poll-based off the
  background job.
- Partial/missing emitter store at results time → endpoint returns 404/empty; job
  marks results step failed but preserves the run record + S3 URI for retry.

## Testing
- **sms-api:** unit-test the observables endpoint against a small fixture zarr store
  and a small fixture Parquet store; integration-test via the tunnel against a real
  completed `smsvpctest` simulation.
- **Dashboard:** `RemoteRunManager` unit tests with a mocked sms-api client covering
  the happy path and each step's failure; endpoint auth-gating test (401 when not
  logged in). Reuse the existing job-poll test patterns.

## Phasing
- **v1:** push → build → run → poll → observable-query → land as study run +
  render; XArray read with Parquet detection; stand in the study view behind login.
- **Deferred:** hosted/public sms-api access (Verified Access), a separate
  analysis-module step, arbitrary-document compose-on-Ray, multi-study/batch
  triggering.

## Resolved during review
1. **Run identity / study attachment:** the remote run returns a `simulation_id`
   that is stored on the study run record as the durable reference handle. The run
   attaches to the study from whose view it was launched.
2. **Observables:** observables/readouts are the **emitted states**, set in the
   **emitter config at submission** (the `observables` param on
   `POST /api/v1/simulations`). This controls data compaction (especially well
   suited to the XArray emitter) and defines what the results endpoint can return.
   The launch panel exposes this selector; charts default to the emitted set.

## Open questions for review
1. Whether `num_generations`/`num_seeds`/`n_steps` defaults should come from the
   `config.ray_*` server defaults (`n_steps=600`, `chunk=60`, `parca_mode=full`) or
   be dashboard-set per launch.
2. Default observable/emitter-config preset offered in the launch panel (a sensible
   v2ecoli readout set) vs. requiring an explicit selection each time.
