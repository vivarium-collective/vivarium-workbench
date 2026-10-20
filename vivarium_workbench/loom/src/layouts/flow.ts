// src/layouts/flow.ts - directional DAG layouts (beyond the default packing).
//
//   flow-down / flow-right : ELK `layered` flow network — ranks nodes by the
//     directed step-flow wires and orders within layers to cut crossings, so
//     store -> process -> store dependencies line up. 'hierarchy' orients it
//     top-to-bottom, 'flow' left-to-right. HUB-store wires (bulk/listeners/…
//     wired by ~everything) are excluded so the graph stays sparse; with that +
//     low thoroughness it is fast (the un-pruned version jammed ~30s on
//     hub-heavy composites).

import ELK from 'elkjs/lib/elk.bundled.js';
import type { Node, Edge } from '@xyflow/react';
import { TIERS } from './tiers';
import { wireStoreEndpoint, hubStoreIds } from '../storeFacts';
import { fullFootprint, removeOverlaps, type Box } from './clusterGrid';
import type { LayoutMode, LayoutResult } from './types';

const elk = new ELK();

// ---- ELK layered flow network -------------------------------------------------

async function elkFlowLayout(
  nodes: Node[], edges: Edge[], direction: 'DOWN' | 'RIGHT' = 'RIGHT',
): Promise<LayoutResult> {
  if (nodes.length === 0) return { nodes };

  const footprint = new Map(nodes.map((n) => [n.id, fullFootprint(n)] as const));
  const nodeIds = new Set(nodes.map((n) => n.id));

  // Hub-store wires (bulk/listeners wired by ~everything) only jam ELK ~30s on
  // LARGE composites — exclude them there for speed. On normal composites keep
  // EVERY store wire so each store sits between the processes that read/write it
  // (node → process → node), instead of floating free (which scattered the
  // graph). Threshold picked well above ordinary composites, below v2ecoli-scale.
  const nProcs = nodes.filter((n) => n.type === 'process').length;
  const hubs = nProcs > 24 ? hubStoreIds(edges) : new Set<string>();

  // Two edge classes, kept apart: DATAFLOW wires (store→process inputs,
  // process→store outputs) are the directed workflow DAG; PLACE edges are the
  // store-containment hierarchy (parent store → child store/process). They pull
  // in different directions, so mixing them in one ELK ranking distorts the
  // flow — a universal root store (`state → everything`) or a nested result
  // leaf (`models/results → BIOMD…`) drags unrelated nodes across layers.
  const dataflow: Array<{ source: string; target: string }> = [];
  const place: Array<{ source: string; target: string }> = [];
  const seenD = new Set<string>();
  const seenP = new Set<string>();
  const push = (
    arr: Array<{ source: string; target: string }>, seen: Set<string>,
    a: string, b: string,
  ) => {
    if (!nodeIds.has(a) || !nodeIds.has(b) || a === b) return;
    const k = a + ' ' + b;
    if (seen.has(k)) return;
    seen.add(k);
    arr.push({ source: a, target: b });
  };
  for (const e of edges) {
    const kind = (e.data as { edgeType?: string } | undefined)?.edgeType;
    if (kind === 'place') { push(place, seenP, e.source, e.target); continue; }
    const store = wireStoreEndpoint(e);
    if (store == null || hubs.has(store)) continue;                 // hub wire → skip
    push(dataflow, seenD, e.source, e.target);                      // step flow
  }

  // RIGHT ("Flow"): rank by DATAFLOW ONLY, so the graph reads as the workflow
  // DAG — root stores → process inputs → process outputs → the next stores,
  // lined up left→right (cycles broken automatically by ELK). Containment is
  // restored afterward by tucking place-only sub-stores under their parent.
  // DOWN keeps place edges in the ranking (the containment tree IS what it draws).
  const rankEdges = direction === 'RIGHT' ? dataflow : [...dataflow, ...place];
  // Small graphs can afford BRANDES_KOEPF (aligns nodes into straight lines so
  // a store sits level with the process it feeds — "nicely lined up") and high
  // thoroughness; big composites fall back to the fast path to avoid the ~30s
  // jam. nProcs>24 is the same large-composite threshold as the hub pruning.
  const big = nProcs > 24;
  const rightOpts: Record<string, string> = big ? {
    'elk.layered.nodePlacement.strategy': 'SIMPLE',
    'elk.layered.thoroughness': '1',
  } : {
    'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
    'elk.layered.nodePlacement.bk.fixedAlignment': 'BALANCED',
    'elk.layered.thoroughness': '7',
    // Every source (a root store with no inbound dataflow) starts at layer 0, so
    // parallel input tracks — e.g. the biomodel_id → load chain alongside the
    // models/ref_grid → runners chain — begin at the left together instead of
    // ELK offsetting a disconnected component into the middle.
    'elk.layered.layering.strategy': 'LONGEST_PATH_SOURCE',
    // Keep disconnected components in one shared coordinate frame (stacked, not
    // offset) so their layers line up column-for-column.
    'elk.separateConnectedComponents': 'false',
  };

  const elkGraph = {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': direction,
      // Reserve a clear gutter between layers so the store→process→store
      // columns read as distinct bands (the footprints already prevent overlap;
      // this is the visual breathing room between columns).
      'elk.layered.spacing.nodeNodeBetweenLayers': direction === 'RIGHT' ? '80' : '48',
      'elk.spacing.nodeNode': '36',
      'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
      'elk.layered.nodePlacement.strategy': 'SIMPLE',
      'elk.layered.thoroughness': '1',
      ...(direction === 'RIGHT' ? rightOpts : {}),
    } as Record<string, string>,
    children: nodes.map((n) => {
      const f = footprint.get(n.id)!;
      return { id: n.id, width: f.w, height: f.h };
    }),
    edges: rankEdges.map((s, i) => ({ id: `f${i}`, sources: [s.source], targets: [s.target] })),
  };

  const res = (await elk.layout(elkGraph as never)) as {
    children?: Array<{ id: string; x?: number; y?: number }>;
  };
  const pos = new Map<string, { x: number; y: number }>();
  for (const c of res.children ?? []) pos.set(c.id, { x: c.x ?? 0, y: c.y ?? 0 });
  const out = nodes.map((n) => {
    const p = pos.get(n.id);
    return p ? { ...n, position: { x: p.x, y: p.y } } : n;
  });

  // flow-right: containment-only stores (meta/runs/provenance under diagnostics,
  // nested result leaves, framework roots) carry NO dataflow wire, so ELK — which
  // ranked by dataflow alone — drops them all into layer 0 (far left, scattered).
  // Reunite each with its place-graph parent: stack it just under the parent so
  // sub-stores hang below the main flow (as in the target layout) rather than
  // pile up at the origin. Flow nodes (those with a dataflow wire) keep their
  // ELK positions.
  if (direction === 'RIGHT' && place.length > 0) {
    const inDataflow = new Set<string>();
    for (const e of dataflow) { inDataflow.add(e.source); inDataflow.add(e.target); }
    const parentOf = new Map<string, string>();  // child → place parent
    for (const e of place) parentOf.set(e.target, e.source);
    const posById = new Map(out.map((n) => [n.id, n] as const));
    const gap = 16;
    // Resolve shallow→deep so a parent is placed before its children.
    const depthOf = (id: string): number => {
      let d = 0; let cur = id;
      const guard = new Set<string>();
      while (parentOf.has(cur) && !guard.has(cur)) { guard.add(cur); cur = parentOf.get(cur)!; d++; }
      return d;
    };
    const orphans = out
      .filter((n) => !inDataflow.has(n.id) && parentOf.has(n.id))
      .sort((a, b) => depthOf(a.id) - depthOf(b.id));
    // Track the running bottom of each parent's child stack so siblings don't overlap.
    const stackBottom = new Map<string, number>();
    for (const n of orphans) {
      const parent = posById.get(parentOf.get(n.id)!);
      if (!parent) continue;
      const pf = footprint.get(parent.id) ?? { w: 120, h: 80 };
      const base = stackBottom.get(parent.id) ?? (parent.position.y + pf.h + gap);
      n.position = { x: parent.position.x, y: base };
      const nf = footprint.get(n.id) ?? { w: 120, h: 80 };
      stackBottom.set(parent.id, base + nf.h + gap);
    }
  }

  // Guarantee no overlaps. ELK reserves each node's footprint, but the RIGHT
  // re-banking above can slide a store onto a process, and tight layer spacing
  // can graze neighbours — push any overlapping boxes apart (order-stable,
  // minimal move; early-returns when nothing overlaps, e.g. clean flow-down).
  const boxes: Box[] = out.map((n) => {
    const f = footprint.get(n.id) ?? { w: 120, h: 80 };
    return { id: n.id, x: n.position.x, y: n.position.y, w: f.w, h: f.h };
  });
  removeOverlaps(boxes, 28, 1000);
  const boxById = new Map(boxes.map((b) => [b.id, b] as const));
  return {
    nodes: out.map((n) => {
      const b = boxById.get(n.id);
      return b ? { ...n, position: { x: b.x, y: b.y } } : n;
    }),
  };
}

// "Hierarchy": ELK layered top-to-bottom — the store dependency hierarchy.
export const flowElkDownMode: LayoutMode = {
  id: 'flow-down', label: 'hierarchy', tiers: TIERS,
  run: (nodes, edges) => elkFlowLayout(nodes, edges, 'DOWN'),
};

// "Flow": ELK layered left-to-right — the workflow DAG, so store -> process ->
// store dependencies line up in reading order.
export const flowElkRightMode: LayoutMode = {
  id: 'flow-right', label: 'flow', tiers: TIERS,
  run: (nodes, edges) => elkFlowLayout(nodes, edges, 'RIGHT'),
};
