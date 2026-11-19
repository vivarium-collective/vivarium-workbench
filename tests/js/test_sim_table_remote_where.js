// tests/js/test_sim_table_remote_where.js — run with: node tests/js/test_sim_table_remote_where.js
// Step 1c-2 (docs/backend-adapters.md): a remote row carries the deployment its run was dispatched to, so every
// action on the run can send it back and the server routes the run by its own record. Loads the real
// static/sim-table.js into a minimal browser stand-in (the test_origin_column.js pattern) and renders rows.
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

const remote = { run_id: 'remote-pending-12', status: 'running', study_slug: 's1',
                 remote_origin: { deployment: 'stanford-test', simulation_id: 12 } };
const html = T.renderRow(remote);
assert.ok(html.includes('data-remote-sim-id="12"'), html);
assert.ok(html.includes('data-remote-deployment="stanford-test"'), 'the row carries its deployment: ' + html);

const local = { run_id: 'r1', status: 'completed', study_slug: 's1' };
assert.ok(!T.renderRow(local).includes('data-remote-deployment'), 'a local row has none');

const hostile = { run_id: 'x', status: 'running', remote_origin: { deployment: '"><img src=x>', simulation_id: 1 } };
assert.ok(!T.renderRow(hostile).includes('<img'), 'the deployment is escaped');

console.log('test_sim_table_remote_where.js: ok');

// The actions send the row's deployment and study: rowWhere reads them off the button's <tr>, whereQuery puts them
// on a URL (the figure gallery), and every request body / URL in sim-table.js is built from them.
const tr = { getAttribute: (n) => ({ 'data-remote-deployment': 'stanford test', 'data-study': 's1' })[n] || null };
const btn = { closest: (sel) => (sel === 'tr' ? tr : null) };
const where = T.rowWhere(btn);
assert.strictEqual(where.deployment, 'stanford test');
assert.strictEqual(where.study, 's1');
assert.strictEqual(T.whereQuery(where), '&deployment=stanford%20test&study=s1');
assert.strictEqual(JSON.stringify(T.rowWhere(null)), JSON.stringify({ deployment: '', study: '' }), 'no row: nothing sent');
assert.strictEqual(T.whereQuery({ deployment: '', study: '' }), '');
// opaque run ids (deliberate change): the id is sent as the row's string, no longer Number(simId).
for (const needle of ['Object.assign({ simulation_id: String(simId), run_id: runId }, _rowWhere(btn))',
                      'deployment: _rowWhere(btn).deployment', 'deployment: _rowWhere(btnEl).deployment',
                      'encodeURIComponent(simId) + _whereQuery(where)']) {
  assert.ok(SRC.includes(needle), 'request built from the row: ' + needle);
}
console.log('test_sim_table_remote_where.js: requests ok');

// Opaque run ids (docs/backend-adapters.md §2.5): the real row actions send the remote run's id as the string the
// row carries -- a compose-api / viva-core id must not become NaN -> null, and an SMS id "12" stays "12" (the server
// reads it exactly as 12). Drive the delegated click handlers sim-table.js registers on document.
{
  const handlers = [];
  const sent = [];
  const doc2 = Object.assign({}, document, { addEventListener: (type, fn) => { if (type === 'click') handlers.push(fn); } });
  const win2 = { document: doc2, addEventListener: noop, location: window.location, confirm: () => true };
  const fetch = (url, opts) => { sent.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null }); return new Promise(noop); };
  const ctx2 = vm.createContext({ window: win2, document: doc2, console, setTimeout, clearTimeout, URLSearchParams, fetch });
  vm.runInContext(SRC, ctx2);

  function click(cls, simId) {
    const attrs = { 'data-run-id': 'remote-pending-x', 'data-remote-sim-id': simId,
                    'data-remote-deployment': 'dep', 'data-study': 's1' };
    const row = { getAttribute: (n) => (n in attrs ? attrs[n] : null) };
    const button = { textContent: '', disabled: false, closest: (sel) => (sel.startsWith('tr') ? row : null) };
    const target = { closest: (sel) => (sel === cls ? button : null) };
    for (const h of handlers) h({ target, stopPropagation: noop, preventDefault: noop });
  }

  for (const simId of ['simulation-E9Ec819', 'compose-7', '12']) {
    sent.length = 0;
    click('.analysis-files-remote-btn', simId);
    click('.run-analysis-btn', simId);
    const land = sent.find((s) => s.url.endsWith('/api/remote-run-land-artifacts'));
    const rerun = sent.find((s) => s.url.endsWith('/api/remote-run-analysis'));
    assert.ok(land && rerun, 'both actions fired for ' + simId + ': ' + JSON.stringify(sent));
    assert.strictEqual(land.body.simulation_id, simId, 'land-artifacts sends the id as is');
    assert.strictEqual(rerun.body.simulation_id, simId, 're-run analysis sends the id as is');
    assert.strictEqual(land.body.deployment, 'dep');
  }
  console.log('test_sim_table_remote_where.js: opaque ids ok');
}
