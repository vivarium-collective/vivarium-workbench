# Analyses Data Explorer

The Analyses Data Explorer is a client-side panel on the **Analyses** tab that lets you browse, plot, and inspect simulation outputs from any run stored in the workspace — without writing code.

---

## Views

### Timeseries
Plot one or more scalar, vector-indexed, or bulk-molecule observables as time-series lines. Observables are grouped by category (Listeners, Bulk molecules, …). Multiple series can be overlaid on the same axes.

### Scatter / Correlation
Select any two scalar observables (X and Y) from the run; points are drawn one-per-step. Useful for visualising correlations between cell variables (e.g. mass vs. RNA counts).

### Allocation (polygonal Voronoi)
Renders a Voronoi-treemap breakdown of molecule allocations in the cell. Each tile is coloured by molecule category; area encodes relative count. Uses `d3-voronoi-treemap` + D3 (loaded as peer deps on first open).

### Flux map (Escher / *e_coli_core*)
Overlays per-reaction fluxes from `listeners.fba_results.base_reaction_fluxes` onto the Escher central-carbon metabolic map for *E. coli* core (`e_coli_core.map.json`). Reactions are keyed by their BiGG IDs; a coverage badge shows how many of the model's base reaction IDs were successfully mapped.

Reactions with no mapping render grey. See [flux ID-map coverage caveat](#flux-id-map-coverage-caveat) below.

---

## HTTP Endpoints

All endpoints live under `/api/explorer/` and are registered in `vivarium_dashboard/server.py`.

| Endpoint | Method | Description |
|---|---|---|
| `/api/explorer/runs` | GET | List all runs discoverable in the current workspace (SQLite `runs.db` files + zarr stores). Returns `{runs: [...]}`. |
| `/api/explorer/observables` | GET | `?run=<db_path_or_store>` — return all observable paths grouped by category. Returns `{categories: {<group>: [{path, label, kind}, ...]}}`. |
| `/api/explorer/series` | POST | Body: `{run, paths: [[path, index|null], ...], subsample}` — return aligned `{time: [...], series: {<key>: [...]}}`. Vector paths use the `path#index` key form; bulk molecules use the `bulk[ID]` form. |
| `/api/explorer/flux` | GET | `?run=<db_path_or_store>&step=<int>` — return `{fluxes: {<bigg_id>: value}, coverage: {mapped, total}, step}` for the requested simulation step. |

### Snapshot / read-only mode

When the dashboard runs in hosted snapshot mode (`window.__DASH_CONFIG__.mode === 'snapshot'`), `walkthrough.js` passes `{snapshot: true}` to `Explorer.mount()`. The mount function detects this and renders a local-only note instead of calling any endpoint — the backend endpoints do not exist in the static snapshot bundle.

---

## Emitter support

The data layer (`vivarium_dashboard/lib/explorer_data.py`) transparently handles two storage formats:

**SQLite (`runs.db`)** — written by `process_bigraph.SQLiteEmitter`. Each row in the `history` table holds a JSON-serialised state dict. `list_runs` discovers these by globbing `studies/*/runs.db`.

**Zarr / XArrayEmitter** — written by `pbg-emitters` `XArrayEmitter`. Stores live under `.pbg/runs/<run_id>/store.zarr`. The resolver distinguishes the two formats from the run-id string; zarr reads use `xarray.DataTree.open_zarr`.

---

## Asset generation

The flux-map view requires three pre-generated static assets under `vivarium_dashboard/static/explorer/`:

| File | Content |
|---|---|
| `ecoli_core.map.json` | Escher central-carbon map (BiGG-keyed) |
| `reaction_id_map.json` | EcoCyc/base reaction ID → BiGG ID mapping (derived from iJO1366) |
| `base_reaction_ids.json` | Ordered list of base reaction IDs (flux-vector order from `sim_data`) |

**Generation command** (must use the v2ecoli venv because it needs `cobra`):

```sh
/Users/eranagmon/code/v2ecoli/.venv/bin/python scripts/build_explorer_assets.py
```

Optional arguments:
- `--ecoli-core <path>` — supply a local Escher map JSON instead of fetching from `escher.github.io`
- `--base-reaction-ids <path>` — supply a JSON list of ordered base reaction IDs from `sim_data`

The script prints a coverage report (`base ids covered by map: N/M`) at the end.

---

## Flux ID-map coverage caveat

The mapping from v2ecoli's `base_reaction_fluxes` vector to BiGG reaction IDs is **partial**. The iJO1366 genome-scale model contains ~2,600 reactions; the *e_coli_core* Escher map covers only ~95 of those. EcoCyc IDs that appear in `base_reaction_ids.json` but have no entry in `reaction_id_map.json`, or whose mapped BiGG ID is absent from the Escher map, are skipped.

The coverage badge in the Flux-map view reports `mapped / total` to make this visible. Unmapped reactions render grey on the map; they are not silently discarded from the raw data (the `/api/explorer/flux` endpoint always returns the full `coverage` envelope).

If coverage is too low, re-run `build_explorer_assets.py` with an updated `--base-reaction-ids` list extracted from a current `sim_data` build.
