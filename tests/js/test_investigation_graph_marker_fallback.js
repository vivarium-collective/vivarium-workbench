// Behavior test for the two-axis ontology derivation + rail/card consistency.
// Extracts the self-contained ontology block from walkthrough.js and evaluates
// it, so we test real behavior, not just source strings.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/static/walkthrough.js'), 'utf8');

let failed = 0;
function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); failed++; } }

// Pull the contiguous ontology block (var _EXEC_META ... end of _studyStatusMeta).
const start = src.indexOf('var _EXEC_META = {');
const end = src.indexOf('function _humanizeStudyName');
assert(start > 0 && end > start, 'ontology block located');
const block = src.slice(start, end);
const api = new Function('var _esc=function(s){return String(s);};\n' + block +
  '\nreturn {_execMeta:_execMeta,_execState:_execState,_verdictMeta:_verdictMeta,' +
  '_studyVerdict:_studyVerdict,_studyStatusMeta:_studyStatusMeta};')();

// --- EXECUTION axis: derived from legacy fields when execution_status absent ---
assert(api._execState({status: 'planned'}) === 'planned', 'planned → planned');
assert(api._execState({status: 'running'}) === 'running', 'running → running');
assert(api._execState({gate_status: 'passed'}) === 'completed', 'passed gate → completed');
assert(api._execState({simulation_status: 'ran', evaluation_status: 'evaluated'}) === 'completed',
  'ran + evaluated → completed');
assert(api._execState({simulation_status: 'failed'}) === 'failed', 'sim failed → failed (execution)');
// explicit execution_status always wins
assert(api._execState({execution_status: 'proposed', status: 'running'}) === 'proposed',
  'authored execution_status wins');
assert(api._execMeta({status: 'planned'}).label === 'Planned', 'execMeta label Planned');
assert(api._execMeta({gate_status: 'passed'}).label === 'Completed', 'execMeta label Completed');

// --- VERDICT axis: authored-only, legacy values folded ---
assert(api._studyVerdict({status: 'planned'}) == null,
  'no verdict when none authored (pending, not forced supported)');
assert(api._studyVerdict({verdict: 'supported'}) === 'supported', 'supported honored');
assert(api._studyVerdict({verdict: 'honest-negative'}) === 'refuted',
  'legacy honest-negative folds to refuted');
assert(api._studyVerdict({verdict: 'mixed'}) === 'inconclusive',
  'legacy mixed folds to inconclusive');
assert(api._verdictMeta({verdict: 'supported'}).label === 'Supported', 'verdictMeta label Supported');
assert(api._verdictMeta({status: 'planned'}) == null, 'verdictMeta null when no verdict');

// A negative result is completed + refuted, NOT a failed execution.
assert(api._execState({gate_status: 'failed'}) === 'completed',
  'failed gate = completed execution (the verdict carries the negative, not the execution)');

// --- rail/card consistency: rail uses verdict-or-execution, never null ---
assert(/var _sm = _verdictMeta\(s\) \|\| _execMeta\(s\)/.test(src),
  'rail uses _verdictMeta || _execMeta (agrees with the card, never null)');

if (failed) { console.error(failed + ' assertion(s) failed'); process.exit(1); }
console.log('PASS test_investigation_graph_marker_fallback');
