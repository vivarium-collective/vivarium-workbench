# Backend contract: compose-api (what the workbench sends, and to whom)

Goal: `vivarium-workbench serve --backend-base-url https://compose.cam.uchc.edu/` runs a workspace's studies on
[compose-api](https://github.com/biosimulations/compose-api), in the environment compose-api has registered for the
workspace. The UI and VivaChat stay local; only dispatch, status, results, traces and cancel cross the wire.
viva-api is not retired: the Stanford workbenches still run on it, and its removal waits on Jim
([compose-api #345](https://github.com/biosimulations/compose-api/pull/345#issuecomment-6802333949), 2026-11-11). The
viva-api routes ([backend-viva-v1.md](backend-viva-v1.md)) stay in place and are reached only when the named backend
advertises `/viva/v1`.

## Provenance (read, not recalled)

| Claim | Source |
|---|---|
| Routes, parameters, schemas | compose-api 0.7.100's OpenAPI, the `openapi.json` shipped in the locked `compose-api-client` (PyPI `compose-api-client` 0.7.100); snapshot at `tests/_contracts/compose_api_openapi_0.7.100.json`, which a test keeps equal to the shipped one. It equals compose.cam.uchc.edu's live `/openapi.json` (0.7.100 on 2026-11-24) except that file uploads read `format: binary` instead of `contentMediaType`, compose-api's deliberate rewrite for its client generator (`compose_api/api/openapi_spec.py`) |
| Signing in as the CLI's user | compose-api-client 0.7.100, `compose_api_client.auth.signed_in_session` and `profiles.py` (biosimulations/compose-api#561) |
| Environments, registry entries, the pilot's interface manifest | `GET /registry/environments`, `/registry/entries`, `/registry/environments/viva-pde-particle/manifest` on compose.cam.uchc.edu 0.7.33, 2026-10-22 (`tests/_fixtures/compose_api/`; the live registry has since moved, see that README) |
| What a run's archive holds | a real export run by viva-pde-particle's real runner in its own image, and production simulation 4706 (`tests/_fixtures/compose_api/README.md`) |
| What a stopped run keeps | compose-api 0.7.108 (biosimulations/compose-api#581): the results file written every few minutes and on SIGTERM, listed as a `results` dataset; production simulation 12966, cancelled (`tests/_fixtures/compose_api/README.md`) |
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

## Signing in

compose-api answers most calls from anyone. Cancelling a run, and the first run of a new combination of registry
entries (compose-api builds its environment), need a signed-in caller, and the registry listings show a private
repository's entries and environments only to the holders of its role. Every call the workbench makes carries the
first credential that applies (`remote_run_compose._session`):

1. **`COMPOSE_API_TOKEN`**, when set and not blank: a bearer token sent to the named backend, as before. It comes
   first, as in compose-api's own CLI: a token given to this process (CI, a service account, a token for another
   deployment) is never overridden by a login stored on the machine, and while it is set the credential store is
   not read.
2. **The compose-api CLI's sign-in**, with the `compose-api-signin` extra
   (`pip install 'vivarium-workbench[compose-api-signin]'`; it adds compose-api-client's `[auth]` dependencies). Each
   person signs in as themselves, once, with `compose-api auth login` (the CLI is compose-api-client's `[cli]` extra,
   installed on its own, e.g. `uv tool install 'compose-api-client[cli]'`), and the workbench calls compose-api as that
   person: the client's `signed_in_session` reads the login from the OS credential store and renews it as the CLI
   does. The profile is the CLI's default (`production`) or the one `COMPOSE_API_CLI_PROFILE` names, and its login
   is sent only when that profile's `api_base_url` is the backend the workbench names (`--backend-base-url`). The
   workbench never picks a profile by URL: for another deployment, set `COMPOSE_API_CLI_PROFILE` to a profile for it
   (`compose-api config show` prints where profiles are configured) and sign in to that profile.
3. **None**: the call is anonymous, as before.

**Signing in from the page** (`lib/compose_signin.py`, `static/compose-signin.js`). With the extra, a person does not
need the CLI to sign in. Where the readiness check reports that runs would go to compose-api unsigned (its
`signed_in` is false; a workspace that names a prebuilt environment gets no such report) and a sign-in here would sign
them in, each composite card shows **Sign in** beside its **Runs: Cloud** badge. The badge itself stays Cloud, since
compose-api allows anonymous runs. The sign-in's instructions show in a panel in the page's corner (a click dismisses
it), and the badges are checked again once it ends. The GitHub panel's Account section also has a **compose-api** row. **Sign in** makes the login
`compose-api auth login` makes, on the same profile and in the same credential store, so the CLI and the workbench share
it and either can sign in or out. The client opens this computer's browser, or shows a code to enter on any device over
SSH or without a display. **Use a code instead** asks for the code directly, for a workbench on another machine. The
page shows the client's own instructions while it waits, up to five minutes. **Signed in · <profile>** signs out after
a confirm, for the CLI too. **Sign in again** makes a new login. The client keeps the current one if that sign-in is
cancelled or fails before Google returns a new login; once it does, the new login replaces the old. While a sign-in
waits, the status does not read the stored login: a renewal during the wait would make the client discard the new
sign-in. A renewal by the CLI or another program during the wait still does, and the page then says so.
- **Routes:** `POST /api/auth/compose-api/start` (`{method: auto | browser | device}`), `GET …/status`, and
  `POST …/logout`.
- **Tokens:** they go from Auth0 to the credential store inside the client and never through these routes.
- **Guards:** the POSTs are CSRF-guarded, not in the read-only allow-list, and excluded from the chat's tools.
- **When the row is hidden:** where a sign-in here would not sign the calls in. That means no backend named,
  `COMPOSE_API_TOKEN` set, or a CLI profile for another API.
- **Linux:** a Linux host needs a running Secret Service (GNOME Keyring or KWallet), as the CLI does. The client keeps
  no file fallback.
- **Known wart:** the client's browser callback page still says to return to the terminal.

Without the extra, only `COMPOSE_API_TOKEN` signs a call in, as before. Install the extra where the workbench runs;
the Composites tab's remote runs run on the workspace's interpreter (`env_resolver.resolve_run_interpreter`), so
install it there too when that is another environment.

**A signed-in run is private.** The workbench sends no `visibility`, so compose-api applies its default, as its CLI
does: an anonymous run is public, and a signed-in caller's run is private to them (and admins). Status, results and
trace of a private run answer only its owner: another person's workbench, or this one once it calls as someone else
or anonymously (a token for another identity, the login gone), gets a 404 for it, which ends the run panel's polling.
**Making a run public.** Its owner (or an admin) can, from the Runs table: a compose-api run's actions have
**🔓 Make public** (Share), which asks first and then calls `PATCH /simulations/{id}` through
`/api/remote-run-visibility`. The other runs of every comparison it is in become public with it, and the toast says
how many. Making it private again is the CLI's: `compose-api simulations visibility <id> private` (the route takes
`private` too). Someone who is not the owner gets a 403, and so does a run submitted without signing in: it has no owner and is
already public (the client keeps no detail on a 403, so the answer names both causes). An anonymous workbench gets a
401 that says how to sign in.

What the answers say:
- A call compose-api refuses for want of a signed-in caller is a **401** that says which credential applied and what
  to do: the token was rejected; sign in, or again, with `compose-api --profile <name> auth login` (no login stored,
  or it expired or was refused); the profile is for another API than the named backend (both are named); or, without
  the extra, install it.
- A sign-in this host cannot set up is a **409** (`sign-in-setup`) with the client's words, and nothing is sent. With
  the extra, any CLI configuration the client refuses does this for every backend, since the profile is loaded before
  it is matched: an unknown profile, an unknown `COMPOSE_API_CLI_*` variable, a config file others can write. For the
  backend of the CLI's profile, so does a credential store that cannot be read (a locked keychain is never taken for
  "signed out"), and a `COMPOSE_API_TOKEN` that is set but blank (the CLI's rule; without the extra, or for another
  API's profile, a blank token is ignored as before).
- Renewing a CLI login that is about to expire asks Auth0 first. When Auth0 cannot be reached, the answer is the
  `502 unreachable` of an unreachable compose-api, but its text is the client's, naming Auth0.
- The preflight's `signed_in` says whether the workbench's calls carry a credential.

## Which environment runs the workspace

The code is not shipped. `GET /registry/entries` and `GET /registry/environments` decide where the run goes:
1. `VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT=name:<environment>`, when set, names a prebuilt environment outright.
   The core reads this form itself (`remote_run_compose.resolve_environment`); viva-core's reading of the same
   variable (a bare name or `id:<env id>`) is the SMS plugin's since step P2 Builds B2 (docs/backend-viva-v1.md).
2. Otherwise the workspace's git `origin` is matched to a registry entry's `source` (an entry pinned by commit links
   `…/tree/<commit>`, which names the same repository). If that entry has a prebuilt environment
   (`kind: prebuilt`, in the entry's `bundles`), its name becomes the run's `environment=`, so its default version runs.
3. If the entry has none, the run names **no environment**, and compose-api infers one from the classes the document
   names: a ready environment that covers them, or else a `mash-up` it builds from the registry entries for a
   signed-in caller and caches by its entry set (`docs/plan-environment-composition.md` in compose-api, D7.1 to D7.7).
   `GET /registry/entries` does not say whether an entry can be composed, so compose-api decides and its refusal is
   shown as it gives it: a **409** (`refused`) carrying its message and each violation, whether it answered 400 or 422
   (a document it will not take) or 409 (a lock pixi cannot solve, or an environment already failed for this set of
   entries). A **429** (it builds at most two new environments at a time) reads as `rate limited`. The first run of a new combination needs
   a signed-in caller ([Signing in](#signing-in); an anonymous one is a **401** that says so) and waits for the build;
   later runs reuse it. The preflight answers `reason: inferred`, with the entry, its level, and whether the
   workbench's calls are signed in (`signed_in`).
4. No registry entry has the repository: the same as 3, with no entry. compose-api admits a document by its classes,
   each under its own registry entry, not by the repository it came from. So a workspace that composes other entries'
   classes, as viva-multiscale-nfkb composes viva-tellurium's, viva-maboss's, viva-pde-particle's and
   viva-basic-processes', needs no entry of its own. Each class runs its own entry's pinned code, not the checkout.
   compose-api refuses a class no entry provides, in its own words, except when it sends the run to a prebuilt image
   that covers the document's other classes: it does not look up a prebuilt image's own classes, so such a class
   fails there at import instead. The preflight answers `reason: inferred` with `registry_entry: null`. A workspace
   whose own classes should run on compose-api still needs an entry, added by a compose-api pull request (below). A
   workspace with no git `origin` is run the same way.

The run uses the environment, not the checkout: the prebuilt image, or the environment built from the pinned commits
of the registry entries its classes belong to. When the entry pins a `commit` and HEAD is another commit, the submit answers with a `warning`. A landed run records the environment version it ran in (`compose.environment_version`, such
as `viva-pde-particle@2`, read from the run's own record) and that version's image digest as `code_version.image`.
A run from before versions has no version, and its image is resolved from the simulator name.
A study run keeps that record in its params. A Composites-tab run's params are the composite's inputs, which a re-run
sends back, so it keeps the same record in its manifest (`compose.*`, and `code_version.image`).

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
- An inferred environment may not exist yet, so there is no manifest to read: only the address form (`local:` and no
  `!`) is checked here, and compose-api's own check of the classes is shown if it refuses.

**Steps.** How long a run may be is compose-api's to say, per caller: up to its `max_interval_time` (1,000 on the
production deployment) for anyone, and up to `max_long_run_interval_time` (86,400 there) for holders of its
`long_run_role` (`long-runs` there). The workbench refuses only a step count that is not a positive number (a
**400**, `bad-steps`) and sends any other as asked. compose-api's own refusal reaches the person in its words: to
someone without the role it names the limit and the role ("compose-api refused the run: Invalid interval time, it has
to be between 0 and 1000; up to 86400 with the long-runs role"), to someone with it only the longer limit. On a
composite card the refusal comes once the detached run has sent the request, as that run's error. The card waits for
a run for 2 h plus 2 s per step (`remote_run_compose.wait_timeout`), longer than compose-api's own job limit for that
run (30 min per 1,000 steps on production), so a long run is not marked failed while compose-api still runs it. While
it polls a run's status (the client's own wait, in the open, poll by poll), and while it downloads the results, a
credential store that refuses (the client's `StorageError`: a session another process holds past the client's 30 s, or
a locked keychain) is retried with backoff. A poll that succeeds ends a streak of refusals; a streak ends the run once it
has lasted 10 minutes from its first refusal (`remote_run_compose.STORE_RETRY_SECONDS`) or reaches the run's deadline.
The run then fails with the store's reason and names the run on compose-api, which may finish it all the same. Every
`StorageError` is treated so, by class, not by its text: one that can never clear (a damaged stored record, say) fails
the run only after that bound. A card run that failed this way cannot be landed again
from the workbench (`/api/remote-run-land` lands into a study); its results are on compose-api under that run id.

## Routes

| Workbench | compose-api |
|---|---|
| submit (study baseline and variant, `/api/remote-run-submit`) | `POST /simulation/run` with the `.pbg` as `uploaded_file`. Query: `environment=<env>` (omitted when compose-api infers it), `interval_time=<n_steps>` (a local run calls `composite.run(1)` per step), `tags=workbench,study:<study>,workbench:dispatch:<uuid>`, `description` |
| Composites-tab remote run (`/api/composite-test-run`, the detached `run_remote` -> `runs.run_composite`) | before launching, the card asks the preflight's environment lookup (`check_environment`, with a 15 s timeout) and refuses the launch with the preflight's reason (409 `unknown-environment` for a named environment compose-api does not list; 502, 401 or 403 when compose-api cannot be asked), with no run row and nothing spawned. Otherwise the same submit (tag `composite:<id>`), then the runner waits on the status, downloads the results and lands them as the card's run. A run that stopped (cancelled, timed out, out of memory, failed) and kept results ([Stopped runs](#stopped-runs)) lands them the same way and is recorded `cancelled` (a cancel) or `failed` (any other stop) with the steps landed. It records the card's emit selection, else the composite's declared emitters; declared analyses are not run, and the run log says so |
| preflight (`/api/remote-dispatch-preflight`) | the environment lookup alone: ready when one runs the workspace, otherwise its reason (the card's run-target badge then reads `Runs: Cloud · not ready ⚠`, with the reason in its tooltip) |
| poll (`/api/remote-run-poll`) | `GET /results/simulation/status` (`submitting` until SLURM has the job) |
| progress (`/api/remote-run-chain-progress`) | the same status, as a single job with no seeds |
| land (`/api/remote-run-land`) | `GET /results/simulation/results/file`; its `results_*.pber` is read, landing `user_emitter` (`remote_run_landing.land_composite_results`). A stopped run: its `results` dataset ([Stopped runs](#stopped-runs)), answered with `partial: true` and the status; a 409 while the run has not finished, or when it stopped and kept nothing |
| cancel (`/api/remote-run-cancel`) | `DELETE /simulations/{id}`, which needs a signed-in caller ([Signing in](#signing-in)). A 401 says so |
| visibility (`/api/remote-run-visibility`, `{simulation_id, visibility}`) | `PATCH /simulations/{id}` (`ComposeApiRuns.change_visibility`, the optional `runs` method; the run feature `visibility`), which only the run's owner or an admin may call: 401 not signed in, 403 not the owner, 404 not readable, 409 compared with runs this caller may not change (compose-api's words), 400 a value other than `public` / `private` (nothing sent). Not in the read-only allow-list, and excluded from the chat's tools (`lib/ai_tools.EXCLUDED_PATHS`) |
| trace (`/api/remote-run-trace?simulation_id=compose-<id>`, or `composite_run_id=`) | `GET /results/simulation/trace/chrome` (`ComposeApiRuns.trace`; the core routes a compose run there by its record or id form, under either key, with or without providers) |
| trace support (`/api/remote-run-trace-support`) | no request: when compose-api is the named backend the core answers `supported: true, capability: "get-simulation-trace-chrome"` itself (compose-api has no capabilities route); otherwise the `unrouted` backend answers, or `no-backend` with providers off (P2 Runs b2) |

### Stopped runs

compose-api keeps `results.zip` only for a completed run. From 0.7.108 a run's runner also writes its results file
every few minutes and on SIGTERM, and announces it as a dataset of kind `results`. A run that stopped is read from
there: `GET /datasets?simulation_id=<id>&kind=results`, then `GET /datasets/{id}/content`
(`remote_run_compose._partial_results`), zipped in the archive's shape and landed as a completed run's is. Nothing on
the dataset marks it as partial, so the run's status does: the card's run is recorded `cancelled` or `failed`, and its
log says `stopped (<status>) at t=<last time landed> of <requested>; the results up to the last snapshot are landed: Runs, ⬇ Raw data`.
A run in an older environment version, or one whose node was lost, lists no such dataset, and a results directory
(`*.zarr`, `*.fenics`) is not read here: those fail as before, naming the status.

The card shows no Results panel for a stopped run. Its status route answers `stopped: {rows, last_time}` when the run's
history kept rows (a `cancelled` or `failed` run), and the card then shows the completed run's header marked as a stop,
`■ stopped (<status>) · results up to t=<last time> · Download ZIP · open in Runs`, with the log line above under it. A
stopped run that kept nothing, and any other failure, shows `✗ <status>` and its log as before. The landed rows are also
reached through ⬇ Raw data in the Runs table, and `/api/composite-run/<id>/download` serves the run's directory (the
landed archive and its log) for a `cancelled` run as for a `failed` one. Drawing the landed rows as a chart is a change of
its own: a completed remote run shows none either.

Run ids are `compose-<simulation id>`. compose-api's ids are integers, which the other routes read as legacy
simulation ids, so the compose check runs before them.

**Failures.**
- `502 unreachable` means only that compose-api could not be reached. Any other failed answer is
  `502 backend_error`, `401` (not signed in, [Signing in](#signing-in)) or `403`. A sign-in this host cannot set up is
  a `409` (`sign-in-setup`).
- compose-api refusing the run (a `400` or `422` whose `detail` lists each address and why, or a `409` conflict) is a
  **409** (`refused`) whose text carries compose-api's message and every violation. The client's own message names only
  the operation and the status, so the text is read from `detail`.
- A compose id with no backend named is a **409** (`no-backend`).
- A submission is never retried, because a retry could run twice. When its answer is lost (`UncertainOutcomeError`),
  the run is looked up by its `workbench:dispatch:<uuid>` tag (5 tries, 3 s apart). If none is found, the answer is
  a **502** with `may_exist` and that tag.
- A run id compose-api does not have is a **404** (`not_found`), which stops the progress poll. The status route's
  404 alone cannot say this, because it also means "SLURM has no job yet".

**Not on compose-api:** the SMS-only flows (pinned and switched builds, `/api/v1/analyses`, chain campaigns). A
workbench talks to one remote backend: naming compose-api beside an `SMS_API_BASE` / `VIVA_API_BASE` alias that points
elsewhere is refused at startup (`backends.config.check_config`, which asks each active provider's `check_config`;
docs/backend-adapters.md steps 1c and P3 C1). With providers off the alias is ignored with a startup warning.

## Onboarding a workspace

As compose-api's maintainer described it on 2026-10-29 (biosimulations/compose-api#353; the design is its
`docs/plan-environment-composition.md`):
1. **A `manifest.yaml` entry in compose-api, reviewed by PR:** `source`, `install` (git, commit and extras, or a PyPI
   pin), `conda`, `requires`, `module_roots` and compliance cases. The manifest is the allow-list: what is trusted
   is a commit, not a repository. `PREBUILT_SIMULATORS` no longer exists.
2. **A prebuilt environment, which this workbench uses when the entry has one.** A workspace that publishes its own image
   (a `compose.Dockerfile`, a workflow that publishes it, and a runner that honours `run <doc> -o <output> -n
   <interval>` and writes `results_<date>.pber`; viva-pde-particle's `docker/compose.Dockerfile`,
   `.github/workflows/compose-image.yml` and `viva_pde_particle/compose_runner.py` are the reference) needs:
   - **Once, by compose-api PR:** the environment declared in `environments.yaml` (`kind: prebuilt`). The version
     route resolves its `name` only through a declared environment or an existing mash-up, and answers 404
     `no environment` otherwise (`compose_api/api/routers/registry.py`, `create_environment_version`).
   - **Per image, by an administrator, through the API:** `POST /registry/environments/{name}/versions` with
     `{"image": "<ref>@sha256:…"}`. The new version is not the default. Runs that name the environment, as this
     workbench does (`environment=<name>`), get it only after `PUT /registry/environments/{name}/default`.
   Later images of an already declared environment need no PR, config change or redeploy (compose-api's maintainer
   registered viva-pde-particle's version 2 that way on 2026-10-27).
3. **Or no image at all.** When no ready environment covers the classes a document names, compose-api composes a
   `mash-up` from the registry entries (cached by its entry set) for a signed-in caller, and writes its own runner
   into the image. This workbench uses it: a workspace whose entry has no prebuilt environment is submitted with no
   `environment=` (see "Which environment runs the workspace"). So a workspace whose own classes should run needs
   one reviewed `manifest.yaml` entry for them and nothing else, and compose-api runs them only when that entry is at
   level `tested` or higher (the header of compose-api's `registry/manifest.yaml`). A workspace that only composes
   registered classes needs no entry.

## Tests

`tests/test_backend_dispatch_compose.py`:
- **Real artifacts:** address form, the manifest check and landing run against `tests/_fixtures/compose_api/`.
- **Transport:** a real workbench server against `VivaV1Stub` generated from the snapshot, including multipart
  validation of the upload.
- **Inferred environments** (an entry no prebuilt environment bundles) run against production's registry as captured
  from 0.7.76 on 2026-10-30 (`entries_0.7.76.json`, `environments_0.7.76.json`); the prebuilt path is pinned against
  the same capture. `VIVARIUM_WORKBENCH_LIVE_COMPOSE=<base URL>` adds one read-only test (two GETs) against a live registry.
- **Signing in** runs in-process against the same stub: a real CLI profile in the CLI's own config file under the
  test's HOME, and a real login committed through the client's `AuthSession`, with its on-disk session state there
  too; only the OS keychain is replaced (by a dict of records). The stub records the Authorization header each call
  carried. Without the extra is tested by blocking the import of `compose_api_client.auth`, as an install without the
  client's `[auth]` dependencies does. A login renewed through Auth0 is the client's own tested behaviour, not
  repeated here.
- **Stopped runs** run against production simulation 12966's real listing and results file (cancelled): the card's run
  and a study land. A timed-out run is tested with the same file, an assumed shape: no real timed-out run's file has
  been captured. That `_partial_results` reads 12966 itself from production was checked by hand when this landed
  (the same bytes as the fixture, 1,721 rows, t = 0 to 1720).
- What neither proves: that a deployment runs the document, or builds the environment. Only a live run against
  compose-api does.
