// tests/js/test_inv_decision_tab_balance.js — run: node tests/js/test_inv_decision_tab_balance.js
//
// Regression for the "Validation / Needs-attention tabs render blank" bug.
//
// The investigation-detail brief concatenates its tab panels into one container:
//   <div class="inv-brief-panels">
//     <div class="inv-brief-panel" data-panel="decisions">DECISIONS_HTML</div>
//     <div class="inv-brief-panel" data-panel="validation">…</div>
//     <div class="inv-brief-panel" data-panel="attention">…</div>
//   </div>
// so DECISIONS_HTML (built by `_invDecisionTabHtml`) MUST have balanced <div>s.
// If it leaks an open <div>, the browser balances the tree by nesting the panels
// that FOLLOW decisions (validation, attention) inside the decision card — they
// collapse to zero height and read as blank. A reconciliation card used to open
// three wrappers but close only two, leaking one <div> per reconciliation_log
// item. This asserts the decisions-tab HTML is balanced, including with many
// reconciliation items (the shape that triggered the bug).
//
// walkthrough.js is not node-requirable (it needs a full DOM and has no exports),
// so we lift the pure string-builder `_invDecisionTabHtml` and its two escaping
// helpers out by brace-matching and run them against stub globals. The recon /
// engine-decision branches only call `_esc` / `_escInv` (both div-free), so no
// DOM is needed for this path.

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'walkthrough.js'), 'utf8');

function extract(name) {
  const m = new RegExp('function ' + name + '\\s*\\(', 'g').exec(SRC);
  if (!m) throw new Error('function not found: ' + name);
  let depth = 0;
  for (let j = SRC.indexOf('{', m.index); j < SRC.length; j++) {
    if (SRC[j] === '{') depth++;
    else if (SRC[j] === '}') { depth--; if (depth === 0) return SRC.slice(m.index, j + 1); }
  }
  throw new Error('unbalanced braces extracting ' + name);
}

// Minimal globals the lifted builder reads.
const window = { __DASH_CONFIG__: {}, _currentIset: 'demo-inv' };
// The authored-decisions branch calls three content helpers that each emit
// balanced markup (verified separately); stub them as div-free so this test
// isolates the STRUCTURAL div balance of `_invDecisionTabHtml` itself.
const stubs = 'function _renderInvLeadMarkdown(s){return _esc(String(s==null?"":s));}'
  + 'function _decisionEvidenceHtml(){return "";}'
  + 'function _decisionOptions(){return [];}';
const code = [stubs, extract('_esc'), extract('_escInv'), extract('_invDecisionTabHtml'),
  ';globalThis.__fn = _invDecisionTabHtml;'].join('\n');
new Function('window', code)(window);
const render = globalThis.__fn;

function divBalance(html) {
  return (html.match(/<div\b/g) || []).length - (html.match(/<\/div>/g) || []).length;
}

function reconItems(n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({ id: 'item-' + i, status: 'open', severity: i % 2 ? 'high' : 'blocking',
               observation: 'Observation ' + i, request: 'Reconcile ' + i });
  }
  return out;
}

// 1) The bug's exact shape: many reconciliation items. Pre-fix this returned +15
//    for 15 items; it must be perfectly balanced.
for (const n of [1, 2, 15, 40]) {
  const html = render({ name: 'demo-inv', reconciliation_log: reconItems(n) });
  assert.strictEqual(divBalance(html), 0,
    n + ' reconciliation items must emit balanced <div>s (got ' + divBalance(html) + ')');
  assert.ok(html.indexOf('eng-decision') !== -1, 'reconciliation cards rendered for n=' + n);
}

// 2) Revision proposals (the sibling engine-decision branch) stay balanced too.
const withRev = render({ name: 'demo-inv',
  revision_proposals: [{ id: 'r1', status: 'proposed', source: { study: 's1' },
                         candidate_changes: [{ name: 'k', from: 1, to: [2, 3] }],
                         inadequacy: 'why', discriminating_test: { pass_if: 'x>0' } }],
  reconciliation_log: reconItems(3) });
assert.strictEqual(divBalance(withRev), 0, 'revision + reconciliation must be balanced');

// 3) Authored expert decisions present alongside engine reconciliation items.
const withNeeded = render({ name: 'demo-inv',
  executive: { decisions_needed: [{ question: 'Pick A or B?', context: 'because' }] },
  reconciliation_log: reconItems(4) });
assert.strictEqual(divBalance(withNeeded), 0, 'needed + reconciliation must be balanced');

// 4) Nothing to show → empty string (no stray wrapper).
assert.strictEqual(render({ name: 'demo-inv' }), '', 'no decisions/docs/engine → empty');

console.log('OK test_inv_decision_tab_balance');
