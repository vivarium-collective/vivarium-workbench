// tests/js/test_methods_block.js — run with: node tests/js/test_methods_block.js
const assert = require('assert');
const { _methodsBlockHtml, _layoutOptsForBand, STATUS_COLOR } =
  require('../../vivarium_workbench/static/aig-graph.js');

// Band-2 (near) turns the block on; bands 0/1 leave it off.
assert.strictEqual(_layoutOptsForBand(2).methods, true, 'band-2 methods flag on');
assert.ok(!_layoutOptsForBand(1).methods, 'band-1 methods flag off');
assert.ok(!_layoutOptsForBand(0).methods, 'band-0 methods flag off');

// Nothing to show -> empty string (no empty block rendered).
assert.strictEqual(_methodsBlockHtml(undefined), '', 'undefined -> empty');
assert.strictEqual(_methodsBlockHtml({}), '', 'no data -> empty');
assert.strictEqual(
  _methodsBlockHtml({ methods: [], fidelity: '', runs: { count: 0 },
                      behavior_tests: { passed: 0, total: 0, tests: [] } }),
  '', 'all-absent -> empty');

// Full "real" composite card.
const real = _methodsBlockHtml({
  baseline_source: 'viva_genesis:vent_reactor.serpentinizing_vent_reactor',
  baseline_composite: 'viva_genesis.composites.vent_reactor.serpentinizing_vent_reactor',
  fidelity: 'real',
  methods: ['PHREEQC speciation (IPhreeqc)', 'FiPy FVM advection-diffusion-reaction'],
  runs: { count: 3, status: 'ran', n_steps: 120, emitter: 'ParquetEmitter' },
  behavior_tests: { passed: 2, total: 2, tests: [{ name: 'a', passed: true }, { name: 'b', passed: true }] },
});
assert(real.indexOf('Methods · Simulations') !== -1, 'header present');
assert(real.indexOf('aig-methods-block') !== -1, 'block class present');
assert(real.indexOf('aig-fidelity-real') !== -1, 'real fidelity chip');
assert(real.indexOf('real tool') !== -1, 'real chip label');
assert(real.indexOf('PHREEQC speciation (IPhreeqc) · FiPy FVM advection-diffusion-reaction') !== -1,
  'methods joined by middot');
assert(real.indexOf('3 runs · ran · 120 steps') !== -1, 'sims line assembled');
assert(real.indexOf('2/2 passed') !== -1, 'tests count');
assert(real.indexOf(STATUS_COLOR.accepted) !== -1, 'all-pass uses the green/teal status colour');

// Reproduction + partial pass + missing sim pieces (omit, never show blanks).
const repro = _methodsBlockHtml({
  baseline_composite: 'viva_genesis.composites.proto_energetics.proto_energetics',
  fidelity: 'reproduction',
  methods: ['coarse-grained energy-currency bookkeeping'],
  runs: { count: 1 },  // no status / n_steps
  behavior_tests: { passed: 1, total: 3, tests: [{ name: 'x', passed: true }, { name: 'y', passed: false }, { name: 'z', passed: false }] },
});
assert(repro.indexOf('aig-fidelity-repro') !== -1, 'reproduction chip');
assert(repro.indexOf('reproduction') !== -1, 'reproduction label');
// Model falls back to the trailing id segment when there's no baseline_source.
assert(repro.indexOf('proto_energetics') !== -1, 'model name from id tail');
assert(repro.indexOf('1 run') !== -1, 'singular run');
assert(repro.indexOf(' steps') === -1, 'n_steps piece omitted when missing');
assert(repro.indexOf('1/3 passed') !== -1, 'partial pass count');
assert(repro.indexOf(STATUS_COLOR.partial) !== -1, 'partial pass uses amber status colour');

// A single method, single run, no tests, no fidelity -> Model row still shows
// (name only), Methods + Sims show, Tests omitted.
const minimal = _methodsBlockHtml({
  baseline_source: 'pkg:demo',
  methods: ['one method'],
  runs: { count: 2, status: 'ran' },
  behavior_tests: { passed: 0, total: 0, tests: [] },
});
assert(minimal.indexOf('pkg:demo') !== -1, 'model shown without a chip');
assert(minimal.indexOf('aig-fidelity-chip') === -1, 'no chip when fidelity undeclared');
assert(minimal.indexOf('2 runs · ran') !== -1, 'sims w/o n_steps');
assert(minimal.indexOf('passed') === -1, 'tests row omitted when no tests');

console.log('ok test_methods_block');
