// Behavior test for the marker fallback (review finding C1) + rail/card
// consistency (I2). Extracts the self-contained marker block from walkthrough.js
// and evaluates it, so we test real behavior, not just source strings.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/static/walkthrough.js'), 'utf8');

let failed = 0;
function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); failed++; } }

// Pull the contiguous marker block (var _STATUS_META ... end of _tierPipsHtml).
const start = src.indexOf('var _STATUS_META = {');
const end = src.indexOf('function _humanizeStudyName');
assert(start > 0 && end > start, 'marker block located');
const block = src.slice(start, end);
const api = new Function(block +
  '\nreturn {_verdictMeta:_verdictMeta,_studyStatusMeta:_studyStatusMeta,_studyVerdict:_studyVerdict};')();

// C1: a study with NO authored verdict must NOT collapse to green "supported".
// Planned / Investigating / Blocked must keep their honest gate-based badge.
assert(api._verdictMeta({status: 'planned'}).label === 'Planned',
  'C1: unmigrated planned study keeps Planned (not forced supported)');
assert(api._verdictMeta({status: 'running'}).label === 'Investigating',
  'C1: unmigrated running study keeps Investigating');
assert(api._verdictMeta({effective_status: 'blocked'}).label === 'Blocked',
  'C1: unmigrated blocked study keeps Blocked');
assert(api._studyVerdict({status: 'planned'}) == null,
  'C1: _studyVerdict is null when no verdict authored (no forced supported)');

// Authored verdict still wins.
assert(api._verdictMeta({verdict: 'honest-negative', gate_status: 'failed'}).label === 'honest-negative',
  'authored verdict wins over gate_status');
// A failed-gate study with no authored verdict still reads refuted (unchanged).
assert(api._verdictMeta({gate_status: 'failed'}).label === 'Refuted',
  'unmigrated failed-gate study still reads Refuted');

// I2: the investigation-graph rail must use the same verdict-preferring meta as
// the card, so they can never show different badges for the same study.
assert(/investigation-graph card \+ legend[\s\S]{0,500}var _sm = _verdictMeta\(s\)/.test(src),
  'I2: graph rail uses _verdictMeta (agrees with the card)');

if (failed) { console.error(failed + ' assertion(s) failed'); process.exit(1); }
console.log('PASS test_investigation_graph_marker_fallback');
