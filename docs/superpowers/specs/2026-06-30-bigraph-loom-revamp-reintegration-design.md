# bigraph-loom revamp + reintegration

**Date:** 2026-06-30
**Status:** Approved (design), pending implementation plan
**Repos touched:** `bigraph-loom`, `vivarium-dashboard`
**Supersedes (in part):** `2026-06-28-unified-run-ui-sp-c-design.md` — the native SP-C "Configure & Run" widget is retired here in favor of bigraph-loom's own Configure/Run tabs.

## Problem

The composite configure/run experience is duplicated and hard to find:

- The **composite-explore page** wraps bigraph-loom in an outer tab pair — "Wiring viewer" / "⚙ Configure & Run". The "Configure & Run" tab mounts a *separate* native SP-C widget (`static/configure-run.js`) into `#ce-configure-run`. But the loom iframe inside the "Wiring viewer" tab **already has its own Configure and Run tabs** hitting the same backend. Two configure/run surfaces, side by side.
- Composite **cards** carry two buttons — "Explore" and "Configure & Run" — that open the same page.
- The loom viewer **defaults to the wiring diagram** ("View" tab). Running a simulation is buried behind a tab, and the whole thing reads as a diagram viewer, not a run interface.
- Opening the explorer **from a study page** passes `?static=1`, which strips loom to view-only — so there is *no* configure/run at all from the study page.
- After a run, composites get **visualizations** but **no analyses / report card**, and there is **no way to download** the results from the interface.

## Goals

1. **One viewer, one entry point.** Unify on bigraph-loom. Delete the SP-C widget and the outer tab pair; collapse the card buttons to one.
2. **Run-first.** The viewer opens on a merged **"Setup & Run"** tab. The wiring diagram is demoted to a later tab near Document.
3. **Prettier configure panel.** A grouped, card-styled parameter form with a clear primary Run CTA.
4. **Full post-run flush.** After a run, dispatch analyses + visualizations (+ a lightweight report card) generically for composite runs, shown in the Results / Visualizations tabs.
5. **Downloadable results.** A single "Download results" action producing a zip of the run (store + figures + analyses + report).
6. **Study-page parity.** In a live dashboard, the study-page pop-out opens the full Setup & Run flow; published snapshots stay read-only.

## Non-goals

- Authoring rich, composite-specific analyses for every multiscale-BATS composite (a generic default figure + minimal report card is in scope; bespoke analyses are a follow-up).
- Changing the backend run engine, emitter selection, or store formats.
- Touching the study/investigation run pipelines (`study_run_post.py`, `study_runs.py`) beyond reusing patterns.
- Reworking the published (snapshot) read-only dashboard beyond keeping it read-only.

## Architecture

Three workstreams, coupled around one run flow.

### WS1 — Backend: post-run flush + download (`vivarium-dashboard`)

Current state: `lib/run_runner.py::execute` writes the store to the run dir, then calls `_render_viz()` → `run_dir/viz.json`. No analyses, no report card, no download endpoint. Run dir convention: `workspace_paths().pbg / "runs" / <run_id> /`.

Changes:

1. **Generic post-run flush.** In the post-run block of `run_runner.py::execute` (immediately after the existing `_render_viz` call, ~line 353):
   - Run a **generic analysis dispatch** over the run's store, writing `run_dir/analyses.json`. Analyses are discovered the same way visualizations are (composite decorators / registered `@analysis` functions); when none are declared, the dispatch is a no-op that still writes a valid empty `analyses.json`.
   - Render a **minimal report card** → `run_dir/report.html` summarizing parameters, steps, key observables, and any analyses/figures. Reuse the `ReportCard` model in `lib/models.py` where it fits; keep the composite-run report standalone (not investigation-scoped).
   - Flush is **synchronous inside the detached run process** — it runs after the sim completes, before the run is marked terminal. The existing status endpoint already surfaces `viz_html`; extend the terminal payload to also advertise analyses/report/figure availability so loom can enable the relevant tabs and the Download button.

2. **Generic default visualization.** Add a framework-level default figure — **"emitted observables over time"** — that plots leaf numeric emitted paths from the store. It runs when a composite declares no visualizations, guaranteeing non-empty output (multiscale-BATS composites currently declare none). Composites that declare their own visualizations are unaffected.

3. **Download endpoint.** New `GET /api/composite-run/<run_id>/download` streaming a zip of the run dir — store (zarr/parquet), `viz.json` + rendered figures, `analyses.json`, `report.html` — with `Content-Type: application/zip` and a `run_<id>.zip` attachment name. Mirror the existing `lib/analysis_outputs.py` zip helper used by `/api/study-analysis-zip`.

**Interfaces:**
- `GET /api/composite-run/<id>/status` — extended terminal payload: `{status, ..., viz_html, has_analyses, has_report, downloadable}`.
- `GET /api/composite-run/<id>/download` — `application/zip`.

### WS2 — Loom UI revamp (`bigraph-loom`)

Current state: `src/App.tsx` tab list `['view','configure','run','results','visualizations','document']`, default `'view'`; `?static=1` → `['view']`. Panels in `src/panels/*` use inline styles; global classes in `src/App.css`.

Changes:

1. **`SetupRunPanel.tsx`** — new panel merging `ConfigurePanel.tsx` (186 lines) and `RunPanel.tsx` (233 lines): grouped/card-styled parameter form (units + labels from param decls) → steps input → primary **Run** CTA in a single scroll, with a sticky run/progress bar. On run completion, **auto-switch to the Results tab**. Backend calls are unchanged (`/api/composite-resolve`, `/api/composite-test-run`, `/api/composite-run/<id>/status|trajectory`).
2. **Tab reorder** in `App.tsx`: `['setup', 'results', 'visualizations', 'wiring', 'document']`, default `'setup'`. The former `'view'` tab is relabeled **"Wiring"** and moved to position 4, next to Document. `?static=1` → `['wiring']` only (read-only diagram for published snapshots).
3. **Aesthetic pass.** Introduce a small set of shared classes in `App.css` for the Setup & Run form (card sections, spacing, input styling) rather than growing the inline-style blocks. Keep the existing palette (indigo `#6366f1`, gray scale).
4. **Download button** in `ResultsPanel.tsx` → `GET /api/composite-run/<id>/download`, enabled when the status payload reports `downloadable`.

**Build loop:** editable install; `npm run build` (`tsc -b && vite build`) writes to the committed `bigraph_loom/_dist`, served immediately by the dashboard — no reinstall. **Branch:** fresh `feat/setup-run-revamp` off loom `main` (isolated from the in-flight `feat/configure-choices-dropdown`).

### WS3 — Reintegration (`vivarium-dashboard` templates/JS)

1. **`templates/index.html.j2`** (composite-explore page): remove the outer `.ce-page-tabs` nav ("Wiring viewer" / "Configure & Run") and the `#ce-configure-run` panel; keep only the loom iframe (which now defaults to Setup & Run). Remove `_ceShowPanel`, the SP-C mount call, and `_ceScrollToConfigure` wiring.
2. **Retire SP-C.** Remove references to `static/configure-run.js` and delete the file (its behavior is fully covered by loom's Setup & Run).
3. **Composite cards** (`static/walkthrough.js`, grid + list views): a **single** button (`_openCompositeExplorer`). Delete `_openCompositeConfigureRun`. Apply to the investigation-embedded composite cards too if present.
4. **Study-page pop-out** (`static/study-detail.js::_openCompositeLoom`): in **live** mode open `/bigraph-loom/index.html?id=<composite>` (full Setup & Run, no `static=1`); in **snapshot** mode keep `?static=1&stateUrl=...` (read-only). Gate on `window.__DASH_CONFIG__.mode === 'snapshot'`.

## Data flow (run + flush + download)

```
Setup & Run tab
  → POST /api/composite-test-run {composite, overrides, steps}
  → detached: run-composite → store in .pbg/runs/<id>/
       → _render_viz() → viz.json  (+ generic default figure if none declared)
       → analysis dispatch → analyses.json
       → report card → report.html
  → GET /api/composite-run/<id>/status  (poll) → terminal: viz_html + has_analyses/has_report/downloadable
  → loom auto-switches to Results; Visualizations tab shows figures
  → Download → GET /api/composite-run/<id>/download → run_<id>.zip
```

## Sequencing

- **WS1** (backend) and **WS2** (loom frontend) are independent → build in parallel.
- **WS3** (glue) depends on both: it needs loom serving the new tabs and the download endpoint live.

## Error handling

- **Flush failure** (analysis or report render throws): the run is still marked complete; the flush error is logged to the run log and surfaced in the status payload (`has_analyses=false`, an `analysis_error` note). A failed flush never fails the sim.
- **Empty declarations:** no visualizations/analyses declared → generic default figure renders; `analyses.json` is a valid empty document. Output is never empty.
- **Download before completion:** endpoint returns `409` while the run is non-terminal; loom disables the button until `downloadable`.
- **Snapshot mode:** no backend → loom stays on the Wiring tab (`static=1`); Download and Run are absent by construction.

## Testing

- **WS1:** unit tests — flush writes `analyses.json` + `report.html`; download zip contains store + figures + analyses + report; flush failure doesn't fail the run; generic default figure renders for a composite with no declared viz.
- **WS2:** build loom; load `?id=bats_fba` — Setup & Run is default, Run executes, Results/Visualizations populate, Download yields a zip; `?static=1` shows Wiring only.
- **WS3 / integration (multiscale-BATS dashboard):** card → single button → Setup & Run; run `bats_fba` end-to-end → results + figures + download; study-page live pop-out shows Setup & Run; published snapshot stays read-only.

## Risks / notes

- **Content gap:** multiscale-BATS composites declare no viz/analyses; the generic default figure is what makes the flush visibly work. Richer per-composite analyses are a deliberate follow-up.
- **Editable-install coupling:** `vivarium-dashboard` is `-e` from `/Users/eranagmon/code/vivarium-dashboard`; branching in place points running dashboards at this branch. Acceptable for dev; verify no other session depends on main mid-implementation.
- **Two-repo merge:** loom changes must land/build before WS3 integration testing is meaningful. Merge order: loom → dashboard (WS1+WS3).
