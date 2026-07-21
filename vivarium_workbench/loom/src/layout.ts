// src/layout.ts — async layered layout via elkjs.
//
// Goal: preserve the inner/outer store hierarchy.
//   - "Outers above inners" → top-to-bottom flow (`direction: DOWN`).
//   - "Inners at the same level shown next to each other in a cluster"
//     → for every store with children, wrap that store AND its children in
//     a synthetic compound. ELK lays out each compound in isolation, so
//     siblings stay together; layered direction DOWN puts the parent at
//     the top and the children in a row below.
//
// Process nodes are pulled into a store's cluster too: their `data.path`
// parent is the store they conceptually live in, so they go into the
// same synthetic compound and end up rendered next to their siblings.
//
// Only PLACE edges (parent-store → child-store nesting) are fed to ELK
// as ranking hints. Wire edges (process port ↔ store) are drawn by
// React Flow based on the resulting positions but don't pull processes
// out of their natural cluster.

import ELK from "elkjs/lib/elk.bundled.js";
import type { Node, Edge } from "@xyflow/react";

const elk = new ELK();

const COMMON_LAYOUT: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.spacing.nodeNodeBetweenLayers": "60",
  "elk.spacing.nodeNode": "40",
  "elk.padding": "[top=20,left=20,bottom=20,right=20]",
};

function nodeSize(n: Node): { width: number; height: number } {
  if (n.type === "process") return { width: 140, height: 60 };
  return { width: 80, height: 80 };  // store circle
}

function parentPathKey(n: Node): string | null {
  const path: unknown = (n.data as { path?: unknown })?.path;
  if (!Array.isArray(path) || path.length <= 1) return null;
  return (path as string[]).slice(0, -1).join(".");
}

function selfPathKey(n: Node): string | null {
  const path: unknown = (n.data as { path?: unknown })?.path;
  if (!Array.isArray(path) || path.length === 0) return null;
  return (path as string[]).join(".");
}

export async function applyLayout(
  nodes: Node[],
  edges: Edge[],
): Promise<Node[]> {
  if (nodes.length === 0) return [];

  // Index nodes and the store nodes by their path-key (so we can find a
  // node's compound parent by name).
  const byId = new Map<string, Node>();
  const storeByPath = new Map<string, Node>();
  for (const n of nodes) {
    byId.set(n.id, n);
    if (n.type === "store") {
      const pk = selfPathKey(n);
      if (pk) storeByPath.set(pk, n);
    }
  }

  // For every node, find its compound parent — the visible store node whose
  // own path matches this node's parent-path. Falls back to ROOT (top-level)
  // when no such store is visible (e.g. its container is collapsed).
  const ROOT = "__root__";
  const compoundParent = new Map<string, string>();
  const childrenByParent = new Map<string, string[]>([[ROOT, []]]);
  for (const n of nodes) {
    // Processes are NOT laid out by ELK with the store hierarchy — they would
    // pile into a horizontal row at the agent's layer. Instead they go in a
    // vertical column to the right of the store hierarchy (see below).
    if (n.type === "process") continue;
    const pk = parentPathKey(n);
    let parentId: string = ROOT;
    if (pk) {
      const parentStore = storeByPath.get(pk);
      if (parentStore) parentId = parentStore.id;
    }
    compoundParent.set(n.id, parentId);
    if (!childrenByParent.has(parentId)) childrenByParent.set(parentId, []);
    childrenByParent.get(parentId)!.push(n.id);
  }

  // Build the ELK tree. Stores with children get wrapped in a synthetic
  // compound whose first child is the store itself (rendered as a real
  // node) and whose remaining children are recursive compounds for the
  // store's own children. Direction DOWN places the store at the top
  // and its children in a row below.
  function buildElkChildren(parentId: string): unknown[] {
    const ids = childrenByParent.get(parentId) ?? [];
    return ids.map((id) => buildElkNodeFor(id));
  }
  function buildElkNodeFor(id: string): unknown {
    const node = byId.get(id);
    if (!node) return { id, width: 0, height: 0 };
    const grandChildren = childrenByParent.get(id) ?? [];
    if (grandChildren.length === 0) {
      // Leaf
      return {
        id,
        ...nodeSize(node),
      };
    }
    // Compound: synthetic wrapper containing this node + recursive
    // sub-compounds for its children.
    return {
      id: `wrap:${id}`,
      layoutOptions: COMMON_LAYOUT,
      children: [
        { id, ...nodeSize(node) },
        ...buildElkChildren(id),
      ],
      // Synthetic internal edges from the parent to each child push the
      // parent to the top of the compound under direction: DOWN.
      edges: grandChildren.map((cid) => {
        const childTarget = (childrenByParent.get(cid) ?? []).length > 0
          ? `wrap:${cid}` : cid;
        return {
          id: `internal:${id}->${childTarget}`,
          sources: [id],
          targets: [childTarget],
        };
      }),
    };
  }

  // Top-level place edges between top-level stores (rare but possible).
  const topLevelChildIds = childrenByParent.get(ROOT) ?? [];
  const topLevelStoreIds = new Set(topLevelChildIds);
  const topLevelEdges = edges
    .filter((e) => (e.data as { edgeType?: string } | undefined)?.edgeType === "place")
    .filter((e) => topLevelStoreIds.has(e.source) && topLevelStoreIds.has(e.target))
    .map((e) => {
      const targetWrap = (childrenByParent.get(e.target) ?? []).length > 0
        ? `wrap:${e.target}` : e.target;
      return {
        id: e.id,
        sources: [e.source],
        targets: [targetWrap],
      };
    });

  const elkGraph = {
    id: ROOT,
    layoutOptions: COMMON_LAYOUT,
    children: topLevelChildIds.map((id) => buildElkNodeFor(id)),
    edges: topLevelEdges,
  };

  const result = (await elk.layout(elkGraph as never)) as {
    children?: Array<{ id: string; x?: number; y?: number; children?: unknown[] }>;
  };

  // Flatten to absolute positions for every real node. Skip synthetic
  // "wrap:*" wrapper compounds — only real React Flow nodes need a position.
  const positions = new Map<string, { x: number; y: number }>();
  function walk(n: { id: string; x?: number; y?: number; children?: unknown[] }, parentX = 0, parentY = 0) {
    const absX = parentX + (n.x ?? 0);
    const absY = parentY + (n.y ?? 0);
    if (n.id !== ROOT && !n.id.startsWith("wrap:")) {
      positions.set(n.id, { x: absX, y: absY });
    }
    for (const c of (n.children ?? []) as Array<{ id: string; x?: number; y?: number; children?: unknown[] }>) {
      walk(c, absX, absY);
    }
  }
  walk({ id: ROOT, children: result.children });

  // Place process nodes in a GRID of vertical columns to the right of the
  // laid-out store hierarchy, evenly spaced — instead of ELK's horizontal row.
  // A single column of N processes is ~N*110px tall; for big composites that's
  // thousands of px and frames as an unusable sliver. So wrap into columns once
  // a column reaches ~14 rows, keeping the block roughly square and easy to fit.
  const storePts = [...positions.values()];
  const procNodes = nodes.filter((n) => n.type === "process");
  if (storePts.length > 0 && procNodes.length > 0) {
    const PROC_W = 140, PROC_H = 60, GAP_X = 240, H_SPACE = 70, V_SPACE = 50;
    const maxRight = Math.max(...storePts.map((p) => p.x)) + 80; // +store circle width
    const minY = Math.min(...storePts.map((p) => p.y));
    const procColX = maxRight + GAP_X;
    // Aim for a roughly-square block: rows ≈ ceil(sqrt(n)), capped to a sane band.
    const maxRows = Math.min(16, Math.max(6, Math.ceil(Math.sqrt(procNodes.length))));
    procNodes.forEach((n, i) => {
      const col = Math.floor(i / maxRows);
      const row = i % maxRows;
      positions.set(n.id, {
        x: procColX + col * (PROC_W + H_SPACE),
        y: minY + row * (PROC_H + V_SPACE),
      });
    });
  }

  return nodes.map((n) => {
    const p = positions.get(n.id);
    return p ? { ...n, position: p } : n;
  });
}

/**
 * Compact layout: tight grid, no hierarchy consideration. Synchronous
 * fallback for tiny composites or environments without async support.
 */
export function applyCompactLayout(nodes: Node[]): Node[] {
  const spacing = 100;
  const cols = Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
  return nodes.map((n, i) => ({
    ...n,
    position: {
      x: (i % cols) * spacing,
      y: Math.floor(i / cols) * spacing,
    },
  }));
}
