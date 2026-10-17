// Source-contract test for the two-axis investigation-graph ontology:
// EXECUTION (execution_status) × VERDICT, plus the four edge relations. Asserts
// the renderer source carries the new model and no longer carries the retired
// single-badge / 5-type edge vocabulary.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/static/walkthrough.js'), 'utf8');

let failed = 0;
function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); failed++; } }

// --- two-axis model exists ---
assert(src.includes('var _EXEC_META'), '_EXEC_META defined (execution axis)');
assert(src.includes('var _VERDICT_META'), '_VERDICT_META defined (verdict axis)');
assert(src.includes('function _execState'), '_execState defined');
assert(src.includes('function _execMeta'), '_execMeta defined');
assert(src.includes('function _studyVerdict'), '_studyVerdict defined');
assert(src.includes('function _verdictMeta'), '_verdictMeta defined');
assert(src.includes('function _studyTier'), '_studyTier defined');
assert(src.includes('_tierPipsHtml'), '_tierPipsHtml defined');
assert(src.includes('function _aigPill'), '_aigPill defined (pill renderer)');

// execution-status vocabulary
['proposed', 'planned', 'running', 'completed', 'failed'].forEach(v =>
  assert(new RegExp('\\b' + v + ':').test(src), 'execution status ' + v + ' present'));
// verdict vocabulary — the four canonical values
['supported', 'qualified', 'inconclusive', 'refuted'].forEach(v =>
  assert(src.includes("'" + v + "'"), 'verdict ' + v + ' present'));
// retired verdicts fold into the four via the alias map (not canonical keys)
assert(src.includes('_VERDICT_ALIASES'), 'legacy verdicts folded via _VERDICT_ALIASES');
const vmBlock = src.slice(src.indexOf('var _VERDICT_META'), src.indexOf('var _VERDICT_ALIASES'));
assert(!/honest-negative/.test(vmBlock), 'honest-negative is NOT a canonical verdict');
assert(!/'mixed'/.test(vmBlock), 'mixed is NOT a canonical verdict');
['conceptual_demonstration', 'mechanistic_prediction', 'calibrated'].forEach(t =>
  assert(src.includes(t), 'tier ' + t + ' present'));
assert(/s\.execution_status/.test(src), 'reads s.execution_status');
assert(/s\.verdict/.test(src), 'reads s.verdict');
assert(/s\.evidence_tier/.test(src), 'reads s.evidence_tier');

// --- node card: both axes rendered as prominent pills ---
assert(src.includes('var execMeta = _execMeta(s)'), 'card computes execMeta');
assert(src.includes('_verdictMeta(s)'), 'card computes verdictMeta');
assert(src.includes('aig-progress-pill'), 'Progress pill present on card');
assert(src.includes('aig-verdict-pill'), 'Verdict pill present on card');

// --- edges: four canonical relations; directed vs undirected ---
['builds-on', 'tests', 'motivates', 'alternative-to'].forEach(r =>
  assert(src.includes("'" + r + "'"), 'edge relation ' + r + ' present'));
const relBlock = src.slice(src.indexOf('var _DAG_REL_STYLE'), src.indexOf('var _DAG_REL_ALIASES'));
assert(!/'precedes'/.test(relBlock), 'precedes removed from style map (aliased)');
assert(!/'leads-to'/.test(relBlock), 'leads-to removed from style map (aliased)');
assert(!/'model-input'/.test(relBlock), 'model-input removed from style map (aliased)');
assert(/'alternative-to':[\s\S]*?arrow: false/.test(relBlock), 'alternative-to is undirected (arrow:false)');
assert(src.includes('_DAG_REL_ALIASES'), 'legacy edge relations folded via _DAG_REL_ALIASES');
assert(src.includes('if (st.arrow !== false)'), 'edge draw honors arrow:false (no marker-end)');

// --- legend: generated from the objects, Progress + Verdict + Evidence rows ---
// Now lives in the "?" info-chip popover (_buildDagLegendHtml), not an
// always-visible strip; the always-visible #investigation-dag-legend is gone.
assert(src.includes('function _buildDagLegendHtml'), '_buildDagLegendHtml defined');
assert(src.includes('function _wireDagLegendPopover'), '_wireDagLegendPopover defined');
assert(!src.includes("id = 'investigation-dag-legend'"), 'always-visible legend strip removed');
const legendFn = src.slice(src.indexOf('function _buildDagLegendHtml'),
                          src.indexOf('function _wireDagLegendPopover'));
assert(/row\('Progress'/.test(legendFn), 'legend Progress row');
assert(/row\('Verdict'/.test(legendFn), 'legend Verdict row');
assert(/row\('Evidence'/.test(legendFn), 'legend Evidence row');
assert(/row\('Edges'/.test(legendFn), 'legend Edges row');
assert(!/_lg\('#16a34a', *'✓', *'Accepted'\)/.test(src), 'old hardcoded Accepted legend removed');

if (failed) { console.error(failed + ' assertion(s) failed'); process.exit(1); }
console.log('PASS test_investigation_graph_markers');
