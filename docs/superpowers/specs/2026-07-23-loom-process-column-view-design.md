# bigraph-loom: process-column view, store-affinity clustering, and semantic zoom

**Date:** 2026-07-23
**Status:** design approved, ready for planning
**Scope:** `vivarium_workbench/loom/` (the vendored loom source that the workbench builds)

## Problem

The v2ecoli `baseline` composite renders in loom's Wiring tab as an unreadable hairball. 55 processes with roughly eight ports each produce on the order of 400 wire edges spanning the full canvas. Two distinct failures compound:

- **Browsability.** Processes are hand-placed in a square-ish grid block to the right of the store hierarchy (`layout.ts:180-201`). There is no ordering a reader can exploit, no grouping, and no way to scan the process inventory.
- **Legibility.** Every wire is drawn at all times. No arrangement of 55 process nodes makes 400 crossing edges readable, so fixing position alone does not fix the view.

The fix must be infrastructural rather than a one-off layout hack: loom currently hardcodes a single layout with no seam for alternatives.

## Goals

1. Processes browsable in an ordered, grouped single column to the left of the stores.
2. Groups derived automatically from the composite, with no authoring burden on composite writers.
3. Edge density reduced to something readable by default, with full detail on demand.
4. Process cards that reveal progressively more detail as the user zooms in.
5. A genuine extension seam, so a third and fourth view mode cost little.

## Non-goals

- Reconciling the standalone `bigraph-loom` repo with this vendored copy. They have diverged (the vendored tree is roughly three weeks ahead, carrying `defaultHiddenIds`, `declaredEmitPaths`, collapsible inspector sections, and retuned ELK spacing). Reconciliation is real work and is deliberately deferred; this spec builds in the vendored copy only.
- Changing the composite document format, or asking composite authors to annotate anything.
- Replacing the existing hierarchy layout. It remains the default.

---

## 1. Infrastructure: a layout-mode registry

Today `applyLayout(nodes, edges)` is imported directly at `App.tsx:13` and invoked at exactly two sites — the rebuild effect (`App.tsx:264`) and the `Re-layout` handler (`App.tsx:347`). Node and edge renderers are two hardcoded object literals (`App.tsx:34-35`). There is no registry of any kind.

Introduce one:

```ts
// src/layouts/types.ts
export interface LayoutResult {
  nodes: Node[];
  bands?: GroupBand[];      // cluster headers: label, yStart, yEnd, key store id
}

export interface LayoutMode {
  id: 'hierarchy' | 'process-column' | string;
  label: string;
  run(nodes: Node[], edges: Edge[], ctx: LayoutContext): Promise<LayoutResult>;
  edgeVisibility?(edges: Edge[], focus: FocusContext): Edge[];
  Rail?: React.ComponentType<RailProps>;
  zoomBands?: ZoomBand[];
}
```

`src/layouts/registry.ts` exports the ordered mode list. `App.tsx` holds a `mode` state value and dispatches through the registry at both existing call sites. A segmented control (`Hierarchy | Process column`) sits in the toolbar next to `Re-layout`.

The existing ELK layout moves verbatim into `src/layouts/hierarchy.ts` as the first registry entry. **Its output must not change**; the existing `src/__tests__/layout.test.ts` is the regression gate and is not to be edited except for its import path.

### Rationale

The alternative — a new top-level tab beside `Wiring` — was rejected. It would duplicate toolbar, inspector, and selection wiring, and it splits the user's mental model of "the graph". A mode toggle keeps one graph with two arrangements, and makes the registry itself the deliverable.

---

## 2. Store-affinity clustering

`src/layouts/affinity.ts`, a pure module with no React or React Flow dependency.

### Algorithm

1. **Collect store keys per process.** Every wired path from `inputs` and `outputs`, resolved against the process's parent store path (handling `..` and `.` exactly as `convert.ts:49-57` does), truncated to depth 2 relative to that parent. Track port multiplicity per key.

2. **Filter bookkeeping stores.** Drop keys matching the noise set: `_layer_token*`, `process.*`, `process_state.*`, `request`, `next_update_time`, `pinned_flux_targets`, `timestep`, `global_time`, `allocate.*`, and any leading-underscore key.

   This mirrors, for stores, what `defaultHiddenIds` (`convert.ts:116`) already does for processes. **This step is the single largest correctness lever** — see the validation section below.

3. **Identify hub stores.** A key touched by at least `hubFraction × n` processes is a hub and is disqualified as a cluster key. Default `hubFraction = 0.30`.

4. **Assign each process** to the *most widely shared* non-hub key it touches, breaking ties by port multiplicity then lexically. Processes whose keys are all hubs fall into a terminal bucket labeled by the hubs they do touch (e.g. `bulk · listeners only`), not a bare `~global`.

5. **Divert hub processes.** A process touching more than `hubProcessKeyLimit` distinct non-hub keys (default 8) is not meaningfully "about" any one of them; it goes to a dedicated `cross-cutting` band. In the baseline, `ecoli-chromosome-structure` touches eleven distinct unique stores and would otherwise land wherever the tie-break happened to fall.

6. **Order clusters** by the mean y of their key store from the store-side layout (barycenter), so column order tracks store order and wires stay short. Processes are ordered alphabetically within a cluster.

Deterministic and pure, therefore directly unit-testable.

### Why not IDF, and why not Jaccard

The first prototype scored candidate keys by `ports × log(n / df)` — classic TF-IDF distinctiveness. **This is backwards for this problem.** Rare stores are process-*private* plumbing, not distinctive shared structure. On the real baseline it produced 36 clusters for 46 processes, 27 of them singletons, keyed on `_layer_token_7` and `next_update_time`. The useful signal is *mid-frequency*: stores shared by a meaningful minority.

Jaccard-similarity agglomerative clustering over process store-sets was also prototyped and **rejected on measured results**: 12–14 clusters with 7–8 singletons, and its largest cluster shared no distinctive store at all, so it could not even be labeled. Hub stores dominate set similarity and fragment everything else.

### Validation against the real composite

Run against `v2ecoli/reports/composite-state/v2ecoli.composites.baseline.json` (46 processes, 27 after bookkeeping-process filtering), at `hubFraction = 0.30`. Hubs excluded: `bulk`, `listeners`, `environment`, `unique.RNA`. Result — 9 clusters, 3 singletons:

| Cluster key | Processes | Reads as |
|---|---|---|
| `unique.active_ribosome` | polypeptide-elongation ×2, polypeptide-initiation, rna-degradation ×2, chromosome-structure | translation |
| `boundary` | metabolism, exchange_data, media_update, division | environment / exchange |
| `unique.promoter` | tf-binding, tf-unbinding, transcript-initiation, counts_deriver | transcriptional regulation |
| `unique.active_RNAP` | transcript-elongation ×2 | transcription elongation |
| `unique.full_chromosome` | chromosome-replication, mark_d_period | replication |
| `ppgpp_state`, `shape`, `unique.DnaA_box` | one each | ppGpp, shape, equilibrium |
| hub-only | complexation, protein-degradation, rna-maturation, two-component, emitter, global_clock | bulk chemistry |

The `_requester`/`_evolver` partition pairs group together automatically, with no name matching.

**`hubFraction` sits on a knee.** At 0.25–0.30 translation and transcription separate cleanly. At 0.35–0.40 they collapse into a single eight-member `unique.RNA` cluster. Rather than hardcode a value on the knee, it is exposed as a **granularity slider** in the rail (coarse ↔ fine, mapping to `hubFraction` roughly 0.45 → 0.20), defaulting to 0.30.

**Caveat for implementation:** this fixture dates from 2026-06-26 and contains 46 processes; the current baseline has 55. Thresholds must be re-validated against a freshly generated state before the defaults are locked.

---

## 3. Process-column layout

`src/layouts/processColumn.ts`.

- Processes occupy a single column at `x = 0`, stacked by prefix-sum of per-card height at the current zoom tier, with a 44 px gap and a labeled band between clusters. Within a cluster, cards are separated by 16 px.
- Stores are laid out by the existing hierarchy ELK pass, translated right by `columnWidth + gutter`, where `columnWidth` is the current tier's card width (180 / 220 / 320 px, §5) and `gutter = 180 px`.
- Returns `bands: GroupBand[]` describing each cluster's label and y-range, consumed by both the rail and an optional canvas-side band annotation.

Because the column is one-dimensional, **reflow is a prefix sum, not a graph layout** — O(n), no ELK invocation. This is what makes tier changes (§5) cheap enough to run on every zoom-band crossing at 345 nodes, which the investigation graph's full-re-render approach would not sustain.

---

## 4. Focus and edge visibility

`edgeVisibility` for the process-column mode:

- **Nothing focused (default):** draw only *aggregate* edges — one edge per (cluster → key store), roughly ten edges instead of roughly 400. Place edges (the store hierarchy) are always drawn; they are structural and few.
- **Focused** — hover, click, or pin: that process's real per-port edges at full strength; all other wires dimmed to about 8% opacity.
- Pins accumulate, so two processes' wiring can be compared side by side.

The existing `retargetEdgesToVisible` dedupe (`panels/filterHidden.ts:51`) already collapses multi-port process→store fans to a single edge and is reused for the aggregate pass.

---

## 5. Semantic zoom on process cards

The workbench's investigation graph achieves semantic zoom with three discrete bands and **no `transform: scale()`** — it re-lays-out at band-specific card widths and toggles sections, so text is never scaled to unreadability (`static/aig-graph.js:91-99`, `static/walkthrough.js:6079-6089`). That code is vanilla JS bound to `document.getElementById` and dashboard-only helpers; it is **not importable** into loom's React/TypeScript tree. The *idiom* ports; the code does not.

Loom has React Flow's real continuous zoom, so tiers are driven from `useStore(s => s.transform[2])` inside `ProcessNode.tsx`:

| Tier | Zoom | Card | Content |
|---|---|---|---|
| **far** | < 0.35 | 180×56 | kind-colored bar, name in large type |
| **mid** | 0.35–0.85 | 220×92 | + kind badge, `6 in / 3 out`, interval |
| **near** | > 0.85 | 320 × (120 + ports·14) | + full port list with wire targets, address, description, config key count |

Following the investigation graph's discipline: **font sizes are identical across tiers**. Legibility at low zoom comes from dropping content, never from shrinking text. Thresholds are chosen so effective rendered type stays at or above roughly 9 CSS px. Full detail remains reachable at any tier via the existing Inspector.

Additions:
- A per-card **⤢ pin-expanded** toggle holds one card at `near` regardless of zoom, so details stay readable while zoomed out.
- Tier crossings are debounced (≈120 ms) and hysteretic (bands overlap by ≈0.03 zoom) so cards do not flicker while the user scrolls across a threshold.

Semantic zoom applies to the process-column mode only in this iteration. The hierarchy mode keeps fixed-size cards, since a tier change there would require a full ELK re-run.

---

## 6. Persistence

- `View` (`viewStore.ts:27-33`) gains `mode?: string` and `pins?: string[]`. `normalizeView` (`:138`) defaults absent `mode` to `'hierarchy'`, so every previously saved view and shared `?view=` link keeps working unchanged.
- Layout localStorage keys become mode-scoped: `bigraph-loom:layout:<compositeId>:<mode>`, so hand-dragged positions in one mode do not corrupt the other.
- Viewport zoom remains unstored, per the existing deliberate choice (`viewStore.ts:10`). The semantic-zoom tier is therefore derived, never persisted.

---

## 7. Module boundaries

`App.tsx` is 775 lines and already holds all application state. Adding mode, focus, pins, and tier state inline would make it materially worse. Two hooks are extracted as part of this work:

- `useLayoutMode()` — owns mode selection, registry dispatch, node positions, and band data.
- `useFocus()` — owns hover/selection/pin state and derives edge visibility.

`App.tsx` becomes composition over these. This is targeted at the code being touched; no unrelated refactoring.

**Hazard:** there is a documented `hiddenRef` race at `App.tsx:71-81`, and object-identity preservation in the two `setNodes` reducers (`:273-286`, `:350-362`) that stops React Flow remounting the whole graph on every collapse. Both must be read before editing and preserved.

### Files

New: `layouts/types.ts`, `layouts/registry.ts`, `layouts/hierarchy.ts` (moved), `layouts/affinity.ts`, `layouts/processColumn.ts`, `panels/ProcessRail.tsx`, `hooks/useLayoutMode.ts`, `hooks/useFocus.ts`.

Modified: `App.tsx` (mode/focus wiring, toolbar control), `nodes/ProcessNode.tsx` (tiers), `layoutStore.ts` (mode-scoped keys), `viewStore.ts` (mode + pins), `App.css` (bands, dimming, tiers), `layout.ts` (thin re-export shim for back-compat).

---

## 8. Phasing

The work is large enough that it should land in reviewable increments, each independently useful and each leaving the app working:

- **P1 — registry seam.** `layouts/types.ts`, `registry.ts`, `hierarchy.ts` (moved verbatim), `useLayoutMode()`, toolbar control with a single registered mode. No visible change; the regression gate is that hierarchy output is unchanged.
- **P2 — clustering.** `affinity.ts` plus its tests and the baseline fixture. Pure module, no UI. Validates cluster quality against the current 55-process baseline before anything depends on it.
- **P3 — column layout.** `processColumn.ts` registered as the second mode, fixed-size cards, all edges drawn. First visible result.
- **P4 — focus and edge culling.** `useFocus()`, aggregate edges, dimming, pins. This is where legibility actually arrives.
- **P5 — rail.** `ProcessRail.tsx`, search, scroll-sync, granularity slider.
- **P6 — semantic zoom.** Tiers in `ProcessNode.tsx`, prefix-sum reflow, hysteresis, pin-expanded.

P1 and P2 are independent and can proceed in parallel. P3 depends on both.

## 9. Testing

- `affinity.test.ts` — noise-store filtering, hub exclusion, hub-process diversion, cluster stability, determinism. Includes the explicit regression that unfiltered noise stores produce singleton explosion.
- `processColumn.test.ts` — column geometry, no card overlap at any tier, band y-ranges, prefix-sum reflow correctness.
- `hierarchy.test.ts` — the existing `layout.test.ts`, import path only, as the no-change gate.
- `registry.test.ts` — every registered mode satisfies the interface.
- `semanticZoom.test.tsx` — tier thresholds, hysteresis, pin-expanded override.
- `ProcessRail.test.tsx` — search filtering, click-to-focus, selection scroll-sync, granularity slider.
- `viewStore.test.ts` — a saved view lacking `mode` normalizes to `hierarchy`.

**Fixture:** `src/__tests__/fixtures/v2ecoli-baseline.json`, a freshly generated baseline state (55 processes), serving as both the clustering-quality fixture and a render performance smoke test.

## Success criteria

1. The v2ecoli baseline opens in process-column mode showing under 20 edges by default, versus roughly 400 today.
2. Clusters are recognizable to a modeler as transcription, translation, replication, regulation, environment, and bulk chemistry, without any annotation added to `baseline.py`.
3. Any process is locatable within about two seconds via rail search.
4. Hierarchy mode output is byte-identical to today.
5. Tier changes and mode switches stay visually smooth at 345 nodes.
