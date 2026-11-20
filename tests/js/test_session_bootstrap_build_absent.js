// tests/js/test_session_bootstrap_build_absent.js — run: node tests/js/test_session_bootstrap_build_absent.js
//
// Step P3 F3 (review fix 13): a tab opened with ?build=<id> on a workbench with no build backend gets a
// 404/405 from /api/source/switch-build (the route is a provider's). session.js says so plainly instead of
// a bare status code, and does not consult the declared features (the route's answer is the authority).
// Any other failure keeps the old message. A 404 is only "no build backend" when it is FastAPI's bare
// {"detail": "Not Found"}; with a provider on, the route itself answers 404 {"error": "build <id> not found"}
// for an unknown build (a stale ?build= link) and that error is shown instead.
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

async function boot(status, body) {
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
      return Promise.resolve({
        ok: status < 400, status: status, headers: { get: () => null },
        json: () => (body === undefined ? Promise.reject(new Error('no body')) : Promise.resolve(body)),
      });
    },
  };
  delete require.cache[require.resolve(SRC)];
  require(SRC);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  return { failures, posted, reloaded };
}

async function run() {
  const absent = [[404, undefined], [404, { detail: 'Not Found' }], [405, undefined], [405, { detail: 'Method Not Allowed' }]];
  for (const [status, body] of absent) {
    const { failures, posted, reloaded } = await boot(status, body);
    assert.deepStrictEqual(posted, ['/api/source/switch-build'], 'only the bind; no features request');
    assert.strictEqual(reloaded, false);
    assert.strictEqual(failures.length, 1);
    assert.ok(/no build backend/.test(failures[0]), status + ': ' + failures[0]);
    assert.ok(/42/.test(failures[0]), 'names the build');
    assert.ok(!/sms/i.test(failures[0]), 'neutral wording');
  }
  // Providers on: the plugin route answers 404 with an error for an unknown build — not "no build backend".
  const unknown = await boot(404, { error: 'build 999 not found' });
  assert.strictEqual(unknown.reloaded, false);
  assert.strictEqual(unknown.failures.length, 1);
  assert.ok(!/no build backend/.test(unknown.failures[0]), unknown.failures[0]);
  assert.ok(/build 999 not found/.test(unknown.failures[0]), unknown.failures[0]);
  const other = await boot(502);
  assert.strictEqual(other.failures.length, 1);
  assert.ok(/switch-build returned 502/.test(other.failures[0]), other.failures[0]);
  console.log('test_session_bootstrap_build_absent.js: all assertions passed');
}

run().catch((e) => { console.error(e); process.exit(1); });
