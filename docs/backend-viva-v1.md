# Backend contract: `/viva/v1` (what the workbench sends, and to whom)

Goal: `vivarium-workbench serve --backend-base-url https://sms.cam.uchc.edu --workspace ./`
runs a workspace's studies / composites through the backend's generic
`/viva/v1/composites` run surface instead of the simulator-keyed `/api/v1/simulations`.
The UI and VivaChat stay local; only dispatch, status and results cross the wire.

## Provenance (read, not recalled)

| Claim | Source |
|---|---|
| Schemas, routes, status codes below | `GET /viva/v1/openapi.json` of a live deployment (viva-core **0.1.9**, 96 paths), fetched 2026-09-30 with GETs only; snapshot committed at `tests/_contracts/viva_core_openapi_0.1.9.json` |
| Capability names, dual-period semantics, which deployments run which mode | viva-api `origin/main` `docs/architecture-core.md` ("`/viva/v1/composites`"), `app/smoke.py` (`check_composites_*`), `app/contract.py` |
| Live capability/health values | `GET /viva/v1/capabilities`, `GET /viva/v1/health` on the same deployment |

The snapshot is the contract the tests run against (`tests/_viva_v1_stub.py` generates
routes, request validation and default answers from it). Refresh it by fetching the
deployment's `/viva/v1/openapi.json` again; a failing contract test then names the drift.

## What the live target is (important)

`sms.cam.uchc.edu` is a **standalone viva-core**, not a full viva-api:

* `GET /viva/v1/capabilities` -> `viva-v1-composites, viva-v1-composites-documents, viva-v1-jobs, viva-v1-surface, viva-v1-workers`.
  No `viva-v1-environments`, no `viva-v1-datasets`.
* `GET /viva/v1/health` -> `services`: `composites: true`, `jobs: true`, `environment_records: false`, `datasets: false`.
* There is **no `/api/v1/*` at all** (`GET /api/v1/version` -> 404; the OpenAPI document has zero non-`/viva/v1` paths). The workbench's current remote path cannot work against it.
* `GET /viva/v1/environments` -> 503 "this deployment provides no environment store"; docs: a standalone core "refuses an environment id and a composite by id, each by name". It runs **documents** in a **site-named environment** (existing runs show `environment: {"id": null, "name": "deployment-default"}`).

The SMS (Stanford/full) deployments instead advertise `viva-v1-environments` (+ `-build`, `-filters`), run composites **by id** (`ecoli-simulation`) in an environment **id**, and still serve `/api/v1`.

## Dispatch: `POST /viva/v1/composites` -> `202 CompositeRunModel`

Request `CreateCompositeRunRequest` (required: `environment`; exactly one of `document` / `composite`):

```
{ "environment": {"id": "<env id>"} | {"name": "<site environment>"},   # exactly one key
  "composite":   {"id": "<composite id>", "params": {...}},             # XOR
  "document":    { ...process-bigraph document... },                    # XOR
  "execution":   {"protocol": null|str, "options": {...}},              # optional; options passed through
  "label":       "<human name>" }                                       # optional
```

Answers: `202` run; `409` environment not ready; `422` unknown composite/protocol/environment or refused params; `503` no composite runner.
`CompositeRunModel`: `id` (opaque string, e.g. `simulation-D55FF7B`; pass back as given), `status` (`JobStatus`), `spec` (`document`|`composite`), `environment`, `composite_id`, `params`, `document_address`, `execution`, `label`, `created_at`, `created_by`, `message`, `trace_id`, `job_id` (e.g. `compose:38`).
`JobStatus`: `unknown waiting pending queued running completed cancelled failed` (lower case).

### Reads (all `GET`, `{id}` = run `id`)

| Route | Answer |
|---|---|
| `/viva/v1/composites` | `CompositeRunPage {runs, limit, offset, next_offset, total}`; filters `status` (repeatable), `composite_id`, `environment_id`, `created_by`, `limit` (1-200), `offset` |
| `/composites/{id}` | `CompositeRunModel` |
| `/composites/{id}/status` | `{id, status, message}` |
| `/composites/{id}/progress` | `{id, status, total, by_kind: {kind: {status: n}}}` |
| `/composites/{id}/jobs` | `{jobs: [JobModel]}` (`id`, `kind`, `owner_kind`, `owner_id`, `status`, `backend`, `external_job_ids`, `output_uri`, `trace_id`) |
| `/composites/{id}/datasets` | `DatasetPage`; **503 when the deployment has no dataset store** (live: yes, 503) |
| `/composites/{id}/events` | `CompositeRunEventsModel {id, trace_id, events, next}` (`after`, `limit`) |
| `/composites/{id}/log` | `text/plain`; `?full=`; **404 when the deployment cannot read one** (live: 404 for a document run) |

### Cancel: `DELETE /viva/v1/composites/{id}`

`200 {run, pending}` cancelled or already finished; `202` cancelled but `pending` names what is still being stopped; `401/403` run has an owner and caller is not it; `409` cannot stop right now, retry shortly; `501` this deployment cannot stop this kind of run. The record is kept.

## Mapping a workspace composite onto the request

A study's `baseline[0]` is `{composite: "<dotted id>", params: {...}}`
(e.g. `viva_biomodels.composites.batch_compare_biomodels.batch-compare-biomodels`).

**Finding that shapes the design.** A composite *by id* (`composite: {id, params}`) is served today
only for the one composite SMS registers (`ecoli-simulation`, params = `POST /api/v1/simulations`'s
own), and a standalone core refuses it by name (docs/architecture-core.md, "`/viva/v1/composites`").
So an arbitrary workspace composite cannot be dispatched by id. The general route is the **document**:

* `viva-v1-document` (backend advertises `viva-v1-composites` AND `viva-v1-composites-documents`):
  the workspace composite is exported with `pbg_export.export_composite_pbg(ws, composite_id, overrides=params)`
  (a process-bigraph document, process addresses rewritten to `local:!module.qualname`) and sent as
  `document`, with `environment` and `execution.options`:
  * `environment`: `{"name": "runtime"}` (default; the **only** name `NAMED_ENVIRONMENTS` accepts in
    viva-core — `deployment-default` is merely the label a legacy compose submission is *recorded*
    under), or `{"id": "<env id>"}` via `VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT=id:<env id>`.
  * `execution.options` (closed set, `extra=forbid`): `interval_time` (= steps, 0..100000);
    for a named environment `extra_pip_deps` = `git+<origin>.git@<HEAD>` (+ the workspace's pinned
    framework versions) so the workspace code is installed in the container — the same contract as
    legacy `/compose/v1`, including the server's `compose_allow_list`; a named environment **refuses**
    `num_nodes` / `analysis_options`; for an environment id `analysis_options` is sent and no pip deps.
  * A named-environment dispatch requires a clean, pushed workspace (`remote_dispatch_preflight`).
* `legacy`: anything else (no capabilities route, or a run surface without documents) — unchanged
  `/compose/v1` + `/api/v1/simulations`.

**Unverified (needs a real run, the owner's call):** that the `runtime` image plus `extra_pip_deps`
resolves a given workspace's processes, and that the repo is on the backend's allow-list.

## Where the workbench uses it

Everything is gated on an operator-**named** backend (`--backend-base-url` /
`VIVARIUM_WORKBENCH_BACKEND_BASE_URL`; the `VIVA_API_BASE` / `SMS_API_BASE` aliases do **not** opt in,
so existing deployments are byte-for-byte unchanged) AND that backend advertising
`viva-v1-composites` + `viva-v1-composites-documents` (`server_capabilities.viva_v1_dispatch_active`,
cached 30 s). A session build (`.viv-build.json`) or the pinned-build config keeps the simulator-keyed path.

| Workbench operation | Route taken |
|---|---|
| `serve` start-up | probe `/version`, `GET /viva/v1/capabilities` + `/health`; prints `backend dispatch: <mode>` |
| `GET /api/remote-run-config` | unchanged payload + `backend: {dispatch, version, capabilities, services, reachable, error}` (key absent when no backend is named) |
| Run target (`remote_pinned.resolve_run_target`) | `deployment` when the above holds |
| Study baseline / variant run, `POST /api/remote-run-submit` (no `simulator_id`) | `POST /viva/v1/composites` (document); answer `{run_id, phase, backend}` |
| Composites-tab / detached run (`run_remote`) | `POST /viva/v1/composites`, poll `/composites/{id}/status`, results `GET /viva/v1/compose/simulation/{n}/results` |
| `GET /api/remote-run-poll?run_id=` | `/composites/{id}/status` |
| `GET /api/remote-run-chain-progress?run_id=` | `/composites/{id}/progress` (`simulation` jobs = seeds) |
| `POST /api/remote-run-cancel {simulation_id: <run id>}` | `DELETE /composites/{id}` |
| `GET /api/remote-dispatch-preflight` | clean+pushed check; `dispatch: viva-v1-document` |

Not yet wired to `/viva/v1` (still legacy, or unsupported against a viva-core-only backend):
landing a finished **study** run into the study (`/api/remote-run-land`, still simulator/`/api/v1`
shaped), analyses (`/api/v1/analyses*`), build/branch pickers, `/composites/{id}/{jobs,datasets,log,events}`
(client methods exist, no UI consumer yet), and the ecoli pinned flow (`simulator_id`).

## Fetching results

| Deployment | Route | Note |
|---|---|---|
| any with the compose surface | `GET /viva/v1/compose/simulation/{n}/results` (`application/zip` or `gzip`), `n` = the integer after `compose:` in the run's `job_id` | verified unauthenticated, live (HTTP range read of a finished document run: 206, `application/zip`) |
| with a dataset store | `GET /viva/v1/composites/{id}/datasets` then `GET /viva/v1/datasets/{dataset_id}/content` | live: store absent (503) |
| SMS (full viva-api) | viva-api's own smoke still downloads via legacy `GET /api/v1/simulations?experiment_id=` then `/api/v1/simulations/{id}/data` | **no `/viva/v1` equivalent yet for an `ecoli-simulation` run's output** |

## Still legacy (no `/viva/v1` equivalent found)

Listing/registering simulators and branch builds (`/core/v1/simulator/*`, partly bridged by
`/viva/v1/environments` already), `/api/v1/simulations/{id}` provenance record and
`/chain-progress`, `/api/v1/analyses*` (analysis dispatch/status/plots), `/api/v1/simulations/discovery`,
`/api/v1/simulations/{id}/data` downloads, trace (`/viva/v1/composites/{id}/trace` is used where advertised).
These stay on the legacy `SmsApiClient` methods.

## Identity / auth

None is required for any call above on the live target (all reads above were unauthenticated).
Cancel of an owned run needs the caller identity the client already forwards (`X-Auth-Request-Email`,
`caller_identity()`); the workbench never stores or sends other credentials. `--backend-base-url`
refuses a URL with embedded credentials.
