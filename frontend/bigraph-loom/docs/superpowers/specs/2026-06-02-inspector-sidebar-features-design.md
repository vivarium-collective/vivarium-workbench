# loom-explore View-tab features: movable sidebar, node/process toggles, process description

**Date:** 2026-06-02
**Repo:** `bigraph-loom-explore` (branch `feat/inspector-sidebar-features`)
**Status:** design approved; spec under review

## Summary

Bring three UX patterns from **bigraph-viz2** into the loom-explore "View" tab (the
React Flow + ELK bigraph viewer embedded in the vivarium-dashboard Composite
Explorer):

1. A **movable (drag-to-resize) + collapsible sidebar** with tabs, replacing the
   current top-right overlay inspector.
2. **Toggle processes / nodes (stores) on/off** via show-hide checkboxes.
3. A **process "description"** section in the node inspector.

bigraph-viz2 (`/Users/eranagmon/code/bigraph-viz2`) is the visual reference; it is a
vanilla-TS/SVG library, so we port the *patterns*, not the code. loom-explore is
React + React Flow, so the implementation is idiomatic React state + components.

## Context (current state)

- View tab lives in `src/App.tsx`; graph rendered with React Flow (`@xyflow/react`)
  + ELK layout (`src/layout.ts`). Nodes: `src/nodes/ProcessNode.tsx`,
  `src/nodes/StoreNode.tsx`. State→graph in `src/convert.ts` (`stateToReactFlow`).
- Inspector today is a **floating overlay** (`src/panels/InspectorPanel.tsx`,
  `position:absolute; top:8; right:8`). It shows node kind + dotted path + raw
  `details` JSON + (for stores) an emit toggle. **No description, no process/node
  show-hide, no movable sidebar.**
- Existing collapse: double-click a group store toggles collapse (`App.tsx`,
  `StoreNode.tsx`). Layout positions persist to `localStorage` (`layoutStore.ts`).
- Dashboard ↔ iframe via postMessage: `composite:load` (in) carries
  `{ state, parameters?, overrides?, default_n_steps?, metadata }`; loom-explore
  emits `explore:ready`, `explore:inspect`, `explore:emit-changed`,
  `explore:run-complete` (`src/api.ts`). `api.ts` already declares an optional
  `description?: string` field — partial plumbing to build on.
- Build: `npm run build` (`tsc -b && vite build`) → `dist/` (`base: './'`). The
  dashboard ships the built bundle at
  `vivarium-dashboard/vivarium_dashboard/static/loom-explore/`; deploy = copy
  `dist/*` there and restart the dashboard.
- Tests: `vitest run` (unit, with `@testing-library/react`), `playwright` (e2e).

## Feature 1 — Movable, collapsible sidebar with tabs

**Goal:** dock the inspector as a right-hand sidebar that the user can drag-resize
and collapse, holding three tabs: **Inspector | Processes | Nodes**.

**Design:**
- New `src/panels/Sidebar.tsx` wrapping the panel: a flex column with a header
  (tab buttons + collapse caret) and a scrollable body. A left-edge **resize
  handle** (`div`, `cursor:col-resize`) with mouse-drag handlers porting
  bigraph-viz2's pattern (`bigraph-viz2/js/src/index.ts:91–107`): width clamped to
  **[200, 760] px**, applied as inline width.
- Collapse: a caret button toggles a collapsed state (sidebar shrinks to a thin
  rail; a click re-expands). 
- **Persistence:** sidebar width + collapsed + active tab persist to
  `localStorage` (reuse the `layoutStore.ts` pattern; new keys
  `loom.sidebar.width`, `loom.sidebar.collapsed`, `loom.sidebar.tab`).
- Layout: the View becomes a flex row — React Flow canvas `flex:1`, sidebar
  `flex:0 0 <width>`. This replaces the absolute-positioned overlay so the canvas
  no longer renders *under* the panel.

**Out of scope (YAGNI):** dockable/repositionable (left/float) sidebar — bigraph-viz2
is right-only too; resize + collapse is the ask.

## Feature 2 — Toggle processes / nodes on/off

**Goal:** the Processes and Nodes tabs each list their items with a show-hide
checkbox; hidden items disappear from the canvas.

**Design:**
- New React state `hidden: Set<string>` (node ids) in `App.tsx`, lifted so both the
  sidebar tabs and the graph read it. A `toggleHidden(id)` setter.
- **Processes tab** lists all process nodes (id, label, kind badge); **Nodes tab**
  lists all store nodes (dotted path). Each row: a checkbox (checked = visible) +
  the name. Mirrors bigraph-viz2 `panel/render.ts:147–205`.
- **Filtering:** in `convert.ts`/`App.tsx`, after building React Flow nodes+edges,
  drop nodes whose id ∈ `hidden` and any edge touching them, *before* ELK layout
  (so layout reflows). Mirrors bigraph-viz2 `layout/index.ts` hidden handling.
- Hidden set persists to `localStorage` (`loom.hidden.<compositeId>`), keyed by
  composite so different composites keep independent visibility.
- Interplay with existing collapse: collapse (double-click) and hide are
  independent; a collapsed store's children are already removed by collapse, hide
  removes the node itself.
- "Show all" affordance: a small link at the top of each tab clears that tab's
  hidden items.

## Feature 3 — Process description in the inspector

**Goal:** the Inspector tab shows a **Description** section for a selected process.

**Design (data flow):**
- **Source decision (to confirm first thing in implementation):** where the
  description comes from. Two candidates, in priority order:
  1. The composite spec / process-bigraph node already carries a doc field
     (e.g. `doc` / `_doc` / `description`) — if so, surface it (cheapest).
  2. If not present, add a small **backend** step in the dashboard's
     `composite:load` payload builder (`vivarium-dashboard/.../server.py`) to pull
     each process's class docstring (`type(process).__doc__` / registry metadata)
     into a per-process `description`. (bigraph-viz2 uses the spec's `doc` key,
     `bigraph-viz2/js/src/normalize.ts:122` → `inspector/render.ts:37–39`.)
- **Plumbing:** `convert.ts` attaches `description` to the React Flow node's `data`
  (the `details`/data object the inspector reads). `InspectorPanel.tsx` renders a
  `Description` block (label + `<pre>`/prose) when present, above the raw JSON.
- The `explore:inspect` postMessage payload (`api.ts`) gains `description?` on the
  detail (the field already exists in the type) so the parent dashboard can use it
  too if desired.

## Build / deploy

1. Implement on `feat/inspector-sidebar-features`.
2. `npm run build` → `dist/`.
3. Copy `dist/*` → `vivarium-dashboard/vivarium_dashboard/static/loom-explore/`
   (the dashboard's committed bundle). Restart the dashboard
   (`python -m pbg_superpowers.dashboard restart`).
4. The backend description step (if needed, Feature 3 path 2) is a separate small
   change in the **vivarium-dashboard** repo — note it carries another session's
   uncommitted WIP (see prior commits), so commit only the specific file(s) touched.

## Testing

- **vitest unit:**
  - `hidden`-set filter: nodes/edges with hidden ids are dropped (pure function).
  - sidebar width clamp [200,760] + persistence read/write.
  - InspectorPanel renders a Description block when `description` present, omits it
    when absent.
- **playwright e2e (optional, if quick):** toggle a process off → it leaves the
  canvas; resize the sidebar → width persists across reload.
- Dashboard side: existing `tests/test_composite_explorer_api.py` covers the
  `composite:load` payload; extend if the backend description step is added.

## Risks / open items

- **Description source** (Feature 3) — confirmed during implementation; may add a
  small dashboard backend change. Everything else is loom-explore-only.
- **Bundle staleness** — the dashboard ships a *built* bundle; forgetting to copy
  `dist/` leaves the live View tab unchanged. The deploy step is explicit above.
- Keep the three features independently shippable: Sidebar (1) is the container,
  but 2 and 3 can land inside the existing panel first if we choose to descope the
  movable sidebar to a fast-follow.
