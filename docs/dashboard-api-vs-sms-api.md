# Dashboard API vs sms-api — boundary note

## Two services, two layers

**Dashboard typed API** (`vivarium_workbench/api/app.py`, this seam) reads the
**local workspace**: investigations, studies, composites, registry, charts, and
catalog.  It is a strangler-fig migration of the stdlib `http.server` handler
— routes move here one at a time, backed by the same `lib/` functions, so
there is one implementation, not two.  All 14 routes (`/health` + 13 GET
routes) are read-only and stateless.

**sms-api** (GovCloud, `smsvpctest`) is the simulation **backend**: it launches
runs, stores results in XArray/Zarr on S3, and exposes them via versioned paths:

| Path prefix | Purpose |
|---|---|
| `/core/v1/simulator/{versions,latest,status,upload}` | Simulator image registry |
| `/api/v1/simulations/{id}/{status,data,observables}` | Per-run results and streaming |
| `/compose/v1/*` | Composition/orchestration endpoints |

## Complementary, not overlapping

The dashboard **consumes** sms-api — it does not duplicate it.  The stdlib
server's `/api/source/builds` and `/api/source/switch-build` routes call
sms-api's `simulator/versions` endpoint today (hand-written in
`lib/sms_api_client.py`).  Those build-management routes have not yet been
ported into the typed seam (`app.py`), so the dependency is implicit.

Neither service owns the other's domain:

- **Dashboard** owns: workspace layout, investigation/study metadata, composite
  specs, registry introspection, BibTeX references, visualization catalog.
- **sms-api** owns: simulator builds, run lifecycle, result storage/streaming.

## Shared concepts (potential model alignment)

Two concepts appear on both sides with different names and shapes:

| Concept | Dashboard | sms-api |
|---|---|---|
| A simulation run | `SimRow` (index entry, workspace-local) | `SimulationRun` (compute record, cloud) |
| A simulator version/build | build entry in `/api/source/builds` response | `SimulatorVersion` from `/core/v1/simulator/versions` |

These are not the same thing — `SimRow` is a local index entry (db path,
study slug, emitter type); `SimulationRun` is a cloud compute record (S3
location, resource usage).  They share a `run_id` key, which is the natural
join point.

## Conventions that differ

| Convention | Dashboard API | sms-api |
|---|---|---|
| Path structure | Flat: `/api/<resource>` | Versioned: `/core/v1/`, `/api/v1/`, `/compose/v1/` |
| Schema typing | pydantic v2 + FastAPI | (varies; OpenAPI published separately) |
| Scope | Local workspace reads | Remote compute + result storage |

The flat vs. versioned path convention is worth aligning if the dashboard ever
exposes public/multi-client routes.  For now, the two services are on separate
hosts and the mismatch causes no practical conflict.

## Recommended reconciliation (separate services, reduced drift)

1. **Generate the dashboard's sms-api client from sms-api's OpenAPI spec.**
   Replace `lib/sms_api_client.py` (hand-written, drifts silently) with a
   generated client (`openapi-python-client` or equivalent) pinned to the
   sms-api schema version.  This is the highest-leverage change: a one-time
   fix that eliminates a whole class of drift.

2. **Share the 2-3 genuinely-shared models.**  Define `SimRunRef` (the
   `run_id` + `study_slug` join key) in a shared location (e.g.
   `vivarium_workbench/lib/models.py`) so the sms-api client and the
   `SimRow` index use the same field names at the boundary.

3. **Document ownership explicitly.**  Add a one-line ownership comment to
   each `lib/sms_api_client.py` call site: "sms-api owns this, dashboard
   is the consumer."  Prevents future routes from accidentally duplicating
   sms-api logic inside the dashboard.

Otherwise, keep the services independent: separate deployments, separate
schemas, separate release cycles.  The goal is a clean interface, not a merge.
