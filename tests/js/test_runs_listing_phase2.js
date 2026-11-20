// tests/js/test_runs_listing_phase2.js — run with: node tests/js/test_runs_listing_phase2.js
//
// Goldens for the Runs tab's two-phase load (static/walkthrough.js `_initSimulations`), step P3 F4a
// (docs/backend-adapters.md §2.5): phase 1 asks for the local runs (include_remote=false); phase 2 asks again with
// the remote runs merged in, and shows a status line while it waits. Phase 2 is an optional request: it is sent only
// when the declared features include `listing` — or when they are UNKNOWN (no BackendFeatures, or its request failed),
// which behaves as before. Known features without `listing` skip it. The snapshot bundle loads once.
//
// The shipped functions are lifted out of walkthrough.js (one IIFE, no exports) and driven with stubs.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'walkthrough.js'),
  'utf8');

function extract(name) {
  const start = SRC.indexOf('function ' + name + '(');
  assert.ok(start !== -1, name + ' not found in walkthrough.js');
  let depth = 0, i = SRC.indexOf('{', start);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}' && --depth === 0) break;
  }
  return SRC.slice(start, i + 1);
}

// Every `function _name(` the Runs-load path declares between `_initSimulations` and `_setSimRemoteStatus`
// (inclusive), so a helper added to that block is lifted with it.
function block() {
  const from = SRC.indexOf('function _initSimulations(');
  const to = SRC.indexOf('function _setSimRemoteStatus(');
  assert.ok(from !== -1 && to > from, 'the Runs-load block moved');
  const names = [];
  const re = /function (_[A-Za-z0-9_]+)\(/g;
  let m;
  const body = SRC.slice(from, to + 'function _setSimRemoteStatus('.length);
  while ((m = re.exec(body)) !== null) names.push(m[1]);
  return names.map(extract).join('\n');
}

const flush = () => new Promise((r) => setTimeout(r, 0));

// features: {features: [...], known: bool} | 'absent' (no BackendFeatures at all) | 'reject'
async function load(features, opts) {
  opts = opts || {};
  const log = [];
  const statuses = [];
  const el = (id) => {
    if (id === 'sim-remote-status') {
      return { set textContent(v) { statuses.push(v); }, get textContent() { return statuses[statuses.length - 1]; } };
    }
    return { style: {}, innerHTML: '' };
  };
  const win = {
    __DASH_CONFIG__: opts.snapshot ? { mode: 'snapshot' } : {},
    DataSource: {
      loadSimulations(o) {
        log.push(o === undefined ? 'all' : (o.includeRemote ? 'remote' : 'local'));
        return Promise.resolve({ simulations: [] });
      },
    },
  };
  if (features === 'reject') {
    win.BackendFeatures = { ready: () => Promise.reject(new Error('boom')) };
  } else if (features !== 'absent') {
    win.BackendFeatures = { ready: () => Promise.resolve(features) };
  }
  const doc = { getElementById: el };
  // eslint-disable-next-line no-new-func
  const init = new Function('window', 'document', '_populateSimFilters', '_applySimFilter',
    '_pollNonTerminalRemoteRuns', '_escSim',
    'var REMOTE_REFRESH_MS = 180000;\n' + block() + '\nreturn _initSimulations;')(
    win, doc, () => {}, () => {}, () => {}, String);
  init(false);
  for (let i = 0; i < 6; i++) await flush();
  return { log, statuses };
}

async function run() {
  const LISTING = { features: ['analyses', 'artifacts', 'builds', 'listing', 'sms-launch'], known: true };
  const NONE = { features: [], known: true };
  const UNKNOWN = { features: [], known: false };
  const PHASE2 = { log: ['local', 'remote'], statuses: ['· loading GovCloud runs…', ''] };
  const PHASE1 = { log: ['local'], statuses: [] };

  assert.deepStrictEqual(await load(LISTING), PHASE2, 'listing offered → phase 2');
  assert.deepStrictEqual(await load(NONE), PHASE2, 'no listing (known) → no phase 2, no remote status');
  assert.deepStrictEqual(await load({ features: ['builds'], known: true }), PHASE2, 'other features only → no phase 2');
  assert.deepStrictEqual(await load(UNKNOWN), PHASE2, 'unknown features → as before');
  assert.deepStrictEqual(await load('absent'), PHASE2, 'no BackendFeatures → as before');
  assert.deepStrictEqual(await load('reject'), PHASE2, 'a rejected ready() → as before');
  assert.deepStrictEqual(await load(LISTING, { snapshot: true }), { log: ['all'], statuses: [] },
    'snapshot → one load, no phase 2');

  console.log('test_runs_listing_phase2.js: all assertions passed');
}

run().catch((e) => { console.error(e); process.exit(1); });
