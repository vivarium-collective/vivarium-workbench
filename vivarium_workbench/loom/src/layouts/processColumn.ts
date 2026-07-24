// src/layouts/processColumn.ts — processes in one clustered column, stores
// laid out to the right by the existing hierarchy pass.
//
// The column is one-dimensional, so re-flowing it for a zoom-tier change is
// a prefix sum rather than a graph layout. That is what makes semantic zoom
// affordable at several hundred nodes.

import type { Node, Edge } from '@xyflow/react';
import { applyLayout } from './hierarchy';
import { clusterProcesses } from './affinity';
import type { LayoutMode, LayoutResult, LayoutContext, GroupBand, ZoomTier } from './types';

export const TIERS: ZoomTier[] = [
  { id: 'far',  minZoom: 0,    cardWidth: 180, cardHeight: 56 },
  { id: 'mid',  minZoom: 0.35, cardWidth: 220, cardHeight: 92 },
  { id: 'near', minZoom: 0.85, cardWidth: 320, cardHeight: 120 },
];

export const CARD_GAP = 16;
export const CLUSTER_GAP = 44;
export const GUTTER = 180;

/**
 * Trailing band for processes `clusterProcesses` never sees — it filters out
 * bookkeeping processes (`*listener*`, `allocator*`, `*unique_update*`), which
 * on the real v2ecoli baseline is 19 of 46. Those nodes are hidden by default
 * but they ARE handed to the layout (App's layout effect only filters by
 * `collapsed`, not by `hidden`), so without a slot here they would keep
 * whatever position a previous mode left them at and land on top of the store
 * hierarchy the moment a user unhides them.
 */
export const UNCLUSTERED_KEY = '~unclustered';

/** Map the rail's coarse..fine granularity slider onto a hub threshold.
 *  Lower hubFraction disqualifies more stores as keys, giving finer groups. */
function hubFractionFor(granularity: number): number {
  const g = Math.min(1, Math.max(0, granularity));
  return 0.20 + g * 0.25;   // 0.20 (fine) .. 0.45 (coarse)
}

/**
 * Find the store node a cluster key names.
 *
 * Cluster keys are RELATIVE to the process's parent store (`unique.RNA`),
 * while store node ids are absolute dotted paths (`agents.0.unique.RNA`), so an
 * exact id match only happens for top-level composites. Fall back to a unique
 * suffix match; when several stores share the suffix — the multi-agent case,
 * `agents.0.unique.RNA` vs `agents.1.unique.RNA` — the key genuinely does not
 * identify one store, so report none rather than pick arbitrarily.
 */
function resolveKeyStoreId(key: string, storeIds: string[]): string | null {
  if (key.startsWith('~')) return null;          // terminal buckets name no store
  if (storeIds.includes(key)) return key;
  const suffix = `.${key}`;
  const matches = storeIds.filter((id) => id.endsWith(suffix));
  return matches.length === 1 ? matches[0] : null;
}

export const processColumnMode: LayoutMode = {
  id: 'process-column',
  label: 'Process column',
  tiers: TIERS,

  async run(nodes: Node[], edges: Edge[], ctx: LayoutContext): Promise<LayoutResult> {
    const tier = TIERS.find((t) => t.id === ctx.tier) ?? TIERS[1];
    const { clusters } = clusterProcesses(nodes, { hubFraction: hubFractionFor(ctx.granularity) });

    // Stores keep the hierarchy arrangement, shifted right of the column.
    const storeNodes = nodes.filter((n) => n.type !== 'process');
    const laidOutStores = await applyLayout(storeNodes, edges.filter(
      (e) => (e.data as { edgeType?: string } | undefined)?.edgeType === 'place'));

    const minStoreX = laidOutStores.length
      ? Math.min(...laidOutStores.map((n) => n.position.x)) : 0;
    const shift = (tier.cardWidth + GUTTER) - minStoreX;
    const storeById = new Map(
      laidOutStores.map((n) => [n.id, { ...n, position: { x: n.position.x + shift, y: n.position.y } }]),
    );
    const storeIds = laidOutStores.map((n) => n.id);

    // Every process gets a band, even the bookkeeping ones clustering drops.
    const clustered = new Set(clusters.flatMap((c) => c.processIds));
    const leftovers = nodes
      .filter((n) => n.type === 'process' && !clustered.has(n.id))
      .map((n) => n.id)
      .sort();
    const groups = leftovers.length
      ? [...clusters, { key: UNCLUSTERED_KEY, label: 'bookkeeping', processIds: leftovers }]
      : clusters;

    // Column: prefix-sum down the clusters. O(n), no graph layout.
    const posById = new Map<string, { x: number; y: number }>();
    const bands: GroupBand[] = [];
    let y = 0;
    for (const c of groups) {
      if (c.processIds.length === 0) continue;
      const yStart = y;
      for (const id of c.processIds) {
        posById.set(id, { x: 0, y });
        y += tier.cardHeight + CARD_GAP;
      }
      bands.push({
        key: c.key,
        label: c.label,
        yStart,
        yEnd: y - CARD_GAP,
        keyStoreId: resolveKeyStoreId(c.key, storeIds),
        nodeIds: [...c.processIds],
      });
      y += CLUSTER_GAP;
    }

    const out = nodes.map((n) => {
      const p = posById.get(n.id);
      if (p) return { ...n, position: p };
      const s = storeById.get(n.id);
      return s ?? n;
    });

    return { nodes: out, bands };
  },
};
