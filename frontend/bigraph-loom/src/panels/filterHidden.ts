// src/panels/filterHidden.ts — pure visibility filter shared by App.tsx and tests.

/** Clamp a sidebar width to the allowed [200, 760] px range. */
export function clampSidebarWidth(w: number): number {
  return Math.max(200, Math.min(760, w));
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
