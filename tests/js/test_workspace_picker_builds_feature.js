// tests/js/test_workspace_picker_builds_feature.js — run: node tests/js/test_workspace_picker_builds_feature.js
//
// Step P3 F3: the workspace picker's remote rows come from /api/source/builds, the `builds` feature. With
// the features KNOWN and lacking `builds` it sends no builds request (it would only 404); known with
// `builds`, unknown, or no BackendFeatures on the page: it asks, as before.
const assert = require('assert');
const wp = require('../../vivarium_workbench/static/workspace-picker.js');

function fakeFetch(log, builds) {
  return function (url) {
    log.push(url);
    if (url === '/api/workspaces') return Promise.resolve({ ok: true, json: () => Promise.resolve({ workspaces: [{ name: 'a' }] }) });
    if (url === '/api/source/builds') {
      if (builds === 'fail') return Promise.reject(new Error('down'));
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ builds: [{ simulator_id: 3, repo: 'r' }] }) });
    }
    return Promise.resolve({ ok: false, status: 404 });
  };
}
function features(answer) { return { ready: () => Promise.resolve(answer) }; }

async function run() {
  let log = [];
  let res = await wp.loadSources(fakeFetch(log), features({ features: ['analyses'], known: true }));
  assert.deepStrictEqual(log, ['/api/workspaces'], 'known without builds: no builds request');
  assert.strictEqual(res[1], null);
  assert.deepStrictEqual(res[0], { workspaces: [{ name: 'a' }] });

  log = [];
  res = await wp.loadSources(fakeFetch(log), features({ features: [], known: true }));
  assert.ok(!log.includes('/api/source/builds'), 'known and empty: no builds request');

  log = [];
  res = await wp.loadSources(fakeFetch(log), features({ features: ['builds'], known: true }));
  assert.ok(log.includes('/api/source/builds'), 'known with builds: asks');
  assert.strictEqual(res[1].builds[0].simulator_id, 3);

  log = [];
  res = await wp.loadSources(fakeFetch(log), features({ features: [], known: false }));
  assert.ok(log.includes('/api/source/builds'), 'unknown: asks, as before');

  log = [];
  res = await wp.loadSources(fakeFetch(log), undefined);
  assert.ok(log.includes('/api/source/builds'), 'no BackendFeatures: asks, as before');

  log = [];
  res = await wp.loadSources(fakeFetch(log), { ready: () => Promise.reject(new Error('x')) });
  assert.ok(log.includes('/api/source/builds'), 'a rejecting ready(): unknown, asks');

  log = [];
  res = await wp.loadSources(fakeFetch(log, 'fail'), features({ features: ['builds'], known: true }));
  assert.strictEqual(res[1], null, 'a failed builds request is "no builds", never a rejection');

  // The features wait is bounded: no answer within the wait counts as unknown (ask, as before), and the
  // local rows (onLocal) never wait on the features at all.
  log = [];
  const order = [];
  const t0 = Date.now();
  res = await wp.loadSources(fakeFetch(log), { ready: () => new Promise(() => {}) },
                             (ws) => order.push(['local', ws, Date.now() - t0]), 50);
  assert.ok(log.includes('/api/source/builds'), 'features never answer: builds requested after the wait');
  assert.strictEqual(res[1].builds[0].simulator_id, 3);
  assert.strictEqual(order.length, 1, 'onLocal fired once');
  assert.deepStrictEqual(order[0][1], { workspaces: [{ name: 'a' }] });
  assert.ok(order[0][2] < 50, 'local rows did not wait on the features: ' + order[0][2] + 'ms');
  assert.ok(Date.now() - t0 >= 45, 'builds waited (bounded) for the features');

  // A late answer (after the wait) does not cancel the builds request already decided.
  log = [];
  let late;
  res = await wp.loadSources(fakeFetch(log), { ready: () => new Promise((r) => { late = r; }) }, null, 20);
  late({ features: [], known: true });
  assert.ok(log.includes('/api/source/builds'), 'late answer: asked as unknown');

  // The default wait is the Source panel's 1.5 s.
  log = [];
  const t1 = Date.now();
  res = await wp.loadSources(fakeFetch(log), { ready: () => new Promise(() => {}) });
  const waited = Date.now() - t1;
  assert.ok(waited >= 1400 && waited < 3000, 'default bounded wait ~1.5 s: ' + waited);
  assert.ok(log.includes('/api/source/builds'));

  console.log('test_workspace_picker_builds_feature.js: all assertions passed');
}

run().catch((e) => { console.error(e); process.exit(1); });
