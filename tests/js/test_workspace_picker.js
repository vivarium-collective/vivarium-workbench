// tests/js/test_workspace_picker.js — run with: node tests/js/test_workspace_picker.js
//
// Exercises the pure helpers of static/workspace-picker.js (the sleek header
// workspace switcher): filterWorkspaces() search + current-first sort, and
// statusMeta() status → dot/class mapping. The module short-circuits to
// module.exports in Node, so requiring it does not touch the DOM.
const assert = require('assert');
const wp = require('../../vivarium_workbench/static/workspace-picker.js');

function run() {
  var items = [
    { name: 'ecoli-v2', label: 'ecoli-v2', status: 'stopped' },
    { name: 'increase-demo', label: 'increase-demo', status: 'current' },
    { name: 'glycolysis-sweep', label: 'glycolysis-sweep', status: 'stopped' },
  ];

  // empty query → all, current first.
  var all = wp.filterWorkspaces(items, '');
  assert.strictEqual(all.length, 3, 'empty query returns all');
  assert.strictEqual(all[0].status, 'current', 'current workspace sorts first');
  assert.strictEqual(all[1].name, 'ecoli-v2', 'rest sort alphabetically (ecoli before glycolysis)');

  // substring filter (case-insensitive).
  var g = wp.filterWorkspaces(items, 'GLY');
  assert.strictEqual(g.length, 1, 'filter matches by case-insensitive substring');
  assert.strictEqual(g[0].name, 'glycolysis-sweep', 'filter selects the right workspace');

  // no match → empty.
  assert.strictEqual(wp.filterWorkspaces(items, 'zzz').length, 0, 'no match → empty list');

  // null-safe.
  assert.strictEqual(wp.filterWorkspaces(null, '').length, 0, 'null items → empty');

  // statusMeta → dot + class.
  assert.strictEqual(wp.statusMeta('current').cls, 'ready', 'current → ready');
  assert.strictEqual(wp.statusMeta('running').cls, 'ready', 'running → ready');
  assert.strictEqual(wp.statusMeta('stopped').cls, 'stopped', 'stopped → stopped');
  assert.strictEqual(wp.statusMeta('stale').cls, 'stale', 'stale → stale');
  assert.strictEqual(wp.statusMeta('missing').cls, 'missing', 'missing → missing');
  assert.strictEqual(wp.statusMeta('bogus').cls, 'stopped', 'unknown → stopped fallback');
  assert.strictEqual(typeof wp.statusMeta('current').dot, 'string', 'meta carries a dot glyph');
  assert.strictEqual(wp.statusMeta('remote').cls, 'remote', 'remote → remote (purple cloud dot)');

  // normalizeRemoteBuilds — reduces sms-api's flat build history to one row
  // per distinct repo (its most recent build), previously reachable only
  // through the separate Source panel.
  var builds = [
    { simulator_id: 1, repo: 'v2ecoli', branch: 'main', commit: 'aaa1111', created_at: '2026-01-01T00:00:00' },
    { simulator_id: 2, repo: 'v2ecoli', branch: 'main', commit: 'bbb2222', created_at: '2026-02-01T00:00:00' },
    { simulator_id: 3, repo: 'sms-ecoli', branch: 'pilot/x', commit: 'ccc3333', created_at: '2026-01-15T00:00:00' },
  ];
  var remote = wp.normalizeRemoteBuilds(builds);
  assert.strictEqual(remote.length, 2, 'one row per distinct repo');
  var v2 = remote.filter(function (r) { return r.path === 'remote:v2ecoli'; })[0];
  assert.ok(v2, 'v2ecoli row present');
  assert.strictEqual(v2.simulator_id, 2, 'picks the LATEST build for the repo (by created_at), not the first');
  assert.strictEqual(v2.kind, 'remote', 'tagged kind: remote');
  assert.strictEqual(v2.status, 'remote', 'status: remote (drives the purple cloud dot)');
  assert.strictEqual(v2.name, null, 'no bindable ?workspace= name — remote builds use ?build=<id>');
  assert.ok(v2.label.indexOf('v2ecoli') !== -1 && v2.label.indexOf('#2') !== -1,
    'label mentions the repo and build number');

  assert.strictEqual(wp.normalizeRemoteBuilds([]).length, 0, 'empty builds → empty list');
  assert.strictEqual(wp.normalizeRemoteBuilds(null).length, 0, 'null builds → empty list, not a crash');
  assert.strictEqual(
    wp.normalizeRemoteBuilds([{ simulator_id: 1, repo: null }]).length, 0,
    'a build record with no repo is skipped, not crashed on'
  );

  // collapseByRepo — one row per repo (first of each group), current always kept.
  var rows = [
    { name: 'a', repo: 'r1', status: 'current', path: '/a' },
    { name: 'b', repo: 'r1', status: 'stopped', path: '/b' },
    { name: 'c', repo: 'r1', status: 'stopped', path: '/c' },
    { name: 'd', repo: 'r2', status: 'stopped', path: '/d' },
  ];
  var col = wp.collapseByRepo(rows);
  assert.strictEqual(col.rows.length, 2, 'two repos → two rows');
  assert.strictEqual(col.hidden, 2, 'two extra r1 worktrees hidden');
  assert.ok(col.rows.some(function (w) { return w.path === '/a'; }), 'current r1 worktree kept');
  assert.ok(col.rows.some(function (w) { return w.path === '/d'; }), 'r2 kept');
  // repoKey falls back to remote:<repo> path, then name
  assert.strictEqual(wp.repoKey({ path: 'remote:foo' }), 'foo', 'remote path → repo key');
  assert.strictEqual(wp.repoKey({ name: 'bar' }), 'bar', 'name fallback');

  console.log('test_workspace_picker.js: all assertions passed');
}

try { run(); } catch (e) { console.error(e); process.exit(1); }
