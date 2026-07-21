// tests/js/test_layout_opts_for_band.js — run with: node tests/js/test_layout_opts_for_band.js
const assert = require('assert');
const { _layoutOptsForBand } = require('../../vivarium_workbench/static/aig-graph.js');

const far = _layoutOptsForBand(0), mid = _layoutOptsForBand(1), near = _layoutOptsForBand(2);
// far: title+badge only
assert.strictEqual(far.cls, 'aig-zoom-far');
assert.strictEqual(far.asks, false); assert.strictEqual(far.finds, false);
assert.strictEqual(far.chain, false); assert.strictEqual(far.followups, false);
// mid: + asks + finds, no chain
assert.strictEqual(mid.cls, 'aig-zoom-mid');
assert.ok(mid.asks && mid.finds); assert.strictEqual(mid.chain, false);
// near: everything
assert.strictEqual(near.cls, 'aig-zoom-near');
assert.ok(near.asks && near.finds && near.chain && near.followups);
// card width grows far < mid < near
assert.ok(far.cardW < mid.cardW && mid.cardW < near.cardW);
// clamp out-of-range to 0..2
assert.strictEqual(_layoutOptsForBand(-5).cls, 'aig-zoom-far');
assert.strictEqual(_layoutOptsForBand(9).cls, 'aig-zoom-near');
console.log('ok test_layout_opts_for_band');
