# FastAPI state → app.state design (strangler-fig Phase C-state)

**Date:** 2026-06-26
**Status:** approved (direction), ready to plan
**Context:** completes the vivarium-dashboard FastAPI strangler-fig migration. ~73 GET + ~48 POST
routes already serve from `api/app.py` via pure `lib/` builders. The remaining ~25 POST routes
(+ a few status GETs) are "stateful" — they read/write in-memory state currently rooted in
`server.py` module globals. This spec defines how that state becomes FastAPI-owned so those routes
can port, and how the eventual flip (Phase D) stands up uvicorn.

## The reframe that shapes everything

The stdlib `Handler` and FastAPI **never run simultaneously in production**. Today the stdlib
`serve()` is live; FastAPI is exercised only in tests. Phase D switches the entrypoint atomically
(uvicorn `create_app()`) and deletes the Handler. Therefore state never needs cross-*process*
sharing — it needs to be **owned by lib-level singletons + a FastAPI lifespan**. Most of it already
is:

| State | Lives today | Effort |
|---|---|---|
| `run_jobs.manager`, `remote_run_jobs.manager` | lib singletons (own their daemon threads) | ~none |
| `github_auth` sessions/flows | lib singleton (`lib/github_auth.py`, keyring-backed) | ~none |
| workspace-keyed caches (registry/linkage/observables/composite-state/data-sources/WP/run-store/composites-list) | lib + server module dicts | low — centralize invalidation |
| `WORKSPACE` Path | `server.py` global, **already mirrored into `lib._root._WS_ROOT`** on startup + every switch (cli.py:45, server.py:294, server.py:11596) | the one real change |

**Decision (user, 2026-06-26):** lib-singleton source of truth — NOT a typed AppState container
injected via `Depends` everywhere. `app.state` is used only as a thin lifespan-populated
convenience. The stdlib handler's 100+ `WORKSPACE` reads stay UNTOUCHED until the flip deletes them.

## Architecture

### 1. Active-workspace single source of truth (`lib/active_workspace.py`)

`lib/_root.py` already holds `_WS_ROOT` and is kept in sync by every workspace mutation. Build the
new module as a thin **facade over `_root`** plus a cache-invalidation registry (do NOT fork the
root state — re-export `_root`'s getter/setter so there is exactly one `_WS_ROOT`):

```python
# lib/active_workspace.py
from . import _root

def get_workspace_root() -> Path | None: return _root.get_workspace_root()
def set_workspace_root(path) -> None:     _root.set_workspace_root(path)

_CLEAR_CBS: list[Callable[[], None]] = []
def register_clear_cb(fn) -> None: _CLEAR_CBS.append(fn)   # each cache module registers its clear_cache
def invalidate() -> None:
    for fn in _CLEAR_CBS: fn()
```

Each cache module (`report_views`, `observables_views`, `composite_state_views`, `data_sources`,
and the server-local `_WP_CACHE`/`_RUN_STORE_SUMMARY_CACHE`/`_COMPOSITES_LIST_CACHE`/`registry`)
registers its `clear_cache` with `register_clear_cb` at import. `server._invalidate_workspace_caches`
becomes a one-line call to `active_workspace.invalidate()` (its current explicit list moves into the
registrations) — **byte-identical clearing behavior**, verified by test.

### 2. FastAPI reads the shared root

`api/app.py::get_workspace()` changes from "read `VIVARIUM_DASHBOARD_WORKSPACE` env var" to:

```python
def get_workspace() -> Path:
    root = active_workspace.get_workspace_root()      # set by lifespan / stdlib startup / switch
    if root is not None: return root
    return Path(os.environ.get(WORKSPACE_ENV, ".")).resolve()   # test/CLI fallback, override-friendly
```

This is behavior-preserving for the existing tests (they set the env var and/or use
`dependency_overrides`), and it makes a stdlib-driven `/api/source/switch` instantly visible to
FastAPI routes during the build period. `get_workspace()` stays the `Depends` seam for all routes.

### 3. Managers & auth stay lib singletons

`run_jobs.manager`, `remote_run_jobs.manager`, and `lib/github_auth.py` are already process-global
and server-agnostic. The ported FastAPI routes import and use them directly (same objects the stdlib
handler uses). Optionally the lifespan stashes references on `app.state` for test injection, but the
**source of truth stays in lib** — no manager is re-instantiated per app.

### 4. FastAPI lifespan = the future entrypoint

Add an `asynccontextmanager` lifespan to `create_app()` that, given a workspace (from an env var /
CLI arg), performs the startup `serve()` does today (cli.py:31-124 + server.py:11575-11626):
`set_workspace_root`, `sys.path.insert(workspace)`, render dashboard HTML once, write server-info,
register in the workspace catalog, reconcile stale runs. During the build period this lifespan is
inert for the live stdlib server (it only runs under uvicorn/TestClient); at the flip it becomes the
real entrypoint. Extract the startup steps into a reusable `lib/startup.py` (pure, ws_root-param) so
both `serve()` and the lifespan call the SAME code (dedup at the flip).

## Port order (each a reviewable batch, same locked patterns)

1. **C-state-1 (foundation):** `lib/active_workspace.py` + cache-callback registrations + `get_workspace()`
   reads the shared root. No route ports. Behavior-preserving; proven by a workspace-switch parity test
   (FastAPI route sees the switched root) + the existing `_invalidate_workspace_caches` clearing test.
2. **C-state-2 (read-only status GETs):** port the routes that only READ managers —
   `investigation-run-unblocked-status`, `remote-run-status`, and any run/job status GETs — to FastAPI
   reading the lib manager singletons. Lowest risk (no mutation).
3. **C-state-3 (stateful POSTs, grouped):** port the mutating routes —
   `source/switch`+`switch-build`+`build-remote`, `remote-run-start`, `investigation-run`+`run-one`+
   `run-unblocked`, `study-run-*`/`study-tests-run`/`composite-test-run`/`run-tests`,
   `catalog-install`/`uninstall`, `system-deps-install`/`import-install`, `auth/github/start`+`logout`,
   `workspaces/start`+`stop`, `work-push`/`create-pr`/`end`, `dirty-commit-all`,
   `investigation-create` (scaffold). `/api/source/switch` becomes a shared lib function (the switch +
   `active_workspace.invalidate()`) callable from both servers. CSRF gets implemented on the FastAPI
   POST surface here (the deferred item).
4. **C-state-4 (the remaining mechanical git/FS POSTs):** `workspaces add/forget/cleanup-stale`,
   `work-start/attach-report/link-branch` — fold in here since they touch the workspace registry /
   shell git, now that the shared-state plumbing exists.
5. **Phase D — the flip (separate spec, explicit user sign-off):** `lib/startup.py` shared by uvicorn
   lifespan; cli `serve` → `uvicorn.run(create_app(...))`; delete the stdlib `Handler` + its `WORKSPACE`
   global; the lib source of truth remains. Then Phase E: openapi-typescript, package-wide mypy,
   full-suite CI.

## Constraints (unchanged)

- **Behavior-preserving** at every step; the live stdlib path stays byte-identical until the flip.
- **Python-first, AI-free.** No new deps (lifespan uses stdlib `contextlib`; no sse/extra libs).
- **No `lib → server` import.** `active_workspace` lives in lib; server calls INTO it.
- Each batch: extract→lib, typed FastAPI route, behavioral parity tests, scoped mypy, generate_ts,
  reviewed before merge. The flip ALWAYS needs explicit user sign-off.

## Risks / watch-items

- **Cache-callback registration order / double-registration:** registry must be idempotent (register
  once at module import); test that `invalidate()` clears exactly the same set as today's explicit list.
- **`get_workspace()` None-fallback:** existing tests rely on the env var + `dependency_overrides`;
  the fallback must preserve both. Verify the full `test_api_app.py` suite stays green.
- **Lifespan inertness during build:** the lifespan must not run side effects (HTML render, server-info
  write) when imported by tests that don't want them — gate on an explicit workspace arg / env and keep
  TestClient usage that doesn't trigger startup writes, or make the writes idempotent/opt-in.
- **`investigation-create` scaffold** is the heaviest POST; consider its own sub-batch within C-state-3.
```
