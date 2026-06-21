# Data Explorer v2 — Design

**Date:** 2026-06-20
**Status:** Approved (brainstorming) — ready for implementation plan
**Repo:** vivarium-dashboard (worktree `vdash-explorer`, branch `feat/analyses-data-explorer`)
**Builds on:** the v1 Data Explorer (`2026-06-20-analyses-data-explorer-design.md`)

## Summary

Rebuild three of the explorer's four views to match how the data is actually
explored: **Timeseries** becomes a multi-trajectory, unit-grouped explorer with
RNA/Protein/Metabolite/Flux class filtering; **Scatter** becomes a run-vs-run
omics comparison (each point an entity, X in run A vs Y in run B, y=x diagonal);
**Allocation** becomes a labeled, drill-down mass Voronoi. Flux view is unchanged.

Grounded in reconnaissance of real v2ecoli run data:
- Per-observable **units** are derivable from the observable path (units live in
  listener port schemas, not in the emitted payload).
- A 4-way **molecule class** (RNA/Protein/Metabolite/Flux) is derivable from the
  observable path.
- The **mass submass hierarchy** (cell_mass → protein/rna/dna/smallMolecule/water;
  rna → rRNA/tRNA/mRNA) is emitted and fixed/known.
- **Experimental omics** in this workspace is culture-level only (no measured
  proteome/transcriptome), so sim-vs-experiment scatter is explicitly deferred.

## Goals

- Make timeseries show many trajectories together, grouped so shared units share
  a y-axis; filter the (large) observable set by class + search.
- Provide a run-vs-run omics scatter for comparing variants/runs.
- Make the mass Voronoi legible (named cells) and drillable into sub-masses.
- Keep the dependency-light, emitter-aware (SQLite + zarr) architecture; new
  client code in focused files.

## Non-goals (deferred)

- Sim-vs-experiment scatter (needs measured proteome + mapping assets; not in
  this workspace).
- Parquet extraction (run-vs-run pairs that live only in parquet stay invisible
  until parquet read support lands).
- Changes to the Flux (Escher) view.

## Architecture

### Backend — `vivarium_dashboard/lib/explorer_data.py`

**1. Observable enrichment.** Add two pure path-classifier helpers and use them in
both the SQLite and zarr discovery paths so every observable dict carries `unit`
and `mclass`:

- `_unit_for(path) -> str`:
  - contains `_mass` or segment `mass` → `"fg"`
  - `rna_counts` / `monomer_counts` / `bulk[` → `"counts"`
  - `fba_results` or `flux` in path → `"mmol·s⁻¹"`
  - `fraction` / `growth_rate` / `ratio` / `conc` → `""` (dimensionless; `conc`
    may be refined later)
  - default → `""`
- `_mol_class(path) -> str` ∈ {`"RNA"`,`"Protein"`,`"Metabolite"`,`"Flux"`,`"Mass"`,`"Other"`}:
  - `rna_counts` → RNA; `monomer_counts` → Protein; `bulk[` → Metabolite;
    `fba_results`/`flux` → Flux; `mass` → Mass; else Other.

`list_observables` returns each entry as
`{path, index, label, kind, unit, mclass}`. (The existing `categories` grouping
stays for back-compat; the frontend now filters by `mclass`.)

**2. Vector snapshot — `get_vector` + `/api/explorer/vector`.**
`get_vector(db_path, path, step, run_id=None, workspace=None) -> {ids, values, step, time}`:
- zarr: open the datatree, find the leaf named by `path`'s last component, take the
  `generation=*` array at the clamped emit `step`, return `values` across the
  `id_<leaf>` dim and `ids` from that coord (generalizes `_zarr_flux`).
- SQLite: `json_extract` the vector at the step's history row; `ids` from a
  discoverable id list if present, else positional indices `["0","1",...]`.
Handler `/api/explorer/vector?db=&run=&path=&step=` delegates, never raises to the
client (`{ids:[], values:[]}` on failure).

### Frontend — split views out of `explorer.js`

`explorer.js` keeps the controller (mount, run-picker, tab shell, shared helpers
`api`/`j`/`observableOptions`). The four view implementations move to a new
`vivarium_dashboard/static/explorer-views.js` (loaded before `explorer.js` in the
template + standalone page), assigning into `window.Explorer._Views`. This keeps
each file focused as the views grow.

## Views

### Timeseries (rebuilt)
- **Left rail:** a class filter (All · RNA · Protein · Metabolite · Flux · Mass),
  a text **search** box, and the multi-select observable list filtered by
  class+search (so hundreds of bulk species stay manageable). Render options:
  log-y, normalize.
- **Main:** selected observables are grouped by `unit`; render **one stacked
  Plotly subplot per distinct unit**, sharing the x (time) axis (Plotly `grid`
  with rows = number of distinct units, or per-trace `yaxis`/`yaxis2`… with
  stacked `domain`s). Each trace lands in its unit's panel; the panel's y-axis is
  titled with the unit.
- Data via `/api/explorer/series` (unchanged) for the selected paths.

### Scatter (rebuilt → run-vs-run omics)
- **Left rail:** class selector (one of Protein/RNA/Metabolite/Flux), **Run A (x)**
  and **Run B (y)** selectors (both from the run list), a step **slider**
  (default = final step), and a **log-log** toggle.
- **Main:** the class maps to its vector observable
  (Protein→`listeners.monomer_counts`, RNA→`listeners.rna_counts.*` /
  appropriate leaf, Metabolite→a bulk aggregate, Flux→`base_reaction_fluxes`).
  Call `/api/explorer/vector` for run A and run B at the step; join by id (or
  index when ids absent); plot each entity as a point (X=A, Y=B) with a **y=x
  diagonal** reference line; hover shows the entity id. Off-diagonal = divergence.
- Requires ≥2 runs; when only one run exists, show a note ("run-vs-run needs two
  runs").

### Allocation Voronoi (rebuilt)
- Mass-only. A static client mass tree:
  `cell_mass → {protein_mass, rna_mass, dna_mass, smallMolecule_mass, water_mass}`;
  `rna_mass → {rRna_mass, tRna_mass, mRna_mass}`. Intersected with the run's
  available mass observables (omit fields a run doesn't emit).
- **Cells are labeled** with the submass name + % (text at the cell centroid,
  hidden when a cell is too small), value + % on hover.
- **Single-click selects** a cell (highlight). **Double-click drills down** into
  that cell's children when the tree has them and they're present in the run;
  leaf cells don't drill. A **breadcrumb** (`cell ▸ rna`) shows the path and
  navigates back up.
- Time **slider** scrubs the timepoint (re-weights the current level's cells).
- Values via `/api/explorer/series` for the current level's mass-field paths,
  read at the slider's time index (as v1 did).

## Testing

- **Backend (unit):** `_unit_for` and `_mol_class` over representative paths;
  `list_observables` returns `unit`+`mclass` (SQLite + zarr fixtures);
  `get_vector` returns aligned `{ids, values}` at a step (zarr id-coord path and
  SQLite index path), incl. step clamping and missing-path → empty.
- **Frontend:** `node --check` on both JS files; the dashboard serves the page +
  both scripts; visual verification by the user (charts, scatter diagonal,
  Voronoi labels + drill-down).

## Risks

1. **Run-vs-run data sparsity.** Variant pairs largely live in parquet (not read);
   today only single zarr/SQLite runs are explorable, so the scatter may have few
   real pairs to show. The feature is correct; data coverage grows as runs land.
2. **SQLite vector ids.** When a SQLite run doesn't emit the id list for a vector,
   the scatter falls back to positional-index matching — valid only across runs of
   the same composite. Labeled clearly (index vs id).
3. **Unit heuristic accuracy.** Units are inferred from path patterns, not read
   from schemas; a wrong/blank unit just groups an observable into a generic
   panel. A future static unit-registry asset can tighten this.

## Out of scope / future

Sim-vs-experiment omics scatter; parquet extraction; per-schema unit registry;
saving explorer view presets.
