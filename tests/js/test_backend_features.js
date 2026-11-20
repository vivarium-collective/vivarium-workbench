// tests/js/test_backend_features.js — run: node tests/js/test_backend_features.js
// static/backend-features.js (step P3 F0): one memoised GET /api/backend/features; snapshot mode resolves
// {features: [], known: true} with no request; a failed fetch is retried once, then resolves known: false
// ("unknown", which is not "no features").
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/static/backend-features.js'), 'utf8');

function load({ snapshot, answers, basePath }) {
  const calls = [];
  const queue = (answers || []).slice();
  const win = {
    __DASH_CONFIG__: { mode: snapshot ? 'snapshot' : 'local-server', basePath: basePath || '' },
    fetch(url, opts) {
      calls.push(url);
      const next = queue.length ? queue.shift() : { throw: new Error('no answer queued') };
      if (next.throw) return Promise.reject(next.throw);
      return Promise.resolve({
        ok: next.status === undefined ? true : next.status < 400,
        status: next.status || 200,
        json: () => (next.badJson ? Promise.reject(new SyntaxError('bad')) : Promise.resolve(next.body)),
      });
    },
  };
  if (basePath) {
    win.DataSource = { apiUrl: (p) => basePath + p };
  }
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SRC, win);
  return { BF: win.BackendFeatures, calls };
}

const tests = [];
function test(name, fn) { tests.push([name, fn]); }

test('fetches once and memoises', async () => {
  const { BF, calls } = load({ answers: [{ body: { features: ['analyses', 'sms-launch'] } }] });
  assert.strictEqual(BF.known(), false);
  assert.strictEqual(BF.has('analyses'), false); // nothing known before ready() resolves
  const [a, b] = await Promise.all([BF.ready(), BF.ready()]);
  const c = await BF.ready();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(a)), { features: ['analyses', 'sms-launch'], known: true });
  assert.strictEqual(a, b);
  assert.strictEqual(a, c);
  assert.deepStrictEqual(calls, ['/api/backend/features']);
  assert.strictEqual(BF.known(), true);
  assert.strictEqual(BF.has('analyses'), true);
  assert.strictEqual(BF.has('builds'), false);
});

test('empty features are known, not unknown', async () => {
  const { BF } = load({ answers: [{ body: { features: [] } }] });
  const r = await BF.ready();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(r)), { features: [], known: true });
});

test('the base path is honoured through DataSource.apiUrl', async () => {
  const { BF, calls } = load({ basePath: '/workbench', answers: [{ body: { features: [] } }] });
  await BF.ready();
  assert.deepStrictEqual(calls, ['/workbench/api/backend/features']);
});

test('snapshot mode makes no request and knows there are no features', async () => {
  const { BF, calls } = load({ snapshot: true });
  const r = await BF.ready();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(r)), { features: [], known: true });
  assert.deepStrictEqual(calls, []);
  assert.strictEqual(BF.has('builds'), false);
});

test('one failure is retried', async () => {
  const { BF, calls } = load({ answers: [{ throw: new Error('net') }, { body: { features: ['builds'] } }] });
  const r = await BF.ready();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(r)), { features: ['builds'], known: true });
  assert.strictEqual(calls.length, 2);
});

test('two failures resolve unknown (known: false), never rejecting', async () => {
  const { BF, calls } = load({ answers: [{ status: 500, body: {} }, { throw: new Error('net') }] });
  const r = await BF.ready();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(r)), { features: [], known: false });
  assert.strictEqual(calls.length, 2);
  assert.strictEqual(BF.known(), false);
  await BF.ready();
  assert.strictEqual(calls.length, 2); // memoised: no third request
});

test('a malformed payload counts as a failure', async () => {
  const { BF, calls } = load({ answers: [{ badJson: true }, { body: { nope: 1 } }] });
  const r = await BF.ready();
  assert.strictEqual(r.known, false);
  assert.strictEqual(calls.length, 2);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log('  ok  ' + name);
    } catch (e) {
      failed += 1;
      console.log('  FAIL ' + name + '\n' + (e && e.stack));
    }
  }
  if (failed) process.exit(1);
})();
