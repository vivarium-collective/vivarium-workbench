# bigraph-loom Process-Column View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give bigraph-loom a pluggable layout-mode registry, then use it to add a process-column view with store-affinity clustering, focus-driven edge culling, a searchable rail, and five-tier semantic zoom across processes, stores, and edges — so the v2ecoli baseline composite becomes readable.

**Architecture:** A `LayoutMode` registry replaces the single hardcoded `applyLayout` import. The existing ELK layout moves into the registry unchanged as `hierarchy`. A second mode, `process-column`, stacks processes in one clustered column left of the stores. Clustering is a pure module deriving groups from wiring. Focus state culls edges. The zoom tier is a property of the **view**, so one `ZoomTierId` drives process cards, store circles, and edge labels together; because the column is one-dimensional, a tier change is a prefix-sum reflow rather than a graph re-layout.

**Companion plan:** `docs/superpowers/plans/2026-07-23-process-contract-and-config.md` implements spec §5's `ProcessContract` in process-bigraph and §7's config fixes in v2ecoli and the workbench. This plan does not depend on it — contracts are derived from docstrings and rows with no data are omitted — but the `contract` and `full` tiers get materially richer once it lands.

**Tech Stack:** React 18, TypeScript 5.6, `@xyflow/react` 12.4 (React Flow), `elkjs` 0.11, Vite 6, Vitest 3, `@testing-library/react` 16.

**Spec:** `docs/superpowers/specs/2026-07-23-loom-process-column-view-design.md`

## Global Constraints

- All work happens in `vivarium_workbench/loom/` — the **vendored** loom source the workbench builds. Do **not** edit `/Users/eranagmon/code/bigraph-loom`; it has diverged and is out of scope.
- All commands run from `vivarium_workbench/loom/` unless stated. Test command is `npm test` (vitest). Build is `npm run build` (`tsc -b && vite build`).
- **The `hierarchy` mode's output must not change.** `src/__tests__/layout.test.ts` is the regression gate; edit only its import path.
- Two hazards in `src/App.tsx` must be preserved, not "cleaned up":
  - the `hiddenRef` race documented at `App.tsx:71-81`
  - object-identity preservation in the `setNodes` reducers at `App.tsx:273-286` and `App.tsx:350-362`, which stops React Flow remounting the entire graph on every collapse
- Node sizes are currently duplicated in three places (`layout.ts:36-37`, `layout.ts:188`, `App.tsx:45-46`). Do not add a fourth; new sizes live in the tier table (Task 8).
- Never derive a process count from the composite's `description` string. `v2ecoli/composites/baseline.py:526` hardcodes "55-process" while the document contains 46. Count by walking the state.
- Commit after every task. Use `feat(loom):` / `test(loom):` / `refactor(loom):` prefixes.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `src/layouts/types.ts` | Interfaces only: `LayoutMode`, `LayoutResult`, `GroupBand`, `LayoutContext`, `FocusContext`, `ZoomTier` |
| `src/layouts/registry.ts` | Ordered list of registered modes + `getMode(id)` lookup |
| `src/layouts/hierarchy.ts` | Existing ELK layout, moved verbatim, wrapped as a `LayoutMode` |
| `src/layouts/affinity.ts` | Pure store-affinity clustering. No React, no React Flow imports beyond the `Node` type |
| `src/layouts/processColumn.ts` | The column layout mode |
| `src/hooks/useLayoutMode.ts` | Owns mode selection, registry dispatch, positions, bands |
| `src/hooks/useFocus.ts` | Owns hover/selection/pin state, derives visible edges |
| `src/panels/ProcessRail.tsx` | Searchable clustered rail with granularity slider and scroll-sync |
| `src/contract.ts` | Pure: contract derivation from docstring, type abbreviation, completeness |
| `src/storeFacts.ts` | Pure: reader/writer derivation from the wire graph |
| `src/__tests__/fixtures/v2ecoli-baseline.json` | Real composite state, the clustering-quality fixture |

**Modified:** `src/App.tsx`, `src/nodes/ProcessNode.tsx`, `src/nodes/StoreNode.tsx`, `src/edges/FloatingStoreEdge.tsx`, `src/convert.ts`, `src/layoutStore.ts`, `src/viewStore.ts`, `src/types.ts`, `src/App.css`, `src/layout.ts` (becomes a re-export shim).

---

## Task 1: Layout-mode registry and the hierarchy mode

**Files:**
- Create: `src/layouts/types.ts`, `src/layouts/registry.ts`, `src/layouts/hierarchy.ts`
- Modify: `src/layout.ts` (reduce to re-export shim)
- Test: `src/__tests__/registry.test.ts`, `src/__tests__/layout.test.ts` (import path only)

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `LayoutMode`, `LayoutResult`, `GroupBand`, `LayoutContext`, `FocusContext`, `ZoomTier`, `ZoomTierId` from `src/layouts/types.ts`; `LAYOUT_MODES: LayoutMode[]`, `getMode(id: string): LayoutMode`, `DEFAULT_MODE_ID = 'hierarchy'` from `src/layouts/registry.ts`; `hierarchyMode: LayoutMode` from `src/layouts/hierarchy.ts`

- [ ] **Step 1: Write `src/layouts/types.ts`**

This file is interfaces only — no logic, so no test of its own. It is exercised by every later test.

```ts
// src/layouts/types.ts — the layout-mode extension seam.
//
// A LayoutMode owns how a composite is arranged: node positions, which
// edges are worth drawing, and (optionally) how process cards change with
// zoom. Registering a mode is the only thing needed to add a new view.

import type { Node, Edge } from '@xyflow/react';

/** A labeled horizontal band grouping consecutive column entries. */
export interface GroupBand {
  /** Cluster key, e.g. 'unique.active_ribosome'. Stable id. */
  key: string;
  /** Human-facing label rendered in the rail and on canvas. */
  label: string;
  /** Vertical extent in flow coordinates. */
  yStart: number;
  yEnd: number;
  /** Node id of the store this cluster is keyed on, if it is a real store. */
  keyStoreId: string | null;
  /** Node ids in this band, in render order. */
  nodeIds: string[];
}

export interface LayoutResult {
  nodes: Node[];
  bands?: GroupBand[];
}

export type ZoomTierId = 'far' | 'mid' | 'near';

export interface ZoomTier {
  id: ZoomTierId;
  /** Lowest React Flow zoom at which this tier applies. */
  minZoom: number;
  cardWidth: number;
  /** Base height; modes may grow a card beyond this for port lists. */
  cardHeight: number;
}

export interface LayoutContext {
  compositeId: string | null;
  /** Current zoom tier, for modes whose geometry depends on card size. */
  tier: ZoomTierId;
  /** Clustering granularity, 0..1. Higher means fewer, coarser clusters. */
  granularity: number;
}

export interface FocusContext {
  /** Hovered or selected process node ids. */
  focused: Set<string>;
  /** Explicitly pinned process node ids. */
  pinned: Set<string>;
}

export interface LayoutMode {
  id: string;
  label: string;
  run(nodes: Node[], edges: Edge[], ctx: LayoutContext): Promise<LayoutResult>;
  /** Cull/annotate edges for this mode. Omitted means "draw them all". */
  edgeVisibility?(edges: Edge[], focus: FocusContext, nodes: Node[]): Edge[];
  /** Semantic-zoom tiers, ordered low to high. Omitted means fixed cards. */
  tiers?: ZoomTier[];
}
```

- [ ] **Step 2: Write the failing registry test**

```ts
// src/__tests__/registry.test.ts
import { describe, it, expect } from 'vitest';
import { LAYOUT_MODES, getMode, DEFAULT_MODE_ID } from '../layouts/registry';

describe('layout registry', () => {
  it('exposes hierarchy as the default mode', () => {
    expect(DEFAULT_MODE_ID).toBe('hierarchy');
    expect(getMode(DEFAULT_MODE_ID).id).toBe('hierarchy');
  });

  it('every registered mode satisfies the interface', () => {
    expect(LAYOUT_MODES.length).toBeGreaterThan(0);
    for (const m of LAYOUT_MODES) {
      expect(typeof m.id).toBe('string');
      expect(m.id.length).toBeGreaterThan(0);
      expect(typeof m.label).toBe('string');
      expect(typeof m.run).toBe('function');
    }
  });

  it('mode ids are unique', () => {
    const ids = LAYOUT_MODES.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('falls back to the default mode for an unknown id', () => {
    expect(getMode('does-not-exist').id).toBe(DEFAULT_MODE_ID);
  });
});
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `npm test -- registry`
Expected: FAIL — `Failed to resolve import "../layouts/registry"`.

- [ ] **Step 4: Move the ELK layout into `src/layouts/hierarchy.ts`**

Move the **entire current contents** of `src/layout.ts` into `src/layouts/hierarchy.ts` with two changes: the `elkjs` and `@xyflow/react` imports stay identical, and a mode wrapper is appended. Do not alter any layout logic, constant, or comment.

Append at the end of the moved file:

```ts
import type { LayoutMode, LayoutResult, LayoutContext } from './types';

export const hierarchyMode: LayoutMode = {
  id: 'hierarchy',
  label: 'Hierarchy',
  async run(nodes: Node[], edges: Edge[], _ctx: LayoutContext): Promise<LayoutResult> {
    return { nodes: await applyLayout(nodes, edges) };
  },
};
```

- [ ] **Step 5: Reduce `src/layout.ts` to a re-export shim**

Existing callers (`App.tsx:13`, `src/__tests__/layout.test.ts`) keep working unchanged.

```ts
// src/layout.ts — moved to layouts/hierarchy.ts. Kept as a re-export so
// existing imports and the layout regression test keep resolving.
export { applyLayout, applyCompactLayout } from './layouts/hierarchy';
```

- [ ] **Step 6: Write `src/layouts/registry.ts`**

```ts
// src/layouts/registry.ts — the ordered set of available layout modes.
// Adding a view mode means adding one entry here.

import type { LayoutMode } from './types';
import { hierarchyMode } from './hierarchy';

export const DEFAULT_MODE_ID = 'hierarchy';

export const LAYOUT_MODES: LayoutMode[] = [hierarchyMode];

export function getMode(id: string | null | undefined): LayoutMode {
  return LAYOUT_MODES.find((m) => m.id === id)
    ?? LAYOUT_MODES.find((m) => m.id === DEFAULT_MODE_ID)
    ?? LAYOUT_MODES[0];
}
```

- [ ] **Step 7: Run the registry test and the layout regression gate**

Run: `npm test -- registry layout`
Expected: PASS. `layout.test.ts` must pass **without edits** — that proves the move was behavior-preserving.

- [ ] **Step 8: Typecheck**

Run: `npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add src/layouts src/layout.ts src/__tests__/registry.test.ts
git commit -m "refactor(loom): extract layout-mode registry, move ELK layout to hierarchy mode"
```

---

## Task 2: Wire the registry into App with a mode toggle

**Files:**
- Create: `src/hooks/useLayoutMode.ts`
- Modify: `src/App.tsx` (import at `:13`, layout call sites at `:264` and `:347`, toolbar at `:638-696`), `src/layoutStore.ts`, `src/viewStore.ts`
- Test: `src/__tests__/useLayoutMode.test.ts`, `src/__tests__/viewStore.test.ts` (add cases)

**Interfaces:**
- Consumes: `LAYOUT_MODES`, `getMode`, `DEFAULT_MODE_ID` (Task 1); `LayoutResult`, `GroupBand` (Task 1)
- Produces: `useLayoutMode(): { modeId, setModeId, mode, bands, runLayout }` where `runLayout(nodes: Node[], edges: Edge[]): Promise<LayoutResult>`; `loadLayout`/`saveLayout`/`clearLayout` gain a trailing `modeId?: string` parameter; `View` type gains `mode?: string` and `pins?: string[]`

- [ ] **Step 1: Write the failing mode-scoped persistence test**

```ts
// src/__tests__/useLayoutMode.test.ts
import { describe, it, expect, beforeEach } from 'vitest';
import { loadLayout, saveLayout } from '../layoutStore';

describe('mode-scoped layout persistence', () => {
  beforeEach(() => localStorage.clear());

  it('keeps positions for different modes apart', () => {
    saveLayout('c1', { a: { x: 1, y: 1 } }, 'hierarchy');
    saveLayout('c1', { a: { x: 99, y: 99 } }, 'process-column');
    expect(loadLayout('c1', 'hierarchy')).toEqual({ a: { x: 1, y: 1 } });
    expect(loadLayout('c1', 'process-column')).toEqual({ a: { x: 99, y: 99 } });
  });

  it('defaults to the hierarchy scope when no mode is given', () => {
    saveLayout('c1', { a: { x: 5, y: 5 } });
    expect(loadLayout('c1', 'hierarchy')).toEqual({ a: { x: 5, y: 5 } });
  });
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `npm test -- useLayoutMode`
Expected: FAIL — positions collide because the third argument is ignored.

- [ ] **Step 3: Make `layoutStore` keys mode-scoped**

In `src/layoutStore.ts`, replace `keyFor` and thread `modeId` through `loadLayout`, `saveLayout`, and `clearLayout`. The default keeps old keys readable for `hierarchy`, so no user loses saved drags.

```ts
function keyFor(compositeId: string | null | undefined, modeId = 'hierarchy'): string | null {
  if (!compositeId) return null;
  // 'hierarchy' keeps the original un-suffixed key so previously saved
  // positions survive this change.
  return modeId === 'hierarchy'
    ? KEY_PREFIX + compositeId
    : `${KEY_PREFIX}${compositeId}:${modeId}`;
}
```

Update each exported function's signature to accept `modeId?: string` as its final parameter and pass it to `keyFor`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- useLayoutMode`
Expected: PASS.

- [ ] **Step 5: Write the failing View back-compat test**

Append to `src/__tests__/viewStore.test.ts`:

```ts
it('normalizes a legacy view with no mode to hierarchy', () => {
  const v = normalizeView({ positions: {}, collapsed: [], hidden: [] } as any);
  expect(v.mode).toBe('hierarchy');
  expect(v.pins).toEqual([]);
});

it('preserves an explicit mode and pins', () => {
  const v = normalizeView({
    positions: {}, collapsed: [], hidden: [],
    mode: 'process-column', pins: ['p1'],
  } as any);
  expect(v.mode).toBe('process-column');
  expect(v.pins).toEqual(['p1']);
});
```

- [ ] **Step 6: Run it and confirm it fails**

Run: `npm test -- viewStore`
Expected: FAIL — `expected undefined to be 'hierarchy'`.

- [ ] **Step 7: Extend the `View` type and `normalizeView`**

In `src/viewStore.ts`, add the two optional fields to `View`:

```ts
export type View = {
  v?: 1;
  positions: LayoutPositions;
  collapsed: string[];
  hidden: string[];
  /** Layout mode this view was captured in. Absent means 'hierarchy'. */
  mode?: string;
  /** Pinned process node ids (process-column mode). */
  pins?: string[];
};
```

In `normalizeView`, add these two lines to the returned object:

```ts
mode: typeof raw?.mode === 'string' ? raw.mode : 'hierarchy',
pins: Array.isArray(raw?.pins) ? raw.pins.filter((p: unknown) => typeof p === 'string') : [],
```

- [ ] **Step 8: Run it to verify it passes**

Run: `npm test -- viewStore`
Expected: PASS.

- [ ] **Step 9: Write `src/hooks/useLayoutMode.ts`**

```ts
// src/hooks/useLayoutMode.ts — owns which layout mode is active and
// dispatches layout runs through the registry.

import { useCallback, useState } from 'react';
import type { Node, Edge } from '@xyflow/react';
import { getMode, DEFAULT_MODE_ID } from '../layouts/registry';
import type { GroupBand, LayoutResult, ZoomTierId } from '../layouts/types';

export interface UseLayoutMode {
  modeId: string;
  setModeId: (id: string) => void;
  mode: ReturnType<typeof getMode>;
  bands: GroupBand[];
  granularity: number;
  setGranularity: (g: number) => void;
  runLayout: (
    nodes: Node[],
    edges: Edge[],
    compositeId: string | null,
    tier: ZoomTierId,
  ) => Promise<LayoutResult>;
}

export function useLayoutMode(initialModeId = DEFAULT_MODE_ID): UseLayoutMode {
  const [modeId, setModeId] = useState(initialModeId);
  const [bands, setBands] = useState<GroupBand[]>([]);
  const [granularity, setGranularity] = useState(0.30);
  const mode = getMode(modeId);

  const runLayout = useCallback(
    async (nodes: Node[], edges: Edge[], compositeId: string | null, tier: ZoomTierId) => {
      const result = await getMode(modeId).run(nodes, edges, { compositeId, tier, granularity });
      setBands(result.bands ?? []);
      return result;
    },
    [modeId, granularity],
  );

  return { modeId, setModeId, mode, bands, granularity, setGranularity, runLayout };
}
```

- [ ] **Step 10: Wire it into `App.tsx`**

Add the import beside the existing layout import at `App.tsx:13`:

```ts
import { useLayoutMode } from './hooks/useLayoutMode';
import { LAYOUT_MODES } from './layouts/registry';
```

Instantiate it beside the other state hooks (near `App.tsx:64`):

```ts
const layoutMode = useLayoutMode();
```

At both existing call sites — the rebuild effect (`App.tsx:264`) and `handleResetLayout` (`App.tsx:347`) — replace `await applyLayout(next, rawEdges)` with:

```ts
const { nodes: laidOut } = await layoutMode.runLayout(next, rawEdges, compositeId, 'mid');
```

then use `laidOut` where `applyLayout`'s result was used. **Preserve the surrounding `setNodes` reducer bodies exactly** — they carry the object-identity guarantee noted in Global Constraints.

Add `layoutMode.modeId` to each effect's dependency array so switching modes re-runs layout.

- [ ] **Step 11: Add the toolbar mode switcher**

In the toolbar block (`App.tsx:638-696`), immediately before the `Re-layout` button at `:658`:

```tsx
<select
  className="loom-mode-select"
  value={layoutMode.modeId}
  onChange={(e) => layoutMode.setModeId(e.target.value)}
  title="Layout mode"
>
  {LAYOUT_MODES.map((m) => (
    <option key={m.id} value={m.id}>{m.label}</option>
  ))}
</select>
```

- [ ] **Step 12: Run the full suite and typecheck**

Run: `npm test && npx tsc -b --noEmit`
Expected: all suites PASS, no type errors. `layout.test.ts` still passes unedited.

- [ ] **Step 13: Commit**

```bash
git add src/hooks src/App.tsx src/layoutStore.ts src/viewStore.ts src/__tests__
git commit -m "feat(loom): dispatch layout through the mode registry, add mode switcher"
```

---

## Task 3: Capture the fixture and build the affinity key extractor

**Files:**
- Create: `src/__tests__/fixtures/v2ecoli-baseline.json`, `src/layouts/affinity.ts`
- Modify: `src/types.ts` (declare the two schema fields)
- Test: `src/__tests__/affinity.test.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure module)
- Produces: from `src/layouts/affinity.ts` — `isNoiseKey(key: string): boolean`, `NOISE_KEY_PREFIXES: string[]`, `storeKeysForProcess(node: Node, keyDepth?: number): Map<string, number>`, `isBookkeepingProcess(label: string): boolean`

**Background — where the wiring data already lives.** `convert.ts:260-266` already attaches `inputPortsSchema` and `outputPortsSchema` to every process node's `data`: a `Record<port, dottedTarget>` where the target is the **raw wire path relative to the process's parent store** (`['unique','RNA']` becomes `'unique.RNA'`). That is precisely the input the clustering needs, so this module reads nodes and never re-walks the raw composite state. Both fields are currently undeclared and `any`-cast at `ProcessNode.tsx:25-26`; this task declares them properly.

- [ ] **Step 1: Copy the fixture**

```bash
mkdir -p src/__tests__/fixtures
cp /Users/eranagmon/code/v2ecoli/reports/composite-state/v2ecoli.composites.baseline.json \
   src/__tests__/fixtures/v2ecoli-baseline.json
```

To refresh it later, regenerate in the v2ecoli workspace with `python scripts/regenerate_composite_states.py` (requires the ParCa cache at `out/cache`), or fetch `GET /api/composite-state?ref=v2ecoli.composites.baseline` from a running workbench.

Verify what you copied — expect 46 wired components, not the 55 the description claims:

```bash
python3 -c "
import json; d=json.load(open('src/__tests__/fixtures/v2ecoli-baseline.json'))
c=[0,0]
def w(n):
    if not isinstance(n,dict): return
    t=n.get('_type')
    if t=='process': c[0]+=1; return
    if t=='step': c[1]+=1; return
    [w(v) for k,v in n.items() if not k.startswith('_')]
w(d['state']); print('process',c[0],'step',c[1],'total',sum(c))"
```

- [ ] **Step 2: Declare the schema fields in `src/types.ts`**

Add to `ProcessNodeData`, replacing the `any` casts downstream:

```ts
  /** port -> dotted wire target, relative to the process's parent store. */
  inputPortsSchema?: Record<string, string>;
  outputPortsSchema?: Record<string, string>;
```

- [ ] **Step 3: Write the failing key-extraction test**

```ts
// src/__tests__/affinity.test.ts
import { describe, it, expect } from 'vitest';
import type { Node } from '@xyflow/react';
import { isNoiseKey, storeKeysForProcess, isBookkeepingProcess } from '../layouts/affinity';

function proc(label: string, inputs: Record<string, string>, outputs: Record<string, string> = {}): Node {
  return {
    id: label, type: 'process', position: { x: 0, y: 0 },
    data: {
      label, nodeType: 'process', processType: 'step', address: 'a', config: {},
      path: ['agents', '0', label], inputPorts: Object.keys(inputs), outputPorts: Object.keys(outputs),
      inputPortsSchema: inputs, outputPortsSchema: outputs,
    },
  } as unknown as Node;
}

describe('isNoiseKey', () => {
  it('rejects process-private bookkeeping stores', () => {
    for (const k of ['_layer_token_7', 'next_update_time', 'process.foo',
                     'process_state.dnaa_hydrolysis', 'request', 'timestep',
                     'global_time', 'pinned_flux_targets', 'allocate.ecoli-x']) {
      expect(isNoiseKey(k)).toBe(true);
    }
  });

  it('keeps real biological stores', () => {
    for (const k of ['bulk', 'listeners', 'unique.RNA', 'unique.active_ribosome',
                     'boundary', 'environment', 'unique.promoter']) {
      expect(isNoiseKey(k)).toBe(false);
    }
  });
});

describe('storeKeysForProcess', () => {
  it('truncates keys to depth 2 and counts port multiplicity', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: 'unique.RNA.foo', b: 'unique.RNA.bar', c: 'bulk' }),
    );
    expect(keys.get('unique.RNA')).toBe(2);
    expect(keys.get('bulk')).toBe(1);
  });

  it('merges input and output ports', () => {
    const keys = storeKeysForProcess(proc('p', { a: 'bulk' }, { b: 'bulk', c: 'listeners' }));
    expect(keys.get('bulk')).toBe(2);
    expect(keys.get('listeners')).toBe(1);
  });

  it('drops noise keys entirely', () => {
    const keys = storeKeysForProcess(proc('p', { a: 'bulk', b: '_layer_token_3', c: 'timestep' }));
    expect([...keys.keys()]).toEqual(['bulk']);
  });
});

describe('isBookkeepingProcess', () => {
  it('matches what defaultHiddenIds already hides', () => {
    expect(isBookkeepingProcess('unique_update_4')).toBe(true);
    expect(isBookkeepingProcess('allocator_2')).toBe(true);
    expect(isBookkeepingProcess('rnap_data_listener')).toBe(true);
    expect(isBookkeepingProcess('ecoli-transcript-initiation')).toBe(false);
  });
});
```

- [ ] **Step 4: Run it and confirm it fails**

Run: `npm test -- affinity`
Expected: FAIL — `Failed to resolve import "../layouts/affinity"`.

- [ ] **Step 5: Write the extractor half of `src/layouts/affinity.ts`**

```ts
// src/layouts/affinity.ts — group processes by the stores they wire into.
//
// Reads inputPortsSchema/outputPortsSchema off process nodes (attached by
// convert.ts), which are already wire paths relative to the process's
// parent store. Pure: no React, no DOM, no React Flow beyond the Node type.

import type { Node } from '@xyflow/react';
import type { ProcessNodeData } from '../types';

/** Stores that are process-private plumbing, never a meaningful group key.
 *  The store-side counterpart of convert.ts's defaultHiddenIds. */
export const NOISE_KEY_PREFIXES = [
  '_layer_token', 'process.', 'process_state.', 'request',
  'next_update_time', 'pinned_flux_targets', 'timestep',
  'global_time', 'allocate.', '_',
];

export function isNoiseKey(key: string): boolean {
  return NOISE_KEY_PREFIXES.some((p) => key === p || key.startsWith(p));
}

/** Bookkeeping processes, matching what defaultHiddenIds already hides. */
export function isBookkeepingProcess(label: string): boolean {
  const n = (label || '').toLowerCase();
  return n.startsWith('unique_update') || n.startsWith('allocator') || n.includes('listener');
}

/** Store keys this process touches -> number of ports wired to each. */
export function storeKeysForProcess(node: Node, keyDepth = 2): Map<string, number> {
  const data = node.data as unknown as ProcessNodeData;
  const out = new Map<string, number>();
  const add = (schema: Record<string, string> | undefined) => {
    for (const target of Object.values(schema ?? {})) {
      if (!target) continue;
      const key = String(target).split('.').slice(0, keyDepth).join('.');
      if (!key || isNoiseKey(key)) continue;
      out.set(key, (out.get(key) ?? 0) + 1);
    }
  };
  add(data.inputPortsSchema);
  add(data.outputPortsSchema);
  return out;
}
```

- [ ] **Step 6: Run it to verify it passes**

Run: `npm test -- affinity`
Expected: PASS, all cases.

- [ ] **Step 7: Commit**

```bash
git add src/layouts/affinity.ts src/types.ts src/__tests__/affinity.test.ts src/__tests__/fixtures
git commit -m "feat(loom): affinity store-key extraction + real v2ecoli baseline fixture"
```

---

## Task 4: Cluster assignment, hub detection, and fixture validation

**Files:**
- Modify: `src/layouts/affinity.ts`
- Test: `src/__tests__/affinity.test.ts` (append), `src/__tests__/affinityFixture.test.ts`

**Interfaces:**
- Consumes: `storeKeysForProcess`, `isBookkeepingProcess`, `isNoiseKey` (Task 3)
- Produces: `clusterProcesses(nodes: Node[], opts?: AffinityOptions): AffinityResult`; `AffinityOptions = { hubFraction?: number; hubProcessKeyLimit?: number; keyDepth?: number }`; `AffinityResult = { clusters: Cluster[]; hubs: string[] }`; `Cluster = { key: string; label: string; processIds: string[] }`

**Why this scoring.** Two alternatives were prototyped against the real baseline and rejected on measured results, recorded in the spec. TF-IDF distinctiveness (`ports × log(n/df)`) gave 36 clusters for 46 processes, 27 of them singletons keyed on `_layer_token_7` — rare stores are process-*private*, not distinctive. Jaccard agglomerative clustering gave 12–14 clusters with 7–8 singletons and a largest cluster sharing no distinctive store, so it could not be labeled. The surviving rule is: **assign each process to the most widely shared non-hub store it touches.**

- [ ] **Step 1: Write the failing clustering tests**

Append to `src/__tests__/affinity.test.ts`:

```ts
import { clusterProcesses } from '../layouts/affinity';

describe('clusterProcesses', () => {
  it('groups processes sharing a mid-frequency store', () => {
    const nodes = [
      proc('a', { x: 'unique.RNA', h: 'bulk' }),
      proc('b', { x: 'unique.RNA', h: 'bulk' }),
      proc('c', { y: 'unique.promoter', h: 'bulk' }),
      proc('d', { y: 'unique.promoter', h: 'bulk' }),
    ];
    const { clusters } = clusterProcesses(nodes, { hubFraction: 0.9 });
    const byKey = Object.fromEntries(clusters.map((c) => [c.key, c.processIds.sort()]));
    expect(byKey['unique.RNA']).toEqual(['a', 'b']);
    expect(byKey['unique.promoter']).toEqual(['c', 'd']);
  });

  it('excludes hub stores as cluster keys', () => {
    const nodes = ['a', 'b', 'c', 'd'].map((n) =>
      proc(n, { h: 'bulk', s: n === 'd' ? 'unique.oriC' : 'unique.RNA' }));
    const { hubs } = clusterProcesses(nodes, { hubFraction: 0.75 });
    expect(hubs).toContain('bulk');
    expect(hubs).not.toContain('unique.oriC');
  });

  it('routes hub-only processes to a labeled terminal bucket', () => {
    const nodes = [proc('a', { h: 'bulk' }), proc('b', { h: 'bulk' }), proc('c', { s: 'unique.RNA' })];
    const { clusters } = clusterProcesses(nodes, { hubFraction: 0.6 });
    const bucket = clusters.find((c) => c.key === '~hub-only');
    expect(bucket?.processIds.sort()).toEqual(['a', 'b']);
    expect(bucket?.label).toMatch(/bulk/);
  });

  it('diverts processes touching too many distinct keys to cross-cutting', () => {
    const many: Record<string, string> = {};
    for (let i = 0; i < 11; i++) many[`p${i}`] = `unique.s${i}`;
    const nodes = [proc('hub', many), proc('a', { s: 'unique.s0' }), proc('b', { s: 'unique.s0' })];
    const { clusters } = clusterProcesses(nodes, { hubFraction: 0.9, hubProcessKeyLimit: 8 });
    expect(clusters.find((c) => c.key === '~cross-cutting')?.processIds).toEqual(['hub']);
  });

  it('excludes bookkeeping processes entirely', () => {
    const nodes = [proc('unique_update_1', { s: 'unique.RNA' }), proc('real', { s: 'unique.RNA' })];
    const ids = clusterProcesses(nodes, { hubFraction: 0.9 }).clusters.flatMap((c) => c.processIds);
    expect(ids).toEqual(['real']);
  });

  it('is deterministic across runs', () => {
    const nodes = ['a', 'b', 'c'].map((n) => proc(n, { s: 'unique.RNA', t: 'bulk' }));
    expect(JSON.stringify(clusterProcesses(nodes)))
      .toBe(JSON.stringify(clusterProcesses(nodes)));
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `npm test -- affinity`
Expected: FAIL — `clusterProcesses is not a function`.

- [ ] **Step 3: Implement `clusterProcesses`**

Append to `src/layouts/affinity.ts`:

```ts
export interface AffinityOptions {
  /** A store touched by >= hubFraction * n processes cannot be a cluster key. */
  hubFraction?: number;
  /** A process touching more than this many distinct non-hub keys is
   *  cross-cutting and is not forced into any one cluster. */
  hubProcessKeyLimit?: number;
  keyDepth?: number;
}

export interface Cluster {
  key: string;
  label: string;
  processIds: string[];
}

export interface AffinityResult {
  clusters: Cluster[];
  hubs: string[];
}

export const HUB_ONLY_KEY = '~hub-only';
export const CROSS_CUTTING_KEY = '~cross-cutting';

export function clusterProcesses(nodes: Node[], opts: AffinityOptions = {}): AffinityResult {
  const { hubFraction = 0.30, hubProcessKeyLimit = 8, keyDepth = 2 } = opts;

  const procs = nodes.filter(
    (n) => n.type === 'process'
      && !isBookkeepingProcess(String((n.data as { label?: unknown })?.label ?? '')),
  );
  const n = procs.length;
  if (n === 0) return { clusters: [], hubs: [] };

  const touches = new Map<string, Map<string, number>>();
  const df = new Map<string, number>();
  for (const p of procs) {
    const keys = storeKeysForProcess(p, keyDepth);
    touches.set(p.id, keys);
    for (const k of keys.keys()) df.set(k, (df.get(k) ?? 0) + 1);
  }

  const hubCut = Math.max(3, Math.round(hubFraction * n));
  const hubs = [...df.entries()].filter(([, c]) => c >= hubCut).map(([k]) => k).sort();
  const hubSet = new Set(hubs);

  const grouped = new Map<string, string[]>();
  const push = (key: string, id: string) => {
    const list = grouped.get(key);
    if (list) list.push(id); else grouped.set(key, [id]);
  };

  for (const p of procs) {
    const keys = touches.get(p.id)!;
    const candidates = [...keys.entries()].filter(([k]) => !hubSet.has(k));
    if (candidates.length === 0) { push(HUB_ONLY_KEY, p.id); continue; }
    if (candidates.length > hubProcessKeyLimit) { push(CROSS_CUTTING_KEY, p.id); continue; }
    // Most widely SHARED non-hub key wins; ties break on port multiplicity,
    // then lexically so the result is stable.
    candidates.sort((a, b) =>
      (df.get(b[0])! - df.get(a[0])!) || (b[1] - a[1]) || a[0].localeCompare(b[0]));
    push(candidates[0][0], p.id);
  }

  const hubLabel = hubs.length ? `${hubs.slice(0, 3).join(' · ')} only` : 'ungrouped';
  const clusters: Cluster[] = [...grouped.entries()]
    .map(([key, ids]) => ({
      key,
      label: key === HUB_ONLY_KEY ? hubLabel : key === CROSS_CUTTING_KEY ? 'cross-cutting' : key,
      processIds: ids.sort(),
    }))
    .sort((a, b) => {
      const rank = (k: string) => (k === CROSS_CUTTING_KEY ? 1 : k === HUB_ONLY_KEY ? 2 : 0);
      return (rank(a.key) - rank(b.key))
        || (b.processIds.length - a.processIds.length)
        || a.key.localeCompare(b.key);
    });

  return { clusters, hubs };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm test -- affinity`
Expected: PASS, all cases.

- [ ] **Step 5: Write the fixture-quality test**

This is the guard that stops the algorithm silently regressing to singleton soup on real data.

```ts
// src/__tests__/affinityFixture.test.ts
import { describe, it, expect } from 'vitest';
import { stateToReactFlow } from '../convert';
import { clusterProcesses } from '../layouts/affinity';
import fixture from './fixtures/v2ecoli-baseline.json';

const { nodes } = stateToReactFlow((fixture as any).state);
const result = clusterProcesses(nodes, { hubFraction: 0.30 });
const sizes = result.clusters.map((c) => c.processIds.length);
const total = sizes.reduce((a, b) => a + b, 0);

describe('affinity clustering on the real v2ecoli baseline', () => {
  it('finds the expected hub stores', () => {
    expect(result.hubs).toEqual(expect.arrayContaining(['bulk', 'listeners']));
  });

  it('produces a readable number of clusters, not singleton soup', () => {
    expect(result.clusters.length).toBeGreaterThanOrEqual(5);
    expect(result.clusters.length).toBeLessThanOrEqual(14);
    expect(sizes.filter((s) => s === 1).length / result.clusters.length).toBeLessThan(0.5);
  });

  it('assigns every non-bookkeeping process exactly once', () => {
    const ids = result.clusters.flatMap((c) => c.processIds);
    expect(new Set(ids).size).toBe(ids.length);
    expect(total).toBeGreaterThan(20);
  });

  it('keeps requester/evolver partition pairs together', () => {
    for (const stem of ['transcript-elongation', 'polypeptide-elongation', 'rna-degradation']) {
      const owner = (suffix: string) =>
        result.clusters.find((c) => c.processIds.some((id) => id.endsWith(`${stem}_${suffix}`)))?.key;
      const req = owner('requester');
      if (req) expect(owner('evolver')).toBe(req);
    }
  });
});
```

- [ ] **Step 6: Run it**

Run: `npm test -- affinityFixture`
Expected: PASS.

**If the cluster-count assertion fails**, the fixture has drifted from what the thresholds were tuned against. Print the actual grouping and re-tune `hubFraction` before proceeding — do **not** loosen the assertion to make it green:

```bash
npx vitest run affinityFixture --reporter=verbose
```

- [ ] **Step 7: Commit**

```bash
git add src/layouts/affinity.ts src/__tests__/affinity.test.ts src/__tests__/affinityFixture.test.ts
git commit -m "feat(loom): store-affinity cluster assignment with hub detection"
```

---

## Task 5: The process-column layout mode

**Files:**
- Create: `src/layouts/processColumn.ts`
- Modify: `src/layouts/registry.ts`
- Test: `src/__tests__/processColumn.test.ts`

**Interfaces:**
- Consumes: `clusterProcesses`, `Cluster` (Task 4); `LayoutMode`, `LayoutResult`, `GroupBand`, `LayoutContext`, `ZoomTier` (Task 1); `applyLayout` from `./hierarchy` (Task 1)
- Produces: `processColumnMode: LayoutMode`; `TIERS: ZoomTier[]`; `CLUSTER_GAP = 44`, `CARD_GAP = 16`, `GUTTER = 180`

- [ ] **Step 1: Write the failing geometry test**

```ts
// src/__tests__/processColumn.test.ts
import { describe, it, expect } from 'vitest';
import type { Node } from '@xyflow/react';
import { processColumnMode, TIERS, CARD_GAP, CLUSTER_GAP } from '../layouts/processColumn';

function proc(id: string, target: string): Node {
  return {
    id, type: 'process', position: { x: 0, y: 0 },
    data: {
      label: id, nodeType: 'process', processType: 'step', address: 'a', config: {},
      path: ['agents', '0', id], inputPorts: ['s'], outputPorts: [],
      inputPortsSchema: { s: target }, outputPortsSchema: {},
    },
  } as unknown as Node;
}
function store(id: string): Node {
  return {
    id, type: 'store', position: { x: 0, y: 0 },
    data: { label: id, nodeType: 'store', path: id.split('.') },
  } as unknown as Node;
}

const ctx = { compositeId: 'c', tier: 'mid' as const, granularity: 0.30 };

describe('processColumnMode', () => {
  it('places every process in a single column at one x', async () => {
    const nodes = [store('unique.RNA'), store('bulk'),
      proc('a', 'unique.RNA'), proc('b', 'unique.RNA'), proc('c', 'bulk')];
    const { nodes: out } = await processColumnMode.run(nodes, [], ctx);
    const xs = new Set(out.filter((n) => n.type === 'process').map((n) => n.position.x));
    expect(xs.size).toBe(1);
  });

  it('never overlaps two cards vertically', async () => {
    const nodes = [store('unique.RNA'),
      ...['a', 'b', 'c', 'd'].map((i) => proc(i, 'unique.RNA'))];
    const { nodes: out } = await processColumnMode.run(nodes, [], ctx);
    const ys = out.filter((n) => n.type === 'process').map((n) => n.position.y).sort((p, q) => p - q);
    const h = TIERS.find((t) => t.id === 'mid')!.cardHeight;
    for (let i = 1; i < ys.length; i++) expect(ys[i] - ys[i - 1]).toBeGreaterThanOrEqual(h + CARD_GAP);
  });

  it('emits one band per cluster covering its members', async () => {
    const nodes = [store('unique.RNA'), store('unique.promoter'),
      proc('a', 'unique.RNA'), proc('b', 'unique.RNA'), proc('c', 'unique.promoter')];
    const { bands } = await processColumnMode.run(nodes, [], ctx);
    expect(bands!.length).toBeGreaterThanOrEqual(2);
    for (const b of bands!) {
      expect(b.yEnd).toBeGreaterThan(b.yStart);
      expect(b.nodeIds.length).toBeGreaterThan(0);
    }
  });

  it('separates clusters by more than it separates cards', async () => {
    const nodes = [store('unique.RNA'), store('unique.promoter'),
      proc('a', 'unique.RNA'), proc('b', 'unique.RNA'), proc('c', 'unique.promoter')];
    const { bands } = await processColumnMode.run(nodes, [], ctx);
    const sorted = [...bands!].sort((p, q) => p.yStart - q.yStart);
    expect(sorted[1].yStart - sorted[0].yEnd).toBeGreaterThanOrEqual(CLUSTER_GAP);
  });

  it('puts stores to the right of the column', async () => {
    const nodes = [store('unique.RNA'), proc('a', 'unique.RNA')];
    const { nodes: out } = await processColumnMode.run(nodes, [], ctx);
    const px = out.find((n) => n.id === 'a')!.position.x;
    const sx = out.find((n) => n.id === 'unique.RNA')!.position.x;
    expect(sx).toBeGreaterThan(px);
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `npm test -- processColumn`
Expected: FAIL — `Failed to resolve import "../layouts/processColumn"`.

- [ ] **Step 3: Implement the mode**

```ts
// src/layouts/processColumn.ts — processes in one clustered column, stores
// laid out to the right by the existing hierarchy pass.
//
// The column is one-dimensional, so re-flowing it for a zoom-tier change is
// a prefix sum rather than a graph layout. That is what makes semantic zoom
// affordable at several hundred nodes.

import type { Node, Edge } from '@xyflow/react';
import { applyLayout } from './hierarchy';
import { clusterProcesses } from './affinity';
import type { LayoutMode, LayoutResult, LayoutContext, GroupBand, ZoomTier } from './types';

export const TIERS: ZoomTier[] = [
  { id: 'far',  minZoom: 0,    cardWidth: 180, cardHeight: 56 },
  { id: 'mid',  minZoom: 0.35, cardWidth: 220, cardHeight: 92 },
  { id: 'near', minZoom: 0.85, cardWidth: 320, cardHeight: 120 },
];

export const CARD_GAP = 16;
export const CLUSTER_GAP = 44;
export const GUTTER = 180;

/** Map the rail's coarse..fine granularity slider onto a hub threshold.
 *  Lower hubFraction disqualifies more stores as keys, giving finer groups. */
function hubFractionFor(granularity: number): number {
  const g = Math.min(1, Math.max(0, granularity));
  return 0.20 + g * 0.25;   // 0.20 (fine) .. 0.45 (coarse)
}

export const processColumnMode: LayoutMode = {
  id: 'process-column',
  label: 'Process column',
  tiers: TIERS,

  async run(nodes: Node[], edges: Edge[], ctx: LayoutContext): Promise<LayoutResult> {
    const tier = TIERS.find((t) => t.id === ctx.tier) ?? TIERS[1];
    const { clusters } = clusterProcesses(nodes, { hubFraction: hubFractionFor(ctx.granularity) });

    // Stores keep the hierarchy arrangement, shifted right of the column.
    const storeNodes = nodes.filter((n) => n.type !== 'process');
    const laidOutStores = await applyLayout(storeNodes, edges.filter(
      (e) => (e.data as { edgeType?: string } | undefined)?.edgeType === 'place'));

    const minStoreX = laidOutStores.length
      ? Math.min(...laidOutStores.map((n) => n.position.x)) : 0;
    const shift = (tier.cardWidth + GUTTER) - minStoreX;
    const storeById = new Map(
      laidOutStores.map((n) => [n.id, { ...n, position: { x: n.position.x + shift, y: n.position.y } }]),
    );

    // Column: prefix-sum down the clusters. O(n), no graph layout.
    const posById = new Map<string, { x: number; y: number }>();
    const bands: GroupBand[] = [];
    let y = 0;
    for (const c of clusters) {
      if (c.processIds.length === 0) continue;
      const yStart = y;
      for (const id of c.processIds) {
        posById.set(id, { x: 0, y });
        y += tier.cardHeight + CARD_GAP;
      }
      bands.push({
        key: c.key,
        label: c.label,
        yStart,
        yEnd: y - CARD_GAP,
        keyStoreId: storeById.has(c.key) ? c.key : null,
        nodeIds: [...c.processIds],
      });
      y += CLUSTER_GAP;
    }

    const out = nodes.map((n) => {
      const p = posById.get(n.id);
      if (p) return { ...n, position: p };
      const s = storeById.get(n.id);
      return s ?? n;
    });

    return { nodes: out, bands };
  },
};
```

- [ ] **Step 4: Register the mode**

In `src/layouts/registry.ts`:

```ts
import { processColumnMode } from './processColumn';

export const LAYOUT_MODES: LayoutMode[] = [hierarchyMode, processColumnMode];
```

- [ ] **Step 5: Run the suite**

Run: `npm test`
Expected: all PASS, including `registry.test.ts`'s uniqueness and interface checks now covering two modes, and `layout.test.ts` still unedited.

- [ ] **Step 6: Verify against the real fixture in the browser**

```bash
npm run dev
```

Open the dev server, load the baseline fixture, switch the toolbar dropdown to **Process column**. Confirm: one column of processes on the left, stores to the right, visible gaps between clusters. Edge density is still high — that is Task 6's job.

- [ ] **Step 7: Commit**

```bash
git add src/layouts/processColumn.ts src/layouts/registry.ts src/__tests__/processColumn.test.ts
git commit -m "feat(loom): process-column layout mode with store-affinity bands"
```

---

## Task 6: Focus state and edge culling

**Files:**
- Create: `src/hooks/useFocus.ts`
- Modify: `src/layouts/processColumn.ts` (add `edgeVisibility`), `src/App.tsx`, `src/App.css`
- Test: `src/__tests__/useFocus.test.ts`, `src/__tests__/edgeVisibility.test.ts`

**Interfaces:**
- Consumes: `FocusContext`, `GroupBand` (Task 1); `processColumnMode` (Task 5)
- Produces: `useFocus(): { focused, pinned, hover, select, togglePin, clear, ctx }` where `ctx: FocusContext`; `processColumnMode.edgeVisibility(edges, focus, nodes): Edge[]`

- [ ] **Step 1: Write the failing edge-visibility test**

```ts
// src/__tests__/edgeVisibility.test.ts
import { describe, it, expect } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import { processColumnMode } from '../layouts/processColumn';

const nodes: Node[] = [
  { id: 'p1', type: 'process', position: { x: 0, y: 0 },
    data: { label: 'p1', inputPortsSchema: { a: 'unique.RNA' }, outputPortsSchema: {} } },
  { id: 'p2', type: 'process', position: { x: 0, y: 0 },
    data: { label: 'p2', inputPortsSchema: { a: 'unique.RNA' }, outputPortsSchema: {} } },
  { id: 'unique.RNA', type: 'store', position: { x: 0, y: 0 }, data: { label: 'RNA' } },
] as unknown as Node[];

const edges: Edge[] = [
  { id: 'p1--in--a', source: 'unique.RNA', target: 'p1', data: { edgeType: 'input' } },
  { id: 'p1--in--b', source: 'unique.RNA', target: 'p1', data: { edgeType: 'input' } },
  { id: 'p2--in--a', source: 'unique.RNA', target: 'p2', data: { edgeType: 'input' } },
  { id: 'place--r--c', source: 'unique', target: 'unique.RNA', data: { edgeType: 'place' } },
] as unknown as Edge[];

const vis = processColumnMode.edgeVisibility!;

describe('process-column edge visibility', () => {
  it('drops wire edges when nothing is focused', () => {
    const out = vis(edges, { focused: new Set(), pinned: new Set() }, nodes);
    expect(out.some((e) => (e.data as any).edgeType === 'input')).toBe(false);
  });

  it('always keeps structural place edges', () => {
    const out = vis(edges, { focused: new Set(), pinned: new Set() }, nodes);
    expect(out.find((e) => e.id === 'place--r--c')).toBeTruthy();
  });

  it('shows only the focused process wires at full strength', () => {
    const out = vis(edges, { focused: new Set(['p1']), pinned: new Set() }, nodes);
    const ids = out.filter((e) => (e.data as any).edgeType === 'input').map((e) => e.id);
    expect(ids).toEqual(expect.arrayContaining(['p1--in--a', 'p1--in--b']));
    expect(ids).not.toContain('p2--in--a');
  });

  it('unions pinned processes with focused ones', () => {
    const out = vis(edges, { focused: new Set(['p1']), pinned: new Set(['p2']) }, nodes);
    const ids = out.filter((e) => (e.data as any).edgeType === 'input').map((e) => e.id);
    expect(ids).toContain('p1--in--a');
    expect(ids).toContain('p2--in--a');
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `npm test -- edgeVisibility`
Expected: FAIL — `edgeVisibility is not a function`.

- [ ] **Step 3: Implement `edgeVisibility` on the mode**

Add to the `processColumnMode` object in `src/layouts/processColumn.ts`, after `run`:

```ts
  edgeVisibility(edges: Edge[], focus, _nodes: Node[]): Edge[] {
    const active = new Set<string>([...focus.focused, ...focus.pinned]);
    return edges.filter((e) => {
      const kind = (e.data as { edgeType?: string } | undefined)?.edgeType;
      // Place edges are the store hierarchy: structural, few, always drawn.
      if (kind === 'place') return true;
      if (active.size === 0) return false;
      return active.has(e.source) || active.has(e.target);
    });
  },
```

- [ ] **Step 4: Run to verify pass**

Run: `npm test -- edgeVisibility`
Expected: PASS.

- [ ] **Step 5: Write the failing focus-hook test**

```ts
// src/__tests__/useFocus.test.ts
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFocus } from '../hooks/useFocus';

describe('useFocus', () => {
  it('starts empty', () => {
    const { result } = renderHook(() => useFocus());
    expect(result.current.ctx.focused.size).toBe(0);
    expect(result.current.ctx.pinned.size).toBe(0);
  });

  it('tracks hover and clears it', () => {
    const { result } = renderHook(() => useFocus());
    act(() => result.current.hover('p1'));
    expect(result.current.ctx.focused.has('p1')).toBe(true);
    act(() => result.current.hover(null));
    expect(result.current.ctx.focused.size).toBe(0);
  });

  it('keeps a selection while hover moves away', () => {
    const { result } = renderHook(() => useFocus());
    act(() => result.current.select('p1'));
    act(() => result.current.hover('p2'));
    expect(result.current.ctx.focused.has('p1')).toBe(true);
    expect(result.current.ctx.focused.has('p2')).toBe(true);
  });

  it('accumulates and removes pins', () => {
    const { result } = renderHook(() => useFocus());
    act(() => result.current.togglePin('p1'));
    act(() => result.current.togglePin('p2'));
    expect(result.current.ctx.pinned.size).toBe(2);
    act(() => result.current.togglePin('p1'));
    expect([...result.current.ctx.pinned]).toEqual(['p2']);
  });
});
```

- [ ] **Step 6: Run and confirm failure**

Run: `npm test -- useFocus`
Expected: FAIL — cannot resolve `../hooks/useFocus`.

- [ ] **Step 7: Implement the hook**

```ts
// src/hooks/useFocus.ts — which processes are "active" right now.
//
// focused = hover ∪ selection (transient). pinned = explicit, accumulates,
// so two processes' wiring can be compared side by side.

import { useCallback, useMemo, useState } from 'react';
import type { FocusContext } from '../layouts/types';

export interface UseFocus {
  hovered: string | null;
  selected: string | null;
  pinned: Set<string>;
  hover: (id: string | null) => void;
  select: (id: string | null) => void;
  togglePin: (id: string) => void;
  clear: () => void;
  ctx: FocusContext;
}

export function useFocus(): UseFocus {
  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [pinned, setPinned] = useState<Set<string>>(() => new Set());

  const togglePin = useCallback((id: string) => {
    setPinned((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    setHovered(null); setSelected(null); setPinned(new Set());
  }, []);

  const ctx = useMemo<FocusContext>(() => {
    const focused = new Set<string>();
    if (hovered) focused.add(hovered);
    if (selected) focused.add(selected);
    return { focused, pinned };
  }, [hovered, selected, pinned]);

  return { hovered, selected, pinned, hover: setHovered, select: setSelected, togglePin, clear, ctx };
}
```

- [ ] **Step 8: Run to verify pass**

Run: `npm test -- useFocus`
Expected: PASS.

- [ ] **Step 9: Wire focus into `App.tsx`**

Instantiate beside `useLayoutMode`:

```ts
const focus = useFocus();
```

Where edges are handed to `<ReactFlow>`, filter through the active mode. Place this next to the existing hidden-filtering logic:

```ts
const visibleEdges = useMemo(() => {
  const fn = layoutMode.mode.edgeVisibility;
  return fn ? fn(displayEdges, focus.ctx, displayNodes) : displayEdges;
}, [displayEdges, displayNodes, focus.ctx, layoutMode.mode]);
```

Use `visibleEdges` as `<ReactFlow edges={...}>`. Extend the existing `handleNodeClick` (`App.tsx:480-488`) with `focus.select(node.id)`, keeping its current `setSelection` and `postInspect` calls intact. Add `onNodeMouseEnter={(_, n) => focus.hover(n.id)}` and `onNodeMouseLeave={() => focus.hover(null)}` to the `<ReactFlow>` props.

- [ ] **Step 10: Add the dimming style**

Append to `src/App.css`:

```css
/* Process-column mode: non-focused wires stay present but recede, so the
   graph reads as one focused path rather than a hairball. */
.loom-mode-process-column .react-flow__edge.loom-edge-dim { opacity: 0.08; }
.loom-cluster-band {
  font-size: 11px; font-weight: 600; color: #475569;
  text-transform: lowercase; letter-spacing: 0.02em;
}
```

- [ ] **Step 11: Full suite, typecheck, and visual check**

Run: `npm test && npx tsc -b --noEmit`
Expected: PASS, no type errors.

Then `npm run dev`, switch to Process column, and confirm the canvas shows **only place edges** until you hover a process, at which point that process's wires appear.

- [ ] **Step 12: Commit**

```bash
git add src/hooks/useFocus.ts src/layouts/processColumn.ts src/App.tsx src/App.css src/__tests__
git commit -m "feat(loom): focus-driven edge culling for the process-column view"
```

---

## Task 7: The process rail

**Files:**
- Create: `src/panels/ProcessRail.tsx`
- Modify: `src/App.tsx`, `src/App.css`
- Test: `src/__tests__/ProcessRail.test.tsx`

**Interfaces:**
- Consumes: `GroupBand` (Task 1); `useFocus` (Task 6); `useLayoutMode`'s `granularity`/`setGranularity` (Task 2)
- Produces: `ProcessRail` component with props `{ bands: GroupBand[]; nodes: Node[]; focus: UseFocus; granularity: number; onGranularityChange: (g: number) => void; onNavigate: (nodeId: string) => void }`

- [ ] **Step 1: Write the failing rail test**

```tsx
// src/__tests__/ProcessRail.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { Node } from '@xyflow/react';
import { ProcessRail } from '../panels/ProcessRail';
import type { GroupBand } from '../layouts/types';

const bands: GroupBand[] = [
  { key: 'unique.RNA', label: 'unique.RNA', yStart: 0, yEnd: 100, keyStoreId: 'unique.RNA',
    nodeIds: ['transcript-initiation', 'rna-degradation'] },
  { key: 'boundary', label: 'boundary', yStart: 150, yEnd: 200, keyStoreId: 'boundary',
    nodeIds: ['media_update'] },
];
const nodes = ['transcript-initiation', 'rna-degradation', 'media_update'].map((id) => ({
  id, type: 'process', position: { x: 0, y: 0 }, data: { label: id, address: 'a' },
})) as unknown as Node[];

const focus = {
  hovered: null, selected: null, pinned: new Set<string>(),
  hover: vi.fn(), select: vi.fn(), togglePin: vi.fn(), clear: vi.fn(),
  ctx: { focused: new Set<string>(), pinned: new Set<string>() },
};

function setup(over: Partial<React.ComponentProps<typeof ProcessRail>> = {}) {
  const onNavigate = vi.fn();
  render(<ProcessRail bands={bands} nodes={nodes} focus={focus as any}
    granularity={0.3} onGranularityChange={vi.fn()} onNavigate={onNavigate} {...over} />);
  return { onNavigate };
}

describe('ProcessRail', () => {
  it('renders every cluster label and process', () => {
    setup();
    expect(screen.getByText('unique.RNA')).toBeTruthy();
    expect(screen.getByText('boundary')).toBeTruthy();
    expect(screen.getByText('transcript-initiation')).toBeTruthy();
  });

  it('filters processes by the search box', () => {
    setup();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'degrad' } });
    expect(screen.queryByText('rna-degradation')).toBeTruthy();
    expect(screen.queryByText('transcript-initiation')).toBeNull();
  });

  it('hides a cluster whose members all filter out', () => {
    setup();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'degrad' } });
    expect(screen.queryByText('boundary')).toBeNull();
  });

  it('navigates and selects when a row is clicked', () => {
    const { onNavigate } = setup();
    fireEvent.click(screen.getByText('media_update'));
    expect(onNavigate).toHaveBeenCalledWith('media_update');
    expect(focus.select).toHaveBeenCalledWith('media_update');
  });

  it('reports granularity slider changes', () => {
    const onGranularityChange = vi.fn();
    setup({ onGranularityChange });
    fireEvent.change(screen.getByLabelText(/granularity/i), { target: { value: '0.5' } });
    expect(onGranularityChange).toHaveBeenCalledWith(0.5);
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `npm test -- ProcessRail`
Expected: FAIL — cannot resolve `../panels/ProcessRail`.

- [ ] **Step 3: Implement the rail**

```tsx
// src/panels/ProcessRail.tsx — browse the process inventory by cluster.
//
// Complements the canvas column: search, jump-to, pin. Selecting here
// drives the same focus state the canvas uses, so the two stay in sync.

import { useMemo, useState } from 'react';
import type { Node } from '@xyflow/react';
import type { GroupBand } from '../layouts/types';
import type { UseFocus } from '../hooks/useFocus';

export interface ProcessRailProps {
  bands: GroupBand[];
  nodes: Node[];
  focus: UseFocus;
  granularity: number;
  onGranularityChange: (g: number) => void;
  onNavigate: (nodeId: string) => void;
}

export function ProcessRail({
  bands, nodes, focus, granularity, onGranularityChange, onNavigate,
}: ProcessRailProps) {
  const [query, setQuery] = useState('');

  const labelById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of nodes) m.set(n.id, String((n.data as { label?: unknown })?.label ?? n.id));
    return m;
  }, [nodes]);

  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () => bands
      .map((b) => ({
        band: b,
        ids: b.nodeIds.filter((id) => !q || (labelById.get(id) ?? id).toLowerCase().includes(q)),
      }))
      .filter((g) => g.ids.length > 0),
    [bands, q, labelById],
  );

  return (
    <div className="loom-process-rail">
      <input
        className="loom-rail-search"
        placeholder="Search processes…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <label className="loom-rail-granularity">
        <span>Granularity</span>
        <input
          type="range" min={0} max={1} step={0.05}
          aria-label="Cluster granularity"
          value={granularity}
          onChange={(e) => onGranularityChange(parseFloat(e.target.value))}
        />
      </label>

      <div className="loom-rail-list">
        {filtered.map(({ band, ids }) => (
          <div key={band.key} className="loom-rail-cluster">
            <div className="loom-rail-cluster-label">{band.label}</div>
            {ids.map((id) => {
              const active = focus.ctx.focused.has(id) || focus.ctx.pinned.has(id);
              return (
                <div
                  key={id}
                  className={`loom-rail-row${active ? ' is-active' : ''}`}
                  onMouseEnter={() => focus.hover(id)}
                  onMouseLeave={() => focus.hover(null)}
                  onClick={() => { focus.select(id); onNavigate(id); }}
                >
                  <span className="loom-rail-row-label">{labelById.get(id) ?? id}</span>
                  <button
                    className={`loom-rail-pin${focus.ctx.pinned.has(id) ? ' is-pinned' : ''}`}
                    title={focus.ctx.pinned.has(id) ? 'Unpin' : 'Pin'}
                    onClick={(e) => { e.stopPropagation(); focus.togglePin(id); }}
                  >
                    📌
                  </button>
                </div>
              );
            })}
          </div>
        ))}
        {filtered.length === 0 && <div className="loom-rail-empty">No matching processes</div>}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm test -- ProcessRail`
Expected: PASS, all five cases.

- [ ] **Step 5: Mount the rail and implement scroll-sync**

In `App.tsx`, render the rail only in process-column mode, to the left of the React Flow canvas:

```tsx
{layoutMode.modeId === 'process-column' && (
  <ProcessRail
    bands={layoutMode.bands}
    nodes={allNodes}
    focus={focus}
    granularity={layoutMode.granularity}
    onGranularityChange={layoutMode.setGranularity}
    onNavigate={(id) => {
      const n = nodes.find((x) => x.id === id);
      if (n) rfInstance?.setCenter(n.position.x + 110, n.position.y + 30, { zoom: 1, duration: 300 });
    }}
  />
)}
```

Use the existing React Flow instance ref rather than adding a new one — `App.tsx:376` already clamps zoom for `setCenter`/`setViewport`, so follow that call's pattern.

- [ ] **Step 6: Style the rail**

Append to `src/App.css`:

```css
.loom-process-rail {
  width: 240px; flex: 0 0 240px; display: flex; flex-direction: column;
  gap: 8px; padding: 10px; overflow: hidden;
  border-right: 1px solid #e2e8f0; background: #fafafa; font-size: 12px;
}
.loom-rail-search { padding: 5px 8px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 12px; }
.loom-rail-granularity { display: flex; align-items: center; gap: 6px; color: #64748b; font-size: 11px; }
.loom-rail-granularity input { flex: 1; accent-color: #2563eb; }
.loom-rail-list { overflow-y: auto; flex: 1; }
.loom-rail-cluster { margin-bottom: 10px; }
.loom-rail-cluster-label {
  font-size: 10px; font-weight: 700; color: #475569; text-transform: uppercase;
  letter-spacing: 0.04em; padding: 2px 4px; border-bottom: 1px solid #e5e7eb; margin-bottom: 3px;
}
.loom-rail-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 3px 6px; border-radius: 4px; cursor: pointer;
}
.loom-rail-row:hover { background: rgba(0, 0, 0, 0.05); }
.loom-rail-row.is-active { background: #dbeafe; font-weight: 600; }
.loom-rail-row-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.loom-rail-pin { border: 0; background: none; cursor: pointer; opacity: 0.25; font-size: 11px; }
.loom-rail-pin.is-pinned, .loom-rail-pin:hover { opacity: 1; }
.loom-rail-empty { color: #94a3b8; padding: 8px 4px; }
```

- [ ] **Step 7: Full suite and visual check**

Run: `npm test && npx tsc -b --noEmit`
Expected: PASS.

`npm run dev`: in Process column mode, confirm the rail lists clusters, search filters, clicking a row centers the canvas on that process and reveals its wires, and dragging the granularity slider changes the number of clusters.

- [ ] **Step 8: Commit**

```bash
git add src/panels/ProcessRail.tsx src/App.tsx src/App.css src/__tests__/ProcessRail.test.tsx
git commit -m "feat(loom): searchable clustered process rail with granularity control"
```

---

## Task 8: Type abbreviation and contract derivation

**Files:**
- Create: `src/contract.ts`
- Modify: `src/types.ts`
- Test: `src/__tests__/contract.test.ts`, `src/__tests__/typeAbbrev.test.ts`

**Interfaces:**
- Consumes: `ProcessNodeData` (Task 3)
- Produces: from `src/contract.ts` — `abbreviateType(type: string): string`, `deriveContract(data: ProcessNodeData): ProcessContract | null`, `contractCompleteness(c: ProcessContract | null, data: ProcessNodeData): { documented: number; total: number; unknownPorts: string[] }`; `ProcessContract` interface

**Why this is its own task.** Both helpers are pure string/object functions with no React involvement, so they are cheap to test exhaustively and the rendering task (Task 9) stays about layout rather than parsing.

- [ ] **Step 1: Write the failing type-abbreviation test**

```ts
// src/__tests__/typeAbbrev.test.ts
import { describe, it, expect } from 'vitest';
import { abbreviateType } from '../contract';

describe('abbreviateType', () => {
  it('collapses a long structured type to a field count', () => {
    const t = 'unique_array[TU_index:integer|transcript_length:integer|is_mRNA:boolean]';
    expect(abbreviateType(t)).toBe('unique_array[3 fields]');
  });

  it('leaves short scalar types alone', () => {
    expect(abbreviateType('string')).toBe('string');
    expect(abbreviateType('bulk_array')).toBe('bulk_array');
    expect(abbreviateType('quantity[g/L]')).toBe('quantity[g/L]');
  });

  it('keeps a single-field structured type readable rather than counting it', () => {
    expect(abbreviateType('map[float]')).toBe('map[float]');
  });

  it('handles the real 17-field transcript type', () => {
    const fields = Array.from({ length: 17 }, (_, i) => `f${i}:integer`).join('|');
    expect(abbreviateType(`unique_array[${fields}]`)).toBe('unique_array[17 fields]');
  });

  it('is a no-op on empty or non-string input', () => {
    expect(abbreviateType('')).toBe('');
    expect(abbreviateType(undefined as unknown as string)).toBe('');
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `npm test -- typeAbbrev`
Expected: FAIL — cannot resolve `../contract`.

- [ ] **Step 3: Write the failing contract-derivation test**

```ts
// src/__tests__/contract.test.ts
import { describe, it, expect } from 'vitest';
import { deriveContract, contractCompleteness } from '../contract';
import type { ProcessNodeData } from '../types';

const DOC = `TranscriptInitiation — distributes activated RNAPs across TUs by weighted multinomial sampling.

    n_to_activate = round(f_active · n_total_RNAP) - n_active
    p_i = max(0, basal_prob_i + ∑_j delta_prob[i,j] · bound_TF_j)
    initiations ~ Multinomial(n_to_activate, p_i / ∑_i p_i)
  f_active: media-dependent active RNAP fraction.`;

function data(over: Partial<ProcessNodeData> = {}): ProcessNodeData {
  return {
    label: 'ecoli-transcript-initiation', nodeType: 'process', processType: 'step',
    address: 'local:X', config: {}, path: ['a'], inputPorts: ['bulk', 'RNAs'],
    outputPorts: ['bulk'], ...over,
  } as ProcessNodeData;
}

describe('deriveContract', () => {
  it('takes the first line as the summary', () => {
    const c = deriveContract(data({ description: DOC }))!;
    expect(c.summary).toMatch(/distributes activated RNAPs/);
    expect(c.summary).not.toContain('\n');
  });

  it('extracts equation lines as math', () => {
    const c = deriveContract(data({ description: DOC }))!;
    expect(c.math).toHaveLength(3);
    expect(c.math[0]).toContain('n_to_activate =');
    expect(c.math[2]).toContain('Multinomial');
  });

  it('keeps remaining prose as the description', () => {
    const c = deriveContract(data({ description: DOC }))!;
    expect(c.description).toContain('media-dependent active RNAP fraction');
    expect(c.description).not.toContain('n_to_activate =');
  });

  it('prefers a declared contract over the docstring', () => {
    const declared = { summary: 'declared', math: ['x = 1'], inputs: { bulk: 'reads counts' } };
    const c = deriveContract(data({ description: DOC, contract: declared } as any))!;
    expect(c.summary).toBe('declared');
    expect(c.inputs.bulk).toBe('reads counts');
  });

  it('returns null when there is nothing to derive from', () => {
    expect(deriveContract(data({ description: undefined }))).toBeNull();
  });

  it('yields a summary-only contract for a doc with no math', () => {
    const c = deriveContract(data({ description: 'Just a plain description.' }))!;
    expect(c.summary).toBe('Just a plain description.');
    expect(c.math).toEqual([]);
  });
});

describe('contractCompleteness', () => {
  it('counts documented ports against the real port list', () => {
    const c = { summary: 's', description: '', math: [], symbols: {},
      inputs: { bulk: 'reads' }, outputs: {}, config: {}, assumptions: [], references: [] };
    const r = contractCompleteness(c, data());
    expect(r.documented).toBe(1);
    expect(r.total).toBe(3);   // bulk + RNAs in, bulk out
  });

  it('flags a contract entry naming a port that does not exist', () => {
    const c = { summary: 's', description: '', math: [], symbols: {},
      inputs: { ghost: 'gone' }, outputs: {}, config: {}, assumptions: [], references: [] };
    expect(contractCompleteness(c, data()).unknownPorts).toEqual(['ghost']);
  });

  it('reports zero documented for a null contract', () => {
    expect(contractCompleteness(null, data()).documented).toBe(0);
  });
});
```

- [ ] **Step 4: Run and confirm failure**

Run: `npm test -- contract`
Expected: FAIL — cannot resolve `../contract`.

- [ ] **Step 5: Implement `src/contract.ts`**

```ts
// src/contract.ts — what a process advertises about itself.
//
// A process may declare a structured contract (serialized as `_contract`).
// When it does not, one is derived from its docstring: 45 of 46 v2ecoli
// baseline processes have a doc, and 14 already carry equations in the
// indented-block convention this parser reads. Derivation means the view
// works on day one and processes upgrade incrementally.

import type { ProcessNodeData } from './types';

export interface ProcessContract {
  summary: string;
  description: string;
  inputs: Record<string, string>;
  outputs: Record<string, string>;
  config: Record<string, string>;
  math: string[];
  symbols: Record<string, string>;
  assumptions: string[];
  references: string[];
}

/** Markers that make a docstring line an equation rather than prose. */
const MATH_RE = /[=~∑∏≈←≥≤]|\b(Multinomial|Binomial|Poisson|Normal|Gamma|Exponential)\s*\(/;

/** Structured types run past 300 chars; a card shows the shape, not the fields. */
export function abbreviateType(type: string): string {
  if (!type || typeof type !== 'string') return '';
  const m = type.match(/^([A-Za-z0-9_]+)\[(.*)\]$/s);
  if (!m) return type;
  const [, base, inner] = m;
  const fields = inner.split('|');
  // One "field" means it is a container like map[float] — keep it literal.
  if (fields.length < 2) return type;
  return `${base}[${fields.length} fields]`;
}

function emptyContract(): ProcessContract {
  return { summary: '', description: '', inputs: {}, outputs: {},
    config: {}, math: [], symbols: {}, assumptions: [], references: [] };
}

function fromDocstring(doc: string): ProcessContract {
  const c = emptyContract();
  const lines = doc.split('\n');
  const prose: string[] = [];

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (!c.summary) { c.summary = line; continue; }
    if (MATH_RE.test(line)) c.math.push(line);
    else prose.push(line);
  }
  c.description = prose.join(' ');
  return c;
}

/** The process's contract: declared if present, else derived from its doc. */
export function deriveContract(data: ProcessNodeData): ProcessContract | null {
  const declared = (data as unknown as { contract?: Partial<ProcessContract> }).contract;
  if (declared && typeof declared === 'object') {
    return { ...emptyContract(), ...declared };
  }
  const doc = data.description;
  if (!doc || !doc.trim()) return null;
  return fromDocstring(doc);
}

export interface Completeness {
  documented: number;
  total: number;
  /** Contract entries naming a port the process no longer has. */
  unknownPorts: string[];
}

export function contractCompleteness(
  c: ProcessContract | null,
  data: ProcessNodeData,
): Completeness {
  const inPorts = new Set(data.inputPorts ?? []);
  const outPorts = new Set(data.outputPorts ?? []);
  const total = inPorts.size + outPorts.size;
  if (!c) return { documented: 0, total, unknownPorts: [] };

  let documented = 0;
  const unknownPorts: string[] = [];
  for (const [port, text] of Object.entries(c.inputs)) {
    if (inPorts.has(port)) { if (text) documented++; } else unknownPorts.push(port);
  }
  for (const [port, text] of Object.entries(c.outputs)) {
    if (outPorts.has(port)) { if (text) documented++; } else unknownPorts.push(port);
  }
  return { documented, total, unknownPorts: unknownPorts.sort() };
}
```

- [ ] **Step 6: Declare the contract field in `src/types.ts`**

Add to `ProcessNodeData`, beside the schema fields added in Task 3:

```ts
  /** Structured contract, serialized as `_contract`. Absent means derive
   *  it from `description` (the process docstring). */
  contract?: Record<string, unknown>;
```

- [ ] **Step 7: Surface `_contract` in `convert.ts`**

`convert.ts:282-287` already maps `doc` → `description` and `_inputs`/`_outputs` → schemas. Add one line beside them, inside the same `data` object:

```ts
          contract: node._contract ?? undefined,
```

- [ ] **Step 8: Run both suites to verify they pass**

Run: `npm test -- contract typeAbbrev`
Expected: PASS, all cases.

- [ ] **Step 9: Commit**

```bash
git add src/contract.ts src/types.ts src/convert.ts src/__tests__/contract.test.ts src/__tests__/typeAbbrev.test.ts
git commit -m "feat(loom): process contract derivation and port-type abbreviation"
```

---

## Task 9: Five-tier semantic zoom on process cards

**Files:**
- Modify: `src/nodes/ProcessNode.tsx`, `src/layouts/processColumn.ts`, `src/App.tsx`, `src/App.css`
- Test: `src/__tests__/semanticZoom.test.tsx`

**Interfaces:**
- Consumes: `TIERS` (Task 5); `ZoomTierId` (Task 1); `deriveContract`, `abbreviateType`, `contractCompleteness` (Task 8)
- Produces: `tierForZoom(zoom: number, current?: ZoomTierId): ZoomTierId` from `src/layouts/processColumn.ts`

**Design constraints.**
1. Font sizes are **identical across all five tiers**. Legibility at low zoom comes from dropping content, never from shrinking text — the discipline the workbench's investigation graph follows (`static/aig-graph.js:91-99`). Do not add `transform: scale()` to card contents.
2. **A row with no data is omitted, never rendered empty.** Config is absent on 45/46 processes until the separate config plan lands, and contracts carry port semantics only once authored. An empty box reads as a bug.

- [ ] **Step 1: Replace the three-tier table in `src/layouts/processColumn.ts`**

The `TIERS` constant from Task 5 Step 3 becomes five entries. `cardHeight` is the *minimum*; real height is content-driven, and the column's prefix sum uses the value returned by `cardHeightFor` below.

```ts
export const TIERS: ZoomTier[] = [
  { id: 'glyph',    minZoom: 0,    cardWidth: 180, cardHeight: 56 },
  { id: 'ports',    minZoom: 0.25, cardWidth: 220, cardHeight: 96 },
  { id: 'types',    minZoom: 0.5,  cardWidth: 300, cardHeight: 150 },
  { id: 'contract', minZoom: 0.9,  cardWidth: 380, cardHeight: 240 },
  { id: 'full',     minZoom: 1.6,  cardWidth: 460, cardHeight: 320 },
];
```

Update `ZoomTierId` in `src/layouts/types.ts` to match:

```ts
export type ZoomTierId = 'glyph' | 'ports' | 'types' | 'contract' | 'full';
```

Every earlier reference to `'mid'` becomes `'ports'`, and `'far'`/`'near'` become `'glyph'`/`'full'`. The affected sites are `useLayoutMode.runLayout`'s default in `App.tsx` (Task 2 Step 10), and the `ctx` literals in `src/__tests__/processColumn.test.ts` (Task 5 Step 1).

- [ ] **Step 2: Write the failing tier tests**

```tsx
// src/__tests__/semanticZoom.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { tierForZoom } from '../layouts/processColumn';
import ProcessNode from '../nodes/ProcessNode';

describe('tierForZoom', () => {
  it('maps zoom onto the five tiers', () => {
    expect(tierForZoom(0.1)).toBe('glyph');
    expect(tierForZoom(0.3)).toBe('ports');
    expect(tierForZoom(0.7)).toBe('types');
    expect(tierForZoom(1.2)).toBe('contract');
    expect(tierForZoom(2.0)).toBe('full');
  });

  it('holds the current tier inside the hysteresis margin', () => {
    expect(tierForZoom(0.88, 'contract')).toBe('contract');
    expect(tierForZoom(0.80, 'contract')).toBe('types');
  });

  it('is stable when no current tier is supplied', () => {
    expect(tierForZoom(0.88)).toBe('types');
  });
});

const DOC = `Distributes activated RNAPs across TUs.

    p_i = max(0, basal_i + ∑_j dp[i,j] · TF_j)`;

const data = {
  label: 'ecoli-transcript-initiation', nodeType: 'process', processType: 'step',
  address: 'local:v2ecoli.processes.transcript_initiation.TranscriptInitiation',
  config: {}, interval: 2, path: ['agents', '0', 'ecoli-transcript-initiation'],
  inputPorts: ['bulk', 'RNAs'], outputPorts: ['bulk'],
  inputPortsSchema: { bulk: 'bulk', RNAs: 'unique.RNA' },
  outputPortsSchema: { bulk: 'bulk' },
  inputSchema: { bulk: 'bulk_array', RNAs: 'unique_array[a:integer|b:float|c:boolean]' },
  outputSchema: { bulk: 'bulk_array' },
  description: DOC,
} as any;

function renderAt(tier: string, over: Record<string, unknown> = {}) {
  render(
    <ReactFlowProvider>
      <ProcessNode id="p" type="process" selected={false} zIndex={0}
        isConnectable={false} dragging={false}
        data={{ ...data, ...over, _tier: tier }} {...({} as any)} />
    </ReactFlowProvider>,
  );
}

describe('ProcessNode tiers', () => {
  it('glyph shows only the name', () => {
    renderAt('glyph');
    expect(screen.getByText('ecoli-transcript-initiation')).toBeTruthy();
    expect(screen.queryByText(/2 in \/ 1 out/)).toBeNull();
  });

  it('ports adds port counts and port names', () => {
    renderAt('ports');
    expect(screen.getByText(/2 in \/ 1 out/)).toBeTruthy();
    expect(screen.getByText('RNAs')).toBeTruthy();
    expect(screen.queryByText(/3 fields/)).toBeNull();
  });

  it('types adds abbreviated port types and the address', () => {
    renderAt('types');
    expect(screen.getByText('unique_array[3 fields]')).toBeTruthy();
    expect(screen.getByText(/TranscriptInitiation/)).toBeTruthy();
    expect(screen.queryByText(/p_i = max/)).toBeNull();
  });

  it('contract adds the math lines', () => {
    renderAt('contract');
    expect(screen.getByText(/p_i = max/)).toBeTruthy();
  });

  it('full adds the completeness indicator', () => {
    renderAt('full');
    expect(screen.getByText(/0\/3 ports documented/)).toBeTruthy();
  });

  it('omits the config row entirely when config is empty', () => {
    renderAt('full');
    expect(screen.queryByText(/^config$/i)).toBeNull();
  });

  it('renders the config row when config is present', () => {
    renderAt('full', { config: { width_um: 1.1 } });
    expect(screen.getByText('width_um')).toBeTruthy();
  });

  it('pinned-open renders full detail regardless of tier', () => {
    renderAt('glyph', { _pinnedOpen: true });
    expect(screen.getByText(/p_i = max/)).toBeTruthy();
  });
});
```

- [ ] **Step 3: Run and confirm failure**

Run: `npm test -- semanticZoom`
Expected: FAIL — `tierForZoom is not a function`.

- [ ] **Step 4: Implement `tierForZoom`**

Append to `src/layouts/processColumn.ts`:

```ts
/** Zoom overlap a tier keeps once entered, so scrolling across a threshold
 *  does not flicker cards between two tiers. */
export const TIER_HYSTERESIS = 0.05;

export function tierForZoom(zoom: number, current?: ZoomTierId): ZoomTierId {
  let next: ZoomTierId = TIERS[0].id;
  for (const t of TIERS) if (zoom >= t.minZoom) next = t.id;
  if (!current || current === next) return next;

  // Only resist leaving the current tier, and only just inside its edge.
  const cur = TIERS.find((t) => t.id === current);
  if (cur && zoom >= cur.minZoom - TIER_HYSTERESIS) return current;
  return next;
}
```

- [ ] **Step 5: Rewrite the card body in `src/nodes/ProcessNode.tsx`**

Keep `_classifyStep` and the `process-node-${stepKind}` class untouched, and keep the existing `<Handle>` / `.port-label` block at the end unchanged — the wires still attach to it at every tier.

Add above the return:

```tsx
  const tier = ((data as any)._tier ?? 'ports') as
    'glyph' | 'ports' | 'types' | 'contract' | 'full';
  const t = (data as any)._pinnedOpen ? 'full' : tier;

  const show = {
    ports:    t !== 'glyph',
    types:    t === 'types' || t === 'contract' || t === 'full',
    contract: t === 'contract' || t === 'full',
    full:     t === 'full',
  };

  const contract = show.contract ? deriveContract(data) : null;
  const completeness = show.full ? contractCompleteness(contract, data) : null;
  const inTypes = (data as any).inputSchema ?? {};
  const outTypes = (data as any).outputSchema ?? {};
  const configEntries = Object.entries(data.config ?? {});

  const portRow = (port: string, types: Record<string, unknown>, isOut: boolean) => {
    const raw = typeof types[port] === 'string' ? (types[port] as string) : '';
    const semantic = isOut ? contract?.outputs?.[port] : contract?.inputs?.[port];
    return (
      <div key={`${isOut ? 'o' : 'i'}-${port}`}
           className={`process-node-port-row${isOut ? ' is-out' : ''}`}>
        <span className="process-node-port-name">{port}</span>
        {show.types && raw && (
          <span className="process-node-port-type" title={raw}>{abbreviateType(raw)}</span>
        )}
        {show.contract && semantic && (
          <span className="process-node-port-semantic">{semantic}</span>
        )}
      </div>
    );
  };
```

Then the card body, before the existing handle block:

```tsx
    <div className={`process-node process-node-${stepKind} process-node-${t}`}>
      <div className="process-node-title">{data.label}</div>

      {show.ports && (
        <>
          <div className="process-node-meta">
            {data.processType} · {inputPorts.length} in / {outputPorts.length} out
            {data.interval != null && <span> · every {data.interval}</span>}
          </div>
          <div className="process-node-ports">
            {inputPorts.map((p) => portRow(p, inTypes, false))}
            {outputPorts.map((p) => portRow(p, outTypes, true))}
          </div>
        </>
      )}

      {show.types && (data as any).address && (
        <div className="process-node-address">{(data as any).address}</div>
      )}

      {show.types && configEntries.length > 0 && (
        <div className="process-node-config">
          {configEntries.map(([k, v]) => (
            <div key={k} className="process-node-config-row">
              <span>{k}</span>
              {show.contract && <span>{String(v).slice(0, 40)}</span>}
            </div>
          ))}
        </div>
      )}

      {show.contract && contract?.summary && (
        <div className="process-node-summary">{contract.summary}</div>
      )}

      {show.contract && contract && contract.math.length > 0 && (
        <div className="process-node-math">
          {contract.math.map((m, i) => <div key={i}>{m}</div>)}
        </div>
      )}

      {show.full && contract && Object.keys(contract.symbols).length > 0 && (
        <div className="process-node-symbols">
          {Object.entries(contract.symbols).map(([s, meaning]) => (
            <div key={s}><em>{s}</em> — {meaning}</div>
          ))}
        </div>
      )}

      {show.full && contract?.description && (
        <div className="process-node-description">{contract.description}</div>
      )}

      {show.full && completeness && completeness.total > 0 && (
        <div className="process-node-completeness">
          {completeness.documented}/{completeness.total} ports documented
          {completeness.unknownPorts.length > 0 && (
            <span className="is-warn"> · unknown: {completeness.unknownPorts.join(', ')}</span>
          )}
        </div>
      )}

      {/* existing Handle + .port-label block, unchanged */}
    </div>
```

Add the imports at the top of the file:

```tsx
import { deriveContract, abbreviateType, contractCompleteness } from '../contract';
```

- [ ] **Step 6: Stamp the tier from the live viewport in `App.tsx`**

```ts
const [tier, setTier] = useState<ZoomTierId>('ports');

const onMove = useCallback((_: unknown, vp: { zoom: number }) => {
  setTier((cur) => tierForZoom(vp.zoom, cur));
}, []);
```

Pass `onMove={onMove}` to `<ReactFlow>`. Stamp the tier onto process nodes, creating new data objects **only when the tier or pin set actually changes** — otherwise the identity-preservation guarantee at `App.tsx:273-286` is defeated and the whole graph remounts on every pan:

```ts
const tieredNodes = useMemo(
  () => displayNodes.map((n) => (n.type !== 'process' ? n : {
    ...n,
    data: { ...n.data, _tier: tier, _pinnedOpen: focus.ctx.pinned.has(n.id) },
  })),
  [displayNodes, tier, focus.ctx.pinned],
);
```

Use `tieredNodes` as `<ReactFlow nodes={...}>`, and add `tier` to the layout effect's dependency array so the column re-flows when card heights change.

- [ ] **Step 7: Style the tiers**

Append to `src/App.css`. Font sizes are deliberately constant across tiers:

```css
/* Semantic zoom: tiers change WHICH rows exist, never their font size. */
.process-node-glyph    { width: 180px; min-height: 56px; }
.process-node-ports    { width: 220px; min-height: 96px; }
.process-node-types    { width: 300px; min-height: 150px; }
.process-node-contract { width: 380px; min-height: 240px; }
.process-node-full     { width: 460px; min-height: 320px; }

.process-node-title { font-size: 12px; font-weight: 600; color: #1e293b; line-height: 1.25; }
.process-node-meta  { font-size: 11px; color: #64748b; margin-top: 3px; }
.process-node-address {
  font-size: 11px; color: #94a3b8; font-family: ui-monospace, monospace;
  margin-top: 4px; overflow-wrap: anywhere;
}
.process-node-ports { margin-top: 5px; border-top: 1px dashed #e5e7eb; padding-top: 4px; }
.process-node-port-row {
  display: flex; gap: 8px; justify-content: space-between;
  font-size: 11px; color: #475569; line-height: 1.4;
}
.process-node-port-row.is-out { color: #0d9488; }
.process-node-port-name { font-weight: 600; }
.process-node-port-type { font-family: ui-monospace, monospace; color: #94a3b8; }
.process-node-port-semantic { color: #64748b; flex: 1 1 auto; text-align: right; }

.process-node-config { margin-top: 5px; border-top: 1px dashed #e5e7eb; padding-top: 4px; }
.process-node-config-row {
  display: flex; justify-content: space-between; gap: 8px;
  font-size: 11px; color: #475569; font-family: ui-monospace, monospace;
}

.process-node-summary { font-size: 11px; color: #334155; margin-top: 6px; line-height: 1.4; }
.process-node-math {
  margin-top: 5px; padding: 4px 6px; background: #f8fafc;
  border-left: 2px solid #cbd5e1; border-radius: 3px;
  font-family: ui-monospace, monospace; font-size: 11px; color: #1e293b;
  line-height: 1.5; overflow-x: auto;
}
.process-node-symbols     { font-size: 11px; color: #64748b; margin-top: 5px; line-height: 1.4; }
.process-node-description { font-size: 11px; color: #64748b; margin-top: 5px; line-height: 1.4; }
.process-node-completeness {
  font-size: 10px; color: #94a3b8; margin-top: 6px;
  border-top: 1px solid #f1f5f9; padding-top: 3px;
}
.process-node-completeness .is-warn { color: #b45309; }
```

- [ ] **Step 8: Run to verify pass**

Run: `npm test -- semanticZoom`
Expected: PASS, all eleven cases.

- [ ] **Step 9: Full suite, typecheck, build**

Run: `npm test && npx tsc -b --noEmit && npm run build`
Expected: all PASS. If `processColumn.test.ts` fails on a `'mid'` literal, update it to `'ports'` per Step 1.

- [ ] **Step 10: Verify against the real composite**

```bash
npm run dev
```

Switch to Process column and zoom slowly from far out to fully in on `ecoli-transcript-initiation`. Confirm all five criteria:
1. Each zoom step adds exactly one new kind of information, no jumps.
2. Port types read as `unique_array[17 fields]`, never a 300-character string; hovering shows the full type.
3. The math block appears at the `contract` tier with the multinomial equations.
4. No empty rows anywhere — config is absent on this composite until the config plan lands, so the config row should simply not appear.
5. No card flicker when scrolling back and forth across a tier threshold.

- [ ] **Step 11: Rebuild the vendored bundle the workbench serves**

```bash
cd /Users/eranagmon/code/vivarium-dashboard && ./scripts/build_loom.sh
```

Then confirm in the workbench: `vivarium-workbench serve --workspace /Users/eranagmon/code/v2ecoli`, open the baseline in Composite Explorer, Wiring tab.

- [ ] **Step 12: Commit**

```bash
git add src/nodes/ProcessNode.tsx src/layouts/processColumn.ts src/layouts/types.ts src/App.tsx src/App.css src/__tests__
git commit -m "feat(loom): five-tier semantic zoom with contract math and abbreviated port types"
```

---
## Task 10: Semantic zoom on stores and edges

**Files:**
- Create: `src/storeFacts.ts`
- Modify: `src/nodes/StoreNode.tsx`, `src/edges/FloatingStoreEdge.tsx`, `src/App.tsx`, `src/App.css`
- Test: `src/__tests__/storeTiers.test.tsx`, `src/__tests__/edgeTiers.test.tsx`

**Interfaces:**
- Consumes: `ZoomTierId` (Task 1); `abbreviateType`, `deriveContract` (Task 8); `tierForZoom` (Task 9)
- Produces: from `src/storeFacts.ts` — `readersAndWriters(storeId: string, edges: Edge[]): { readers: string[]; writers: string[] }`

**Why stores and edges too.** The tier is a property of the **view**, not of the process card. If only process cards tiered, the canvas would reveal detail unevenly — dense typed cards floating among bare circles. Edge tiering is additionally a performance lever: labelling ~400 edges at low zoom costs text layout for glyphs nobody can read.

- [ ] **Step 1: Write the failing reader/writer test**

Direction comes free from the existing edge model: `convert.ts` emits `edgeType: 'input'` for store→process (the process *reads*) and `'output'` for process→store (the process *writes*).

```ts
// src/__tests__/storeTiers.test.tsx
import { describe, it, expect } from 'vitest';
import type { Edge } from '@xyflow/react';
import { readersAndWriters } from '../storeFacts';

const edges = [
  { id: 'e1', source: 'unique.RNA', target: 'transcript-init', data: { edgeType: 'input' } },
  { id: 'e2', source: 'unique.RNA', target: 'rna-degradation', data: { edgeType: 'input' } },
  { id: 'e3', source: 'transcript-init', target: 'unique.RNA', data: { edgeType: 'output' } },
  { id: 'e4', source: 'other', target: 'bulk', data: { edgeType: 'output' } },
  { id: 'e5', source: 'unique', target: 'unique.RNA', data: { edgeType: 'place' } },
] as unknown as Edge[];

describe('readersAndWriters', () => {
  it('lists processes that read the store', () => {
    expect(readersAndWriters('unique.RNA', edges).readers.sort())
      .toEqual(['rna-degradation', 'transcript-init']);
  });

  it('lists processes that write the store', () => {
    expect(readersAndWriters('unique.RNA', edges).writers).toEqual(['transcript-init']);
  });

  it('ignores structural place edges', () => {
    const r = readersAndWriters('unique.RNA', edges);
    expect(r.readers).not.toContain('unique');
    expect(r.writers).not.toContain('unique');
  });

  it('deduplicates a process wired through several ports', () => {
    const many = [
      { id: 'a', source: 'S', target: 'p', data: { edgeType: 'input' } },
      { id: 'b', source: 'S', target: 'p', data: { edgeType: 'input' } },
    ] as unknown as Edge[];
    expect(readersAndWriters('S', many).readers).toEqual(['p']);
  });

  it('returns empty lists for an unwired store', () => {
    expect(readersAndWriters('nope', edges)).toEqual({ readers: [], writers: [] });
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `npm test -- storeTiers`
Expected: FAIL — cannot resolve `../storeFacts`.

- [ ] **Step 3: Implement `src/storeFacts.ts`**

```ts
// src/storeFacts.ts — facts about a store derived from the wire graph.
//
// The store-side counterpart of the process contract: a process declares
// what it does with a port, and a store can report who touches it. Both
// come from data already in the document.

import type { Edge } from '@xyflow/react';

export interface StoreFacts {
  /** Processes that read this store (store -> process, edgeType 'input'). */
  readers: string[];
  /** Processes that write this store (process -> store, edgeType 'output'). */
  writers: string[];
}

export function readersAndWriters(storeId: string, edges: Edge[]): StoreFacts {
  const readers = new Set<string>();
  const writers = new Set<string>();
  for (const e of edges) {
    const kind = (e.data as { edgeType?: string } | undefined)?.edgeType;
    if (kind === 'input' && e.source === storeId) readers.add(e.target);
    else if (kind === 'output' && e.target === storeId) writers.add(e.source);
  }
  return { readers: [...readers].sort(), writers: [...writers].sort() };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm test -- storeTiers`
Expected: PASS, all five cases.

- [ ] **Step 5: Write the failing store-render test**

Append to `src/__tests__/storeTiers.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import StoreNode from '../nodes/StoreNode';

const storeData = {
  label: 'RNA', nodeType: 'store', path: ['agents', '0', 'unique', 'RNA'],
  value: 'Array(8)', valueType: 'unique_array[a:integer|b:float|c:boolean]',
  isGroup: false,
} as any;

function renderStore(tier: string, over: Record<string, unknown> = {}) {
  render(
    <ReactFlowProvider>
      <StoreNode id="unique.RNA" type="store" selected={false} zIndex={0}
        isConnectable={false} dragging={false}
        data={{ ...storeData, ...over, _tier: tier }} {...({} as any)} />
    </ReactFlowProvider>,
  );
}

describe('StoreNode tiers', () => {
  it('glyph shows only the name', () => {
    renderStore('glyph');
    expect(screen.getByText('RNA')).toBeTruthy();
    expect(screen.queryByText('Array(8)')).toBeNull();
  });

  it('ports adds the value summary', () => {
    renderStore('ports');
    expect(screen.getByText('Array(8)')).toBeTruthy();
    expect(screen.queryByText(/3 fields/)).toBeNull();
  });

  it('types adds the abbreviated declared type', () => {
    renderStore('types');
    expect(screen.getByText('unique_array[3 fields]')).toBeTruthy();
  });

  it('contract adds the reader/writer summary', () => {
    renderStore('contract', { _readers: ['a', 'b'], _writers: ['a'] });
    expect(screen.getByText(/2 read/)).toBeTruthy();
    expect(screen.getByText(/1 write/)).toBeTruthy();
  });

  it('omits the reader/writer row when the store is unwired', () => {
    renderStore('contract', { _readers: [], _writers: [] });
    expect(screen.queryByText(/read/)).toBeNull();
  });
});
```

- [ ] **Step 6: Run and confirm failure**

Run: `npm test -- storeTiers`
Expected: FAIL — the store renders the same content at every tier.

- [ ] **Step 7: Add tiers to `src/nodes/StoreNode.tsx`**

Keep the four existing `<Handle>` elements (`top-place`, `left-out`, `right-in`, `bottom-place`) and the `▶/▼` group indicator exactly as they are — collapse behavior and edge attachment depend on them.

Add above the return:

```tsx
  const tier = ((data as any)._tier ?? 'ports') as
    'glyph' | 'ports' | 'types' | 'contract' | 'full';
  const readers: string[] = (data as any)._readers ?? [];
  const writers: string[] = (data as any)._writers ?? [];
  const rawType = typeof (data as any).valueType === 'string' ? (data as any).valueType : '';

  const show = {
    value:   tier !== 'glyph',
    type:    tier === 'types' || tier === 'contract' || tier === 'full',
    wiring:  tier === 'contract' || tier === 'full',
    full:    tier === 'full',
  };
```

Inside the circle body, after the existing label element:

```tsx
      {show.value && data.value != null && (
        <div className="store-node-value">{String(data.value)}</div>
      )}
      {show.type && rawType && (
        <div className="store-node-type" title={rawType}>{abbreviateType(rawType)}</div>
      )}
      {show.wiring && (readers.length > 0 || writers.length > 0) && (
        <div className="store-node-wiring">
          {readers.length > 0 && <span>{readers.length} read</span>}
          {readers.length > 0 && writers.length > 0 && <span> · </span>}
          {writers.length > 0 && <span>{writers.length} write</span>}
        </div>
      )}
      {show.full && (data as any)._emitted && (
        <div className="store-node-emit">emitted</div>
      )}
```

Import at the top:

```tsx
import { abbreviateType } from '../contract';
```

- [ ] **Step 8: Write the failing edge-tier test**

```tsx
// src/__tests__/edgeTiers.test.tsx
import { describe, it, expect } from 'vitest';
import { edgeLabelFor } from '../edges/FloatingStoreEdge';

const base = { port: 'RNAs', portType: 'unique_array[a:integer|b:float]',
  semantic: 'appends newly initiated transcripts' };

describe('edgeLabelFor', () => {
  it('renders nothing at glyph', () => {
    expect(edgeLabelFor('glyph', base)).toBe('');
  });

  it('renders the port name at ports', () => {
    expect(edgeLabelFor('ports', base)).toBe('RNAs');
  });

  it('adds the abbreviated type at types', () => {
    expect(edgeLabelFor('types', base)).toBe('RNAs: unique_array[2 fields]');
  });

  it('adds the contract semantic at contract', () => {
    expect(edgeLabelFor('contract', base))
      .toBe('RNAs: unique_array[2 fields] — appends newly initiated transcripts');
  });

  it('degrades when the contract has no semantic for the port', () => {
    expect(edgeLabelFor('contract', { ...base, semantic: undefined }))
      .toBe('RNAs: unique_array[2 fields]');
  });

  it('degrades when no type is known', () => {
    expect(edgeLabelFor('types', { port: 'x' })).toBe('x');
  });
});
```

- [ ] **Step 9: Run and confirm failure**

Run: `npm test -- edgeTiers`
Expected: FAIL — `edgeLabelFor is not exported`.

- [ ] **Step 10: Implement `edgeLabelFor` and use it in the edge renderer**

Add to `src/edges/FloatingStoreEdge.tsx`, exported so it is testable without rendering an SVG:

```tsx
import { abbreviateType } from '../contract';
import type { ZoomTierId } from '../layouts/types';

export interface EdgeLabelParts {
  port: string;
  portType?: string;
  semantic?: string;
}

/** What a wire says about itself at a given tier. Empty string means no
 *  label at all — which is also the perf path, since ~400 edges labelled
 *  at low zoom costs text layout for glyphs nobody can read. */
export function edgeLabelFor(tier: ZoomTierId, parts: EdgeLabelParts): string {
  if (tier === 'glyph') return '';
  let label = parts.port;
  if (tier === 'ports') return label;
  if (parts.portType) label += `: ${abbreviateType(parts.portType)}`;
  if ((tier === 'contract' || tier === 'full') && parts.semantic) {
    label += ` — ${parts.semantic}`;
  }
  return label;
}
```

In the component body, replace the current unconditional label render with:

```tsx
  const tier = ((data as any)?._tier ?? 'ports') as ZoomTierId;
  const label = edgeLabelFor(tier, {
    port: (data as any)?.port ?? '',
    portType: (data as any)?._portType,
    semantic: (data as any)?._semantic,
  });
```

and render the label element only when `label` is non-empty.

- [ ] **Step 11: Run to verify pass**

Run: `npm test -- edgeTiers`
Expected: PASS, all six cases.

- [ ] **Step 12: Stamp tier and derived facts in `App.tsx`**

Extend the `tieredNodes` memo from Task 9 Step 6 to cover stores, and add an equivalent for edges. Keep both memos keyed so new objects are created **only** when the tier actually changes — the identity guarantee at `App.tsx:273-286` depends on it.

```ts
const tieredNodes = useMemo(
  () => displayNodes.map((n) => {
    if (n.type === 'process') {
      return { ...n, data: { ...n.data, _tier: tier, _pinnedOpen: focus.ctx.pinned.has(n.id) } };
    }
    // Reader/writer facts are only needed from the 'contract' tier up;
    // computing them for every store at every tier would be wasted work.
    const wiring = (tier === 'contract' || tier === 'full')
      ? readersAndWriters(n.id, rawEdges) : { readers: [], writers: [] };
    return { ...n, data: { ...n.data, _tier: tier,
      _readers: wiring.readers, _writers: wiring.writers } };
  }),
  [displayNodes, rawEdges, tier, focus.ctx.pinned],
);

const tieredEdges = useMemo(() => {
  if (tier === 'glyph') return visibleEdges;   // no labels, nothing to stamp
  return visibleEdges.map((e) => {
    const proc = nodeById.get(
      (e.data as any)?.edgeType === 'input' ? e.target : e.source);
    const pdata = proc?.data as ProcessNodeData | undefined;
    const port = (e.data as any)?.port ?? '';
    const isOut = (e.data as any)?.edgeType === 'output';
    const types = (pdata as any)?.[isOut ? 'outputSchema' : 'inputSchema'] ?? {};
    const contract = pdata ? deriveContract(pdata) : null;
    return { ...e, data: { ...e.data, _tier: tier,
      _portType: typeof types[port] === 'string' ? types[port] : undefined,
      _semantic: isOut ? contract?.outputs?.[port] : contract?.inputs?.[port] } };
  });
}, [visibleEdges, nodeById, tier]);
```

Add a `nodeById` memo beside them if one does not already exist:

```ts
const nodeById = useMemo(() => new Map(displayNodes.map((n) => [n.id, n])), [displayNodes]);
```

Pass `nodes={tieredNodes}` and `edges={tieredEdges}` to `<ReactFlow>`.

- [ ] **Step 13: Style the store and edge tiers**

Append to `src/App.css`. Font sizes stay constant across tiers, as with the process card:

```css
.store-node-value  { font-size: 10px; color: #64748b; line-height: 1.3; }
.store-node-type   { font-size: 10px; color: #94a3b8; font-family: ui-monospace, monospace; }
.store-node-wiring { font-size: 10px; color: #475569; }
.store-node-emit   { font-size: 9px; color: #0d9488; font-weight: 600; }

/* A store circle grows to fit its tier's content rather than clipping it. */
.store-node.store-node-types,
.store-node.store-node-contract,
.store-node.store-node-full { width: auto; min-width: 80px; padding: 6px 10px; border-radius: 12px; }
```

- [ ] **Step 14: Full suite, typecheck, build**

Run: `npm test && npx tsc -b --noEmit && npm run build`
Expected: all PASS, no type errors.

- [ ] **Step 15: Verify against the real composite**

```bash
npm run dev
```

Switch to Process column and zoom slowly through all five tiers. Confirm:
1. Processes, stores, and edges reveal detail **together** — no element kind races ahead or lags.
2. At `glyph`, no edge labels render at all, and panning is noticeably smoother than at `contract`.
3. At `types`, both port types and store types read as `…[N fields]`, never raw.
4. At `contract`, hovering a focused process shows wires labelled with the port semantic where the contract supplies one, and just `port: type` where it does not.
5. Stores show plausible reader/writer counts — `bulk` should report many readers, a private store few.

- [ ] **Step 16: Rebuild the vendored bundle**

```bash
cd /Users/eranagmon/code/vivarium-dashboard && ./scripts/build_loom.sh
```

- [ ] **Step 17: Commit**

```bash
git add src/storeFacts.ts src/nodes/StoreNode.tsx src/edges/FloatingStoreEdge.tsx src/App.tsx src/App.css src/__tests__
git commit -m "feat(loom): semantic zoom on stores and edges, matching the process tiers"
```

---
## Self-Review Notes

**Spec coverage.** §1 registry → Tasks 1–2. §2 clustering → Tasks 3–4. §3 column layout → Task 5. §4 focus/edges → Task 6. §5 process contract → Task 8 (loom-side derivation and rendering; the process-bigraph `ProcessContract` itself is the companion plan). §6 semantic zoom → Tasks 9 (processes) and 10 (stores, edges). §7 config → the companion plan, out of scope here. §8 persistence → Task 2 steps 3–8. §9 module boundaries → `useLayoutMode` (Task 2), `useFocus` (Task 6), `contract.ts` (Task 8), `storeFacts.ts` (Task 10). §11 testing → every task's test file, plus the fixture in Task 3.

**Companion plan.** `docs/superpowers/plans/2026-07-23-process-contract-and-config.md` covers spec §5's `ProcessContract` dataclass and §7's config fixes. Those span process-bigraph and v2ecoli, not loom, and are independently useful. This plan's Tasks 8–10 degrade gracefully without them: a contract is derived from the docstring, and rows with no data are omitted rather than rendered empty.

**Known gap, deliberately deferred.** `captureCurrentView` (`App.tsx:393-398`) and `applyView` (`App.tsx:403-409`) are not updated to read or write the new `mode`/`pins` fields, so saved views round-trip them as defaults rather than live state. Called out so it is not mistaken for done. Nothing blocks on it, since `normalizeView` guarantees old views keep working.

**Type consistency check.** `ZoomTierId` is `'glyph' | 'ports' | 'types' | 'contract' | 'full'` from Task 9 Step 1 onward; Tasks 1, 2, and 5 are written against the earlier three-value form and Task 9 Step 1 explicitly lists every site to update (`App.tsx` default, `processColumn.test.ts` ctx literals). `abbreviateType` is defined once in Task 8 and consumed by Tasks 9 and 10. `deriveContract` returns `ProcessContract | null`, and every consumer null-checks. `readersAndWriters` (Task 10) returns sorted, deduplicated arrays.
