// src/layoutPolicy.ts — adaptive default layout policy.
//
// Reads a composite's SHAPE on first open (no saved view) and picks the initial
// layout mode + collapse-repeats state, so a graph with many repeated children
// lands readable without the user touching the Layout menu. It only seeds the
// INITIAL view; the first menu interaction replaces that state and the policy
// never runs again ("decide once, back off"). See
// docs/superpowers/specs/2026-10-24-loom-adaptive-layout-policy-design.md.
//
// Measured on the GDM composite (3 islands × 60 species, 220 visible nodes):
// today's default (Tree, collapse off) lays out 41015×1052 (aspect ~39 — an
// unreadable flat band). Grid layout + collapse-repeats on lays out 3648×1802
// (aspect ~2) with a clean top-down hierarchy. The policy below produces that.

import type { Edge } from '@xyflow/react';
import { overviewGraph } from './convert';

export interface InitialView {
  /** A layout mode id from layouts/registry: 'flow-down' (Tree) | 'tree-grid' (Grid). */
  modeId: string;
  collapseRedundant: boolean;
}

/** The single tuning surface. Thresholds sit far from real data (GDM = 220
 *  nodes / fan 60, cutoffs 40 / 12) so the choice is not knife-edge. */
export const POLICY = {
  /** Visible-node count above which we pack (Grid) + collapse repeats. */
  BIG_N: 40,
  /** A single parent's child-store fan above which we pack + collapse. */
  FAN: 12,
} as const;

const TREE: InitialView = { modeId: 'flow-down', collapseRedundant: false };
const GRID: InitialView = { modeId: 'tree-grid', collapseRedundant: true };

/** Largest number of child stores under any single parent (place-graph fan). */
export function maxSiblingFan(edges: Edge[]): number {
  const kids = new Map<string, number>();
  for (const e of edges) {
    if ((e as { data?: { edgeType?: string } }).data?.edgeType !== 'place') continue;
    kids.set(e.source, (kids.get(e.source) ?? 0) + 1);
  }
  let m = 0;
  for (const c of kids.values()) if (c > m) m = c;
  return m;
}

/** Decide the initial layout mode + collapse state from the graph's shape.
 *  Best-effort: any error falls back to today's static defaults. */
export function chooseInitialView(state: unknown): InitialView {
  try {
    const { nodes, edges } = overviewGraph(state);
    const big = nodes.length > POLICY.BIG_N
      || maxSiblingFan(edges as unknown as Edge[]) > POLICY.FAN;
    return big ? { ...GRID } : { ...TREE };
  } catch {
    return { ...TREE };
  }
}

/** Shape signals, exposed for tests / diagnostics. */
export function shapeSignals(state: unknown): { nNodes: number; maxFan: number } {
  const { nodes, edges } = overviewGraph(state);
  return { nNodes: nodes.length, maxFan: maxSiblingFan(edges as unknown as Edge[]) };
}
