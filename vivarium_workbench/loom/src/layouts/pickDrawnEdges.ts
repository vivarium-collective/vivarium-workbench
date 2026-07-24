// src/layouts/pickDrawnEdges.ts — the "what actually gets drawn" seam,
// pulled out of App.tsx so it is testable without mounting React Flow.
//
// jsdom renders zero `.react-flow__edge` elements (the floating-edge geometry
// needs real getBoundingClientRect measurements it never gets there), so any
// DOM assertion about which edges ended up on screen passes vacuously. This
// function IS the entire selection logic — unit-test it directly instead.

import type { Node, Edge } from '@xyflow/react';
import type { LayoutMode, FocusContext } from './types';

/**
 * What actually gets drawn: `edges`, minus whatever `mode` culls for the
 * current `focus`. A mode with no `edgeVisibility` (hierarchy) returns the
 * SAME array identity — no filtering, no copy — so hierarchy mode stays
 * byte-identical to how it rendered before focus-driven culling existed.
 */
export function pickDrawnEdges(
  mode: LayoutMode,
  edges: Edge[],
  focus: FocusContext,
  nodes: Node[],
): Edge[] {
  const cull = mode.edgeVisibility;
  return cull ? cull(edges, focus, nodes) : edges;
}
