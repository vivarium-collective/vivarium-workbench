# Source & Branch panel — design

**Date:** 2026-06-23
**Branch:** `feat/commit-agnostic-remote-builds` (worktree `/Users/eranagmon/code/vdash-sp3`)
**Status:** Design — approved, for spec review

## Context

The commit-agnostic dashboard (SP1/SP2/SP3) can switch its active source between
local workspaces and remote sms-api simulator builds. Today the switcher is a
single dropdown opened from the rail's workspace-name chip. With ~16 local
worktrees of one repo and 50+ remote builds, that flat menu has become long and
hard to scan for a specific branch.

This redesign moves source management out of the cramped dropdown into the
existing **Branch tab** (`#page-github`, `data-page="github"`) as an organized
`local/remote · repo · branch · commit` panel, and surfaces two actions that the
plumbing already mostly supports: **commit + push** (local) and **build/register
a remote repo via sms-api** (the `simulator/upload` flow).

## Goal

One home (the Branch tab) for understanding and changing the dashboard's source:
which repo, which branch, which commit, local or remote — plus pushing local work
to GitHub and registering a remote build. The rail chip becomes a quiet read-only
indicator of the current source.

## Decisions (settled in brainstorming)

1. **Rail chip → plain display.** The `● <repo>:<branch> @<commit>` chip is
   read-only text: no button, no dropdown, no caret, no hover affordance. (Revert
   the trigger styling added during the earlier dropdown work.)
2. **Branch tab = the full panel.** All source/repo/branch/commit management and
   the three actions live in `#page-github`. No competing dropdown in the rail.
3. **Push = commit + push.** The dashboard stages + commits the active workspace's
   changes (with a message the user types), then `git push origin <branch>`. Local
   sources only; disabled for read-only remote builds.
4. **Build via sms-api = register latest HEAD of repo+branch.** Resolve the
   branch HEAD via `GET /core/v1/simulator/latest`, register it via
   `POST /core/v1/simulator/upload` (the #64 flow). "Update" re-registers at the
   new HEAD. The simulator image build runs async on AWS Batch and is not waited
   on (workspace browsing needs only the registered version + repo tarball).

## Current state this builds on

- `#page-github` section exists; `walkthrough.js` renders it when
  `pageId === 'github'`. The new panel mounts as an **isolated module**
  (`static/branch-source.js`) into a container added to `#page-github` — kept out
  of the 15k-line `walkthrough.js`.
- `GET /api/workspaces` — local catalog rows; already carries a branch-derived
  `label`. This spec adds explicit `repo` / `branch` / `commit` fields per row.
- `GET /api/source/builds` — remote sms-api builds; already carries
  `simulator_id` / `repo` / `branch` / `commit` / `label`.
- `POST /api/source/switch {path}` — re-point to a local workspace (SP2).
- `POST /api/source/switch-build {simulator_id}` — materialize + switch to a
  remote build (SP3); already 502-leaves-state-unchanged on failure.
- Existing git push / fork plumbing in `server.py` (~lines 10200–10350) and a
  `git push` helper (~6582) to reuse for the new push endpoint.
- `lib/sms_api_client.py` — `SmsApiClient` with `list_simulators` /
  `download_workspace`; this spec adds `latest_simulator(repo, branch)` and
  `register_simulator(repo, branch, commit)` wrappers over the sms-api endpoints.

## Architecture

### Component 1 — rail chip (display only)
`templates/index.html.j2`: the `#viv-source-switch-trigger` chip reverts to a
plain `.viv-workspace-name` display (no `role=button`, `tabindex`, caret, hover,
or cursor). `source-switch.js` is removed from the page (its switching role is
superseded by the Branch panel). The chip text shows the current source as
`<repo>:<branch> @<commit>` (from the server-rendered current-source context).

### Component 2 — Branch-tab Source panel (`static/branch-source.js`)
Mounts into a `<div id="viv-branch-source">` placed at the top of `#page-github`.
Layout (as approved):

```
Source   ( ◉ Local    ○ Remote )
Repo     [ <repo> ▾ ]
Branch   [ <branch> ▾ ]
Commit     <short-sha>   (current ✓ when it is the active source)
─────────────────────────────────────────
[ Switch ]   [ Commit + Push ]   [ Build via sms-api ]

<compact list of matching entries for the repo+branch selection;
 local rows carry a ✕ forget button (POST /api/workspaces/forget)>
```

Behavior:
- **Source toggle** Local | Remote chooses the data source for the selectors.
- **Repo** selector = distinct repos in scope (local: distinct repo of each
  workspace; remote: distinct repo across builds).
- **Branch** selector = branches for the selected repo.
- **Commit** = the resolved commit for the repo+branch selection (local: that
  worktree's HEAD; remote: the build's commit — when a branch has multiple
  builds, the list below lets the user pick a specific one; the selector defaults
  to the newest).
- The entry matching the dashboard's active source is marked `current ✓`.
- **Switch** → local: `POST /api/source/switch {path}`; remote: `POST
  /api/source/switch-build {simulator_id}`. On success, reload (existing behavior).
- **Commit + Push** (enabled only for Local) → `POST /api/branch/push {message}`.
- **Build via sms-api** → `POST /api/source/build-remote {repo, branch}`, then
  refresh the Remote list and select the new build.

### Component 3 — `POST /api/branch/push` (server.py)
Stages all changes in `WORKSPACE`, commits with the provided message (skips the
commit if the tree is clean), and `git push origin <current-branch>` using the
existing gh-auth/push helpers. Returns `{ok, branch, pushed, commit}` or a 4xx/5xx
with the git/gh stderr surfaced (`{error}`). No force-push. Refuses when WORKSPACE
is not a git repo (the active source is a materialized remote build) → 409 with a
clear reason.

### Component 4 — `POST /api/source/build-remote` (server.py)
Body `{repo, branch}`. Via the dashboard's `SmsApiClient(_sms_api_base())`:
resolve HEAD (`latest_simulator`), then register (`register_simulator`). Returns
`{ok, simulator_id, repo, branch, commit}`. Degrades like the other sms-api routes
when the tunnel is down: 502 with the reason, no state change. Does NOT wait on the
async image build.

## Data flow

```
Branch tab opens
  → Local  : GET /api/workspaces        → group by repo → branch → commit
  → Remote : GET /api/source/builds     → group by repo → branch → commit
User picks repo/branch/commit, clicks:
  Switch        → /api/source/switch[-build] → reload into the new source
  Commit+Push   → /api/branch/push {message} → commit + push origin <branch>
  Build via sms → /api/source/build-remote {repo,branch}
                  → sms-api latest + upload → new build id → refresh Remote list
```

## Error handling

- Push: git/gh failures (auth, no remote, non-fast-forward) returned verbatim,
  shown inline; never silent. Clean tree → `pushed:false, reason:"nothing to commit"`.
- Build: sms-api unreachable / 4xx → 502 with reason; the panel shows it without
  breaking the Local view.
- Switch-build (materialize): unchanged — 502 leaves the active source intact.
- Non-git active source (remote build): Push disabled in UI and 409 server-side.

## Testing

- **Server:** `/api/branch/push` on a temp git repo (clean tree → no-op; dirty
  tree → commit + push to a local bare remote; non-git dir → 409). `/api/source/
  build-remote` with a mocked `SmsApiClient` (success → simulator_id; SmsApiError →
  502). `/api/workspaces` rows now include `repo`/`branch`/`commit`.
- **JS (string-presence, repo convention):** `branch-source.js` references the
  Source toggle, repo/branch/commit selectors, and the three action endpoints;
  `source-switch.js` no longer loaded; the chip has no `role="button"`.

## Phasing (staged tasks within one plan)

- **P1 — reorg:** chip → display; `branch-source.js` panel with Local/Remote ·
  repo · branch · commit + Switch + forget; `/api/workspaces` repo/branch/commit
  fields. No new write endpoints.
- **P2 — push:** Commit + Push action + `POST /api/branch/push`.
- **P3 — build:** Build via sms-api action + `POST /api/source/build-remote` +
  the two `SmsApiClient` wrappers.

## Out of scope (YAGNI)

- Tracking/displaying the async AWS Batch image-build status (register-and-go;
  a future "Register + track status" iteration can add `get-simulator-status`).
- Registering an arbitrary (non-HEAD) commit as a build.
- Force-push, branch creation/deletion, or merge/PR orchestration (the Branch tab
  keeps its existing PR/merge affordances; this spec only adds commit+push).
- Cache eviction for materialized builds (manual `rm`, as in SP3).
