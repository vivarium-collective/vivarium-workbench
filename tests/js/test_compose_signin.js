// tests/js/test_compose_signin.js — run with: node tests/js/test_compose_signin.js
// The compose-api sign-in chip (static/compose-signin.js), the real file in a stand-in page. Each case names what it
// falsifies. The server side (routes, background task, the CLI's store) is tested in test_backend_dispatch_compose.py.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'compose-signin.js'), 'utf8');

function el() {
  const e = { style: {}, dataset: {}, textContent: '', title: '', onclick: null, disabled: false, children: [],
              append(...xs) { this.children.push(...xs); } };
  Object.defineProperty(e, 'innerHTML', { set() { e.children = []; }, get() { return ''; } });
  return e;
}

const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

function page(status, opts) {
  opts = opts || {};
  const els = { 'viv-compose-row': el(), 'viv-compose-chip': el(), 'viv-compose-hint': el(),
                'viv-compose-flow': el() };
  const sent = [];
  const state = { status };
  const timers = { set: [], cleared: [] };
  const fetch = async (url, o) => {
    sent.push({ url, method: (o && o.method) || 'GET', body: o && o.body ? JSON.parse(o.body) : null });
    const canned = (opts.responses || {})[url.split('/').pop()];
    if (canned) return { ok: canned.ok, status: canned.status || 200, json: async () => canned.body };
    const body = url.endsWith('/status') ? state.status : { flow: { state: 'running', notes: [] } };
    return { ok: true, json: async () => body };
  };
  const document = { getElementById: (id) => els[id] || null, createElement: () => el() };
  const window = Object.assign({ __BASE_PATH__: '/base' }, opts.window || {});
  vm.runInContext(SRC, vm.createContext({
    window, document, console, fetch, JSON,
    confirm: () => (opts.confirm === undefined ? true : opts.confirm),
    setInterval: (fn) => { timers.set.push(fn); return timers.set.length; },
    clearInterval: (id) => { timers.cleared.push(id); },
  }));
  return { els, sent, state, timers, window };
}

const OUT = { available: true, signed_in: false, profile: 'lab', api_base_url: 'https://compose.test', flow: null };
const posts = (p) => p.sent.filter((s) => s.method === 'POST');

(async () => {
  // Falsifies: the row shows where signing in would not sign this workbench's runs in.
  let p = page({ available: false, reason: 'no-backend', flow: null });
  await flush();
  assert.strictEqual(p.els['viv-compose-row'].style.display, 'none');
  assert.deepStrictEqual(p.sent.map((s) => s.url), ['/base/api/auth/compose-api/status']);   // base path kept

  // Falsifies: "Sign in" posts something other than a CLI-style sign-in; the code option is missing or the same.
  p = page(OUT);
  await flush();
  assert.strictEqual(p.els['viv-compose-row'].style.display, '');
  assert.strictEqual(p.els['viv-compose-chip'].textContent, 'Sign in');
  p.els['viv-compose-chip'].onclick();
  await flush();
  assert.deepStrictEqual(posts(p), [{ url: '/base/api/auth/compose-api/start', method: 'POST', body: { method: 'auto' } }]);
  const link = p.els['viv-compose-hint'].children.find((c) => c.textContent === 'Use a code instead');
  assert.ok(link, 'the device-code option is offered');
  link.onclick({ preventDefault() {} });
  await flush();
  assert.deepStrictEqual(posts(p)[1].body, { method: 'device' });

  // Falsifies: while the person signs in, the page neither shows the client's instructions nor keeps polling, or it
  // keeps polling after the sign-in ends.
  p = page({ ...OUT, flow: { state: 'running', notes: ['Open https://x.test and enter WXYZ-1234'], error: null } });
  await flush();
  assert.strictEqual(p.els['viv-compose-chip'].disabled, true);
  assert.ok(p.els['viv-compose-flow'].textContent.includes('WXYZ-1234'));
  assert.strictEqual(p.timers.set.length, 1, 'polls while waiting');
  p.state.status = { ...OUT, signed_in: true, flow: { state: 'ok', notes: ['Signed in.'], error: null } };
  p.timers.set[0]();
  await flush();
  assert.strictEqual(p.els['viv-compose-chip'].textContent, 'Signed in · lab');
  assert.deepStrictEqual(p.timers.cleared, [1], 'stops polling once the sign-in ends');

  // Falsifies: signing out happens without the person's confirmation, or does not reach the server when confirmed.
  p = page({ ...OUT, signed_in: true }, { confirm: false });
  await flush();
  p.els['viv-compose-chip'].onclick();
  await flush();
  assert.deepStrictEqual(posts(p), []);
  p = page({ ...OUT, signed_in: true }, { confirm: true });
  await flush();
  p.els['viv-compose-chip'].onclick();
  await flush();
  assert.deepStrictEqual(posts(p).map((s) => s.url), ['/base/api/auth/compose-api/logout']);

  // Falsifies: a failed sign-in is shown as success or not at all.
  p = page({ ...OUT, flow: { state: 'error', notes: [], error: 'the credential store is locked' } });
  await flush();
  assert.ok(p.els['viv-compose-flow'].textContent.includes('Sign-in failed: the credential store is locked'));
  assert.strictEqual(p.els['viv-compose-chip'].textContent, 'Sign in');

  // Falsifies: a finished sign-in keeps its old code and waiting text on screen.
  p = page({ ...OUT, signed_in: true, flow: { state: 'ok', notes: ['Enter WXYZ-1234', 'Signed in.'], error: null } });
  await flush();
  assert.strictEqual(p.els['viv-compose-flow'].style.display, 'none');

  // Falsifies: polling goes on behind a row that is no longer offered.
  p = page({ ...OUT, flow: { state: 'running', notes: ['wait'], error: null } });
  await flush();
  p.state.status = { available: false, reason: 'token', flow: null };
  p.timers.set[0]();
  await flush();
  assert.strictEqual(p.els['viv-compose-row'].style.display, 'none');
  assert.deepStrictEqual(p.timers.cleared, [1]);

  // Falsifies: a refused sign-out, or one Auth0 could not revoke, is silent.
  p = page({ ...OUT, signed_in: true }, { responses: { logout: { ok: false, status: 409, body: { error: 'store locked' } } } });
  await flush();
  p.els['viv-compose-chip'].onclick();
  await flush();
  assert.ok(p.els['viv-compose-flow'].textContent.includes('Could not sign out: store locked'));
  p = page({ ...OUT, signed_in: true },
           { responses: { logout: { ok: true, body: { signed_in: false, revocation: 'unavailable' } } } });
  await flush();
  p.els['viv-compose-chip'].onclick();
  await flush();
  assert.ok(p.els['viv-compose-flow'].textContent.includes('Auth0 could not be reached'));

  // Falsifies: a signed-in person cannot make a new login without signing out first (the safe way to re-sign).
  p = page({ ...OUT, signed_in: true });
  await flush();
  const again = p.els['viv-compose-hint'].children.find((c) => c.textContent === 'Sign in again');
  assert.ok(again, 'signed in -> "Sign in again" offered');
  again.onclick({ preventDefault() {} });
  await flush();
  assert.deepStrictEqual(posts(p), [{ url: '/base/api/auth/compose-api/start', method: 'POST', body: { method: 'auto' } }]);

  // Falsifies: other places (the composite card's badge) cannot start the same flow, or their badges stay stale after it.
  let badgeRefreshes = 0;
  p = page({ ...OUT, flow: { state: 'running', notes: ['wait'], error: null } },
           { window: { _refreshRunTargetBadges: () => { badgeRefreshes += 1; } } });
  await flush();
  assert.strictEqual(typeof p.window.ComposeSignin.start, 'function');
  assert.strictEqual(badgeRefreshes, 0, 'nothing to re-check while waiting');
  p.state.status = { ...OUT, signed_in: true, flow: { state: 'ok', notes: [], error: null } };
  p.timers.set[0]();
  await flush();
  assert.strictEqual(badgeRefreshes, 1, 'the badges are re-checked once the sign-in ends');
  p.window.ComposeSignin.start('auto');
  await flush();
  assert.deepStrictEqual(posts(p).map((x) => x.body), [{ method: 'auto' }]);

  console.log('PASS  tests/js/test_compose_signin.js');
})().catch((e) => { console.error(e); process.exit(1); });
