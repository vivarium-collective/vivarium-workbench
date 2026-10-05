// run with: node tests/js/test_find_candidates_panel.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const src = fs.readFileSync(path.join(__dirname, '../../vivarium_workbench/static/walkthrough.js'), 'utf8');
// walkthrough.js is one huge IIFE; extract the marked block + the in-file _esc.
const block = src.match(/\/\/ <find-candidates>[\s\S]*?\/\/ <\/find-candidates>/)[0];
const esc = src.match(/  function _esc\(str\) \{[\s\S]*?\n  \}/)[0];
const window = {}; // deliberately no window._esc
vm.runInContext = vm.runInContext;
const ctx = vm.createContext({ window, document: { getElementById: () => null } });
vm.runInContext(esc + '\n' + block, ctx);
const R = window._renderCandidateList;
assert.strictEqual(typeof R, 'function');

const ok = R({ status: 'ok', candidates: [
  { address: 'local:fits', match: 'full', fails: [] },
  { address: 'local:x', match: 'partial', fails: [{ condition: 'bounds.inputs.m', reason: 'too wide' }] }] });
assert.ok(ok.includes('fits'));
assert.ok(ok.includes('fc-full') && ok.includes('>full<'));
assert.ok(ok.includes('near-miss'));
assert.ok(ok.includes('too wide') && ok.includes('bounds.inputs.m'));
assert.ok(R({ status: 'ok', candidates: [] }).includes('no matching'));
assert.ok(R({ status: 'unavailable' }).includes('unavailable'));
assert.ok(R({ status: 'error', error: 'boom' }).includes('boom'));
const xss = R({ status: 'ok', candidates: [{ address: '<b>a</b>', match: 'partial',
  fails: [{ condition: 'c', reason: '<script>alert(1)</script>' }] }] });
assert.ok(!xss.includes('<script>') && xss.includes('&lt;script&gt;') && !xss.includes('<b>a'));
R(null); R({});
console.log('OK');
