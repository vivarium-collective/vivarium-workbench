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
const { _compositeContractPanelBody } = sandbox.window;

// Per-process contract badge/panel are gone; only the composite WIRING panel remains.
assert.strictEqual(sandbox.window._contractBadge, undefined, '_contractBadge removed');
assert.strictEqual(sandbox.window._contractPanelBody, undefined, '_contractPanelBody removed');
assert.ok(_compositeContractPanelBody(undefined).includes('No audit data'));
const pass = _compositeContractPanelBody({ status: 'pass', n_processes: 2, wiring: { status: 'pass', findings: [] } });
assert.ok(pass.includes('Wiring') && pass.includes('pass') && pass.includes('2 processes'));
const bad = _compositeContractPanelBody({ status: 'fail', n_processes: 1, wiring: { status: 'fail',
  findings: [{ severity: 'error', code: 'type-mismatch', where: 'a.b', message: 'x<y' }] } });
assert.ok(bad.includes('contract-finding is-error') && bad.includes('type-mismatch') && bad.includes('a.b') && bad.includes('x&lt;y'));
assert.ok(!bad.includes('completeness'));

const count = (n) => src.split(n).length - 1;
// Clean card face: both the grid card and the full-card header now show just the
// name (full header keeps the read-only pill). The "Composite" kind badge, the
// composite-tier badge, the inline contract marker, and the "workspace" tag were
// removed as redundant/obvious; the process count moved next to the param count.
assert.ok(src.includes('<div class="reg-card-head"><strong class="reg-card-name">\' + _esc(c.name) + \'</strong></div>'), 'grid head name-only');
assert.ok(src.includes("'<span class=\"loom-name\">' + _esc(c.name) + '</span>' + roPill +"), 'full header name + roPill');
// No tier/contract badge is rendered inline on either card anymore (the helpers
// stay defined; only the inline render calls were removed).
assert.ok(!src.includes('_contractBadge'), 'contract badge helper gone');
// Live process count sits next to the param count in the meta line (filled by the
// IntersectionObserver from the resolved bigraph).
assert.ok(src.includes('data-role="proc-count"'), 'process-count slot in meta line');
// Description is click-to-expand on both card views.
assert.ok(src.includes('pcard-desc-clamp') && src.includes('_pcardToggleDesc'), 'description click-to-expand');
// The wiring audit lives behind a header "§ Wiring" button that lazy-fetches
// the real composite audit into a panel — not an always-open accordion section.
assert.ok(!src.includes("_pcardSection('contract'"), 'no contract accordion section');
assert.ok(src.includes('_compositeContractBtn() +') && src.includes('§ Wiring'), 'wiring button wired in header');
assert.ok(src.includes('data-role="composite-contract"'), 'contract panel present');
assert.ok(typeof sandbox.window._compositeContractBtn === 'function', '_compositeContractBtn exported');
assert.ok(typeof sandbox.window._compositeContractPanelBody === 'function', '_compositeContractPanelBody exported');

console.log('test_composite_card_contract OK');
