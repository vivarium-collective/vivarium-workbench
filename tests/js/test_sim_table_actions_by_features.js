// tests/js/test_sim_table_actions_by_features.js — run with: node tests/js/test_sim_table_actions_by_features.js
// Step P3 F1 (docs/backend-adapters.md §2.2): the Runs table's row actions, pinned for every kind of row through the
// real _actionList (static/sim-table.js loaded into the test_sim_table_remote_where.js stand-in). The server gives
// each remote row its run's features, remote_origin.features (step P3 F0): an SMS run ["analyses", "artifacts"], a
// viva-core run [], a compose-api run ["visibility"] (with providers off too), and every other remote row [] with
// providers off.
//
// Each action is pinned as "GROUP class label — desc" (class: the action's js class, or "link" for an <a>).
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'sim-table.js'), 'utf8');
const noop = () => {};

function load(extra, fetchImpl) {
  const handlers = [];
  const document = { addEventListener: (type, fn) => { if (type === 'click') handlers.push(fn); },
                     querySelectorAll: () => [], querySelector: () => null,
                     getElementById: () => null, createElement: () => ({ style: {}, setAttribute: noop }) };
  const window = Object.assign({ document, addEventListener: noop, confirm: () => true,
                                 location: { pathname: '/', search: '', hash: '' } }, extra || {});
  const sent = [];
  const fetch = (url, opts) => { sent.push({ url, body: opts && opts.body ? opts.body : null });
                                 return fetchImpl ? fetchImpl(url, opts) : new Promise(noop); };
  // The page's alert (sim-table.js's _toast falls back to it with no toast.js) is the window's, when a test gives one.
  const alert = (m) => (window.alert ? window.alert(m) : undefined);
  vm.runInContext(SRC, vm.createContext({ window, document, console, setTimeout, clearTimeout, URLSearchParams, fetch, alert }));
  return { T: window.SimTable, window, handlers, sent };
}

const JS_CLASSES = ['viz-remote-btn', 'trace-remote-btn', 'analysis-files-remote-btn', 'land-viva-btn',
                    'run-analysis-btn', 'rerun-btn', 'make-public-btn'];
function sig(a) {
  const cls = JS_CLASSES.find((c) => a.html.includes(c)) || (a.html.startsWith('<a ') ? 'link' : '?');
  const label = a.html.replace(/<[^>]*>/g, '').trim();
  return a.group + ' ' + cls + ' ' + label + (a.desc ? ' — ' + a.desc : '');
}
function actions(T, row) { return Array.from(T.actionList(row), sig); }   // into this realm's Array

const SMS = ['analyses', 'artifacts'];
function remoteRow(simId, features, status) {
  const ro = { deployment: 'dep', simulation_id: simId };
  if (features !== undefined) ro.features = features;
  return { run_id: 'remote-pending-' + simId, status: status || 'completed', study_slug: 's1', remote_origin: ro };
}
const LOCAL = { run_id: 'r1', status: 'completed', study_slug: 's1', store_path: '/ws/studies/s1/runs.db' };

// The action lists.
const VIZ_LOCAL = 'VIEW link 📊 Viz — Open the run\'s figures';
const VIZ_REMOTE = 'VIEW viz-remote-btn 📊 Viz — Open the run\'s figures';
const TRACE = 'VIEW trace-remote-btn ⏱ Trace — Trace timeline in Perfetto';
const REPORT = 'VIEW link 📋 Report — Open the report card';
const FILES_REMOTE = 'DOWNLOAD analysis-files-remote-btn ⬇ Analysis files — analyses.json — lands from the deployment, then downloads';
const FILES_LINK_REMOTE = 'DOWNLOAD link ⬇ Analysis files — analyses.json — lands from the deployment, then downloads';
const FILES_LOCAL = 'DOWNLOAD link ⬇ Analysis files — analyses.json';
const LAND = 'DOWNLOAD land-viva-btn ⬇ Land results — Bring the run\'s results into this study';
const RAW = 'DOWNLOAD link ⬇ Raw data — Raw emitter data (.zip)';
const REANALYSE = 'RERUN run-analysis-btn 🧪 Re-run analysis — Recompute the run\'s analyses on its backend';
const RERUN = 'RERUN rerun-btn ↻ Re-run simulation — Replay this run\'s exact manifest';
const PUBLIC = 'SHARE make-public-btn 🔓 Make public — Let anyone read this run on its backend';

const CASES = [
  // [name, row, snapshot?, expected]. The base (goldens commit) -> after, step P3 F1:
  //   SMS rows: identical (the Re-run analysis description is neutral);
  //   viva-core / compose rows: Figures (remote Viz), Analysis files and Re-run analysis hidden -- their backend
  //     declares neither `artifacts` nor `analyses`; the server answered each with a 409;
  //   an all-digit viva-core id: Land results (a fix; it used to get Analysis files by its id's form);
  //   providers off, or an old payload without features: the same as a viva-core row (Land results is the core
  //     run lifecycle's; the server answers it as before);
  //   snapshot: identical.
  ['on: SMS row (integer id)', remoteRow(12, SMS), false,
    [VIZ_REMOTE, TRACE, REPORT, FILES_REMOTE, REANALYSE, RERUN]],
  ['on: SMS row (integer id as a string)', remoteRow('12', SMS), false,
    [VIZ_REMOTE, TRACE, REPORT, FILES_REMOTE, REANALYSE, RERUN]],
  ['on: viva-core row (string id)', remoteRow('simulation-E9Ec819', []), false,
    [TRACE, REPORT, LAND, RERUN]],
  ['on: viva-core row with an all-digit id', remoteRow('123', []), false,
    [TRACE, REPORT, LAND, RERUN]],
  ['on: compose row (visibility: its owner may make it public)', remoteRow('compose-7', ['visibility']), false,
    [TRACE, REPORT, LAND, RERUN, PUBLIC]],
  ['on: compose row, still running: it can be made public already', remoteRow('compose-7', ['visibility'], 'running'), false,
    [TRACE, LAND, RERUN, PUBLIC]],
  ['an old payload without visibility: no Make public', remoteRow('compose-7', []), false,
    [TRACE, REPORT, LAND, RERUN]],
  ['on: viva-core row, still running', remoteRow('simulation-E9Ec819', [], 'running'), false,
    [TRACE, LAND, RERUN]],
  ['on: SMS row, still running', remoteRow(12, SMS, 'running'), false,
    [TRACE, RERUN]],
  ['an empty run id: nothing to land', remoteRow('', []), false,
    [TRACE, REPORT, RERUN]],
  ['a zero run id: the server reads it as no id (as _first_id), nothing to land', remoteRow(0, []), false,
    [TRACE, REPORT, RERUN]],
  ['an all-zero string id: nothing to land', remoteRow('00', []), false,
    [TRACE, REPORT, RERUN]],
  ['a backend with artifacts but no analyses', remoteRow('x-1', ['artifacts']), false,
    [VIZ_REMOTE, TRACE, REPORT, FILES_REMOTE, RERUN]],
  ['off: SMS row (no features)', remoteRow(12, []), false,
    [TRACE, REPORT, LAND, RERUN]],
  ['off: viva-core row (no features)', remoteRow('simulation-E9Ec819', []), false,
    [TRACE, REPORT, LAND, RERUN]],
  ['old payload: SMS row without a features key', remoteRow(12, undefined), false,
    [TRACE, REPORT, LAND, RERUN]],
  ['local row', LOCAL, false,
    [VIZ_LOCAL, REPORT, FILES_LOCAL, RAW, RERUN]],
  ['local row with a stray features key', Object.assign({ features: SMS }, LOCAL), false,
    [VIZ_LOCAL, REPORT, FILES_LOCAL, RAW, RERUN]],
  ['snapshot: SMS row', remoteRow(12, SMS), true,
    [VIZ_REMOTE, REPORT, FILES_LINK_REMOTE]],
  ['snapshot: compose row: never Make public', remoteRow('compose-7', ['visibility']), true,
    [VIZ_REMOTE, REPORT, FILES_LINK_REMOTE]],
  ['snapshot: viva-core row', remoteRow('simulation-E9Ec819', []), true,
    [VIZ_REMOTE, REPORT, FILES_LINK_REMOTE]],
  ['snapshot: local row', LOCAL, true,
    [VIZ_LOCAL, REPORT, FILES_LOCAL, RAW]],
];

const live = load();
const snap = load({ __DASH_CONFIG__: { mode: 'snapshot' } });
const failures = [];
for (const [name, row, snapshot, expected] of CASES) {
  const got = actions((snapshot ? snap : live).T, row);
  const want = expected;
  try { assert.deepStrictEqual(got, want); }
  catch (e) { failures.push(name + '\n  got:  ' + JSON.stringify(got, null, 1) + '\n  want: ' + JSON.stringify(want, null, 1)); }
}
assert.ok(!failures.length, failures.join('\n\n'));
console.log('test_sim_table_actions_by_features.js: action lists ok (' + CASES.length + ' rows)');

// The PTools path: the SMS row's ⬇ Analysis files posts /api/remote-run-land-artifacts with exactly this body.
{
  const { handlers, sent } = load();
  const attrs = { 'data-run-id': 'remote-pending-12', 'data-remote-sim-id': '12',
                  'data-remote-deployment': 'dep', 'data-study': 's1' };
  const row = { getAttribute: (n) => (n in attrs ? attrs[n] : null) };
  const button = { textContent: '', disabled: false, closest: (sel) => (sel.startsWith('tr') ? row : null) };
  const target = { closest: (sel) => (sel === '.analysis-files-remote-btn' ? button : null) };
  for (const h of handlers) h({ target, stopPropagation: noop, preventDefault: noop });
  assert.deepStrictEqual(JSON.parse(JSON.stringify(sent)), [{
    url: '/api/remote-run-land-artifacts',
    body: '{"simulation_id":"12","run_id":"remote-pending-12","deployment":"dep","study":"s1"}',
  }]);
  console.log('test_sim_table_actions_by_features.js: SMS land-artifacts body ok');
}

// Make public: confirmed first, then POST /api/remote-run-visibility with the row's id and where; declined: nothing sent.
{
  const attrs = { 'data-run-id': 'compose-7', 'data-remote-sim-id': 'compose-7',
                  'data-remote-deployment': 'prod', 'data-study': 's1' };
  const row = { getAttribute: (n) => (n in attrs ? attrs[n] : null) };
  const button = { textContent: '', disabled: false, closest: (sel) => (sel.startsWith('tr') ? row : null) };
  const target = { closest: (sel) => (sel === '.make-public-btn' ? button : null) };
  const asked = [];
  const yes = load({ confirm: (m) => { asked.push(m); return true; } });
  for (const h of yes.handlers) h({ target, stopPropagation: noop, preventDefault: noop });
  assert.strictEqual(asked.length, 1);
  assert.ok(asked[0].includes('compose-7') && asked[0].includes('other runs of any comparison'), asked[0]);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(yes.sent)), [{
    url: '/api/remote-run-visibility',
    body: '{"simulation_id":"compose-7","visibility":"public","deployment":"prod","study":"s1"}',
  }]);
  const no = load({ confirm: () => false });
  for (const h of no.handlers) h({ target, stopPropagation: noop, preventDefault: noop });
  assert.deepStrictEqual(no.sent, []);
  console.log('test_sim_table_actions_by_features.js: Make public confirms, then posts the row');
}

// Make public's answers: the button comes back on every path, the toast says what happened, a success refreshes the
// table, and the base path is kept.
(async () => {
  const attrs = { 'data-run-id': 'compose-7', 'data-remote-sim-id': 'compose-7' };
  const row = { getAttribute: (n) => (n in attrs ? attrs[n] : null) };
  async function click(answer) {
    const button = { textContent: '🔓 Make public', disabled: false, closest: (sel) => (sel.startsWith('tr') ? row : null) };
    const target = { closest: (sel) => (sel === '.make-public-btn' ? button : null) };
    const toasts = []; let refreshed = 0;
    const env = load({ __BASE_PATH__: '/wb', _initSimulations: () => { refreshed += 1; } },
                     () => (answer instanceof Error ? Promise.reject(answer)
                       : Promise.resolve({ ok: answer.status < 400, status: answer.status, json: () => Promise.resolve(answer.body) })));
    env.window.alert = (m) => toasts.push(m);
    for (const h of env.handlers) h({ target, stopPropagation: noop, preventDefault: noop });
    await new Promise((r) => setTimeout(r, 10));
    return { button, toasts, refreshed, url: env.sent[0].url };
  }
  const ok = await click({ status: 200, body: { changed: ['compose-7', 'compose-8', 'compose-9'] } });
  assert.strictEqual(ok.url, '/wb/api/remote-run-visibility');
  assert.deepStrictEqual([ok.button.disabled, ok.button.textContent, ok.refreshed], [false, '🔓 Make public', 1]);
  assert.deepStrictEqual(ok.toasts, ['Run compose-7 is public (with 2 other runs of its comparisons).']);
  const refused = await click({ status: 403, body: { error: 'only its owner may' } });
  assert.deepStrictEqual([refused.button.disabled, refused.refreshed], [false, 0]);
  assert.deepStrictEqual(refused.toasts, ['Could not make the run public: only its owner may']);
  const down = await click(new Error('offline'));
  assert.deepStrictEqual([down.button.disabled, down.button.textContent], [false, '🔓 Make public']);
  assert.ok(down.toasts[0].startsWith('Could not make the run public: network error'), down.toasts[0]);
  console.log('test_sim_table_actions_by_features.js: Make public answers ok');
})().catch((e) => { console.error(e); process.exit(1); });
