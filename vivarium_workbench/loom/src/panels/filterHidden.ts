// src/panels/filterHidden.ts — pure visibility filter shared by App.tsx and tests.

/** Clamp a sidebar width to the allowed [200, 760] px range. */
export function clampSidebarWidth(w: number): number {
  return Math.max(200, Math.min(760, w));
}

/**
 * True when a node (identified by its `path`) should be hidden because the node
 * itself OR any of its ancestor stores is in `hidden`. Node ids are the path
 * joined by '.', with the root rendered as '<root>'. Walking the path prefixes
 * lets a single hidden parent cascade-hide its entire subtree.
 *
 * Pure — does not mutate its inputs.
 */
export function isHiddenByAncestor(path: string[], hidden: Set<string>): boolean {
  if (hidden.size === 0) return false;
  // The node's own id (path.join('.')) plus every ancestor-prefix id. The root
  // node has an empty path and the id '<root>'.
  const selfId = path.length ? path.join('.') : '<root>';
  if (hidden.has(selfId)) return true;
  for (let i = 1; i < path.length; i++) {
    if (hidden.has(path.slice(0, i).join('.'))) return true;
  }
  return false;
}

/**
 * Nearest VISIBLE node id for a (possibly hidden/collapsed) node id: the id
 * itself if visible, else the longest ancestor-prefix id that is visible.
 * Node ids are dotted paths. Returns null if nothing on the path is visible.
 */
export function nearestVisibleId(id: string, visibleIds: Set<string>): string | null {
  if (visibleIds.has(id)) return id;
  const parts = id.split('.');
  for (let i = parts.length - 1; i > 0; i--) {
    const anc = parts.slice(0, i).join('.');
    if (visibleIds.has(anc)) return anc;
  }
  return null;
}

/**
 * Re-target edges so a wire to a node inside a collapsed/hidden branch is drawn
 * to the nearest VISIBLE ancestor (the branch node) instead of being dropped,
 * AND de-dupe ALL edges per (source,target,edgeType). A process commonly wires
 * several ports to the same store; rendering one custom floating edge per port
 * is the main source of lag on big composites, so collapse them to one line per
 * (pair, direction). Self-loops are dropped. Pure — does not mutate its inputs.
 */
export function retargetEdgesToVisible<
  E extends { source: string; target: string; data?: { edgeType?: string } },
>(edges: E[], visibleIds: Set<string>): E[] {
  const out: E[] = [];
  const seen = new Set<string>();
  for (const e of edges) {
    const s = nearestVisibleId(e.source, visibleIds);
    const t = nearestVisibleId(e.target, visibleIds);
    if (!s || !t || s === t) continue;
    const key = `${s}__${t}__${e.data?.edgeType ?? ''}`;
    if (seen.has(key)) continue;  // collapse multi-port / overlapping wires to one
    seen.add(key);
    out.push(s === e.source && t === e.target ? e : { ...e, source: s, target: t });
  }
  return out;
}

/**
 * Compute the set of node ids that should be visually hidden (via React Flow's
 * `node.hidden` CSS flag) given the `hidden` selection set. A node is hidden
 * when it OR any ancestor store is in `hidden`. Reads each node's path from
 * `data.path`. Pure — does not mutate its inputs.
 */
export function hiddenNodeIds<N extends { id: string; data?: { path?: string[] } }>(
  nodes: N[],
  hidden: Set<string>,
): Set<string> {
  const out = new Set<string>();
  if (hidden.size === 0) return out;
  for (const n of nodes) {
    if (isHiddenByAncestor(n.data?.path ?? [], hidden)) out.add(n.id);
  }
  return out;
}

/**
 * Drop nodes whose id is in `hidden`, and drop any edge touching a dropped node.
 * Pure — does not mutate its inputs.
 */
export function filterHidden<
  N extends { id: string },
  E extends { source: string; target: string },
>(nodes: N[], edges: E[], hidden: Set<string>): { nodes: N[]; edges: E[] } {
  const visibleNodes = nodes.filter((n) => !hidden.has(n.id));
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  const visibleEdges = edges.filter(
    (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
  );
  return { nodes: visibleNodes, edges: visibleEdges };
}
