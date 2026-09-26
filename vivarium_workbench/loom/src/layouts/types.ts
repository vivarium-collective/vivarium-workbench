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

export type ZoomTierId = 'glyph' | 'ports' | 'types' | 'config' | 'contract' | 'full';

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
  /** Print-figure mode: pack tighter (shorter springs) and size cards to the
   *  actual (possibly narrowed) card width, so a busy multi-process composite
   *  fits a book page legibly instead of sprawling. Figure renders only. */
  compact?: boolean;
}

export interface FocusContext {
  /** Hovered or selected process node ids. */
  focused: Set<string>;
  /** Explicitly pinned process node ids. */
  pinned: Set<string>;
  /**
   * Hierarchy mode's "Show hub wires" toggle. When true, `edgeVisibility`
   * stops hiding hub-store wires and returns every edge. Carried on the focus
   * context because it is the one input, besides focus, that the drawn-edge
   * seam depends on. Undefined/false means hub wires stay hidden (the default).
   */
  showHubWires?: boolean;
}

export interface LayoutMode {
  id: string;
  label: string;
  run(nodes: Node[], edges: Edge[], ctx: LayoutContext): Promise<LayoutResult>;
  /** Cull/annotate edges for this mode. Omitted means "draw them all". */
  edgeVisibility?(edges: Edge[], focus: FocusContext, nodes: Node[]): Edge[];
  /**
   * Whether this mode reveals edges in response to FOCUS (hover / selection /
   * pins). Drives App's hover handlers, focus hint, and pin-pruning. Process-
   * column mode is focus-driven; hierarchy mode is NOT — it culls hub wires
   * structurally via a toggle, so its focus is inert and these behaviors stay
   * off (no hover re-render, no "hover to reveal" hint). Distinct from
   * `edgeVisibility` being defined: a mode can cull edges without reacting to
   * focus. Omitted means false.
   */
  focusReveals?: boolean;
  /** Semantic-zoom tiers, ordered low to high. Omitted means fixed cards. */
  tiers?: ZoomTier[];
}
