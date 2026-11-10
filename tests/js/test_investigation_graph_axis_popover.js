// Behavior test: the clickable Progress/Verdict axis markers on the graph cards
// expose the full breakdown. In particular the Verdict popover must carry the
// study's findings in FULL (no truncation) — the whole point of making the marker
// clickable, and a direct guard against the regression where a finding could only
// be read truncated with an ellipsis. Extracts the self-contained ontology block
// from walkthrough.js and evaluates it, so we test real behavior, not source
// strings (mirrors test_investigation_graph_marker_fallback.js).
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/static/walkthrough.js'), 'utf8');

let failed = 0;
function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); failed++; } }

const start = src.indexOf('var _EXEC_META = {');
const end = src.indexOf('function _humanizeStudyName');
assert(start > 0 && end > start, 'ontology block located');
const block = src.slice(start, end);
// Shim the two helpers the block reaches for that live outside it. The top-level
// `if (typeof window !== 'undefined' ...)` popover-wiring is a no-op here (no
// window), which is exactly why that guard exists.
const api = new Function(
  'var _esc=function(s){return String(s);};\n' +
  'var _asFindings=function(v){return Array.isArray(v)?v:(v&&typeof v==="object"'
  + '?(Array.isArray(v.entries)?v.entries:Object.keys(v).map(function(k){return v[k];})):[]);};\n' +
  block +
  '\nreturn {_aigProgressRows:_aigProgressRows,_aigVerdictRows:_aigVerdictRows,'
  + '_aigAxisFindingsHtml:_aigAxisFindingsHtml};')();

// --- Progress rows: present lifecycle signals only, empties omitted ---
const pr = api._aigProgressRows({ status: 'completed', gate_status: 'passed', confidence: 'Provisional', simulation_status: '' });
const prKeys = pr.map(function (r) { return r[0]; });
assert(prKeys.indexOf('status') >= 0 && prKeys.indexOf('gate_status') >= 0 && prKeys.indexOf('confidence') >= 0,
  'progress rows include the present signals');
assert(prKeys.indexOf('simulation_status') < 0, 'an empty signal is omitted');

// --- Verdict rows: authored verdict + the three conclusion tracks + confidence ---
const vr = api._aigVerdictRows({
  verdict: 'qualified', confidence: 'Provisional',
  derived: { conclusion_verdicts: {
    regression_compatibility: { result: 'PASS' },
    biological_validation: { result: 'PASS' },
    explanatory_gain: { result: 'POSITIVE' } } }
});
const vmap = {}; vr.forEach(function (r) { vmap[r[0]] = r[1]; });
assert(vmap.verdict === 'qualified', 'authored verdict row present');
assert(vmap.regression === 'PASS' && vmap.biology === 'PASS' && vmap.insight === 'POSITIVE',
  'the three conclusion-verdict tracks are surfaced');
assert(vmap.confidence === 'Provisional', 'confidence row present');

// --- Findings rendered in FULL (the regression this feature fixes) ---
const long = 'A single real CPM cell coupled to a real spatio-flux acetate field through one '
  + 'coupling process, run deterministically for twenty ticks, reproduces the level-shift '
  + 'pattern at per-pixel resolution with full mass balance.';
const html = api._aigAxisFindingsHtml([{ statement: long, status: 'confirms' }, { summary: 'second finding body' }]);
assert(html.indexOf('…') < 0, 'no ellipsis — the finding is not truncated');
assert(html.indexOf('full mass balance.') >= 0, 'the END of a long finding is present (full text, not a teaser)');
assert(html.indexOf('finding 1') >= 0 && html.indexOf('finding 2') >= 0, 'every finding is listed');
assert(html.indexOf('second finding body') >= 0, 'a summary-only finding is rendered');
assert(api._aigAxisFindingsHtml([]) === '', 'no findings → empty block (nothing to show)');

if (failed) { console.error(failed + ' assertion(s) failed'); process.exit(1); }
console.log('PASS test_investigation_graph_axis_popover');
