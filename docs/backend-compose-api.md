# Backend contract: compose-api (what the workbench sends, and to whom)

Goal: `vivarium-workbench serve --backend-base-url https://compose.cam.uchc.edu/` runs a workspace's studies on
[compose-api](https://github.com/biosimulations/compose-api), in the environment compose-api has registered for the
workspace. The UI and VivaChat stay local; only dispatch, status, results, traces and cancel cross the wire.
compose-api replaces viva-api, which is being archived. The viva-api routes ([backend-viva-v1.md](backend-viva-v1.md))
stay in place and are reached only when the named backend advertises `/viva/v1`.

## Provenance (read, not recalled)

| Claim | Source |
|---|---|
| Routes, parameters, schemas | compose-api 0.7.35's OpenAPI, the `openapi.json` shipped in the locked `compose-api-client` (git tag `0.7.35`); snapshot at `tests/_contracts/compose_api_openapi_0.7.35.json`, which a test keeps equal to the shipped one. It equals compose.cam.uchc.edu's live `/openapi.json` (0.7.36 on 2026-10-22, whose spec is unchanged from 0.7.35) except that file uploads read `format: binary` instead of `contentMediaType`, compose-api's deliberate rewrite for its client generator (`compose_api/api/openapi_spec.py`) |
| Environments, registry entries, the pilot's interface manifest | `GET /registry/environments`, `/registry/entries`, `/registry/environments/viva-pde-particle/manifest` on compose.cam.uchc.edu 0.7.33, 2026-10-22 (`tests/_fixtures/compose_api/`) |
| What a run's archive holds | a real export run by viva-pde-particle's real runner in its own image, and production simulation 4706 (`tests/_fixtures/compose_api/README.md`) |
| Address rules | compose-api `compose_api/registry/validation.py`: only `local:<module>.<Class>`; `local:!` and other protocols are always refused |
| Cancel | compose-api `DELETE /simulations/{simulation_id}` (`cancel-simulation`, biosimulations/compose-api#346) |

## Detection

compose-api has no capabilities route. `server_capabilities.backend_profile` reads `GET /openapi.json` when the
backend has no `/viva/v1` composites surface. The dispatch is `compose-api` when every operation in
`COMPOSE_OPERATIONS` is there:
- `run-simulation`
- `get-simulation-status`
- `get-simulation-results-file`
- `list-environments`
- `get-environment-manifest`
- `list-registry-entries`

Operation ids are compose-api's stable public contract (pbest's client methods are named for them), so the check
is membership, never version. Startup prints `backend dispatch: compose-api (server v<version>)`. With no backend
named, nothing is probed and runs stay local.

## Which environment runs the workspace

The code is not shipped. The run uses an image compose-api already lists:
1. `VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT=name:<environment>`, when set, names it outright.
2. Otherwise the workspace's git `origin` is matched to a registry entry's `source` (`GET /registry/entries`), and
   that entry's prebuilt environment (`GET /registry/environments`, `kind: prebuilt`) is the one. Its `simulator`
   becomes the run's `simulator=`.
3. No match is a **409** naming the registration steps (below).

The run uses the image, not the checkout. When the entry pins a `commit` and HEAD is another commit, the submit
answers with a `warning`. A landed run records the image digest as its `code_version.image`.

## The document

`remote_run_compose.export_document`:
- Exports the composite with `address_form="registry"`, so every class is `local:<module>.<qualname>`
  (`pbg_export.ADDRESS_FORMS`).
- Adds one RAM emitter, `user_emitter`, recording what a local run of the study records
  (`study_runs.study_emit_paths`: the study's observables, else the composite's declared emitters).
- With nothing to record, it requires the composite's own single emitter, and is otherwise a 422.

Numpy values export as the lists bigraph-schema realizes them from. Anything JSON cannot represent is refused
rather than written as its `str`. Only the registry form is this strict. The import form (viva-api, SMS) writes
`str` as it always has.

**Preflight against the live manifest.** Each address, in string or `{protocol, data}` form (read as compose-api's
`registry/validation.py` reads them), is checked before anything is sent: for a prebuilt image, compose-api itself
does not look classes up.
- A class under the environment's module roots must be in `GET /registry/environments/{name}/manifest`.
- An engine class (`process_bigraph.*`) is resolved by every process-bigraph core.
- Anything else is a **409** listing each address.
- An environment with no interface manifest is a **409** (`no-manifest`).

**Steps.** compose-api runs `interval_time` from 0 to 1000. A run asking for more is a **400** (`bad-steps`), not a
shorter run.

## Routes

| Workbench | compose-api |
|---|---|
| submit (study baseline and variant, `/api/remote-run-submit`) | `POST /simulation/run` with the `.pbg` as `uploaded_file`. Query: `simulator=<env>`, `interval_time=<n_steps>` (a local run calls `composite.run(1)` per step), `tags=workbench,study:<study>,workbench:dispatch:<uuid>`, `description` |
| Composites-tab remote run (`/api/composite-test-run`, the detached `run_remote`) | the same submit (tag `composite:<id>`), then the runner waits on the status, downloads the results and lands them as the card's run. It records the card's emit selection, else the composite's declared emitters; declared analyses are not run, and the run log says so |
| preflight (`/api/remote-dispatch-preflight`) | the environment lookup alone: ready when one runs the workspace, otherwise its reason |
| poll (`/api/remote-run-poll`) | `GET /results/simulation/status` (`submitting` until SLURM has the job) |
| progress (`/api/remote-run-chain-progress`) | the same status, as a single job with no seeds |
| land (`/api/remote-run-land`) | `GET /results/simulation/results/file`; its `results_*.pber` is read, landing `user_emitter` (`remote_run_landing.land_composite_results`) |
| cancel (`/api/remote-run-cancel`) | `DELETE /simulations/{id}`, which needs a signed-in caller: set `COMPOSE_API_TOKEN`, as compose-api's CLI reads it. A 401 says so |
| trace (`/api/remote-run-trace?simulation_id=compose-<id>`, or `composite_run_id=`) | `GET /results/simulation/trace/chrome` |

Run ids are `compose-<simulation id>`. compose-api's ids are integers, which the other routes read as legacy
simulation ids, so the compose check runs before them.

**Failures.**
- `502 unreachable` means only that compose-api could not be reached. Any other failed answer is
  `502 backend_error`, `401` (no `COMPOSE_API_TOKEN`) or `403`.
- A compose id with no backend named is a **409** (`no-backend`).
- A submission is never retried, because a retry could run twice. When its answer is lost (`UncertainOutcomeError`),
  the run is looked up by its `workbench:dispatch:<uuid>` tag (5 tries, 3 s apart). If none is found, the answer is
  a **502** with `may_exist` and that tag.
- A run id compose-api does not have is a **404** (`not_found`), which stops the progress poll. The status route's
  404 alone cannot say this, because it also means "SLURM has no job yet".

**Not on compose-api:** the SMS-only flows (pinned and switched builds, `/api/v1/analyses`, chain campaigns).

## Onboarding a workspace

Two pieces, by pattern (viva-pde-particle is the reference):
1. **The workspace's repository:** a `compose.Dockerfile`, a workflow that publishes the image, and a runner that
   honours the job contract `run <doc> -o <output> -n <interval>` and writes `results_<date>.pber`
   (`gather_emitter_results` as JSON). See viva-pde-particle's `docker/compose.Dockerfile`,
   `.github/workflows/compose-image.yml` and `viva_pde_particle/compose_runner.py`.
2. **compose-api:** a `manifest.yaml` entry (level `tested`, `bundles: [<env>]`, `source`, `commit`, evidence), an
   `environments.yaml` entry, the image digest in `PREBUILT_SIMULATORS`, and the CI-generated interface manifest.

## Tests

`tests/test_backend_dispatch_compose.py`:
- **Real artifacts:** address form, the manifest check and landing run against `tests/_fixtures/compose_api/`.
- **Transport:** a real workbench server against `VivaV1Stub` generated from the snapshot, including multipart
  validation of the upload.
- What neither proves: that a deployment runs the document. Only a live run against compose-api does.
