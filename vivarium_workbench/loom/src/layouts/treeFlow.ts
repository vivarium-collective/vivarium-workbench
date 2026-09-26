// src/layouts/treeFlow.ts — the "Tree" and "Grid" layouts: hierarchy-first.
//
// A process bigraph carries two relationships at once: the PLACE graph (which
// store is nested in which — inherently a vertical hierarchy) and the WIRING
// (which process reads / writes which store). Ranking everything one way (ELK
// left→right) collapses the store hierarchy sideways — a parent ends up beside
// its child. Both modes here keep the STORES as a tidy vertical org-chart from
// the place graph; they differ only in where the PROCESSES go:
//
//   • Tree — processes packed INTO the hierarchy: each docks into a horizontal
//     swimlane between the store rows it bridges, near the stores it wires.
//   • Grid — the store hierarchy stays on the left and ALL processes are pulled
//     to the RIGHT in a scannable grid, so the tree reads clean and the process
//     list is easy to skim.
//
// This is a rewrite of the first (reverted) attempt, hardened for the cases that
// collapsed it on large / hub-heavy composites:
//   1. The store tree reserves each subtree's REAL width (disjoint horizontal
//      bands), so it never collapses to a thin overlapping column and never
//      overlaps — forests, place-DAGs, disconnected stores and cycles are all
//      handled.
//   2. Processes are PACKED into bounded grids (Tree: per-lane; Grid: one right
//      block), never dropped at a centroid and force-scattered — so nothing is
//      flung outside the frame.
//   3. Footprints honor a card's saved `_size` and otherwise over-reserve height
//      for the tallest full-tier content, so the packing leaves no overlaps.
// A final bounded removeOverlaps is a safety net only — the packing is
// overlap-free by construction.

import type { Node, Edge } from '@xyflow/react';
import { TIERS } from './tiers';
import { wireStoreEndpoint, hubStoreIds } from '../storeFacts';
import { fullFootprint, removeOverlaps, clusterGridEdgeVisibility, type Box } from './clusterGrid';
import { deriveContract } from '../contract';
import type { LayoutMode, LayoutResult, FocusContext } from './types';

type XY = { x: number; y: number };
type Foot = { w: number; h: number };

const FULL = TIERS[TIERS.length - 1];

/** Gap set — Tree is generous + readable; Grid packs its right block tighter. */
interface Gaps {
  rowGap: number;   // vertical gap between store rows (and above a Tree lane)
  colGap: number;   // horizontal gap between sibling store subtrees
  procGapX: number; // horizontal gap between packed processes
  procGapY: number; // vertical gap between packed process rows
  bandGap: number;  // gap before the process block (right grid / trailing band)
}
// Gaps are kept tight so processes sit close to each other and close to the
// store rows they wire — the card footprints (full tier, 620×320) already force
// generous spacing, so small gaps read as compact, not cramped.
const TREE_GAPS: Gaps = { rowGap: 58, colGap: 22, procGapX: 26, procGapY: 22, bandGap: 48 };
const GRID_GAPS: Gaps = { rowGap: 30, colGap: 12, procGapX: 24, procGapY: 20, bandGap: 40 };

/** Grow an estimated footprint to React Flow's LIVE measured box when the card
 *  actually rendered taller/wider than the estimate. The full-tier estimates
 *  below are content-blind (a fixed store height, a port/contract-derived
 *  process height), so a content-rich store — a wrapped multi-line name plus a
 *  value, a wrapped type, and a "N read · M write" badge — renders past the
 *  fixed STORE_H and the packing below overlaps up into it. `n.measured` is the
 *  real rendered box; take the max so the reservation is never SMALLER than what
 *  the card occupies, and never smaller than the full-tier estimate either. */
function withMeasured(n: Node, est: Foot): Foot {
  const nn = n as unknown as { width?: number; height?: number; measured?: { width?: number; height?: number } };
  const w = nn.width || nn.measured?.width;   // React Flow populates both after render
  const h = nn.height || nn.measured?.height;
  if (w && h) return { w: Math.max(w, est.w), h: Math.max(h, est.h) };
  return est;
}

/** Footprint of a node, sized for the LARGEST (full) tier so positions are
 *  identical at every zoom (persistent placement). Honors a hand-set `_size`
 *  (the authoritative measured box); otherwise takes the max of the full-tier
 *  estimate and React Flow's live measured box (via withMeasured) so the
 *  tallest full-tier content (ports, config band, contract, equations, or a
 *  content-rich store) can never overlap a neighbour. */
export function treeFootprint(n: Node): Foot {
  const saved = (n.data as { _size?: { width: number; height: number } } | undefined)?._size;
  if (saved && saved.width > 0 && saved.height > 0) return { w: saved.width, h: saved.height };
  if (n.type !== 'process') return withMeasured(n, fullFootprint(n)); // store: full-tier or real
  // Minimal figure: the process is a compact box (name only), so reserve a
  // box-sized footprint — the lane placement then centers it under its stores
  // instead of parking a full-width card slot off to one side.
  if ((n.data as { _minimal?: boolean } | undefined)?._minimal) return { w: 208, h: 160 };

  const d = n.data as {
    inputPorts?: unknown; outputPorts?: unknown;
    configSchema?: Record<string, unknown>; config?: Record<string, unknown>;
    math?: unknown; equation?: unknown; equations?: unknown;
  } | undefined;
  const nIn = Array.isArray(d?.inputPorts) ? d!.inputPorts.length : 0;
  const nOut = Array.isArray(d?.outputPorts) ? d!.outputPorts.length : 0;
  // Ports lay out in two columns (in | out); the card's port-driven min height is
  // (max(in,out)+1) rows (ProcessNode.portsMinH, 46px/row at full).
  const portH = Math.max(nIn, nOut) > 0 ? (Math.max(nIn, nOut) + 1) * 46 : 0;
  // Config band adds one row per real config key (metadata keys drop out later,
  // so this is an upper bound); equations add a generous block.
  const nCfg = d?.configSchema ? Object.keys(d.configSchema).length
    : (d?.config ? Object.keys(d.config).length : 0);
  const cfgH = nCfg > 0 ? Math.min(nCfg, 8) * 26 : 0;
  // Governing-equation / symbol-legend / description blocks: the card typesets
  // these from the CONTRACT, which parses the process docstring (`data.doc`) —
  // not any top-level `math` field. Miss them and an equation-heavy card (e.g.
  // ecoli-protein-degradation, ~470px) under-reserves and overflows its lane into
  // the store row. Derive the same contract the card renders and reserve per its
  // real content. Generous per-block heights + a buffer keep the estimate safe.
  const contract = deriveContract(d as never);
  const mathH = contract && contract.math.length ? contract.math.length * 46 + 30 : 0;
  const nSym = contract ? Object.keys(contract.symbols).length : 0;
  const symH = nSym ? nSym * 24 + 20 : 0;
  const descH = contract && contract.description ? 100 : 0;
  const contentH = FULL.cardHeight + cfgH + mathH + symH + descH + 24;
  return withMeasured(n, { w: FULL.cardWidth, h: Math.max(contentH, portH) });
}

/** Spanning forest of the store place graph: for each store its depth (BFS from
 *  roots, so a store with multiple place-parents keeps its SHALLOWEST) and the
 *  children under that spanning tree. Cycle-safe (BFS visited set). */
function storeForest(stores: Node[], placeEdges: Edge[]): {
  children: Map<string, string[]>;
  roots: string[];
  depth: Map<string, number>;
} {
  const byId = new Map(stores.map((s) => [s.id, s] as const));
  const outAdj = new Map<string, string[]>(stores.map((s) => [s.id, []]));
  const indeg = new Map<string, number>(stores.map((s) => [s.id, 0]));
  for (const e of placeEdges) {
    if (!byId.has(e.source) || !byId.has(e.target) || e.source === e.target) continue;
    outAdj.get(e.source)!.push(e.target);
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
  }
  const roots = stores.filter((s) => (indeg.get(s.id) ?? 0) === 0).map((s) => s.id);
  const depth = new Map<string, number>();
  const children = new Map<string, string[]>(stores.map((s) => [s.id, []]));
  // BFS assigns each store its shallowest depth and pins it under the first
  // (shallowest) parent that reaches it → a clean spanning tree over any DAG.
  const queue: string[] = [...roots];
  for (const r of roots) depth.set(r, 0);
  while (queue.length) {
    const id = queue.shift()!;
    const dp = depth.get(id)!;
    for (const c of outAdj.get(id) ?? []) {
      if (depth.has(c)) continue;      // already reached by a shallower parent
      depth.set(c, dp + 1);
      children.get(id)!.push(c);
      queue.push(c);
    }
  }
  // Any store unreached from a root (pure cycle with no entry) → make it a root.
  for (const s of stores) {
    if (!depth.has(s.id)) { depth.set(s.id, 0); roots.push(s.id); }
  }
  return { children, roots, depth };
}

/** Tidy org-chart center-x per store: every subtree reserves its own REAL width
 *  (a disjoint horizontal band), so subtrees never overlap and a linear chain
 *  stays a centered column. Parents are centered over the span of their children
 *  (Reingold–Tilford's centering, band-reserved instead of contour-threaded —
 *  slightly wider but robust and collapse-proof). */
function tidyTreeX(
  roots: string[], children: Map<string, string[]>, foot: (id: string) => Foot, colGap: number,
): Map<string, number> {
  const subW = new Map<string, number>();
  const widthOf = (id: string): number => {
    const memo = subW.get(id);
    if (memo != null) return memo;
    const kids = children.get(id) ?? [];
    const own = foot(id).w;
    const w = kids.length === 0 ? own
      : Math.max(own, kids.reduce((a, c) => a + widthOf(c), 0) + colGap * (kids.length - 1));
    subW.set(id, w);
    return w;
  };
  const centerX = new Map<string, number>();
  const place = (id: string, left: number): void => {
    const kids = children.get(id) ?? [];
    if (kids.length === 0) { centerX.set(id, left + widthOf(id) / 2); return; }
    let cursor = left;
    const kc: number[] = [];
    for (const c of kids) { place(c, cursor); kc.push(centerX.get(c)!); cursor += widthOf(c) + colGap; }
    centerX.set(id, (Math.min(...kc) + Math.max(...kc)) / 2); // parent over children's span
  };
  let left = 0;
  for (const r of roots) { place(r, left); left += widthOf(r) + colGap; }
  return centerX;
}

/** Non-hub stores a process wires to. Hubs are excluded from HOMING so a process
 *  isn't dragged toward a `bulk`-style hub everything touches. */
function processHomes(procId: string, edges: Edge[], storeIds: Set<string>, hubs: Set<string>): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const e of edges) {
    const kind = (e.data as { edgeType?: string } | undefined)?.edgeType;
    if (kind !== 'input' && kind !== 'output') continue;
    if (e.source !== procId && e.target !== procId) continue;
    const s = wireStoreEndpoint(e);
    if (s && storeIds.has(s) && !hubs.has(s) && !seen.has(s)) { seen.add(s); out.push(s); }
  }
  return out;
}

/** Pack pre-ordered `items` into a grid of `cols` columns with per-cell max size,
 *  top-left at (leftX, topY). Cards are centered within their (max-width) cell.
 *  Returns positions + the block's width/height. */
function packBlock(
  items: Array<{ id: string; f: Foot }>, leftX: number, topY: number, cols: number,
  gapX: number, gapY: number,
): { pos: Map<string, XY>; w: number; h: number } {
  const pos = new Map<string, XY>();
  if (items.length === 0) return { pos, w: 0, h: 0 };
  const c = Math.max(1, Math.min(items.length, cols));
  const cellW = Math.max(...items.map((it) => it.f.w));
  const cellH = Math.max(...items.map((it) => it.f.h));
  const rows = Math.ceil(items.length / c);
  items.forEach((it, i) => {
    const r = Math.floor(i / c);
    const cc = i % c;
    pos.set(it.id, {
      x: leftX + cc * (cellW + gapX) + (cellW - it.f.w) / 2,
      y: topY + r * (cellH + gapY),
    });
  });
  return { pos, w: c * cellW + (c - 1) * gapX, h: rows * cellH + (rows - 1) * gapY };
}

/** Shared spine: build the store org-chart. Returns center-x + depth + per-depth
 *  row height, plus the hub set and place/wire context both modes reuse. */
function storeSpine(nodes: Node[], edges: Edge[], foot: (id: string) => Foot, colGap: number) {
  const stores = nodes.filter((n) => n.type === 'store');
  const procs = nodes.filter((n) => n.type === 'process');
  const storeIds = new Set(stores.map((s) => s.id));
  const placeEdges = edges.filter((e) => (e.data as { edgeType?: string } | undefined)?.edgeType === 'place');
  const hubs = hubStoreIds(edges);
  const { children, roots, depth } = storeForest(stores, placeEdges);
  const centerX = tidyTreeX(roots, children, foot, colGap);
  const maxDepth = stores.length ? Math.max(0, ...stores.map((s) => depth.get(s.id) ?? 0)) : 0;
  const rowH: number[] = new Array(maxDepth + 1).fill(0);
  for (const s of stores) { const dp = depth.get(s.id) ?? 0; rowH[dp] = Math.max(rowH[dp], foot(s.id).h); }
  return { stores, procs, storeIds, depth, centerX, maxDepth, rowH, hubs };
}

/** Finalize: bounded safety de-overlap + normalize the top-left to the origin. */
function finalize(nodes: Node[], pos: Map<string, XY>, foot: (id: string) => Foot, margin: number): LayoutResult {
  const boxes: Box[] = nodes.filter((n) => pos.has(n.id)).map((n) => {
    const p = pos.get(n.id)!; const f = foot(n.id);
    return { id: n.id, x: p.x, y: p.y, w: f.w, h: f.h };
  });
  removeOverlaps(boxes, margin, 400);
  const boxById = new Map(boxes.map((b) => [b.id, b] as const));
  let minX = Infinity, minY = Infinity;
  for (const b of boxes) { if (b.x < minX) minX = b.x; if (b.y < minY) minY = b.y; }
  const shiftX = Number.isFinite(minX) ? -minX : 0;
  const shiftY = Number.isFinite(minY) ? -minY : 0;
  return {
    nodes: nodes.map((n) => {
      const b = boxById.get(n.id);
      return b ? { ...n, position: { x: b.x + shiftX, y: b.y + shiftY } } : n;
    }),
  };
}

// ── Tree: processes packed into swimlanes between the store rows they bridge ──
async function treeLanesLayout(nodes: Node[], edges: Edge[], gaps: Gaps): Promise<LayoutResult> {
  if (nodes.length === 0) return { nodes };
  const footById = new Map(nodes.map((n) => [n.id, treeFootprint(n)] as const));
  const foot = (id: string) => footById.get(id) ?? { w: 120, h: 80 };
  const { stores, procs, storeIds, depth, centerX, maxDepth, rowH, hubs } =
    storeSpine(nodes, edges, foot, gaps.colGap);

  // Home each process to a lane = shallowest depth among its stores. Prefer
  // NON-hub stores (so a process isn't dragged toward a `bulk`-style hub), but
  // a process that wires ONLY hub stores still homes near those hubs rather than
  // being banished to a far-below band — only truly UNWIRED processes band.
  const noHubs = new Set<string>();
  const laneMembers = new Map<number, Array<{ id: string; f: Foot; key: number }>>();
  const bandProcs: Node[] = [];
  for (const p of procs) {
    let homes = processHomes(p.id, edges, storeIds, hubs);
    if (homes.length === 0) homes = processHomes(p.id, edges, storeIds, noHubs); // hub-only fallback
    if (homes.length === 0) { bandProcs.push(p); continue; }                     // truly unwired
    const lane = Math.min(...homes.map((h) => depth.get(h) ?? 0));
    const key = homes.reduce((a, h) => a + (centerX.get(h) ?? 0), 0) / homes.length;
    let arr = laneMembers.get(lane);
    if (!arr) { arr = []; laneMembers.set(lane, arr); }
    arr.push({ id: p.id, f: foot(p.id), key });
  }
  for (const arr of laneMembers.values()) arr.sort((a, b) => a.key - b.key || (a.id < b.id ? -1 : 1));

  const treeLeft = stores.length ? Math.min(...stores.map((s) => centerX.get(s.id)! - foot(s.id).w / 2)) : 0;
  const treeRight = stores.length ? Math.max(...stores.map((s) => centerX.get(s.id)! + foot(s.id).w / 2)) : 0;
  const treeMidX = (treeLeft + treeRight) / 2;
  const laneBudget = Math.max(treeRight - treeLeft, FULL.cardWidth * 3);
  const cellW = FULL.cardWidth;
  const laneCols = Math.max(1, Math.floor((laneBudget + gaps.procGapX) / (cellW + gaps.procGapX)));

  const pos = new Map<string, XY>();
  let y = 0;
  for (let dp = 0; dp <= maxDepth; dp++) {
    const rowTop = y;
    for (const s of stores) {
      if ((depth.get(s.id) ?? 0) !== dp) continue;
      pos.set(s.id, { x: centerX.get(s.id)! - foot(s.id).w / 2, y: rowTop });
    }
    y += rowH[dp] + gaps.rowGap;
    const lane = laneMembers.get(dp);
    if (lane && lane.length) {
      // Place each process centered under the stores it wires — its `key` is the
      // mean center-x of its connected stores — so a process sits DIRECTLY BELOW
      // the nodes it connects to, instead of being packed into one centered block.
      // finalize()'s removeOverlaps then spreads any that collide horizontally.
      let laneH = 0;
      for (const m of lane) {
        pos.set(m.id, { x: m.key - m.f.w / 2, y });
        if (m.f.h > laneH) laneH = m.f.h;
      }
      y += laneH + gaps.rowGap;
    }
  }
  // Trailing band: hub-only / unwired processes, centered below the tree.
  if (bandProcs.length) {
    const items = bandProcs.map((p) => ({ id: p.id, f: foot(p.id) }));
    const cols = Math.min(items.length, laneCols);
    const blockW = cols * FULL.cardWidth + (cols - 1) * gaps.procGapX;
    const packed = packBlock(items, treeMidX - blockW / 2, y + gaps.bandGap - gaps.rowGap, cols, gaps.procGapX, gaps.procGapY);
    for (const [id, p] of packed.pos) pos.set(id, p);
  }
  return finalize(nodes, pos, foot, Math.min(gaps.procGapX, gaps.colGap));
}

interface Block { w: number; h: number; place: (ox: number, oy: number, pos: Map<string, XY>) => void; }

/** Flow-pack sub-blocks into wrapped rows (area-based budget → roughly square),
 *  centering each row. Returns a container Block (no node of its own). */
function packBlocksGrid(blocks: Block[], hGap: number, vGap: number): Block {
  if (blocks.length === 0) return { w: 0, h: 0, place: () => {} };
  const area = blocks.reduce((a, b) => a + b.w * b.h, 0);
  const budget = Math.max(...blocks.map((b) => b.w), Math.sqrt(area * 1.4));
  const rows: Block[][] = [];
  let cur: Block[] = [], curW = 0;
  for (const b of blocks) {
    if (cur.length && curW + b.w > budget) { rows.push(cur); cur = []; curW = 0; }
    cur.push(b); curW += b.w + hGap;
  }
  if (cur.length) rows.push(cur);
  const rowW = rows.map((r) => r.reduce((a, b) => a + b.w + hGap, 0) - hGap);
  const rowH = rows.map((r) => Math.max(...r.map((b) => b.h)));
  const w = Math.max(...rowW);
  const h = rowH.reduce((a, x) => a + x + vGap, 0) - vGap;
  return {
    w, h,
    place: (ox, oy, pos) => {
      let cy = oy;
      rows.forEach((r, ri) => {
        let cx = ox + (w - rowW[ri]) / 2;
        for (const b of r) { b.place(cx, cy, pos); cx += b.w + hGap; }
        cy += rowH[ri] + vGap;
      });
    },
  };
}

/** Compact org-chart block for a store subtree, laid out to keep place edges
 *  legible. The node sits centered on top; beneath it, its LEAF children wrap
 *  into one compact grid while each SUBTREE child keeps its own coherent column.
 *  Separating the two means a tall subtree (e.g. `unique`) no longer packs inline
 *  among leaf siblings — so edges from the parent to later siblings stop crossing
 *  that subtree, and nested-subtree edges stay untangled. Overlap-free by
 *  construction; a wide leaf level still wraps (~4×4) rather than sprawling. */
function storeBlock(
  id: string, children: Map<string, string[]>, foot: (id: string) => Foot,
  hGap: number, vGap: number, seen: Set<string>,
): Block {
  const f = foot(id);
  const kids = seen.has(id) ? [] : (children.get(id) ?? []);
  seen.add(id);
  if (kids.length === 0) {
    return { w: f.w, h: f.h, place: (ox, oy, pos) => pos.set(id, { x: ox, y: oy }) };
  }
  const isLeaf = (k: string) => (children.get(k)?.length ?? 0) === 0;
  const leafKids = kids.filter(isLeaf);
  const subKids = kids.filter((k) => !isLeaf(k));
  const items: Block[] = [];
  if (leafKids.length) {
    items.push(packBlocksGrid(leafKids.map((k) => storeBlock(k, children, foot, hGap, vGap, seen)), hGap, vGap));
  }
  for (const k of subKids) items.push(storeBlock(k, children, foot, hGap, vGap, seen));
  const cont = packBlocksGrid(items, hGap, vGap);
  const blockW = Math.max(f.w, cont.w);
  const blockH = f.h + vGap + cont.h;
  return {
    w: blockW, h: blockH,
    place: (ox, oy, pos) => {
      pos.set(id, { x: ox + (blockW - f.w) / 2, y: oy });   // parent centered on top
      cont.place(ox + (blockW - cont.w) / 2, oy + f.h + vGap, pos);
    },
  };
}

/** Lay out a store forest as compact wrapped blocks, roots flowed left→right. */
function compactStoreForest(
  roots: string[], children: Map<string, string[]>, foot: (id: string) => Foot,
  hGap: number, vGap: number,
): Map<string, XY> {
  const pos = new Map<string, XY>();
  const seen = new Set<string>();
  let x = 0;
  for (const r of roots) {
    const b = storeBlock(r, children, foot, hGap, vGap, seen);
    b.place(x, 0, pos);
    x += b.w + hGap * 2;
  }
  return pos;
}

// ── Grid: store org-chart on the left, ALL processes pulled right into a grid ──
async function treeGridLayout(nodes: Node[], edges: Edge[], gaps: Gaps): Promise<LayoutResult> {
  if (nodes.length === 0) return { nodes };
  const footById = new Map(nodes.map((n) => [n.id, treeFootprint(n)] as const));
  const foot = (id: string) => footById.get(id) ?? { w: 120, h: 80 };
  const stores = nodes.filter((n) => n.type === 'store');
  const procs = nodes.filter((n) => n.type === 'process');
  const placeEdges = edges.filter((e) => (e.data as { edgeType?: string } | undefined)?.edgeType === 'place');
  const { children, roots } = storeForest(stores, placeEdges);

  // Compact store org-chart: wide sibling levels WRAP into blocks (a 16-store
  // level becomes ~4×4, not one ~2850px row) so the tree groups closely on the
  // left instead of sprawling across the canvas.
  const pos = compactStoreForest(roots, children, foot, gaps.colGap, gaps.rowGap);
  let treeRight = 0, treeTop = Infinity;
  for (const s of stores) {
    const p = pos.get(s.id);
    if (!p) continue;
    treeRight = Math.max(treeRight, p.x + foot(s.id).w);
    treeTop = Math.min(treeTop, p.y);
  }
  if (!Number.isFinite(treeTop)) treeTop = 0;

  // All processes → a scannable grid to the RIGHT, ordered by display label.
  // Column count targets a near-square block (slightly wide) so the grid reads
  // as a compact panel regardless of how deep/shallow the store tree is — tying
  // the row count to the tree height degenerates to a single huge row when the
  // place graph is shallow (v2ecoli), and to a thin column when it is deep.
  const label = (n: Node) => String((n.data as { label?: unknown } | undefined)?.label ?? n.id);
  const items = [...procs]
    .sort((a, b) => label(a).localeCompare(label(b)) || (a.id < b.id ? -1 : 1))
    .map((p) => ({ id: p.id, f: foot(p.id) }));
  if (items.length) {
    const cellW = Math.max(...items.map((it) => it.f.w));
    const cellH = Math.max(...items.map((it) => it.f.h));
    const TARGET_ASPECT = 1.3; // block width / height — a touch wider than square
    const cols = Math.max(1, Math.min(items.length,
      Math.round(Math.sqrt((TARGET_ASPECT * items.length * cellH) / cellW)) || 1));
    const packed = packBlock(items, treeRight + gaps.bandGap, treeTop, cols, gaps.procGapX, gaps.procGapY);
    for (const [id, p] of packed.pos) pos.set(id, p);
  }
  return finalize(nodes, pos, foot, Math.min(gaps.procGapX, gaps.colGap));
}

/**
 * Grid edge visibility — keep the view a clean, scannable catalog. With nothing
 * focused, draw ONLY the place-graph skeleton (the store org-chart edges) and
 * hide every process↔store wire, which would otherwise fan across the full width
 * from the left-hand tree to the right-hand grid. Hovering / pinning a process
 * reveals just that process's wires (stamped `_focused`); all other wires stay
 * hidden. Place edges (no store endpoint) are always kept.
 */
export function gridEdgeVisibility(edges: Edge[], focus: FocusContext, _nodes: Node[]): Edge[] {
  const active = new Set<string>([...focus.focused, ...focus.pinned]);
  const isPlace = (e: Edge) => wireStoreEndpoint(e) == null;
  if (active.size === 0) {
    const out = edges.filter(isPlace);
    return out.length === edges.length ? edges : out;   // identity when no wires
  }
  const out: Edge[] = [];
  for (const e of edges) {
    if (isPlace(e)) { out.push(e); continue; }
    if (active.has(e.source) || active.has(e.target)) out.push({ ...e, data: { ...e.data, _focused: true } });
    // non-focused wires stay hidden — the catalog stays clean
  }
  return out;
}

// "Tree": stores as a vertical org-chart, processes docked into swimlanes.
// id kept as 'flow-down' so saved layouts keyed to the old top-to-bottom mode
// still resolve here.
export const treeMode: LayoutMode = {
  id: 'flow-down', label: 'tree', tiers: TIERS,
  run: (nodes, edges) => treeLanesLayout(nodes, edges, TREE_GAPS),
  edgeVisibility: clusterGridEdgeVisibility,
  focusReveals: true,
};

// "Grid": the same org-chart, with every process pulled right into a grid.
export const gridMode: LayoutMode = {
  id: 'tree-grid', label: 'grid', tiers: TIERS,
  run: (nodes, edges) => treeGridLayout(nodes, edges, GRID_GAPS),
  edgeVisibility: gridEdgeVisibility,   // clean catalog: skeleton only, wires on hover
  focusReveals: true,
};
