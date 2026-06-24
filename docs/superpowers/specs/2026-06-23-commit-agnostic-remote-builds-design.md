# Commit-agnostic dashboard — remote sms-api build source (SP3)

**Date:** 2026-06-23
**Branch:** `feat/commit-agnostic-remote-builds` (worktree `/Users/eranagmon/code/vdash-sp3`), **stacked on SP2** (`feat/commit-agnostic-source-switch`, PR #306)
**Status:** Design — for review

## Context: sub-project 3 of 3

The commit-agnostic dashboard: one server, one URL, a source dropdown that
switches between **local workspaces** and **remote sms-api simulator builds**
(each a containerized `repo@commit`). SP3 adds the remote-build half, on top of:

- **SP1** (sms-api PR #146, unmerged) — `GET /api/v1/simulations/workspace?simulator_id=`
  streams a build's `repo@commit` as a gzipped tarball.
- **SP2** (PR #306, unmerged, this branch's base) — one server re-points its
  active `WORKSPACE` in-process (`_switch_active_workspace(new_root)` +
  `_invalidate_workspace_caches()`), with a header source dropdown
  (`static/source-switch.js`) and `POST /api/source/switch`.

SP3 = list sms-api builds, **materialize** the selected one (download SP1's
tarball → extract to a local cache dir), and switch to it via SP2's in-process
re-pointing. Because materialization produces a **local directory**, the switch
itself reuses SP2 unchanged.

## Goal

The source dropdown shows two sections: **Local** (existing registered
workspaces) and **Builds** (live from sms-api). Selecting a build downloads its
workspace once (cached by commit), then the dashboard re-points to it — you
browse that `repo@commit`'s studies/composites/investigations as a full
workspace, all in the same server/URL.

## Decisions

1. **Lazy materialize.** The dropdown lists builds from `get-simulator-versions`
   (cheap metadata only). The workspace tarball is downloaded **only when a build
   is selected** — never all builds upfront.
2. **Cache by commit, reuse immutably.** A build's workspace extracts to
   `~/.pbg/build-cache/sim<id>-<commit>/`. A `repo@commit` is immutable, so if the
   cache dir already exists it is reused (no re-download). (Cache eviction is out
   of scope — manual `rm` for v1.)
3. **Strip GitHub's top-level dir.** GitHub's tarball wraps everything in one
   `<org>-<repo>-<sha>/` directory; the extractor lifts that single child up so
   the cache dir is the workspace root (`workspace.yaml` at top).
4. **Degrade gracefully when sms-api is unreachable.** The Builds section is
   populated by a best-effort call to sms-api (`SMS_API_BASE`, default the SSM
   tunnel `localhost:8080`). On any error it returns an empty list with a reason;
   the dropdown shows "Local" only. No hard failure.
5. **Reuse SP2 for the switch.** After materializing, switch via
   `_switch_active_workspace(cache_dir)` — the cache dir is a server-created,
   trusted local path, so it does not need the catalog allow-list that
   `POST /api/source/switch` enforces for user-supplied paths.

## Current state (what SP3 builds on)

- `lib/sms_api_client.py` — `SmsApiClient(base_url)` with `_get` + `download_data`
  (streams `/data`). **Lacks** a build-list method and a workspace download.
- `lib/remote_run_landing.py` — the tarball extract pattern: `tarfile.open(p,
  "r:gz")` + `tar.extractall(root, filter="data")`. SP3 reuses this shape.
- `server.py` — `_sms_api_base()` → `SMS_API_BASE` env; `_switch_active_workspace`
  + `_invalidate_workspace_caches` (SP2); `POST /api/source/switch` (SP2);
  `GET /api/workspaces` (the Local catalog the dropdown already lists).
- `static/source-switch.js` (SP2) — the dropdown; lists `/api/workspaces`,
  switches + reloads.

## Architecture

### Component 1 — `SmsApiClient` extension (`lib/sms_api_client.py`)
- `list_simulators() -> dict` → `GET /core/v1/simulator/versions`
  (returns `{"versions": [{database_id, git_repo_url, git_commit_hash,
  git_branch, created_at}, ...]}`).
- `download_workspace(simulator_id: int, dest_dir: Path) -> Path` → streams
  `GET /api/v1/simulations/workspace?simulator_id=<id>` to
  `dest_dir/workspace.tar.gz`, returns that path. (Mirrors the existing
  `download_data` streaming-to-file method.)

### Component 2 — build materialization (`lib/remote_build_source.py`, new)
- `build_cache_root() -> Path` → `~/.pbg/build-cache` (created on demand).
- `cache_dir_for(simulator_id: int, commit: str) -> Path` →
  `build_cache_root() / f"sim{simulator_id}-{commit}"`.
- `materialize_build(client, simulator_id, commit, *, force=False) -> Path` →
  if the cache dir exists and not `force`, return it (reuse). Else: download the
  tarball to a temp file (`client.download_workspace`), extract with
  `tarfile.open(..., "r:gz")` + `extractall(filter="data")`, **lift the single
  top-level `<org>-<repo>-<sha>/` child** into the cache dir, return the cache
  dir. Atomic-ish: extract to a temp dir then `os.replace` into place so a
  partial download never leaves a half-cache.
- `list_build_sources(client) -> list[dict]` → calls `client.list_simulators()`,
  maps each to `{simulator_id, repo, commit, branch, label}` where
  `label = f"{repo-name} @ {commit} (build #{id})"`; returns `[]` (best-effort)
  on any sms-api error.

### Component 3 — server endpoints (`server.py`)
- `GET /api/source/builds` → `_get_source_builds`: `list_build_sources(
  SmsApiClient(_sms_api_base()))`; returns `{"builds": [...], "error": <str|None>}`.
- `POST /api/source/switch-build {simulator_id}` → `_post_source_switch_build`:
  look up the simulator in `list_simulators()` (resolve commit), `materialize_build(
  ...)`, then `_switch_active_workspace(cache_dir)`; return `{"ok", "source":
  {path, name}}`. 404 if the simulator_id isn't found; 502 if materialization fails
  (sms-api/download error) — the active workspace is unchanged on failure.

### Component 4 — dropdown integration (`static/source-switch.js`)
Extend SP2's `_populate` to render two `<optgroup>`s: **Local** (from
`/api/workspaces`, existing) and **Builds** (from `/api/source/builds`). A Local
option carries its `path` and switches via `POST /api/source/switch` (SP2,
unchanged). A Build option carries its `simulator_id` and switches via
`POST /api/source/switch-build` (Component 3). Both reload on success. While a
build materializes (first selection downloads), show a "Loading build…" state on
the control.

## Data flow (select a build)

```
Dropdown "Builds" populated  ← GET /api/source/builds ← sms-api get-simulator-versions
   │ user picks build #45 (v2ecoli @ 32b901)
   ▼
POST /api/source/switch-build {simulator_id: 45}
   │  resolve commit; cache = ~/.pbg/build-cache/sim45-32b901
   │  if absent: download /api/v1/simulations/workspace?simulator_id=45 (SP1)
   │             → extract tar.gz → strip top dir → cache
   │  _switch_active_workspace(cache)         (SP2 re-point + invalidate)
   ▼
{ok} → client reload → SPA renders build #45's workspace
```

## Error handling

- sms-api unreachable (tunnel down) → `/api/source/builds` returns `{builds: [],
  error}`; dropdown shows Local only. No crash.
- Unknown `simulator_id` on switch-build → 404, no state change.
- Download/extract failure → 502, active workspace **unchanged** (materialize
  fails before `_switch_active_workspace` is called).
- Private-repo / token errors surface from sms-api as the 502 reason (SP1 owns the
  GitHub token; the dashboard just reports the failure).
- Corrupt/partial cache avoided by extract-to-temp-then-`os.replace`.

## Testing

- **Client:** `list_simulators` hits `/core/v1/simulator/versions`;
  `download_workspace` streams to `dest/workspace.tar.gz` (fake HTTP).
- **Materialize:** with a fabricated `workspace.tar.gz` (a tar containing
  `org-repo-sha/workspace.yaml`), `materialize_build` extracts + strips the top
  dir so `cache_dir/workspace.yaml` exists; a second call with the cache present
  **reuses** (no re-download — assert the client's download is not called).
- **list_build_sources** maps versions → labels; returns `[]` on a client error.
- **Endpoints:** `/api/source/builds` returns the mapped list (mock the client);
  `switch-build` 404s unknown id, and on success calls `_switch_active_workspace`
  with the cache dir (mock materialize) → `WORKSPACE` is the cache dir.
- **Dropdown:** string-presence — `source-switch.js` references
  `/api/source/builds`, `/api/source/switch-build`, and renders an `optgroup`.

## Out of scope (YAGNI / other sub-projects)

- Cache eviction / size management (manual for v1).
- Running sims against a build, or viewing a build's *results* — that's the
  separate remote-runs feature; SP3 is workspace browsing only.
- Auth to sms-api beyond the existing `SMS_API_BASE` tunnel.
- Live no-reload swap (SP2 uses reload).
- vEcoli (non-pbg) builds rendering perfectly — the repo@commit is served as a
  workspace; non-workspace repos simply show little. v2ecoli builds are the target.

## Risks & mitigations

- **Large tarball download blocks the request thread** (the stdlib server is
  threaded, so one slow download ties up one worker, not the server). Mitigate by
  streaming to a temp file (not memory) and keeping the dropdown's "Loading…"
  state; a future enhancement could background the materialize + poll.
- **Top-level-dir assumption** (GitHub always wraps in one dir) — assert exactly
  one top-level entry during extract; if not, fall back to using the extract root
  as-is and log.
- **Stacked on two unmerged PRs** (SP1 #146 + SP2 #306). SP3's tests mock sms-api,
  so they pass without #146 deployed; a live end-to-end needs #146 on smsvpctest
  and #306 merged. The PR documents the merge order: #146 + #306, then #301-style
  retarget of this branch to main.
