// run with: node tests/js/test_composite_card_contract.js
// Renderers need heavy stubbing, so assert helper output + source wiring.
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
const file = path.join(__dirname, '../../vivarium_workbench/static/composite-card.js');
const src = fs.readFileSync(file, 'utf8');
vm.runInContext(src, sandbox);
const { _contractBadge, _contractPanelBody } = sandbox.window;

assert.ok(_contractBadge({ status: 'unavailable' }).includes('contract-none'));
assert.ok(_contractPanelBody({ status: 'unavailable' }) !== undefined);
assert.strictEqual(_contractBadge(undefined), '');

const count = (n) => src.split(n).length - 1;
assert.ok(src.includes("_compositeTierBadge(c) + _contractBadge(c.contract_audit) + wsPill + '</div>'"), 'grid head');
assert.ok(src.includes("_compositeTierBadge(c) + _contractBadge(c.contract_audit) + wsPill + roPill"), 'full header');
// The contract now lives behind a header "§ Contract" button that lazy-fetches
// the real composite audit into a panel — not an always-open accordion section.
assert.ok(!src.includes("_pcardSection('contract'"), 'no contract accordion section');
assert.ok(src.includes("c.contract_audit ? _compositeContractBtn()"), 'contract button wired in header');
assert.ok(src.includes('data-role="composite-contract"'), 'contract panel present');
assert.ok(typeof sandbox.window._compositeContractBtn === 'function', '_compositeContractBtn exported');
assert.ok(typeof sandbox.window._compositeContractPanelBody === 'function', '_compositeContractPanelBody exported');
// Two badges remain (grid card, full header); the accordion's was removed.
assert.strictEqual(count('_contractBadge(c.contract_audit)'), 2);

console.log('test_composite_card_contract OK');
