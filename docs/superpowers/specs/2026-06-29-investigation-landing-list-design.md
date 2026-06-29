# All-Investigations Landing List Declutter — Design

**Date:** 2026-06-29
**Status:** Design — approved, pending spec review
**Repo:** `vivarium-dashboard` (branch `feat/investigation-landing-list`, off `main`)
**Part of:** the 5-pillar streamline. Study-detail page (5 pillars) + the single investigation detail page are done; this is the other investigation surface — the **all-investigations landing list** (the Investigations tab's top level).

## Goal

The landing list is a flat grid of dense cards under a verbose lead. Make it scannable and uniform — group into Active/Closed sections, declutter each card, condense the header, and add a client-side filter — without changing any data or backend.

## Current state (grounded)

SPA-rendered. Skeleton in `templates/index.html.j2` (`#page-investigations`, ~lines 786–796); cards built by `static/walkthrough.js` `_renderInvestigationSets()` (~line 4724), shown via `_showInvestigationList()` (~4710).

- **Header:** `<h2>Investigations</h2>` + an **empty actions `<div>`** (a comment notes the actions moved to the left-rail switcher — dead markup).
- **Lead:** `#investigation-page-lead` — a long paragraph ("All investigations in this repo. Select one to open it — its studies appear in the left rail. Merged investigations are preserved as artifacts; in-progress ones live on their branch/PR.").
- **List:** `#investigations-list` — a CSS grid (`repeat(auto-fit, minmax(360px, 1fr))`). `_renderInvestigationSets()` sorts (archived/closed → bottom, baseline → top, else declaration order) and emits one `investigation-set-card` per investigation. Each card: title + current-branch pill + status pill (or gray "Closed") + an `intent:` subtitle (when author status ≠ effective) + a monospace slug row + first-line description (≤240 chars) + a footer (`N studies · click to open DAG` + ↓report + ↓notebook links). Click → `_openInvestigationDetail(name)`.
- Two hidden modals (New Investigation, Clone) follow; unchanged by this work.
- Data: each item (`window._isetIndex[]`) has `name`, `title`, `description`, `status`, `effective_status`, `current`, `n_studies`.

## Architecture

Two files: restructure `_renderInvestigationSets()` (the card builder) and the `#page-investigations` skeleton. No API/data change.

### ① Group into Active / Closed sections
`_renderInvestigationSets()` partitions the already-sorted list into **Active** (status not archived/closed) and **Closed** (archived/closed). It emits, into `#investigations-list`, for each non-empty group: a section header (`<h3 class="iset-group-head">` with the group label + count, e.g. "Active (7)") followed by a card grid `<div class="investigations-grid">` of that group's cards. Baseline still floats to the top within Active (existing sort). When a group is empty, its header + grid are omitted. (Because section headers now live *inside* `#investigations-list`, the grid CSS moves from `#investigations-list` onto the inner `.investigations-grid` wrappers — see ③.)

### ② Declutter each card
Drop from the card body: the `· click to open DAG` footer text, the standalone monospace slug row, and the separate `intent:` subtitle. Keep: title + current-branch pill + status pill (or "Closed") + first-line description + `N studies` + ↓report/↓notebook. Move the dropped info into `title=` tooltips:
- The card gets `title="<slug>"` (slug still discoverable on hover).
- The status pill gets `title="effective: <eff>  ·  intent: <author>"` when they diverge (same pattern the detail-page status pill uses), else `title="status: <eff>"`.
The card also gains `data-iset-name`, `data-iset-title`, `data-iset-slug`, `data-iset-status` attributes (lowercased) to drive the filter (③).

### ③ Condense header + lead
- Replace the dead empty actions `<div>` with the filter input (④).
- Shorten `#investigation-page-lead` to one line: "Select an investigation to open its study graph."
- Move the grid CSS (`display:grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap:12px`) off `#investigations-list` (now a vertical stack of section blocks) onto a `.investigations-grid` class used by each group's inner wrapper. `#investigations-list` becomes a plain block container.

### ④ Client-side filter
Add `<input id="investigations-filter" type="search" placeholder="Filter investigations…">` in the header (where the dead div was). On `input`, `_filterInvestigations()` reads the lowercased query and, for each `.investigation-set-card`, shows it iff the query is a substring of its `data-iset-title + data-iset-slug + data-iset-status` (empty query → show all). After filtering, each section header's count updates to the visible count and the header+grid hide when zero cards are visible. A "no matches" line shows when the whole list is filtered to empty. The input is wired via `oninput="_filterInvestigations()"`; `_filterInvestigations` is exposed on `window`. `_renderInvestigationSets()` calls `_filterInvestigations()` at the end so a re-render re-applies any active query.

## Data flow
`_showInvestigationList()` → `_renderInvestigationSets()` builds grouped, decluttered cards into `#investigations-list` → `_filterInvestigations()` applies the current `#investigations-filter` value. Typing in the filter re-runs `_filterInvestigations()` only (no re-fetch, no re-render).

## Error handling / compatibility
- Empty index → existing empty-state message (unchanged), shown in `#investigations-list`; the filter input shows but matches nothing.
- A group with zero members → its header/grid omitted.
- Filter with no matches → a "No investigations match." line; clearing the query restores all.
- Click-through, report/notebook links, the New/Clone modals, and `_openInvestigationDetail` are unchanged.
- Slug/intent still available (tooltips) — no information removed, only relocated.

## Testing
- **`tests/test_investigation_landing_list.py`** (assert on source):
  - `index.html.j2`: `#investigations-filter` input exists in `#page-investigations`; the dead empty actions `<div>` is gone; `#investigation-page-lead` no longer contains "preserved as artifacts" (condensed); a `.investigations-grid` CSS rule exists and `#investigations-list` no longer carries the inline `grid-template-columns`.
  - `walkthrough.js`: `_renderInvestigationSets` emits `iset-group-head` and the labels "Active"/"Closed" and a `.investigations-grid` wrapper; card markup no longer contains "click to open DAG"; a `data-iset-status` attribute is emitted; a `function _filterInvestigations` exists and is exposed on `window`; the standalone slug row markup (`font-family:monospace` slug `<div>`) is gone from the card builder.
- **Jinja parse** (`Environment().parse` on `index.html.j2`); **`node --check walkthrough.js`**.
- **Served SPA (you view it):** Investigations tab → Active/Closed sections with counts, decluttered cards, condensed header; typing in the filter narrows cards live and updates counts; clearing restores. (Full visual needs a browser — the SPA renders client-side.)

## Out of scope (later)
- The left-rail investigation switcher / `viv-iset-menu`.
- The New/Clone modals' internals.
- The single investigation detail page (already done) and the study DAG renderer.
- Server-side filtering/pagination (client-side filter is sufficient at current scale).
