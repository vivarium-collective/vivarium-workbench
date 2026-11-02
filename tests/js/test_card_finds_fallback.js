// Source-contract test for the DAG study-card "Finds:" line.
//
// Two gaps this pins (both surfaced on the discovery-sbml-recovery graph):
//   1. The EXPANDED card (_aigCardDetailHtml) built "Finds:" only from the
//      structured findings[] array, so a COMPLETED study that carries its
//      one-line claim headline but no structured findings rendered
//      "pending evidence" when expanded — even though the collapsed preview
//      showed its claim fine. The expanded card must fall back to s.claim.
//   2. "pending evidence" is the wrong label for a PARKED study: it is a
//      deliberately-deferred future direction, not pending work. Both the
//      collapsed and expanded render sites must say "future direction" for
//      parked studies, gated on the parked execution state.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/static/walkthrough.js'), 'utf8');

let failed = 0;
function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); failed++; } }

// --- expanded card (_aigCardDetailHtml) ---
const detailFn = src.slice(
  src.indexOf('function _aigCardDetailHtml'),
  src.indexOf('window._aigCardDetailHtml'));
assert(detailFn.length > 0, '_aigCardDetailHtml located');
assert(/s\.claim/.test(detailFn),
  'expanded card falls back to s.claim when findings[] is empty');
assert(/_execState\(s\)\s*===\s*'parked'/.test(detailFn),
  'expanded card gates the future-direction label on the parked state');
assert(detailFn.includes('future direction'),
  'expanded card shows "future direction" for a parked study');
// the claim fallback must be reached BEFORE the pending-evidence placeholder
// markup (match the rendered <em>…</em>, not the phrase in the comment above it)
assert(detailFn.indexOf('s.claim') < detailFn.indexOf('pending evidence</em>'),
  'expanded card tries the claim before the pending-evidence placeholder');

// --- collapsed preview: parked studies must not read "pending evidence" ---
// The collapsed preview already used `claim`; this pins the parked branch so a
// parked study with no claim reads "future direction", not "pending evidence".
assert(/isParked\s*\?\s*'<em[^>]*>future direction/.test(src),
  'collapsed preview shows "future direction" for parked (gated on isParked)');

// --- parked predicted direction (#1564 builder surfaces s.hypothesis) ---
// A parked card with no claim shows its authored hypothesis (the predicted
// direction), trimmed to the first sentence; both render sites consume it.
const fnSrc = src.slice(
  src.indexOf('function _parkedDirection'),
  src.indexOf('window._parkedDirection'));
assert(fnSrc.length > 0, '_parkedDirection defined');
assert(/s\.hypothesis/.test(fnSrc), '_parkedDirection reads s.hypothesis');
const _parkedDirection = new Function('return (' + fnSrc + ')')();
assert(
  _parkedDirection({ hypothesis:
    'Removing the conserved-moiety rank deficiency lets the sparse fit isolate the true three reactions. A second clause that must be dropped.' })
  === 'Removing the conserved-moiety rank deficiency lets the sparse fit isolate the true three reactions.',
  '_parkedDirection returns only the first sentence');
assert(_parkedDirection({}) === null, '_parkedDirection returns null with no hypothesis');
assert(_parkedDirection({ hypothesis: '   ' }) === null, '_parkedDirection returns null on whitespace-only');
const long = _parkedDirection({ hypothesis: 'x'.repeat(400) });
assert(long.length <= 200 && long.endsWith('…'), '_parkedDirection caps an over-long single sentence with an ellipsis');

// both render sites consume the predicted direction for parked studies
assert(/_parkedDirection\(s\)/.test(detailFn),
  'expanded card consumes _parkedDirection for the parked predicted direction');
const collapsedParked = src.slice(src.indexOf("isParked\n"), src.indexOf("pending evidence</em>'"));
assert(/_parkedDirection\(s\)/.test(collapsedParked) || src.split('_parkedDirection(s)').length - 1 >= 2,
  'collapsed preview consumes _parkedDirection for the parked predicted direction');

if (failed) { console.error(failed + ' assertion(s) failed'); process.exit(1); }
console.log('PASS test_card_finds_fallback');
