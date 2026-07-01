# Read-only Composite Explorer tabs + Studies sidebar scoping — Design

**Date:** 2026-07-01
**Status:** Approved (design), pending spec review

Two independent UX improvements to the vivarium-dashboard experience, delivered
as separate workstreams in separate repos:

- **WS-A (bigraph-loom):** the read-only (snapshot) Composite Explorer should
  show the full **Setup & Run / Results / Visualizations / Wiring / Document**
  tab set instead of the wiring diagram only.
- **WS-B (vivarium-dashboard):** the STUDIES sidebar should scope to a single
  chosen investigation instead of listing every investigation plus a large
  "Ungrouped" bucket.

They share no code and can be built and merged in either order.

---

## Background / root causes (from investigation)

**WS-A.** In snapshot mode the dashboard embeds loom as
`/bigraph-loom/index.html?static=1&stateUrl=/api/composite-state/<id>.json`.
Loom's `App.tsx` restricts the tab set to `['wiring']` and hides the tab strip
whenever `?static=1` (`App.tsx:556-558`, `589-591`), so only the wiring diagram
shows. The static loader (`App.tsx:165-191`) fetches the `stateUrl` JSON but
keeps **only** `data.state`, discarding the rest of the payload.

Crucially, the published `api/composite-state/<id>.json` is the **full**
`resolve_composite()` dict (`publish.py:734-791` → `_composite_resolve_data`),
which already contains `parameters` (schema: `type`/`default`/`description`),
`default_n_steps`, and `state` (`lib/composite_resolve.py:147-155`). So the
Setup & Run configure form has a complete data source in the snapshot **with no
publish-side change** — loom simply throws the data away today.

Composite runs (`.pbg/composite-runs.db`, trajectory rows, run-level
`viz_html`) are **not** exported to the snapshot (confirmed: no `composite-run`
references in `publish.py`). So Results/Visualizations have no data source in
read-only mode and must render as empty states.

**WS-B.** Study→investigation grouping is one-way: each `investigation.yaml`
declares `studies: [slug, ...]` (`lib/investigation_status.py:156,172`). Studies
listed in no investigation become the "Ungrouped" bucket
(`walkthrough.js:11809-11827`). The 31 orphans are a **data gap**, not a
rendering bug. A filter-to-current-investigation already exists
(`walkthrough.js:11829-11845`) but never activates because
`window._currentIsetSlug` is only set when the user opens an investigation
detail (`walkthrough.js:5213`), never on page load.
`/api/investigation-summaries` already carries a `current` flag per investigation
(`investigation_status.py:174`).

---

## WS-A — Read-only Composite Explorer tabs (bigraph-loom)

### Behavior

In static mode (`?static=1`):

1. Render the **full tab strip** and default to **Setup & Run** (not `wiring`).
2. `SetupRunPanel` renders the configure form from the published `parameters`
   /`overrides`/`default_n_steps`, but in a **read-only** posture: the **Run**
   and **Preview-wiring** buttons are disabled, with a short note that running
   requires a live dashboard.
3. **Results** and **Visualizations** render **empty-state** panels with a
   read-only-aware message (e.g. "No runs in the read-only mirror — run in a
   live dashboard to generate results"). No data is fetched.
4. **Wiring** and **Document** are unchanged (they already work from `state`).

Live (non-static) mode is unchanged in every respect.

### Components / changes

- **`src/App.tsx`**
  - Tab set: in static mode use the full list
    `['setup','results','visualizations','wiring','document']` (remove the
    `STATIC ? ['wiring']` restriction). Show the tab strip in static mode
    (remove the `display: STATIC ? 'none'` on the `<nav>`).
  - Default tab: `'setup'` in both static and live (drop the static→`'wiring'`
    special case).
  - Static loader (`App.tsx:165-191`): after fetching the `stateUrl` JSON, in
    addition to `state`, read `parameters`, `overrides`, `default_n_steps`, and
    `metadata` from the same object (tolerating their absence) and seed the
    corresponding state setters — mirroring the fields the live `composite:load`
    postMessage path already sets (`App.tsx:131-159`).
  - Thread a `readOnly` value (derived from `STATIC`) into `SetupRunPanel`.
- **`src/panels/SetupRunPanel.tsx`**
  - New optional prop `readOnly?: boolean`. When true: render the parameter
    form (values visible), **disable** the Run and Preview-wiring buttons, and
    show a one-line "requires a live dashboard" note. No calls to
    `/api/composite-resolve`, `/api/composite-test-run`, or status/trajectory
    endpoints are made in read-only mode.
- **`src/panels/ResultsPanel.tsx` / `src/panels/VisualizationsPanel.tsx`**
  - These already show empty states when `trajectory`/`vizHtml` is null
    (`ResultsPanel.tsx:121`, `VisualizationsPanel.tsx:19`). Add a `readOnly`
    (or reuse `hasRun===false` + a read-only flag) so the empty message reads
    as an intentional read-only state rather than "loading".

### Data flow

Snapshot: `api/composite-state/<id>.json` (already published, unchanged) →
loom static loader parses `state` + `parameters` + `default_n_steps` →
Setup & Run form (read-only) + Wiring + Document render; Results/Visualizations
show empty states.

### Out of scope (WS-A)

- Publishing composite run artifacts (trajectory / viz_html) to the snapshot.
- Wiring composite Results to study charts. (Possible future enhancement; the
  data source would be `api/study-charts/<slug>.json`.)

### Delivery / release path

1. Change loom source, run `npm run build` (rebuilds the committed `_dist`),
   `npm test`.
2. PR to bigraph-loom `main`, merge.
3. The published v2ecoli read-only dashboard picks it up automatically via the
   `bigraph-loom@main` force-reinstall now in `publish-dashboard.yml`. Live
   dashboards pick it up on a bigraph-loom pin bump.

---

## WS-B — Studies sidebar scoping (vivarium-dashboard)

### Behavior

The STUDIES rail shows **one investigation at a time**:

1. If a current investigation is set → show **only** that investigation's
   studies (flat list under its title). No "Ungrouped" bucket is rendered.
2. If **no** current investigation is set → show a **"Choose an investigation"**
   placeholder (no study list, no Ungrouped bucket).
3. A small **investigation picker** in the STUDIES section header lets the user
   choose/switch. Selection sets `window._currentIsetSlug`, is **persisted**
   (localStorage, keyed per workspace), and re-renders the rail. Opening an
   investigation detail continues to set the current investigation too.
4. On load, the current investigation is resolved as: persisted selection →
   else the `current` flag from `/api/investigation-summaries` (when the branch
   maps to one) → else none (show the chooser).

### Components / changes

- **`static/walkthrough.js`**
  - `_renderRailInvestigationGroups()` (`~11788`): when `_currentIsetSlug` is
    empty, render the "Choose an investigation" placeholder instead of the
    all-groups + Ungrouped list. When set, render only the current
    investigation's studies (the existing filter at `11829-11845` already
    reduces `groups` to the current one; the change is the empty-current branch
    + never appending the `__ungrouped__` group in this scoped view).
  - Add the header picker (a `<select>` of investigation names from
    `window._isetIndex`, default option "Choose an investigation…"). Its
    `onchange` sets `_currentIsetSlug`, writes localStorage, and calls
    `_renderRailInvestigationGroups()`.
  - On initial sidebar data load (`~3395-3410`, after `_isetIndex` is
    populated): set `_currentIsetSlug` from localStorage, else from the iset
    whose `current` flag is true, else leave empty.
  - Persistence key includes the workspace identity so different workspaces
    don't cross-contaminate the remembered selection.

### Data flow

`/api/investigations` + `/api/investigation-summaries` (unchanged payloads;
`summaries` already carries `studies[]` and `current`) → `_isetIndex` →
`_renderRailInvestigationGroups()` renders the picker + the scoped list or the
placeholder. Identical in live and snapshot modes (the data-source URL helpers
already branch by mode).

### Out of scope (WS-B)

- Editing `investigation.yaml` / `study.yaml` to associate the 31 orphans
  (display-only fix chosen). No schema change; no reverse `investigation:`
  field.

---

## Testing

**WS-A (bigraph-loom, vitest):**
- Static loader parses `parameters`/`default_n_steps` from a `{state, parameters,
  default_n_steps}` JSON fixture and seeds the form.
- Static mode exposes all five tabs and defaults to `setup`.
- `SetupRunPanel` with `readOnly` disables Run + Preview and makes no fetch
  calls; renders the form values.
- Results/Visualizations render the read-only empty state when data is null.
- Live mode regression: tab set + default unchanged; postMessage path still
  seeds params.

**WS-B (vivarium-dashboard):** the sidebar render is vanilla JS in
`walkthrough.js` with no unit harness; verify via a focused DOM-logic test if a
seam exists, otherwise a scripted manual check against a fixture workspace:
- No current → placeholder shown, no study rows, no "Ungrouped".
- Current set → only that investigation's studies; picker reflects selection.
- Selection persists across reload (localStorage), scoped per workspace.

---

## Global constraints

- WS-A must not change live (non-static) behavior: same tabs, same default
  (`setup`), same postMessage seeding.
- WS-A requires **no** change to `publish.py` or the composite-state JSON
  contract — the fields consumed already exist in the published payload.
- WS-B is **display-only**: no `.yaml` edits, no backend payload changes.
- The read-only Run/Preview affordances must be visibly disabled (not hidden)
  with a one-line reason, so the UI is self-explanatory.
