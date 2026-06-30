# Run a pbg-template Workspace on sms-api — Design

**Date:** 2026-06-29
**Status:** Design — approved by user; proceeding to plan + implementation
**Repos:** `sms-api`, `pbg-template`, `vivarium-dashboard` (all in scope)
**Feature branch (all repos):** `feat/run-workspace-on-sms-api`

## Goal

Let a workspace scaffolded from `pbg-template` run its composite on **sms-api** the
way v2ecoli does — submit, run remotely on HPC/Batch, retrieve results — using
sms-api's **generic compose `.pbg` path**, and **without the `pbest` dependency**.

The recently-merged template `Singularity.def.j2` (pbg-template #22) is *not* the
mechanism — it deploys a workspace dashboard on HPC and is orthogonal to sms-api.
This feature is the actual on-ramp.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| On-ramp | Generic compose `.pbg` path (not the vEcoli-specific simulator backend) |
| Repo scope | All three repos are fair game |
| `pbest` | **Removed entirely** from sms-api; sms-api owns the def template + runner |
| Submission granularity | One named composite per submission (CLI takes a composite id; works for any composite) |
| Process-code delivery | Workspace is pip-installable from git; processes installed via `extra_pip_deps=["git+<repo>@<sha>"]` |
| Address form in the `.pbg` | Full import-path addresses (`local:!pbg_<slug>.module.Class`) so a bare container resolves them with no registry/`build_core` |

## Grounded current state

**sms-api compose backend** (verified):
- `POST /compose/v1/simulation/run` — multipart `uploaded_file` (`.pbg`/`.omex`/`.sbml`), `interval_time`, `batch_submission` → returns `ComposeSimulationExperiment{simulation_database_id,…}` (`api/routers/compose.py:119-143`).
- `GET /compose/v1/simulation/{id}/status` → `ComposeHpcRun` (`:151-163`); `GET /compose/v1/simulation/{id}/results` → `results.zip` SCP'd from HPC (`:177-215`).
- The build seam: `run_compose_simulation` calls `generate_container_def_file(...)` (pbest) → `ContainerizationFileRepr`, then `_inject_pip_deps()` appends pip installs before `## Execute`, then the def is hashed (`get_singularity_hash`), cached, built to `.sif`, run (`compose/handlers.py:107-125`).
- `extra_pip_deps` exists in `run_compose_simulation` but is **only** set by curated handlers (`run_compose_v2ecoli` hardcodes `extra_pip_deps=["git+https://github.com/vivarium-collective/v2ecoli.git"]` + an `override_command`); it is **not** exposed on the generic `/run` endpoint.

**`pbest` reality** (read from installed `pbest==0.5.5`):
- Dependency *inference* is commented out (`container_constructor.py:60-62`); it installs a **hardcoded** list (copasi/tellurium/numpy/scipy/matplotlib/`pb_multiscale_actin`/readdy — `get_experiment_deps():24-50`).
- Its container `ENTRYPOINT`s `/runtime/pbest/main.py` — pbest is also the *runner* (`generic_container.jinja:28`). v2ecoli's `override_command` exists to replace that entrypoint.
- sms-api's only uses of pbest: `generate_container_def_file()` (1 call) and the DTO `ContainerizationFileRepr` (`{representation: str}`, used in `handlers.py`, `models.py`, `tables_orm.py`, `database_service.py`).

**Workspace/template side** (verified):
- A composite serializes to a `.pbg` today via `CompositeSpec.to_document()` / `Composite.serialize_state()` / `Composite.save()` (`process-bigraph/process_bigraph/composite_spec.py:207`, `composite.py:1803,1841`).
- Process addresses: `local:Name` (registry short-name, needs `build_core()`) or `local:!module.path.Class` (direct `importlib`) — dispatch in `bigraph-schema/bigraph_schema/protocols.py:23,38,46-55`.
- Template `pbg_<slug>` is **not** pip-installable: `pyproject.toml.j2` sets `bypass-selection = true` ("research workspace, not a package"); processes register only via `build_core()` (`template/template-init.sh:136-146`), not entry-points.
- `vivarium-dashboard` already has `SmsApiClient` (`lib/sms_api_client.py`) and a remote-build/composite-resolve track (SP-D) — reuse the client, don't duplicate.

## Architecture

Three components; the contract between them is the generic `/run` endpoint + the
`.pbg` document format.

### Component ① — sms-api: remove pbest, own the def + runner, expose deps

1. **Inline the DTO.** New `sms_api/compose/container_def.py` defining
   `ContainerizationFileRepr` (`{representation: str}`, a small pydantic model).
   Swap the 4 `from pbest.utils.input_types import ContainerizationFileRepr`
   imports (`handlers.py`, `models.py`, `tables_orm.py`, `database_service.py`)
   to this module.
2. **Own the def template.** In `container_def.py`, `build_pbg_def(pbg_filename, interval_time, extra_pip_deps) -> ContainerizationFileRepr` renders a Singularity `.def` (string/Jinja) that:
   - `Bootstrap: docker` from a pinned `python:3.12-slim` (or uv image),
   - installs `process-bigraph`, `bigraph-schema`, and the default emitter,
   - installs each `extra_pip_deps` entry (the workspace `git+<repo>@<sha>` + any extras),
   - copies in the `.pbg` and the runner, and sets `%runscript` to run the runner.
   This replaces the `generate_container_def_file(...)` call at `handlers.py:111-118`.
   `_inject_pip_deps` is retained (it already operates on the def string) **or**
   folded into `build_pbg_def` (deps passed directly) — folding is cleaner.
3. **Own the runner.** Ship `sms_api/compose/run_pbg.py` (copied into the image):
   loads the `.pbg`, builds a `Composite` against a bare core (full-path addresses
   resolve via `importlib`), runs `interval_time`, and writes outputs to the path
   sms-api collects as `results.zip`. **The results-write contract is the first
   spike** (where/what the HPC run must write — read from the existing collection
   code, `compose/hpc_utils.py` + `get_results`).
4. **Expose deps on the generic endpoint.** Add optional `extra_pip_deps: list[str]`
   (and `override_command: str | None`, kept for parity/escape-hatch) as form/query
   params on `submit_simulation` (`api/routers/compose.py:126-143`), threaded into
   `run_compose_simulation`. Additive, backward-compatible.
5. **Drop the dependency.** Remove `pbest==0.5.5` from `pyproject.toml:66`, its mypy
   override (`:140`), `uv lock`. Regenerate the API client (`make api_client`) — its
   `ContainerizationFileRepr` copy is standalone (no pbest import).
6. Migrate `run_compose_v2ecoli` onto `build_pbg_def` (it already passes
   `extra_pip_deps` + `override_command`), so nothing else imports pbest.

### Component ② — pbg-template: make `pbg_<slug>` installable

- In `pyproject.toml.j2`, declare the package explicitly:
  `[tool.hatch.build.targets.wheel] packages = ["pbg_<slug>"]`, drop
  `bypass-selection = true`, keep a real `name`/`version`. Goal: `pip install
  "git+<repo>@<sha>"` installs the workspace's process code **and** local editable
  installs keep working (dashboard editable-install must be unaffected).
- Document the run-on-sms-api flow in `NEXT_STEPS.md.j2`.

### Component ③ — vivarium-dashboard: export + submit client + CLI

- **Export helper** (`lib/`): build the named composite via the workspace's
  `build_core()` + `CompositeSpec`, serialize via `to_document()`, **rewrite
  addresses** `local:Name` → `local:!<module>.<qualname>` (look up each class in the
  built core's registry; leave external/package addresses untouched; fail loudly if
  a process isn't in an importable module), ensure an **emitter** is present so
  results are captured, write `<composite>.pbg`.
- **Client**: extend `SmsApiClient` with `submit_compose(pbg, interval_time,
  extra_pip_deps)`, `get_status(id)`, `download_results(id, dest)`.
- **CLI**: `vivarium-dashboard run-remote <composite> [--interval N]` — export →
  derive `git+<origin>@<HEAD-sha>` → submit → poll → download `results.zip` → land
  in the workspace (e.g. `out/` or a runs dir). Refuse if the tree is dirty or HEAD
  is unpushed (the container would install wrong/missing code).

## Data flow

```
workspace composite
  → build_core() + CompositeSpec.to_document()
  → rewrite addresses to local:!… + ensure emitter
  → <name>.pbg
  → POST /compose/v1/simulation/run  (file=<name>.pbg, interval_time,
                                       extra_pip_deps=["git+<repo>@<sha>"])
  → sms-api build_pbg_def(): Singularity def installs process-bigraph + workspace pkg
  → hash → cache → apptainer build .sif → run run_pbg.py for interval_time
  → results written → results.zip on HPC
  → client polls GET /{id}/status → GET /{id}/results → land results.zip
```

## Error handling / compatibility

- Dirty/unpushed git tree → CLI refuses to submit (clear message).
- Unresolvable `local:Name` (process in `__main__`/non-importable module) → export
  fails listing the offending process.
- sms-api `FAILED` status → surface `error_message` from `ComposeHpcRun`.
- Removing pbest is backward-compatible: the DB column stays a string; the generated
  client regenerates; `run_compose_v2ecoli` keeps working via `build_pbg_def`.
- `extra_pip_deps`/`override_command` are additive optional params → existing callers
  unaffected.

## Risks & spikes (front-loaded in the plan)

1. **Results-write contract (highest risk).** What must the in-container run write,
   and where, for `get_results` to return a populated `results.zip`? Resolve by
   reading `compose/hpc_utils.py` + `get_results` before writing `run_pbg.py`.
   `override_command` is retained as the escape hatch if the default runscript path
   is insufficient.
2. **Package-installability flip.** Flipping `bypass-selection` must not break local
   editable installs / the dashboard. Verify with a real `pip install
   "git+file://…@<sha>"` of a scaffolded workspace into a clean venv (imports a
   process) AND a local `uv sync` + dashboard editable install.
3. **End-to-end is infra-gated.** The true submit→HPC→results loop needs the
   GovCloud tunnel (flaky under the harness). Unit/integration tests run locally;
   the live E2E is a documented manual gate.

## Testing

- **sms-api**: unit-test `build_pbg_def` (renders a valid def; installs the right
  deps; no pbest import anywhere — `grep` gate); `run_pbg.py` runs a trivial `.pbg`
  locally and writes the expected results layout; endpoint accepts `extra_pip_deps`;
  `make check` (mypy/deptry) passes with pbest gone.
- **pbg-template**: scaffold a throwaway workspace → `pip install "git+file://…"`
  into a clean venv → import a process (proves installability); template parity tests
  still pass.
- **vivarium-dashboard**: unit-test address-rewrite (short→full-path) against a
  fixture core; `.pbg` export reloads as a valid `Composite`; client submit/poll
  against a mocked endpoint.
- **Live E2E (manual gate):** submit a trivial workspace composite to a live sms-api,
  poll, download a populated `results.zip`.

## Out of scope (later)

- Batch / multi-composite submission.
- Ripping `override_command` out of sms-api (retained as escape hatch).
- Private-repo `git+` installs needing container-side credentials.
- Convergence with the SP-D whole-workspace remote-build track (reuses `SmsApiClient`
  but is a separate flow).
