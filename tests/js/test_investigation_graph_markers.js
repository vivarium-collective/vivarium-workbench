// Source-contract test for the two-axis investigation-graph markers.
// Same style as tests/js/test_composite_card_contract.js: assert the renderer
// source carries the new verdict x evidence-tier model + the right-sized edge
// vocabulary, and no longer carries the retired binary badge / edge types.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/static/walkthrough.js'), 'utf8');

let failed = 0;
function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); failed++; } }

// --- node marker: two-axis model exists ---
assert(src.includes('_VERDICT_META'), '_VERDICT_META defined');
assert(src.includes('function _studyVerdict'), '_studyVerdict defined');
assert(src.includes('function _studyTier'), '_studyTier defined');
assert(src.includes('_tierPipsHtml'), '_tierPipsHtml defined');
['supported', 'qualified', 'honest-negative', 'refuted', 'mixed'].forEach(v =>
  assert(src.includes("'" + v + "'"), 'verdict ' + v + ' present'));
['conceptual_demonstration', 'mechanistic_prediction', 'calibrated'].forEach(t =>
  assert(src.includes(t), 'tier ' + t + ' present'));
assert(/s\.verdict/.test(src), 'reads s.verdict');
assert(/s\.evidence_tier/.test(src), 'reads s.evidence_tier');
assert(src.includes('var ss = _verdictMeta(s)'), 'node marker uses _verdictMeta');

// --- edges: right-sized vocabulary, authored relation read ---
assert(src.includes("'alternative-to'"), 'alternative-to style present');
assert(src.includes("'precedes'"), 'precedes style present');
assert(!/_DAG_REL_STYLE[\s\S]*?'calibrates-threshold'[\s\S]*?\};/.test(src),
  'calibrates-threshold removed from style map');
assert(!/_DAG_REL_STYLE[\s\S]*?'evidence':[\s\S]*?\};/.test(src),
  "'evidence' removed from style map");
assert(src.includes('e.relation || (e.artifact'), 'edge build prefers authored e.relation');

// --- legend: generated from the objects, three rows ---
assert(/>Verdict:</.test(src), 'legend Verdict row');
assert(/>Evidence:</.test(src), 'legend Evidence row');
assert(!/_lg\('#16a34a', *'✓', *'Accepted'\)/.test(src), 'old hardcoded Accepted legend removed');

if (failed) { console.error(failed + ' assertion(s) failed'); process.exit(1); }
console.log('PASS test_investigation_graph_markers');
