# Robust Composite Simulation Runs — Design

**Date:** 2026-05-14
**Repo:** `vivarium-dashboard`
**Status:** Approved design — ready for implementation planning

## Goal

Make running composite simulations from the dashboard robust against network
errors, browser/tab interruptions, server restarts, oversized composite state,
and dirty git trees — by decoupling the run from the HTTP request that starts
it and persisting all run state to disk.

## Motivation

The current pipeline (`POST /api/composite-test-run`) runs `Composite.run(steps)`
**synchronously inside the HTTP request handler** via a `python -c` subprocess
with a 120 s hard timeout. This has several structural failure modes:

- **Coupled to the connection.** Any network blip, tab sleep, server restart, or
  run exceeding 120 s surfaces in the browser as `Network error: TypeError:
  Load failed`, with no way to reconnect to an in-flight run.
- **ARG_MAX crash.** Both `_post_composite_test_run` and `_render_composite_svg`
  embed the entire composite state JSON inside the `python -c` script string.
  For large composites (v2ecoli's ~5 MB state) this exceeds the OS argument-size
  limit: `OSError: [Errno 7] Argument list too long`. `_render_composite_svg`
  was patched to feed state via stdin; `_post_composite_test_run` is still
  broken for large composites.
- **Scratchpad-only storage.** Only the latest run per composite is kept — prior
  runs are `DELETE`d before each new run. No history, no comparison.
- **No crash recovery.** A server restart mid-run leaves the `runs_meta` row
  stuck `running` forever and orphans the subprocess.
- **Dirty-tree coupling.** The ParCa cache (`out/cache/`, ~175 MB) is not
  gitignored, so it shows as `?? out/`. `_active_branch_action` checks
  `_dirty_workspace()` and returns `409 working tree dirty` *before doing
  anything*, blocking every mutating dashboard action. Its blanket `git add -A`
  would also sweep the 175 MB cache into a commit.

## Architecture

A composite run becomes a **detached background job**, executed by a dedicated
CLI subcommand that outlives the HTTP request. The dashboard's role splits
cleanly in two:

- **Write path** — `POST /api/composite-test-run` writes a *run-request file*,
  spawns the runner detached, records the PID, and returns `202 {run_id}`
  immediately.
- **Read path** — `GET` endpoints only ever read the SQLite DB. They never
  touch the run process, so the browser can disconnect, reload, or the server
  can restart without losing the run.

On server startup a **reconcile pass** repairs any `runs_meta` row left
`running` by a previous crash.

The run logic is extracted out of the HTTP handler into a pure, unit-testable
module (`run_runner.py`). The state is never passed via argv — the runner loads
a run-request file and rebuilds the composite from the registry — which
*structurally* eliminates the ARG_MAX bug class.

## Tech Stack

Python 3.12, `argparse` CLI, `subprocess` with `start_new_session=True` for
detachment, `sqlite3` (WAL mode), `process_bigraph` (`Composite`,
`SQLiteEmitter`), `pbg_superpowers.composite_generator` (`build_generator`).
Frontend: plain `fetch` + `setInterval` polling in the bundled loom-explore
iframe (no SSE/WebSocket).

## Components & File Structure

### New files

- **`vivarium_dashboard/lib/run_runner.py`** — core run logic, extracted from
  the HTTP handler. Public entry point `execute(request_path: Path) -> int`:
  loads the run-request, builds the composite, injects the `SQLiteEmitter`,
  runs in chunks writing progress after each, persists results, writes a
  per-run log, sets a terminal status. Pure (no HTTP, no globals) and
  unit-testable.

- **`vivarium_dashboard/lib/run_registry.py`** — process-lifecycle helpers:
  - `spawn_detached(request_path, workspace) -> int` — launches
    `vivarium-dashboard run-composite --request <path>` with
    `start_new_session=True`; returns the child PID.
  - `reconcile_stale_runs(db_file) -> int` — scans `runs_meta` for
    `status='running'`; for each, if `pid` is NULL or `os.kill(pid, 0)` raises,
    marks the row `orphaned`. Returns the count reconciled.
  - `count_running(db_file) -> int` — counts `status='running'` rows, for the
    concurrency cap.

### Modified files

- **`vivarium_dashboard/cli.py`** — new `run-composite` subcommand: a thin
  argparse wrapper that calls `run_runner.execute(args.request)`. This is the
  process spawned detached.

- **`vivarium_dashboard/lib/composite_runs.py`** — `runs_meta` schema gains
  `pid`, `progress_step`, `log_path`, `heartbeat_at` (all nullable, added via
  guarded `ALTER TABLE`); new helpers `update_progress()`, `mark_orphaned()`,
  `prune_runs()`; `connect()` enables WAL mode and `busy_timeout`.

- **`vivarium_dashboard/server.py`** — `_post_composite_test_run` rewritten to
  the write-path shape; new `GET /api/composite-run/<id>/status` handler;
  `serve()` calls `reconcile_stale_runs()` on startup; `_active_branch_action`
  staging hardened (see Git Hygiene).

- **`vivarium_dashboard/static/loom-explore/...`** — the iframe's run flow
  changes from one blocking fetch to start-then-poll (see Frontend).

- **`.gitignore`** (this workspace) and the **pbg-template scaffold gitignore**
  — add `out/`.

### Run-request file & per-run directory

Each run owns `.pbg/runs/<run_id>/`:

- `request.json` — `{spec_id, overrides, steps, db_file, emit_paths, pkg}`.
  The runner reads state inputs from here and rebuilds the composite from the
  registry — never from argv.
- `run.log` — full stdout/stderr/traceback from the detached process.

`.pbg/` is already gitignored, so run artifacts are git-invisible by
construction.

## Data Flow / Run Lifecycle

### Starting a run — `POST /api/composite-test-run`

1. Validate `spec_id`, resolve `pkg` from `workspace.yaml`, generate `run_id`
   (`<spec_id>__<ts>__<hash6>`, unchanged).
2. Check the concurrency cap: `count_running(db) >= CAP` (default 4) →
   `429 {error: "too many runs in progress"}`.
3. Write `.pbg/runs/<run_id>/request.json`.
4. Insert a `runs_meta` row: `status='running'`, `pid=NULL`,
   `progress_step=0`, `started_at=now`, `log_path` set.
5. `spawn_detached()` launches the runner; write the returned PID into the row.
   If the spawn itself raises, mark the row `failed` and return
   `500 {error, run_id}` (the row persists so the UI can show why).
6. Return `202 {run_id, status: "running"}`. The handler completes in well
   under a second.

### During the run — the detached CLI process

1. Load `request.json`. Build the composite once via `build_generator` (the
   same registry path the build endpoints use). If the ParCa cache is missing,
   the `FileNotFoundError` is caught and the run fails fast with a clear
   message (see Error Handling).
2. Inject the `SQLiteEmitter` (existing `composite_runs.inject_sqlite_emitter`)
   and any user-selected `emit_paths`.
3. Run in chunks (1 step per chunk initially; batch size is an internal
   constant, tunable later). After each chunk: `update_progress(run_id, step,
   heartbeat_at=now)`. The `SQLiteEmitter` writes per-step rows to `history`
   as the run proceeds.
4. All stdout/stderr is redirected to `run.log`.
5. On completion: `complete_metadata(status='completed', n_steps)`.
   On exception: write the traceback to `run.log`,
   `complete_metadata(status='failed')`.
6. Enforce `max_runtime` (default 30 min): if exceeded, self-terminate and set
   `status='failed'` with reason `"exceeded max runtime"`.

### Watching a run — browser, while running

- `GET /api/composite-run/<id>/status` → `{status, progress_step, n_steps,
  heartbeat_at, error?}`. Single-row read; polled every ~1.5 s.
- `GET /api/composite-run/<id>` → trajectory rows from `history`. Works
  mid-run, returning partial results, so the UI renders the trajectory as it
  grows. (Endpoint already exists.)

### Reconcile on startup — `serve()`

Scan `runs_meta WHERE status='running'`. For each row: if `pid` is NULL or
`os.kill(pid, 0)` raises `ProcessLookupError`, mark `status='orphaned'`. A live
PID is left untouched — the run genuinely survived the restart and keeps
writing to the same DB; the UI re-attaches by polling `status` again.

**Invariant:** every read endpoint touches only the DB, never the run process.

## Storage Schema & Retention

### `runs_meta` additions

| Column | Type | Purpose |
|---|---|---|
| `pid` | INTEGER | Detached child PID; reconcile checks liveness via `os.kill(pid, 0)` |
| `progress_step` | INTEGER | Last completed step; drives the UI progress bar |
| `log_path` | TEXT | Workspace-relative path to `run.log` |
| `heartbeat_at` | REAL | Updated each chunk; lets reconcile/UI spot a wedged-but-alive process |

All nullable. `connect()` adds each column via an `ALTER TABLE ADD COLUMN`
guarded by a "column already present?" check against `PRAGMA table_info`, so
existing DBs migrate in place with no data loss.

### Status enum

`running` → `completed` | `failed` | `orphaned`.

- `completed` — clean finish.
- `failed` — a clean in-process error; traceback is in `run.log`.
- `orphaned` — the process died without writing a terminal status (distinct
  from `failed` — there may be no traceback, only the reconcile pass detecting
  a dead PID).

### Retention

The scratchpad `DELETE`-prior-runs logic in `_post_composite_test_run` is
**removed**. All runs are retained in the single shared
`.pbg/composite-runs.db`. A `prune_runs(db, spec_id, keep=20)` helper trims to
the most recent N runs per `spec_id`, called opportunistically at run start.
`keep` is a fixed default for now (not user-configurable — YAGNI).

### Concurrency & locking

`connect()` sets `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` so the
detached runner (writer) and the dashboard's read endpoints don't collide.

## Git Hygiene — the dirty-tree fix

1. **Gitignore `out/`** in this workspace's `.gitignore` and in the
   pbg-template scaffold gitignore, so the ~175 MB ParCa cache stops appearing
   as `?? out/`. `.pbg/` (run-request files, logs, the DB) is already ignored.
2. **Scope `_active_branch_action` staging** (server.py:735): replace the
   blanket `git add -A` with `git add -A -- <content paths>` limited to the
   directories the dashboard authors (`studies/`, `reports/`, top-level `*.py`,
   `*.yaml`, etc.). Even if a large artifact dir reappears untracked, it can
   never be swept into a commit.
3. **Runs stay outside the commit path.** Composite runs do not call
   `_active_branch_action` (they already don't) and write only to gitignored
   locations — so running a sim never trips the `409 working tree dirty` check.

**Out of scope:** making `_active_branch_action` tolerate genuinely dirty
*content* files (e.g. `M reports/index.html`). That `409` is correct — the
dashboard should not auto-commit on top of unrelated edits. This fix ensures
*run artifacts* never cause that 409, not that the check is weakened.

## Error Handling

| Failure | Behavior |
|---|---|
| Browser disconnects / tab sleeps / network blip | Run is a detached process — unaffected. UI re-attaches by polling `status` on reload. |
| Server restarts mid-run | Run's process group is independent (`start_new_session=True`) — keeps writing to the DB. Reconcile sees a live PID and leaves it. UI re-attaches. |
| Server restarts, run already dead | Reconcile sees a dead PID → marks `orphaned`. UI shows "orphaned" + a link to `run.log`. |
| Run raises an exception | CLI catches it, writes the traceback to `run.log`, sets `status='failed'`. `status` endpoint returns `{status:'failed', error:<summary>}`; UI links the full log. |
| Run hangs (no progress) | `heartbeat_at` stops advancing — UI flags "no progress for Ns". A `max_runtime` guard (default 30 min) self-terminates and sets `status='failed'`, reason `"exceeded max runtime"`. |
| Missing/stale ParCa cache | The runner's first build step catches `FileNotFoundError` for `out/cache/initial_state.json`, fails fast with `"ParCa cache missing — run build_cache.py"`, `status='failed'`. |
| ARG_MAX / oversized state | Structurally impossible — state is never passed via argv; the runner loads the run-request file and rebuilds from the registry. |
| Spawn itself fails (CLI not found, etc.) | POST handler catches it, marks the row `failed`, returns `500 {error, run_id}`. The row persists so the UI shows why. |
| Too many concurrent runs | `count_running(db) >= CAP` (default 4) at POST → `429 {error:"too many runs in progress"}`. |
| DB locked (concurrent writers) | `connect()` sets WAL mode + `busy_timeout=5000` so runner writes and dashboard reads don't collide. |

**Logging:** every run has `.pbg/runs/<run_id>/run.log` with full
stdout/stderr/traceback. The `status` endpoint returns a short `error`
summary; the UI links the log for the full detail. This replaces today's
fragile "parse `@@@ERROR@@@` out of stdout" scheme.

## Frontend — loom-explore iframe

The Composite Explorer's run flow changes from one blocking fetch to a
start-then-poll cycle.

**New flow:**

1. **Start** — `POST /api/composite-test-run` returns `202 {run_id}` in <1 s.
   The UI immediately shows a "running" card with a progress bar and the
   `run_id`.
2. **Poll** — every ~1.5 s, `GET /api/composite-run/<id>/status`; update the
   progress bar from `progress_step / n_steps`. While running, also pull
   `GET /api/composite-run/<id>` to render the partial trajectory as it grows.
3. **Terminal** — on `completed`: render final results + viz. On
   `failed`/`orphaned`: show the error summary + a "view log" link to
   `run.log`.
4. **Re-attach** — the active `run_id` is stashed in `sessionStorage`. On
   iframe reload or after a network blip, if there is a stored `run_id` still
   `running`, polling resumes automatically — the user sees the live run, not
   a blank slate.
5. **Cap reached** — `429` → inline "too many runs in progress, wait for one
   to finish" message; no crash.

**Run history panel:** because runs are retained, the explorer gains a small
list of recent runs for the spec via the existing
`GET /api/composite-runs?spec_id=...`. Each entry shows `run_id`, label, a
status badge, step count, and timestamp; clicking one loads its trajectory.

Polling is plain `setInterval` + `fetch` — no SSE/WebSocket. Each poll is an
independent cheap request, so a dropped one simply retries on the next tick.
That independence is the robustness.

## Testing

### Unit tests (`vivarium-dashboard/tests/`)

- **`run_runner`** — execute a tiny composite from a run-request file: assert
  `runs_meta` ends `completed`, `progress_step == n_steps`, `history` has rows,
  `run.log` exists. A deliberately-broken spec asserts `status='failed'` with a
  traceback in the log.
- **`composite_runs`** — schema migration: open an *old* DB (no new columns),
  assert `connect()` adds them and existing rows survive. `prune_runs` keeps
  exactly N newest per spec. `update_progress` / `mark_orphaned` round-trip.
- **`run_registry`** — `reconcile_stale_runs`: a `running` row with a bogus
  dead PID flips to `orphaned`; a row with `os.getpid()` (alive) is left alone.
  `count_running` counts correctly.

### Integration tests (marked slow)

- **Full detached path** — POST `/api/composite-test-run` against a small real
  composite → assert `202 {run_id}` returns in <2 s → poll `status` until
  terminal → assert `completed`, and that the trajectory is readable both
  mid-run and after.
- **Restart resilience** — start a run, kill + restart the server process, hit
  `status`: assert the run still completes (live PID) or is cleanly `orphaned`
  (dead PID).
- **ARG_MAX regression** — run a composite whose state JSON exceeds ~1 MB:
  assert it completes (today this is the exact crash).

### Git-hygiene tests

- `_active_branch_action` with an untracked `out/` present → assert the commit
  does **not** include `out/` and still succeeds.
- The scaffold/workspace `.gitignore` contains `out/`.

### Manual verification

Start a run from the Composite Explorer in the browser; reload the iframe
mid-run and confirm polling re-attaches and the run finishes. Kill the
dashboard mid-run and restart; confirm reconcile + re-attach behave as
designed.
