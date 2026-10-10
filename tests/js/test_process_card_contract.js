// tests/js/test_process_card_contract.js — run with: node tests/js/test_process_card_contract.js
// _renderProcessCard lives inside walkthrough.js's IIFE (not exported), so this
// asserts the helper output plus that each insertion point is wired in source.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const noop = () => {};
const document = { addEventListener: noop, createElement: () => ({}) };
const window = { document, addEventListener: noop, _esc: (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;') };
const sandbox = { window, document, console };
vm.createContext(sandbox);
const dir = path.join(__dirname, '../../vivarium_workbench/static');
vm.runInContext(fs.readFileSync(path.join(dir, 'composite-card.js'), 'utf8'), sandbox);
const { _contractBadge, _contractPanelBody } = sandbox.window;

assert.ok(_contractBadge({ status: 'pass', grade: 1 }).includes('contract-pass'));
assert.ok(_contractPanelBody({ status: 'pass', grade: 1 }) !== undefined);
assert.strictEqual(_contractBadge(undefined), '');   // no contract_audit -> no badge, no throw

const w = fs.readFileSync(path.join(dir, 'walkthrough.js'), 'utf8');
const count = (needle) => w.split(needle).length - 1;
assert.ok(w.includes("kindBadge + _regUseBadge(p) + _contractBadge(p.contract_audit)"), 'full-card header');
assert.ok(w.includes("defaultBadge + _regUseBadge(p) + _contractBadge(p.contract_audit)"), 'grid card head');
assert.ok(w.includes("_procKindBadge(kind) + _regUseBadge(p) + _contractBadge(p.contract_audit)"), 'loom-body-head');
// The contract now lives behind a header "§ Contract" button that toggles a
// pre-rendered panel — not an always-open accordion section.
assert.ok(!w.includes("section('contract'"), 'no contract accordion section');
assert.ok(w.includes("p.contract_audit ? _processContractBtn()"), 'contract button wired in header');
assert.ok(w.includes('data-role="process-contract"'), 'contract panel present');
assert.ok(w.includes("_contractPanelBody(p.contract_audit)"), 'panel renders the per-process audit');
assert.ok(w.includes("function _toggleProcessContract"), 'toggle function defined');
// Three badges remain (full card, grid card, loom body); the accordion's was removed.
assert.strictEqual(count('_contractBadge(p.contract_audit)'), 3);

console.log('test_process_card_contract OK');
