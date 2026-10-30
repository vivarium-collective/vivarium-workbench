# Loom adaptive layout policy: shape-aware defaults on first open

**Date:** 2026-10-24
**Status:** draft design (pending user approval) — revised after measuring the real GDM graph
**Repos touched:** `vivarium-workbench` only — the embedded bigraph-loom
(`vivarium_workbench/loom/src`, built to `_dist`). No workspace/schema changes.

## Intent

When a composite with many near-identical children opens in the loom, today's
defaults render it unusably. Motivating case: the GDM composite
(`viva_island_biogeography.composites.gdm.general_dynamic_model`,
`n_islands=3`, `n_pool=60`). With the current defaults (layout `flow-down` = Tree,
collapse-repeats **off**) it lays out as one flat, dim, full-width band far too
small to read (screenshot 1). The user can hand-tune it into something good
(screenshot 2) — switch to a packed layout, turn collapse on, drag the `island`
node up so the hierarchy reads top-down. **Goal: make a fresh open land at that
good state automatically, by reading the graph's shape, without touching the
Layout menu** — and degrade gracefully as shape scales up.

**Why now:** the island-biogeography investigation just published; its GDM /
microbial / neutral composites are the first in the ecosystem with large
repeated-child fan-outs, and they expose that the loom has no node-count-aware
default. The fix belongs in the loom (the shared viewer), not per-workspace.

### Decisions confirmed with the user

- **Adaptive, not a single smarter static default** — read the shape, pick the
  layout + collapse.
- **Decide once at open, then back off** — the policy computes only the *initial*
  view for a fresh composite (no saved view). The **first** Layout-menu (or view)
  interaction ends the policy for that session; it never re-adapts when the graph
  changes under it.
- **Overflow handled by collapsing redundant repeats to `*×N` nodes** (chosen over
  a "+N-more chip" and "always full grid" for being simplest/cleanest/most
  reproducible).

## What the real GDM graph is (measured `@40bd4c38`, via the loom's own convert + collapse + layouts)

Serving the workspace and running `stateToReactFlow` + the collapse passes + each
layout on the actual resolved state gave hard numbers that reshaped this design:

- **Structure:** top-level stores `presence` / `richness` / `area` / `island_meta`,
  each keyed by **island** (`island_00/01/02`); `presence.island_0X` holds 60
  `Species_0..59` scalar leaves. Islands are **processes** `island_00/01/02` (no
  brackets, 2-digit suffix). Totals: **221 raw nodes** (4 processes incl. emitter,
  217 stores; stores by depth 4 / 12 / 201).
- **Default overview today** (`defaultCollapsedIds` minDepth=3 + `defaultHiddenIds`):
  **220 visible**, still including all 201 depth-3 species — the depth collapse does
  *not* tame them.
- **The flat band is the layout:** `treeMode` (today's default) on that graph →
  bbox **41015 × 1052, aspect ≈ 39:1**. That *is* screenshot 1.
- **Species collapse for free:** `collapseRedundantStores` takes **220 → 82 nodes** —
  the 3 islands under each top store are *redundant subtrees*
  (`{Species_0..59}`), so they collapse to one `island_*×3`, leaving 60 species
  shown **once**. No leaf-collapse code is needed. *(The island **processes** do
  NOT collapse — they miss the `INDEXED`/`GENERATED` regexes — but that doesn't
  matter; the store-subtree collapse is what tames the graph.)*
- **Layout choice, on the collapsed 82-node graph:**

  | layout | bbox | aspect | hierarchy (mean-Y depth 1→3) |
  |---|---|---|---|
  | `treeMode` (Tree) | 14305 × 1116 | 12.8:1 (band) | 282 / 246 / 1116 — top-down ✓ |
  | `clusterGridMode` (Packing) | 3918 × 1328 | 2.95:1 | 737 / 770 / 614 — **scrambled** ✗ |
  | **`gridMode` (Grid)** | **3648 × 1802** | **2.02:1** | **82 / 232 / 966 — top-down ✓** |

  **Grid wins on both axes** — most compact *and* clean top-down hierarchy — with
  no force-layout surgery. (Grid even on the *uncollapsed* 220-node overview is
  aspect 1.6, vs Tree's 39.) This is screenshot 2, automatically.

**Consequences for the design:** the two components the first draft proposed are
both **unnecessary** and are dropped:
- ~~Leaf-sibling collapse~~ — redundancy is at the island-subtree level, already
  collapsed; so we never touch the shared `collapseRedundantStores` (this also
  removes the builder-check's "mutating shared collapse changes other composites"
  risk).
- ~~Hierarchy top-bias in clusterGrid~~ — Grid already reads top-down, so no change
  to the force-directed packing layout (removes the builder-check's "fighting the
  layout" risk).

The fix reduces to: **pick the right existing layout + flip the existing collapse
toggle, for the right graphs, on fresh open.**

## Current state (verified `@40bd4c38`)

- **Layout modes** (`loom/src/panels/LayoutMenu.tsx:10-15`): `flow-down` "Tree"
  (`treeMode`), `tree-grid` "Grid" (`gridMode`), `hierarchy` "Packing"
  (`clusterGridMode`), `flow-right` "Flow". `treeMode`/`gridMode` both live in
  `layouts/treeFlow.ts` (`:489`, `:497`).
- **Default mode** = `flow-down` — `layouts/registry.ts:21` (`DEFAULT_MODE_ID`),
  consumed at `hooks/useLayoutMode.ts:26-27`.
- **Collapse-repeats default = OFF** — `App.tsx:1024-1027` (`useState`, on only via
  `?collapse=1`); applied in the `raw` memo `App.tsx:1036-1039`.
- **Fresh-view init** (`App.tsx:1697-1810`, `applyView` `:1628-1688`): a saved view
  (`?view`, `?viewUrl`, localStorage, static snapshot, `/api/composite-default-view`)
  **overrides** fresh defaults; only the **else** branch (no saved view) uses the
  initial state. Explicit URL params apply *after* and win.
- **Build**: `scripts/build_loom.sh` (`tsc -b && vite build` → `_dist`, gitignored;
  a `tsc` error silently keeps the old bundle — confirm it rebuilt). Tests:
  `cd loom && npm run test` (vitest). CI runs Node 20; the DOM/testing-library
  suites fail under this machine's Node 25 (env-only, pre-existing on `main`) — the
  **pure-logic layout suites pass clean** (7 files / 63 tests) and are where this
  work's tests live.

## Architecture

**One pure policy function** seeded into the fresh-view branch. No new collapse
logic, no layout-algorithm changes.

### A. Policy — `loom/src/layoutPolicy.ts` (new)

```
export function chooseInitialView(nodes, edges): { mode: string; collapseRedundant: boolean }
```

Pure, no React/DOM deps, all thresholds in one exported const. Computed from the
already-seeded, default-hidden graph.

- **Signals:** `nNodes` (visible node count); `maxSibFan` (largest group of
  same-parent, same-base siblings — the repeat width); `nEdges`. (Depth is
  available but not needed for the v1 rule.)
- **Decision:**
  - **Large / wide** — `nNodes > BIG_N` (≈40) **or** `maxSibFan > FAN` (≈12):
    `{ mode: 'tree-grid' (Grid), collapseRedundant: true }`.
  - **else:** `{ mode: 'flow-down' (Tree), collapseRedundant: false }` — identical
    to today for small/simple composites.

  GDM (220 nodes) → Grid + collapse → measured aspect 2.02, top-down. A 3-process
  no-repeat composite → Tree + no collapse, unchanged.

Thresholds are my calls (see "Open calls"); they sit far from the data (GDM at 220
vs a cutoff of 40) so they are not knife-edge.

### B. Wire into fresh-view init — `loom/src/App.tsx`

In the **else** branch of the startup-view resolution (no saved view), call
`chooseInitialView(...)` on the seeded graph and apply its `mode` (via
`useLayoutMode`'s setter) and `collapseRedundant` (via `setCollapseRedundant`),
before the auto-fit. A saved view / `?view` / `?collapse` / `?embed` continue to
win exactly as today — the policy is strictly the default branch. "Decide once,
back off" falls out: the policy only sets initial state; the first menu action
replaces it through the normal setters and the policy never runs again.

## Data flow

seed composite → `stateToReactFlow` → default hide → **`chooseInitialView`** (only
if no saved view) → set initial `{mode, collapseRedundant}` → `raw` memo applies
the existing `collapseRedundantProcesses`/`Stores` → chosen layout (Grid) →
auto-fit. First user view action → normal manual path, policy inert.

## Error handling / fallback

`chooseInitialView` is best-effort and must **never render worse than today**: wrap
the call so any throw / unexpected shape falls back to the current static defaults
(`flow-down`, collapse off). Nothing changes on the saved-view, embed, or
URL-param paths, nor for composites below the thresholds.

## Testing

- `loom/src/__tests__/layoutPolicy.test.ts` (new) — table of synthetic shapes →
  expected `{mode, collapseRedundant}`: tiny no-repeat → Tree/off; GDM-like →
  Grid/on; boundary cases around `BIG_N`/`FAN`.
- `loom/src/__tests__/gdmAdaptive.test.ts` (new) — the measured regression guard,
  using a committed GDM fixture (`fixtures/gdm-general-dynamic-model.json`, the
  resolved state captured for this design): assert `chooseInitialView` returns
  `{tree-grid, collapse:true}`, that `collapseRedundantStores` reduces 220→~82, and
  that `gridMode` on the collapsed graph yields **aspect < 4** and shallow-depth
  mean-Y < deep-depth mean-Y (i.e. the flat band is gone and the hierarchy reads
  top-down). This encodes the entire point of the change as a test.
- `useLayoutMode.test.ts` — unchanged default honored when policy declines.
- Manual: serve island-biogeography, open GDM fresh (no saved view) → compact
  Grid, collapsed, hierarchy top-down, no menu-touching; touch Layout → policy
  inert.

## Delivery

One PR to `vivarium-workbench`. Rebuild the loom bundle (`scripts/build_loom.sh`;
confirm `_dist` rebuilt). Pure-logic vitest green locally; full suite green in CI
(Node 20). After merge, republish island-biogeography (and any other read-only
workbench) so its served bundle picks up the new defaults.

## Open calls (my defaults; flag any you'd set differently)

- `BIG_N` ≈ **40**, `FAN` ≈ **12** — when to switch to Grid + collapse. GDM is far
  above (220), so these are comfortable, not knife-edge.
- **Grid (`tree-grid`) over Packing** as the big-graph layout — measured strictly
  better here (compact *and* top-down). Packing stays available in the menu.
- Policy chooses between **Tree and Grid only** (Packing/Flow manual-only), to keep
  it legible.
- With collapse on, the 3 islands collapse to one `island_*×3` representative, so a
  fresh open shows **one** island's 60-species grid (expandable), not all three —
  this is the compact look from screenshot 2.

## Out of scope (YAGNI)

- Leaf-sibling collapse and any change to `collapseRedundant*` (not needed — subtree
  collapse covers the case).
- Any change to the clusterGrid/force layout (Grid already reads top-down).
- Re-adapting live on graph/config change; the "+N more" chip; policy control over
  Flow mode, font scale, or detail tier; per-workspace overrides; any
  workspace-schema or server-API change.
