# Backend adapters: SMS as a plugin

Status: **in progress**. The design was settled on 2026-11-18, and the scope was narrowed on 2026-11-19 (see [Scope](#scope)).
Where each step stands is recorded in [Progress](#progress), and nowhere else.

## Progress

Every PR that advances this plan updates this table in the same PR. Steps are defined in §4.

| Step | What | PR | State (date) |
|---|---|---|---|
| 0 | SMS-call ratchet; drop the dead `composite_resolve` fallback | wb#1646 | **done**, merged 2026-11-19 |
| 1a | `lib/backends/`: the three adapters, `Backend`, `for_run`; SMS code moved to `sms_legacy.py`; routing unchanged | wb#1652 | **done**, merged 2026-11-19 |
| 1b | `backends.named/select/active`: one place picks the backend for a new run; the six predicates become wrappers; submit goes through the adapter | wb#1653 | **done**, merged 2026-11-19 |
| 1c-1 | One remote backend per process (startup refusal); every remote run records where it went (`params.remote`); `backends.records.resolve_existing` | wb#1654 | **done**, merged 2026-11-19 |
| 1c-2 | Views, routes, reconciler and figures route existing runs by their record (`resolve_existing`); the Runs tab sends its row's deployment | wb#1655 | **done**, merged 2026-11-19 |
| — | Plan rewritten for the narrower scope (SMS as a plugin) | wb#1657 | **done**, merged 2026-11-19 |
| P1a | Core seams that need no provider (`lib/backend_config.py`, `Backend.identity`); no behaviour change | wb#1658 | **done**, merged 2026-11-19 |
| P3-ids | Opaque run ids end to end, pulled forward from P3 (runs in parallel with P1, Jim 2026-11-19) | wb#1660 | **done**, merged 2026-11-19 |
| P3-launch | The vEcoli launch panel quarantined in `static/sms-launch.js`, pulled forward from P3 (parallel) | wb#1659 | **done**, merged 2026-11-19 |
| — | compose-api server identity (`COMPOSE.identity` = `compose_api.compose_health_identity`, `deployment_id` on `GET /health`); identity caches keyed by (identity family, URL) | compose-api#543 (compose-api PR #545); wb#1665 | **done**, merged 2026-11-19 |
| P1b | Pure move: whole SMS modules into `vivarium_workbench_sms` (same distribution), every import and patch target re-pointed, lazy registry imports; import-linter contract whose ignore list is the ratchet | wb#1662 | **done**, merged 2026-11-19 |
| — | `refresh_submitted`: batch-job items settle against the backend that ran them (viva-core / compose-api items never settled before) | wb#1661 | **done**, merged 2026-11-19 |
| — | Route dependencies (`get_workspace`, ...) moved to `api/deps.py`, so a plugin router can use them without importing `api/app.py` | wb#1663 | **done**, merged 2026-11-19 |
| — | Test isolation: the conftest reset forgets the cached GitHub session and every backend-URL cache | wb#1664 | **done**, merged 2026-11-19 |
| — | Untrack 57 generated fixture artifacts committed by accident in wb#1661 (`3f52a96e`); ignore their four directories | wb#1666 | **done**, merged 2026-11-19 |
| P1c | `Provider` (`backends`, `select`, `recognise`) and `SmsProvider`; the registry (one core→plugin edge) with inert sentinels when providers are off; capability probe split; 409 `backend-not-installed`; no behaviour change with providers on | wb#1667 | **done**, merged 2026-11-19 |
| P2 (target predicates) | `backends.resolve_run_target` replaces `remote_pinned.resolve_run_target`; the three `uses_*_dispatch` wrappers deleted (no production caller); the preflight and `remote_run_submit` decide once (`backends.active`); ratchet 35 → 31; no behaviour change with providers on | wb#1669 | **done**, merged 2026-11-19 |
| P2 (cleanup) | `comparison_pinning` + `smsApiBase` deleted (no reader); `_normalize_repo_url` to core (`git_status.normalize_repo_url`); ratchet −1 | wb#1670 | in review (2026-11-19) |
| P2 | Split the mixed modules area by area (§2.5): SMS parts into the plugin, core callers through `backends` and facets, facet routes stay core, SMS-only routes into the plugin's router | — | not started |
| P3 | Opaque string run ids end to end; the UI shows the nine SMS-today features (§2.5) by declared feature; launch panel quarantined (`sms-launch.js`); core tests run without the plugin | — | not started |
| P4 | `vivarium_workbench_sms` moves into the viva-api repository as its own distribution, found by entry point; the Stanford image installs it | — | not started |
| — | Already done, now counted as plugin work: analysis figures and PTools files read through `/viva/v1/datasets` | wb#1650 | **done**, merged 2026-11-19 |

| Ask (viva-api#1055) | What | Needed by this plan? |
|---|---|---|
| A | `run_id` on `/api/v1/simulations/{id}` | no: only for moving SMS runs onto viva-core (Appendix A) |
| B | one file inside a prefix dataset | no |
| C | data files with a document on `POST /composites` | no: viva-core document runs are plugin territory now |
| D | `/environments/{id}/manifest` and `/source` | no (the hosted catalog, W-b, is out of scope) |
| E | `/composites/{id}/results` for SMS runs | no: only for Appendix A's step 2 |
| F | a stable `deployment_id` on `/viva/v1/health` | nice to have: records fall back to URL + deployment name (§2.3) |
| — | why prod's dataset registry is under 1% of stanford-test's | no: the figures' S3 fallback stays in the plugin |

None of the asks block this plan. They stay open on viva-api#1055 because they're useful on their own.

## Scope

**Decided by Jim on 2026-11-19.** This replaces the 2026-11-18 goal, "the workbench on viva-core and compose-api only".

- **Goal.**
  - The workbench core contains **no SMS-specific code**. That includes code for viva-api's `/api/v1`, `/core/v1` and `/viva/v1` surfaces.
  - That code lives in a **backend provider plugin**: one package that can be shipped separately, gives the workbench what it needs through a defined interface, and may call **both** viva-core (`/viva/v1`) and the legacy SMS API (`/api/v1`, `/core/v1`).
- **compose-api stays in core** as its built-in remote backend (Jim, 2026-11-19).
- **Out of scope** (optional later work, mostly inside the plugin, with no workbench change):
  - moving SMS runs from `/api/v1` onto viva-core: old steps 2–6, the analysis job kind (Q2), asks A and E;
  - keeping viva-core as a service independent of SMS (Jim, 2026-11-19). The 2026-11-18 decision that "viva-core outlives viva-api" (Q3) no longer shapes this plan;
  - a hosted workbench on compose-api: the worker-independence track W-a, W-b and W-c.

  The research behind this work is kept in Appendix A.
- **Why this is smaller.** The plugin may keep calling `/api/v1`, so nothing waits on viva-api. The work is in the workbench: draw the boundary, move code across it, and make the UI ask the provider what it offers.

Neighbours:

- [run-backend.md](run-backend.md) covers *local vs remote*: detached runs, durability and reconciliation. This page covers *which remote*.
- [backend-compose-api.md](backend-compose-api.md) is the wire contract of the core's remote backend.
  [backend-viva-v1.md](backend-viva-v1.md) is the wire contract of the plugin's viva-core adapter.
- `tests/test_backend_interop.py` pins the behaviour each backend must keep.

---

## 1. Where things stand after step 1

Step 1 (wb#1652–#1655) built the seam the plugin needs.

- **One run interface.** `lib/backends/` defines `RunFacet` (`owns`, `submit`, `land`, `status`, `progress`, `cancel`, `trace`) and three adapters:
  - `COMPOSE` (compose-api);
  - `VIVA_CORE` (`/viva/v1`);
  - `SMS_LEGACY` (`/api/v1`). The SMS code from `remote_run_views` was moved into `sms_legacy.py` verbatim.
- **One place picks the backend for a new run.**
  - `backends.select(ws)` checks the session build, then the deployment pin (both mean SMS), then the named backend.
  - `active(ws)` adds wb#1630's rule that a backend which is named but unreachable is refused.
- **Every remote run records where it went:** `params.remote = {kind, id, base_url, deployment, server_id}`.
- **Every operation on an existing run is routed by that record.** `backends.for_existing` is the public seam; `records.resolve_existing` is its implementation.
- **One remote backend per process.** A conflicting configuration is refused at startup.

Step 1 did **not** draw a package boundary. A grep on 2026-11-19 shows SMS knowledge spread across the core:

- 24 modules import `sms_api_client`, and 17 import `remote_pinned`.
- **Core code reaches into SMS modules.**
  - The compose-api path uses the SMS client and its capability probe. `remote_run_compose` reads `sms_api_client.explicit_backend_base_url` and `remote_pinned`. `server_capabilities.backend_profile` detects compose-api through `SmsApiClient.openapi_operations()`.
  - wb#1630's reachability rule (`require_named_backend_reachable`) lives in `server_capabilities`.
  - `backends/records.py` reads `server_id` with `SmsApiClient` from `/viva/v1/health`.
  - `backends/__init__.py` imports the SMS adapters at load time.
- **Generic configuration lives in SMS modules.**
  - `explicit_backend_base_url`, `backend_configured` and `check_single_backend` are in `sms_api_client`.
  - `remote_deployment_name` is in `remote_pinned`, and its default is `"smsvpctest"`.

§2.5 lists what has to change. Untangling these seams, rather than moving files, is most of the work.

## 2. Design

### 2.1 The backend interface

The run facet already exists (§1). The features that only SMS has *today* become optional facets. The UI checks whether a facet is present, never which backend it is talking to:

```python
@dataclass(frozen=True)
class Backend:
    name: str                          # a record's `kind`: "compose-api", "viva-core", "sms-legacy", ...
    runs: RunFacet                     # required (exists)
    identity: Callable[[str], str | None] | None  # the server's id for records (viva: /viva/v1/health; compose-api: /health)
    listing: ListingFacet | None       # runs on the server, merged into /api/simulations (SMS today)
    artifacts: ArtifactFacet | None    # a run's analysis files, figures, PTools files, data downloads (SMS today)
    analyses: AnalysisFacet | None     # re-run analyses on a finished run (SMS today)
    builds: BuildFacet | None          # environments this backend can run: build, list, switch, pin (SMS today)
```

- **Facets are named by capability, and their interfaces carry no SMS terms** (Jim, 2026-11-19).
  - A facet says what it does (`listing`, `artifacts`, `analyses`, `builds`), not how SMS does it.
  - SMS-specific detail lives in the plugin's implementation, for example vEcoli parameters, integer simulation ids and branch builds.
  - This leaves a path for compose-api: its core adapter can implement any facet later (§2.6), and the existing routes and UI then work for compose-api runs with no other workbench change.
- **A facet is defined only when a step needs it.** Its methods come from the code it replaces; method shapes are not designed ahead of the code.
- **Facets keep the views' `(body, status)` shapes,** as the run facet does, so routes and the browser don't change when code moves behind one.
- **SMS-specific dispatch goes behind `builds` and `runs`, not a predicate swap.** Two examples:
  - `study_runs` builds a vEcoli dispatch (`n_generations`, `n_seeds`, `run_parca`, the pinned simulator id).
  - `composite_test_run_views` calls `/api/v1/simulations/discovery`.

### 2.2 Providers (the plugin boundary)

A **provider** is what a plugin registers. Until P4 the core imports the one in-tree provider by name; from P4 it finds providers through the Python entry-point group `vivarium_workbench.backends` (§2.4):

```python
class Provider(Protocol):
    name: str                                                  # "sms"
    # P1c (lib/backends/provider.py):
    def backends(self) -> tuple[Backend, ...]: ...             # VIVA_CORE and SMS_LEGACY, built once
    def select(self, ws_root: Path) -> Backend | None: ...     # its claim on a NEW run (session build, pin)
    def recognise(self, client: object) -> Recognised: ...     # its half of backend_profile: (profile,
                                                               #   has_composites, stop); never raises
    # added by later steps:
    def check_config(self) -> None: ...                        # startup refusal (BackendConfigError) (P3)
    def features(self) -> frozenset[str]: ...                  # what the UI may show (P3)
    def router(self) -> APIRouter | None: ...                  # routes only this provider serves
    def cli(self) -> Iterable[Command]: ...                    # its subcommands (`run-remote`)
    def worker_launcher(self) -> WorkerLauncher | None: ...    # hosted env workers (/viva/v1/workers)
```

- **The core owns:**
  - the registry of providers, and of backends by `kind`;
  - `select`, `active` and `for_existing`;
  - the record store;
  - compose-api and local runs;
  - **generic backend configuration**, in `lib/backend_config.py`:
    - the named URL;
    - the deployment name, with a neutral default; the provider may supply its own;
    - the one-backend rule;
    - the reachability rule;
    - a plain-HTTP probe (`ping`, `openapi_operations`) that compose-api detection and the health view use instead of `SmsApiClient`.
- **The core's `select`** (from P1c) asks the providers first (the session build and the pin are the SMS provider's claims), then the named backend, then local. That is today's order.
- **A record whose `kind` no installed provider serves** is refused with 409 `backend-not-installed`. It is never guessed at by id form.
  - A run with no record (written before 1c-1, or listed live) falls back to `for_run()`'s id forms; when the kind that form names has no installed provider (a bare integer without the SMS plugin), it gets the same 409.
- **Routes keep their paths, so browser code and published bundles keep working.** Where a route lives depends on whether another backend could ever serve it:
  - **Routes for a facet live in the core.** They dispatch through the run's backend (`for_existing`, as step 1c-2 already does) and answer "feature unavailable" when that backend lacks the facet.
    - `artifacts`: `/api/remote-run-land-artifacts`, `/api/remote-analysis-figures`, `/api/remote-analysis-figure`, `/api/study-remote-figures`;
    - `analyses`: `/api/remote-run-analysis`;
    - `listing`: merged into the core's `/api/simulations` (`simulations_index._append_remote_simulations`). The Runs table has no route of its own.
  - **The run lifecycle is already core and shared with compose-api:** `/api/remote-run-submit`, `-poll`, `-land`, `-cancel`, `-chain-progress`, `-trace-support` and `-trace`.
  - **Only SMS-only routes go to the plugin's `router()`**:
    - `/api/remote-run-pinned-build` and `/api/remote-run-build`;
    - the old pipeline `/api/remote-run-start` and `/api/remote-run-status`, which no browser code calls but which are on the read-only allowlist;
    - the `/api/source/builds`, `build-remote` and `switch-build` build routes.

    If `builds` is later made generic, those move to the core with it. `remote-health` stays in the core, on the core ping.
  - **Mixed routes stay in the core, and their SMS branch goes behind a facet:**
    - `/api/remote-dispatch-preflight` already has compose-api and viva-core branches; only its "build on cloud / switch build" branch is SMS (`builds`).
    - `/api/remote-run-config` reports the pin (SMS, `builds`) and the named backend's profile (core).
    - `/api/composite-run/{id}/status` and `/stop` branch on `remote-sim-<int>` ids (`composite_run_views`, `composite_stop_views`; Loom polls them). They should go through `for_existing`.
  - The plugin router is mounted before the app's read-only filter and before the SPA catch-all route. The provider declares which of its routes are read-only-safe.
- **CLI.** `run-remote` is wholly SMS (`--sms-api-url`, `SmsApiClient`). It becomes the provider's `cli()`.
- **Hosted env workers.** `env_worker_launcher.default_launcher()` asks the active provider for a launcher. The core keeps `LocalWorkerLauncher`. The proxy and remote launchers move to the plugin.

### 2.3 Existing runs are routed by their record

This section is unchanged from step 1c. From P1c the backends behind the record kinds come from the provider registry; the kinds themselves (`records.KINDS`) stay a fixed list, the check that a record is well formed. A record whose kind no active provider serves (providers turned off) is refused with 409 `backend-not-installed` (`terminal: true`); it is never routed by its id's form.

- **New runs and existing runs are different questions (Jim, 2026-11-19).**
  - A *new* run goes to the one configured backend (`select` / `active`).
  - An *existing* run is answered by the backend it was dispatched to, which its stored record names. Every remote run's row carries `params.remote = {kind, id, base_url, deployment, server_id}` (step 1c-1).
  - `backends.records.resolve_existing` routes by that record. A run that belongs to another backend is refused (409 `other-backend`) rather than sent to the configured one.
- **A run's provenance is (server identity, run id); a URL is only an address.**
  - The same deployment has several URLs, and different deployments share one. Both Stanford sites are `http://api:8000`, and every laptop tunnel is `http://localhost:8080`.
  - When the record and the configured server both report an identity (`server_id`: ask F for viva-core and sms-legacy, compose-api#543 for compose-api), the identity alone decides.
  - Each backend's identity comes from its own `Backend.identity`, and the identity caches are keyed by (identity family, URL). viva-core and sms-legacy are one viva-api, so they share an entry; a compose-api named at viva-core's URL never shares or crosses its identity.
  - Until both do, the URL and the deployment name must both match. This best-effort guard can't tell apart deployments that share a URL and a name.
  - Rows written before 1c-1, runs listed live from the configured server, and the study page's own dispatches fall back to `for_run()`'s id forms.
- **Every operation on an existing run goes through its record (step 1c-2).**
  - `backends.for_existing(ws, id, deployment=, study=)` is used by:
    - land, status, chain progress and cancel;
    - land-artifacts and re-run analysis;
    - trace, the figure routes and the study gallery;
    - the store download, `composite_run_views` and the reconciler.
  - **Which id.** The id is `run_id`, else `simulation_id`; for land it is `simulation_id`, else a compose-api `run_id`. It is stripped, and a falsy id counts as no id.
  - **Refusals.** A run that belongs to another backend is refused before anything is asked, with a 409:
    `{error, reason ("other-backend" | "ambiguous" | "not-sms"), phase, terminal: true, simulation_id, run, dispatched_to?}`.
    Pollers treat it as terminal. The study page shows the reason and hides "Stop campaign".
  - **What callers send.**
    - The Runs tab sends each row's `deployment` and `study`.
    - The study page sends `study`.
    - `composite_run_views` passes this process's own deployment; its `remote-sim-<id>` runs are its own dispatches.
  - viva-core land refuses with **409 `no-backend`** when no backend is named, as compose-api always has.
  - **Known gap.** Placeholder rows are `remote-pending-<id>`, not namespaced by deployment. If two deployments produce runs with the same id in one study, those runs share a placeholder row. A future fix is `remote-pending-<deployment>-<id>`.
  - **Not by record, by id form** (the code says why): the investigation batch job's `run_jobs.refresh_submitted`. The job lives only as long as this process, whose one backend dispatched every item, and the adapters' id forms do not overlap, so `backends.for_run` names the backend that ran each item: SMS integer ids go in one `compose_status_batch` call, viva-core and compose-api runs through their adapter's `runs.status` (a backend that does not answer is skipped for the rest of that refresh). Caveat: a backend URL re-pointed while the job lives is asked anyway, where `for_existing` would refuse; the SMS path always had that exposure. (Before this, every item was asked of SMS, whose integer-only batch route raised on a viva-core or compose-api id, so such an item never settled.)
  - **Exempt** (the code says why): `remote_run_jobs.run_remote_pipeline`, an SMS-only pipeline that polls the same client it dispatched with, inside the same call.
- **One remote backend per process (Jim, 2026-11-19).**
  - A named backend beside an alias (`VIVA_API_BASE` / `SMS_API_BASE`) that points elsewhere is refused at startup, by `sms_api_client.check_single_backend` for `cli.BACKEND_COMMANDS`.
  - In P1a the rule moved to `lib/backend_config.py`; in P3 (moved from P1c) the SMS aliases become the plugin's `check_config`.
- **Policy, from wb#1630:** a named backend that can't be reached never turns into a local run. A run is local only when no backend is configured.

### 2.4 Package layout and guards

**Move whole modules once, early, under their final package name, and re-point every reference by hand.**

- P1b moves the whole SMS modules into a top-level package, `vivarium_workbench_sms`. It ships inside this distribution until P4.
- The same PR rewrites every import and patch target to the new names, tests included.
- **No aliases.** A `sys.modules` finder would hide those imports from grimp (import-linter) and from mypy's file list, and it would have to be installed before any test imported an old name. So the diff is large but mechanical: about 60 test files reference these modules, and about 12 of them patch by dotted string.
- The `vivarium_dashboard.lib.<moved>` deprecation aliases stop resolving for the moved modules. None of the sibling checkouts uses them: viva-superpowers, sms-ecoli, v2ecoli, pbg-ptools, sms-api, vEcoli and compose-api.

```
vivarium_workbench/lib/backends/
    __init__.py        # core: Backend, facets, Provider, registry (loads providers lazily), select/active/for_existing
    records.py         # core: params.remote, resolve_existing (server identity via Backend.identity)
    compose_api.py     # core: compose-api adapter
vivarium_workbench/lib/backend_config.py   # core: named URL, deployment name, one-backend + reachability rules,
                                           #   generic per-URL breaker + BackendError (P1a); outside `backends/` so the
                                           #   SMS client can import it without running `backends/__init__` (no cycle)
vivarium_workbench_sms/   # the plugin, from P1b (module names kept from lib/, so the move is mechanical)
    sms_api_client.py, remote_link.py, remote_pinned.py, remote_build_source.py, source_build_views.py,
    remote_simulations.py, remote_reconcile.py, remote_analysis_figures.py, remote_run_viva_v1.py,
    remote_run_jobs.py, sms_legacy.py, viva_core.py
    provider.py        # SmsProvider (P1c)
```

**The registry loads lazily, and the move must not create an import cycle.**

- Today `backends/__init__.py` imports `sms_legacy` and `viva_core` at module level, and `sms_legacy` imports `backends.records` at module level. That works only because `sms_legacy` is a submodule, so `backends/__init__` always runs first.
- After the move, loading a plugin module first would hit a half-initialised `backends`. So P1b turns the adapter imports in `backends/__init__.py` into lazy lookups, in the same PR as the move. A module-level `__getattr__` returns the same cached `COMPOSE` / `VIVA_CORE` / `SMS_LEGACY` objects, because 29 references in 15 files (tests included) use them and `by_kind` compares identity.
- A test imports every plugin module first, each in a fresh interpreter.
- The other module-level imports from core into the plugin are in `api/app.py`, `remote_run`, `remote_run_compose`, `remote_run_views`, `server_capabilities` and `study_runs` (`remote_link` too, until P1a splits it). They stay legal under the ignore list and are checked by the same test.

**How the core finds the provider.**

- Until P4 there is no entry point. The registry imports `vivarium_workbench_sms.provider` lazily, by name: one `ignore_imports` edge, which lasts until P4.
- P3's core-only CI job turns it off with a flag.
- P4 replaces the import with the entry-point group `vivarium_workbench.backends`, which is `pyproject.toml`'s first `entry-points` table. From then on, startup refuses when an SMS alias (`SMS_API_BASE` / `VIVA_API_BASE` / `VIVARIUM_WORKBENCH_REMOTE_PINNED`) is configured but no provider is installed. Those names stay listed in the core for that refusal.
- So a stale editable install can't hide the provider before P4.

**Guards (from P1b).**

- **An import-linter `forbidden` contract: `vivarium_workbench` must not import `vivarium_workbench_sms`.**
  - Neither package contains the other, so every core module is checked, including `publish.py` and `env_worker.py`.
  - Its `ignore_imports` lists the edges that are still real:
    - from the half-SMS core modules, until P2 splits them;
    - the registry's one provider import, until P4.
  - That list **is the ratchet**. Each later PR removes entries, and none may add one. An unmatched entry fails the check, so stale entries can't linger.
  - **As landed in P1b:** 38 edges, generated with grimp after the move (`pyproject.toml`, the contract named "the core does not import the SMS plugin"). The route ratchet's `ALLOWED` list holds the core's 13 remaining (module, method) pairs.
- **The route-literal ratchet** (`tests/test_sms_route_ratchet.py`).
  - Its `ALLOWED` list becomes "SMS client methods and `/api/v1` / `/core/v1` literals outside the plugin". After P1b that is exactly the mixed core modules.
  - The list shrinks with P2 and is empty when P2 ends.
  - Its docstring and its "go through viva-core" message change with it.
- **Every check that names packages or scans `vivarium_workbench/` must add the plugin.** Otherwise it passes while checking nothing there.
  - Tool configuration:
    - ruff in `.github/workflows/types.yml`;
    - mypy's file list;
    - import-linter's `root_packages`;
    - hatch's `packages`.
  - Tests:
    - `test_ai_automation.py` (git-push sites);
    - `test_write_text_utf8_encoding.py`;
    - `test_plugin_import_allowlist.py`;
    - `test_no_ai_deps.py`;
    - the conftest's `sys.modules.get(...)` resets (`sms_api_client`, and `remote_link._links`), which move to the new names.
- **The plugin's router is included before the read-only filter and before the SPA catch-all** (`/{rel:path}`). Otherwise the catch-all route answers its GET requests first.

### 2.5 What has to change

This list comes from greps and a grimp import graph on 2026-11-19, checked by two independent reviews. It is a starting list; each step's plan verifies its own part.

**Core seams (P1a; rows marked P1c need the provider; P1c's are done, and four moved to P3).** These are the places where core code uses SMS code today, and each is fixed in the core:

| Seam | Today | Change |
|---|---|---|
| compose-api path (P1a, done) | `remote_run_compose` reads `remote_pinned.remote_deployment_name` and `sms_api_client.explicit_backend_base_url` | reads `lib/backend_config.py`; no longer imports `remote_pinned` |
| capability probe (P1c, done) | `server_capabilities.explicit_backend_profile` → `backend_profile(SmsApiClient)`: `client.capabilities()` (`/viva/v1`, then `/core/v1`), then `openapi_operations()` for compose-api, all through the `remote_link` circuit breaker; `require_named_backend_reachable` and `_PROFILE_FAILURE_TTL` rely on that breaker | `backend_profile(client)` stays the one entry point; the provider's `recognise(client)` does the viva-core half (moved verbatim to `vivarium_workbench_sms/provider.py`); the core asks compose-api's OpenAPI exactly when the capability list lacks `viva-v1-composites`, as before. With providers off the profile starts reachable and legacy and only compose-api is detected |
| breaker (P1a, done) | `remote_link` imports `SmsApiError` and `sms_api_base` at module level; `CircuitOpen` subclasses `SmsApiError`; `snapshot()` reads the AWS SSO cache | split: the generic per-URL breaker and a core `BackendError` are in `backend_config.py`. **`SmsApiError` is an alias of `BackendError`, not a subclass** (a deliberate deviation): every `except SmsApiError` stays exact and still catches `CircuitOpen(BackendError)`. `remote_link` keeps the SMS default URL, its own `link()` / `start_probe()`, and the SSO expiry as the function `remote_link.snapshot(lk)` (the core breaker has no snapshot); they go to the plugin in P1b |
| health (P1a, done) | `workspace_deps_views.remote_health` pings with `SmsApiClient.ping()` | `backend_config.ping`, the same request (`GET /version`, 5 s default timeout, `Accept` and caller-identity headers). `SmsApiClient.ping` keeps its own copy until P1b, because tests steer it through `sms_api_client.urlopen`; a test pins that the two agree |
| records (P1a, done) | `records.py` uses `SmsApiClient` (`/viva/v1/health`), `_canonical_url`, `remote_pinned._DEFAULT_REMOTE_DEPLOYMENT`; `configured_url` / `_deployment_names_now` use `sms_api_base` and `BACKEND_BASE_ENV_VARS` | `Backend.identity` (viva-core and sms-legacy: `viva_core.viva_health_identity`; compose-api: `compose_api.compose_health_identity`, `deployment_id` on `GET /health`, compose-api#543). `records` asks the identity function of the record's own kind, and keys its caches by (identity family, URL), so a compose-api record never picks up viva-core's identity at a shared URL. URL canonicaliser, deployment name and configured URL come from `backend_config.py` |
| registry (P1c, done) | `backends/__init__.py` imports `SmsLegacyRuns`, `VivaCoreRuns` at load | `lib/backends/provider.providers()` imports `vivarium_workbench_sms.provider` (the one registry edge; P4: an entry point); `VIVA_CORE` / `SMS_LEGACY` / `_ALL` resolve through it, the same objects every time. `VIVARIUM_WORKBENCH_BACKEND_PROVIDERS=none` turns providers off: the attributes become inert sentinels (they own no run; `is` comparisons stay false) that no lookup returns. "Providers off" is not "plugin absent": the core cannot be imported without the package until P2 finishes. With providers off, a reachable named viva-api reads as a legacy backend and runs locally, by the existing "reachable legacy stays local" rule (P3 may add a startup warning) |
| deployment name (P1a, done) | `remote_pinned.remote_deployment_name()` (default `"smsvpctest"`) | `backend_config.deployment_name()`; `remote_pinned`'s function is a call-time wrapper. The default stays `"smsvpctest"` until P3 (moved from P1c), then neutral, with the provider supplying its own |
| session build and pin (P1c, done: `SmsProvider.select`) | `run_core.run_target_for` (`.viv-build.json`) and `remote_pinned.is_pinned_enabled()`, used by `backends.select`; `.viv-build.json` is also read by `report.py`, `provenance_manifest.py`, `composite_resolve`, `env_worker_launcher`, `readouts_views` and `run_core` | `SmsProvider.select`; the other readers ask the provider (P2 plans each) |
| generic helpers in SMS modules | `remote_simulations._read_build_meta` (used by `composite_resolve`, `composite_state_views`), ~~`source_build_views._normalize_repo_url` (used by `git_status`)~~ moved to the core as `git_status.normalize_repo_url` (P2 cleanup) | move to a core module, or behind `builds` if SMS-only |

`remote_run_landing` is mixed. Its archive landing (`land_composite_results`, `fold_analyses`, `land_ptools_tsvs`, §3) is core, and compose-api and the plugin both call it. `land_remote_run` (which hardcodes the `"sms-legacy"` record and imports `remote_pinned` and `sms_api_base`) and `land_remote_simulation_artifacts` (`download_data`) are SMS: they go in P2's Runs area.

**Corrections from the P2 inventory (2026-11-19), applied in P1b:**
- `remote_trace` is mixed, not SMS-only: compose-api's trace goes through it. It stays in the core until P2.
- `comparison_pinning` has no production caller, and `publish.py`'s `smsApiBase` is read by nothing. Both were deleted in the P2 cleanup PR rather than kept (P1b moved `comparison_pinning` as-is).
- Target predicates also include `investigation_run_one_views`.
- Routes that reach SMS code and are not yet classified: `simulation-run-download`, `composite-run/{id}/artifact`, `composite-run/{id}` and `/state`, `investigation-run-unblocked-status`, `investigation-run-redrive`. P2's Routes area classifies them.
- `ai_tools.py` hard-codes SMS route paths, `_run_auth_ok` reads the SMS pin, and `app.py` clears the listing module's private caches. These belong to P2's areas.

**The plugin set.** Whole modules move in P1b. The SMS parts of mixed modules are split out in P2, one area per PR. Until then their calls into the plugin are the contract's `ignore_imports`. The hosted env-worker pod ships the core without the plugin, so worker-reachable core modules must import without it; `tests/test_core_imports_without_plugin.py` pins the shrinking list.

| Area | Whole modules (P1b) | SMS parts of mixed modules (P2) | Core sees it through |
|---|---|---|---|
| Client | `sms_api_client` (less the config split out) | viva-core half of `server_capabilities` | — |
| Runs | `backends/sms_legacy`, `backends/viva_core`, `remote_run_viva_v1`, `remote_trace`, `remote_run_jobs` | `remote_run.run_remote` (called by `run_runner`), SMS parts of `remote_run_views`, `remote_run_landing.land_remote_run` and `land_remote_simulation_artifacts`, `run_jobs.refresh_submitted`'s SMS half (`compose_status_batch`, interim `/viva/v1/compose`) | `runs` |
| Builds and pinning | `remote_pinned`, `remote_build_source`, `source_build_views` | build-source half of `workspace_deps_views`, the vEcoli dispatch in `study_runs`, discovery in `composite_test_run_views` | `builds`, `Provider.select` |
| Remote tab and store | `remote_simulations`, `remote_reconcile` | `simulations_index._append_remote_simulations`, `resolve_or_fetch_store`; `investigations_index`'s `list_remote_simulations_swr` | `listing`, `artifacts` |
| Analyses and figures | `remote_analysis_figures` | — | `artifacts`, `analyses`; the routes stay core and dispatch by facet (§2.2) |
| Routes | — | `api/app.py`: imports of 4 plugin modules (`source_build_views`, `remote_run_jobs`, `remote_analysis_figures`, `remote_simulations`), inline `SmsApiClient(...)` at the figure routes, `_sms_only`; its read-only allowlist names SMS routes | facet routes stay core and dispatch by facet; SMS-only routes go to the plugin's `router()` (§2.2) |
| Workers | — | `ProxyWorkerLauncher`, `RemoteWorkerLauncher` in `env_worker_launcher`; the viva-api proxy assumptions in `env_worker_pool` (its hints), `env_worker_dialback` and `env_worker_client` | `Provider.worker_launcher` |
| CLI and startup | — | `run-remote` in `cli.py`; startup checks in `cli`, `startup`, `api/__main__` | `Provider.cli`, `check_config` |
| Models and bundle | — | sms-api status + remote-provenance models in `models.py` (feed `generate_ts`); `publish.py`'s `smsApiBase` | P3 decides |
| Target predicates (P2, this area's PR) | — | none. `remote_pinned`'s four predicates were core wrappers: `resolve_run_target` moved to `backends`, the three `uses_*_dispatch` were deleted (no production caller). Callers: `investigation_steps`, `investigation_run_views`, `investigation_run_one_views`, `study_runs`, `composite_test_run_views`, `remote_run_views`, `api/app.py` | `backends.resolve_run_target` / `backends.active`; the SMS half is `Provider.select` (P1c) |
| P2 (cleanup) | `comparison_pinning` + `smsApiBase` deleted (no reader); `_normalize_repo_url` to core (`git_status.normalize_repo_url`); ratchet −1 | wb#1670 | in review (2026-11-19) |
| To check in P2's plans | — | the vEcoli/ParCa case keyed on `.viv-build.json` in `readouts_views`; the `SMS_API_BASE` hint in `run_runner`; vEcoli knobs (`n_seeds`, `n_generations`) in `preflight.py`, `env_worker.py` | — |

**Features that are SMS-only today (P3).**

The UI shows these only when the run's backend has the matching feature. Checked against the routes and browser code on 2026-11-19.

| # | Feature | Where in the UI | Routes | Gated today by | Facet / feature | Could compose-api offer it? |
|---|---|---|---|---|---|---|
| 1 | Cloud builds: build a branch on the deployment, list or switch builds | Source / branch picker: "Build on cloud", the Cloud toggle (`branch-source.js`, `workspace-picker.js`, `session.js`) | `/api/source/builds`, `build-remote`, `switch-build` | `isRemote` | `builds` | yes: its registry environments and versions are the analogue |
| 2 | Pinned deployment runs, and the relabelled run card | run card, configure-run dialog (`study-detail.js`, `walkthrough.js`, `configure-run.js`) | `/api/remote-run-config`; `remote-run-pinned-build` and `remote-run-build` (HTTP clients only, no browser caller) | `pinned: true` | `builds` (pin) | maybe: pin a registry version |
| 3 | Remote preflight with "Build on cloud" / "Switch build" actions | composite card's Run button (`composite-card.js`) | `/api/remote-dispatch-preflight` | remote run target | `builds` | with 1 |
| 4 | vEcoli launch parameters (generations, seeds, ParCa) and the campaign view | study page's remote-run panel (`study-detail.js`, `templates/study-detail.html`) | `/api/remote-run-submit` with the SMS body | deployment run target | not a facet: quarantined in `sms-launch.js`, feature `sms-launch` (Q-launch-ui) | no: model science. Better driven by the composite's own parameter schema |
| 5 | Runs on the server listed next to local runs | Runs table (`sim-table.js`, `remote-sim-<id>` rows) | merged into `/api/simulations` | rows present | `listing` | yes: it lists runs |
| 6 | Land a run's analysis files (`analyses.json`, PTools exports) | row action on a completed remote run (`sim-table.js`) | `/api/remote-run-land-artifacts` | any remote row with an id (also compose-api's); the server refuses non-SMS runs with 409 `not-sms` | `artifacts` | yes: run results and datasets |
| 7 | Re-run analyses on a finished run | row action (`sim-table.js`) | `/api/remote-run-analysis` | as 6 | `analyses` | needs an analysis job kind there |
| 8 | Analysis figures, per run and as a study gallery (also feeds the Viz / Analyses tabs) | figures window (`sim-table.js`), study tabs (`study-detail.js`) | `/api/remote-analysis-figures`, `remote-analysis-figure`, `study-remote-figures` | per-run: as 6; the study gallery builds an SMS client inline and scans the study's SMS runs, with no refusal | `artifacts` | yes, with 6 |
| 9 | Explore a remote run's state | Loom embed (`loom-embed.js`: `run_target=deployment&build_sim=`) | `/api/composite-run/{id}/status` and `/stop` (SMS branch on `remote-sim-<int>`) | `remote-sim` rows | later, if ever | later |

**SMS assumptions outside any facet (P3).** A facet carries data, not UI and not id formats, so these need their own work:

- **Run ids are assumed to be SMS integers.**
  - The figure routes declare `simulation_id: int`.
  - `sim-table.js` sends `Number(simId)` for land-artifacts and re-run analysis.
  - `study-detail.js` and `perfetto-open.js` choose `simulation_id` or `run_id` by an all-digits test.
  - The composite-run status and stop routes match `remote-sim-<int>`.

  P3 makes run ids opaque strings end to end; the record's `kind` decides, never the id's form. This has to land before any facet can serve compose-api runs.
- **The vEcoli launch panel** (feature 4) is SMS UI in core files. Decided: quarantine it in `static/sms-launch.js` behind a feature, as accepted debt (Q-launch-ui).
- **Gating is a server refusal, not a hidden button.** The UI shows features 6–8 on any remote row with an id; P3 hides them by declared feature.
- **SMS wording and plumbing:**
  - "sms-api" strings in `branch-source.js`, `study-detail.js`, `workspace-picker.js`, `loom-embed.js`, `session.js`, `progress-track.js`, `sim-table.js` and `index.html.j2`;
  - the sms-api models in `models.py`, which feed the generated TypeScript types.
- **To check in P3's plan:** the "remote workspace" Cloud toggle in `workspace-picker.js` (`ws.kind === "remote"`) may be a feature separate from cloud builds.

**Not SMS-only.** The run lifecycle on any remote run is core and shared with compose-api: poll, land, cancel, chain progress and trace (`sim-table.js`, `walkthrough.js`, `study-detail.js`, `perfetto-open.js`, `progress-track.js`). The backend health dot (`/api/source/remote-health`) stays in the core. Some things are SMS wording rather than SMS features: the "Cloud / Build via sms-api" help text in `index.html.j2`.

**What P3 changes.** Each feature is already conditional, but on a different signal each time. P3 gives them one source: the run's backend, or for new runs the active backend, declares its features, and the browser shows or hides features 1–9 by that declaration. With the plugin installed, the UI looks exactly as it does today. Without it, none of 1–9 appear.

### 2.6 Adding a feature to compose-api later

Because the facets and their routes are generic (§2.1, §2.2), giving compose-api one of the features above takes these steps, once P3 has made run ids opaque strings (§2.5):

1. Implement the facet in the core compose-api adapter (`backends/compose_api.py`).
2. Declare the feature.
3. If compose-api lacks a server route that the facet needs, ask compose-api for it.

After P3, adopting a facet needs no route, browser or plugin change. Two features need more first:
- the study figure gallery, which needs a generic `listing` to find a study's runs;
- feature 4 (vEcoli parameters), which is meant to stay SMS-specific.

## 3. Contracts every step must keep

**Stanford keeps working.** Each step is one PR and leaves `main` working at both Stanford sites. Until P4 the plugin ships inside this distribution, so the Stanford image needs no change. At P4 the image must also install the plugin.

**PTools.** Pathway Tools reads simulation output in two places, and neither may break.

- **The pbg-ptools viewer** (the PTools card on the Analyses page) reads the on-disk contract `<study>/**/ptools/*.tsv`, which it fetches over HTTP from the workbench (`pbg_ptools.workbench_viewers`).
  - The workbench produces these files at landing: `remote_run_landing.land_ptools_tsvs` copies `**/ptools/*.tsv` out of the run's archive into the study.
  - Whatever ends up in the plugin must still produce that layout, filenames included.
  - The contract is the **folder**, not the kind. The folder includes the `cd1_*` tables, which viva-api's registry files as kind `analysis`.
- **viva-api's PTools page** (SRI's `sms.js`, vendored into viva-api) calls `/api/v1/simulations`, `/api/v1/analyses?experiment_id=` and `/api/v1/analyses/{id}/data`.
  - Those are viva-api's routes, pinned by viva-api's `app/contract.py`. Nothing in this plan touches them.

**Review (Jim, 2026-11-19).** Each step gets two independent reviews by a reviewer with fresh context, who is given the material rather than the author's conclusions:

- one of the step's plan before implementation starts, checked against the code and these contracts;
- one of the implementation before it is committed.

Rounds repeat until they find nothing new. The PR description says what each review found and what changed.

## 4. Order of work

0. **Ratchet and dead code** (done: wb#1646).
1. **Extract the interface** (wb#1652, #1653, #1654, #1655). See §1.
2. **P1a: core seams that need no provider.**
   - Add `lib/backend_config.py` (outside `backends/`, so no import cycle) and `Backend.identity`; split `remote_link`.
   - Add the fresh-interpreter import test (each module imported first), and keep it from here on.
   - Fix the rows in §2.5's core-seams table that aren't marked P1c.
   - **No behaviour change.** Tests pass unchanged, apart from patch targets that move with the seams; those are marked as deliberate.
3. **P1b: the move, and nothing else.**
   - Move the whole modules in §2.5 into `vivarium_workbench_sms`, and re-point every import and patch target.
   - Make the adapter imports in `backends/__init__` lazy, and add the fresh-interpreter import test.
   - Add the import-linter contract, the tool and test coverage, and the ratchet's new rule (§2.4).
   - Review checks one thing: that this is a pure move.
4. **P1c: the provider.**
   - Add `Provider` (`backends`, `select`, `recognise`), `SmsProvider` and the registry's lazy provider import.
   - Fix the P1c seam rows: the registry, the session build and pin, and the capability probe with `recognise`.
   - Route `select`, `for_existing` and the record kinds' backends through the registry; refuse a record of an inactive kind (409 `backend-not-installed`).
   - **No behaviour change** with providers on.
   - **Moved to P3 (2026-11-19, plan reviews):** the configured base URL, the default deployment name, the SMS alias env vars and the alias half of the one-backend check (`check_single_backend`). They stay plain data in `backend_config`, because its import-linter contract forbids it from asking `lib.backends` or the plugin, and `sms_api_client` imports it at module level (a cycle). Also moved: the bare-id `backend-not-installed` refusal (a run with no record whose id no active backend owns), which needs `Resolution(kind=None)` to stop meaning both "blank id" and "no owner".
5. **P2: split the mixed modules, one area per PR,** in the order of §2.5's plugin-set table. Each PR:
   - moves the area's SMS parts into the plugin;
   - re-points core callers at `backends` and the facet;
   - keeps the area's facet routes in the core, dispatching by facet, and moves only its SMS-only routes into the plugin's router, keeping their paths (§2.2);
   - removes the matching `ignore_imports` entries and ratchet entries.

   When P2 ends, only the registry's provider import is left on the ignore list.
   **Pulled forward, in parallel with P1 (Jim, 2026-11-19).** Two P3 items don't depend on the P1 chain and run as their own PRs now: opaque run ids end to end, and the launch-panel quarantine (`static/sms-launch.js`, gated by today's signals until P1c's declared features exist). A short parallel PR gives compose-api a server identity if compose-api reports one; it does (compose-api#543, `deployment_id` on `GET /health`), see the Progress row.
6. **P3: features, and a core without the plugin.**
   - From P1c: the configured base URL, default deployment name, SMS alias env vars and the alias half of the one-backend check become neutral in the core (the provider supplies SMS's), and the bare-id `backend-not-installed` refusal.
   - Run ids become opaque strings end to end (routes and browser); the record's `kind` decides, never the id's form (§2.5).
   - The browser reads the active provider's `features` and hides SMS-only UI when they're absent.
   - Quarantine the launch panel in `static/sms-launch.js` behind the `sms-launch` feature (Q-launch-ui, decided); move the SMS branches of mixed routes behind their facets (§2.2).
   - The models and the bundle key are settled.
   - A CI job turns the provider off and runs the core suite.
   - The plugin's routes then leave the OpenAPI schema. The tests over the whole route table (`tests/test_traversal_sweep.py` and the generated TypeScript types) run with the plugin, and the core-only job skips them.
7. **P4: ship it.**
   - `vivarium_workbench_sms` becomes its own distribution, moved into the viva-api repository (Q-P4).
   - The entry point replaces the registry's import, which leaves the ignore list empty.
   - The Stanford image installs the plugin.
   - No module paths change.

## 5. Open questions

- **Q-P4: where the plugin lives after P4. Jim's preference (2026-11-19): in the viva-api repository** (vivarium-collective/viva-api), next to the API it calls, rather than in a repo of its own.
  - **It ships as its own distribution there** (`vivarium-workbench-sms`, for example under `packages/`). It is not part of viva-api's server package. The plugin depends on `vivarium-workbench`; viva-api's server must not. An import-linter contract in viva-api ("the server never imports the plugin") would match the ones it already has.
  - **Timing.** The plugin stays in this repo until P3 is done and the provider interface has stopped changing. Until then, an interface change is one PR here. After P4, it is a workbench release plus a viva-api PR.
  - **Versioning.** The plugin pins a compatible range of `vivarium-workbench`, and the workbench states which provider-interface version it offers. A contract test in viva-api runs the plugin against the workbench version it pins.
  - **Needs agreement from viva-api's maintainers:** who owns the package, who reviews it, its CI, and how the Stanford workbench image gets it. Ask before P4; it doesn't block P1–P3.
- **Q-name: the plugin's package name.** The working name is `vivarium_workbench_sms` (distribution `vivarium-workbench-sms`). Jim's phrase was "sms-client provider".
- **Q-launch-ui, decided (Jim, 2026-11-19): (c) quarantine the panel in the core for now, and revisit at P4.**
  - **What the panel is.** It's in `study-detail.js`, around lines 2496–2640.
    - A dispatch-mechanism selector (`multi_node_dispatch`, `mbp_dispatch`, `nextflow_dispatch`), with different fields for each mechanism.
    - `n_seeds` and `n_generations` come from the study's baseline and block the dispatch when unset, because they size an AWS Batch job.
    - A confirmation step that names the AWS Batch job, and the campaign view (generations × seeds, "Stop campaign").
  - **(c), in P3.** Move the panel code unchanged into `static/sms-launch.js`, which the core loads only when the backend declares the `sms-launch` feature.
    - The UX doesn't change, and there's nothing new to deploy.
    - Without the plugin, the panel is hidden.
    - A browser-side check allows SMS terms in that one file only.
  - **The debt this accepts.**
    - The core repo still holds vEcoli UI text.
    - Every change to SMS launching is a workbench PR, and the dispatch mechanisms change often.
  - **Rejected for now.**
    - (a) a schema-driven form: the right long-term shape, but awkward for per-mechanism fields, block-don't-default rules, the confirmation and the campaign view without building a small UI language.
    - (b) a plugin-shipped UI file: the core would have to serve files from the plugin package, and the plugin's JS would be coupled to the core's page and its version.
  - **When to revisit: at P4, or earlier if compose-api composites need launch parameters.** Once the plugin moves to viva-api, every launch change becomes a two-repo change. At that point:
    - move `sms-launch.js` into the plugin (b), which by then is mostly a file move plus serving it;
    - or switch to (a), if a generic parameter form is wanted by then.
- **Q-features: how features reach the browser.** Either extend an existing core view or add a route. Decide in P3's plan.

---

## Appendix A. Out of scope: moving SMS onto viva-core

This is the 2026-11-18 plan and research for moving the SMS flow off `/api/v1` and onto viva-core. Since 2026-11-19 it is **not** part of this plan; see [Scope](#scope). It is kept because it is still accurate as research, and because it is the plugin's own roadmap if that move is ever made. Inside the plugin it would need no workbench change. The section numbers below are the old ones. A few names come from the old design and are no longer defined here: `EnvFacet` (the old environment facet), M6 (viva-api's milestone that retires `/api/v1`), and §2A.7 (in `docs/REFACTOR-PLAN.md`).

### A.1 Two questions answered by one switch (as of 2026-11-18)

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

The code answers both with the same predicates: `backends.resolve_run_target`, `backends.select` (formerly
`remote_pinned.uses_viva_v1_dispatch` / `uses_compose_dispatch`) and `server_capabilities.explicit_backend_profile`. In practice:

- A pinned or session build **always** takes the SMS API, even on a backend that serves `/viva/v1/composites`.
- The document routes are used **only** when the operator names a backend with `--backend-base-url`.

So "the Stanford flow" and "the SMS API" look like one thing, but they aren't. viva-core already accepts the
Stanford flow (§A.3).


### A.2 The environment is a value, not a route

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

### A.3 Each SMS call and its replacement

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
| `simulator_status`, `list_branch_builds`, `list_simulators`, `simulator_commit`, `upload_simulator`, `register_branch_head` (`/core/v1/simulator*`) | `remote_pinned`, `remote_build_source`, `source_build_views`, `remote_run_views`, `remote_run_jobs`, `remote_simulations` | `/viva/v1/environments` (W2 already goes there first; these are fallbacks) | both Stanford sites advertising `viva-v1-environments[-build]` (to verify) |
| `download_workspace` | `remote_build_source` | `/environments/{id}/source` | ~~nothing~~ the route is a 501 stub: ask D |
| `/api/v1/simulations/discovery` (raw `client._get`) | `composite_test_run_views` | a vEcoli-specific listing of `configs/*.json` and `ecoli/analysis/<category>/*.py`, done over SSH on the HPC checkout or through the GitHub Contents API. Replacement: the environment's read-back manifest, extended by the SMS package (Q-config, settled) | ask D |
| `run_analysis`, `analysis_status`, `list_analyses` | `remote_run_views`, `remote_analysis_figures` | analyses requested **at submit** already travel as `analysis_options` in the params. Re-running analyses on a finished run has no core route | Eran (Q2) |
| **Direct S3 reads, outside any API**: `remote_analysis_figures` reads analysis figures with boto3 (GovCloud credentials) from the `s3://` `result_uri` that `list_analyses` returns | `api/app.py` (3 routes) | `/composites/{id}/datasets` (or an artifact route) so the backend owns storage access | Q2. The workbench must not hold object-store credentials for a backend |
| `composite_resolve` (`/core/v1/simulator/{id}/composite-resolve`) | `composite_resolve` | none; the route has never existed (verified above) | nothing: deleted in step 0 |
| env workers (`/env-worker/v1/*`, already rewritten to `/viva/v1/workers`) | `env_worker_launcher` | already core | — |

#### A.3.1 What depends on hosted env workers

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

#### A.3.2 Every outbound call, swept 2026-11-18

Every network primitive (`urlopen`, httpx, boto3, websockets and the compose-api client) was searched in the Python
package, along with absolute URLs in `static/` and `templates/`:

- **viva-api:** only through `sms_api_client.py` (hand-written `urllib`, not generated), except the raw
  `client._get` in `composite_test_run_views` and the **direct S3 reads** in `remote_analysis_figures` (§A.3 table).
- **compose-api:**
  - through the `compose_api_client` package (`ComposeSession`, in `remote_run_compose` only);
  - a raw `urlopen` of `/registry/entries` in `compose_entry`, which the CLI calls;
  - a `GET /openapi.json` in `SmsApiClient.openapi_operations`, which detects compose-api.
- **Not a backend:** GitHub (device-flow auth, repository URLs), Crossref and Unpaywall (`references_fetch`), the
  Perfetto UI download, AI providers (`ai_auth`, `ai_tools` to its own ASGI app), and workbench-to-workbench
  calls (`investigations_index` peers, `prepare_investigation`, `cli_runs`, `cli` manifest fetch, `smoke`).
- **Browser:** no direct backend calls. All of the frontend's data goes through the workbench's own `/api`.

### A.4 The old order of work, steps 2–6 and the worker track

Step numbers are the old ones. Every reference to "steps 2–6" below means these.

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

**A parallel track: less dependence on hosted workers (§A.3.1).** It's independent of steps 2–6, and each item can
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

### A.5 Open questions

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
- **Q3, settled 2026-11-18 (Jim), and no longer binding on this plan (2026-11-19: keeping viva-core independent of SMS is out of scope): viva-core outlives viva-api.** The viva-core adapter is long-term, not frozen,
  and the `analyses` facet and ask C are built on viva-core. Only viva-api's SMS-specific surfaces retire.
- **Q4, settled 2026-11-18 (Jim and Alex):** compose-api will not host env workers, and viva-api's are not ported.
  Hosted workers are an optional `workers` facet that only viva-core provides. Work that depends on them moves to the
  manifest, the Run facet or plain file access (§A.3.1), and the facet is removed if SMS turns out not to need it. A
  fully remote workbench with live probes is a later, separate design.
  - ~~My earlier recommendation, a workbench-side Kubernetes launcher running the registry image, is withdrawn.~~
  - Jim, 2026-11-18: SMS needs no hosted env workers beyond the workbench. The CLI exposes them for convenience,
    but production doesn't use them. So the `workers` facet, and viva-api's `/viva/v1/workers`, exist only for
    the hosted workbench, and they can be retired once §A.3.1's remaining uses (probes, generator discovery, viz
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
