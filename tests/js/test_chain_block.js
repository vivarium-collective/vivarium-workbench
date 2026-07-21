// tests/js/test_chain_block.js — run with: node tests/js/test_chain_block.js
const assert = require('assert');
const { _chainBlockHtml, _groupClaims } = require('../../vivarium_workbench/static/aig-graph.js');

// graceful: no chain -> '' (card identical to today)
assert.strictEqual(_chainBlockHtml(undefined), '', 'undefined chain -> empty');
assert.strictEqual(_chainBlockHtml({ nodes: [], edges: [], violations: [] }), '', 'empty chain -> empty');
assert.deepStrictEqual(_groupClaims({ nodes: [], edges: [] }), [], 'empty -> no claims');

function fullClaim(cv, statement, opts) {
  opts = opts || {};
  const f = 'finding/d-' + cv, e = 'evidence/d-' + cv, d = 'decision/d-' + cv, c = 'conclusion/d-' + cv;
  const nodes = [
    { id: f, type: 'finding', lifecycle_state: 'asserted', statement: statement, source: 'derived from study.yaml conclusion_verdicts[' + cv + ']' },
    { id: e, type: 'evidence', lifecycle_state: opts.evState || 'accepted', statement: 'the basis' },
  ];
  const edges = [
    { source: 'study/s', target: f, rel: 'contains' },
    { source: e, target: f, rel: 'cites' },
  ];
  if (opts.decision) {
    nodes.push({ id: d, type: 'decision', lifecycle_state: 'recorded', outcome: opts.decision });
    edges.push({ source: d, target: e, rel: 'decides' });
  }
  if (opts.conclusion) {
    nodes.push({ id: c, type: 'conclusion', lifecycle_state: 'published', statement: statement });
    edges.push({ source: c, target: e, rel: 'concludes' });
    edges.push({ source: c, target: d, rel: 'via' });
  }
  return { nodes, edges };
}

// one published claim -> one claim, all stages, status published, claim text present
const pub = fullClaim('cv0', 'basal elongation dominates', { evState: 'accepted', decision: 'accept', conclusion: true });
pub.derived = true; pub.violations = [];
const g = _groupClaims(pub);
assert(g.length === 1, 'one component');
assert(g[0].claimText === 'basal elongation dominates', 'claim text from finding statement');
assert(g[0].status === 'published', 'status published');
assert(g[0].stages.finding && g[0].stages.evidence && g[0].stages.decision && g[0].stages.conclusion, 'all stages');
assert(g[0].source.indexOf('conclusion_verdicts[cv0]') !== -1, 'source carried');

const htmlPub = _chainBlockHtml(pub);
assert(htmlPub.indexOf('basal elongation dominates') !== -1, 'renders the claim text');
assert(htmlPub.indexOf('published') !== -1, 'renders status word');
assert(htmlPub.indexOf('· derived') !== -1, 'derived hint');
assert((htmlPub.match(/aig-claim-row/g) || []).length === 1, 'one clickable claim row');
assert(htmlPub.indexOf('data-claim-index="0"') !== -1, 'row carries index');

// pending: finding+evidence(proposed), no decision/conclusion
const pend = fullClaim('cv0', 'needs more samples', { evState: 'proposed' });
pend.violations = [];
const gp = _groupClaims(pend);
assert(gp.length === 1 && gp[0].status === 'pending', 'pending status');
assert(gp[0].stages.finding && gp[0].stages.evidence && !gp[0].stages.decision, 'two stages');

// refuted
const ref = fullClaim('cv0', 'claim X', { evState: 'rejected', decision: 'reject' });
ref.violations = [];
assert(_groupClaims(ref)[0].status === 'refuted', 'refuted status');

// two claims -> two components, two rows
const a = fullClaim('cv0', 'claim A', { decision: 'accept', conclusion: true });
const b = fullClaim('cv1', 'claim B', { decision: 'accept', conclusion: true });
const two = { nodes: a.nodes.concat(b.nodes), edges: a.edges.concat(b.edges), derived: true, violations: [] };
assert(_groupClaims(two).length === 2, 'two claims');
assert(_chainBlockHtml(two).indexOf('(2 claims)') !== -1, 'count shown');
assert((_chainBlockHtml(two).match(/aig-claim-row/g) || []).length === 2, 'two rows');

// singleton findings.entries finding (no intra edges)
const single = { nodes: [{ id: 'finding/d-fe0', type: 'finding', lifecycle_state: 'asserted', statement: 'a gap' }],
                edges: [{ source: 'study/s', target: 'finding/d-fe0', rel: 'contains' }], violations: [] };
const gs = _groupClaims(single);
assert(gs.length === 1 && gs[0].claimText === 'a gap' && gs[0].status === 'pending', 'singleton finding claim');

console.log('ok');
