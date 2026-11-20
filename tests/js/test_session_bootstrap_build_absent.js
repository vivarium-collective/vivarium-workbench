// tests/js/test_session_bootstrap_build_absent.js — run: node tests/js/test_session_bootstrap_build_absent.js
//
// Step P3 F3 (review fix 13): a tab opened with ?build=<id> on a workbench with no build backend gets a
// 404/405 from /api/source/switch-build (the route is a provider's). session.js says so plainly instead of
// a bare status code, and does not consult the declared features (the route's answer is the authority).
// Any other failure keeps the old message.
const assert = require('assert');
const path = require('path');

const SRC = path.join(__dirname, '../../vivarium_workbench/static/session.js');

function makeStore(seed) {
  const m = Object.assign({}, seed || {});
  return {
    getItem: (k) => (k in m ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); },
    removeItem: (k) => { delete m[k]; },
  };
}

async function boot(status) {
  const failures = [];
  const posted = [];
  let reloaded = false;
  global.document = { documentElement: { removeAttribute() {} } };
  global.window = {
    sessionStorage: makeStore({}),
    location: {
      href: 'http://localhost:8000/?build=42', origin: 'http://localhost:8000', pathname: '/',
      search: '?build=42', hash: '', reload: function () { reloaded = true; },
    },
    history: { replaceState: function () {} },
    crypto: { randomUUID: () => 'fresh-uuid' },
    vivSessionStatus: { renderFailure: (o) => failures.push(o.error) },
    fetch: function (input) {
      posted.push(input);
      return Promise.resolve({ ok: status < 400, status: status, headers: { get: () => null } });
    },
  };
  delete require.cache[require.resolve(SRC)];
  require(SRC);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  return { failures, posted, reloaded };
}

async function run() {
  for (const status of [404, 405]) {
    const { failures, posted, reloaded } = await boot(status);
    assert.deepStrictEqual(posted, ['/api/source/switch-build'], 'only the bind; no features request');
    assert.strictEqual(reloaded, false);
    assert.strictEqual(failures.length, 1);
    assert.ok(/no build backend/.test(failures[0]), status + ': ' + failures[0]);
    assert.ok(/42/.test(failures[0]), 'names the build');
    assert.ok(!/sms/i.test(failures[0]), 'neutral wording');
  }
  const other = await boot(502);
  assert.strictEqual(other.failures.length, 1);
  assert.ok(/switch-build returned 502/.test(other.failures[0]), other.failures[0]);
  console.log('test_session_bootstrap_build_absent.js: all assertions passed');
}

run().catch((e) => { console.error(e); process.exit(1); });
