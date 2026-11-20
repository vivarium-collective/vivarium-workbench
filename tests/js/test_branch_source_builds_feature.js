// tests/js/test_branch_source_builds_feature.js — run: node tests/js/test_branch_source_builds_feature.js
//
// Step P3 F3 (docs/backend-adapters.md §2.1): the Source panel's Cloud scope, build rows and the
// /api/source/builds request, and VivEnv's Cloud scope (which the loom and the composite cards read),
// all need the `builds` feature. Known WITHOUT `builds`: no Cloud toggle, no "Build on cloud", no builds
// or remote-health request, a held Cloud scope reads Local. Known WITH `builds`, or UNKNOWN (the features
// request failed twice): exactly as before the step.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const STATIC = path.join(__dirname, '../../vivarium_workbench/static');
const FEATURES_SRC = fs.readFileSync(path.join(STATIC, 'backend-features.js'), 'utf8');
const SOURCE_SRC = fs.readFileSync(path.join(STATIC, 'branch-source.js'), 'utf8');

// ── a DOM just big enough for branch-source.js's _render() ──────────────────
class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.style = {}; this.attrs = {}; this.listeners = {};
    this._text = ''; this.id = ''; this.className = ''; this.disabled = false; this.title = ''; this.value = '';
    this.classList = { add() {}, remove() {} };
  }
  appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
  set innerHTML(_v) { this.children = []; this._text = ''; }
  get innerHTML() { return ''; }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  setAttribute(k, v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  remove() {}
  walk(fn) { fn(this); this.children.forEach((c) => c.walk(fn)); }
}

function find(root, pred) {
  const out = [];
  root.walk((e) => { if (pred(e)) out.push(e); });
  return out;
}

// answers: url → body | {status} | 'fail' | a Promise (deferred)
function load({ features, mount = true, currentPath = '/ws/build-cache/sim7-abcdef0', snapshot = false }) {
  const calls = [];
  const events = [];
  const host = new El('div'); host.id = 'viv-branch-source';
  const helpItems = [new El('li'), new El('span')];
  const docListeners = {};
  const document = {
    createElement: (t) => new El(t),
    createTextNode: (t) => { const e = new El('#text'); e.textContent = t; return e; },
    getElementById(id) {
      if (!mount) return null;
      if (id === 'viv-branch-source') return host;
      const hit = find(host, (e) => e.id === id);
      return hit[0] || null;
    },
    querySelectorAll(sel) { return sel === '[data-feature="builds"]' ? helpItems : []; },
    addEventListener(t, f) { (docListeners[t] = docListeners[t] || []).push(f); },
  };
  function respond(body, status) {
    return Promise.resolve({ ok: (status || 200) < 400, status: status || 200, json: () => Promise.resolve(body) });
  }
  const win = {
    __DASH_CONFIG__: { mode: snapshot ? 'snapshot' : 'local-server' },
    document,
    setTimeout, clearTimeout, setInterval, clearInterval, Promise, console,
    Event: class Event { constructor(type) { this.type = type; } },
    dispatchEvent(e) { events.push(e.type); },
    sessionStorage: { setItem() {} },
    location: { origin: 'http://localhost', reload() {} },
    open() {},
    alert() {},
    fetch(url) {
      calls.push(url);
      if (url === '/api/backend/features') {
        if (features === 'fail') return Promise.reject(new Error('down'));
        if (features && typeof features.then === 'function') return features.then((f) => respond({ features: f }));
        return respond({ features });
      }
      if (url === '/api/workspaces') {
        return respond({ current: { name: 'ws', path: currentPath },
                         workspaces: [{ name: 'ws', path: currentPath, repo: 'ws', branch: 'main',
                                        commit: 'abcdef0123', status: 'current' }] });
      }
      if (url === '/api/source/builds') {
        return respond({ builds: [{ simulator_id: 7, repo: 'ws', repo_url: 'https://g/ws', branch: 'main',
                                    commit: 'abcdef0123', created_at: '2026-11-01' }] });
      }
      if (url === '/api/source/remote-health') return respond({ configured: true, reachable: true, base_url: 'http://b' });
      return respond({}, 404);
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(FEATURES_SRC, win);
  vm.runInContext(SOURCE_SRC, win);
  return { win, host, calls, events, helpItems, docListeners };
}

const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

function buttonsTexts(host) {
  return find(host, (e) => e.tagName === 'button').map((e) => e._text);
}

const tests = [];
function test(name, fn) { tests.push([name, fn]); }

test('features known without builds: no Cloud scope, no build rows, no builds request', async () => {
  const { win, host, calls, helpItems } = load({ features: ['analyses', 'artifacts'] });
  await win._renderBranchSource();
  await tick(10);
  assert.ok(!calls.includes('/api/source/builds'), 'no /api/source/builds request: ' + calls);
  assert.ok(!calls.includes('/api/source/remote-health'), 'no remote-health request: ' + calls);
  const texts = buttonsTexts(host);
  assert.ok(!texts.includes('Cloud') && !texts.includes('Local'), 'no scope toggle: ' + texts);
  assert.ok(!texts.includes('Build on cloud'), 'no Build on cloud: ' + texts);
  assert.strictEqual(find(host, (e) => e.id === 'viv-bs-build').length, 0);
  assert.ok(texts.includes('Use this environment') && texts.includes('Open in new tab'), 'local actions stay');
  assert.ok(!/Cloud uses/.test(host.textContent), 'subtitle does not offer Cloud');
  // the active source is a materialized build (would have opened on Cloud): it reads Local
  assert.strictEqual(win.VivEnv.scope(), 'local');
  assert.strictEqual(win.VivEnv.isCloud(), false);
  assert.strictEqual(win.VivEnv.runBuild(), null);
  // a card flipping the scope cannot reach Cloud, and asks for no builds
  win.VivEnv.setScope('remote');
  await tick(10);
  assert.strictEqual(win.VivEnv.isCloud(), false);
  assert.ok(!calls.includes('/api/source/builds'), 'still no builds request after setScope');
  helpItems.forEach((el) => assert.strictEqual(el.style.display, 'none', 'build-only help hidden'));
});

test('features known with builds: as before (Cloud toggle, build rows, builds request)', async () => {
  const { win, host, calls, helpItems } = load({ features: ['analyses', 'builds'] });
  await win._renderBranchSource();
  assert.ok(calls.includes('/api/source/builds'));
  assert.ok(calls.includes('/api/source/remote-health'));
  const texts = buttonsTexts(host);
  assert.ok(texts.includes('Local') && texts.includes('Cloud'), 'scope toggle: ' + texts);
  assert.ok(texts.includes('Build on cloud'));
  assert.ok(/Cloud uses/.test(host.textContent));
  assert.strictEqual(win.VivEnv.scope(), 'remote');
  assert.strictEqual(win.VivEnv.isCloud(), true);
  assert.strictEqual(win.VivEnv.runBuild().simulator_id, 7);
  helpItems.forEach((el) => assert.notStrictEqual(el.style.display, 'none'));
});

test('features unknown (fetch failed, then the retry): as before', async () => {
  const { win, host, calls } = load({ features: 'fail' });
  await win._renderBranchSource();
  assert.deepStrictEqual(calls.filter((u) => u === '/api/backend/features').length, 2, 'one retry');
  assert.strictEqual(win.BackendFeatures.known(), false);
  assert.ok(calls.includes('/api/source/builds'));
  const texts = buttonsTexts(host);
  assert.ok(texts.includes('Cloud') && texts.includes('Build on cloud'));
  assert.strictEqual(win.VivEnv.isCloud(), true);
});

test('no BackendFeatures on the page: as before', async () => {
  const { win, host, calls } = load({ features: ['builds'] });
  delete win.BackendFeatures;
  await win._renderBranchSource();
  assert.ok(calls.includes('/api/source/builds'));
  assert.ok(buttonsTexts(host).includes('Cloud'));
  assert.strictEqual(win.VivEnv.isCloud(), true);
});

test('VivEnv alone: a held Cloud scope stands until the features say no builds', async () => {
  let release;
  const deferred = new Promise((r) => { release = r; });
  const { win, calls, events } = load({ features: deferred, mount: false });
  win.VivEnv.setScope('remote');                     // e.g. a composite card's run-target chip
  assert.strictEqual(win.VivEnv.isCloud(), true, 'unknown → the held scope stands');
  assert.strictEqual(win.VivEnv.scope(), 'remote');
  release(['analyses']);
  await tick(10);
  assert.strictEqual(win.VivEnv.isCloud(), false, 'known without builds → Local');
  assert.strictEqual(win.VivEnv.scope(), 'local');
  assert.ok(events.filter((e) => e === 'viv:envchange').length >= 2, 'cards told the scope changed');
  assert.ok(!calls.includes('/api/source/builds'));
});

test('VivEnv alone: with builds the held Cloud scope stays', async () => {
  const { win } = load({ features: ['builds'], mount: false });
  win.VivEnv.setScope('remote');
  await tick(10);
  assert.strictEqual(win.VivEnv.isCloud(), true);
});

test('the panel wording is neutral', () => {
  assert.ok(!/sms-api|SMS_API_BASE|GovCloud/i.test(SOURCE_SRC), 'no SMS nouns in branch-source.js');
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('ok  ' + name); }
    catch (e) { failed++; console.error('FAIL ' + name + '\n' + (e && e.stack || e)); }
  }
  if (failed) { console.error(failed + ' failed'); process.exit(1); }
  console.log('test_branch_source_builds_feature.js: all ' + tests.length + ' passed');
  process.exit(0);
})();
