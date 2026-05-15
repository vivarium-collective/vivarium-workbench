# Open a Prior Run in the Composite Explorer — Design

**Date:** 2026-05-15
**Repo:** `vivarium-dashboard`
**Branch:** `open-prior-run-in-explorer` (off `main`)
**Status:** Approved design — ready for implementation planning

## Goal

Make a Simulations-tab row's **composite cell** a deep link into the
Composite Explorer with that run's results and visualizations already
loaded. As a fold-in, fix the Composite Explorer's existing **Run**
button, which is currently broken against the detached-runs backend.

## Motivation

The Simulations tab gives a workspace-wide view of every persisted run,
but a row only shows metadata — to see a run's trajectory or its
Visualization HTML, the user has to remember the `run_id` and find it
elsewhere. There is no in-product path from "I see a completed run" to
"I see its results."

The Composite Explorer already has a Run tab that renders both the
observable trajectory and `viz_html` iframes — but only for runs the
user *just* kicked off in this session. A prior run's data exists
(persisted in `.pbg/composite-runs.db` and `studies/<name>/runs.db`),
and the endpoints to fetch it exist (`GET /api/composite-run/<id>` and
`GET /api/composite-run/<id>/status`). The gap is purely frontend
plumbing: a route in, a transform from trajectory→observables, and
reuse of the existing render path.

**Latent issue folded in:** today's `_ceTestRun()` (the Explorer's Run
button handler) still expects the OLD synchronous response shape
`{results, viz_html, steps}` from `POST /api/composite-test-run`. The
backend now returns `202 {run_id, status:"running"}` instead. So the
fresh-Run flow inside the Explorer is silently broken — it shows
"0 steps, no observables." This design rewrites `_ceTestRun` to follow
the new start-then-poll contract, **using the same render path** that
prior-run loading uses. One code path serves both flows.

## Architecture

A single render path. Both **prior-run load** (URL has `?run_id=`) and
**fresh in-Explorer run** (user clicks Run) end up calling the same
`_ceLoadRunFromId(run_id)`, which fetches status + trajectory, transforms
trajectory into observable-keyed arrays, and renders into the existing
`#ce-test-results` container. For `running` runs the function starts a
1.5 s `setInterval` poll loop that re-renders on every tick and clears
on terminal state (or on page navigation).

Pure frontend change. No backend code touched, no new endpoints — just
two reads against endpoints that already exist.

## Tech Stack

Plain JavaScript in `vivarium_dashboard/static/walkthrough.js` (vanilla
ES5-ish; the file uses `var`, inline styles, `function () {…}`). One
test in `tests/test_open_run_in_explorer.py` exercising the same
in-process server pattern as `tests/test_simulations_api.py`.

## Components & File Structure

### Modified files

- **`vivarium_dashboard/static/walkthrough.js`** — six small additions:
  1. `_trajectoryToObservables(trajectory) → {key: [entries...]}` —
     transforms per-step rows `[{step, time, state: {...}}]` into the
     observable-keyed shape the existing renderer wants.
  2. `_ceRenderRunResults({status, results, viz_html, n_steps,
     progress_step, log_path, error})` — single writer of
     `#ce-test-results`. Renders four states: `completed` (table + viz
     iframes), `running` (progress bar + partial table),
     `failed`/`orphaned` (error excerpt + log path).
  3. `_ceLoadRunFromId(run_id)` — fetches
     `/api/composite-run/<id>/status` and `/api/composite-run/<id>`,
     transforms, renders, and (if `running`) starts polling.
  4. `_ceStopRunPoll()` — clears the interval. Called by `_switchPage`
     on navigation away.
  5. `_ceTestRun()` rewrite — replaces the broken synchronous flow:
     POST → `202 {run_id}` → set `window._ceLastRunId = run_id` → call
     `_ceLoadRunFromId(run_id)`. Same code path as prior-run loading.
  6. `_initCompositeExplorer` extension — after `_ceFetch()` populates
     wiring/Document/View tabs, read `?run_id=` from
     `window.location.search`; if present, call `_ceLoadRunFromId`.
  7. `_renderSimRow` update — wrap the composite `<code>{spec_id}</code>`
     cell in an anchor `<a href="<url>?id=<spec>&run_id=<run>#composite-explore"
     class="sim-composite-link" onclick="_openSimulationInExplorer(...)">`.
     Click handler builds the URL the same way `_openCompositeExplorer`
     does today (line 2433): `url.searchParams.set('id', ...)`,
     `url.searchParams.set('run_id', ...)`, `url.hash =
     '#composite-explore'`, `history.pushState`, `_switchPage('composite-explore')`.
     The visible `href` provides right-click/middle-click affordance.

### Test file

- **`tests/test_open_run_in_explorer.py`** *(new)* — integration smoke:
  - `test_walkthroughjs_exports_required_symbols` — fetch
    `/walkthrough.js`, assert all five new symbols
    (`_ceLoadRunFromId`, `_ceRenderRunResults`,
    `_trajectoryToObservables`, `_ceStopRunPoll`, and a `_ceTestRun`
    that reads `run_id` from the response).
  - `test_simulations_row_template_links_to_explorer_with_run_id` —
    same fetch, assert the row-render template includes `'run_id'` and
    wraps the composite `<code>` in an anchor.
  - `test_explorer_loads_with_run_id_then_endpoints_serve` — start a
    real composite-test-run, poll to `completed`, then GET both
    `/api/composite-run/<id>` (trajectory) and
    `/api/composite-run/<id>/status` (status + viz_html). Confirm the
    JS would receive a complete canonical input.

## Data Flow

### Loading a prior run via URL (`#composite-explore?id=<spec>&run_id=<id>`)

```
_initCompositeExplorer
  ├─ parse ?id   → window._ceCurrent = {id, overrides: {}}
  ├─ parse ?run_id → window._ceCurrent.run_id (if present)
  ├─ _ceFetch()    → /api/composite-resolve → wiring/Document/View tabs
  └─ if run_id:
       _ceLoadRunFromId(run_id)
```

### `_ceLoadRunFromId(run_id)`

1. `GET /api/composite-run/<id>/status` → `{status, progress_step,
   n_steps, viz_html?, error?, log_path?}`.
2. `GET /api/composite-run/<id>` → `{trajectory: [{step, time, state}, …]}`.
3. `results = _trajectoryToObservables(trajectory)` — flatten per-step
   rows into observable-keyed arrays.
4. `_ceRenderRunResults({status, results, viz_html, n_steps,
   progress_step, log_path, error})` → write `#ce-test-results`.
5. If `status === 'running'`: `_cePollIntervalId = setInterval(tick, 1500)`
   where `tick` re-fetches both endpoints, recomputes `results`, and
   re-renders. On any terminal status, `_ceStopRunPoll()`.

### Fresh in-Explorer run (`_ceTestRun()` rewrite)

```
user clicks Run in Explorer
  → POST /api/composite-test-run {id, overrides, steps, emit_paths}
  → response: 202 {run_id, status: "running"}
  → window._ceLastRunId = run_id
  → _ceLoadRunFromId(run_id)           // same path as URL load
```

The old `_ceTestRun` body (table-build from `json.results`, viz iframe
construction, traceback `<details>`) is replaced entirely. Its UI
elements (`#ce-steps`, `#ce-test-results`) are reused unchanged.

### Simulations row click → Explorer

`_renderSimRow` produces an anchor inside the Composite cell:

```html
<a href="?id=<spec>&run_id=<run>#composite-explore"
   class="sim-composite-link"
   onclick="_openSimulationInExplorer('<run>', '<spec>'); return false;">
  <code>v2ecoli.composites.baseline.<strong>baseline</strong></code>
</a>
```

`_openSimulationInExplorer(run_id, spec_id)` is a small new helper that
mirrors `_openCompositeExplorer(id)` at `walkthrough.js:2433`: builds the
URL via `new URL(window.location.href)`, sets both `id` and `run_id`
search params, sets `hash = '#composite-explore'`, `history.pushState`,
`_switchPage('composite-explore')`.

The visible `href` lets the URL be right-clicked/middle-clicked into a
new tab and survives plain link-following. The hash router stays
untouched — neither `fromHash()` nor `validPages` need to change,
because the page key (`composite-explore`) lives in the hash and the
params live in the search portion of the URL, which is exactly what
`_initCompositeExplorer` already reads via `window.location.search`.

## Error Handling

| Failure | Behavior |
|---|---|
| Missing `id` in URL | Existing behavior — `_initCompositeExplorer` shows "No composite id specified". |
| `run_id` present but `id` missing | Same as above; `run_id` is ignored since wiring/spec context is needed too. |
| `GET /api/composite-resolve` fails | Existing behavior — `_ceFetch`'s catch surfaces "Network error: …" in `#ce-loading`. Run-tab content stays empty. |
| `GET /api/composite-run/<id>/status` 404 (run deleted) | Banner in `#ce-test-results`: "This run no longer exists. It may have been deleted from the Simulations tab. Click **Run** to start a new one." Wiring/Document tabs work normally. |
| Empty `trajectory` (early `running` or `failed` before emit) | Status chip + step count shown; results table replaced with "No trajectory data yet." |
| One of two fetches fails | Render whatever's available. Status-only with no trajectory → status banner + no table. Trajectory-only with no status (very unlikely) → treat as `completed`. |
| `_trajectoryToObservables` malformed input | Skip rows without `step`/`state`. Empty result → "No observables in this run." |
| One `viz_html` iframe fails | Iframes are independent — one broken iframe doesn't break the others. |
| Server restart mid-poll | Each poll's `.catch` is silent; the next tick retries. On the next successful poll, an `orphaned` flip renders the orphaned UI. |
| Page navigation away from explorer | `_switchPage` calls `_ceStopRunPoll()` to clear the interval. Without this, a stale interval polls forever. |
| `_ceTestRun` rewrite: 429 cap | Surface server's error message inline, no retry. Same pattern as the loom-explore RunPanel. |
| Poll never reaches terminal | No UI timeout — backend's `MAX_RUNTIME_SEC = 1800` self-terminates the run; UI keeps polling and eventually sees `failed`. |

**Invariant:** `_ceRenderRunResults` is the sole writer of
`#ce-test-results`. The container's state is always a function of the
last input it received. Both URL-load and fresh-run paths route
through it.

## Testing

### Integration smoke (`tests/test_open_run_in_explorer.py`)

Spin up the server against the `ws_increase_demo` fixture (same
pattern as `test_simulations_api.py`).

- **`test_walkthroughjs_exports_required_symbols`** — fetch
  `/walkthrough.js`, assert the served bytes contain
  `_ceLoadRunFromId`, `_ceRenderRunResults`,
  `_trajectoryToObservables`, `_ceStopRunPoll`, and a `_ceTestRun` body
  that references `run_id` (not the old `simulation_id` or `results`
  fields from the synchronous response).
- **`test_simulations_row_template_links_to_explorer_with_run_id`** —
  same fetch, assert the `_renderSimRow` template wraps the composite
  cell in `<a` and the link/handler references both `id` and `run_id`.
- **`test_explorer_loads_with_run_id_then_endpoints_serve`** — POST a
  composite-test-run (the fixture's `pbg_ws_increase_demo.composites.increase-demo`),
  poll via `_poll_until_terminal` (helper in
  `tests/test_simulations_api.py`), then assert `GET
  /api/composite-run/<id>` returns `{trajectory: [...]}` with ≥1 row
  and `GET /api/composite-run/<id>/status` returns
  `{status:'completed', viz_html: ...}`. Confirms the data the JS
  would render is well-formed.

### Manual checks (in the PR test plan, not automated)

1. Click Run inside the Composite Explorer — confirm the rewrite
   works: `202` returned, progress bar advances, terminal state
   renders the observable table + `viz_html` iframes. (The OLD broken
   `_ceTestRun` would have shown "0 steps, no observables.")
2. From the Simulations tab, click a row's composite cell — Explorer
   opens with that run's results pre-loaded. URL contains
   `?id=...&run_id=...#composite-explore`. Hard-refresh restores the
   same view.
3. Click a `running` run — Explorer opens, progress advances, terminal
   state renders fully.
4. Click a `failed`/`orphaned` run — Explorer opens, banner shows the
   error excerpt and `log_path`. No empty results table.
5. Navigate away from the Explorer mid-poll — confirm via DevTools
   that no further `/api/composite-run/.../status` requests fire
   (interval cleared).
6. Delete a run from Simulations, then revisit its Explorer URL —
   confirm the "this run no longer exists" banner appears.

## Out of Scope

- **JS unit-test runner** (vitest/jest) — the dashboard repo has no JS
  test infrastructure and bringing one in just for `_trajectoryToObservables`
  is disproportionate. If vitest is added later, that function is the
  obvious first test target.
- **URL update on a fresh in-Explorer run** — after a fresh Run
  completes, the URL is *not* automatically updated with the new
  `run_id`. The fresh-run path uses `_ceLastRunId` in memory but
  doesn't `pushState`. A future improvement could make every completed
  fresh-run shareable via URL; out of scope here.
- **In-place delete from the Explorer** — there is no "delete this
  run" affordance on the Explorer page; deletion stays in the
  Simulations tab.
- **Migrating other run-display surfaces** (Study Detail page, etc.)
  to the same canonical render path. They have their own paths today
  and aren't affected by this work.

## Non-Goals

- Replace the Simulations tab — it remains the workspace-wide listing
  + delete affordance. This feature only adds a navigation edge from
  it into the Explorer.
- Backend changes — no new endpoints, no schema changes, no run
  semantics changes.
- Live updates while a user is in the Simulations tab — the table
  refresh is still manual (Refresh button). The Explorer's
  in-progress polling is local to the Explorer page.
