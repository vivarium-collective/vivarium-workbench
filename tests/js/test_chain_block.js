// tests/js/test_chain_block.js — run with: node tests/js/test_chain_block.js
const assert = require('assert');
const { _chainBlockHtml } = require('../../vivarium_dashboard/static/aig-graph.js');

// graceful: no chain -> empty string (card identical to today)
assert.strictEqual(_chainBlockHtml(undefined), '', 'undefined chain -> empty');
assert.strictEqual(_chainBlockHtml({ nodes: [], edges: [], violations: [] }), '', 'empty chain -> empty');

// full chain -> lists all four node types in finding->evidence->decision->conclusion order
const chain = {
  nodes: [
    { id: 'conclusion/c1', type: 'conclusion', label: 'C', lifecycle_state: 'published' },
    { id: 'finding/f1', type: 'finding', label: 'F', lifecycle_state: 'asserted' },
    { id: 'evidence/e1', type: 'evidence', label: 'E', lifecycle_state: 'accepted' },
    { id: 'decision/d1', type: 'decision', label: 'D', lifecycle_state: 'recorded' },
  ],
  edges: [], violations: [],
};
const html = _chainBlockHtml(chain);
assert(html.indexOf('Evidence chain') !== -1, 'has header');
['finding', 'evidence', 'decision', 'conclusion'].forEach(function (t) {
  assert(html.indexOf(t) !== -1, 'lists ' + t);
});
assert(html.indexOf('finding') < html.indexOf('evidence'), 'finding before evidence');
assert(html.indexOf('evidence') < html.indexOf('decision'), 'evidence before decision');
assert(html.indexOf('decision') < html.indexOf('conclusion'), 'decision before conclusion');
assert(html.indexOf('accepted') !== -1 && html.indexOf('published') !== -1, 'lifecycle badges rendered');
assert(html.indexOf('chain gap') === -1, 'no violation marker when clean');

// violations -> marker present
const bad = {
  nodes: [{ id: 'evidence/e1', type: 'evidence', label: 'E', lifecycle_state: 'proposed' }],
  edges: [], violations: [{ node_id: 'evidence/e1', invariant: 'x', message: 'y' }],
};
assert(_chainBlockHtml(bad).indexOf('chain gap') !== -1, 'violation marker present');

// derived hint
const derivedChain = {
  nodes: [{ id: 'finding/derived-s1-cv0', type: 'finding', label: 'F', lifecycle_state: 'asserted' }],
  edges: [], violations: [], derived: true,
};
assert(_chainBlockHtml(derivedChain).indexOf('derived') !== -1, 'derived hint shown when derived');

const authoredChain = {
  nodes: [{ id: 'finding/f1', type: 'finding', label: 'F', lifecycle_state: 'asserted' }],
  edges: [], violations: [], derived: false,
};
assert(_chainBlockHtml(authoredChain).indexOf('· derived') === -1, 'no derived hint when authored');

console.log('ok');
