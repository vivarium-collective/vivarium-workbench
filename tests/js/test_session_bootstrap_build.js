// tests/js/test_session_bootstrap_build.js — run with: node tests/js/test_session_bootstrap_build.js
//
// The hosted half of the spawn bootstrap: a tab opened with ?build=<simulator_id>
// binds to an sms-api build via /api/source/switch-build (which materializes the
// build's workspace). Same fresh-session + strip + reload contract as ?workspace=.
const assert = require('assert');

function makeStore(seed) {
  const m = Object.assign({}, seed || {});
  return {
    getItem: (k) => (k in m ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); },
    removeItem: (k) => { delete m[k]; },
  };
}

let posted = null;
let reloaded = false;
let replaced = null;

global.window = {
  sessionStorage: makeStore({ 'viv-session-id': 'inherited' }),
  location: {
    href: 'http://localhost:8000/?build=42',
    origin: 'http://localhost:8000',
    pathname: '/',
    search: '?build=42',
    hash: '',
    reload: function () { reloaded = true; },
  },
  history: { replaceState: function (s, t, url) { replaced = url; } },
  crypto: { randomUUID: () => 'fresh-uuid' },
  fetch: function (input, init) {
    posted = { url: input, init: init };
    return Promise.resolve({ ok: true, headers: { get: () => null } });
  },
};

require('../../vivarium_workbench/static/session.js');

async function run() {
  await new Promise((r) => setTimeout(r, 0));

  // fresh per-tab id, not the inherited one.
  assert.strictEqual(global.window.sessionStorage.getItem('viv-session-id'), 'fresh-uuid',
    'inherited id replaced by a fresh one');

  // POSTs the build bind to switch-build with a NUMERIC simulator_id.
  assert(posted, 'a bind request was sent');
  assert.strictEqual(posted.url, '/api/source/switch-build', 'binds via /api/source/switch-build');
  assert.strictEqual(posted.init.method, 'POST', 'bind is a POST');
  assert.deepStrictEqual(JSON.parse(posted.init.body), { simulator_id: 42 },
    'bind carries {simulator_id: <number>}');
  assert.strictEqual(posted.init.headers.get('X-VW-Session'), 'fresh-uuid',
    'bind carries this tab\'s fresh X-VW-Session');

  // ?build= stripped; reloaded once bound.
  assert.strictEqual(replaced, '/', 'build param stripped from the URL');
  assert.strictEqual(reloaded, true, 'reloads after a successful bind');

  console.log('test_session_bootstrap_build.js: all assertions passed');
}

run().catch((e) => { console.error(e); process.exit(1); });
