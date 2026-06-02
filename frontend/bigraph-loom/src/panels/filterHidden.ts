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
 * to the nearest VISIBLE ancestor (the branch node) instead of being dropped.
 * Unchanged edges pass through as-is; re-targeted edges are de-duped per
 * (source,target,edgeType) so a heavily-wired collapsed branch shows one wire
 * per kind rather than dozens of overlapping ones. Self-loops are dropped.
 * Pure — does not mutate its inputs.
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
    if (s === e.source && t === e.target) { out.push(e); continue; }
    const key = `${s}__${t}__${e.data?.edgeType ?? ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ ...e, source: s, target: t });
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
