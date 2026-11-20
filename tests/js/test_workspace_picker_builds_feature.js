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

  console.log('test_workspace_picker_builds_feature.js: all assertions passed');
}

run().catch((e) => { console.error(e); process.exit(1); });
