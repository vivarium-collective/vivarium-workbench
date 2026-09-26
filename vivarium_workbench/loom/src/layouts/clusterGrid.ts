// src/layouts/clusterGrid.ts — the sole canvas layout: a compact, DETERMINISTIC
// FORCE-DIRECTED packing that places each store among the processes that read /
// write it, so connectivity is legible, and packs the whole graph densely to a
// viewport-like aspect (big cards at the overview fit-zoom).
//
// Why force, not the old cluster-grid packing: the grid packing segregated ALL
// stores into a trailing "shared" row and tiled process blocks — connectivity
// was invisible and the field sprawled (fit-view zoom ~0.088). This layout runs
// ELK's STRESS algorithm over the NON-HUB wire graph (stores as nodes, wires as
// springs) so a store gravitates between the processes it connects, then removes
// residual overlaps and parks the hub / no-wire nodes in a compact trailing band.
//
// Pipeline (pure + deterministic given the seed):
//   1. Size every node for the LARGEST (`full`) tier — independent of ctx.tier —
//      so there is always room for the biggest card and positions are IDENTICAL
//      at every zoom (persistent placement; App always calls this at LAYOUT_TIER).
//   2. Feed ELK stress: every node that touches a NON-HUB wire, with those wires
//      as edges. Hub-store wires and place edges are EXCLUDED (a hub like `bulk`
//      wires to nearly everything and would pull the graph into a ball). Seeded
//      (`elk.randomSeed` constant) so the same input yields the same layout.
//   3. Remove residual node overlaps with a deterministic minimal-translation
//      push (ELK stress positions centers, it does not separate boxes), with a
//      uniform scale-out fallback that GUARANTEES zero overlaps.
//   4. Park hub stores + any node with no non-hub wire in a compact trailing grid
//      band below the force field (their wires are hub-hidden — no adjacency to
//      preserve), so every node is placed.
//
// Ignores ctx.tier entirely (sizes at `full`) so positions are identical at every
// zoom. `applyLayout` / `hierarchyMode` in hierarchy.ts are retained (layout.test
// .ts + hierarchy.test.ts exercise them directly) but no longer drive the canvas.

import ELK from 'elkjs/lib/elk.bundled.js';
import type { Node, Edge } from '@xyflow/react';
import { TIERS } from './tiers';
import { hubStoreIds, wireStoreEndpoint } from '../storeFacts';
import type {
  LayoutMode, LayoutResult, LayoutContext, FocusContext,
} from './types';
import { deriveContract } from '../contract';

const elk = new ELK();

/** Reduced-height estimate for a COMPACT figure card (title + role + meta +
 *  governing equation + port rows only — no config band, symbol legend,
 *  description or address). Generous per-block so nothing overlaps; still far
 *  shorter than the full-tier reservation, which is what removes the vertical
 *  sprawl in a busy multi-process figure. */
function compactProcHeight(n: Node): number {
  const d = n.data as { inputPorts?: unknown; outputPorts?: unknown } | undefined;
  const nPorts = Math.max(
    Array.isArray(d?.inputPorts) ? d!.inputPorts.length : 0,
    Array.isArray(d?.outputPorts) ? d!.outputPorts.length : 0,
  );
  const portsH = nPorts > 0 ? (nPorts + 1) * 46 : 0;
  const contract = deriveContract(d as never);
  const mathH = contract && contract.math.length ? contract.math.length * 92 + 24 : 0;
  // Symbol legend renders as a ~2-column grid at the compact card width, so its
  // height is ~one row per two symbols (must be reserved or cards overlap).
  const nSym = contract ? Object.keys(contract.symbols).length : 0;
  const symH = nSym ? Math.ceil(nSym / 2) * 30 + 20 : 0;
  const roleH = contract && contract.summary ? 60 : 0;  // one-line role sentence
  const headH = 150;  // title + meta line + padding
  return Math.max(headH + roleH + mathH + symH, portsH) + 30;
}

/** Coarse grid retained for the exported constant (no longer used to snap the
 *  organic force result — snapping would fight the minimal-overlap spacing). */
export const GRID = 20;
/** Gap kept between card boxes by the overlap-removal pass, and between cells in
 *  the trailing band. Big enough to read as clear separation at the overview. */
const NODE_GAP = 36;
/** Gap between the force field and the trailing band. */
const BAND_GAP = 80;
/** Per-port row height added to a process's full-tier card height, matching
 *  hierarchy.ts's processHeight so the reserved footprint is the real one. */
const PORT_ROW_H = 16;
/** Store card full-tier footprint (matches STORE_ELK_SIZE.full in hierarchy.ts). */
const STORE_W = 168;
const STORE_H = 150;

/** The live process-card width (App sets `--proc-card-w`, e.g. narrowed for a
 *  compact figure). Lets the layout reserve the card's ACTUAL width. Null when
 *  unset or off-DOM (unit tests). */
function readProcCardWidth(): number | null {
  if (typeof document === 'undefined') return null;
  try {
    const raw = getComputedStyle(document.documentElement).getPropertyValue('--proc-card-w').trim();
    const px = parseFloat(raw);
    return Number.isFinite(px) && px > 0 ? px : null;
  } catch { return null; }
}

/** The full (largest) tier — every node is sized for this regardless of zoom. */
const FULL = TIERS[TIERS.length - 1];

/** Full-tier footprint of a node. Process cards grow one row per port (as the
 *  card does from the `types` tier up). Stores are `height:auto` with only a
 *  `min-height` (StoreNode.tsx / App.css), so a content-rich store grows well
 *  past the old fixed STORE_H: at the full tier it stacks a wrapped 27px name,
 *  a 22px value, a wrapped 24px type, an illustration/icon block, a
 *  "N read · M write" badge, and an "emitted" tag. Estimating a fixed height
 *  under-reserved these and the packing dropped the process lane INTO the store
 *  row (the reported overlap). Reserve per the blocks the card actually shows. */
export function fullFootprint(n: Node): { w: number; h: number } {
  if (n.type === 'process') {
    const d = n.data as { inputPorts?: unknown; outputPorts?: unknown } | undefined;
    const nPorts = (Array.isArray(d?.inputPorts) ? d!.inputPorts.length : 0)
      + (Array.isArray(d?.outputPorts) ? d!.outputPorts.length : 0);
    return { w: FULL.cardWidth, h: FULL.cardHeight + nPorts * PORT_ROW_H };
  }
  const d = n.data as {
    label?: unknown; value?: unknown; valueType?: unknown; figure?: unknown;
    _readers?: unknown; _writers?: unknown; _emitted?: unknown;
  } | undefined;
  const nRW = (Array.isArray(d?._readers) ? d!._readers.length : 0)
    + (Array.isArray(d?._writers) ? d!._writers.length : 0);
  const contentH =
    24                                                              // vertical padding + inter-block gaps
    + 66                                                            // name label (27px, up to 2 wrapped lines)
    + (d?.value != null ? 28 : 0)                                  // value row (22px)
    + (typeof d?.valueType === 'string' && d.valueType ? 58 : 0)   // type (24px, may wrap to 2 lines)
    + (d?.figure ? 52 : 0)                                         // illustration / icon block
    + (nRW > 0 ? 20 : 0)                                           // "N read · M write" badge
    + (d?._emitted ? 16 : 0);                                      // "emitted" tag
  // Auto-width stores (App.css: width max-content, min 168, max 360) render
  // wider than STORE_W for a long name — but React Flow hasn't MEASURED them on
  // the first layout pass, so reserve width from the label length here or sibling
  // nodes overlap ("chromosome 0" / "chromosome 1"). ~15px/char at the 27px label
  // font + padding; clamped to the same [168, 360] the CSS enforces.
  const label = typeof d?.label === 'string' ? d.label : '';
  const vtype = typeof d?.valueType === 'string' ? d.valueType : '';
  const textW = Math.max(label.length * 15, vtype.length * 13) + 28;
  const w = Math.min(360, Math.max(STORE_W, textW));
  return { w, h: Math.max(STORE_H, contentH) };
}

export interface Box { id: string; x: number; y: number; w: number; h: number }

/**
 * Remove pairwise overlaps by pushing overlapping boxes apart along their axis
 * of least penetration until every pair has at least `margin` of clearance on a
 * separating axis. Deterministic: pairs are processed in a stable (id-sorted)
 * order and ties (dx===0 / dy===0) break by id. Converges quickly for a
 * force-seeded scatter; capped by `maxIter`. Returns the boxes mutated in place.
 */
export function removeOverlaps(items: Box[], margin: number, maxIter: number): void {
  const sorted = [...items].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  for (let iter = 0; iter < maxIter; iter++) {
    let moved = false;
    for (let i = 0; i < sorted.length; i++) {
      for (let j = i + 1; j < sorted.length; j++) {
        const a = sorted[i];
        const b = sorted[j];
        const dx = (b.x + b.w / 2) - (a.x + a.w / 2);
        const dy = (b.y + b.h / 2) - (a.y + a.h / 2);
        const px = (a.w + b.w) / 2 - Math.abs(dx);   // x-penetration (>0 → overlap on x)
        const py = (a.h + b.h) / 2 - Math.abs(dy);   // y-penetration
        if (px > 0 && py > 0) {                      // boxes actually overlap
          if (px <= py) {
            const dir = dx === 0 ? (a.id < b.id ? 1 : -1) : Math.sign(dx);
            const s = (dir * (px + margin)) / 2;
            a.x -= s; b.x += s;
          } else {
            const dir = dy === 0 ? (a.id < b.id ? 1 : -1) : Math.sign(dy);
            const s = (dir * (py + margin)) / 2;
            a.y -= s; b.y += s;
          }
          moved = true;
        }
      }
    }
    if (!moved) return;
  }
}

/** True if any two boxes overlap. */
function anyOverlap(items: Box[]): boolean {
  for (let i = 0; i < items.length; i++) {
    for (let j = i + 1; j < items.length; j++) {
      const a = items[i];
      const b = items[j];
      if (a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h) return true;
    }
  }
  return false;
}

/** Axis-aligned bounding box of a set of boxes. */
function bboxOf(items: Box[]): { minX: number; minY: number; maxX: number; maxY: number } {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of items) {
    if (p.x < minX) minX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.x + p.w > maxX) maxX = p.x + p.w;
    if (p.y + p.h > maxY) maxY = p.y + p.h;
  }
  if (!Number.isFinite(minX)) return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  return { minX, minY, maxX, maxY };
}

/**
 * Pack `cells` into a tidy grid (~ceil(sqrt) columns, or `colsOverride`) with
 * per-column max width and per-row max height, so variable-size cards never
 * overlap. Returns absolute positions with the block's top-left at (ox, oy).
 */
function packGrid(
  cells: Box[], ox: number, oy: number, gap: number, colsOverride?: number,
): { positions: Map<string, { x: number; y: number }>; w: number; h: number } {
  const positions = new Map<string, { x: number; y: number }>();
  const n = cells.length;
  if (n === 0) return { positions, w: 0, h: 0 };
  const cols = Math.max(1, Math.min(n, colsOverride ?? Math.ceil(Math.sqrt(n))));
  const rows = Math.ceil(n / cols);
  const colW = new Array<number>(cols).fill(0);
  const rowH = new Array<number>(rows).fill(0);
  cells.forEach((c, i) => {
    const r = Math.floor(i / cols);
    const cc = i % cols;
    if (c.w > colW[cc]) colW[cc] = c.w;
    if (c.h > rowH[r]) rowH[r] = c.h;
  });
  const colX = new Array<number>(cols).fill(0);
  const rowY = new Array<number>(rows).fill(0);
  let ax = 0;
  for (let c = 0; c < cols; c++) { colX[c] = ax; ax += colW[c] + gap; }
  let ay = 0;
  for (let r = 0; r < rows; r++) { rowY[r] = ay; ay += rowH[r] + gap; }
  cells.forEach((c, i) => {
    const r = Math.floor(i / cols);
    const cc = i % cols;
    positions.set(c.id, { x: ox + colX[cc], y: oy + rowY[r] });
  });
  return { positions, w: Math.max(0, ax - gap), h: Math.max(0, ay - gap) };
}

/**
 * The force-directed layout. Async (ELK) but pure + deterministic: ELK stress is
 * seeded, overlap removal is order-stable, and the trailing band packs in stable
 * node order. Ignores ctx.tier (sizes at `full`) so positions are identical at
 * every zoom (persistent placement).
 */
export async function clusterGridLayout(
  nodes: Node[], edges: Edge[], _ctx: LayoutContext,
): Promise<LayoutResult> {
  if (nodes.length === 0) return { nodes };

  // Print-figure mode packs tighter and sizes process cards to the actual
  // (narrowed) card width, so a busy composite fits a page. The card height
  // estimate is left at the full-tier reservation (it over-reserves for a
  // compact card, which only shows title+role+equation), so nothing overlaps —
  // compact just removes the horizontal sprawl.
  const compact = _ctx.compact === true;
  const compactCardW = compact ? readProcCardWidth() : null;
  const footprint = new Map<string, { w: number; h: number }>(
    nodes.map((n) => {
      const f = fullFootprint(n);
      if (compact && n.type === 'process') {
        const w = compactCardW ? Math.min(f.w, compactCardW) : f.w;
        return [n.id, { w, h: Math.min(f.h, compactProcHeight(n)) }] as const;
      }
      return [n.id, f] as const;
    }),
  );
  const nodeIds = new Set(nodes.map((n) => n.id));

  // Non-hub wire edges are the springs; hub-store wires + place edges are
  // excluded so hub stores don't collapse the graph into a ball.
  const hubs = hubStoreIds(edges);
  const springs: Array<{ source: string; target: string }> = [];
  const wired = new Set<string>();
  for (const e of edges) {
    const store = wireStoreEndpoint(e);
    if (store == null || hubs.has(store)) continue;         // place / hub wire
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue;
    springs.push({ source: e.source, target: e.target });
    wired.add(e.source);
    wired.add(e.target);
  }

  // Nodes with at least one non-hub wire go through ELK; the rest (hub stores +
  // isolated nodes) are parked in the trailing band.
  const graphNodes = nodes.filter((n) => wired.has(n.id));
  const trailingNodes = nodes.filter((n) => !wired.has(n.id));

  const pos = new Map<string, { x: number; y: number }>();
  let forceBoxes: Box[] = [];

  if (graphNodes.length > 0) {
    const elkGraph = {
      id: 'root',
      layoutOptions: {
        'elk.algorithm': 'stress',
        // Compact target edge length: short springs → stores nestle against
        // their processes and the field packs densely (high fit-zoom). Tuned on
        // the v2ecoli baseline for aspect ~1.4 + fit-zoom ~0.3 after overlap
        // removal (see clusterGrid.test.ts MEASURE).
        // Figure mode uses much shorter springs + tighter spacing so the field
        // packs densely for print instead of spreading for an interactive
        // fit-zoom overview.
        'elk.stress.desiredEdgeLength': compact ? '120' : '240',
        'elk.spacing.nodeNode': compact ? '26' : '40',
        // Deterministic: a constant seed makes stress reproducible.
        'elk.randomSeed': '1',
      } as Record<string, string>,
      children: graphNodes.map((n) => {
        const f = footprint.get(n.id)!;
        return { id: n.id, width: f.w, height: f.h };
      }),
      edges: springs.map((s, i) => ({ id: `s${i}`, sources: [s.source], targets: [s.target] })),
    };

    const res = (await elk.layout(elkGraph as never)) as {
      children?: Array<{ id: string; x?: number; y?: number; width?: number; height?: number }>;
    };
    for (const c of res.children ?? []) {
      const f = footprint.get(c.id)!;
      forceBoxes.push({ id: c.id, x: c.x ?? 0, y: c.y ?? 0, w: f.w, h: f.h });
    }

    // ELK stress positions centers but does not separate boxes — clean up.
    removeOverlaps(forceBoxes, compact ? 24 : NODE_GAP, 1000);
    // Guaranteed no-overlap fallback: if the push did not fully converge, scale
    // positions out about the centroid until nothing overlaps (monotonic, so it
    // always terminates). Rarely triggers — the push converges on real data.
    if (anyOverlap(forceBoxes)) {
      const bb = bboxOf(forceBoxes);
      const cx = (bb.minX + bb.maxX) / 2;
      const cy = (bb.minY + bb.maxY) / 2;
      for (let guard = 0; guard < 40 && anyOverlap(forceBoxes); guard++) {
        for (const b of forceBoxes) {
          b.x = cx + (b.x + b.w / 2 - cx) * 1.2 - b.w / 2;
          b.y = cy + (b.y + b.h / 2 - cy) * 1.2 - b.h / 2;
        }
      }
    }
    for (const b of forceBoxes) pos.set(b.id, { x: b.x, y: b.y });
  }

  // Trailing band: hub stores + no-wire nodes. Park it on whichever side keeps
  // the OVERALL bounding box closest to the viewport aspect — to the right when
  // the force field is too tall/narrow (widen it), below when it is too wide.
  // With no force field, pack it into a roughly-square block on its own.
  const fieldBB = bboxOf(forceBoxes);
  const fieldW = fieldBB.maxX - fieldBB.minX;
  const fieldH = fieldBB.maxY - fieldBB.minY;
  const VIEWPORT_ASPECT = 1.5;
  if (trailingNodes.length > 0) {
    const cells: Box[] = trailingNodes.map((n) => {
      const f = footprint.get(n.id)!;
      return { id: n.id, x: 0, y: 0, w: f.w, h: f.h };
    });
    const avgCellW = cells.reduce((a, c) => a + c.w, 0) / cells.length + NODE_GAP;
    const avgCellH = cells.reduce((a, c) => a + c.h, 0) / cells.length + NODE_GAP;

    if (graphNodes.length === 0) {
      // No force field — just a compact square-ish block at the origin.
      const cols = Math.max(1, Math.ceil(Math.sqrt(cells.length)));
      const packed = packGrid(cells, 0, 0, NODE_GAP, cols);
      for (const [id, p] of packed.positions) pos.set(id, p);
    } else if (fieldW / fieldH < VIEWPORT_ASPECT) {
      // Field too tall/narrow → band as a COLUMN to the right, ~field height.
      const rows = Math.max(1, Math.round(fieldH / avgCellH));
      const cols = Math.max(1, Math.ceil(cells.length / rows));
      const packed = packGrid(cells, fieldBB.maxX + BAND_GAP, fieldBB.minY, NODE_GAP, cols);
      for (const [id, p] of packed.positions) pos.set(id, p);
    } else {
      // Field too wide → band as a ROW below, ~field width.
      const cols = Math.max(1, Math.round(fieldW / avgCellW));
      const packed = packGrid(cells, fieldBB.minX, fieldBB.maxY + BAND_GAP, NODE_GAP, cols);
      for (const [id, p] of packed.positions) pos.set(id, p);
    }
  }

  // Normalize so the whole layout's top-left sits at the origin.
  const allBoxes: Box[] = nodes
    .filter((n) => pos.has(n.id))
    .map((n) => {
      const p = pos.get(n.id)!;
      const f = footprint.get(n.id)!;
      return { id: n.id, x: p.x, y: p.y, w: f.w, h: f.h };
    });
  const bb = bboxOf(allBoxes);
  const shiftX = Number.isFinite(bb.minX) ? -bb.minX : 0;
  const shiftY = Number.isFinite(bb.minY) ? -bb.minY : 0;

  const out = nodes.map((n) => {
    const p = pos.get(n.id);
    return p ? { ...n, position: { x: p.x + shiftX, y: p.y + shiftY } } : n;
  });
  return { nodes: out };
}

/**
 * Focus-driven edge visibility for the canvas view.
 *
 * Nothing focused (the default declutter): keep place edges + non-hub wires,
 * hide hub-store wires (bulk / listeners / timestep …) — identical to the
 * hierarchy hub-hiding, identity-preserving so React Flow doesn't re-derive its
 * edge store on a no-op. The global "Show hub wires" toggle reveals the full fan.
 *
 * A process focused (hovered OR pinned — so a CLICK-pin keeps its wires up at
 * any zoom): draw ALL of that process's wires INCLUDING its normally-hidden hub
 * wires, and stamp `_focused` on them (→ `.loom-edge-focused`, thick + highlit).
 * Every OTHER non-place wire stays drawn but is stamped `_dim` (→ `.loom-edge-
 * dim`) so the focused neighbourhood stands out. Hub wires of NON-focused
 * processes remain hidden (unless the toggle is on), preserving the declutter.
 *
 * O(edges); mints new edge objects only while a focus is active (an intentional
 * change), and returns the input array identity in the no-focus pass-through.
 */
export function clusterGridEdgeVisibility(
  edges: Edge[], focus: FocusContext, _nodes: Node[],
): Edge[] {
  const active = new Set<string>([...focus.focused, ...focus.pinned]);
  const focusActive = active.size > 0;
  const showHub = focus.showHubWires === true;
  const hubs = showHub ? null : hubStoreIds(edges);

  // No focus: the unchanged, identity-preserving hub-hiding path.
  if (!focusActive) {
    if (!hubs || hubs.size === 0) return edges;
    const out = edges.filter((e) => {
      const store = wireStoreEndpoint(e);
      if (store == null) return true;      // place / non-wire: always kept
      return !hubs.has(store);             // wire kept only if its store isn't a hub
    });
    return out.length === edges.length ? edges : out;
  }

  // A focus is active: reveal + highlight the focused process's wiring.
  const out: Edge[] = [];
  for (const e of edges) {
    const store = wireStoreEndpoint(e);
    const isPlace = store == null;
    const isFocused = active.has(e.source) || active.has(e.target);
    const isHubWire = !isPlace && hubs !== null && hubs.has(store!);
    // A hub wire of a non-focused process stays hidden (declutter); a focused
    // process's hub wires are revealed on top.
    if (isHubWire && !isFocused) continue;
    if (isPlace) { out.push(e); continue; }        // structural skeleton, undimmed
    out.push({ ...e, data: { ...e.data, _focused: isFocused, _dim: !isFocused } });
  }
  return out;
}

export const clusterGridMode: LayoutMode = {
  // Keeps the sole view's id ('hierarchy') so saved layouts, the default-mode
  // resolution, and App's `modeId === 'hierarchy'` hub-count branch all keep
  // working — only the ALGORITHM is swapped. No mode dropdown returns.
  id: 'hierarchy',
  label: 'Relationship',
  tiers: TIERS,

  async run(nodes: Node[], edges: Edge[], ctx: LayoutContext): Promise<LayoutResult> {
    return clusterGridLayout(nodes, edges, ctx);
  },

  // Focus-driven: the default view keeps the hub-hidden declutter, and focusing
  // a process (hover or click-pin) reveals + highlights ALL its wires, hub wires
  // included, at any zoom. `focusReveals: true` turns on App's hover handlers,
  // the focus hint, and pin pruning. The "Show hub wires" toggle still works.
  edgeVisibility: clusterGridEdgeVisibility,
  focusReveals: true,
};
