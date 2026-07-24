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
} from '../layouts/processColumn';
import { HUB_ONLY_KEY, CROSS_CUTTING_KEY } from '../layouts/affinity';
import type { LayoutContext } from '../layouts/types';
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

const ctx: LayoutContext = { compositeId: 'c', tier: 'mid', granularity: 0.30 };
const MID = TIERS.find((t) => t.id === 'mid')!;

const procId = (label: string) => `agents.0.${label}`;
const storeId = (dotted: string) => `agents.0.${dotted}`;

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
});
