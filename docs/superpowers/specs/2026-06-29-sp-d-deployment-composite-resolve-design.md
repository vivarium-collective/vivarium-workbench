# Deployment Composite Resolve (SP-D1) — Design

**Date:** 2026-06-29
**Status:** Design (brainstormed + grounded in an sms-api/dashboard survey; approved by user)
**Author:** Eran Agmon (with Claude)
**Builds on:** SP-A's deployment seam (`run_core.run_target_for` → `"deployment"`, `RunTargetUnavailable`)
and the WS3 `.viv-build.json` remote-build marker — both in `main`.

## 1. Context & goal

A remote-sourced dashboard (a materialized sms-api build, marked by `.viv-build.json`) can browse a
build's committed composites, but the **Composite Explorer can't resolve generator composites** (the
v2ecoli WCM ones) because resolving runs `build_core` + ParCa **locally**, and those build artifacts
aren't in the repo tarball. The user hit this twice; it's the core remaining gap of the remote
experience.

A survey confirmed **sms-api has no composite capability at all** — its endpoints are full
*simulation* runs (build→parca→run), discovery, observables, and workspace export. So fixing the
remote Explorer requires a **new sms-api feature**, not just wiring an existing one.

**Goal (SP-D1):** resolve a composite *inside a build's environment* on the deployment, so the
Composite Explorer works on a remote build (shows the wiring diagram + the config form). This is the
first, smallest cross-repo slice of SP-D; composite **run** on the deployment is SP-D2 (follow-up).

### Approved decisions
1. **Resolve-only first** (composite-run on the deployment is SP-D2).
2. **Execution = a short job from the build's Docker image** — reuse the same image/job infra sms-api
   already uses for simulations; no new long-lived services. (Rejected: baking a resolve HTTP server
   into the image; resolving in the sms-api process — which would need every build's deps.)

## 2. Architecture — two pieces, one per repo, over the existing tunnel

```
Composite Explorer (remote build)
  → dashboard composite-resolve handler detects .viv-build.json
  → SmsApiClient.composite_resolve(simulator_id, composite_ref, overrides)
  → [tunnel] sms-api POST /core/v1/simulator/{id}/composite-resolve
  → sms-api runs a SHORT JOB from build {id}'s Docker image:
        resolve entrypoint: build_core(composite_ref, overrides) → resolved composite → JSON on stdout
  → endpoint returns the JSON
  → dashboard maps it to the explorer's resolve shape (parameters/state/...)
  → Explorer renders wiring + config form
```

### Piece A — sms-api: composite-resolve endpoint
- New route `POST /core/v1/simulator/{simulator_id}/composite-resolve`, body
  `{composite_ref: str, overrides: dict}`. (Under `/core/.../simulator/...` to match the simulator
  namespace; the internal ALB already routes `/core`.)
- It launches a **short, synchronous job from build `{simulator_id}`'s image** with a **resolve
  entrypoint**: a small command in the build/workspace env that imports the workspace package, calls
  `build_core(composite_ref, overrides)` (the process-bigraph resolver the workspace ships), and
  prints the resolved composite as JSON (the `state`/`parameters`/`default_n_steps`/`module`/`kind`
  fields the dashboard's local `/api/composite-resolve` produces). The endpoint captures stdout and
  returns the JSON (or a JSON error).
- The job mechanism reuses sms-api's existing image-run path (the same images it runs sims from); the
  exact invocation (a short job that captures stdout vs the async sim-job pattern) is settled in
  planning against `simulation_service*`. The resolve is fast, so it returns synchronously (a bounded
  timeout → a JSON timeout error, never a hang).

### Piece B — dashboard: route the deployment target's resolve
- `SmsApiClient.composite_resolve(simulator_id, composite_ref, overrides) -> dict` (stdlib urllib,
  per-call timeout — resolves can take seconds; reuse the WS3 timeout pattern).
- The composite-resolve handler (`lib/composite_state_views` / the `/api/composite-resolve` route)
  gains a **remote-build branch**: when `run_core.run_target_for(ws) == "deployment"`, read the
  build's `simulator_id` from `.viv-build.json` and dispatch to `SmsApiClient.composite_resolve`
  instead of local `build_core`; map the result into the explorer's expected shape. Local workspaces
  are unchanged.

## 3. Data flow / shape
The deployment resolve returns the **same JSON shape** the local resolve does (so the explorer and
its config-form generation — SP-C — work unchanged): at least `{name, id, module, kind, parameters:
{name: {type, default, description}}, state, default_n_steps}`. The SP-D1 contract is "the deployment
resolve is shape-compatible with the local resolve."

## 4. Error handling
| Case | Behavior |
|---|---|
| Resolve job fails (unresolvable ref / build_core error) | sms-api returns a JSON `{error}`; the dashboard's existing defensive parse (the explorer fix) renders it inline |
| Build image missing / job infra error | sms-api 5xx JSON; dashboard shows "couldn't resolve on the deployment" |
| Tunnel down / SSO expired | `SmsApiError` (network/401) → the dashboard shows the reachability message (WS-style) |
| Resolve exceeds the timeout | bounded → JSON timeout error, never a hung request |

## 5. Components (each independently testable)
- **sms-api:** the resolve route + a `resolve-in-build-image` runner (reusing the image/job infra) +
  the resolve entrypoint/command in the build env. Unit-test the route + JSON mapping with a **fake
  job runner**; the real container exec needs a deployed sms-api.
- **dashboard:** `SmsApiClient.composite_resolve` + the composite-resolve handler's remote-build
  dispatch. Unit-test the dispatch + shape-mapping with a **fake SmsApiClient**.

## 6. Testing
- **sms-api (headless):** the route returns the runner's JSON; a fake runner returns a sample
  resolved-composite dict; error/timeout paths return JSON errors. No real container.
- **dashboard (headless):** on a `.viv-build.json` workspace, the resolve handler calls
  `SmsApiClient.composite_resolve` (faked) and maps its result to the explorer shape; on a local
  workspace it still calls local `build_core`. Fakes only.
- **Live E2E (IN-THE-LOOP — requires a *deployed* sms-api with this endpoint + the tunnel):** open a
  remote build's Composite Explorer for a generator composite (e.g. v2ecoli WCM baseline) → it
  resolves via the deployment → wiring diagram + config form render. Cannot be validated headless.

## 7. Scope boundaries
**In:** sms-api composite-resolve endpoint (short job from the build image) + dashboard remote-build
resolve dispatch + the live E2E (in-the-loop).
**Out:** SP-D2 (composite **run** on the deployment — the Configure & Run widget's deployment run
path; until then a remote-build ad-hoc run still hits the 409 seam). No new long-lived sms-api
services; no change to the local resolve path; no caching of deployment resolves (a follow-up if
latency hurts).

## 8. Realism / build split (READ FIRST)
- **Headless-doable now:** the sms-api route + JSON-mapping logic + the dashboard dispatch + the
  resolve entrypoint command — all unit-tested with fakes.
- **In-the-loop ONLY:** wiring the real container/job execution and validating end-to-end requires
  **deploying the updated sms-api** (a real deployment step on your side) + the tunnel — like WS1's
  cutover. The headless work prepares everything; the deploy + live validation is collaborative.

## 9. Open questions (resolve in planning)
1. **Job mechanism** — the exact sms-api primitive for "run a short command in build `{id}`'s image
   and capture stdout JSON" (a one-off job vs reusing a sim-job runner with a resolve command).
   Settle against `simulation_service_ray.py` / `simulation_service.py` during planning.
2. **Resolve entrypoint location** — a small CLI shipped in the workspace/build (`run-composite
   resolve …`?) vs a `python -c` invoking the resolver. Prefer reusing an existing workspace CLI if
   one resolves composites.
3. **Auth/SSO for the job** — the resolve job runs with the api pod's existing role (the WS-noted
   `batch-submit` SA already has what sim jobs use); confirm no extra grant needed.
