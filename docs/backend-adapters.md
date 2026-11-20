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
| P2 (cleanup) | `comparison_pinning` + `smsApiBase` deleted (no reader); `_normalize_repo_url` to core (`git_status.normalize_repo_url`); ratchet −1 | wb#1670 | **done**, merged 2026-11-19 |
| P2 Workers | Hosted env-worker launchers (proxy, dial-back) and the `.viv-build.json` read move to `vivarium_workbench_sms/env_workers.py`; `Provider.worker_launcher`; the core refuses hosted workers no provider supplies (startup exit 2, `BackendConfigError` at launch, never Local); generator discovery inside an env worker runs in-process (no nested worker); ratchet 35→34 | wb#1668 | **done**, merged 2026-11-19 |
| P2 Client + CLI | The viva-api capability contract (`fetch_capabilities`, `require_capabilities`, `ServerCapabilities`, `CapabilityUnsupportedError`, the slugs) moves verbatim into `vivarium_workbench_sms/sms_api_client.py`; `Provider.probe_client` (SMS: `SmsApiClient.for_("probe")`) and, with providers off, the core's plain-HTTP `backend_config.ProbeClient` (`/openapi.json` only); `run-remote` moves to `vivarium_workbench_sms/cli.py` behind `Provider.cli()` / `Command` (same slot, arguments and help; absent with providers off; a broken plugin warns and drops its subcommands); startup probes `backend_config.start_probe(configured_base_url())` and `remote_link.start_probe` is deleted; `server_capabilities`, `cli` and `startup` import with the plugin absent; ratchet 29 → 26, route-ratchet pairs 13 → 12; no behaviour change with providers on | wb#1672 | **done**, merged 2026-11-19 |
| P2 Runs a1 | `remote_run_views`' SMS constants and login check (`_run_auth_ok`) move to `sms_legacy`; its six read-backs of `remote_run_views` are gone (new contract: the plugin does not import `lib.remote_run_views`); the SMS code still in `remote_run_views` reads every seam through `sms_legacy` (one patch target per name, no aliases; 105 test patches re-pointed); new optional facet `Backend.unrouted` answers the lifecycle requests no record routes (SMS's 401 still before 400), and with providers off the core answers them (400 / 409 `no-backend` / `backend-not-installed`); ratchet 26 → 25; no behaviour change with providers on | wb#1673 | **done**, merged 2026-11-19 |
| P2 Runs c2 | `run_jobs.refresh_submitted(job)` (no `client=`) asks each item's owner (`backends.for_run`): one `runs.status_many` call when the owner declares that optional method (`backends.BatchStatus`), otherwise `runs.status` per item with the down-skip; `_refresh_one`'s mapping becomes the shared `_apply`; `_refresh_sms` and `_UPSTREAM_*` move to `sms_legacy` (`status_batch`, `SmsLegacyRuns.status_many`); ratchet −1 (`run_jobs -> sms_api_client`), route-ratchet pairs −1 (`lib.run_jobs: compose_status_batch`); with providers on, three declared changes: an id stored as the text `"12"` settles; an id no backend owns no longer makes the SMS batch fail (it used to leave every SMS item `submitted`); a non-text status from viva-api drops the whole batch (it used to apply the rows before it). Only canonical integer text matches an SMS answer (`"012"` never settled, before or now); providers off, integer batch items are not asked and stay `submitted` | wb#1675 | **done**, merged 2026-11-19 |
| P2 Runs b | `remote_run.run_remote` becomes a backend-blind dispatcher (`select`, else `unrouted`, else `RuntimeError`) over the new `RunFacet.run_composite` / `UnroutedFacet.run_composite` (the detached runner's blocking call; compose-api's is today's branch); the SMS half and its poll loop move verbatim to `vivarium_workbench_sms/deployment_run.py` (sms-legacy and unrouted: `/compose/v1`; viva-core: `via_viva_v1=True`); `viva_v1_document_options` / `compose_simulation_id` move to `remote_run_viva_v1`; the plugin CLI's `run-remote` calls `deployment_run.run`; the PTools landing stays in `run_runner`, untouched. Providers off: a deployment run with nothing named is a `RuntimeError` in the runner, and the opt-in compose path is refused at launch (409 `no-backend`); plugin absent: a named compose-api run now works. Ratchet 24 → 22, route-ratchet pairs 11 → 7 (after Runs c2); no behaviour change with providers on | wb#1677 | **done**, merged 2026-11-19 |
| P2 Runs c1 | The SMS store landing moves verbatim from core `remote_run_landing` into `vivarium_workbench_sms/sms_landing.py` (`land_remote_run`, `land_remote_simulation_artifacts`, `RemoteRunSeedCountMismatch`, the zarr/parquet helpers); the core keeps the archive landing (`land_composite_results`, `fold_analyses`, `land_ptools_tsvs`) byte for byte, read by the plugin through its module; `sms_legacy` no longer binds `land_remote_run` (one binding, `sms_landing`) and gains `land_artifacts_tail` (the SMS half of `remote_run_land_artifacts`, whose guards stay core); a PTools golden test (paths, sha256, `analyses.json`, run id, computed before the move) pins the layout; ratchet 22 → 20, route-ratchet pairs 7 → 6 (after Runs c2 and b); no behaviour change with providers on | wb#1679 | **done**, merged 2026-11-19 |
| P2 Runs a2 | The four SMS-only routes (`/api/remote-run-start`, `-status`, `-build`, `-pinned-build`) move verbatim from `api/app.py` to the plugin's `routes.py`, served through the new `Provider.router() -> ProviderRouter` (routes + the paths a read-only deployment keeps); their views (the legacy pipeline submit, the build phase, `_resolve_repo_branch`) move to `vivarium_workbench_sms/remote_pipeline_views.py`, which only `routes` imports (env workers never load `remote_run_jobs`); `create_app` adds the routes flat before the SPA catch-all and refuses a clash or a foreign allowlisted path; the read-only allowlist is the app's own (`app.state.readonly_allowed_mutations`); new contract: the plugin does not import `api.app`; OpenAPI and the read-only route set identical with providers on; with providers off the routes are absent (POST 405, GET 404); ratchet 20 → 18, route-ratchet pairs 6 → 5 (after Runs c2, b and c1) | wb#1678 | **done**, merged 2026-11-19 |
| P2 Builds B1 | The session build is the provider's answer: `Provider.session_build(ws_root) -> SessionBuild \| None` (`SessionBuild` beside `Recognised` in `lib/backends/provider.py`; no network call, never raises) and core `backends.session_build` (the first provider's answer; `None` with providers off or the plugin absent); `SmsProvider.session_build` = `remote_pinned.session_build` (reads `.viv-build.json`), and `SmsProvider.select` uses it instead of `run_core.run_target_for`; the six core readers (`run_core.run_target_for`, `composite_resolve`, the ParCa notice in `composite_state_views`, the rail chip in `report`, `provenance_manifest`, the readouts soft-degrade) ask `backends.session_build` and no longer name the file (an AST scan pins it; `lib/remote_run.py`'s git-dirtiness exclusion stays); providers off or plugin absent: a stamped workspace reads as plain local; ratchet 18 → 16 (after Runs); no behaviour change with providers on except three corners on stamps no writer produces (the `{}` stamp gets the ParCa notice; a null id shows `#?`; a non-dict stamp is unreadable instead of an `AttributeError`) | wb#1680 | **done**, merged 2026-11-19 |
| P2 Builds B2 | The composite card and `/api/remote-dispatch-preflight` stop branching on backend identity: new `RunFacet.dispatch_preflight` / `dispatch_composite` on all three backends (compose-api: `checked_steps`, then `Detached`; viva-core: the named-environment git preflight; SMS: the cloud dispatch) and `UnroutedFacet.dispatch_composite` (the explicit cloud run with nothing selected); `Detached(build_ref)` in `lib/backends/provider.py`; the card's SMS helpers (`resolve_cloud_target`, `CloudTarget`, the image dispatch, the opt-in compose path) move verbatim to `vivarium_workbench_sms/cloud_dispatch.py`; `backend_environment` / `DEFAULT_BACKEND_ENVIRONMENT` move to `remote_run_viva_v1`. The #1107 drift guard gains a dirty tree; Finding F (an `id:` environment: the card skips the git preflight, the preflight route runs it) was pinned as a strict xfail; **resolved in B2b** (wb#1685). Providers off / plugin absent: an explicit cloud run with nothing selected is 409 `no-backend`. Ratchet 16 → 14, route-ratchet pairs 5 → 4, route literals 1 → 0 (after Runs and B1); no behaviour change with providers on (a dispatch table pinned on the base) | wb#1683 | **done**, merged 2026-11-19 |
| P2 Analyses and figures | The analysis-output and re-run requests go behind two new optional facets on `Backend`: `artifacts` (`land_outputs`, `list_figures`, `read_figure`, `study_figures`) and `analyses` (`rerun`), declared only by `sms-legacy` (new plugin module `vivarium_workbench_sms/analyses.py`: the re-run's SMS half moved verbatim, the land-artifacts guards, the integer-id check, the figure reads, the gallery). The five routes stay core; `remote_run_views` routes by the run's record (`_artifacts_of` / `_analyses_of`; a backend without the facet answers 409 `not-sms`), sends a land-artifacts or re-run that names no run to the `unrouted` backend's facet (SMS's 401 still before its 400), and asks every backend `backends.offering("artifacts")` lists for the study gallery. `_sms_only` and its memo are gone (`_FACET_MEMO`), and `api/app.py` no longer imports `remote_analysis_figures`. Route-level goldens recorded on the base. Providers off: SMS is never reached (a named run is 409 `backend-not-installed`, no-id requests get the core's 400s, the gallery is empty); before, an integer id with no record reached SMS. Ratchet 14 → 13, route-ratchet pairs 4 → 3 (after Builds B1 and B2). With providers on, no behaviour change except four declared corners: an unauthenticated land-artifacts / re-run of another backend's run is 409, not 401 (record before login, as `remote_run_land`); a land-artifacts with a zero or blank `simulation_id` gets the facet's "simulation_id and run_id are required"; the `not-sms` refusal's `error` text is neutral; the view refuses a landing folder (`run_id`) that is a path (routes already did, through the app-wide identifier guard) | wb#1684 | **done**, merged 2026-11-19 |
| P2 Builds B3 | New optional facet `Backend.builds: BuildFacet` (`pin`, `run_study`), declared only by sms-legacy (`sms_legacy.SmsLegacyBuilds`): `pin` is `remote_run_views._remote_run_config` moved verbatim, and `/api/remote-run-config` asks the selected backend's `builds.pin` (else `{"pinned": false, deployment}` from `backend_config`; the `backend` key stays conditional on the profile); `run_study` is the study baseline's vEcoli dispatch from `study_runs.run_study_baseline`, reached through `backends.active`, and calls `sms_legacy.submit_tail`; `workspace_deps_views._sms_api_base` deleted (its users read `sms_api_client.sms_api_base` through the module; `remote_health` reads `backend_config.configured_base_url`) and `build_source_builds` moved to `vivarium_workbench_sms/source_build_views.py`; `/api/source/builds`, `build-remote` and `switch-build` move to the plugin router (the two POSTs declared read-only-safe), the switch's session half is the new core helper `api.deps.bind_session`, shared with core `/api/source/switch`; goldens taken on the base (`tests/test_builds_goldens.py`) hold with providers on; OpenAPI operations and the read-only route set identical (one route description names the builder's new module); providers off: the build routes are absent (GET 404, POST 405) and a pin env var no longer reaches SMS's pin (`pinned: false`); ratchet 13 → 6 (−7, after Analyses: the six build edges plus `remote_run_views -> sms_legacy`, whose last user `_sms_client` went with the pin); `STILL_NEED_PLUGIN` loses `remote_run_views` and `api.app`, which now import without the plugin | wb#1686 | **done**, merged 2026-11-19 |
| P2 Builds B2b (Finding F) | A viva-core `id:` environment runs its prebuilt image and ships nothing from git (Jim's decision: rule A plus a non-blocking warning). `VivaCoreRuns.dispatch_preflight` answers `ok: true` for an `id:` environment whatever the tree, and when the git check finds it dirty or unpushed adds `warnings: [...]` (the card's run-target badge shows an ⓘ and the note in its tooltip, styled ready); `deployment_run.run(via_viva_v1=True)` with an `id:` environment skips `git_pip_url` and `workspace_pinned_deps` (`viva_v1_document_options` dropped them anyway). Named environments, the legacy `/compose/v1` path, compose-api, the card and the study page are unchanged. The #1107 drift guard's Finding F xfail now passes; the dispatch table's preflight `id:` row is updated | wb#1685 | **done**, merged 2026-11-19 |
| P2 Runs b2 (trace) | The trace splits along the facet line: `remote_trace`'s viva-api half (`make_client`, the `viva-v1-trace` capability check, `simulation_trace`, `composite_run_trace`, the `compose_id` correlation, `_positive_int`, `_safe`) moves verbatim to `vivarium_workbench_sms/sms_trace.py` (client built through `sms_legacy`'s seams, capability helpers read through `sms_api_client`); `SMS_LEGACY.runs.trace` / `VIVA_CORE.runs.trace` become real; new `UnroutedFacet.trace(ids)` / `trace_support()`; the core keeps record routing (a provider's backend answers only a LONE `simulation_id` its record names; compose-api either key), compose-api's support answer, `count_trace_events` and the Perfetto `viewer`; `fetch_trace` / `trace_support` keyword-only with no client; `records.not_installed` / `unowned` (from `remote_run_views`) give the run views and the trace one refusal body. Providers off: support is `supported: false, reason: "no-backend"` (no request), a lone id no record or form routes is 409 `backend-not-installed` (any of the three keys), no id / several stay 400; plugin absent: the routes answer instead of 500. Ratchet 6 → 5, route-ratchet pairs 3 → 1 (after Builds B3), `STILL_NEED_PLUGIN` 1 → 0 (empty: every core module imports without the plugin); no behaviour change with providers on (a 140-row dispatch table pinned on the base) | wb#1687 | **done**, merged 2026-11-20 |
| P2 Remote tab R1 (listing) | The remote runs in `/api/simulations` go behind a new optional facet `Backend.listing` (`ListingFacet`: `list_runs(ws_root, *, fresh=False)`, `state(ws_root)`, `invalidate()`), declared only by `sms-legacy` (`SmsListing` in `vivarium_workbench_sms/remote_simulations.py`: the same stale-while-revalidate calls, read at call time). The core finds it by facet presence (`backends.offering("listing")`, a registry read: never `active()`, a profile or a record, #1106): `simulations_index._append_remote_simulations` (name and signature kept; the merge is `list_remote_runs`, deduped against local rows and, new, across backends), the `remote` state (first non-`None` answer), `investigations_index._remote_study_run_stats`, and `?refresh=true`'s flush (`simulations_index.forget_remote_listings()`; it reached into the plugin's private caches). The study-card gate (`workspace.yaml` `remote_run_study_map`: `load_rules`, `infer_study_slug`) moves to new core `lib/remote_study_map.py`; `_study_investigation_map` stays in the plugin. Route-level goldens recorded on the base (payload and outbound calls on `?refresh=true`, the warm default path, `include_remote=false`, study counts, the flush). Providers off (declared): SMS is never listed or asked (before, it was), so the Runs table, the study counts and the analysis-tool run picker (`analysis_tools._run_candidates`) have no SMS rows and `remote` is `null`; plugin absent: unchanged, now probed. Ratchet 5 → 2 (after Runs b2); no behaviour change with providers on | wb#1688 | **done**, merged 2026-11-20 |
| P2 Remote tab R2 (store) | `ArtifactFacet` gains the required `fetch_store(run_id, dest) -> Path | None` (blocking; `None` when the server cannot provide the store; other errors propagate). `simulations_index.resolve_or_fetch_store` keeps the local half, the `for_existing` routing and the temporary directory (now removed on `None` **and** on an exception); "the run's backend is `SMS_LEGACY`" becomes "the run's backend has `artifacts`". The download (`SmsApiClient(...).download_data(int(id), dest)`, `SmsApiError` → `None`) and the tar extraction into `dest/"extracted"` (`filter="data"`) move verbatim to `sms_legacy.fetch_store_tail`, which reads `sms_legacy`'s own seams; `SmsArtifacts.fetch_store` calls it through the module. Goldens taken on the base (`tests/test_store_fetch_goldens.py`: the s3-only SMS zip's names and bytes, the composite-run artifact's zarr fallback, the 404 of a viva-core / compose-api / other-deployment row, `SmsApiError` → `(None, None)`) hold with providers on. Providers off and plugin absent: `(None, None)`, SMS never asked (as before; now pinned). Ratchet 2 → 1 (`simulations_index -> sms_api_client`; only the registry edge is left), route-ratchet pairs 1 → 0 (the core calls no SMS method; after Runs b2 and R1) (`lib.simulations_index: download_data`; `download_data` left `PINNED_UNION`). One declared corner, not observable in any response: a `fetch_store` that raises (a corrupt tar) no longer leaves its temporary directory to the garbage collector | wb#1690 | **done**, merged 2026-11-20 |
| P2 Routes Rt2 (ai_excluded) | `ProviderRouter.ai_excluded` (default empty): a provider's routes the built-in AI must never see or call; `create_app` refuses a path that is not one of the router's own routes (as for `readonly_allowed`) and keeps the union on `app.state.provider_ai_excluded`; `lib/ai_tools.EXCLUDED_PATHS` drops `/api/remote-run-start` and `-build`, which the SMS router now declares (`routes.AI_EXCLUDED`), and `build_index` / `capabilities` use `ai_tools.excluded_paths(app)`; `is_excluded` takes the path set; `ai_tools` names no plugin route (the moved `/api/source/*` build routes were never literals: the core's `/api/source/` prefix covers them). Goldens taken on the base (`tests/test_ai_excluded_goldens.py`): with providers on, the operations the AI index leaves out and the Capabilities popover's `excluded` list are identical; declared: with providers off the two paths leave the popover's list (the routes do not exist then) | wb#1689 | **done**, merged 2026-11-20 |
| Compose: the card checks the environment like the preflight | The compose analogue of Finding F: the preflight refused a named environment compose-api does not list (409 `unknown-environment`) or a compose-api it could not ask, while the card launched anyway and the detached run failed in `run_remote`. `remote_run_compose.check_environment(ws_root, *, timeout)` is the environment step both now share; `ComposeApiRuns.dispatch_composite` calls it after `checked_steps` with `CHECK_TIMEOUT` (15 s, since it runs inside the launch request; the preflight keeps the session's 120 s) and refuses with the preflight's reason (`ComposeDispatchError`'s body and status, or `_backend_error`'s for a client error), writing no run row and spawning nothing. The run-target badge shows an amber `Runs: Cloud · not ready ⚠` for any `ok: false` cloud preflight. The #1107 drift guard now runs compose's real preflight and card check against one fake session, with an unknown environment and a registry error as cases (strict xfail on the base); the dispatch table gains the unknown-environment row. viva-core and SMS dispatch unchanged | wb#1691 | **done**, merged 2026-11-20 |
| P2 Routes Rt3 (close P2) | The end-of-P2 checklist: the import-linter ignore list is the registry edge alone (`lib.backends.provider -> vivarium_workbench_sms.provider`; P4 replaces it with an entry point); `STILL_NEED_PLUGIN` is empty and a permanent guard (any entry is a regression) and `api.app` joins `MUST_IMPORT`; the route ratchet becomes a ban (`ALLOWED`, `PINNED_UNION`, `PINNED_PAIRS`, `ROUTE_LITERALS` deleted; the core references no SMS method and spells no viva-api route, `/viva/v1` included); new guard `tests/test_core_names_no_provider_backend.py` (no `SMS_LEGACY` / `VIVA_CORE` / `_ATTRS` / `_SENTINELS` and no `"sms-legacy"` / `"viva-core"` literal outside `lib/backends/`). Its one hit fixed: `study_runs._named_backend_runs_documents` decides by facet (a backend with `builds` is not a document run; any other selected backend is), goldens taken on the base (`tests/test_study_dispatch_by_backend.py`: none/compose-api/viva-core/sms-legacy). Plugin-absent probe: `remote-sim-12` status 409 `backend-not-installed`, Stop `remote-uncancellable`, `create_app()` serves trace support `no-backend`. No behaviour change | wb#1695 | **done**, merged 2026-11-20 |
| P2 | Split the mixed modules area by area (§2.5): SMS parts into the plugin, core callers through `backends` and facets, facet routes stay core, SMS-only routes into the plugin's router | — | **done** with Routes Rt3: the rows above are its PRs; the models moved to P3 (M1) |
| P3-0 | Provider interface additions: `features`, `check_config`, `base_url`, `deployment_default` (§2.2); no core caller, no behaviour change | — | dropped: its methods come with the PRs that use them (F0 `features`, M1 `payload_types`, C1 the config methods) |
| P3 F0 | Declared features: `Provider.features()` (SMS: `sms-launch`), `lib/backends/features.py` (`process_features`, `run_features`), `GET /api/backend/features` (§2.2), `remote_origin.features` on `/api/simulations` rows, `static/backend-features.js` (no consumer yet: no UI change), the browser SMS-terms ratchet (`tests/test_browser_sms_terms.py`) | wb#1696 | **done**, merged 2026-11-20 |
| P3 F1 | Runs table (`sim-table.js`) by the run's own features (`remote_origin.features`; remote vs local decided first, so local rows are unchanged, and a remote row without the key declares nothing): "⬇ Analysis files" (land then download) and the figure window (remote Viz) need `artifacts`, "🧪 Re-run analysis" needs `analyses`, "⬇ Land results" is every remote row with a study and no `artifacts` (replacing the id-form test). SMS rows unchanged; viva-core and compose rows lose the Viz, Analysis files and Re-run analysis the server answered with 409; an all-digit viva-core id gets Land results (a fix); providers off, any remote row shows only Trace, Report, Land results and Re-run simulation; the snapshot unchanged (its remote rows keep the Viz button they had). Neutral wording ("on its backend"), the terms ratchet's `sim-table.js` row removed (now zero). Goldens on the base (`tests/js/test_sim_table_actions_by_features.js`, the real `_actionList`, now `SimTable.actionList`), including the SMS row's exact land-artifacts body (PTools path) | wb#1698 | **done**, merged 2026-11-20 |
| P3 F2 | Study page (`study-detail.js`, `study-detail.html`): the remote figure gallery (Visualizations tab) and the remote ptools list (Analyses tab) ask `/api/study-remote-figures` only when `BackendFeatures` declares `artifacts`; unknown features (a failed fetch, or no `backend-features.js`) ask as before, and the snapshot bundle keeps its old behaviour without consulting them. Per review B1 nothing else reads features: `SmsLaunch` still mounts whenever its script is loaded and `/api/remote-run-config` still decides the dispatch (no `has("sms-launch")` outside `sms-launch.js`). Neutral wording: "Rendered on GovCloud" becomes "Rendered on the remote backend", and the page's comments and the launch plugin's error text no longer name sms-api / `sms-launch.js` / smsvpctest; both files leave the SMS-terms ratchet at zero. Declared: providers off, the gallery makes no request and its panels stay empty (today they asked and the server answered `available: false`). Tests: `tests/js/test_study_gallery_features.js` (declared / known-empty / unknown / absent / snapshot), `tests/js/test_sms_launch_hook.js` case 4b (known-empty features: the plugin still mounts and the pinned dispatch still goes remote) | wb#1697 | **done**, merged 2026-11-20 |
| P3 F3 | Source panel, workspace picker, `VivEnv` by `builds` (feature 9 rides on it) | — | not started |
| P3 F4 | a: the Runs listing's second phase by `listing`; loom wording; b: the composite card's SMS tips come from the preflight answer | — | not started |
| P3 I1 | The synthetic composite run id's prefix is one core constant, `lib/backends/ids.py` `REMOTE_RUN_PREFIX = "remote-sim-"` (kept: viva-superpowers' skills poll `/api/composite-run/<run_id>/status` and read `status`); the plugin's `cloud_dispatch` builds its run id through it, and an AST test pins that the core spells it nowhere else. `/api/composite-run/remote-sim-<id>/status` passes the suffix on as the opaque string (`opaque_run_id`-normalised) and echoes the backend's `simulation_id` (SMS: still its integer), else the suffix. The refusal literal `not-sms` becomes `not-offered` (`reason` and `phase`; core `_not_offered` and the plugin's non-integer-id check); `study-detail.js` accepts both. The composite Stop note is neutral ("Remote runs continue on their backend — track it in the Runs tab."), the `remote-uncancellable` detach kept. Docstrings: `_sim_recency_key`, `RemoteOrigin.simulation_id` ("the run's id on its backend"). Goldens on the base (`tests/test_remote_ids_goldens.py`, needs the plugin; the pure-core pins in `tests/test_remote_run_prefix.py`); the bare-id audit (`tests/test_bare_id_audit.py`): every core caller of the existing-run router answers an integer id with no record, providers off, with 409 `backend-not-installed` (the store fetch: documented absent, `(None, None)`), and a new caller fails it until listed. Declared: the `not-offered` literal; the stop note; a non-SMS run with an all-digit id echoes `"123"`, not `123`; a non-ASCII-digit suffix (`"١٢"`) is no longer `int()`-ed into an SMS id; a negative suffix (`remote-sim--12`) is passed on and echoed as `"-12"`, not `-12` (every spelling of zero is still no id: the 400). Bundle keys: `simulation_id` / `remote_origin` are kept, read as opaque "the run's id on its backend"; `simulations.json` only gains keys | wb#1694 | **done**, merged 2026-11-20 |
| P3 C1 | Config seams. New `lib/backends/config.py` (`check_config`, `configured_base_url`, `backend_configured`, `deployment_name`, `deployment_names_now`, `stray_backend_env`) reads the core's half plus each active provider's, through four new `Provider` methods: `check_config(named)`, `base_url()`, `configured()` (review fix 2: `base_url()` keeps the tunnel default, `configured()` says an alias is set) and `deployment_default()`. `backend_config` keeps the neutral half: `named_backend` (the named URL is valid; replaces `check_single_backend`), `configured_base_url()` = the named URL or `None`, `deployment_name(default=None)` with the neutral `_DEFAULT_DEPLOYMENT = "default"`, and `BACKEND_BASE_ENV_VARS` only for the warning; `DEFAULT_BACKEND_BASE`, `backend_configured` and `check_single_backend` are deleted (no aliases; `sms_api_client`'s re-exports of them too). The plugin owns `ALIAS_ENV_VARS`, the alias check (`check_aliases`, verbatim), `sms_api_base()`'s precedence, the tunnel default and `smsvpctest` (`remote_pinned`). Core callers re-pointed: `cli` (both checks), `startup`, `api/__main__` (both now report a broken plugin like `cli`; `api/__main__` no longer imports `api.app` at module level, which built the app before the check), `records` (the legacy guard's names include every provider's default and, with a provider active, the alias hosts), `composite_run_views`, `composite_test_run_views`, `remote_run_compose`, `remote_run_views`, `workspace_deps_views.remote_health`. `RemoteHealth.base_url` is `str | None` (not in the generated TS; `branch-source.js` guards it). Start-up warns `<VAR> is set but no backend provider is active; it is ignored`; the CLI adds a generic hint to "invalid choice" when no provider added subcommands. Goldens on the base (`tests/test_backend_config_goldens.py`: 12 environments × on/off × check, URL, deployment, remote-health, `serve`'s start-up lines, the legacy guard's names, `smsvpctest` rows): providers on unchanged. Declared, providers off or plugin absent: an alias is ignored (no URL, no tunnel, no ping, no breaker thread, no refusal beside a named backend; a warning), remote-health `configured: false, base_url: null` with no ping, deployment default `"default"`. Verified: no env-worker path writes `params.remote` | — | in review (2026-11-20) |
| P3 C2 | Server wording and hints neutral: startup lines, the runner's failure hint, `ProbeClient`, route descriptions (declared OpenAPI description change) | — | not started |
| P3 M1 | The SMS-only payload models (`RemoteJobStatus`, `RemoteRunStep`, `RemoteRunJob`, `RemoteRunStartRequest/Response`, `BuildRemoteRequest/Response`, `SwitchBuildRequest`, `SourceBuilds`) move verbatim to `vivarium_workbench_sms/models.py` behind the new `Provider.payload_types() -> PayloadTypes`; `generate_ts` emits the core's types, then each provider's, and refuses to write with providers off; OpenAPI `components.schemas` byte-identical with providers on, the `.d.ts` declaration blocks identical (order and header changed); goldens taken on the base | wb#1693 | **done**, merged 2026-11-20 |
| P3 K1 | New non-blocking `core-only` CI job (`.github/workflows/pytest.yml`, 4 shards plus the stable `core-only` result check, which reads the shards' conclusions from the API because `needs.result` is `success` under `continue-on-error`): installs as `suite` does, deletes `vivarium_workbench_sms/` (the editable `.pth` lists the project root, so the package stops resolving; a `find_spec` step asserts it), smokes `create_app()` and the CLI (`serve` listed, `run-remote` not), then runs the gate with `uv run --no-sync`. `tests/conftest.py` gains `CORE_MODE` (`absent` / `off` via `VIVARIUM_WORKBENCH_BACKEND_PROVIDERS=none`), the `plugin` marker (absent: strict xfail; off: skip), an AST collection-ignore for modules that import the plugin at runtime module level, directly, through a sibling test module or through a module function called at import (`if TYPE_CHECKING:` excluded) and job-summary counts; `scripts/pytest_core_only.sh` is the best-effort local mode (`VIVARIUM_WORKBENCH_TEST_NO_PLUGIN=1`). `tests/test_plugin_test_marking.py` ratchets the 53 unmarked module-level importers (K2 empties it). `continue-on-error` until Z (K3), then required (Jim, 2026-11-20) | wb#1692 | **done**, merged 2026-11-20 |
| P3 K2a | Test conversions, backends group (lazy plugin imports, `plugin` marks) | — | not started |
| P3 K2b | Test conversions, workers and CLI group | — | not started |
| P3 K2c | Test conversions, views and index group | — | not started |
| P3 Z | Close P3: the SMS-terms ratchet becomes a ban, the AST string-constant ban, K3 (the core-only job blocking, then a required check) | — | not started |
| P3 | Opaque ids (done: wb#1660, finished by I1); launch panel (done: wb#1659); declared features hide SMS-only controls (F0–F4); config seams (C1, C2); models (M1); core-only CI (K1, K2, Z) | — | in progress: the rows above are its PRs |
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
    unrouted: UnroutedFacet | None     # P2 Runs a1: lifecycle requests no run record routes (SMS today)
```

- **`unrouted`** answers the lifecycle requests (submit, land, status, progress, cancel, and since P2 Runs b2 trace and trace support) that name no run the core can route by its record: no run id at all, or a build / analysis id riding along. Its methods are `sms_legacy`'s `*_tail` signatures, so SMS's own checks (its 401) still come first; at most one backend may declare it, and with providers off the core answers those requests itself. A bare `analysis_id` poll (`/api/remote-run-poll?analysis_id=`) stays with it for good: it names no run, so no record can route it (P2 Analyses and figures). It shrinks to "no run id, or only an analysis id" once Builds takes the build ids. The same backend's `artifacts` / `analyses` facets answer the land-artifacts and re-run requests that name no run.

- **`artifacts` and `analyses`** (P2 Analyses and figures). `ArtifactFacet`: `land_outputs(ws, body)` (study-less landing into `.pbg/runs/<run_id>/`), `list_figures(run_id)`, `read_figure(run_id, analysis, path)` (`RunFacet.trace`'s `(bytes | dict, status, content_type)` shape) and `study_figures(ws, study, max_runs)`; `AnalysisFacet`: `rerun(ws, body)`. Only `sms-legacy` declares them (`vivarium_workbench_sms/analyses.py`). The core routes a request that names a run by its record and passes the id as a normalised string; the facet makes its own checks (SMS: login, the "required" 400s, the integer-id check). The study gallery names no run, so the core asks every backend that declares `artifacts` (`backends.offering`, compose-api first; the first `available` answer wins).
  - **`artifacts.fetch_store(run_id, dest) -> Path | None`** (P2 Remote tab R2; required, since SMS implements all of `artifacts`): download the run's native store into `dest` and return where to read it, or `None` when the server cannot provide it; any other error propagates. It answers no route: it is `simulations_index.resolve_or_fetch_store`'s remote half (the Results zip of `/api/simulation-run-download`, the Viz/Report zarr fallback), so, like `run_composite`, it **keeps the blocking `Path` shape of the code it replaced** rather than `(body, status)`. The caller owns `dest` (a temporary directory it removes on `None` and on an exception) and picks the backend by the run's record (`for_existing`); a backend without `artifacts` has no store to fetch. SMS's implementation is `sms_legacy.fetch_store_tail`; PTools files are untouched (it writes only under `dest`).
- **`builds`** (P2 Builds B3; `backends.BuildFacet`) runs the workspace as one of the backend's own builds. Only sms-legacy declares it (`sms_legacy.SmsLegacyBuilds`); the core reaches it through the backend it selected (`select` / `active`), so there is no registry rule. Two methods, each taken from the code it replaced:
  - `pin(ws_root) -> dict` is `/api/remote-run-config`'s build half (`remote_run_views._remote_run_config`, moved verbatim): the session's own build, else the deployment's pin, else `{"pinned": false, deployment}`; an unresolved build is `build_error`, never a raise. With no backend with builds selected, the core answers `{"pinned": false, "deployment": backend_config.deployment_name()}` itself and asks nothing.
  - `run_study(ws_root, *, study, params, request) -> (body, status)` is the study baseline's dispatch of the default entry on the selected build (from `study_runs.run_study_baseline`): SMS resolves the build id, renames to `num_generations` / `num_seeds` / `extra_params`, answers its 409 when no build resolves, and submits through `submit_tail`.
  - `run_study` sits on `builds`, not `runs`, **only because this facet is optional**: it has no meaning for a backend that runs documents, and putting it on `runs` would make every backend implement a refusal.

- **`listing`** (P2 Remote tab R1). `ListingFacet`: `list_runs(ws, *, fresh=False) -> list[dict]` (rows shaped like `simulations_index` rows, `remote_origin` included, carrying `study_slug` / `studies` / `investigation_slug` when the backend knows the run's study), `state(ws) -> dict | None` (`RemoteSourceState`: `{state, as_of, error}`) and `invalidate()`. They replace `list_remote_simulations_swr` / `list_remote_simulations(use_cache=False, ...)`, `remote_state` and `app.py`'s two `.clear()` calls. **The rule: no method blocks on the network unless `fresh`** (then a bounded fetch: SMS's 10 s, one retry), and none raises for an unreachable server. Only `sms-legacy` declares it (`SmsListing`, `vivarium_workbench_sms/remote_simulations.py`). The core merges every listing backend's rows (`backends.offering("listing")`), deduped by `run_id` against the local rows and across backends (first seen wins; one backend's own rows are kept as given); the `remote` state is the first non-`None` answer.

- **Facets are named by capability, and their interfaces carry no SMS terms** (Jim, 2026-11-19).
  - A facet says what it does (`listing`, `artifacts`, `analyses`, `builds`), not how SMS does it.
  - SMS-specific detail lives in the plugin's implementation, for example vEcoli parameters, integer simulation ids and branch builds.
  - This leaves a path for compose-api: its core adapter can implement any facet later (§2.6), and the existing routes and UI then work for compose-api runs with no other workbench change.
- **Features are derived, not listed (P3).**
  - The facet features are the facets an active backend has: `listing`, `artifacts`, `analyses`, `builds`.
  - A provider declares only the UI features no facet implies (`Provider.features()`; SMS: `sms-launch`).
  - A run's own features are the facets of the backend that ran it (`artifacts`, `analyses`), carried on its `/api/simulations` row as `remote_origin.features`.
  - **Features only hide optional controls** (the gallery, re-run, figures, build rows, the listing's second phase). No dispatch or run-target decision reads them: `/api/remote-run-config` and the preflight decide, as today. A features request that fails is "unknown": retried once, then the controls show as today and the server refuses with its 409.
- **A facet is defined only when a step needs it.** Its methods come from the code it replaces; method shapes are not designed ahead of the code.
  - **An optional method on a facet** follows the same rule. `runs.status_many(run_ids) -> {run_id: body}` (`backends.BatchStatus`, step P2 Runs c2) asks the status of several runs in one call. Only SMS declares it, because it replaces SMS's one `compose_status_batch` call; no other backend has a batch route. `run_jobs.refresh_submitted` finds it by `getattr` and otherwise asks `runs.status` per run. The inert sentinels and the `unrouted` facet never declare it.
- **Facets keep the views' `(body, status)` shapes,** as the run facet does, so routes and the browser don't change when code moves behind one.
  - **The composite card's two methods (P2 Builds B2), on every backend's `runs`:**
    - `dispatch_preflight(ws_root) -> dict` is `/api/remote-dispatch-preflight`'s body once `backends.active` chose the backend (compose-api: does a registry environment run the workspace, `remote_run_compose.check_environment`, which its `dispatch_composite` also asks before it launches; viva-core: the git preflight for a named environment, `ok` with a non-blocking `warnings` entry for a dirty `id:` environment (B2b); SMS: does a cloud image resolve, with the `actions[]` shape when none does).
    - `dispatch_composite(ws_root, body, *, spec_id, overrides, emit_paths, steps) -> (body, status) | Detached | None` is the card's launch: a `(body, status)` the backend answers itself (SMS's image run, 202; any refusal), `Detached(build_ref)` when the run goes through the detached runner (`build_ref` written into the run request and carried unread to `run_composite`), or **`None` for a local run** (SMS's `resolve_cloud_target` can answer "local"). `Detached` lives in `lib/backends/provider.py`.
    - `UnroutedFacet.dispatch_composite` has the same signature: the Environment picker's explicit cloud run (`run_target: "deployment"`) when nothing is selected. The inert sentinels refuse both methods.
  - **The trace (P2 Runs b2).** `RunFacet.trace(run_id) -> (body, status, filename)` is real on all three backends (compose-api: `get-simulation-trace-chrome`; sms-legacy: `/api/v1/simulations/{id}/trace`; viva-core: `/viva/v1/composites/{id}/trace`; both viva-api ones through the plugin's `sms_trace`, after the `viva-v1-trace` capability check). `UnroutedFacet.trace(ids)` takes `{simulation_id, composite_run_id, compose_id}` as given (no record, a `/compose/v1` submission number, several ids) and its own id checks answer (its 400s); `UnroutedFacet.trace_support() -> (body, 200)` says whether that backend serves traces (`{supported, reason, capability, server_version?, error?}`, never raising; the core adds `viewer`). **No `RunFacet.trace_support`** (the review's simpler shape, chosen over the plan's): `named()` only ever returns compose-api or viva-core, compose-api's answer is static and core, and viva-core's would be the very same viva-api capability call the unrouted SMS backend makes, so a per-backend method would have one unreachable implementation (sms-legacy) and one duplicate (viva-core). A future provider whose named backend serves traces on its own terms adds it then.
  - **One exception: `RunFacet.run_composite`** (P2 Runs b). The detached runner (`run_runner._execute_remote`, through `remote_run.run_remote`) runs a composite to completion: the method blocks, returns the results archive's `Path` (a `.zip` of emitter history or a `.tar.gz` the runner folds) and raises (`RuntimeError`, `TimeoutError`). That is the shape of the two functions it replaced (`remote_run.run_remote`'s SMS half and `remote_run_compose.run_composite`), and its only caller is a detached process, not a view. `analysis_options` and `build_ref` ride along unread until Analyses and Builds take them. Every backend implements it; the unrouted facet has it too.
- **SMS-specific dispatch goes behind `builds` and `runs`, not a predicate swap.** Two examples:
  - `study_runs` builds a vEcoli dispatch (`n_generations`, `n_seeds`, `run_parca`, the pinned simulator id).
  - ~~`composite_test_run_views` calls `/api/v1/simulations/discovery`.~~ Done in P2 Builds B2: behind `runs.dispatch_composite` (`vivarium_workbench_sms/cloud_dispatch.py`).

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
    # P3 (features: F0; payload_types: M1; the config methods: C1):
    def features(self) -> frozenset[str]: ...                  # UI features no facet implies (SMS: {"sms-launch"})
    def check_config(self, named: str | None) -> None: ...     # its half of the one-backend rule (BackendConfigError)
    def base_url(self) -> str | None: ...                      # the URL its backends are asked at (SMS: named, alias, tunnel)
    def configured(self) -> bool: ...                          # one of its own settings is set (SMS: an alias)
    def deployment_default(self) -> str | None: ...            # its deployment name when none is set (SMS: "smsvpctest")
    def payload_types(self) -> PayloadTypes | None: ...        # its wire models, for generate_ts (M1)
    # P2 Runs a2 (lib/backends/provider.py):
    def router(self) -> ProviderRouter | None: ...             # routes only this provider serves, built per call
    # P2 Workers (lib/backends/provider.py):
    def worker_launcher(self) -> WorkerLauncher | None: ...    # hosted env workers: viva-api's env-worker surface,
                                                               #   /env-worker/v1, sent as /viva/v1/workers when the
                                                               #   server advertises viva-v1-workers
    # P2 Client / CLI (lib/backends/provider.py):
    def probe_client(self, base_url: str) -> OpenApiProbe: ... # the client `recognise` reads a named backend with;
                                                               #   also answers openapi_operations(); no network call
    def cli(self) -> tuple[Command, ...]: ...                  # its subcommands (`run-remote`), built on every call
    # P2 Builds (lib/backends/provider.py):
    def session_build(self, ws_root: Path) -> SessionBuild | None: ...  # the build ws_root is a checkout of, when
                                                               #   this provider made it; no network call, never raises

@dataclass(frozen=True)
class SessionBuild:                                            # P2 Builds: what the build's record says, raw
    readable: bool                                             # False: the record exists but cannot be read
    id: int | str | None                                       # the backend's build id (SMS: the simulator id)
    repo_url: str | None
    commit: str | None
    branch: str | None

@dataclass(frozen=True)
class Command:                                                 # P2 CLI: the core owns argparse
    name: str
    help: str
    configure: Callable[[argparse.ArgumentParser], None]       # adds its arguments
    run: Callable[[argparse.Namespace], int]                   # the handler (exit code)
    uses_backend: bool                                         # joins cli.BACKEND_COMMANDS' start-up checks

@dataclass(frozen=True)
class ProviderRouter:                                          # P2 Runs a2
    router: APIRouter                                          # its routes (TYPE_CHECKING import: no FastAPI in workers)
    readonly_allowed: frozenset[str]                           # its POST/DELETE paths a read-only deployment keeps
    ai_excluded: frozenset[str] = frozenset()                  # P2 Routes Rt2: its paths the built-in AI never sees

@dataclass(frozen=True)
class PayloadTypes:                                            # P3 M1
    aliases: dict[str, object]                                 # named Literal types, kept by name in the .d.ts
    models: list[type[BaseModel]]                              # its interfaces, emitted after the core's
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
    - a plain-HTTP probe that compose-api detection and the health view use instead of `SmsApiClient`: `ping` (P1a) and `ProbeClient.openapi_operations` (P2 Client, used with providers off; with providers on the provider's `probe_client` is used, so the same client reads capabilities and OpenAPI).
- **The core's `select`** (from P1c) asks the providers first (the session build and the pin are the SMS provider's claims), then the named backend, then local. That is today's order.
- **A record whose `kind` no installed provider serves** is refused with 409 `backend-not-installed`. It is never guessed at by id form.
  - A run with no record (written before 1c-1, or listed live) falls back to `for_run()`'s id forms; when the kind that form names has no installed provider (a bare integer without the SMS plugin), it gets the same 409.
- **Routes keep their paths, so browser code and published bundles keep working.** Where a route lives depends on whether another backend could ever serve it:
  - **Routes for a facet live in the core.** They dispatch through the run's backend (`for_existing`, as step 1c-2 already does) and answer "feature unavailable" when that backend lacks the facet: concretely the terminal 409 refusal with `reason` / `phase` `"not-offered"` and a neutral `error` ("run X ran on viva-core, which does not offer analysis re-runs"). P3 I1 renamed the literal from `"not-sms"`; `study-detail.js` accepts both until Z (its test is on the chain-progress / cancel answers, which never carry it; the Runs table shows `error`), and `test_remote_ids_goldens.py` pins it.
    - `artifacts`: `/api/remote-run-land-artifacts`, `/api/remote-analysis-figures`, `/api/remote-analysis-figure`, `/api/study-remote-figures`;
    - `analyses`: `/api/remote-run-analysis`;
    - `listing`: merged into the core's `/api/simulations` (`simulations_index._append_remote_simulations`). The Runs table has no route of its own. Since P2 Remote tab R1 the `?refresh=true` flush (`simulations_index.forget_remote_listings()`) and the study cards' remote counts (`investigations_index._remote_study_run_stats`, gated on the core's `lib/remote_study_map.py`) go through the facet too.
  - **Declared features (P3 F0).** The core route `GET /api/backend/features` answers `{features: [...]}`, sorted: the optional facets some active backend has (`listing`, `artifacts`, `analyses`, `builds`, by `backends.offering`) and every provider's `features()` (`lib/backends/features.process_features`). Each `/api/simulations` row with a `remote_origin` carries the run's own, `remote_origin.features`: the facets among `artifacts` and `analyses` of the backend that listed it, else the one its record names (`params.remote.kind`), else the owner of its id's form (`simulations_index._annotate_run_features`, one pass over the merged rows). Both are registry reads (#1106: no profile, record or network call); providers off or the plugin absent, both are empty. The browser reads them through `static/backend-features.js` (`ready()` resolves `{features, known}`; `known: false` after a failed fetch and one retry means "unknown", not "none"; snapshot mode resolves `{features: [], known: true}` with no request). Features only hide optional controls: no dispatch or run-target decision reads them.
  - **The run lifecycle is already core and shared with compose-api:** `/api/remote-run-submit`, `-poll`, `-land`, `-cancel`, `-chain-progress`, `-trace-support` and `-trace`.
  - **Only SMS-only routes go to the plugin's `router()`**:
    - `/api/remote-run-pinned-build` and `/api/remote-run-build`;
    - the old pipeline `/api/remote-run-start` and `/api/remote-run-status`, which no browser code calls; read-only mode keeps both (start is declared read-only-safe; status is a GET, always kept);
    - the `/api/source/builds`, `build-remote` and `switch-build` build routes (P2 Builds B3, done: `vivarium_workbench_sms/routes.py`, views in `source_build_views`; the router declares `build-remote` and `switch-build` read-only-safe, as the core's allowlist did). `switch-build`'s session half (rebind the session, align the cookie, `session_env.prepare(managed=True)`) is the core helper `api.deps.bind_session`, which the core's `/api/source/switch` uses too (with no `managed` argument, as before), so no session code moved into the plugin.

    If `builds` is later made generic, those move to the core with it. `remote-health` stays in the core, on the core ping (`backend_config.ping` on `backend_config.configured_base_url()`).
  - **Mixed routes stay in the core, and their SMS branch goes behind a facet:**
    - `/api/remote-dispatch-preflight`: done in P2 Builds B2. The core keeps the head (`backends.active`, the unreachable body, local) and every branch after it is the chosen backend's `runs.dispatch_preflight`. **Correction:** the SMS "build on cloud / switch build" branch went behind `runs`, not `builds`; every backend has a preflight branch, and the card's launch asks the same backend's `runs.dispatch_composite` (#1107).
    - `/api/remote-run-config` reports the pin (`builds.pin` of the selected backend, P2 Builds B3, done) and the named backend's profile (core).
    - `/api/composite-run/{id}/status` and `/stop` branch on `remote-sim-<int>` ids (`composite_run_views`, `composite_stop_views`; Loom polls them). They should go through `for_existing`.
  - **The plugin router (P2 Runs a2, done for the four Runs routes).** `create_app` calls `_include_provider_routers` after every core route and before the SPA catch-all, then the read-only filter.
    - It adds each provider route to the app itself, flat, as `@app.post` would (`api.app._add_route_like`), not with `app.include_router`: from FastAPI 0.138 that adds one lazy entry that hides its routes from `app.router.routes`, so the read-only filter, the AI tool index and the clash check would not see them. The app-level dependencies (`_plain_identifiers`), the middleware (CSRF, Host, session) and the exception handlers apply to them as to the core's.
    - It refuses (`RuntimeError`) a (path, method) the app already serves, and a `readonly_allowed` path that is not one of the router's own routes: a plugin cannot re-enable a core route that read-only mode drops.
    - The effective read-only allowlist is the app's own, `app.state.readonly_allowed_mutations` (the core's `_READONLY_ALLOWED_MUTATIONS` ∪ the providers'), never a mutated module global, so providers-on and providers-off apps in one process do not leak into each other. `_apply_readonly_filter(app)` defaults to it.
    - The SMS router declares `remote-run-start` and `-build`; `remote-run-pinned-build` stays dropped in read-only mode, as before.
    - **`ai_excluded` (P2 Routes Rt2).** A provider declares which of its routes the built-in AI must never see or call (they push to git); like `readonly_allowed`, a path that is not one of the router's own routes is refused (`RuntimeError`), and the app keeps the union on `app.state.provider_ai_excluded`. `lib/ai_tools` (the optional `[chat]` extra; `api/app.py` does not import it) uses `EXCLUDED_PATHS ∪ app.state.provider_ai_excluded` (`ai_tools.excluded_paths(app)`) for the tool index and the Capabilities popover, so `ai_tools` names no plugin route. The SMS router declares `remote-run-start` and `-build` (`routes.AI_EXCLUDED`); its `/api/source/*` build routes stay out of reach through the core's own `/api/source/` prefix rule. With providers on, the index and the popover are identical to before; with providers off the two paths leave the popover's list (the routes do not exist then).
    - With providers off the SMS-only routes do not exist: a POST answers 405 (the GET-only catch-all matches the path) and a GET 404, which is how read-only mode already answers a dropped route.
    - A provider that fails to load now raises at `create_app()`, not at first backend use (a broken plugin must not look absent).
- **CLI (P2, done).** `run-remote` is wholly SMS (`--sms-api-url`, `SmsApiClient`); it is the SMS provider's `cli()` (`vivarium_workbench_sms/cli.py`), and since Runs b it calls the plugin's `deployment_run.run` directly with its own client (no backend selection). The core adds provider commands at the slot `run-remote` always had, so `--help` is unchanged with providers on; a name that collides with a core subcommand raises. `Command.uses_backend` puts a command with `cli.BACKEND_COMMANDS`, so the one-backend check and the hosted-worker check run before it. With providers off (or the plugin absent) the command does not exist (argparse "invalid choice", exit 2; a pointed message is P3). A provider that fails to load is a warning in the CLI and only its subcommands are dropped, so `doctor` still runs; the backend commands still refuse to start (`check_worker_config` asks the providers again). A provider subcommand whose `configure` raises, or whose name collides, still fails the whole parser: a plugin bug its tests catch, to revisit when P4 makes the plugin an external package.
- **Hosted env workers (P2 Workers, done).** `env_worker_launcher.default_launcher()` asks the providers, in order, for a launcher (`Provider.worker_launcher()`). The core keeps the `WorkerLauncher` protocol and `LocalWorkerLauncher`; the proxy and dial-back launchers are the SMS plugin's (`vivarium_workbench_sms/env_workers.py`).
  - **The refusal rule.** The core knows only the names by which a deployment asks for hosted workers, `env_worker_launcher.HOSTED_WORKER_ENV` (`ENV_WORKER_PROXY_BASE`, `ENV_WORKER_ADVERTISE_HOST`, under either env prefix), and keeps them only to refuse, as P4 refuses an alias. When one is set and no provider supplies a launcher (providers off, plugin absent), the workbench refuses: `serve`, `api/__main__`, the commands in `cli.BACKEND_COMMANDS` and the provider commands with `Command.uses_backend` exit 2 at startup, and a pool call raises `BackendConfigError` (never cached, never `EnvWorkerUnavailable`, logged at ERROR once per pool). It never falls back to a local subprocess.
  - **Inside an env worker** generator discovery runs in-process (`composite_lookup.IN_WORKER_DISCOVER`, set by the worker's `main`): the worker is the environment, and a hosted worker pod has no plugin to launch a nested one with.
- **Features reach the browser by route (P3; Q-features decided).** The core's `GET /api/backend/features` (`{features}`, a registry read, no network call) and, per run, `remote_origin.features` on `/api/simulations` rows. They are never rendered into the page shell: workspaces keep the rendered shell on disk and publish it (`docs/dashboard/`). A snapshot bundle resolves to no features, with no request.
- **Wording is neutral (Jim, 2026-11-20).** No provider labels: core browser text and server text (startup lines, refusals, runner hints) say "remote backend" and the like, with providers on or off.
- **Models (P3 M1).** The SMS-only payload models are the plugin's (`vivarium_workbench_sms/models.py`), with the same class names, so the OpenAPI schema names do not change. `lib/generate_ts.py` emits the core's types, then each provider's `payload_types()`; `main()` refuses to write `domain.generated.d.ts` while providers are off, since the file would lack their sections.

### 2.3 Existing runs are routed by their record

This section is unchanged from step 1c. From P1c the backends behind the record kinds come from the provider registry; the kinds themselves (`records.KINDS`) stay a fixed list, the check that a record is well formed. A record whose kind no active provider serves (providers turned off) is refused with 409 `backend-not-installed` (`terminal: true`); it is never routed by its id's form.

- **New runs and existing runs are different questions (Jim, 2026-11-19).**
  - A *new* run goes to the one configured backend (`select` / `active`).
  - The detached deployment runner (`remote_run.run_remote`, P2 Runs b) asks `select` and calls that backend's `runs.run_composite`; a deployment run no backend selected (only the opt-in compose path, `ALLOW_COMPOSE_DISPATCH`) goes to `unrouted().unrouted.run_composite` (SMS's `/compose/v1`, as before). With providers off it is a `RuntimeError` in the run log; the Composites tab refuses that run at launch (409 `no-backend`).
  - **The composite card's explicit cloud run** (P2 Builds B2): a card request with `run_target: "deployment"` (the Environment picker's Cloud scope) that no backend selected goes to `unrouted().unrouted.dispatch_composite` (SMS: the selected build's image, as before). With providers off, or the plugin absent, there is no unrouted backend and the card answers 409 `no-backend`; nothing is sent. With nothing selected and no explicit target, the card asks `backends.active` (wb#1630: a named, unreachable backend is a 502) and otherwise runs locally.
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
    `{error, reason ("other-backend" | "ambiguous" | "not-offered"), phase, terminal: true, simulation_id, run, dispatched_to?}` (`"not-offered"` was `"not-sms"` before P3 I1; the browser accepts both until Z).
    Pollers treat it as terminal. The study page shows the reason and hides "Stop campaign".
  - **What callers send.**
    - The Runs tab sends each row's `deployment` and `study`.
    - The study page sends `study`.
    - `composite_run_views` passes this process's own deployment; its `remote-sim-<id>` runs are its own dispatches.
  - viva-core land refuses with **409 `no-backend`** when no backend is named, as compose-api always has.
  - **The trace (P2 Runs b2, `lib/remote_trace.py`).** `simulation_id`, then `composite_run_id`, each through its record: a refusal is returned as is; a compose-api run (record or id form) goes to `COMPOSE.runs.trace` under either key; a run whose record names a provider's backend goes to that backend's `runs.trace` **only for a lone `simulation_id`** (the Runs tab sends a viva-core row's id that way). That restriction is what keeps the answers identical to before b2, several-id 400s included. Everything else (no record, a `compose_id`, several ids, none) goes to `unrouted().unrouted.trace(ids)`. With providers off: no id or several is the old 400; one id no record or form routes is 409 `backend-not-installed` (`records.unowned`, the run views' body), for any of the three keys.
  - **Trace support.** compose-api named: the core's static answer (`get-simulation-trace-chrome`); otherwise the `unrouted` backend's `trace_support()`; with providers off (or the plugin absent) `supported: false, reason: "no-backend"`, with no request. `viewer` is always added last; `perfetto-open.js` hides the action whenever `supported` is false (it answers `no-backend` itself for the read-only bundle), so the browser is unchanged.
  - **Known gap.** Placeholder rows are `remote-pending-<id>`, not namespaced by deployment. If two deployments produce runs with the same id in one study, those runs share a placeholder row. A future fix is `remote-pending-<deployment>-<id>`.
  - **Not by record, by id form** (the code says why): the investigation batch job's `run_jobs.refresh_submitted`. The job lives only as long as this process, whose one backend dispatched every item, and the adapters' id forms do not overlap, so `backends.for_run` names the backend that ran each item. Each item goes to its owner's optional `runs.status_many` in one call when the owner has it (SMS: one `compose_status_batch`, in the plugin since step P2 Runs c2), otherwise through `runs.status` per item (viva-core, compose-api; a backend that does not answer is skipped for the rest of that refresh). **An id that no active backend owns stays `submitted` and is not asked**: with providers off, SMS is no longer asked about integer ids (a declared change, providers-off only). Caveat: a backend URL re-pointed while the job lives is asked anyway, where `for_existing` would refuse; the SMS path always had that exposure. (Before this, every item was asked of SMS, whose integer-only batch route raised on a viva-core or compose-api id, so such an item never settled.)
  - **Not by record, by facet presence:** the study gallery (`/api/study-remote-figures`) names no run, so the core asks every backend that declares `artifacts` (`backends.offering`, P2 Analyses and figures). SMS's gallery still checks the candidates it takes from the local index against their records (`remote_analysis_figures._this_backends_sms_run`), inside the plugin. A bare `analysis_id` poll names no run either and stays on `unrouted.status` (§2.1).
  - **Not by record, by facet presence: the remote-runs listing** (P2 Remote tab R1). `/api/simulations`, its `remote` state, the study cards' counts and the `?refresh=true` flush ask every backend that declares `listing` (`backends.offering("listing")`), never `active()`, `for_existing` or a profile: `active()` probes reachability, and the listing path must not block (#1106). Listed rows carry no record, so an operation on one is routed by its id form, as above for "runs listed live". Not gated on the configured backend: SMS is listed whether or not compose-api is named, as before.
  - **Exempt** (the code says why): `remote_run_jobs.run_remote_pipeline`, an SMS-only pipeline that polls the same client it dispatched with, inside the same call.
- **One remote backend per process (Jim, 2026-11-19).**
  - A named backend beside an alias (`VIVA_API_BASE` / `SMS_API_BASE`) that points elsewhere is refused at startup, by `backends.config.check_config` for `cli.BACKEND_COMMANDS` and the provider commands with `uses_backend` (`run-remote`), and by `serve_fastapi` and `api/__main__`.
  - In P1a the rule moved to `lib/backend_config.py`; in P3 C1 it split: the core checks the named URL (`backend_config.named_backend`), and each active provider checks its own settings against it (`Provider.check_config`; SMS: the aliases, `sms_api_client.check_aliases`). With providers off, or the plugin absent, a set alias is ignored with a start-up warning (`backends.config.stray_backend_env`); P4 refuses it.
  - The configured URL and the deployment name come from `lib/backends/config.py`: the named URL, else the first active provider's `base_url()` (SMS: an alias, else the tunnel), else none; the deployment name is `VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT`, else the named URL's host, else the provider's `deployment_default()` (SMS: `smsvpctest`), else `"default"`.
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
vivarium_workbench/lib/remote_study_map.py # core: the workspace's remote_run_study_map (load_rules, infer_study_slug;
                                           #   P2 Remote tab R1): workspace configuration, not a backend API
vivarium_workbench_sms/   # the plugin, from P1b (module names kept from lib/, so the move is mechanical)
    sms_api_client.py, remote_link.py, remote_pinned.py, remote_build_source.py, source_build_views.py,
    remote_simulations.py (sms-legacy's listing facet, SmsListing, since P2 Remote tab R1),
    remote_reconcile.py, remote_analysis_figures.py, remote_run_viva_v1.py,
    remote_run_jobs.py, sms_legacy.py, viva_core.py
    sms_landing.py     # the SMS store landing (P2 Runs c1): land_remote_run, land_remote_simulation_artifacts
    analyses.py        # sms-legacy's artifacts / analyses facets (P2 Analyses and figures)
    provider.py        # SmsProvider (P1c)
    routes.py, remote_pipeline_views.py   # SmsProvider.router() and its views (P2 Runs a2); only router() imports them
    cloud_dispatch.py  # the composite card's SMS cloud dispatch and preflight (P2 Builds B2)
    models.py          # its payload models, served to generate_ts by Provider.payload_types() (P3 M1)
```

**The registry loads lazily, and the move must not create an import cycle.**

- Today `backends/__init__.py` imports `sms_legacy` and `viva_core` at module level, and `sms_legacy` imports `backends.records` at module level. That works only because `sms_legacy` is a submodule, so `backends/__init__` always runs first.
- After the move, loading a plugin module first would hit a half-initialised `backends`. So P1b turns the adapter imports in `backends/__init__.py` into lazy lookups, in the same PR as the move. A module-level `__getattr__` returns the same cached `COMPOSE` / `VIVA_CORE` / `SMS_LEGACY` objects, because 29 references in 15 files (tests included) use them and `by_kind` compares identity.
- A test imports every plugin module first, each in a fresh interpreter.
- The other module-level imports from core into the plugin are in `api/app.py`, `remote_run`, `remote_run_compose`, `remote_run_views` and `study_runs` (`remote_link` too, until P1a splits it; `server_capabilities` until P2 Client). They stay legal under the ignore list and are checked by the same test.

**How the core finds the provider.**

- Until P4 there is no entry point. The registry imports `vivarium_workbench_sms.provider` lazily, by name: one `ignore_imports` edge, which lasts until P4.
- P3's core-only CI job runs the core with the plugin physically absent (`PROVIDERS=none` stays a local mode).
- P4 replaces the import with the entry-point group `vivarium_workbench.backends`, which is `pyproject.toml`'s first `entry-points` table. From then on, startup refuses when an SMS alias (`SMS_API_BASE` / `VIVA_API_BASE` / `VIVARIUM_WORKBENCH_REMOTE_PINNED`) is configured but no provider is installed. Those names stay listed in the core for that refusal.
- So a stale editable install can't hide the provider before P4.

**Guards (from P1b).**

- **An import-linter `forbidden` contract: `vivarium_workbench` must not import `vivarium_workbench_sms`.**
  - Neither package contains the other, so every core module is checked, including `publish.py` and `env_worker.py`.
  - Its `ignore_imports` lists the edges that are still real:
    - from the half-SMS core modules, until P2 splits them;
    - the registry's one provider import, until P4.
  - That list **is the ratchet**. Each later PR removes entries, and none may add one. An unmatched entry fails the check, so stale entries can't linger.
  - **As landed in P1b:** 38 edges, generated with grimp after the move (`pyproject.toml`, the contract named "the core does not import the SMS plugin"). The route ratchet's `ALLOWED` list holds the core's 13 remaining (module, method) pairs. **After P2 Runs (a1, a2, b, c1, c2):** 18 edges and 5 pairs. **After Builds B1:** 16 edges. **After Builds B2:** 14 edges (`composite_test_run_views -> remote_pinned` and `-> sms_api_client` gone), 4 pairs (`lib.composite_test_run_views: run_simulation` gone, and `run_simulation` left `PINNED_UNION`: no core caller), and no route literal left in the core (`ROUTE_LITERALS` is empty: the card's `/api/v1/simulations/discovery` moved). **After P2 Analyses and figures:** 13 edges and 3 pairs (`api.app -> remote_analysis_figures` gone; `lib.remote_run_views: run_analysis` gone). **After Builds B3:** 6 edges (−7: `api.app -> source_build_views`, `remote_run_views -> remote_pinned`, `remote_run_views -> sms_legacy`, `study_runs -> remote_pinned`, `study_runs -> sms_api_client`, `workspace_deps_views -> remote_build_source`, `workspace_deps_views -> sms_api_client`) and 3 pairs; `STILL_NEED_PLUGIN` holds only `lib.remote_trace` (`api.app` and `remote_run_views` import without the plugin). **After Runs b2:** 5 edges (`remote_trace -> sms_api_client` gone), 1 pair (`lib.remote_trace: compose_status, simulation_trace` gone, both names out of `PINNED_UNION`), and `STILL_NEED_PLUGIN` is empty. **After Remote tab R1:** 2 edges (`api.app`, `investigations_index` and `simulations_index` `-> remote_simulations` gone): the registry edge and `simulations_index -> sms_api_client` (R2's). **After Remote tab R2:** 1 edge, the registry's (`simulations_index -> sms_api_client` gone), and 0 pairs (`lib.simulations_index: download_data` gone, and `download_data` left `PINNED_UNION`): the core imports no plugin module except through the registry and calls no SMS method. **At the end of P2 (Routes Rt3):** the same, now final: 1 edge, 0 pairs, 0 route literals, `STILL_NEED_PLUGIN` empty.
- **The route-literal ratchet** (`tests/test_sms_route_ratchet.py`).
  - Its `ALLOWED` list becomes "SMS client methods and `/api/v1` / `/core/v1` literals outside the plugin". After P1b that is exactly the mixed core modules.
  - The list shrank with P2 and was empty when P2 ended; Routes Rt3 turned the ratchet into a ban (below).
- **The three end-state guards (end of P2, Routes Rt3).** Each is a ban; any hit is a regression, never a list entry.
  1. **Imports** (`tests/test_core_imports_without_plugin.py` and the import-linter contract). The contract's `ignore_imports` is the registry edge alone (`lib.backends.provider -> vivarium_workbench_sms.provider`, until P4's entry point). `STILL_NEED_PLUGIN` is empty and stays as a permanent guard; `MUST_IMPORT` is `env_worker`, `lib.study_runs` and `api.app` (`create_app()` with the plugin absent includes no provider routes). Probes run the core's no-plugin answers: run target, archive landing, CLI, refresh, deployment run, session build, card run, trace, listing, store fetch, and the composite-run status / Stop and the app's trace support.
  2. **SMS calls and routes** (`tests/test_sms_route_ratchet.py`). `ALLOWED`, `PINNED_UNION`, `PINNED_PAIRS` and `ROUTE_LITERALS` are deleted. `test_the_core_calls_no_sms_method` (`_sms_calls() == {}`) and `test_the_core_spells_no_sms_route` (`_route_literals() == {}`; the core spells no viva-api route at all, `/viva/v1` included: the old carve-out for viva-core is gone). `test_sms_methods_match_the_client` keeps `SMS_METHODS` honest, and `NOT_SMS_RECEIVERS` stays.
  3. **Provider backends by name** (`tests/test_core_names_no_provider_backend.py`, new). What grimp and the route ban cannot see: an AST scan of `vivarium_workbench/**.py` outside `lib/backends/` finds no `SMS_LEGACY` / `VIVA_CORE` attribute or import, no `getattr(backends, "SMS_LEGACY" / "VIVA_CORE")`, no `backends._ATTRS` / `_SENTINELS`, and no string constant `"sms-legacy"` / `"viva-core"` (docstrings skipped). The core asks a backend for a facet or routes by record. The registry vocabulary (`_ATTRS`, `named()`'s viva-core mapping, `records.KINDS`, `DEFAULT_IDENTITY_KIND`) stays in `lib/backends/` until P3/P4.
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
- **The plugin's router is included before the read-only filter and before the SPA catch-all** (`/{rel:path}`). Otherwise the catch-all route answers its GET requests first. `tests/test_backend_providers.py` pins the order, the guards on the moved routes, the read-only set and the clash refusals.
- **Contracts from P2** (`pyproject.toml`): the plugin does not import `lib.remote_run_views` (Runs a1; the reverse seam) and does not import `api.app` (Runs a2; `app → provider → routes → app` would be a cycle). Both are `forbidden` with `allow_indirect_imports`.
- **Final counts (end of P2, Routes Rt3):** 1 `ignore_imports` edge (the registry's); 0 SMS method references and 0 viva-api route literals in the core; `STILL_NEED_PLUGIN` empty; 0 provider-backend names outside `lib/backends/`.
- **End-of-P3 guards (Z).**
  - No SMS terms in core `static/` and `templates/` except `sms-launch.js` (F0's growth-only ratchet becomes a ban).
  - No `sms-api`, `GovCloud`, `SMS_API_BASE` or `smsvpctest` string constant in core `.py` (docstrings excluded), outside `lib/backends/` and `backend_config.BACKEND_BASE_ENV_VARS`.
  - The `plugin` test marker and the core-only CI job (K1): a test that needs the plugin is marked, and a marked test that passes without it fails (strict xfail). The job becomes a required check once it has been green a while (Jim, 2026-11-20).

### 2.5 What has to change

This list comes from greps and a grimp import graph on 2026-11-19, checked by two independent reviews. It is a starting list; each step's plan verifies its own part.

**Core seams (P1a; rows marked P1c need the provider; P1c's are done, and four moved to P3).** These are the places where core code uses SMS code today, and each is fixed in the core:

| Seam | Today | Change |
|---|---|---|
| compose-api path (P1a, done) | `remote_run_compose` reads `remote_pinned.remote_deployment_name` and `sms_api_client.explicit_backend_base_url` | reads `lib/backend_config.py`; no longer imports `remote_pinned` |
| capability probe (P1c, done) | `server_capabilities.explicit_backend_profile` → `backend_profile(SmsApiClient)`: `client.capabilities()` (`/viva/v1`, then `/core/v1`), then `openapi_operations()` for compose-api, all through the `remote_link` circuit breaker; `require_named_backend_reachable` and `_PROFILE_FAILURE_TTL` rely on that breaker | `backend_profile(client)` stays the one entry point; the provider's `recognise(client)` does the viva-core half (moved verbatim to `vivarium_workbench_sms/provider.py`); the core asks compose-api's OpenAPI exactly when the capability list lacks `viva-v1-composites`, as before. With providers off the profile starts reachable and legacy and only compose-api is detected |
| breaker (P1a, done) | `remote_link` imports `SmsApiError` and `sms_api_base` at module level; `CircuitOpen` subclasses `SmsApiError`; `snapshot()` reads the AWS SSO cache | split: the generic per-URL breaker and a core `BackendError` are in `backend_config.py`. **`SmsApiError` is an alias of `BackendError`, not a subclass** (a deliberate deviation): every `except SmsApiError` stays exact and still catches `CircuitOpen(BackendError)`. `remote_link` keeps the SMS default URL, its own `link()` (its `start_probe()` was deleted in P2: core startup probes `backend_config.start_probe(configured_base_url())`, the same URL), and the SSO expiry as the function `remote_link.snapshot(lk)` (the core breaker has no snapshot); they go to the plugin in P1b |
| health (P1a, done) | `workspace_deps_views.remote_health` pings with `SmsApiClient.ping()` | `backend_config.ping`, the same request (`GET /version`, 5 s default timeout, `Accept` and caller-identity headers). `SmsApiClient.ping` keeps its own copy until P1b, because tests steer it through `sms_api_client.urlopen`; a test pins that the two agree |
| records (P1a, done) | `records.py` uses `SmsApiClient` (`/viva/v1/health`), `_canonical_url`, `remote_pinned._DEFAULT_REMOTE_DEPLOYMENT`; `configured_url` / `_deployment_names_now` use `sms_api_base` and `BACKEND_BASE_ENV_VARS` | `Backend.identity` (viva-core and sms-legacy: `viva_core.viva_health_identity`; compose-api: `compose_api.compose_health_identity`, `deployment_id` on `GET /health`, compose-api#543). `records` asks the identity function of the record's own kind, and keys its caches by (identity family, URL), so a compose-api record never picks up viva-core's identity at a shared URL. URL canonicaliser, deployment name and configured URL come from `backend_config.py` |
| registry (P1c, done) | `backends/__init__.py` imports `SmsLegacyRuns`, `VivaCoreRuns` at load | `lib/backends/provider.providers()` imports `vivarium_workbench_sms.provider` (the one registry edge; P4: an entry point); `VIVA_CORE` / `SMS_LEGACY` / `_ALL` resolve through it, the same objects every time. `VIVARIUM_WORKBENCH_BACKEND_PROVIDERS=none` turns providers off: the attributes become inert sentinels (they own no run; `is` comparisons stay false) that no lookup returns. "Providers off" is not "plugin absent": the core cannot be imported without the package until P2 finishes. With providers off, a reachable named viva-api reads as a legacy backend and runs locally, by the existing "reachable legacy stays local" rule (P3 may add a startup warning) |
| deployment name (P1a, done) | `remote_pinned.remote_deployment_name()` (default `"smsvpctest"`) | `backend_config.deployment_name()`; `remote_pinned`'s function is a call-time wrapper. The default stays `"smsvpctest"` until P3 (moved from P1c), then neutral, with the provider supplying its own |
| session build and pin (P1c, done: `SmsProvider.select`; P2 Builds B1: `Provider.session_build`) | `run_core.run_target_for` and `remote_pinned.is_pinned_enabled()`, used by `backends.select`; `.viv-build.json` was also read by `report.py`, `provenance_manifest.py`, `composite_resolve`, `composite_state_views`, `env_worker_launcher` (moved to the plugin's `env_workers` by P2 Workers: the plugin is the stamp's only writer), `readouts_views` and `run_core` | `SmsProvider.select` and `SmsProvider.session_build` (`remote_pinned.session_build`) read the stamp; the six core readers ask `backends.session_build` and never name the file (B1). The core keeps only the stamp's git-dirtiness exclusion in `lib/remote_run.py` (bookkeeping). The pin's reader in `remote_run_views` is Builds B3 |
| generic helpers in SMS modules | ~~`remote_simulations._read_build_meta` (used by `composite_resolve`, `composite_state_views`)~~ plugin-private since P2 Builds B1 (the core asks `backends.session_build`), ~~`source_build_views._normalize_repo_url` (used by `git_status`)~~ moved to the core as `git_status.normalize_repo_url` (P2 cleanup) | move to a core module, or behind `builds` if SMS-only |

~~`remote_run_landing` is mixed.~~ Done (P2 Runs c1): the archive landing (`land_composite_results`, `fold_analyses`, `land_ptools_tsvs`, §3) is in core `remote_run_landing`, and compose-api and the plugin both call it; the SMS store landing (`land_remote_run`, which hardcodes the `"sms-legacy"` record and imports `remote_pinned` and `sms_api_base`, and `land_remote_simulation_artifacts`, `download_data`) is in `vivarium_workbench_sms/sms_landing.py`.

**Corrections from the P2 inventory (2026-11-19), applied in P1b:**
- `remote_trace` is mixed, not SMS-only: compose-api's trace goes through it. It stays in the core until P2. (P2 Runs b2: its viva-api half is the plugin's `sms_trace`; the core keeps the routing.)
- `comparison_pinning` has no production caller, and `publish.py`'s `smsApiBase` is read by nothing. Both were deleted in the P2 cleanup PR rather than kept (P1b moved `comparison_pinning` as-is).
- Target predicates also include `investigation_run_one_views`.
- Routes that reach SMS code and are not yet classified: `simulation-run-download`, `composite-run/{id}/artifact`, `composite-run/{id}` and `/state`, `investigation-run-unblocked-status`, `investigation-run-redrive`. P2's Routes area classifies them. (Done, Routes Rt3: the classification table below.)
- `ai_tools.py` hard-codes SMS route paths, `_run_auth_ok` reads the SMS pin, and `app.py` clears the listing module's private caches. These belong to P2's areas. (All done: `_run_auth_ok` moved to `sms_legacy` in Runs a1; the cache clear is `listing.invalidate`, Remote tab R1; `ai_tools`' two plugin paths are the SMS router's `ai_excluded`, Routes Rt2.)

**Corrections from the P2 Remote tab plan (2026-11-19):**
- `resolve_or_fetch_store`'s remote half is `artifacts` (R2's `fetch_store`), not `listing`.
- `remote_reconcile` has no core caller (only `remote_simulations` calls it); it is plugin-internal and rides behind `listing` unchanged. `_scope_build_ids` is plugin-private too (not in `investigations_index`).
- `publish.py` reaches the listing only through `build_simulations_data`; so do `analysis_tools._run_candidates` (the analysis-tool run picker, which builds the index *with* remote rows even when `/api/simulations` is asked with `include_remote=false`) and the plugin's own study gallery (`remote_analysis_figures`, which calls back into `simulations_index.build_simulations_data_cached`).
- Only the gate moved to the core: `_study_investigation_map` stays in the plugin (it globs `studies/` itself and opens files bare; a core version would go through `workspace_paths`).

**Route classification at the end of P2 (Routes Rt3).** The routes the corrections above left unclassified, and the others whose owner P2 settled. "Facet core" means the route stays in `api/app.py` and reaches the backend through a facet.

| Route | Class | Owner / state |
|---|---|---|
| `/api/composite-run/{id}`, `/state` | core | no SMS reach |
| `/api/composite-run/{id}/status` | core | the `remote-sim-` branch calls `remote_run_views.remote_run_status` (record routing, `unrouted`; with the plugin absent a terminal 409 `backend-not-installed`, pinned by a probe) |
| `/api/composite-run/{id}/stop` | core | no SMS call: a `remote-sim-` run answers `remote-uncancellable` (a detach); whether Stop asks `runs.cancel` is P3 (a behaviour change) |
| `/api/composite-run/{id}/artifact/{name}`, `/api/simulation-run-download` | facet core | `artifacts.fetch_store` through `simulations_index.resolve_or_fetch_store` (Remote tab R2) |
| `/api/investigation-run-unblocked-status`, `-redrive` | core | `run_jobs.refresh_submitted` asks each item's owner (`backends.for_run`; Runs c2) |
| `/api/study-run-baseline`, `-variant` | facet core | `builds.run_study` (Builds B3); a selected backend without `builds` runs documents (`study_runs._named_backend_runs_documents`, by facet since Rt3) |
| `/api/composite-test-run`, `/api/remote-dispatch-preflight` | facet core | `runs.dispatch_composite` / `dispatch_preflight` (Builds B2) |
| `/api/remote-run-config` | facet core | `builds.pin` (Builds B3) |
| `/api/source/builds`, `build-remote`, `switch-build` | plugin router | Builds B3 |
| `/api/remote-run-start`, `-status`, `-build`, `-pinned-build` | plugin router | Runs a2; `-start` and `-build` are the router's `ai_excluded` (Rt2) |
| `/api/remote-run-trace-support`, `-trace` | core lifecycle | by record, then `unrouted` (Runs b2); with the plugin absent support is `no-backend` (probe) |
| `/api/remote-run-land-artifacts`, `-analysis`, the three figure routes | facet core | `artifacts`, `analyses` (Analyses and figures) |
| `/api/source/remote-health` | core | `backend_config.ping` (P1a); the "sms-api" wording is P3 |

**The plugin set.** Whole modules move in P1b. The SMS parts of mixed modules are split out in P2, one area per PR. Until then their calls into the plugin are the contract's `ignore_imports`. The hosted env-worker pod ships the core without the plugin, so worker-reachable core modules must import without it; `tests/test_core_imports_without_plugin.py` pinned the shrinking list, which is empty since the end of P2 and stays empty (§2.4).

| Area | Whole modules (P1b) | SMS parts of mixed modules (P2) | Core sees it through |
|---|---|---|---|
| Client (P2, done) | `sms_api_client` (less the config split out) | the capability contract (`fetch_capabilities`, `require_capabilities`, `ServerCapabilities`, `CapabilityUnsupportedError`, the slugs) → `sms_api_client`; the core keeps the dispatch vocabulary, compose-api detection, the profile cache and the reachability rule | `Provider.probe_client`; providers off: `backend_config.ProbeClient` |
| Runs | `backends/sms_legacy`, `backends/viva_core`, `remote_run_viva_v1`, `remote_run_jobs` (correction: `remote_trace` did not move in P1b; it is core, `lib/remote_trace.py`, and was mixed until b2) | **a (a1 + a2, done):** the SMS parts of `remote_run_views` (constants, login check, the no-id lifecycle tails behind `unrouted`; the legacy pipeline and build-phase views and their four routes → `remote_pipeline_views` + `routes`, `Provider.router`), except `land_artifacts` / `analysis` / `_sms_only` (Analyses, done) and `_remote_run_config` (Builds). **b (done):** `remote_run.run_remote`'s SMS half → `vivarium_workbench_sms/deployment_run.py`, the core keeps the dispatcher. **c (done):** `remote_run_landing.land_remote_run` and `land_remote_simulation_artifacts` → `sms_landing` (c1); `run_jobs.refresh_submitted`'s SMS half → `sms_legacy.status_batch` (c2). ~~Deferred: `backend_environment` (Builds)~~ moved to `remote_run_viva_v1` in Builds B2 (its readers: `submit`, `VivaCoreRuns.dispatch_composite`, `deployment_run`; compose-api's `name:` form stays in `remote_run_compose`). **b2 (done):** the trace's viva-api half → `vivarium_workbench_sms/sms_trace.py` behind `runs.trace` and `unrouted.trace` / `trace_support`; the core keeps the routing, compose-api's answer, the event count and the viewer | `runs`, `unrouted`, `Provider.router` |
| Builds and pinning | `remote_pinned`, `remote_build_source`, `source_build_views` | ~~discovery in `composite_test_run_views`~~ done in B2: the card's cloud dispatch (`resolve_cloud_target`, `CloudTarget`, the image dispatch and its config pick, the opt-in compose path, `cloud_build_actions`) is `vivarium_workbench_sms/cloud_dispatch.py`, and the preflight's SMS branch went with it. **B3 (done):** the pin (`remote_run_views._remote_run_config` → `SmsLegacyBuilds.pin`), the vEcoli dispatch in `study_runs` (→ `SmsLegacyBuilds.run_study`), the build-source half of `workspace_deps_views` (`build_source_builds` → `source_build_views`; the `_sms_api_base` alias deleted, its users read `sms_api_client.sms_api_base`), the three build routes (→ the plugin router; `api.deps.bind_session` stays core). Nothing of this area is left in the core | `builds`, `Provider.select`, `Provider.session_build` (B1), `Provider.router`; the card and the preflight: `runs.dispatch_composite` / `dispatch_preflight` (B2) |
| Remote tab and store (P2, done) | `remote_simulations`, `remote_reconcile` | **R1 (done):** `simulations_index._append_remote_simulations` and `_build_simulations_data`'s `remote_state` read, `investigations_index._remote_study_run_stats`, `app.py`'s `?refresh=true` cache clear → `SmsListing`; the study-card gate (`remote_run_study_map`) → core `lib/remote_study_map.py`. **R2 (done):** `resolve_or_fetch_store`'s remote half: the download and the tar extraction → `sms_legacy.fetch_store_tail` behind `artifacts.fetch_store`; the core keeps the routing and the temporary directory | `listing` (by `backends.offering("listing")`), `artifacts` |
| Analyses and figures (P2, done) | `remote_analysis_figures` | `remote_run_views`' `_sms_only` (deleted: the core routes by record and facet presence; its integer-id check moved to the plugin), the re-run's SMS half and the land-artifacts guards → `vivarium_workbench_sms/analyses.py`; the three figure routes in `api/app.py` are thin delegations to `remote_run_views`. The store landing (`land_remote_simulation_artifacts`) went with Runs c1 | `artifacts`, `analyses`; `unrouted` for a request that names no run, `backends.offering` for the gallery; the routes stay core (§2.2) |
| Routes (P2, done) | — | `api/app.py`: no plugin import at all (module-level gone with Builds B3, the last lazy one with Remote tab R1: the refresh clear is `listing.invalidate`); `remote_run_jobs` went with Runs a2, `remote_analysis_figures` with Analyses, `source_build_views` with Builds B3; its read-only allowlist named SMS routes (Runs a2 moved `remote-run-start` / `-build`, Builds B3 `source/build-remote` / `switch-build`, to the plugin's declaration); the trace with Runs b2; `ai_tools`' plugin paths with Rt2 (`ProviderRouter.ai_excluded`). Rt3 closed the area: every route is classified (table above), `api.app` must import without the plugin, and the three end-state guards hold (§2.4) | facet routes stay core and dispatch by facet; SMS-only routes go to the plugin's `router()` (§2.2) |
| Workers (P2, wb#1668) | — | the launchers and the stamp read move whole (`ProxyWorkerLauncher`, `RemoteWorkerLauncher`, their workers, `.viv-build.json`'s `commit`) to `vivarium_workbench_sms/env_workers.py`; `env_worker_pool`, `env_worker_dialback`, `env_worker_client`: wording only | `Provider.worker_launcher` |
| CLI and startup (P2, done) | — | `run-remote` → `vivarium_workbench_sms/cli.py`; the startup breaker probe runs on core config (`backend_config.start_probe(configured_base_url())`); the startup checks in `cli`, `startup`, `api/__main__` are core (`backend_config`, `check_worker_config`) | `Provider.cli`; `check_config` is P3 (the one SMS fact left in the checks is the alias env-var names in `backend_config`, which may not ask providers) |
| Models and bundle | — | the SMS-only payload models (`RemoteJobStatus`, `RemoteRunStep`, `RemoteRunJob`, `RemoteRunStartRequest` / `Response`, `BuildRemoteRequest` / `Response`, `SwitchBuildRequest`, `SourceBuilds`) → `vivarium_workbench_sms/models.py` (P3 M1, done); `RemoteOrigin`, `RemoteSourceState`, `RemoteHealth` stay core (generic shapes; wording is C2). The bundle key is settled (`smsApiBase` deleted, wb#1670) | M1: `Provider.payload_types()` |
| Target predicates (P2, this area's PR) | — | none. `remote_pinned`'s four predicates were core wrappers: `resolve_run_target` moved to `backends`, the three `uses_*_dispatch` were deleted (no production caller). Callers: `investigation_steps`, `investigation_run_views`, `investigation_run_one_views`, `study_runs`, `composite_test_run_views`, `remote_run_views`, `api/app.py` | `backends.resolve_run_target` / `backends.active`; the SMS half is `Provider.select` (P1c) |
| To check in P2's plans | — | the vEcoli/ParCa case in `readouts_views` (keyed on `backends.session_build` since P2 Builds B1; the ParCa wording stays core model vocabulary); the `SMS_API_BASE` hint in `run_runner` (Runs b: stays, P3 wording); `run_runner._remote_analysis_options` (Analyses: stays core, a model convention like the vEcoli knobs, carried opaque through `run_composite`) and `RunRequest.build_ref` (since Builds B2: what a backend's `dispatch_composite` returned in `Detached`, carried unread to `run_composite`); vEcoli knobs (`n_seeds`, `n_generations`) in `preflight.py`, `env_worker.py`; P3 (opaque ids, from Remote tab R1): `_sim_recency_key`'s `int(simulation_id)` tiebreak (already guarded, so a sort-order question, not a crash) and the listing's run-id dedup | — |

**Features that are SMS-only today (P3).**

The UI shows these only when the run's backend has the matching feature. Checked against the routes and browser code on 2026-11-19.

| # | Feature | Where in the UI | Routes | Gated before P3 by | Facet / feature (P3 hides it by this) | Could compose-api offer it? |
|---|---|---|---|---|---|---|
| 1 | Cloud builds: build a branch on the deployment, list or switch builds | Source / branch picker: "Build on cloud", the Cloud toggle (`branch-source.js`, `workspace-picker.js`, `session.js`) | `/api/source/builds`, `build-remote`, `switch-build` | `isRemote` | `builds` | yes: its registry environments and versions are the analogue |
| 2 | Pinned deployment runs, and the relabelled run card | run card, configure-run dialog (`study-detail.js`, `walkthrough.js`, `configure-run.js`) | `/api/remote-run-config`; `remote-run-pinned-build` and `remote-run-build` (HTTP clients only, no browser caller) | `pinned: true` | `builds` (pin) | maybe: pin a registry version |
| 3 | Remote preflight with "Build on cloud" / "Switch build" actions | composite card's Run button (`composite-card.js`) | `/api/remote-dispatch-preflight` | remote run target | `runs` (`dispatch_preflight`, P2 Builds B2; the actions are the SMS answer) | with 1 |
| 4 | vEcoli launch parameters (generations, seeds, ParCa) and the campaign view | study page's remote-run panel (`study-detail.js`, `templates/study-detail.html`) | `/api/remote-run-submit` with the SMS body | deployment run target | not a facet: quarantined in `sms-launch.js`, feature `sms-launch` (Q-launch-ui) | no: model science. Better driven by the composite's own parameter schema |
| 5 | Runs on the server listed next to local runs | Runs table (`sim-table.js`, `remote-sim-<id>` rows) | merged into `/api/simulations` | the backend has `listing` (`backends.offering("listing")`, P2 Remote tab R1; none with providers off: no remote rows, `remote: null`) | `listing` | yes: it lists runs |
| 6 | Land a run's analysis files (`analyses.json`, PTools exports) | row action on a completed remote run (`sim-table.js`) | `/api/remote-run-land-artifacts` | any remote row with an id (also compose-api's); the server refuses a run whose backend has no `artifacts` facet (409 `not-offered`) | `artifacts` | yes: run results and datasets |
| 7 | Re-run analyses on a finished run | row action (`sim-table.js`) | `/api/remote-run-analysis` | as 6, by the `analyses` facet | `analyses` | needs an analysis job kind there |
| 8 | Analysis figures, per run and as a study gallery (also feeds the Viz / Analyses tabs) | figures window (`sim-table.js`), study tabs (`study-detail.js`) | `/api/remote-analysis-figures`, `remote-analysis-figure`, `study-remote-figures` | per-run: as 6; the study gallery asks every backend with an `artifacts` facet (`backends.offering`; none with providers off: an empty gallery) | `artifacts` | yes, with 6 |
| 9 | Explore a remote run's state | Loom embed (`loom-embed.js`: `run_target=deployment&build_sim=`) | `/api/composite-run/{id}/status` and `/stop` (SMS branch on `remote-sim-<int>`) | `remote-sim` rows | `builds`: the loom's Cloud scope is `VivEnv.isCloud()` (F3) | later |

**SMS assumptions outside any facet (P3).** A facet carries data, not UI and not id formats, so these need their own work:

- **Run ids are assumed to be SMS integers.**
  - The figure routes declare `simulation_id: int`.
  - `sim-table.js` sends `Number(simId)` for land-artifacts and re-run analysis.
  - `study-detail.js` and `perfetto-open.js` choose `simulation_id` or `run_id` by an all-digits test.
  - The composite-run status and stop routes match `remote-sim-<int>`.

  P3 makes run ids opaque strings end to end; the record's `kind` decides, never the id's form. This has to land before any facet can serve compose-api runs. Done in wb#1660; I1 finishes it (the `remote-sim-` prefix becomes one core constant, `not-sms` becomes `not-offered`).
- **The vEcoli launch panel** (feature 4) is SMS UI in core files. Decided: quarantine it in `static/sms-launch.js` behind a feature, as accepted debt (Q-launch-ui).
- **Gating is a server refusal, not a hidden button.** The UI shows features 6–8 on any remote row with an id; P3 hides them by declared feature (F1–F4).
- **SMS wording and plumbing:**
  - "sms-api" strings in `branch-source.js`, `study-detail.js`, `workspace-picker.js`, `loom-embed.js`, `session.js`, `progress-track.js`, `sim-table.js` and `index.html.j2`;
  - the sms-api models in `models.py`, which feed the generated TypeScript types: M1, `Provider.payload_types()`.

  P3 makes the wording neutral everywhere (Jim, 2026-11-20): F1–F4 in the browser, C2 on the server.
- ~~**To check in P3's plan:** the "remote workspace" Cloud toggle in `workspace-picker.js`.~~ Resolved: its workspaces come from `/api/source/builds`, so it is feature 1 (`builds`).

**Not SMS-only.** The run lifecycle on any remote run is core and shared with compose-api: poll, land, cancel, chain progress and trace (`sim-table.js`, `walkthrough.js`, `study-detail.js`, `perfetto-open.js`, `progress-track.js`). The backend health dot (`/api/source/remote-health`) stays in the core. Some things are SMS wording rather than SMS features: the "Cloud / Build via sms-api" help text in `index.html.j2`.

**What P3 changes.** Each feature is already conditional, but on a different signal each time. P3 gives them one source: the run's backend, or for new runs the active backend, declares its features, and the browser shows or hides features 1–9 by that declaration. With the plugin installed, the same controls show as today (in neutral wording). Without it, none of 1–9 appear.

### 2.6 Adding a feature to compose-api later

Because the facets and their routes are generic (§2.1, §2.2), giving compose-api one of the features above takes these steps, once P3 has made run ids opaque strings (§2.5):

1. Implement the facet in the core compose-api adapter (`backends/compose_api.py`).
2. Declare the feature.
3. If compose-api lacks a server route that the facet needs, ask compose-api for it.

After P3, adopting a facet needs no route, browser or plugin change. Two features need more first:
- the study figure gallery, which needs a generic `listing` to find a study's runs. The facet exists since P2 Remote tab R1, but SMS's gallery still finds its candidates its own way (`remote_analysis_figures` reads the local index and `list_remote_simulations`); moving it onto `listing` is not done;
- feature 4 (vEcoli parameters), which is meant to stay SMS-specific.

## 3. Contracts every step must keep

**Stanford keeps working.** Each step is one PR and leaves `main` working at both Stanford sites. Until P4 the plugin ships inside this distribution, so the Stanford image needs no change. At P4 the image must also install the plugin.

**PTools.** Pathway Tools reads simulation output in two places, and neither may break.

- **The pbg-ptools viewer** (the PTools card on the Analyses page) reads the on-disk contract `<study>/**/ptools/*.tsv`, which it fetches over HTTP from the workbench (`pbg_ptools.workbench_viewers`).
  - The workbench produces these files at landing: `remote_run_landing.land_ptools_tsvs` (core) copies `**/ptools/*.tsv` out of the run's archive into the study.
  - Its only caller that passes a study destination is the plugin's `sms_landing.land_remote_run` (P2 Runs c1): the only landing writer of `<study>/**/ptools/*.tsv` (`study_run_post` also writes them, for local runs). Whatever ends up in the plugin must still produce that layout, filenames included; `tests/test_remote_run_landing.py`'s golden test pins paths, bytes and the run id.
  - P2 Runs b moved the SMS deployment run into the plugin but left the tar.gz landing (`fold_analyses`, `land_ptools_tsvs`) in the core runner, untouched: the plugin only downloads the same `results.tar.gz`.
  - The study-less land-artifacts reaches SMS through the `artifacts` facet since P2 Analyses and figures; it still lands with `sms_landing.land_remote_simulation_artifacts`, which folds with the core's `fold_analyses` / `land_ptools_tsvs` into `.pbg/runs/<run_id>/` (never a study), unchanged.
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

   When P2 ends (it did, with Routes Rt3), only the registry's provider import is left on the ignore list, and the ratchets become bans (§2.4's three end-state guards):
   - `STILL_NEED_PLUGIN` is empty and stays a permanent guard; `api.app` joins `MUST_IMPORT`;
   - the route ratchet's `ALLOWED`, `PINNED_*` and `ROUTE_LITERALS` are deleted: the core references no SMS method and spells no viva-api route (`/viva/v1` included);
   - a new ban: no core module outside `lib/backends/` names a provider's backend (`SMS_LEGACY`, `VIVA_CORE`, `_ATTRS`, `_SENTINELS`, `"sms-legacy"`, `"viva-core"`).

   The models went to P3 (M1). This is the precondition for P3's core-only CI job.
   **Pulled forward, in parallel with P1 (Jim, 2026-11-19).** Two P3 items don't depend on the P1 chain and run as their own PRs now: opaque run ids end to end, and the launch-panel quarantine (`static/sms-launch.js`, gated by today's signals until P1c's declared features exist). A short parallel PR gives compose-api a server identity if compose-api reports one; it does (compose-api#543, `deployment_id` on `GET /health`), see the Progress row.
6. **P3: features, and a core without the plugin.** Opaque ids (wb#1660) and the launch-panel quarantine (wb#1659) are done. The rest is one PR each (Progress rows):
   - **P3-0:** the `Provider` additions (§2.2), with no core caller.
   - **F0:** the features seam (§2.1, §2.2). **F1–F4**, in parallel after F0: the Runs table, the study page, the source panel / workspace picker / `VivEnv`, the listing's second phase and loom host, the composite card's tips. Features only hide optional controls; dispatch never reads them.
   - **I1:** ids and wire literals (`REMOTE_RUN_PREFIX`, `not-offered`, the bare-id audit).
   - **C1:** config seams (`lib/backends/config.py`; the SMS aliases and tunnel default to the plugin). **C2:** neutral server wording and hints.
   - **M1:** the SMS-only payload models behind `Provider.payload_types()`.
   - **K1:** the core-only CI job, plugin physically absent, non-blocking. **K2a/b/c:** convert the tests (lazy plugin imports, `plugin` marks).
   - **Z:** the §2.4 bans; K3 makes the core-only job blocking, and then it becomes a required check.
   - ~~Move the SMS branches of mixed routes behind their facets~~: P2 did it.
   - **Correction:** the traversal sweep (`tests/test_traversal_sweep.py`) runs in both jobs; only the TS-types test and the full-schema OpenAPI goldens need the plugin.
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
- **Q-features, decided (P3 plan, 2026-11-20): a route plus a per-row annotation** (`GET /api/backend/features`, `remote_origin.features`), never rendered into the page shell (§2.2).

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
| `simulation_trace` | the plugin's `sms_trace` (core `remote_trace` until P2 Runs b2) | `/composites/{id}/trace` (the adapter already has `composite_run_trace`) | as above |
| `download_data` | `remote_run_views`, `simulations_index`, `remote_run_jobs`, `remote_run_landing` | `/composites/{id}/datasets` | as above |
| `list_build_simulations` | `remote_simulations` (Remote tab) | `GET /composites?environment_id=` | nothing |
| `compose_submit`, `compose_status[_batch]`, `download_compose_results` (`/viva/v1/compose`, interim) | the plugin's `deployment_run.run` (the core's `remote_run.run_remote` until P2 Runs b), `run_jobs`, the plugin's `sms_trace` (core `remote_trace` until P2 Runs b2), **and `remote_run_viva_v1.land`** (the viva-core adapter itself lands results through the interim route) | the viva-core document route (`create_composite_run`) | Stanford must advertise `viva-v1-composites-documents` (to verify) |
| `simulator_status`, `list_branch_builds`, `list_simulators`, `simulator_commit`, `upload_simulator`, `register_branch_head` (`/core/v1/simulator*`) | `remote_pinned`, `remote_build_source`, `source_build_views`, `remote_run_views`, `remote_run_jobs`, `remote_simulations` | `/viva/v1/environments` (W2 already goes there first; these are fallbacks) | both Stanford sites advertising `viva-v1-environments[-build]` (to verify) |
| `download_workspace` | `remote_build_source` | `/environments/{id}/source` | ~~nothing~~ the route is a 501 stub: ask D |
| `/api/v1/simulations/discovery` (raw `client._get`) | `composite_test_run_views` | a vEcoli-specific listing of `configs/*.json` and `ecoli/analysis/<category>/*.py`, done over SSH on the HPC checkout or through the GitHub Contents API. Replacement: the environment's read-back manifest, extended by the SMS package (Q-config, settled) | ask D |
| `run_analysis`, `analysis_status`, `list_analyses` | `remote_run_views`, `remote_analysis_figures` | analyses requested **at submit** already travel as `analysis_options` in the params. Re-running analyses on a finished run has no core route | Eran (Q2) |
| **Direct S3 reads, outside any API**: `remote_analysis_figures` reads analysis figures with boto3 (GovCloud credentials) from the `s3://` `result_uri` that `list_analyses` returns | `api/app.py` (3 routes) | `/composites/{id}/datasets` (or an artifact route) so the backend owns storage access | Q2. The workbench must not hold object-store credentials for a backend |
| `composite_resolve` (`/core/v1/simulator/{id}/composite-resolve`) | `composite_resolve` | none; the route has never existed (verified above) | nothing: deleted in step 0 |
| env workers (`/env-worker/v1/*`, sent as `/viva/v1/workers` when the server advertises `viva-v1-workers`) | `vivarium_workbench_sms.env_workers` | the SMS plugin (`env_workers`), P2 Workers | — |

#### A.3.1 What depends on hosted env workers

Hosted workers matter in one situation: a **hosted** workbench, which has no local venv, such as the Stanford pod.
There, `env_worker_launcher.default_launcher()` gets `ProxyWorkerLauncher` (when `ENV_WORKER_PROXY_BASE` is set; it
wins over `ENV_WORKER_ADVERTISE_HOST`) or `RemoteWorkerLauncher` (when only `ENV_WORKER_ADVERTISE_HOST` is set) from
the SMS provider. Both call viva-api's env-worker surface, which the client spells `/env-worker/v1` and sends as
`/viva/v1/workers` when the server advertises `viva-v1-workers` (both Stanford sites do, so prod requests
`/viva/v1/workers`).
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
     (`remote_run_viva_v1.compose_simulation_id`, which refuses a `job_id` that is not `compose:<n>`) and downloads the
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
  - Jim, 2026-11-18: SMS's simulation pipeline needs no hosted env workers; only the hosted workbench uses them
    (both Stanford sites proxy them through `http://api:8000`). So the `workers` facet, and viva-api's `/viva/v1/workers`, exist only for
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
