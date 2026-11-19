# Backend adapters: the workbench on viva-core and compose-api only

Status: **in progress**, design settled 2026-11-18. Where each step stands is in [Progress](#progress), the
single record of it.

## Progress

Every PR that advances this plan updates this table in the same PR. Steps are defined in §4; asks are on
viva-api#1055. "Q2" means §5's Q2 (analyses), which is Question 1 in viva-api#1055.

| Step | What | PR | State (date) |
|---|---|---|---|
| 0 | SMS-call ratchet; drop the dead `composite_resolve` fallback | wb#1646 | **done**, merged 2026-11-19 |
| 1a | `lib/backends/`: the three adapters, `Backend`, `for_run`; SMS code moved to `sms_legacy.py`; routing unchanged | wb#1652 | **done**, merged 2026-11-19 |
| 1b | `backends.named/select/active`: one place picks the backend for a new run; the six predicates become wrappers; submit goes through the adapter | wb#1653 | **done**, merged 2026-11-19 |
| 1c-1 | One remote backend per process (startup refusal); every remote run records where it went (`params.remote`); `backends.records.resolve_existing` | wb#1654 | in review (2026-11-19) |
| 1c-2 | Views, routes, reconciler and figures route existing runs by their record (`resolve_existing`); the Runs tab sends its row's deployment | (this PR) | in review (2026-11-19) |
| 2 | `EnvironmentRef`; new pinned runs on `ecoli-simulation` | — | blocked only on landing: ask E, or the job-owner interim (§4 step 2). Both sites advertise the capabilities and define `ecoli-simulation` (checked 2026-11-18) |
| 3 | Composites tab off `/viva/v1/compose` | — | not started; composites that carry data files wait on ask C |
| 4 | Reads off `/api/v1` | wb#1650 (analysis figures and PTools files, taken early) | figures and PTools files **done**, wb#1650 merged 2026-11-19; its S3 fallback stays until prod's registry covers its analyses (prod question below). Remaining: the Remote-tab run listing, `/source`, trace, other data downloads |
| 5 | Old runs carry the run id | — | waits on ask A |
| 6 | Analyses as a core job kind; delete `sms_legacy.py` | — | waits on viva-api building the analysis job kind (Q2, decided; no ask filed yet) |
| W-a | Post-run analyses off the hosted worker | — | waits on step 1 and Q2 |
| W-b | Catalog from the manifest | — | waits on ask D |
| W-c | Authoring and source as plain file access | — | not started |

| Ask (viva-api#1055) | What | State (date) |
|---|---|---|
| A | `run_id` on `/api/v1/simulations/{id}` and its status | asked 2026-11-18, no reply |
| B | one file inside a prefix dataset; needed only for files not registered on their own | low priority (2026-11-19): figures and PTools files are registered singly |
| C | data files with a document on `POST /composites` | asked 2026-11-18, no reply |
| D | `/environments/{id}/manifest` and `/source` (501 stubs today) | asked 2026-11-18, no reply |
| E | `/composites/{id}/results` for SMS runs | asked 2026-11-19, no reply |
| — | why prod's dataset registry is under 1% of stanford-test's | asked 2026-11-19, no reply |

Related, already on `main`: wb#1642 (runs on compose-api send their data files, 2026-11-18; viva-core waits on
ask C); wb#1644 (landing leaves
PTools TSVs in the study, where pbg-ptools finds them, 2026-11-18).

**Goal.** The workbench reaches a remote backend only through one interface. That interface has two
implementations:

- **viva-core**: the `/viva/v1/*` surface that viva-api serves, and any other deployment of viva-core;
- **compose-api**: biosimulations/compose-api.

Calls to the SMS-specific surfaces (`/api/v1/*`, `/core/v1/*`, and the interim `/viva/v1/compose`) are removed
from everything except one frozen module. That module serves only runs recorded before the move, and it is
deleted when viva-api retires those routes (its milestone M6). Routes the PTools page still calls are not
retired by this plan (§4, "Contracts every step must keep").

Neighbours:

- [run-backend.md](run-backend.md) covers *local vs remote*: detached runs, durability and reconciliation. This
  page covers *which remote*. The two compose: run-backend.md's cloud adapter becomes whichever adapter this page
  selects.
- [backend-viva-v1.md](backend-viva-v1.md) and [backend-compose-api.md](backend-compose-api.md) are the wire
  contracts of the two implementations.
- `tests/test_backend_interop.py` pins the behaviour each backend must keep.

---

## 1. Today

### Two questions answered by one switch

Every remote run answers two independent questions:

1. **Which environment** does the code run in? It can be:
   - a workspace build the session materialized (`.viv-build.json`);
   - the build pinned to a branch for the whole deployment (`VIVARIUM_WORKBENCH_REMOTE_PINNED`);
   - a compose-api registry entry's image;
   - an environment compose-api infers from the document's classes (mash-up, wb#1513).
2. **Which API** carries the run? It can be:
   - legacy SMS (`/api/v1/simulations`, `/core/v1/simulator`, `/viva/v1/compose`);
   - viva-core (`POST /viva/v1/composites`);
   - compose-api (`POST /simulation/run`).

The code answers both with the same predicates: `remote_pinned.resolve_run_target`, `uses_viva_v1_dispatch`,
`uses_compose_dispatch` and `server_capabilities.explicit_backend_profile`. In practice:

- A pinned or session build **always** takes the SMS API, even on a backend that serves `/viva/v1/composites`.
- The document routes are used **only** when the operator names a backend with `--backend-base-url`.

So "the Stanford flow" and "the SMS API" look like one thing, but they aren't. viva-core already accepts the
Stanford flow (§3).

### Where the branching lives

The predicates are tested in 13 modules. `SmsApiClient` is named in 19 modules besides the client itself. The run lifecycle is routed by the form of
the run id:

| Run id | Example | Route | Module |
|---|---|---|---|
| integer | `12345` | legacy SMS | inline in `remote_run_views.py` |
| `compose-<n>` | `compose-4706` | compose-api | `remote_run_compose.py` |
| any other string | `simulation-D55FF7B` | viva-core | `remote_run_viva_v1.py` |

Two run entry points branch separately:

- **Study runs** go through `remote_run_views.remote_run_submit`, then `land`, `status`, `progress` and `cancel`.
- **Composites-tab runs** go through `remote_run.run_remote`, then `run_jobs`. Its legacy branch still calls
  `compose_submit` and `compose_status_batch` on the interim `/viva/v1/compose`.

### An informal adapter interface already exists

`remote_run_viva_v1` and `remote_run_compose` each expose `is_run_id`, `submit`, `land`, `status`, `progress` and
`cancel`. compose-api also has `trace`. Each returns the legacy view's `(dict, status)` shape, so the panel and
the polling code don't care which one answered. Extracting the interface is therefore mostly *naming what is
there* and moving the inline SMS code into a third module with the same shape.

## 2. Design

### 2.1 One backend object per base URL, with optional facets

```python
class Backend(Protocol):
    name: str                       # "viva-core" | "compose-api" | "sms-legacy"
    base_url: str
    capabilities: frozenset[str]    # what it advertises (membership, never version)
    runs: RunFacet                  # required
    environments: EnvFacet | None   # resolve/build/status/source
    workers: WorkerFacet | None     # live env workers (viva-core only today)
    analyses: AnalysisFacet | None  # re-run analyses on a finished run (sms-legacy only today)


class RunFacet(Protocol):
    def owns(self, run_id: str) -> bool: ...
    def submit(self, req: RunRequest) -> RunHandle: ...    # returns immediately
    def status(self, run_id: str) -> RunStatus: ...
    def progress(self, run_id: str) -> RunProgress: ...
    def cancel(self, run_id: str) -> None: ...
    def land(self, run_id: str, dest: LandTarget) -> LandResult: ...  # fetch outputs into runs.db
    def trace(self, run_id: str) -> bytes | None: ...


@dataclass
class RunRequest:
    environment: EnvironmentRef           # §2.2
    composite: CompositeRef               # a document, or a server-side id like "ecoli-simulation"
    params: dict                          # for a composite by id, the server's own params model
    steps: float | None
    files: dict[str, Path] = field(default_factory=dict)  # data a document names (wb#1642)
```

- **Facets are optional, and the UI reads presence, not backend names.** An absent facet hides its feature: no
  `analyses` facet means no "re-run analyses" button. This is the same "membership, never version" rule the
  capabilities route already follows.
- **Workers are an optional capability, and stay out of `runs`.** viva-core provides hosted workers; compose-api
  never will, which Jim and Alex agreed on 2026-11-18. On compose-api the workbench runs with a local workspace
  venv: authoring, probes and viz previews run in the local env worker, and runs go to compose-api as batch jobs.
  The `workers` facet means *hosted* workers only. The local subprocess launcher is not a backend feature and is
  always available when a local venv exists. The goal is to depend on hosted workers less over time (§3.1), and
  to remove the facet once SMS turns out not to need it either.
- `files` exists because compose-api now takes data files with a document (wb#1642). viva-core does not. Its
  adapter refuses a request that carries `files` with a clear message until viva-core can accept them (§5, Q5).

### 2.2 The environment is a value, not a route

```python
EnvironmentRef = (
    BuildRef(environment_id)         # a viva-core /environments row (was: the session build or pinned build)
  | RegistryRef(name, version)       # a compose-api registry environment
  | InferRef()                       # let the backend choose (compose-api mash-up)
)
```

- The session build and the pinned branch stop choosing an API. They become two ways of producing a `BuildRef`
  (`remote_pinned.resolve_pinned_build` already resolves through `/viva/v1/environments` first).
- Each backend's `environments` facet resolves and builds the refs it understands. A backend given a ref it
  doesn't understand refuses it, with no fallback. Example: compose-api given a `BuildRef`.
- This is also where science/environment separation plugs in later: an `environment:` entry in `workspace.yaml` is
  one more way of producing a ref.

### 2.3 Selection happens in one place

```python
backends.active() -> Backend | None   # from the named URL, or else the alias; read once, cached like the profile
backends.for_run(run_id) -> Backend   # the adapter whose runs.owns(run_id) is true
```

- Step 1b added `named()` (the named backend's profile alone), `select(ws)` (session build, then pin, then `named()`; never
  raises) and `active(ws)` (`select` plus wb#1630's refusal). The six predicates stay as wrappers and primitives because
  tests patch `resolve_run_target` at its call sites; callers of the `uses_*` predicates now ask `backends` directly.
  Like today, `active()` reads only the named URL; the aliases do not count.
- `active()` replaces `resolve_run_target`, `uses_viva_v1_dispatch`, `uses_compose_dispatch`,
  `uses_named_backend_dispatch`, `compose_dispatch_active` and `viva_v1_dispatch_active`. Every call site asks one
  question: `active()`, then `.runs.submit(...)`.
- **New runs and existing runs are different questions (Jim, 2026-11-19).** A *new* run goes to the one configured
  backend (`select` / `active`). An *existing* run is answered by the backend it was dispatched to, which its stored
  record names: every remote run's row carries `params.remote = {kind, id, base_url, deployment, server_id}` (step
  1c-1), and `backends.records.resolve_existing` routes by it, refusing (409 `other-backend`) a run that belongs to
  another backend instead of asking the configured one about it.
- **A run's provenance is (server identity, run id); a URL is only an address.** The same deployment has several URLs
  (in-cluster name, tunnel, public), and different deployments share one (both Stanford sites are `http://api:8000`;
  every laptop tunnel is `http://localhost:8080`). So when the record and the configured server both report an
  identity (`server_id`: viva-api#1055 ask F, a stable `deployment_id` on `/viva/v1/health`), the identity alone
  decides. Until both do, the URL and the deployment name must both match: a best-effort guard that cannot tell
  apart deployments sharing a URL and a name. Rows written before 1c-1, runs listed live from the configured server
  and the study page's own dispatches fall back to `for_run()`'s id forms.
- **Every operation on an existing run goes through its record (step 1c-2).** `backends.for_existing(ws, id,
  deployment=, study=)` is used by land, status, chain progress, cancel, land-artifacts, re-run analysis, trace, the
  figure routes and study gallery, the store download, `composite_run_views` and the reconciler. The id is `run_id`,
  else `simulation_id` (for land, `simulation_id`, else a compose-api `run_id`), stripped; a falsy id is no id.
  - A run that belongs to another backend is refused before anything is asked: **409**, `{error, reason
    ("other-backend" | "ambiguous" | "not-sms"), phase, terminal: true, simulation_id, run, dispatched_to?}`. Pollers
    treat it as terminal; the study page shows the reason and hides "Stop campaign".
  - The Runs tab sends each row's `deployment` and `study`; the study page sends `study`; `composite_run_views`
    passes this process's own deployment (its `remote-sim-<id>` runs are its own dispatches).
  - viva-core land refuses **409 `no-backend`** when no backend is named, as compose-api always has.
  - **Known gap:** placeholder rows are `remote-pending-<id>`, not namespaced by deployment, so two deployments' runs
    with the same id in one study share a placeholder row. A future fix: `remote-pending-<deployment>-<id>`.
  - Exempt, with the reason in the code: the in-memory batch jobs (`run_jobs.refresh_submitted`,
    `remote_run_jobs`), whose ids this process just dispatched to its one backend.
- **One remote backend per process (Jim, 2026-11-19).** A named backend beside an alias (`VIVA_API_BASE` /
  `SMS_API_BASE`) pointing elsewhere is refused at startup (`sms_api_client.check_single_backend`, for the CLI
  commands in `cli.BACKEND_COMMANDS`). It replaced `sms_api_base()`'s exception for a named compose-api beside an
  SMS alias.
- **Policy, from wb#1630:** a backend that was named but can't be reached never turns into a local run. Local
  happens only when no backend is configured. This becomes the contract of `active()`.

### 2.4 Package layout

```
vivarium_workbench/lib/backends/
    __init__.py        # active(), for_run(), the Protocols
    viva_core.py       # SmsApiClient restricted to /viva/v1; absorbs remote_run_viva_v1 + the /environments half of W2
    compose_api.py     # compose_api_client; absorbs remote_run_compose
    sms_legacy.py      # FROZEN: integer ids, post-hoc analyses, legacy build fallbacks; deleted at viva-api M6
```

- An import-linter contract makes `backends/*` the only importer of `sms_api_client` and `compose_api_client`.
- A ratchet test fails on any `/api/v1` or `/core/v1` path outside `sms_legacy.py`.

## 3. Each SMS call and its replacement

Verified against viva-api **code** on `main` (1c8b27ae; re-checked at a38cfde6), 2026-11-18, not against its docs:

- **Route table.** The app was built from `viva_api.api.main` and its 218 method+path routes listed. Every route
  that `sms_api_client.py` spells exists there, including the regex-built `/viva/v1/workers/{job}/{rest}` (as
  `/{job}/call`). viva-core's `/composites/{id}/results` and `/datasets` are present, so the viva-core adapter can
  drop the interim compose results download. Deployed sites may run older images: confirm against each site's
  `/openapi.json` before relying on a route there.
  - **Correction, same day:** being registered is not being implemented. viva-core's `GET /environments/{id}/manifest`
    and `GET /environments/{id}/source` are stubs that answer **501**
    (`viva_core/api/routers/environment_lifecycle.py`: "need a package hook … which does not exist yet"). Every
    replacement below that relies on them is blocked until they're implemented (ask D in viva-api#1055).
    A grep for `status_code=501` over `viva_api` and `viva_core` finds no other unconditional stub. The rest are
    conditional: `DELETE /composites/{id}` when a run's mechanism can't be stopped, and SLURM-only legacy routes.
    The literal check, redone with both quote styles, covers all 39 route literals in `sms_api_client.py`.
- **composite-resolve never existed.**
  - The app built from `main` has only `latest`, `status`, `versions` and `upload` under `/core/v1/simulator`.
  - No version of the generated OpenAPI spec, on any of the 432 refs (146 spec revisions), lists composite-resolve
    under any spelling.
  - No `.py` file on any ref defines it.
  - The workbench added the client method in 536fa2eb (SP-D1, 2026-06-28), leaving the server endpoint as a
    follow-on that was never built.
- **viva-api's docs drift.** `docs/plan-core.md`'s W1 table still says the workbench calls `/analyses/{id}/status`
  without the `/api/v1` prefix. The client now spells `/api/v1/analyses/{id}/status`, and that route exists.
  Treat those docs as leads to verify, not as evidence.

- `viva_api/simulation/composites/catalog.py` defines `ecoli-simulation`, whose `SimulationParams` are
  `POST /api/v1/simulations`'s parameters under the same names. The simulator is the environment's
  `legacy_simulator_id`.
- `runs.py` (`SmsCompositeRuns`) submits and cancels through the same handlers.

| Workbench call (route) | Callers | Replacement on viva-core | Blocked by |
|---|---|---|---|
| `run_simulation` (`POST /api/v1/simulations`) | `remote_run_views`, `remote_run_jobs`, `composite_test_run_views` | `POST /viva/v1/composites` with `composite: ecoli-simulation`, `environment: {id}`, the same params | nothing for new runs |
| `simulation_status`, `get_simulation`, `simulation_chain_progress`, `cancel_simulation` | `remote_run_views`, `remote_simulations`, `remote_reconcile`, `remote_run_jobs`, `remote_analysis_figures` | `/composites/{id}`, `/status`, `/progress`, `DELETE` | old integer ids: the id lookup (Eran issue) |
| `simulation_trace` | `remote_trace` | `/composites/{id}/trace` (the adapter already has `composite_run_trace`) | as above |
| `download_data` | `remote_run_views`, `simulations_index`, `remote_run_jobs`, `remote_run_landing` | `/composites/{id}/datasets` | as above |
| `list_build_simulations` | `remote_simulations` (Remote tab) | `GET /composites?environment_id=` | nothing |
| `compose_submit`, `compose_status[_batch]`, `download_compose_results` (`/viva/v1/compose`, interim) | `remote_run.run_remote`, `run_jobs`, `remote_trace`, **and `remote_run_viva_v1.land`** (the viva-core adapter itself lands results through the interim route) | the viva-core document route (`create_composite_run`) | Stanford must advertise `viva-v1-composites-documents` (to verify) |
| `simulator_status`, `list_branch_builds`, `list_simulators`, `simulator_commit`, `upload_simulator`, `register_branch_head` (`/core/v1/simulator*`) | `remote_pinned`, `comparison_pinning`, `remote_build_source`, `source_build_views`, `remote_run_views`, `remote_run_jobs`, `remote_simulations` | `/viva/v1/environments` (W2 already goes there first; these are fallbacks) | both Stanford sites advertising `viva-v1-environments[-build]` (to verify) |
| `download_workspace` | `remote_build_source` | `/environments/{id}/source` | ~~nothing~~ the route is a 501 stub: ask D |
| `/api/v1/simulations/discovery` (raw `client._get`) | `composite_test_run_views` | a vEcoli-specific listing of `configs/*.json` and `ecoli/analysis/<category>/*.py`, done over SSH on the HPC checkout or through the GitHub Contents API. Replacement: the environment's read-back manifest, extended by the SMS package (Q-config, settled) | ask D |
| `run_analysis`, `analysis_status`, `list_analyses` | `remote_run_views`, `remote_analysis_figures` | analyses requested **at submit** already travel as `analysis_options` in the params. Re-running analyses on a finished run has no core route | Eran (Q2) |
| **Direct S3 reads, outside any API**: `remote_analysis_figures` reads analysis figures with boto3 (GovCloud credentials) from the `s3://` `result_uri` that `list_analyses` returns | `api/app.py` (3 routes) | `/composites/{id}/datasets` (or an artifact route) so the backend owns storage access | Q2. The workbench must not hold object-store credentials for a backend |
| `composite_resolve` (`/core/v1/simulator/{id}/composite-resolve`) | `composite_resolve` | none; the route has never existed (verified above) | nothing: deleted in step 0 |
| env workers (`/env-worker/v1/*`, already rewritten to `/viva/v1/workers`) | `env_worker_launcher` | already core | — |

### 3.1 What depends on hosted env workers

Hosted workers matter in one situation: a **hosted** workbench, which has no local venv, such as the Stanford pod.
There, `env_worker_launcher.default_launcher()` picks `ProxyWorkerLauncher` (when `ENV_WORKER_PROXY_BASE` is set)
or `RemoteWorkerLauncher` (when `ENV_WORKER_ADVERTISE_HOST` is set); both go through viva-core's `/viva/v1/workers`.
Everywhere else it picks `LocalWorkerLauncher`. All 37 methods `env_worker.py` dispatches go through the one choke
point `WorkerPool.call`; `env_worker_routing.py` classifies some of them (`JOB_CLASS_METHODS` is `run_study`,
`run_study_analyses` and `run_investigation_analysis`). Grouped by what could replace a hosted worker:

| Group | Methods | Alternative without a hosted worker | Direction |
|---|---|---|---|
| **Catalog**: what classes, ports and config schemas exist | `registry_catalog`, `reexport_map`, `attach_process_docs`, `viz_classes`, `viz_class_inputs`, `analysis_viewers`, `data_sources_provider` | read from the environment's **interface manifest** (compose-api `/registry/environments/{name}/manifest`, `/registry/entries`; viva-core `/environments/{id}/manifest`, **a 501 stub today**: ask D). Alex's first step for a hosted workbench | move behind `EnvFacet.manifest()`; the worker becomes the fallback |
| **Composite discovery**: generators, `composites_full` | `discover_composites`, `composites_full`, `list_generators` | documents are workspace files and need no code; **generators** execute workspace code | documents need nothing; generators stay on the worker (or get pre-rendered at build time: open) |
| **Jobs** (`JOB_CLASS_METHODS`, plus one more) | `run_study` (already refused on a deployment target), `run_study_analyses`, `run_investigation_analysis`; and `report_core_snapshot`, which is not job-class but runs the core the same way | the **Run facet**: a batch run on the backend. Analyses at submit go in the run's params; post-run analyses over landed output need an analysis job | move to `runs.submit` once the Run facet has analysis jobs (Q2). A hosted worker should never run science |
| **Probes**: single update, state resolution, readouts | `run_process`, `resolve_composite_state`, `resolve_inner_composite_state`, `config_to_composite`, `composite_contract_audit`, `observables` (`readouts_views`), `study_readout_check` (`observables_views`), `study_precheck` (no caller today) | none yet: they execute live code | stay local. Hosted: viva-core workers only. **The open problem** for a fully remote workbench, deferred until a real user needs it (Alex) |
| **Authoring and source editing** | `authoring_create`, `authoring_validate`, `process_source[_write]`, `composite_source[_write]`, `process_template`, `scaffold_template` | the source belongs to the science workspace, not the environment; reading and writing files needs no worker | audit why each goes through the worker; most should become plain file operations |
| **Viz** | `render_viz_doc`, `viz_preview`, `validate_generated_visualization` | heavy rendering goes into the run; light previews stay interactive (§2A.7: "split as it comes") | stay local; hosted: viva-core workers only |
| Housekeeping | `ping`, `initialize`, `install_modules`, `shutdown` | — | — |

So:

- **On compose-api,** a local workbench loses nothing.
- **A hosted workbench on compose-api** is possible for the catalog, documents, runs and authoring groups once the
  manifest route and plain file access land.
- **Probes (including the readout checks a study page runs), generator discovery and viz previews** are what
  still need live code. They remain viva-core-only
  (`workers` facet) until a remote design exists.

### 3.2 Every outbound call, swept 2026-11-18

Every network primitive (`urlopen`, httpx, boto3, websockets and the compose-api client) was searched in the Python
package, along with absolute URLs in `static/` and `templates/`:

- **viva-api:** only through `sms_api_client.py` (hand-written `urllib`, not generated), except the raw
  `client._get` in `composite_test_run_views` and the **direct S3 reads** in `remote_analysis_figures` (§3 table).
- **compose-api:**
  - through the `compose_api_client` package (`ComposeSession`, in `remote_run_compose` only);
  - a raw `urlopen` of `/registry/entries` in `compose_entry`, which the CLI calls;
  - a `GET /openapi.json` in `SmsApiClient.openapi_operations`, which detects compose-api.
- **Not a backend:** GitHub (device-flow auth, repository URLs), Crossref and Unpaywall (`references_fetch`), the
  Perfetto UI download, AI providers (`ai_auth`, `ai_tools` to its own ASGI app), and workbench-to-workbench
  calls (`investigations_index` peers, `prepare_investigation`, `cli_runs`, `cli` manifest fetch, `smoke`).
- **Browser:** no direct backend calls. All of the frontend's data goes through the workbench's own `/api`.

## 4. Order of work

Each step is one PR and leaves `main` working at Stanford.

**Review, from step 1 on (Jim, 2026-11-19).** Each step gets two independent reviews by a reviewer with fresh
context, given the material rather than the author's conclusions: one of the step's plan before implementation
starts (checked against the code and against the contracts below), and one of the implementation before it is
committed. Rounds repeat until they find nothing new; the PR description says what each review found and what
changed.

**Contracts every step must keep: PTools.** Pathway Tools reads simulation output in two places, and neither may
break:

- **The pbg-ptools viewer** (the Analyses page's PTools card) reads the on-disk file contract
  `<study>/**/ptools/*.tsv`, fetched over HTTP from the workbench (`pbg_ptools.workbench_viewers`). The workbench
  produces it at landing (`remote_run_landing.land_ptools_tsvs`, which copies `**/ptools/*.tsv` out of the run's
  archive into the study). Any new landing path must reproduce that layout, filenames included. That means ask E's
  archive (step 2) and any per-file landing through datasets (step 4). The contract is the **folder**: it includes
  the `cd1_*` tables, which viva-api's registry files as kind `analysis`. `ptools-analysis` is only the Omics
  Viewer series (`ptools_*`), so landing or listing by `kind=ptools-analysis` would drop files.
- **viva-api's PTools page** (SRI's `sms.js`, vendored) calls `/api/v1/simulations`,
  `/api/v1/analyses?experiment_id=` and `/api/v1/analyses/{id}/data`. viva-api pins those by response shape
  (`app/contract.py`, callers `ptools`; a facade may add keys, never remove them). The workbench leaving
  `/api/v1` does **not** mean those routes can go, and anything this plan asks of viva-api must be additive there.

0. **Ratchet and dead code.** Add `tests/test_sms_route_ratchet.py`, which pins today's callers of SMS-route client
   methods and SMS route literals. It fails on a new call, and on a removed call that is still listed, so the list
   shrinks in the PR that removes the call. Delete the `composite_resolve` remote fallback. No behaviour change.
   (Done: wb#1646.)
1. **Extract the interface** (split into 1a, 1b and 1c; see Progress). Create `lib/backends/` with the Protocols, `active()` and `for_run()`.
   - Wrap `remote_run_viva_v1` and `remote_run_compose` as adapters; their logic doesn't change.
   - Move the inline SMS code from `remote_run_views` into `sms_legacy.py`.
   - Views and `run_remote` dispatch through `for_run()` and `active()`.
   - 1a and 1b are pure refactors: existing tests, `test_backend_interop.py` included, pass unchanged, apart
     from the SMS-call ratchet's table. 1c is the deliberate behaviour change: it replaced the views' per-key rules
     (pinned in `tests/test_backends_routing.py`) with routing by each run's stored record (`for_existing`), editing
     that test on purpose. **Was waiting on
     wb#1630**, which edits the same modules (merged 2026-11-19).
2. **Separate the environment from the API.** Introduce `EnvironmentRef`. The session or pinned build produces a
   `BuildRef`, and **new** pinned runs go to viva-core `ecoli-simulation` when the backend advertises
   `viva-v1-composites`; otherwise they stay legacy. This is the first visible change, so it needs a stanford-test
   soak before prod.
   - **Blocked on landing (found 2026-11-18).** `remote_run_viva_v1.land` finds the run's compose simulation
     (`remote_run.compose_simulation_id`, which refuses a `job_id` that is not `compose:<n>`) and downloads the
     interim `/viva/v1/compose/simulation/{n}/results`. An `ecoli-simulation` run has `job_id` `hpcrun:<n>`, so
     that refuses it. viva-core's own `/composites/{id}/results` doesn't help either: it serves only document runs
     (`compose_simulation_id`) and answers 404 for this one (checked live on stanford-test). The native output
     (`parquet`) is not served as a dataset, and the job's `output_uri` is empty. Step 2 needs a landing path of its own: **ask E** on viva-api#1055
     (`/results` for SMS runs), or, as an interim, the run's job owner (`GET /viva/v1/jobs/{job_id}` →
     `simulation:<id>`, checked live on stanford-test) and the legacy
     `/api/v1/simulations/{id}/data`.
   - **What a new run loses until steps 4–6.** Its record carries a run id, not an integer simulation id, so the
     integer-only reads (`run_analysis`/`analysis_status`/`list_analyses`, and `remote_analysis_figures`) do not
     apply. Figures come back with step 4 (`/composites/{id}/datasets`, by the run); re-running an analysis comes
     back with step 6. Until step 6, either keep the integer id beside the run id (from the job owner, above), or
     accept that a new run cannot re-run analyses. Decide before step 2 ships.
3. **Composites tab off `/viva/v1/compose`.** Composites-tab runs use the viva-core document route.
4. **Reads off `/api/v1`.** Remote-tab listing, datasets, `/source` and trace for new runs. The analysis figures and PTools
   files went first (wb#1650, merged 2026-11-19): it lists by `source=simulation:<id>`, so it needs no run id. Drop the `/core/v1`
   build fallbacks once both sites advertise the environments capabilities.
5. **Old runs.** Once viva-api exposes the run id of a legacy simulation, add it to stored records on first read,
   **keeping the integer id beside it**: run reads go by the run id, and `sms_legacy` still needs the integer for
   post-hoc analyses until step 6.
6. **Analyses.** Implement whatever Q2 decides, then delete `sms_legacy.py` (or delete it at M6, whichever comes
   first).

**A parallel track: less dependence on hosted workers (§3.1).** It's independent of steps 2–6, and each item can
be done on its own:

- W-a: jobs off the hosted worker.
  - ~~Today `investigation_steps` calls `run_study` with no run-target gate.~~ Wrong, taken from a stale docstring:
    `_run_study_hook` already refuses the worker on a `deployment` target (workstream 8, step 2a).
  - What remains are the post-run analyses: `run_study_analyses`, called from `study_run_post` (for `study_runs`
    and `declared_results`) and from `composite_flush`; and `run_investigation_analysis`. On a hosted workbench
    they run in the hosted worker over landed output.
  - Moving them needs an analysis job on the Run facet, so W-a depends on step 1 and Q2. It can't start ahead of
    them.
- W-b: the catalog from the manifest, through `EnvFacet.manifest()`, with the worker as the fallback.
- W-c: audit the authoring and source methods, and turn file reads and writes into plain file operations.

## 5. Open questions

- **Q1, settled 2026-11-18 (Jim): option A1.** viva-api adds the correlation id as `run_id` to `SimulationRun` and
  `GET /api/v1/simulations/{id}`, and the workbench rewrites stored integer ids on first read (step 5). A
  `legacy_simulation_id` filter on core's run list was rejected because it would put an SMS id into core, which
  outlives viva-api. The field itself is asked of viva-api in the issue; step 5 waits on it.
- **Q2, settled 2026-11-18 (Jim): yes.** Re-running analyses on a finished run becomes a core job kind attached to
  the run, with the SMS package registering the v2ecoli analysis DAG through core's extension points. This unblocks
  step 6 once viva-api builds it.
- **Q-config, settled 2026-11-18 (Jim): follow compose-api's manifests.** viva-core implements
  `/environments/{id}/manifest` the way compose-api does: the image is read back when it's built or registered
  (`pbg-interfaces/1` classes plus installed packages), and a prebuilt or older image gets its own inspection job.
  Existing environments are **backfilled** by instantiating their containers and introspecting them; compose-api
  does this lazily, on the first read of a ready version that lacks a read-back. The SMS package extends the
  read-back with vEcoli's config files and analysis modules, which replaces `/api/v1/simulations/discovery`.
  `/source` needs the same package hook. This is ask D in viva-api#1055.
- **Q3, settled 2026-11-18 (Jim): viva-core outlives viva-api.** The viva-core adapter is long-term, not frozen,
  and the `analyses` facet and ask C are built on viva-core. Only viva-api's SMS-specific surfaces retire.
- **Q4, settled 2026-11-18 (Jim and Alex):** compose-api will not host env workers, and viva-api's are not ported.
  Hosted workers are an optional `workers` facet that only viva-core provides. Work that depends on them moves to the
  manifest, the Run facet or plain file access (§3.1), and the facet is removed if SMS turns out not to need it. A
  fully remote workbench with live probes is a later, separate design.
  - ~~My earlier recommendation, a workbench-side Kubernetes launcher running the registry image, is withdrawn.~~
  - Jim, 2026-11-18: SMS needs no hosted env workers beyond the workbench. The CLI exposes them for convenience,
    but production doesn't use them. So the `workers` facet, and viva-api's `/viva/v1/workers`, exist only for
    the hosted workbench, and they can be retired once §3.1's remaining uses (probes, generator discovery, viz
    previews) have another home or are judged not worth hosting.
- **Q5, Jim's position 2026-11-18: yes, if it is backwards compatible.** viva-core's `POST /composites` should
  accept data files with a document, as compose-api does (wb#1642). It must be strictly additive: no files means
  today's behaviour, and support is advertised as a capability. It's ask C in viva-api#1055. Until a backend
  advertises it, the viva-core adapter refuses a run that carries files.
- **Verified 2026-11-18, the Stanford sites' `/viva/v1/capabilities`,** read through the EKS API proxy:

  | Site | sms-api image | viva-core | workbench image | Workbench route |
  |---|---|---|---|---|
  | stanford-test (`sms-api-stanford-test`) | 0.9.194 | 0.1.10 | 0.5.5 | `SMS_API_BASE=http://api:8000`, `REMOTE_PINNED=1`, hosted workers proxied through the api, no `--backend-base-url` |
  | prod (`sms-api-stanford`) | 0.9.182 | 0.1.10 | 0.4.1 | the same |

  - **Both sites advertise all four capabilities the plan needs:** `viva-v1-composites`,
    `viva-v1-composites-documents`, `viva-v1-environments` and `viva-v1-environments-build`. They also advertise
    `-results`, `-datasets`, `-trace`, `-jobs` and `-workers`, and `/viva/v1/health` reports every service wired.
  - **Prod's v0.9.182 already defines `ecoli-simulation`** with the same `SimulationParams`.
  - So steps 2–4 aren't blocked by either site. The exceptions are `/source` and `/manifest`, confirmed live on
    stanford-test to answer "not implemented … (plan P5)", which is ask D.