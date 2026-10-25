// Regression guard for the adaptive layout policy, over the REAL GDM composite
// state (viva_island_biogeography.composites.gdm.general_dynamic_model, 3 islands
// x 60 species), captured @ vivarium-workbench 40bd4c38. This is the composite
// that rendered as an unreadable 41015x1052 flat band under the old defaults; it
// encodes the whole point of the change: fresh open -> Grid + collapse ->
// compact, top-down, no flat band.
import { describe, it, expect } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import { stateToReactFlow, overviewGraph } from '../convert';
import { collapseRedundantStores } from '../collapseRedundant';
import { gridMode, treeMode } from '../layouts/treeFlow';
import { chooseInitialView, shapeSignals } from '../layoutPolicy';
import type { LayoutContext } from '../layouts/types';
import fixture from './fixtures/gdm-general-dynamic-model.json';

const STATE = (fixture as { state: unknown }).state;
const ctx: LayoutContext = { compositeId: 'gdm', tier: 'full', granularity: 0.5 };

function bbox(nodes: Node[]) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const n of nodes) {
    x0 = Math.min(x0, n.position.x); y0 = Math.min(y0, n.position.y);
    x1 = Math.max(x1, n.position.x); y1 = Math.max(y1, n.position.y);
  }
  return { w: x1 - x0, h: y1 - y0, aspect: (x1 - x0) / Math.max(1, y1 - y0) };
}
const depth = (n: Node) => ((n as { data?: { path?: string[] } }).data?.path?.length ?? 1);
const meanYByDepth = (nodes: Node[]) => {
  const acc: Record<number, number[]> = {};
  for (const n of nodes) (acc[depth(n)] ??= []).push(n.position.y);
  const out: Record<number, number> = {};
  for (const [d, ys] of Object.entries(acc)) out[+d] = ys.reduce((a, b) => a + b, 0) / ys.length;
  return out;
};

describe('GDM adaptive layout (real composite)', () => {
  it('shape is large/wide as measured (~220 visible nodes, fan 60)', () => {
    const { nNodes, maxFan } = shapeSignals(STATE);
    expect(nNodes).toBeGreaterThan(150);
    expect(maxFan).toBeGreaterThanOrEqual(60);
  });

  it('policy picks Grid + collapse-repeats on', () => {
    expect(chooseInitialView(STATE)).toEqual({ modeId: 'tree-grid', collapseRedundant: true });
  });

  it('collapse-repeats folds the 3 redundant island subtrees (220 -> ~82)', () => {
    const ov = overviewGraph(STATE);
    expect(ov.nodes.length).toBeGreaterThan(150);
    const col = collapseRedundantStores(ov.nodes as never, ov.edges as never);
    expect(col.nodes.length).toBeLessThan(100);
    const reps = (col.nodes as { data?: { _collapsedCount?: number } }[])
      .filter((n) => n.data?._collapsedCount);
    expect(reps.length).toBeGreaterThan(0);
  });

  it('OLD default (Tree, no collapse) is the flat band this change fixes', async () => {
    const ov = overviewGraph(STATE);
    const laid = await treeMode.run(ov.nodes as unknown as Node[], ov.edges as unknown as Edge[], ctx);
    // the regression we are removing: an extreme aspect ratio
    expect(bbox(laid.nodes).aspect).toBeGreaterThan(20);
  });

  it('NEW default (Grid + collapse) is compact AND reads top-down', async () => {
    const ov = overviewGraph(STATE);
    const col = collapseRedundantStores(ov.nodes as never, ov.edges as never);
    const laid = await gridMode.run(col.nodes as unknown as Node[], col.edges as unknown as Edge[], ctx);
    const bb = bbox(laid.nodes);
    // compact: no flat band
    expect(bb.aspect).toBeLessThan(4);
    // top-down hierarchy: shallow depths sit above (smaller y) the deepest
    const my = meanYByDepth(laid.nodes);
    const depths = Object.keys(my).map(Number).sort((a, b) => a - b);
    const shallow = my[depths[0]];
    const deepest = my[depths[depths.length - 1]];
    expect(shallow).toBeLessThan(deepest);
  });

  it('sanity: raw graph has the 3 island processes', () => {
    const raw = stateToReactFlow(STATE);
    const procs = raw.nodes.filter((n) => (n as { type?: string }).type === 'process');
    const islands = procs.filter((n) =>
      String((n as { data?: { label?: string } }).data?.label ?? '').startsWith('island_'));
    expect(islands.length).toBe(3);
  });
});
