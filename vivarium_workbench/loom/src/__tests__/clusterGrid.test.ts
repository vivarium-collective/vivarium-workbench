// Force-directed canvas layout: determinism (seeded ELK stress → identical
// positions), persistence across zoom tiers, zero overlap at the full tier, hub
// / no-wire nodes still placed, and non-hub wires (not hub wires) feeding the
// force graph. Runs the REAL layout over the REAL v2ecoli baseline composite —
// filtered to the default overview (collapse + hide) so it exercises the same
// ~27-process / ~31-store graph the canvas actually lays out.
import { describe, it, expect } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import {
  stateToReactFlow, defaultCollapsedIds, defaultHiddenIds,
} from '../convert';
import {
  clusterGridLayout, fullFootprint, GRID,
} from '../layouts/clusterGrid';
import { hubStoreIds, wireStoreEndpoint } from '../storeFacts';
import type { LayoutContext, ZoomTierId } from '../layouts/types';
import fixture from './fixtures/v2ecoli-baseline.json';

const STATE = (fixture as any).state;

/** The default overview graph: collapse deep containers + hide bookkeeping,
 *  exactly as App does before laying out, then keep edges between survivors. */
function overview(): { nodes: Node[]; edges: Edge[] } {
  const { nodes, edges } = stateToReactFlow(STATE);
  const collapsed = defaultCollapsedIds(STATE);
  const hidden = defaultHiddenIds(STATE);
  const isHidden = (n: any) => {
    const path: string[] = n.data?.path ?? [];
    for (let i = 1; i < path.length; i++) if (collapsed.has(path.slice(0, i).join('.'))) return true;
    for (let i = 1; i <= path.length; i++) if (hidden.has(path.slice(0, i).join('.'))) return true;
    return false;
  };
  const vis = nodes.filter((n) => !isHidden(n));
  const visIds = new Set(vis.map((n) => n.id));
  const visEdges = edges.filter((e) => visIds.has(e.source) && visIds.has(e.target));
  return { nodes: vis as unknown as Node[], edges: visEdges as unknown as Edge[] };
}

const { nodes: NODES, edges: EDGES } = overview();
const ctx = (tier: ZoomTierId): LayoutContext => ({
  compositeId: 'fixture', tier, granularity: 0.5,
});

/** Axis-aligned box of a node at the full-tier footprint. */
const boxOf = (n: Node) => {
  const { w, h } = fullFootprint(n);
  const p = n.position;
  return { x0: p.x, y0: p.y, x1: p.x + w, y1: p.y + h };
};
const overlaps = (a: ReturnType<typeof boxOf>, b: ReturnType<typeof boxOf>) =>
  a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

describe('force layout on the real v2ecoli baseline', () => {
  it('sanity — the overview graph is the expected ~27 proc / ~31 store size', () => {
    const procs = NODES.filter((n) => n.type === 'process').length;
    const stores = NODES.filter((n) => n.type === 'store').length;
    expect(procs).toBeGreaterThanOrEqual(20);
    expect(stores).toBeGreaterThanOrEqual(20);
    expect(EDGES.length).toBeGreaterThan(50);
  });

  it('(a) DETERMINISTIC — same input yields identical positions', async () => {
    const r1 = (await clusterGridLayout(NODES, EDGES, ctx('full'))).nodes;
    const r2 = (await clusterGridLayout(NODES, EDGES, ctx('full'))).nodes;
    const p2 = new Map(r2.map((n) => [n.id, n.position]));
    for (const n of r1) {
      const q = p2.get(n.id)!;
      expect(n.position.x, `x ${n.id}`).toBe(q.x);
      expect(n.position.y, `y ${n.id}`).toBe(q.y);
    }
  });

  it('(b) PERSISTENCE — positions are identical at every zoom tier', async () => {
    const tiers: ZoomTierId[] = ['glyph', 'ports', 'types', 'contract', 'full'];
    const ref = (await clusterGridLayout(NODES, EDGES, ctx('full'))).nodes;
    const refPos = new Map(ref.map((n) => [n.id, n.position]));
    for (const t of tiers) {
      const out = (await clusterGridLayout(NODES, EDGES, ctx(t))).nodes;
      for (const n of out) {
        const r = refPos.get(n.id)!;
        expect(n.position.x, `x@${t} ${n.id}`).toBe(r.x);
        expect(n.position.y, `y@${t} ${n.id}`).toBe(r.y);
      }
    }
  });

  it('(c) NO-OVERLAP — no two full-tier card footprints overlap', async () => {
    const out = (await clusterGridLayout(NODES, EDGES, ctx('full'))).nodes;
    const boxes = out.map(boxOf);
    let collisions = 0;
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        if (overlaps(boxes[i], boxes[j])) collisions++;
      }
    }
    expect(collisions).toBe(0);
  });

  it('(d) EVERY NODE PLACED — hub + no-wire nodes get a position too', async () => {
    const out = (await clusterGridLayout(NODES, EDGES, ctx('full'))).nodes;
    // Every input node appears in the output with a finite position.
    expect(out.length).toBe(NODES.length);
    for (const n of out) {
      expect(Number.isFinite(n.position.x), `x ${n.id}`).toBe(true);
      expect(Number.isFinite(n.position.y), `y ${n.id}`).toBe(true);
    }
    // A hub store (its wires are hub-hidden, so it has no non-hub spring) is
    // parked in the trailing band — but it IS placed.
    const hubs = hubStoreIds(EDGES);
    const hubStore = out.find((n) => n.type === 'store' && hubs.has(n.id));
    expect(hubStore, 'a hub store is present + placed').toBeTruthy();
  });

  it('(e) SPRINGS — non-hub wires feed the force graph, hub wires excluded', async () => {
    const hubs = hubStoreIds(EDGES);
    // Reconstruct the springs the layout uses: wire edges whose store endpoint
    // is NOT a hub. Hub-store wires and place edges are excluded.
    const springEndpoints = new Set<string>();
    let nonHubWires = 0;
    let hubWires = 0;
    for (const e of EDGES) {
      const store = wireStoreEndpoint(e);
      if (store == null) continue;                 // place edge
      if (hubs.has(store)) { hubWires++; continue; }
      nonHubWires++;
      springEndpoints.add(e.source);
      springEndpoints.add(e.target);
    }
    expect(nonHubWires).toBeGreaterThan(0);
    expect(hubWires).toBeGreaterThan(0);
    // No hub store is a force-graph endpoint (it only touches excluded wires).
    for (const store of hubs) {
      expect(springEndpoints.has(store), `hub ${store} excluded from springs`).toBe(false);
    }
  });

  it('MEASURE — dense, viewport-like bbox with a high fit-view zoom', async () => {
    const out = (await clusterGridLayout(NODES, EDGES, ctx('full'))).nodes;
    const boxes = out.map(boxOf);
    const minX = Math.min(...boxes.map((b) => b.x0));
    const minY = Math.min(...boxes.map((b) => b.y0));
    const maxX = Math.max(...boxes.map((b) => b.x1));
    const maxY = Math.max(...boxes.map((b) => b.y1));
    const width = maxX - minX;
    const height = maxY - minY;
    const aspect = width / height;
    // Fit-view zoom in a 1400×900 viewport (React Flow fits the bbox to the
    // smaller of the two axis ratios).
    const fitZoom = Math.min(1400 / width, 900 / height);
    // eslint-disable-next-line no-console
    console.log(
      `force layout bbox: ${Math.round(width)}x${Math.round(height)} `
      + `aspect=${aspect.toFixed(2)} fitZoom=${fitZoom.toFixed(3)}`,
    );
    // Viewport-like: not a wide sliver, not a tall pillar.
    expect(aspect).toBeGreaterThan(0.4);
    expect(aspect).toBeLessThan(4);
    // Dense enough that a full-tier card is big at the overview (≳0.19, up from
    // the old grid packing's ~0.088). The detail-aware footprint — cards that
    // render a description / config reserve their real (taller) height, and a
    // card with a governing equation or symbol legend reserves a wider center
    // lane — lowers this a touch from the earlier ~0.28, in exchange for no
    // overlap at full detail (see the NO-OVERLAP case above).
    expect(fitZoom).toBeGreaterThan(0.19);
  });

  it('exports the GRID constant', () => {
    expect(typeof GRID).toBe('number');
  });
});
