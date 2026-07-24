// src/layouts/processColumn.ts — processes in one clustered column, stores
// packed into a compact hierarchical block to the right.
//
// The column is one-dimensional, so re-flowing it for a zoom-tier change is
// a prefix sum rather than a graph layout. That is what makes semantic zoom
// affordable at several hundred nodes.
//
// The store side does NOT use the hierarchy mode's ELK pass. ELK lays every
// sibling set out in a single row, which on the real v2ecoli baseline (two
// stores with 87 leaf children each) is a 37,980px-wide, 1,680px-tall ribbon:
// framed next to a 220px column at a 1400px viewport that is zoom ≈ 0.03, i.e.
// the process column renders ~8px wide and the whole mode is invisible by
// default. `hierarchy.ts` hit exactly this failure for its own process grid and
// fixed it by wrapping into columns to keep the block roughly square (see its
// comment at "Place process nodes in a GRID"); `packStoreTree` below does the
// same for stores, wrapping each sibling set into shelves. It is a private
// layout for THIS mode — hierarchy mode's ELK output is untouched.

import type { Node, Edge } from '@xyflow/react';
import { clusterProcesses } from './affinity';
import type {
  LayoutMode, LayoutResult, LayoutContext, FocusContext, GroupBand, ZoomTier,
  ZoomTierId,
} from './types';

export const TIERS: ZoomTier[] = [
  { id: 'glyph',    minZoom: 0,    cardWidth: 180, cardHeight: 56 },
  { id: 'ports',    minZoom: 0.25, cardWidth: 220, cardHeight: 96 },
  { id: 'types',    minZoom: 0.5,  cardWidth: 300, cardHeight: 150 },
  { id: 'contract', minZoom: 0.9,  cardWidth: 380, cardHeight: 240 },
  { id: 'full',     minZoom: 1.6,  cardWidth: 460, cardHeight: 320 },
];

/** Zoom overlap a tier keeps once entered, so scrolling across a threshold
 *  does not flicker cards between two tiers. */
export const TIER_HYSTERESIS = 0.05;

export function tierForZoom(zoom: number, current?: ZoomTierId): ZoomTierId {
  // Raw tier for this zoom: the highest tier (TIERS is ascending by minZoom)
  // whose lower edge the zoom has reached.
  let rawIdx = 0;
  for (let i = 0; i < TIERS.length; i++) if (zoom >= TIERS[i].minZoom) rawIdx = i;
  const raw = TIERS[rawIdx].id;
  if (!current) return raw;

  const curIdx = TIERS.findIndex((t) => t.id === current);
  if (curIdx < 0 || raw === current) return raw;

  // Zooming IN (raw is a higher tier): advance immediately — by definition of
  // the raw tier, `zoom` has already passed the target tier's minZoom. Applying
  // hysteresis here is what stalled every upward transition.
  if (rawIdx > curIdx) return raw;

  // Zooming OUT (raw is a lower tier): hold the current tier until `zoom` dips a
  // full TIER_HYSTERESIS below the current tier's lower edge, so a small wobble
  // across the threshold does not flicker a tier. The margin (0.05) is smaller
  // than every gap between adjacent minZooms (>=0.25), so no tier is skipped.
  if (zoom >= TIERS[curIdx].minZoom - TIER_HYSTERESIS) return current;
  return raw;
}

export const CARD_GAP = 16;
export const CLUSTER_GAP = 44;
export const GUTTER = 180;

/** Store circle footprint, matching the size hierarchy.ts feeds ELK. */
export const STORE_W = 80;
export const STORE_H = 80;
/** Gaps inside the packed store block: between siblings, and parent → children. */
const STORE_H_GAP = 40;
const STORE_V_GAP = 60;

/**
 * Trailing band for processes `clusterProcesses` never sees — it filters out
 * bookkeeping processes (`*listener*`, `allocator*`, `*unique_update*`), which
 * on the real v2ecoli baseline is 19 of 46. Those nodes are hidden by default
 * but they ARE handed to the layout (App's layout effect only filters by
 * `collapsed`, not by `hidden`), so without a slot here they would keep
 * whatever position a previous mode left them at and land on top of the store
 * hierarchy the moment a user unhides them.
 */
export const UNCLUSTERED_KEY = '~unclustered';

/**
 * Slider default. The granularity → hubFraction map below is ANCHORED here, so
 * the app's default path runs the clustering at exactly the hubFraction that
 * `affinityFixture.test.ts` validates (0.30). Keep the two in lockstep: that
 * boundary is fragile (at n=27, hubFraction 0.30 vs 0.275 differ only because
 * no store happens to sit at df=7 — see AffinityOptions.hubFraction), so a
 * default that merely lands "near" the validated value is a latent regression.
 */
export const DEFAULT_GRANULARITY = 0.5;

/** The validated hubFraction (see affinityFixture.test.ts), reproduced exactly
 *  at DEFAULT_GRANULARITY. */
const HUB_FRACTION_AT_DEFAULT = 0.30;
/** Full slider travel, i.e. ±half this around the anchor: 0.20 .. 0.40. */
const HUB_FRACTION_SPAN = 0.20;

/** Map the rail's coarse..fine granularity slider onto a hub threshold.
 *  Lower hubFraction disqualifies more stores as keys, giving finer groups.
 *  Written as an offset FROM the anchor so `hubFractionFor(DEFAULT_GRANULARITY)`
 *  is bit-exactly HUB_FRACTION_AT_DEFAULT, not a float a hair off it. */
export function hubFractionFor(granularity: number): number {
  const g = Math.min(1, Math.max(0, granularity));
  return HUB_FRACTION_AT_DEFAULT + (g - DEFAULT_GRANULARITY) * HUB_FRACTION_SPAN;
}

/**
 * Find the store node a cluster key names.
 *
 * Cluster keys are RELATIVE to the process's parent store (`unique.RNA`), while
 * store node ids are absolute dotted paths (`agents.0.unique.RNA`). The key
 * store's id is therefore exactly `<member process's parent path> + <key>` — an
 * O(1) lookup, no scanning. Resolving by unique id SUFFIX (the previous
 * approach) is not equivalent: when the in-scope store is absent — inside a
 * collapsed subtree, say — a lone match in an unrelated branch looks "unique"
 * and gets returned, which highlights the wrong store.
 *
 * Members can disagree only when a cluster spans agents (`agents.0` and
 * `agents.1` both wiring `unique.RNA`); the key then identifies no single
 * store, so report none rather than pick arbitrarily.
 */
function resolveKeyStoreId(
  key: string,
  memberIds: string[],
  nodeById: Map<string, Node>,
  storeIds: Set<string>,
): string | null {
  if (key.startsWith('~')) return null;          // terminal buckets name no store
  const segments = key.split('.');
  let found: string | null = null;
  for (const id of memberIds) {
    const path = (nodeById.get(id)?.data as { path?: unknown } | undefined)?.path;
    if (!Array.isArray(path) || path.length === 0) continue;
    // Wire targets are written relative to the process's PARENT store.
    const candidate = [...(path as string[]).slice(0, -1), ...segments].join('.');
    if (!storeIds.has(candidate)) continue;
    if (found !== null && found !== candidate) return null;   // spans scopes
    found = candidate;
  }
  return found;
}

/**
 * Lay the store hierarchy out as a compact block: every sibling set is
 * shelf-packed into rows whose width budget is √(their total area), so each
 * subtree — and therefore the whole block — comes out roughly square instead of
 * ELK's single-row ribbon. Parents sit centered above their children, so
 * nesting still reads top-down exactly as in hierarchy mode.
 *
 * Returns absolute positions with the block's top-left at (0, 0). Pure and
 * deterministic: sibling order is the input order (convert.ts's stable walk).
 */
export function packStoreTree(storeNodes: Node[]): Map<string, { x: number; y: number }> {
  const pos = new Map<string, { x: number; y: number }>();
  if (storeNodes.length === 0) return pos;

  const pathOf = (n: Node): string[] =>
    (Array.isArray((n.data as { path?: unknown })?.path)
      ? ((n.data as { path: string[] }).path) : []);

  // Index by dotted path so a node's parent can be found by name. A node whose
  // parent store is not present (collapsed subtree, or a top-level store) is a
  // root of the packed forest — same fallback hierarchy.ts's ELK build uses.
  const byPath = new Map<string, Node>();
  for (const n of storeNodes) {
    const p = pathOf(n);
    if (p.length) byPath.set(p.join('.'), n);
  }
  const childIds = new Map<string, string[]>();
  const rootIds: string[] = [];
  for (const n of storeNodes) {
    const p = pathOf(n);
    const parent = p.length > 1 ? byPath.get(p.slice(0, -1).join('.')) : undefined;
    if (parent && parent.id !== n.id) {
      const list = childIds.get(parent.id);
      if (list) list.push(n.id); else childIds.set(parent.id, [n.id]);
    } else {
      rootIds.push(n.id);
    }
  }

  /** A laid-out subtree: its size, plus a deferred placement at an origin. */
  type Block = { w: number; h: number; place: (x: number, y: number) => void };

  function packForest(ids: string[]): Block {
    if (ids.length === 0) return { w: 0, h: 0, place: () => {} };
    const blocks = ids.map(packNode);
    // Shelf-pack to a √area width budget → an ~1:1 block for any sibling count.
    const area = blocks.reduce((a, b) => a + (b.w + STORE_H_GAP) * (b.h + STORE_V_GAP), 0);
    const budget = Math.max(...blocks.map((b) => b.w), Math.sqrt(area));
    const rows: Block[][] = [];
    let row: Block[] = [];
    let rowW = 0;
    for (const b of blocks) {
      const add = row.length === 0 ? b.w : STORE_H_GAP + b.w;
      if (row.length > 0 && rowW + add > budget) { rows.push(row); row = []; rowW = 0; }
      row.push(b);
      rowW += row.length === 1 ? b.w : STORE_H_GAP + b.w;
    }
    if (row.length) rows.push(row);

    const rowW_ = (r: Block[]) => r.reduce((a, b) => a + b.w, 0) + STORE_H_GAP * (r.length - 1);
    const rowH_ = (r: Block[]) => Math.max(...r.map((b) => b.h));
    const w = Math.max(...rows.map(rowW_));
    const h = rows.reduce((a, r) => a + rowH_(r), 0) + STORE_V_GAP * (rows.length - 1);
    return {
      w,
      h,
      place: (x, y) => {
        let ry = y;
        for (const r of rows) {
          let rx = x + (w - rowW_(r)) / 2;          // center each shelf
          for (const b of r) { b.place(rx, ry); rx += b.w + STORE_H_GAP; }
          ry += rowH_(r) + STORE_V_GAP;
        }
      },
    };
  }

  function packNode(id: string): Block {
    const kids = childIds.get(id) ?? [];
    if (kids.length === 0) {
      return { w: STORE_W, h: STORE_H, place: (x, y) => pos.set(id, { x, y }) };
    }
    const inner = packForest(kids);
    const w = Math.max(STORE_W, inner.w);
    const h = STORE_H + STORE_V_GAP + inner.h;
    return {
      w,
      h,
      place: (x, y) => {
        pos.set(id, { x: x + (w - STORE_W) / 2, y });     // parent centered on top
        inner.place(x + (w - inner.w) / 2, y + STORE_H + STORE_V_GAP);
      },
    };
  }

  packForest(rootIds).place(0, 0);
  // Round so positions are stable strings across runs / persisted views.
  for (const [id, p] of pos) pos.set(id, { x: Math.round(p.x), y: Math.round(p.y) });
  return pos;
}

export const processColumnMode: LayoutMode = {
  id: 'process-column',
  label: 'Process column',
  tiers: TIERS,

  async run(nodes: Node[], _edges: Edge[], ctx: LayoutContext): Promise<LayoutResult> {
    const tier = TIERS.find((t) => t.id === ctx.tier) ?? TIERS[1];
    const { clusters } = clusterProcesses(nodes, { hubFraction: hubFractionFor(ctx.granularity) });

    // Stores keep their nesting but are packed compactly, shifted right of the
    // column (see packStoreTree for why not the ELK hierarchy pass).
    const storeNodes = nodes.filter((n) => n.type !== 'process');
    const packed = packStoreTree(storeNodes);

    const minStoreX = packed.size ? Math.min(...[...packed.values()].map((p) => p.x)) : 0;
    const shift = (tier.cardWidth + GUTTER) - minStoreX;
    const storePosById = new Map(
      [...packed].map(([id, p]) => [id, { x: p.x + shift, y: p.y }] as const),
    );
    const storeIds = new Set(storeNodes.map((n) => n.id));
    const nodeById = new Map(nodes.map((n) => [n.id, n]));

    // Every process gets a band, even the bookkeeping ones clustering drops.
    const clustered = new Set(clusters.flatMap((c) => c.processIds));
    const leftovers = nodes
      .filter((n) => n.type === 'process' && !clustered.has(n.id))
      .map((n) => n.id)
      .sort();
    const groups = leftovers.length
      ? [...clusters, { key: UNCLUSTERED_KEY, label: 'bookkeeping', processIds: leftovers }]
      : clusters;

    // Column: prefix-sum down the clusters. O(n), no graph layout.
    const posById = new Map<string, { x: number; y: number }>();
    const bands: GroupBand[] = [];
    let y = 0;
    for (const c of groups) {
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
        keyStoreId: resolveKeyStoreId(c.key, c.processIds, nodeById, storeIds),
        nodeIds: [...c.processIds],
      });
      y += CLUSTER_GAP;
    }

    const out = nodes.map((n) => {
      const p = posById.get(n.id) ?? storePosById.get(n.id);
      return p ? { ...n, position: p } : n;
    });

    return { nodes: out, bands };
  },

  /**
   * Cull wires down to the focused processes' own wiring. This is the point of
   * the mode: the v2ecoli baseline emits ~400 wire edges, which as a single
   * drawing is a hairball no reader can trace. Drawing only the store hierarchy
   * until a process is hovered/selected/pinned turns the canvas into "one
   * process's neighbourhood at a time".
   *
   * Focus ids are node ids, so this also works when the hovered node is a
   * STORE: every wire into or out of that store is revealed, which is the
   * natural read of "what touches this store".
   *
   * O(edges) with a Set membership test per edge — no node scan — because it
   * re-runs on every hover. `nodes` is part of the seam (other modes may need
   * it) but is deliberately unused here so a node drag cannot make this work
   * grow. Returns the INPUT array identity when nothing is culled, so
   * pass-through cases don't force React Flow to re-derive its edge store.
   */
  edgeVisibility(edges: Edge[], focus: FocusContext, _nodes: Node[]): Edge[] {
    const active = new Set<string>([...focus.focused, ...focus.pinned]);
    const out = edges.filter((e) => {
      const kind = (e.data as { edgeType?: string } | undefined)?.edgeType;
      // Place edges are the store hierarchy: structural, few, always drawn.
      if (kind === 'place') return true;
      if (active.size === 0) return false;
      return active.has(e.source) || active.has(e.target);
    });
    return out.length === edges.length ? edges : out;
  },
};
