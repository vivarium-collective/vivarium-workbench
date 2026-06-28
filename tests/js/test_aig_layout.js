// tests/js/test_aig_layout.js  — run with: node tests/js/test_aig_layout.js
const assert = require('assert');
const { _aigLayout } = require('../../vivarium_dashboard/static/aig-graph.js');

const graph = {
  investigation: 'inv',
  studies: [
    { id: 'study/s1', slug: 's1', type: 'study', label: 'S1', status: 'complete' },
    { id: 'study/s2', slug: 's2', type: 'study', label: 'S2', status: 'planned' },
  ],
  study_edges: [{ source: 'study/s1', target: 'study/s2', rel: 'prerequisite' }],
  chains: {
    s1: { nodes: [], edges: [], violations: [] },
    s2: {
      nodes: [
        { id: 'finding/f1', type: 'finding', label: 'F', lifecycle_state: 'asserted' },
        { id: 'evidence/e1', type: 'evidence', label: 'E', lifecycle_state: 'accepted' },
      ],
      edges: [
        { source: 'study/s2', target: 'finding/f1', rel: 'contains' },
        { source: 'evidence/e1', target: 'finding/f1', rel: 'cites' },
      ],
      violations: [{ node_id: 'evidence/e1', invariant: 'evidence->hypothesis', message: 'x' }],
    },
  },
};

const out = _aigLayout(graph);
const s1 = out.nodes.find(n => n.id === 'study/s1');
const s2 = out.nodes.find(n => n.id === 'study/s2');
assert(s1 && s2, 'both studies positioned');
assert(s2.y > s1.y, 's2 (depth 1) below s1 (depth 0)');
const e1 = out.nodes.find(n => n.id === 'evidence/e1');
assert(e1 && typeof e1.x === 'number' && typeof e1.y === 'number', 'evidence positioned');
const f1 = out.nodes.find(n => n.id === 'finding/f1');
assert(f1 && f1.y < e1.y, 'finding (type 0) sorts above evidence (type 1) in the chain cluster');
assert(out.edges.length === 3, 'study edge + 2 chain edges resolved to coords');
out.edges.forEach(e => ['x1', 'y1', 'x2', 'y2'].forEach(
  k => assert(typeof e[k] === 'number', 'edge coord ' + k)));
assert(out.violations.length === 1 && out.violations[0].study === 's2',
  'violation surfaced + tagged with study');

// dangling edge dropped
const dangling = _aigLayout({
  investigation: 'i',
  studies: [{ id: 'study/a', slug: 'a', type: 'study', label: 'A', status: 'planned' }],
  study_edges: [{ source: 'study/ghost', target: 'study/a', rel: 'prerequisite' }],
  chains: { a: { nodes: [], edges: [], violations: [] } },
});
assert(dangling.edges.length === 0, 'edge to absent node dropped');

// graceful: empty chains -> only study nodes
const empty = _aigLayout({
  investigation: 'i',
  studies: [{ id: 'study/a', slug: 'a', type: 'study', label: 'A', status: 'planned' }],
  study_edges: [], chains: { a: { nodes: [], edges: [], violations: [] } },
});
assert(empty.nodes.length === 1 && empty.edges.length === 0, 'graceful empty == study DAG');

console.log('ok');
