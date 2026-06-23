# Commit-agnostic dashboard — runtime workspace re-pointing (SP2)

**Date:** 2026-06-23
**Branch:** `feat/commit-agnostic-source-switch` (worktree `/Users/eranagmon/code/vdash-sp2`)
**Status:** Design approved — ready for implementation plan

## Context: this is sub-project 2 of 3

The larger goal is a **commit-agnostic vivarium-dashboard**: one server, one URL,
with a source dropdown that switches between **local workspaces** and **remote
sms-api simulator builds** (each a containerized `repo@commit`). That decomposes
into three sub-projects:

- **SP1 — sms-api workspace export.** A new `GET /simulator/<id>/workspace.tar.gz`
  endpoint that exports a build's workspace as a tarball. Small, independent,
  review-gated by Alex Patrie / Jim Schaff. Proceeds in parallel; **out of scope
  here.**
- **SP2 — runtime workspace re-pointing (THIS SPEC).** Make one dashboard server
  re-point its active `WORKSPACE` in-process via a source dropdown, switching
  among **local** workspaces with no restart and no port change. The foundation;
  immediately useful on its own ("switch local repos in one server").
- **SP3 — remote-build source.** List sms-api builds (`get-simulator-versions`),
  materialize the selected one (download SP1's `workspace.tar.gz` → local cache),
  and add it to the same source dropdown. Built on SP1 + SP2. **Out of scope here.**

SP2 is built first because it is self-contained, demoable with local repos
alone, and de-risks the runtime re-pointing / cache-invalidation / `sys.modules`
machinery before remote complexity rides on top.

## Goal

One long-lived dashboard server whose **active workspace is switchable at
runtime**. Selecting a different registered local workspace from a header source
dropdown re-points the server in-process — same process, same port, same URL —
and the UI reflects the new workspace after a reload. No `vivarium-dashboard
serve` restart required.

## Why this shape (decisions made during brainstorming)

1. **Single re-pointing server**, not one-server-per-workspace. The existing
   switcher spawns a new `serve --workspace` process per workspace and navigates
   between ports; SP2 instead re-points `WORKSPACE` in-process. (User chose the
   single-server model for a true one-URL "commit-agnostic" feel.)
2. **Materialization (SP3) uses a local cache**, so by the time a source is
   "active" it is always a local directory — meaning SP2 only ever has to
   re-point at a *local* path. Remote builds are SP3's concern.
3. **Subprocess-isolate composite discovery.** The hard part of re-pointing is
   that composite discovery/generators `import` the workspace's Python package
   in-process, leaving stale `sys.modules` after a switch. `build_core` (the
   registry) already avoids this by running in a subprocess; SP2 extends that
   same isolation to composite discovery so the main server never imports a
   workspace package. (User chose the clean foundation over degrade-then-harden.)
4. **Keep the legacy per-workspace separate-process flow** (`/api/workspaces/
   start`/`stop`) as an option alongside the new in-process re-pointing. Nothing
   removed. (User chose to keep it.)

## Current state (as surveyed)

- `WORKSPACE` is a module global set once in `serve()` (`server.py` ~16919);
  `lib._root.set_workspace_root` is called once at startup. Many lib functions
  read the root from there.
- The workspace switcher (`static/workspace-switcher.js`) is a **catalog** UI:
  `GET /api/workspaces` lists registered workspaces; `add`/`forget` manage the
  catalog; `start`/`stop` spawn/kill a per-workspace `serve` subprocess on its
  own port; "open" does `window.location.href = ws.url` (navigate to that port).
- `build_core()` (registry data) is **already run in a subprocess**
  (`server.py` ~367/447, `sys.executable`, ~30s cache) — the main server does
  *not* hold the workspace package for the registry path.
- In-process workspace-package imports remain on the **composite** paths:
  `discover_all_composites(ws_root, pkg)` (~3586) and `discover_generators()`
  (~3632), both after `_ws_add_to_sys_path()` (prepends `WORKSPACE` to
  `sys.path`). These are what subprocess-isolation (decision 3) targets.
- The data-reading paths (studies, investigations, runs, reports, charts) and
  the new FastAPI routes (`/api/simulations`, `/api/iset-list`, `/api/data-
  sources`, `/api/references-bib`, `/api/saved-visualizations`) read **files**
  from the workspace and already accept an explicit `ws_root` in `lib/`, so they
  are re-point-ready.

## Architecture

### Component 1 — the switch handler
`POST /api/source/switch` with body `{path: <workspace dir>}`:
1. Validate `path` is a registered/known workspace directory (reuse the catalog
   allow-list; reject arbitrary paths — no traversal).
2. `lib._root.set_workspace_root(new_root)` + reassign the `server.WORKSPACE`
   global.
3. `invalidate_workspace_caches()` (Component 2).
4. Return `{"ok": true, "source": {path, name}}`.

Guarded by a process-level lock so a switch cannot interleave with an in-flight
request mid-read. The handler is a thin adapter over a pure
`lib`-level function (`switch_active_workspace(new_root)`), so it is unit-testable
without HTTP.

### Component 2 — `invalidate_workspace_caches()`
A single function, called in exactly one place (the switch handler), that clears
every cache/registry keyed to the previous workspace:
- the `build_core`/registry subprocess-result cache,
- the data-sources cache (`lib.data_sources` cache is keyed by `ws_root`, so this
  is already isolated — but clear defensively),
- the composite-discovery results + the generator `_REGISTRY`,
- any other module-level `WORKSPACE`-derived caches discovered during
  implementation (the plan enumerates them by grepping for module-global caches
  that read `WORKSPACE`).
Auditable by construction: one function, one call-site.

### Component 3 — subprocess-isolate composite discovery
Extend the existing `build_core`-subprocess pattern to composite discovery so the
**main server never imports the workspace package**:
- Move the in-process `discover_all_composites` / `discover_generators` calls
  (after `_ws_add_to_sys_path`) behind a subprocess runner that imports the
  workspace package, discovers, and returns the result as JSON — mirroring how
  registry data is already fetched.
- Cache the result per workspace (like the registry cache); cleared by
  `invalidate_workspace_caches()`.
- Net effect: after a switch, composites are recomputed against the new
  workspace's package in a fresh subprocess — always fresh, no `sys.modules`
  pollution in the server.

### Component 4 — source dropdown (UI)
Reuse the existing catalog. Change only the **"open" action**:
- Add a header **source dropdown** listing registered local workspaces with the
  active one marked (data from `GET /api/workspaces`, which already exists).
- Selecting a workspace `POST`s `/api/source/switch` then `window.location.
  reload()`. The reload re-renders the SPA shell server-side (new workspace
  branding) and every `/api/*` fetch hits the new `WORKSPACE`.
- The catalog's `add`/`forget` are unchanged. The legacy `start`/`stop`
  (separate-process, own-port) flow stays available as an option.

## Data flow (a switch)

```
User picks workspace B in the source dropdown
   │
   ▼
POST /api/source/switch {path: B}
   │  set_workspace_root(B); WORKSPACE = B
   │  invalidate_workspace_caches()
   ▼
{ok}  ──►  client: window.location.reload()
   │
   ▼
GET /  → SPA shell rendered for B (branding)
GET /api/study/... , /api/investigations, /api/composites, …
   → all read from B; composites recomputed via subprocess
```

## Error handling

- Unknown/unregistered `path` → 400, no state change.
- Switch while a detached run is executing: unaffected — runs are subprocesses
  writing to *their* workspace's `runs.db`; re-pointing the server does not touch
  them.
- A workspace that fails to load (missing `workspace.yaml`, etc.): the switch
  still re-points (the UI degrades per-route as it already does for a broken
  workspace); the switch handler does not validate workspace health beyond
  "is a registered directory."
- Concurrent switches / switch-mid-request: serialized by the process lock.

## Testing

- **Unit:** `switch_active_workspace(new_root)` re-points the root and the
  `WORKSPACE` global; `invalidate_workspace_caches()` empties each enumerated
  cache; the switch handler rejects an unregistered path (400).
- **Composite subprocess:** the subprocess composite-discovery runner returns the
  same shape the in-process path did for a fixture workspace (parity test).
- **Flow:** with two registered local fixture workspaces, one server serves
  distinct `/api/workspace` + `/api/study` + `/api/composites` data across a
  `POST /api/source/switch` — proving re-pointing + cache invalidation end to end
  without a restart.
- Existing workspace-switcher tests stay green (the catalog `add`/`forget`/`list`
  + `start`/`stop` are unchanged).

## Out of scope (YAGNI / other sub-projects)

- SP1 (sms-api `workspace.tar.gz` endpoint) and SP3 (remote-build catalog +
  materialization + download-to-cache). SP2 only re-points among *local*
  registered workspaces.
- "Re-render in place without a reload." A full `window.location.reload()` is the
  v1 mechanism (robust, re-renders branding). A no-reload live swap is a possible
  future polish, not SP2.
- Removing the legacy separate-process flow (kept by decision 4).
- Multi-user / shared-server concurrency beyond the single-process switch lock.

## Risks & mitigations

- **Hidden `WORKSPACE`-keyed caches** not cleared on switch → stale data. Mitigate
  by enumerating module-global caches in the plan (grep for caches reading
  `WORKSPACE`) and centralizing clears in `invalidate_workspace_caches()`.
- **Subprocess composite-discovery parity** — the subprocess must reproduce the
  in-process discovery output exactly. Mitigate with the parity test above and by
  reusing the established `build_core` subprocess machinery rather than inventing
  a new one.
- **`sys.path` accumulation** — `_ws_add_to_sys_path` prepends each workspace;
  after isolating composites the server should no longer need to mutate `sys.path`
  for discovery at all. The plan removes/limits those in-process `sys.path`
  insertions so switching does not accumulate stale roots.
