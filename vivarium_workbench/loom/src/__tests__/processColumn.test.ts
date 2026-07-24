// Geometry guard for the process-column layout mode.
//
// Nodes are built by running the REAL `stateToReactFlow` over a minimal
// composite state, exactly as `affinity.test.ts` does. That matters: the
// clustering this mode consumes reads the RESOLVED absolute `*PortsTarget`
// fields, which only convert.ts produces. Hand-rolled nodes carrying just the
// lossy display-only `*PortsSchema` strings would cluster every process into
// the `~hub-only` bucket and the banding assertions below would be vacuous.
import { describe, it, expect } from 'vitest';
import type { Node } from '@xyflow/react';
import { stateToReactFlow } from '../convert';
import {
  processColumnMode, TIERS, CARD_GAP, CLUSTER_GAP, GUTTER, UNCLUSTERED_KEY,
  DEFAULT_GRANULARITY, hubFractionFor, STORE_W, STORE_H,
} from '../layouts/processColumn';
import { HUB_ONLY_KEY, CROSS_CUTTING_KEY } from '../layouts/affinity';
import type { LayoutContext, ZoomTier } from '../layouts/types';
import fixture from './fixtures/v2ecoli-baseline.json';

/**
 * Build a composite whose processes all live at `agents.0.<label>` (so their
 * wiring scope — the parent store — is `agents.0`) and whose stores are
 * created by nesting the given dotted paths under the same agent.
 *
 * `procs` maps a process label to the dotted store path its single input port
 * wires to, written relative to `agents.0` just as real wiring is.
 */
function build(procs: Record<string, string>, stores: string[]): Node[] {
  const agent: Record<string, unknown> = {};
  for (const dotted of stores) {
    let cur = agent;
    for (const seg of dotted.split('.')) {
      if (!cur[seg]) cur[seg] = {};
      cur = cur[seg] as Record<string, unknown>;
    }
  }
  for (const [label, target] of Object.entries(procs)) {
    agent[label] = {
      _type: 'process',
      address: 'local:X',
      config: {},
      inputs: { s: target.split('.') },
      outputs: {},
    };
  }
  const { nodes } = stateToReactFlow({ agents: { '0': agent } });
  return nodes as unknown as Node[];
}

// Runs the DEFAULT app path: useLayoutMode seeds granularity to exactly this,
// and hubFractionFor anchors it on the validated hubFraction (see I-2 below).
const ctx: LayoutContext = { compositeId: 'c', tier: 'mid', granularity: DEFAULT_GRANULARITY };
const MID = TIERS.find((t) => t.id === 'mid')!;

const procId = (label: string) => `agents.0.${label}`;
const storeId = (dotted: string) => `agents.0.${dotted}`;

/** Bounding box of a laid-out graph, using the mode's own card/store sizes
 *  (React Flow measures the DOM; jsdom does not, so size them here). */
function boundsOf(out: Node[], tier: ZoomTier) {
  const size = (n: Node) => (n.type === 'process'
    ? { w: tier.cardWidth, h: tier.cardHeight } : { w: STORE_W, h: STORE_H });
  const xs = out.map((n) => n.position.x);
  const ys = out.map((n) => n.position.y);
  const x2 = out.map((n) => n.position.x + size(n).w);
  const y2 = out.map((n) => n.position.y + size(n).h);
  const width = Math.max(...x2) - Math.min(...xs);
  const height = Math.max(...y2) - Math.min(...ys);
  return { width, height };
}

/** The zoom React Flow's fitView settles on — `getViewportForBounds` picks the
 *  smaller of the two axis fits, padded. App passes `padding: 0.2`. */
function fitZoom(b: { width: number; height: number }, vw = 1400, vh = 900, pad = 0.2) {
  return Math.min(vw / (b.width * (1 + pad)), vh / (b.height * (1 + pad)));
}

describe('processColumnMode', () => {
  it('places every process in a single column at one x', async () => {
    const nodes = build(
      { a: 'unique.RNA', b: 'unique.RNA', c: 'bulk' },
      ['unique.RNA', 'bulk'],
    );
    const { nodes: out } = await processColumnMode.run(nodes, [], ctx);
    const xs = new Set(out.filter((n) => n.type === 'process').map((n) => n.position.x));
    expect(xs.size).toBe(1);
  });

  it('never overlaps two cards vertically', async () => {
    // All four wire the same store, which at n=4 is itself a hub, so every
    // process lands in the terminal `~hub-only` bucket — the largest cluster
    // on the real fixture too. It must still be laid out like any other.
    const nodes = build(
      { a: 'unique.RNA', b: 'unique.RNA', c: 'unique.RNA', d: 'unique.RNA' },
      ['unique.RNA'],
    );
    const { nodes: out } = await processColumnMode.run(nodes, [], ctx);
    const ys = out.filter((n) => n.type === 'process')
      .map((n) => n.position.y).sort((p, q) => p - q);
    expect(ys.length).toBe(4);
    for (let i = 1; i < ys.length; i++) {
      expect(ys[i] - ys[i - 1]).toBeGreaterThanOrEqual(MID.cardHeight + CARD_GAP);
    }
  });

  it('emits one band per cluster covering its members', async () => {
    const nodes = build(
      { a: 'unique.RNA', b: 'unique.RNA', c: 'unique.promoter' },
      ['unique.RNA', 'unique.promoter'],
    );
    const { bands } = await processColumnMode.run(nodes, [], ctx);
    expect(bands!.length).toBeGreaterThanOrEqual(2);
    for (const b of bands!) {
      expect(b.yEnd).toBeGreaterThan(b.yStart);
      expect(b.nodeIds.length).toBeGreaterThan(0);
    }
  });

  it('separates clusters by more than it separates cards', async () => {
    const nodes = build(
      { a: 'unique.RNA', b: 'unique.RNA', c: 'unique.promoter' },
      ['unique.RNA', 'unique.promoter'],
    );
    const { bands } = await processColumnMode.run(nodes, [], ctx);
    const sorted = [...bands!].sort((p, q) => p.yStart - q.yStart);
    expect(sorted.length).toBeGreaterThanOrEqual(2);
    expect(sorted[1].yStart - sorted[0].yEnd).toBeGreaterThanOrEqual(CLUSTER_GAP);
  });

  it('puts stores to the right of the column', async () => {
    const nodes = build({ a: 'unique.RNA' }, ['unique.RNA']);
    const { nodes: out } = await processColumnMode.run(nodes, [], ctx);
    const px = out.find((n) => n.id === procId('a'))!.position.x;
    for (const s of out.filter((n) => n.type === 'store')) {
      expect(s.position.x).toBeGreaterThan(px);
    }
    const minStoreX = Math.min(...out.filter((n) => n.type === 'store').map((n) => n.position.x));
    expect(minStoreX).toBe(MID.cardWidth + GUTTER);
  });

  it('bands cover every process exactly once, including bookkeeping ones', async () => {
    // `rna_listener` is bookkeeping, so `clusterProcesses` drops it. The column
    // must still give it a slot rather than leave it at a stale position.
    const nodes = build(
      { a: 'unique.RNA', b: 'unique.RNA', c: 'unique.promoter', rna_listener: 'bulk' },
      ['unique.RNA', 'unique.promoter', 'bulk'],
    );
    const { nodes: out, bands } = await processColumnMode.run(nodes, [], ctx);
    const banded = bands!.flatMap((b) => b.nodeIds);
    expect(new Set(banded).size).toBe(banded.length);
    const processIds = out.filter((n) => n.type === 'process').map((n) => n.id).sort();
    expect([...banded].sort()).toEqual(processIds);

    // The leftover bucket is last, and the bookkeeping process is in it.
    const byY = [...bands!].sort((p, q) => p.yStart - q.yStart);
    const last = byY[byY.length - 1];
    expect(last.key).toBe(UNCLUSTERED_KEY);
    expect(last.nodeIds).toEqual([procId('rna_listener')]);
    expect(last.keyStoreId).toBeNull();
  });

  it('resolves keyStoreId to the real store node backing the cluster key', async () => {
    const nodes = build(
      { a: 'unique.RNA', b: 'unique.RNA', c: 'unique.promoter' },
      ['unique.RNA', 'unique.promoter'],
    );
    const { bands } = await processColumnMode.run(nodes, [], ctx);
    const rna = bands!.find((b) => b.key === 'unique.RNA')!;
    expect(rna.keyStoreId).toBe(storeId('unique.RNA'));
  });

  it('never resolves a key store from an unrelated branch', async () => {
    // `agents.0` holds the processes but has NO `unique.RNA` store of its own
    // (collapsed subtree, or simply absent); `agents.1` does. A unique-suffix
    // scan matches exactly one store and would hand back the WRONG branch's;
    // the parent-anchored lookup reports none.
    const { nodes } = stateToReactFlow({
      agents: {
        '0': {
          a: { _type: 'process', address: 'local:X', config: {}, inputs: { s: ['unique', 'RNA'] }, outputs: {} },
          b: { _type: 'process', address: 'local:X', config: {}, inputs: { s: ['unique', 'RNA'] }, outputs: {} },
        },
        '1': { unique: { RNA: {} } },
      },
    });
    const { bands } = await processColumnMode.run(nodes as unknown as Node[], [], ctx);
    const rna = bands!.find((b) => b.key === 'unique.RNA')!;
    expect(rna).toBeDefined();
    expect(rna.keyStoreId).toBeNull();
  });

  it('re-flows the column for a coarser zoom tier', async () => {
    const nodes = build(
      { a: 'unique.RNA', b: 'unique.RNA', c: 'unique.promoter' },
      ['unique.RNA', 'unique.promoter'],
    );
    const far = TIERS.find((t) => t.id === 'far')!;
    const { nodes: out } = await processColumnMode.run(nodes, [], { ...ctx, tier: 'far' });
    const ys = out.filter((n) => n.type === 'process')
      .map((n) => n.position.y).sort((p, q) => p - q);
    expect(ys[1] - ys[0]).toBe(far.cardHeight + CARD_GAP);
    const minStoreX = Math.min(...out.filter((n) => n.type === 'store').map((n) => n.position.x));
    expect(minStoreX).toBe(far.cardWidth + GUTTER);
  });

  it('is a no-op-safe layout for a composite with no processes', async () => {
    const nodes = build({}, ['unique.RNA']);
    const { nodes: out, bands } = await processColumnMode.run(nodes, [], ctx);
    expect(bands).toEqual([]);
    expect(out.length).toBe(nodes.length);
  });
});

// Quality guard on the REAL composite — the substitute for eyeballing the dev
// server. Unit tests above use three-process toys where "one column, stores to
// the right, banded" is trivially true; this asserts the same invariants at
// 351 nodes / 46 processes, where they are not.
describe('processColumnMode on the real v2ecoli baseline', () => {
  const { nodes, edges } = stateToReactFlow((fixture as any).state);

  it('puts every process in one column and every store to its right', async () => {
    const { nodes: out } = await processColumnMode.run(
      nodes as unknown as Node[], edges as unknown as any[], ctx,
    );
    const procs = out.filter((n) => n.type === 'process');
    const stores = out.filter((n) => n.type === 'store');
    expect(procs.length).toBe(46);
    expect(new Set(procs.map((n) => n.position.x)).size).toBe(1);
    const columnRight = procs[0].position.x + MID.cardWidth;
    for (const s of stores) expect(s.position.x).toBeGreaterThanOrEqual(columnRight + GUTTER);
  });

  it('bands the whole column, terminal buckets last, keyed on real stores', async () => {
    const { bands } = await processColumnMode.run(
      nodes as unknown as Node[], edges as unknown as any[], ctx,
    );
    const banded = bands!.flatMap((b) => b.nodeIds);
    expect(banded.length).toBe(46);
    expect(new Set(banded).size).toBe(46);

    // Clustering ranks the two terminal buckets last; leftovers follow them.
    expect(bands!.map((b) => b.key).slice(-3))
      .toEqual([CROSS_CUTTING_KEY, HUB_ONLY_KEY, UNCLUSTERED_KEY]);

    // Bands run top-to-bottom and never overlap. The gap between one band's
    // bottom edge and the next band's top is CLUSTER_GAP *on top of* the
    // CARD_GAP the prefix sum already advanced past the last card — so
    // clusters are separated by strictly more than cards are, by CLUSTER_GAP.
    for (let i = 1; i < bands!.length; i++) {
      expect(bands![i].yStart - bands![i - 1].yEnd).toBe(CLUSTER_GAP + CARD_GAP);
    }
    // Every band keyed on a real store resolves to that store's node id.
    for (const b of bands!) {
      if (b.key.startsWith('~')) expect(b.keyStoreId).toBeNull();
      else expect(b.keyStoreId).toBe(`agents.0.${b.key}`);
    }
  });

  // ---- I-2: the SHIPPED default must be the VALIDATED clustering -----------
  it('runs the app default at the hubFraction affinityFixture.test.ts validates', () => {
    // affinityFixture.test.ts measures the grouping at hubFraction 0.30 and
    // documents that the boundary is fragile. useLayoutMode seeds granularity
    // to DEFAULT_GRANULARITY, so this is the app's real path — it must land on
    // 0.30 EXACTLY, not merely near it (0.275 and 0.30 give hubCut 7 vs 8 and
    // coincide on this fixture only because no store sits at df=7).
    expect(hubFractionFor(DEFAULT_GRANULARITY)).toBe(0.30);
  });

  it('pins the exact bands the default path produces', async () => {
    // Band count/coverage/ordering are asserted above; this pins the actual
    // GROUPING. Without it a drift in the granularity map, the default, or
    // hubCut reshuffles which processes share a band while every other
    // assertion stays green.
    const { bands } = await processColumnMode.run(
      nodes as unknown as Node[], edges as unknown as any[], ctx,
    );
    expect(bands!.map((b) => `${b.key} (${b.nodeIds.length})`)).toEqual([
      'unique.active_ribosome (5)',
      'boundary (4)',
      'unique.promoter (4)',
      'unique.active_RNAP (2)',
      'unique.full_chromosome (2)',
      'listeners.mass (1)',
      'ppgpp_state (1)',
      'unique.DnaA_box (1)',
      '~cross-cutting (1)',
      '~hub-only (6)',
      '~unclustered (19)',
    ]);
  });

  // ---- I-1: the mode must be READABLE in its default framing ---------------
  it('frames readably: near-square bounds, not a 22:1 sliver', async () => {
    // Regression guard. The store side used to reuse hierarchy mode's ELK pass,
    // which rows every sibling set out: 37,980 x 1,680 for the store block,
    // 38,380 x 5,392 overall — fitView at 1400x900 landed at zoom 0.030, i.e.
    // the process column rendered ~8px wide. packStoreTree shelf-packs instead:
    // store block 2,600 x 4,420, overall 3,000 x 5,392, zoom 0.139 at the mid
    // tier and 0.170 once the column re-flows at the far tier it settles into.
    for (const tier of [MID, TIERS.find((t) => t.id === 'far')!]) {
      const { nodes: out } = await processColumnMode.run(
        nodes as unknown as Node[], edges as unknown as any[], { ...ctx, tier: tier.id },
      );
      const b = boundsOf(out, tier);
      expect(Math.max(b.width / b.height, b.height / b.width)).toBeLessThanOrEqual(4);
      expect(fitZoom(b)).toBeGreaterThan(0.12);
    }
    // The far tier is where the app settles (any zoom < 0.35 selects it), and
    // there the whole graph must be genuinely readable.
    const far = TIERS.find((t) => t.id === 'far')!;
    const { nodes: outFar } = await processColumnMode.run(
      nodes as unknown as Node[], edges as unknown as any[], { ...ctx, tier: 'far' },
    );
    expect(fitZoom(boundsOf(outFar, far))).toBeGreaterThanOrEqual(0.15);
  });

  it('keeps every store above its own children in the packed block', async () => {
    // Compactness must not cost the nesting cue: a parent store still sits
    // strictly above (and never overlapping) the block holding its children.
    const { nodes: out } = await processColumnMode.run(
      nodes as unknown as Node[], edges as unknown as any[], ctx,
    );
    const stores = out.filter((n) => n.type === 'store');
    const byPath = new Map(stores.map((n) => [(n.data as any).path.join('.'), n]));
    let checked = 0;
    for (const n of stores) {
      const path = (n.data as any).path as string[];
      const parent = path.length > 1 ? byPath.get(path.slice(0, -1).join('.')) : undefined;
      if (!parent) continue;
      checked++;
      expect(n.position.y).toBeGreaterThanOrEqual(parent.position.y + STORE_H);
    }
    expect(checked).toBeGreaterThan(250);   // the fixture's 305 stores, less roots
  });
});
