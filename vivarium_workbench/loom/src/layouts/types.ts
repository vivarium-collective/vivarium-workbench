// src/layouts/types.ts — the layout-mode extension seam.
//
// A LayoutMode owns how a composite is arranged: node positions, which
// edges are worth drawing, and (optionally) how process cards change with
// zoom. Registering a mode is the only thing needed to add a new view.

import type { Node, Edge } from '@xyflow/react';

/** A labeled horizontal band grouping consecutive column entries. */
export interface GroupBand {
  /** Cluster key, e.g. 'unique.active_ribosome'. Stable id. */
  key: string;
  /** Human-facing label rendered in the rail and on canvas. */
  label: string;
  /** Vertical extent in flow coordinates. */
  yStart: number;
  yEnd: number;
  /** Node id of the store this cluster is keyed on, if it is a real store. */
  keyStoreId: string | null;
  /** Node ids in this band, in render order. */
  nodeIds: string[];
}

export interface LayoutResult {
  nodes: Node[];
  bands?: GroupBand[];
}

export type ZoomTierId = 'glyph' | 'ports' | 'types' | 'contract' | 'full';

export interface ZoomTier {
  id: ZoomTierId;
  /** Lowest React Flow zoom at which this tier applies. */
  minZoom: number;
  cardWidth: number;
  /** Base height; modes may grow a card beyond this for port lists. */
  cardHeight: number;
}

export interface LayoutContext {
  compositeId: string | null;
  /** Current zoom tier, for modes whose geometry depends on card size. */
  tier: ZoomTierId;
  /** Clustering granularity, 0..1. Higher means fewer, coarser clusters. */
  granularity: number;
}

export interface FocusContext {
  /** Hovered or selected process node ids. */
  focused: Set<string>;
  /** Explicitly pinned process node ids. */
  pinned: Set<string>;
}

export interface LayoutMode {
  id: string;
  label: string;
  run(nodes: Node[], edges: Edge[], ctx: LayoutContext): Promise<LayoutResult>;
  /** Cull/annotate edges for this mode. Omitted means "draw them all". */
  edgeVisibility?(edges: Edge[], focus: FocusContext, nodes: Node[]): Edge[];
  /** Semantic-zoom tiers, ordered low to high. Omitted means fixed cards. */
  tiers?: ZoomTier[];
}
