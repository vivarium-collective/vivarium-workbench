// tests/js/test_session.js — run with: node tests/js/test_session.js
//
// Exercises static/session.js (per-tab identity + fetch override) under Node by
// standing up minimal browser globals (sessionStorage, location, crypto, fetch)
// BEFORE requiring the module, so its require-time auto-install patches our fake
// window.fetch. Node 18+ provides global Headers/URL.
const assert = require('assert');

function makeStore() {
  const m = {};
  return {
    getItem: (k) => (k in m ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); },
    removeItem: (k) => { delete m[k]; },
  };
}

let lastFetch = null;      // { input, init } the wrapped fetch forwarded
let responseHeader = null; // value the fake response returns for X-VW-Session

global.window = {
  sessionStorage: makeStore(),
  location: { href: 'http://localhost:8000/', origin: 'http://localhost:8000' },
  crypto: { randomUUID: () => 'uuid-fixed' },
  fetch: function (input, init) {
    lastFetch = { input: input, init: init };
    return Promise.resolve({
      headers: { get: (k) => (k === 'X-VW-Session' ? responseHeader : null) },
    });
  },
};

const sess = require('../../vivarium_workbench/static/session.js');

async function run() {
  // ---- id management --------------------------------------------------------
  assert.strictEqual(sess.getId(), null, 'no id before first use');
  assert.strictEqual(sess.ensureId(), 'uuid-fixed', 'ensureId mints via crypto.randomUUID');
  assert.strictEqual(sess.getId(), 'uuid-fixed', 'minted id is persisted');
  assert.strictEqual(sess.ensureId(), 'uuid-fixed', 'ensureId is stable once minted');

  // ---- fetch override attaches the header on same-origin --------------------
  await window.fetch('/api/state');
  assert.strictEqual(lastFetch.init.headers.get('X-VW-Session'), 'uuid-fixed',
    'same-origin request carries X-VW-Session');

  // A Request-like object (input.url) is also treated as same-origin.
  await window.fetch({ url: '/api/guidance' });
  assert.strictEqual(lastFetch.init.headers.get('X-VW-Session'), 'uuid-fixed',
    'Request-style input carries the header too');

  // ---- cross-origin gets NO session header ----------------------------------
  lastFetch = null;
  await window.fetch('https://cdn.example.com/lib.js');
  const xhdr = lastFetch.init && lastFetch.init.headers;
  assert(!(xhdr && typeof xhdr.get === 'function' && xhdr.get('X-VW-Session')),
    'cross-origin request is not tagged');

  // ---- caller-set header is not clobbered -----------------------------------
  responseHeader = null;
  sess.setId('stored-id');
  await window.fetch('/api/x', { headers: { 'X-VW-Session': 'caller-set' } });
  assert.strictEqual(lastFetch.init.headers.get('X-VW-Session'), 'caller-set',
    'an explicit caller header wins over the stored id');

  // ---- a server-minted response header is captured --------------------------
  sess.clearId();
  assert.strictEqual(sess.getId(), null, 'clearId removes the stored id');
  responseHeader = 'server-minted-id';
  await window.fetch('/api/state');
  assert.strictEqual(sess.getId(), 'server-minted-id',
    'X-VW-Session response header is captured into storage');

  // ---- installFetch is idempotent -------------------------------------------
  const patched = window.fetch;
  sess.installFetch(window);
  assert.strictEqual(window.fetch, patched, 'installFetch does not double-wrap');

  console.log('test_session.js: all assertions passed');
}

run().catch((e) => { console.error(e); process.exit(1); });
