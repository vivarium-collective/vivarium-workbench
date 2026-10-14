// tests/js/test_dag_plan_layer.js — run with: node tests/js/test_dag_plan_layer.js
// The investigation-graph node card gains, at full magnification (_opts.plan),
// a "Plan" layer (declared contract + mechanisms + models/methods + success
// criteria) and shows the FULL question (_opts.asksFull). _renderInvestigationDag
// and _dagPlanHtml live inside walkthrough.js's IIFE, so assert the wiring in
// source (same approach as test_process_card_contract.js).
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const w = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/static/walkthrough.js'), 'utf8');

// The plan-layer helper exists and is wired, gated on _opts.plan.
assert.ok(w.includes('function _dagPlanHtml'), '_dagPlanHtml helper defined');
assert.ok(w.includes('_opts.plan ? _dagPlanHtml(s)'), 'plan layer wired, gated on _opts.plan');
// It renders each planning sub-block from the node data.
assert.ok(w.includes('s.proposed_mechanisms'), 'plan layer reads proposed_mechanisms');
assert.ok(w.includes('s.proposed_models_methods'), 'plan layer reads proposed_models_methods');
assert.ok(w.includes('s.behavior_test_names'), 'plan layer reads behavior_test_names');
assert.ok(w.includes('s.contract') && w.includes('Contract:'), 'plan layer summarizes the declared contract');
assert.ok(/Mechanisms|Success criteria/.test(w), 'plan layer labels present');
// Full-magnification Asks: full question when _opts.asksFull, else first sentence.
assert.ok(w.includes('_opts.asksFull ? _qFull'), 'asks shows full question at full magnification');
assert.ok(w.includes('_clamp(_opts.asksFull ? 10 : 2)'), 'asks clamp relaxes at full magnification');

console.log('ok test_dag_plan_layer');
