# Remote-run thin-client simplification design

**Date:** 2026-06-26
**Status:** approved (direction), ready to plan
**Context:** an API-simplification review (mapping the external **sms-api** service against the
vivarium-dashboard's remote-run machinery) found the dashboard re-implements, less reliably,
async orchestration that sms-api already owns server-side. This spec defines collapsing the
dashboard's remote-run path to a **thin client** of sms-api.

## The finding

sms-api (`/Users/eranagmon/code/sms-api`) is a complete async simulator service. `POST
/api/v1/simulations` launches a multi-phase workflow (build→parca→run→analysis) on HPC/K8s and
returns a job_id immediately; a server-side **JobScheduler** polls HPC every 30s and subscribes to
Redis worker events, maintaining a **durable Postgres state machine** (PENDING→RUNNING→
COMPLETED/FAILED); the client just polls `GET /api/v1/simulations/{id}/status` and fetches results
via `/data` or `/observables`. The intended client contract: **submit → poll status → fetch results.**

The dashboard instead wraps this in its own re-orchestration — `lib/remote_run_jobs.py`'s
`RemoteRunManager` runs a 6-stage client-side pipeline (`push → build → run → poll → download →
land`) on a **daemon thread** that **blocks polling sms-api `/status` every 5s for up to an hour**
(`_poll()`, lines 141–193) and mirrors sms-api's durable state in a **non-durable in-process
`RemoteRunJob.steps[]`** (lost on dashboard restart). This duplicates, less reliably, three things
sms-api already does: background polling, multi-stage orchestration, and durable job state.

## Decision (user, 2026-06-26): thin-client rewrite

Drop the client-side async machinery and lean fully on sms-api:
- **DELETE** `RemoteRunManager`, its daemon thread, the `_poll()` blocking loop, and the in-process
  `RemoteRunJob.steps[]` state.
- **`remote-run-start`** → submit to sms-api (`POST /api/v1/simulations`, after ensuring the
  simulator build exists) and return sms-api's job/simulation id. No background thread; returns
  immediately with the sms-api id.
- **`remote-run-status`** → read sms-api `GET /api/v1/simulations/{id}/status` **on demand** per
  client poll, mapped to the UI's expected shape. No in-process state; durability comes from
  sms-api's Postgres.
- **Landing becomes an explicit, on-demand step** (a separate route/action) the user invokes when
  they want results materialized locally (`runs.db`/zarr for the local viz path) — NOT an automatic
  final pipeline stage. When local materialization isn't needed, results are read directly from
  sms-api `/observables` / `/data`.

## What stays (real value-add — do NOT remove)

- `source/build-remote` — repo-URL normalization + centralized sms-api error/tunnel handling.
- `source/switch-build` — per-commit workspace caching (`materialize_build`).
- remote-simulations list — filters sms-api's list by the active build (`.viv-build.json`).
- `land_remote_run` (the function) — kept, but invoked on demand rather than inline in a pipeline.

## What to drop/slim

- `source/builds` — a thin passthrough of `list_simulators`; drop the dashboard route if the UI can
  call the simulations-list path, or keep as a 1-line read. (Decide during implementation; low stakes.)

## Phased plan (each behavior-checked, reviewed)

- **R1 — sms-api client gaps:** ensure `lib/sms_api_client.py` exposes the calls the thin path needs
  (`run_simulation`/submit, `simulation_status`, and whatever build-ensure step is required). It
  already has `latest_simulator`/`register_simulator`/`simulator_status`/`list_*`/`download_*`/
  `observables*` — confirm the submit + status signatures; add none that aren't needed.
- **R2 — thin `remote-run-start`:** rewrite `lib/remote_run_views.remote_run_start` to: ensure the
  build (reuse the existing build-ensure path / `source/build-remote` logic), submit via sms-api,
  return `{job_id: <sms id>}`. Remove the `PipelineCtx` + `run_remote_pipeline` + `manager.submit`
  usage. Keep the FastAPI route (already ported) calling the new builder.
- **R3 — thin `remote-run-status`:** rewrite to query sms-api `/status` on demand and map to the
  UI shape (the existing `job_status_views` shape or a small mapper). Remove the
  `remote_run_jobs.manager` read.
- **R4 — explicit landing:** add a `POST /api/remote-run-land` (or fold into an existing action)
  that calls `land_remote_run` on demand; remove the inline `land` pipeline stage.
- **R5 — delete the dead machinery:** remove `RemoteRunManager`, `RemoteRunJob`, `run_remote_pipeline`,
  `PipelineCtx`, the `_poll()` loop from `lib/remote_run_jobs.py` (keep only what R2–R4 still use);
  remove the now-unused server wiring. Update tests.

## Interaction with the other in-flight work

- This is the **remote** run path; the **study-run engine extraction**
  (`2026-06-26-study-run-engine-extraction-design.md`) is the **local subprocess** path — they are
  independent run systems. Doing this first means the eventual FastAPI flip carries far less async
  machinery (no daemon-thread manager to move into a lifespan).
- `remote-run-start` (#352) and `remote-run-status` (#349) are already ported to FastAPI; this is a
  behavior-simplifying refactor of that already-merged code, not a new port.

## Constraints

- **Behavior-observable from the UI is preserved** (the user still submits a remote run and watches
  status), but the *mechanism* changes (sms-api owns async/state). Where the response shape changes,
  update the JS consumer (`static/*.js` remote-run panel) accordingly — this is a real behavior
  change, NOT a byte-identical port, so it is NOT gated by the migration's byte-identical rule; it
  needs functional tests + a UI check against a live sms-api.
- **Python-first, AI-free. No new deps** (sms-api client is stdlib urllib). Tests monkeypatch the
  sms-api client — never hit a real sms-api.
- The actual cutover of the remote-run UX should be **verified against a live sms-api tunnel** before
  merge (the tunnel setup is in memory: `ptools-proxy.sh -s smsvpctest -p 9000`, `SMS_API_BASE`).

## Risks / watch-items

- **Status shape mapping:** the UI currently renders `RemoteRunJob.steps[]` (push/build/run/poll/
  download/land). sms-api exposes a single run status (+ build status separately). The mapper must
  produce a UI-renderable shape from sms-api's status; confirm what the JS panel needs and whether
  the multi-step display still makes sense or collapses to build-status + run-status.
- **Job history across restart:** previously in-process (lost on restart); now durable in sms-api —
  this is an improvement, but the status route needs the sms-api id to query (the client must hold
  it). Ensure `remote-run-start` returns an id the client persists.
- **Landing trigger:** moving landing out of the auto-pipeline means a run's results aren't local
  until the user lands them; confirm the downstream viz/study-run flows tolerate "results live in
  sms-api until landed."
- **Build-ensure step:** `POST /simulations` needs an existing simulator build; the thin start must
  still ensure/trigger the build (reuse `source/build-remote`/`switch-build` logic) — don't drop
  that.
```
