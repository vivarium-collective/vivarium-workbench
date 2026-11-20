// tests/js/test_sim_table_actions_by_features.js — run with: node tests/js/test_sim_table_actions_by_features.js
// Step P3 F1 (docs/backend-adapters.md §2.2): the Runs table's row actions, pinned for every kind of row through the
// real _actionList (static/sim-table.js loaded into the test_sim_table_remote_where.js stand-in). The server gives
// each remote row its run's features, remote_origin.features (step P3 F0): an SMS run ["analyses", "artifacts"], a
// viva-core or compose-api run [], and every remote row [] with providers off.
//
// Each action is pinned as "GROUP class label — desc" (class: the action's js class, or "link" for an <a>).
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'sim-table.js'), 'utf8');
const noop = () => {};

function load(extra) {
  const handlers = [];
  const document = { addEventListener: (type, fn) => { if (type === 'click') handlers.push(fn); },
                     querySelectorAll: () => [], querySelector: () => null,
                     getElementById: () => null, createElement: () => ({ style: {}, setAttribute: noop }) };
  const window = Object.assign({ document, addEventListener: noop, confirm: () => true,
                                 location: { pathname: '/', search: '', hash: '' } }, extra || {});
  const sent = [];
  const fetch = (url, opts) => { sent.push({ url, body: opts && opts.body ? opts.body : null }); return new Promise(noop); };
  vm.runInContext(SRC, vm.createContext({ window, document, console, setTimeout, clearTimeout, URLSearchParams, fetch }));
  return { T: window.SimTable, window, handlers, sent };
}

const JS_CLASSES = ['viz-remote-btn', 'trace-remote-btn', 'analysis-files-remote-btn', 'land-viva-btn',
                    'run-analysis-btn', 'rerun-btn'];
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
  ['on: compose row', remoteRow('compose-7', []), false,
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
