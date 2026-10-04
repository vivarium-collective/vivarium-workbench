// tests/js/test_session_bootstrap.js — run with: node tests/js/test_session_bootstrap.js
//
// Exercises static/session.js's ?workspace= spawn bootstrap. A tab that loads with
// the param must force-mint a fresh session id, POST the bind by name, and — on the
// FIRST bind in this tab — reload so subsequent fetches use the bound session. The
// param is KEPT in the URL (the link is self-describing + reload-proof; the server
// honors it with precedence). A reload (guard flag already set) re-binds idempotently
// but does NOT reload again — no loop. Stands up minimal browser globals, then re-
// requires the module for each scenario.
const assert = require('assert');
const path = require('path');

const MODULE_PATH = path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'session.js');

function makeStore(seed) {
  const m = Object.assign({}, seed || {});
  return {
    getItem: (k) => (k in m ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); },
    removeItem: (k) => { delete m[k]; },
    _m: m,
  };
}

// Build a fake window + load session.js fresh against it. Returns the captured
// side effects (the bind POST, whether it reloaded, any replaceState URL) plus
// the loaded module and the sessionStorage so tests can inspect them.
function loadWith(seed) {
  const captured = { posted: null, reloaded: false, replaced: null };
  const store = makeStore(seed);
  global.window = {
    sessionStorage: store,
    location: {
      href: 'http://localhost:8000/?workspace=increase-demo',
      origin: 'http://localhost:8000',
      pathname: '/',
      search: '?workspace=increase-demo',
      hash: '',
      reload: function () { captured.reloaded = true; },
    },
    history: { replaceState: function (s, t, url) { captured.replaced = url; } },
    crypto: { randomUUID: () => 'fresh-uuid' },
    fetch: function (input, init) {
      captured.posted = { url: input, init: init };
      return Promise.resolve({ ok: true, headers: { get: () => null } });
    },
  };
  // minimal document for bindFailed()'s overlay dismissal (no-op path here)
  global.document = { documentElement: { removeAttribute: function () {} } };
  delete require.cache[require.resolve(MODULE_PATH)];
  const mod = require(MODULE_PATH);
  return { captured, store, mod };
}

async function firstBind() {
  // Fresh tab (browser-cloned → carries an INHERITED id the bootstrap must drop).
  const { captured, store, mod } = loadWith({ 'viv-session-id': 'inherited-from-sibling' });
  await new Promise((r) => setTimeout(r, 0));  // let the bind promise resolve

  // (a) the inherited id was discarded and a fresh one minted.
  assert.strictEqual(mod.getId(), 'fresh-uuid',
    'inherited id replaced by a freshly minted one');

  // (b) it POSTed the bind by NAME (not path) to the switch endpoint.
  assert(captured.posted, 'a bind request was sent');
  assert.strictEqual(captured.posted.url, '/api/source/switch', 'binds via /api/source/switch');
  assert.strictEqual(captured.posted.init.method, 'POST', 'bind is a POST');
  assert.deepStrictEqual(JSON.parse(captured.posted.init.body), { name: 'increase-demo' },
    'bind carries {name: <catalog name>}');
  assert.strictEqual(captured.posted.init.headers.get('X-VW-Session'), 'fresh-uuid',
    'bind carries this tab\'s fresh X-VW-Session');

  // (c) the ?workspace= param is KEPT in the URL — no replaceState strip.
  assert.strictEqual(captured.replaced, null, 'workspace param is NOT stripped (link stays self-describing)');

  // (d) the first bind reloads so fetches route to the now-bound session.
  assert.strictEqual(captured.reloaded, true, 'reloads after the first successful bind');

  // (e) the bind is recorded so a reload does not re-trigger it.
  assert.strictEqual(store.getItem('viv-ws-bound'), 'increase-demo',
    'records the bound workspace to guard the reload loop');

  console.log('test_session_bootstrap.js: first-bind assertions passed');
}

async function reloadNoLoop() {
  // Reload of an already-bound tab: the guard flag is set to the same workspace.
  const { captured, store, mod } = loadWith({
    'viv-session-id': 'already-mine',
    'viv-ws-bound': 'increase-demo',
  });
  await new Promise((r) => setTimeout(r, 0));

  // Re-binds idempotently (survives a server restart that dropped the registry)...
  assert(captured.posted, 'reload still (re)binds the session idempotently');
  assert.deepStrictEqual(JSON.parse(captured.posted.init.body), { name: 'increase-demo' });
  // ...but does NOT reload again (no loop) and keeps the existing id.
  assert.strictEqual(captured.reloaded, false, 'does NOT reload again on an already-bound tab');
  assert.strictEqual(mod.getId(), 'already-mine', 'keeps the tab\'s existing id on a reload');
  assert.strictEqual(captured.replaced, null, 'still keeps the param in the URL');

  console.log('test_session_bootstrap.js: reload-no-loop assertions passed');
}

async function run() {
  await firstBind();
  await reloadNoLoop();
  console.log('test_session_bootstrap.js: all assertions passed');
}

run().catch((e) => { console.error(e); process.exit(1); });
