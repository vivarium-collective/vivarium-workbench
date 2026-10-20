// flow-right ("Flow") layout — the left→right dataflow DAG.
//
// Regression guard for the batch-compare-biomodels layout fix: rank by DATAFLOW
// only (store→process→store), drop the place-depth re-banking, and tuck
// containment-only sub-stores under their place parent. Before the fix the
// containment edges (`state → everything`, nested result leaves) distorted the
// ELK ranking and the depth re-banking stacked all flat stores onto one band.
import { describe, it, expect } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import { stateToReactFlow, defaultCollapsedIds } from '../convert';
import { retargetEdgesToVisible } from '../panels/filterHidden';
import { flowElkRightMode } from '../layouts/flow';
import type { LayoutContext, ZoomTierId } from '../layouts/types';
import fixture from './fixtures/batch-compare-biomodels.json';

const ctx = (tier: ZoomTierId): LayoutContext => ({
  compositeId: 'batch-compare-biomodels', tier, granularity: 0.5,
});

function visibleGraph(): { nodes: Node[]; edges: Edge[] } {
  const raw = stateToReactFlow(fixture);
  const nodes = raw.nodes as unknown as Node[];
  const edges = raw.edges as unknown as Edge[];
  const collapsed = defaultCollapsedIds(fixture);
  const isHidden = (n: Node) => {
    const path: string[] = (n.data as { path?: string[] })?.path ?? [];
    for (let i = 1; i < path.length; i++) {
      if (collapsed.has(path.slice(0, i).join('.'))) return true;
    }
    return false;
  };
  const visibleNodes = nodes
    .filter((n) => !isHidden(n))
    .map((n) => (collapsed.has(n.id) ? { ...n, data: { ...n.data, isCollapsed: true } } : n));
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  return { nodes: visibleNodes, edges: retargetEdgesToVisible(edges, visibleIds) };
}

/** Find a laid-out node by its label (stable across id churn). */
function byLabel(nodes: Node[], label: string): Node | undefined {
  return nodes.find((n) => (n.data as { label?: string })?.label === label);
}

describe('flow-right ("Flow") layout — batch-compare-biomodels', () => {
  it('reads left→right: dataflow sources sit left of the sinks they feed', async () => {
    const { nodes, edges } = visibleGraph();
    const { nodes: laid } = await flowElkRightMode.run(nodes, edges, ctx('full'));

    const x = (label: string) => byLabel(laid, label)?.position.x ?? NaN;
    // Root input stores on the left; terminal viz on the right.
    const models = x('models');
    const runnerX = Math.min(x('runner_simbio'), x('runner_tellurium'), x('runner_copasi'));
    const results = x('results');
    const compare = x('compare');
    const comparisons = x('comparisons');
    const overlay = x('batch_overlay_viz');
    const vizHtml = x('viz_html');

    // The canonical dataflow spine: models → runners → results → compare →
    // comparisons → batch_overlay_viz → viz_html, each strictly to the right.
    expect(models).toBeLessThan(runnerX);
    expect(runnerX).toBeLessThan(results);
    expect(results).toBeLessThan(compare);
    expect(compare).toBeLessThan(comparisons);
    expect(comparisons).toBeLessThan(overlay);
    expect(overlay).toBeLessThan(vizHtml);
  });

  it('left-aligns the parallel load chain near the roots (not shoved mid-graph)', async () => {
    const { nodes, edges } = visibleGraph();
    const { nodes: laid } = await flowElkRightMode.run(nodes, edges, ctx('full'));
    const xs = laid.map((n) => n.position.x);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const span = maxX - minX;
    const biomodelId = byLabel(laid, 'biomodel_id_BIOMD0000000001')!.position.x;
    // The biomodel_id root (start of the load chain) must sit in the left
    // quarter of the graph, not be offset into the middle as a stray component.
    expect(biomodelId - minX).toBeLessThan(span * 0.25);
  });

  it('tucks containment-only sub-stores beside their place parent', async () => {
    const { nodes, edges } = visibleGraph();
    const { nodes: laid } = await flowElkRightMode.run(nodes, edges, ctx('full'));
    const diag = byLabel(laid, 'diagnostics')!;
    // meta/provenance/runs are nested under diagnostics (place edges only); each
    // should share diagnostics' column (±140px) and sit below it.
    for (const label of ['meta', 'provenance', 'runs']) {
      const n = byLabel(laid, label)!;
      expect(Math.abs(n.position.x - diag.position.x)).toBeLessThan(140);
      expect(n.position.y).toBeGreaterThan(diag.position.y);
    }
  });

  it('places every node without a pairwise bounding-box overlap', async () => {
    const { nodes, edges } = visibleGraph();
    const { nodes: laid } = await flowElkRightMode.run(nodes, edges, ctx('full'));
    type Box = { id: string; x: number; y: number; w: number; h: number };
    const boxes: Box[] = laid.map((n) => ({
      id: n.id, x: n.position.x, y: n.position.y,
      w: n.type === 'process' ? 140 : 80, h: n.type === 'process' ? 60 : 80,
    }));
    const EPS = 0.5;
    const overlaps: string[] = [];
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j];
        const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        if (ox > EPS && oy > EPS) overlaps.push(`${a.id} ∩ ${b.id}`);
      }
    }
    expect(overlaps).toEqual([]);
  });
});
