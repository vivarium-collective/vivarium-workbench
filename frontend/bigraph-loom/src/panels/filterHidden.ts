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
