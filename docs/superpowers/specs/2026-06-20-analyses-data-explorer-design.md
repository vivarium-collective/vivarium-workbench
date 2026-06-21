# Analyses Data Explorer — Design

**Date:** 2026-06-20
**Status:** Approved (brainstorming) — ready for implementation plan
**Repo:** vivarium-dashboard (worktree `vdash-explorer`, branch `feat/analyses-data-explorer`)

## Summary

Add a third card to the dashboard **Analyses** page (alongside the parsimony 3D
viewer and the PTools launcher): a **native, marimo-style reactive data
explorer** for v2ecoli simulation runs. The user picks a run from the
Simulations DB, then explores it interactively across four views — **Timeseries,
Scatter, Allocation (polygonal Voronoi), and Flux map (Escher-style)** — with
controls that re-fetch and re-render live.

"marimo-style" means the *reactive feel* (change a control → output updates), not
the marimo runtime. No marimo/jupyter/pandas dependency is added. All new
runtime dependencies are client-side, loaded via CDN.

## Goals

- One cohesive, detailed interactive experience for inspecting any run's data.
- Reuse the dashboard's existing data readers and charting stack; add no
  server-side heavy deps (honor the AI-free / lightweight philosophy).
- Keep new code in focused modules rather than bloating `server.py` /
  `walkthrough.js`.
- Degrade honestly in hosted snapshot mode (no live DB) — show a "local-only"
  note like the PTools card.

## Non-goals (explicit fast-follow, not v1)

- Full state-over-time scrubber / whole-state inspector at a step.
- iJO1366 full genome-scale Escher map (v1 uses **e_coli_core**).
- Cross-study multi-run overlay/comparison (v1 explores one run at a time).

## Architecture

Thin HTTP + JSON + client-side rendering, matching the existing dashboard.

### Backend — `vivarium_dashboard/lib/explorer_data.py` (new)

Pure data-prep module. The `server.py` handlers stay thin and delegate here.
Reuses existing readers; no new Python deps.

Four GET endpoints (registered in `server.py`'s GET dispatch, returning
`self._json(...)`):

1. **`GET /api/explorer/runs`**
   List runs for the run-picker. Reuses `lib/simulations_index` (already powers
   `/api/simulations`). Returns
   `[{run_id, label, study, investigation, n_steps, status, db_path, source}]`,
   SQLite + zarr + parquet aware.

2. **`GET /api/explorer/observables?run=<id>&db=<path>`**
   Enumerate available observable paths for the selected run and group them into
   friendly categories. Reuses `lib/study_charts._extract_paths_from_db`
   (+ `_extract_paths_from_zarr` / `_extract_paths_from_parquet`). Returns
   `{categories: {<category>: [{path, index, label, kind}]}}` where category is
   derived from the top-level store key mapped to a friendly name (see
   Categories). `kind` ∈ {scalar, vector-element}.

3. **`GET /api/explorer/series?run=<id>&db=<path>&paths=a,b,c&subsample=N`**
   Return aligned time series for the chosen observable paths. Reuses
   `lib/comparative_viz._extract_trace` (SQLite `json_extract`, with the
   `agents/0/` single-cell fallback already handled) and the zarr/parquet
   extractors. Returns `{time: [...], series: {"<path>": [...], ...}}`. Used by
   Timeseries (N traces), Scatter (2 paths → x/y, optional color-by-time), and
   as the data source for the Allocation slider.

4. **`GET /api/explorer/flux?run=<id>&db=<path>&step=<int>`**
   Return the reaction-flux vector at one timepoint, remapped to BiGG IDs:
   `{step, time, fluxes: {<bigg_reaction_id>: <flux>}, coverage: {mapped, total}}`.
   Reads the run's reaction-flux observable + reaction names, then remaps IDs
   through the static asset `reaction_id_map.json`. Unmapped reactions are
   omitted (they render grey in Escher).

All endpoints return `{error: "..."}` with a non-200 on failure and `[]`/empty
structures for missing/sparse data, so one bad path never sinks the page.

### Frontend — `vivarium_dashboard/static/explorer.js` (new)

Self-contained controller (a single module-scoped object), kept out of the
already-large `walkthrough.js`. `walkthrough.js`'s `_loadAnalysesPage()` gains a
third entry that renders the Explorer card into `#analyses-gallery` and calls
`Explorer.mount(el, {basePath})`. The template (`templates/index.html.j2`) loads
`explorer.js` and the CDN libraries.

Card layout: inline panel + an "Open ↗" full-window mode (same affordance as the
parsimony card). Left rail = controls (run picker, view tabs, category +
observable selectors, sliders, toggles); main area = the active view. Control
changes trigger a debounced fetch → re-render (the reactive feel).

### New client assets

- `static/vendor`/CDN: `d3` (v7), `d3-voronoi-treemap`, `escher` (escher.js).
  All via CDN like Plotly today; no Python packaging impact.
- Bundled static data assets under `static/explorer/`:
  - `ecoli_core.map.json` — the Escher e_coli_core map (central-carbon layout).
  - `reaction_id_map.json` — v2ecoli/EcoCyc reaction id → BiGG id mapping.

### Asset generation — `scripts/build_explorer_assets.py` (new)

One-shot generator (run by a developer, not at request time):
- Fetches/embeds the canonical Escher **e_coli_core** map JSON.
- Builds `reaction_id_map.json` from `cobra` iJO1366 annotations (BiGG ↔ EcoCyc)
  intersected with v2ecoli's metabolism reaction IDs, and prints a coverage
  report (`mapped/total`, plus the list of high-flux unmapped reactions so the
  map can be curated over time).

## The four views (v1)

### 1. Timeseries
Run → category → multi-select observables → multi-trace Plotly line chart over
time. Controls: subsample, log/linear y, normalize (per-trace max) toggle.

### 2. Scatter / correlation
Pick X observable + Y observable, optional color-by-time → Plotly scatter
(phase-space / correlation). Reuses `/series` with two paths.

### 3. Allocation — polygonal Voronoi
Pick a category + a timepoint (slider) → the category members' shares (counts or
mass, user-selectable measure) → a computed **`d3-voronoi-treemap`** (organic
polygonal cells, area ∝ value), colored with the parsimony functional-category
palette. The slider scrubs the timepoint; cells re-weight with an animated
transition. Hover shows member name + value + %.

### 4. Flux map — Escher-style
`escher.js` renders the bundled e_coli_core map with native pan/zoom. A timepoint
slider drives `GET /api/explorer/flux?step=…`; the returned `{bigg_id: flux}` is
fed to Escher's `reaction_data` overlay (color + thickness ∝ flux). A coverage
badge shows `mapped/total`. Unmapped reactions stay grey.

## Categories

Observables are grouped by their top-level store key, mapped to friendly names:
- **Mass** (cell mass / dry mass / submasses)
- **Bulk molecules** (bulk counts)
- **Listeners** (monomer_counts, rna_counts, rnap_data, …)
- **Fluxes** (reaction fluxes / exchange)
- **Growth & division** (growth rate, division markers)

Unmapped top-level keys fall through to an **Other** group. The mapping lives in
`explorer_data.py` as a small ordered dict so it's easy to extend.

## Snapshot / hosted mode

In the hosted read-only bundle there is no live runs DB. The card detects this
(`window.__DASH_CONFIG__.mode === 'snapshot'` and/or an empty `/runs` response)
and renders an honest note — "Interactive exploration is available in the local
dashboard" — exactly like the PTools card does today. No fake interactivity.

## Testing

- **Backend (primary):** unit tests in `tests/` against a fixture `runs.db`
  (reuse `testing/study_fixtures.py`). Cover: `/runs` enumeration; `/observables`
  discovery + categorization; `/series` extraction incl. the `agents/0/`
  fallback and missing-path → empty; `/flux` extraction + ID remap + coverage
  count; malformed/missing run → graceful error.
- **Asset generator:** a test asserting `reaction_id_map.json` is valid JSON and
  reports a coverage number; the generator prints, doesn't fail, on partial
  coverage.
- **Frontend:** light — a smoke check that the card mounts and the four view tabs
  render; manual verification of live interactivity against a real run.

## Risks

1. **Flux ID-map coverage (accepted).** v2ecoli/EcoCyc reaction IDs are not BiGG;
   the e_coli_core map only covers central carbon. Coverage will be partial;
   unmapped reactions render grey and the coverage badge is honest. Mitigation:
   the generator reports high-flux unmapped reactions for incremental curation.
2. **Run data availability.** The main checkout currently has no populated
   `runs.db`; real data lives in study zarr/parquet. The explorer must handle
   sparse/empty enumeration gracefully and the run-picker must surface zarr +
   parquet runs, not only SQLite.
3. **escher.js weight/API.** It's a heavier CDN lib with a specific Builder API;
   contain it behind the Flux view so a load failure degrades to a message
   rather than breaking the card.

## Out of scope / future

State-over-time whole-state scrubber; iJO1366-full map toggle; cross-run
comparison; saving/sharing explorer views as named presets.
