# Investigation Graph — Semantic Zoom + Status Click-through — Design

**Date:** 2026-07-21
**Repo:** vivarium-workbench (`/code/vivarium-dashboard`)
**Worktree/branch:** `/code/vdash-graph-zoom` on `feat/investigation-graph-semantic-zoom` (off `origin/main`)
**File:** `vivarium_workbench/static/walkthrough.js` (graph renderer ~L5700) + `style.css`

## Problem

The Investigation graph (`iset-dag-node` cards laid out in DAG columns + SVG edges) renders every study card at one fixed detail level, and there is no zoom. Two gaps:

1. You can't zoom out to grasp the whole DAG, nor zoom in for progressively more detail — the card either fits or it doesn't, and long investigations overflow.
2. A node's status badge (e.g. "Investigating") states a conclusion but gives no path to *why*. The reason (finding + evidence + verdict) exists on the study but isn't reachable from the badge.

## Goal

Add **semantic zoom** (geometric scale + level-of-detail) driven by a slider and mouse-wheel, and make the **status badge a click-through** to the study's finding/evidence.

Non-goals: no graph-library swap (stays DOM cards + SVG edges); no backend change (the graph API already emits study nodes + typed evidence-chain nodes/edges + gate verdicts); no confidence-vs-verdict badge reconciliation (deferred — the badge keeps using the study's `confidence` field, which wins over the derived gate verdict today).

## Interaction

### Zoom input
- A **zoom slider** in the graph header (left of / beside the "Investigation graph" title), range covering the three bands.
- **Mouse-wheel / trackpad-scroll over the graph area zooms** (centered on the cursor), and updates the slider. While the pointer is over the graph, wheel events are captured for zoom (`preventDefault`) rather than scrolling the page; outside the graph, scrolling is normal.
- Slider and wheel write the same `aigBand` state; both re-render.

### Three level-of-detail bands
Zoom is **three discrete bands**. The slider/wheel selects a band (`far` → `mid` → `near`); each is a class on the graph container (`aig-zoom-far` / `aig-zoom-mid` / `aig-zoom-near`) that shows/hides card sections, **plus** a recompute of the layout constants so columns re-pack at the band's card size.

| Band | Card content | `CARD_W` | detail sections visible |
|---|---|---|---|
| **far** (overview) | icon + title + status badge | narrow (~150px) | title row only |
| **mid** | + Asks + Finds (clamped) | medium (~280px) | title, `asks`, `finds` |
| **near** | + evidence for/against + test runs | wide (~340px) | title, asks, finds, `_chainBlockHtml` (evidence/decision/conclusion chain + verdict rows), follow-ups |

Text is **never scaled to unreadability**: geometry is achieved purely by re-laying-out at band-specific `CARD_W`/`X_GAP` and toggling sections — **no `transform: scale()` on cards or text**. The "zoom out shows the whole graph" effect comes from the *far* band packing many small (title-only) cards into tight columns; "zoom in" grows card width and adds sections. Zoom is thus **three discrete bands** (the slider/wheel moves between them with snapping; there is no blurry continuous pixel-scale). Edges (SVG) are redrawn whenever the layout recomputes.

### Status → reason click-through
- The status badge (`<span>…confidence…</span>`) becomes a focusable button with its own click handler (`event.stopPropagation()` so it doesn't trigger the node's quick-look).
- Clicking it opens the study **at its finding/evidence**: reuse `_openInvestigationDrawer('study', s)` then scroll the drawer to the Findings/Evidence section (add an anchor/`scrollIntoView` target), OR if no drawer, `_openStudyInsideInvestigation(s.name)` deep-linked to the findings section. The node body keeps its existing single-click quick-look and double-click open-study behavior.

## Architecture / implementation shape

- **State:** a module-scoped `aigBand` (0=far, 1=mid, 2=near) held next to the graph render; a `_setAigBand(b)` that clamps to 0..2, sets the container band class, recomputes layout constants, re-runs the existing Pass-1/Pass-2 layout + edge draw, and syncs the slider value.
- **Layout reuse:** the current renderer already does Pass 1 (build cards at column x, measure) and Pass 2 (stack per column, center) and an edge pass. `_setAigBand` changes `CARD_W`/`X_GAP` (and which sections each card includes) and re-invokes that pipeline. Factor the current inline layout into a `_layoutAigGraph(opts)` so zoom just calls it with band opts.
- **Wheel capture:** a `wheel` listener on the graph container with `{passive:false}` that maps `deltaY` → zoom delta, `preventDefault()`s, and calls `_setAigBand`. A `pointerenter/leave` guard scopes capture to the graph.
- **Slider:** an `<input type="range">` in the header wired to `_setAigBand`; `_setAigBand` also writes the slider value so wheel and slider stay in sync.
- **Badge button:** in card build, wrap the confidence badge in a clickable span with `data-study` and a handler that opens the finding/evidence.

## Testing

The renderer is DOM-string + measurement, so tests are light and behavioral:
- Unit: `_layoutOptsForBand(band)` returns the documented `CARD_W` + section flags for each of far/mid/near; `_setAigBand` clamps out-of-range band indices to 0..2.
- DOM (jsdom, matching existing `tests/` style): rendering at band=far yields cards with the title but no `Asks:`/`Finds:` nodes; band=mid adds Asks/Finds; band=near adds the chain block. A badge click dispatches the finding/evidence open (spy on `_openInvestigationDrawer`).
- Manual: serve the workbench from this worktree, open the v2ecoli-vecoli-comparison investigation, verify wheel+slider zoom, band detail transitions, edge redraw, and badge → finding.

## Risks / notes

- Wheel-capture over the graph hijacks page scroll in that region; scope it tightly (pointer-over-graph only) and keep the slider as the always-available fallback.
- Re-layout per zoom step must stay cheap (cards are ~10–50 nodes); measure-and-restack is O(n) and already runs once — fine at interactive rates. Debounce wheel if needed.
- Shared-checkout hazard: build only in this worktree; to preview live, serve the workbench from this worktree (not the shared `/code/vivarium-dashboard`).
- The badge still shows the `confidence` field (not the verdict) by design; the click-through makes the reason reachable. A follow-up could reconcile badge↔verdict.
