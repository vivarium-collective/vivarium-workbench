// tests/js/test_contract_badge.js — run with: node tests/js/test_contract_badge.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// minimal window with the escaper the helpers rely on
const noop = () => {};
const document = { addEventListener: noop, createElement: () => ({}) };
const window = { document, addEventListener: noop, _esc: (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;') };
const sandbox = { window, document, console };
vm.createContext(sandbox);
const src = fs.readFileSync(path.join(__dirname, '../../vivarium_workbench/static/composite-card.js'), 'utf8');
vm.runInContext(src, sandbox);
const { _contractBadge, _contractPanelBody } = sandbox.window;

// test_badge_per_status
assert.ok(_contractBadge({ status: 'pass', grade: 1 }).includes('contract-pass'));
assert.ok(_contractBadge({ status: 'fail', findings: [{ severity: 'error', message: 'x' }] }).includes('contract-fail'));
assert.ok(_contractBadge({ status: 'fail', findings: [{ severity: 'error', message: 'x' }] }).includes('1')); // finding count
assert.ok(_contractBadge({ status: 'incomplete', grade: 0.2 }).includes('contract-incomplete'));
assert.ok(_contractBadge({ status: 'unavailable' }).includes('contract-none'));

// test_absent_contract_audit_renders_empty
assert.strictEqual(_contractBadge(undefined), '');
assert.strictEqual(_contractPanelBody(undefined), '');
assert.strictEqual(_contractBadge(null), '');

// test_panel_escapes_finding_message
const panel = _contractPanelBody({ status: 'fail', grade: 0.5,
  conditions: [{ kind: 'post', name: 'n', expr: 'outputs.x >= 0' }],
  findings: [{ severity: 'error', code: 'c', where: 'w', message: '<script>bad</script>' }] });
assert.ok(!panel.includes('<script>bad'));           // escaped
assert.ok(panel.includes('&lt;script&gt;'));
assert.ok(panel.includes('outputs.x &gt;= 0'));      // expr escaped

console.log('test_contract_badge OK');
