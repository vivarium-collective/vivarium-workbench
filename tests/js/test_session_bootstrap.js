// tests/js/test_session_bootstrap.js — run with: node tests/js/test_session_bootstrap.js
//
// Exercises static/session.js's ?workspace= spawn bootstrap: a tab that loads with
// the param must force-mint a fresh session id, POST the bind by name, strip the
// param, and reload. Stands up minimal browser globals before requiring the module.
const assert = require('assert');

function makeStore(seed) {
  const m = Object.assign({}, seed || {});
  return {
    getItem: (k) => (k in m ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); },
    removeItem: (k) => { delete m[k]; },
    _m: m,
  };
}

let posted = null;       // the bind request captured
let reloaded = false;
let replaced = null;     // the URL replaceState wrote

// A fresh tab that the browser cloned carries an INHERITED id — the bootstrap
// must NOT keep it.
global.window = {
  sessionStorage: makeStore({ 'viv-session-id': 'inherited-from-sibling' }),
  location: {
    href: 'http://localhost:8000/?workspace=increase-demo',
    origin: 'http://localhost:8000',
    pathname: '/',
    search: '?workspace=increase-demo',
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

const sess = require('../../vivarium_workbench/static/session.js');

async function run() {
  // The IIFE auto-ran bootstrapWorkspaceParam() at require time. Give the fetch
  // promise a tick to resolve.
  await new Promise((r) => setTimeout(r, 0));

  // (a) the inherited id was discarded and a fresh one minted.
  assert.strictEqual(sess.getId(), 'fresh-uuid',
    'inherited id replaced by a freshly minted one');

  // (b) it POSTed the bind by NAME (not path) to the switch endpoint.
  assert(posted, 'a bind request was sent');
  assert.strictEqual(posted.url, '/api/source/switch', 'binds via /api/source/switch');
  assert.strictEqual(posted.init.method, 'POST', 'bind is a POST');
  assert.deepStrictEqual(JSON.parse(posted.init.body), { name: 'increase-demo' },
    'bind carries {name: <catalog name>}');
  // the fetch override attached the fresh session header.
  assert.strictEqual(posted.init.headers.get('X-VW-Session'), 'fresh-uuid',
    'bind carries this tab\'s fresh X-VW-Session');

  // (c) the ?workspace= param was stripped to a clean URL.
  assert.strictEqual(replaced, '/', 'workspace param stripped from the URL');

  // (d) the tab reloaded once bound.
  assert.strictEqual(reloaded, true, 'reloads after a successful bind');

  console.log('test_session_bootstrap.js: all assertions passed');
}

run().catch((e) => { console.error(e); process.exit(1); });
