// tests/js/test_origin_column.js — run with: node tests/js/test_origin_column.js
// The Runs table's Origin column says where a run RAN. A /viva/v1 run landed into a study has no remote_origin
// (its data is local now) but carries `ran_on`; before, the column read only remote_origin and flipped from
// "sms.cam.uchc.edu" to "local" the moment the run was landed. Loads the real static/sim-table.js (no copy) into a
// minimal browser stand-in and calls its exported originLabel/originPill.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'sim-table.js'), 'utf8');
const noop = () => {};
const document = { addEventListener: noop, querySelectorAll: () => [], querySelector: () => null,
                   getElementById: () => null, createElement: () => ({ style: {}, setAttribute: noop }) };
const window = { document, addEventListener: noop, location: { pathname: '/', search: '', hash: '' } };
const ctx = vm.createContext({ window, document, console, setTimeout, clearTimeout, URLSearchParams });
vm.runInContext(SRC, ctx);
const T = window.SimTable;
assert.ok(T && typeof T.originLabel === 'function' && typeof T.originPill === 'function', 'SimTable exports');

// A landed /viva/v1 run: data local, ran on the backend.
const landed = { remote_origin: null, ran_on: 'sms.cam.uchc.edu' };
assert.strictEqual(T.originLabel(landed), 'sms.cam.uchc.edu', 'a landed run names the backend it ran on');
const pill = T.originPill(landed);
assert.ok(pill.includes('>sms.cam.uchc.edu<') && pill.includes('origin-remote'), pill);
assert.ok(pill.includes('Ran on sms.cam.uchc.edu; results landed into this workspace'), pill);

// A pending remote run: its deployment, as before; no hard-coded "AWS GovCloud".
const pending = { remote_origin: { deployment: 'sms.cam.uchc.edu', simulation_id: 'simulation-X' } };
assert.strictEqual(T.originLabel(pending), 'sms.cam.uchc.edu');
assert.ok(!T.originPill(pending).includes('GovCloud'), 'no hard-coded AWS GovCloud in the tooltip');

// A run that ran here.
assert.strictEqual(T.originLabel({}), 'local');
assert.ok(T.originPill({}).includes('origin-local'));

// ran_on is text, never markup.
assert.ok(!T.originPill({ ran_on: '<img src=x>' }).includes('<img'), 'ran_on is escaped');

console.log('test_origin_column.js: ok');
