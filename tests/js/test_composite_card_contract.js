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
// Clean card face: both the grid card and the full-card header now show just the
// name (full header keeps the read-only pill). The "Composite" kind badge, the
// composite-tier badge, the inline contract marker, and the "workspace" tag were
// removed as redundant/obvious; the process count moved next to the param count.
assert.ok(src.includes('<div class="reg-card-head"><strong class="reg-card-name">\' + _esc(c.name) + \'</strong></div>'), 'grid head name-only');
assert.ok(src.includes("'<span class=\"loom-name\">' + _esc(c.name) + '</span>' + roPill +"), 'full header name + roPill');
// No tier/contract badge is rendered inline on either card anymore (the helpers
// stay defined; only the inline render calls were removed).
assert.ok(!src.includes('_compositeTierBadge(c) + _contractBadge'), 'tier/contract badges not rendered inline');
assert.strictEqual(count('_contractBadge(c.contract_audit)'), 0, 'contract badge not rendered inline');
// Live process count sits next to the param count in the meta line (filled by the
// IntersectionObserver from the resolved bigraph).
assert.ok(src.includes('data-role="proc-count"'), 'process-count slot in meta line');
// Description is click-to-expand on both card views.
assert.ok(src.includes('pcard-desc-clamp') && src.includes('_pcardToggleDesc'), 'description click-to-expand');
// The contract now lives behind a header "§ Contract" button that lazy-fetches
// the real composite audit into a panel — not an always-open accordion section.
assert.ok(!src.includes("_pcardSection('contract'"), 'no contract accordion section');
assert.ok(src.includes("c.contract_audit ? _compositeContractBtn()"), 'contract button wired in header');
assert.ok(src.includes('data-role="composite-contract"'), 'contract panel present');
assert.ok(typeof sandbox.window._compositeContractBtn === 'function', '_compositeContractBtn exported');
assert.ok(typeof sandbox.window._compositeContractPanelBody === 'function', '_compositeContractPanelBody exported');
// The _contractBadge helper itself stays defined + exported (used by the panel).
assert.ok(typeof _contractBadge === 'function', '_contractBadge still exported');

console.log('test_composite_card_contract OK');
