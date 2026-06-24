# FastAPI Server Cutover — Complete the Strangler-Fig Migration

**Date:** 2026-06-24
**Status:** Design (approved in brainstorming; spec under review)
**Branch / worktree:** `feat/fastapi-server-cutover` @ `/Users/eranagmon/code/vdash-cutover`

## Goal

Retire the 16,780-line stdlib `BaseHTTPRequestHandler` in `vivarium_dashboard/server.py` and make FastAPI (served by uvicorn) the dashboard's only server — completing Jim Schaff's Python-first direction. The migration is **behavior-preserving** and **invisible to users until a single flip**.

## Background / current state

- **Server today:** `server.py` is one `Handler(BaseHTTPRequestHandler)` (stdlib `http.server`, run via a threading server), dispatching ~66 routes through a hand-written `if self.path == ... / startswith(...)` chain. No FastAPI in the live path.
- **Strangler seed already on `main`:** `vivarium_dashboard/api/app.py` (FastAPI) serves **6 routes** — `/health`, `/api/simulations`, `/api/config`, `/api/iset-list`, `/api/data-sources`, `/api/references-bib` — by calling pure builders in `lib/`. It is **test-validated only, not in the live serving path**.
- **Established pattern (from PRs #296/#299/#301/#305):** extract a route's pure builder into `lib/<name>.py` (ws_root-parameterized; workspace-coupled deps injected as callables) → `server.py` keeps a thin delegating shim (call-sites/tests unchanged) → FastAPI route returns a typed pydantic `response_model`. A parity test asserts `server shim == lib builder`.
- **Types:** `lib/models.py` holds pydantic payload models; `lib/generate_ts.py` emits `static/types/domain.generated.d.ts` from them (pure Python, no npm) with a CI staleness test. `mypy --strict` runs on a narrow scope (`models.py`, `api/app.py`, the extracted `lib/` modules) with `follow_imports="skip"` to dodge numpy's stub when importing `server.py`.
- **In-flight stack (review-blocked, → not yet on `main`):** #305 (`/api/saved-visualizations`), #310 (composites / investigations / catalog / registry — stacked on #305), #311 (the Source & Branch panel + Simulations DB work, includes `store_path` on `SimRow`). These add more read-only routes and must land before this work stacks on `main`.

## Constraints

- **Python-first, AI-free.** pydantic / FastAPI / mypy / uvicorn / sse-starlette are allowed (not AI deps). No JS→TS migration, no app bundler, no npm in the runtime path. The dashboard ships plain JS that browsers run directly.
- **Behavior-preserving.** Every ported route produces byte-identical (or test-equal) output to the stdlib handler. Parity tests enforce this during the transition.
- **Single-active workspace preserved.** One process-global active workspace (today a module global `WORKSPACE`, re-pointed wholesale by the in-process source-switch), one branch-derived current investigation, all investigations listed via `iter_iset_dirs`. Local workspaces with local repos and worktree+source-switch all keep working. True multi-active (multiple workspaces/investigations simultaneously) is **out of scope** (see Out of Scope); the architecture must not *preclude* it but will not build it.
- **Incremental and reviewable.** Each increment is a small PR. The flip itself is tiny because everything is ported first.
- **Work in worktrees.** The shared `/code/vivarium-dashboard` main checkout is used by other sessions — never branch-switch it.

## Decisions (locked in brainstorming)

1. **Cutover model: parallel, then one flip.** Keep porting into the standalone FastAPI app (not live) until it covers all routes + static + SSE + state, then one PR switches `cli serve` to uvicorn and deletes the handler.
2. **Extraction discipline: extract to `lib/` as we go.** Each ported route fully moves its builder into `lib/` so `server.py` is emptied route-by-route — no "FastAPI lazily calls `server.py`" shims survive to flip time.
3. **Workspace scope: preserve single-active.** Single process-global active workspace, held in `app.state` post-flip instead of a module global. Per-request scoping stays a clean future add (lib is already `ws_root`-parameterized; `get_workspace` is a `Depends`).
4. **Modeling fidelity: hybrid.** Full pydantic models for stable/flat payloads; `extra="allow"` + typed `Any` passthrough for deeply-nested/variable payloads (`study-charts`, `investigation-state-tree`, `composite-state`) so fields are never stripped and schema drift doesn't break the route.
5. **SSE dependency: `sse-starlette`** (`EventSourceResponse`) is an allowed dep for `/api/events`; a raw `StreamingResponse` with `text/event-stream` is the fallback if we prefer zero new deps.
6. **Sequencing: after the in-flight stack (#305 → #310 → #311) merges.** New batches stack on `main` so we don't deepen an unmerged stack.

## Architecture

```
TODAY:   cli serve ──> [stdlib Handler]  (live, 60/66 routes)
                       api/app.py (FastAPI, 6 routes, tested only)

DURING:  ... port routes + static + SSE + state into api/app.py,
             extracting each builder into lib/ ...
         cli serve still ──> [stdlib Handler]  (handler shrinks)

FLIP:    cli serve ──> [uvicorn + FastAPI(create_app())]   (one PR)
                       # stdlib Handler + HTTP plumbing deleted
                       # static via StaticFiles, state in app.state
```

- **`api/app.py`** is the assembly point: `create_app()` builds the FastAPI app, mounts routers, mounts `StaticFiles`, and wires lifespan state. It imports **only** `lib/` (never `server.py`).
- **`lib/`** holds every route's pure builder + the moved machinery (event broadcaster, remote-run service, workspace/source-switch state helpers). All `ws_root`-parameterized.
- **`app.state`** holds the single process-global runtime state post-flip: active workspace path, source-switch target, in-memory caches (composite-state TTL cache, registry cache), the remote-run service, and auth session state. Set in the lifespan startup; re-pointed by the source-switch route.

## Phase A — finish the read-only GET routes

**Triage** the remaining read-only GET routes (those that are genuinely read-only and not stateful/streaming — `/api/events`, `/api/remote-run-status`, `/api/auth`, and the POST routes are Phase C, not here):

- **Easy (flat payloads):** `branches`, `branch-diff`, `branch-staleness`, `dirty-status`, `git-status`, `github-repo`, `system-deps-check`, `ui-config`, `framework-metrics`, `pending`, `suggest-poll`, `work-status`, `generation`, `state`.
- **Medium:** `investigation`, `investigations`, `investigation-registry`, `investigation-rigor`, `investigation-composites`, `investigation-composite-doc`, `investigation-run-unblocked-status`, `investigation-viz-html`, `iset`, `study-rigor`, `study-export`, `study-bigraph-paths`, `visualization-classes`, `visualization-instances`, `visualization-status`, `composites`, `composite-resolve`, `composite-runs`, `composite-run`, `work-composite-diff`, `explorer`, `ptools-launch`.
- **Heavy (nested/variable → hybrid modeling):** `study-charts`, `investigation-state-tree`, `composite-state`, `investigation-notebook`.

(Several of the Medium/Heavy ones — `composites`, `composite-resolve`, `investigations`, `catalog`, `registry`, `composite-state`, `study-charts` — arrive via the in-flight stack #310/#311; this phase ports the remainder. Re-triage against `main` after the stack merges so nothing is double-ported.)

**Per-route procedure:**
1. Extract the builder into `lib/<name>.py`, `ws_root`-parameterized; inject any workspace-coupled dep (e.g. "does study have runs?") as a callable so the existing reader stays in `server.py` untouched until its own port.
2. Replace the `server.py` body with a thin delegating shim (name retained → call-sites/tests unchanged).
3. Add a typed FastAPI route in `api/app.py` with a pydantic `response_model` (full model for stable payloads; `extra="allow"` + `Any` passthrough for Heavy).
4. Add a parity test: `server shim output == lib builder output` on a fixture workspace; add a FastAPI `TestClient` test for the route.
5. Regenerate `domain.generated.d.ts`; extend mypy's file list to the new `lib/` module.

**Batching:** thematic PRs (~5–7), grouping `branch/git-*`, `investigation-*`, `study-*`, `visualization-*`, `composite-*`, misc. Each PR is one theme, 4–6 routes, all green (mypy + types staleness + parity + route tests).

## Phase C — the hard machinery

1. **Static / SPA / snapshot.** Mount `StaticFiles` for `static/` assets and report bundles; add a catch-all SPA route returning `index.html` for client-side routes. Snapshot mode is a static bundle served the same way — fold its current special-casing into the StaticFiles mount + a `snapshot` flag on `app.state`. One PR.
2. **SSE `/api/events`.** Move the state-broadcast logic (what the handler streams at `server.py:16478`) into `lib/events.py` as an async broadcaster; the route is an `EventSourceResponse` (sse-starlette) over it. Verify reconnection + the `event: state` payload shape match today. One PR.
3. **POST / mutating routes** (`do_POST`): source-switch, branch push, build-remote, study CRUD, catalog install/uninstall, auth start/poll/logout, etc. Typed pydantic request bodies; logic to `lib/`. Likely 2 PRs (grouped by domain).
4. **In-memory state into `app.state` / lifespan.** Move the `WORKSPACE` global + source-switch re-pointing, the composite-state + registry caches, and the remote-run service into a lifespan-initialized `app.state`. `RemoteRunManager` becomes an app-held service shared across requests (one process). Auth sessions already live in `lib/github_auth`; surface their state through `app.state`. One PR (may pair with the source-switch POST route).

## Phase D — the flip (one PR)

- `cli serve` builds and runs `uvicorn.run(create_app(), host, port)` instead of the threading stdlib server.
- Mount `StaticFiles`; set `app.state` from the `--workspace` arg in the lifespan.
- Delete the `Handler` class and the HTTP plumbing from `server.py` (keep any pure helpers that already moved to `lib/`; ideally `server.py` is gone or a thin re-export shim for back-compat imports).
- **Post-flip smoke test:** launch `create_app()` via `TestClient` / a subprocess, then exercise: a representative GET route, an `/api/events` SSE connect (receives one `state` event), a static asset (`/static/...` 200), a source-switch POST (re-points `app.state`), and `/health`.

The flip is small because every route + static + SSE + state was ported in earlier phases.

## Phase E — typing + CI payoff (follow-ups)

- **Types:** replace `lib/generate_ts.py`'s hand-rolled emission with **`openapi-typescript`** run against the now-complete FastAPI OpenAPI schema, yielding full per-route types (params + request + response). Keep the CI staleness test.
- **mypy:** widen from the narrow file list to package-wide, incrementally (drop `follow_imports="skip"` once `server.py` no longer imports the numpy-stub-heavy modules, or scope around them).
- **CI:** wire a real full-suite job using PR #298's env fixes (editable pbg-superpowers + the `test` extra `[polars,xarray,zarr,pyarrow]`) so the suite passes in CI, not just the local dev env.

## Testing strategy

- **Parity tests** during transition: `server shim == lib builder` per extracted route (catches behavior drift before the flip).
- **FastAPI `TestClient`** per ported route: status, response_model shape, error paths.
- **Types staleness test:** `domain.generated.d.ts` (then the openapi-typescript output) stays in sync with the models / schema.
- **Post-flip smoke/e2e:** the launch + key-routes + SSE + static + source-switch test above.
- **Regression:** the existing iset/investigation/publish/data-endpoint suites must stay green at each step.

## Sequencing / PR shape

1. (Prereq, not ours to merge) in-flight stack **#305 → #310 → #311** lands on `main`.
2. Re-triage remaining read-only routes against `main`.
3. Phase A: ~5–7 thematic read-only PRs (extract→lib + typed route + parity + tests each).
4. Phase C: ~3–4 PRs — static/SPA/snapshot, SSE, POST routes, state-into-app.state.
5. Phase D: 1 flip PR (+ smoke test).
6. Phase E: typing + mypy-widening + CI follow-up PRs.

Each PR: mypy clean (scoped), types-staleness green, parity + route tests, no behavior change until the flip.

## Traps / risks (all previously hit — respect them)

- `response_model` **strips undeclared fields** → model every field, or use `extra="allow"` / typed `Any` for variable payloads (Heavy routes).
- `server.py`'s route logic is a **web of interdependent private helpers** → extract incrementally; inject the one or two workspace-coupled deps as callables rather than pulling a whole subtree.
- **numpy's 3.12-syntax stub** breaks mypy when following `server.py` imports → keep `follow_imports="skip"` until the import is gone.
- **Full-suite failures are env-provisioning**, not bugs (missing optional polars/xarray/zarr + stale editable pbg-superpowers). Use the `test` extra + editable install (PR #298).
- The shared `/code/vivarium-dashboard` checkout is **used by other sessions** — work only in the worktree.

## Out of scope

- **Multi-active workspaces/investigations** (multiple simultaneously). The architecture keeps `lib` ws_root-parameterized and `get_workspace` a `Depends` so it's a clean future add, but it is not built here.
- **JS→TS overlay / app bundler.** Shelved per Jim's steer; TS stays generated-only.
- **New UI features.** This is a server-plumbing migration; no user-facing behavior changes.

## Success criteria

- `cli serve` runs FastAPI under uvicorn; the stdlib `Handler` is deleted.
- All 66 routes + static/SPA + snapshot + SSE + source-switch behave identically to today, verified by parity + smoke tests.
- Local workspaces with local repos, all-investigations-listed, and branch-derived current investigation all work unchanged.
- Types are generated from the OpenAPI schema; mypy runs package-wide; full-suite CI is green.
- Each step shipped as a small, reviewable, behavior-preserving PR.
