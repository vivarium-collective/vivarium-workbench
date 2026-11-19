// tests/js/test_sms_launch_hook.js — run: node tests/js/test_sms_launch_hook.js
// The vEcoli/SMS launch UI is quarantined in static/sms-launch.js; study-detail.js
// only calls window.SmsLaunch.mount(...) when that file is loaded. This runs the
// REAL study-detail.js (and sms-launch.js) against a minimal fake DOM, with and
// without the plugin, and checks: the hook is safe when absent ("Run current spec"
// goes straight to the local confirm, no remote calls, no Dispatch-composite
// button); present, it keeps today's gating (button hidden until remote-health says
// configured, never in a snapshot) and today's pinned / not-pinned dispatch.
// Also guards the quarantine itself: no vEcoli launch terms in study-detail.js.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dir = path.join(__dirname, '../../vivarium_workbench/static');
const SD = fs.readFileSync(path.join(dir, 'study-detail.js'), 'utf8');
const SMS = fs.readFileSync(path.join(dir, 'sms-launch.js'), 'utf8');
const TPL = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/templates/study-detail.html'), 'utf8');

// ── a minimal DOM: enough for study-detail.js to load and the launch paths to run ──
function makeDom() {
  const byId = {};
  function el(tag) {
    const e = {
      tagName: (tag || 'div').toUpperCase(), style: {}, children: [], parentNode: null,
      dataset: {}, _id: '', _listeners: {}, textContent: '', innerHTML: '', value: '',
      className: '', disabled: false, title: '', type: '',
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
      appendChild(c) { c.parentNode = e; e.children.push(c); if (c._id) byId[c._id] = c; return c; },
      insertBefore(c) { return e.appendChild(c); },
      remove() { if (e.parentNode) e.parentNode.children = e.parentNode.children.filter(x => x !== e); if (e._id) delete byId[e._id]; },
      addEventListener(t, fn) { (e._listeners[t] = e._listeners[t] || []).push(fn); },
      removeEventListener() {},
      querySelector() { return null; }, querySelectorAll() { return []; },
      focus() {}, click() { (e._listeners.click || []).forEach(fn => fn({ target: e })); },
    };
    Object.defineProperty(e, 'id', {
      get() { return e._id; }, set(v) { e._id = v; byId[v] = e; },
    });
    return e;
  }
  const body = el('body');
  const docListeners = {};
  const document = {
    readyState: 'loading', // keep the page's own DOMContentLoaded init out of this test
    body, head: el('head'), documentElement: el('html'),
    createElement: el,
    getElementById: id => byId[id] || null,
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener(t, fn) { (docListeners[t] = docListeners[t] || []).push(fn); },
    removeEventListener() {},
  };
  const actions = el('div'); actions.id = 'sim-run-actions'; body.appendChild(actions);
  return { document, byId, body, actions, docListeners };
}

function load({ withPlugin, routes, snapshot, preload }) {
  const dom = makeDom();
  const calls = [];
  const win = {
    document: dom.document,
    location: { hash: '', pathname: '/studies/demo', search: '' },
    _studyName: 'demo',
    _study: { baseline: [{ params: { n_generations: 2, n_seeds: 3 } }] },
    __DASH_CONFIG__: snapshot ? { mode: 'snapshot' } : undefined,
    DataSource: { loadStudy: () => Promise.resolve(win._study) },
    addEventListener() {}, removeEventListener() {},
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    requestAnimationFrame: () => 0,
    alert(m) { throw new Error('native alert: ' + m); },
    confirm(m) { throw new Error('native confirm: ' + m); },
    console: { log() {}, warn() {}, error() {} }, Promise, JSON, Object, Array, String, Number, Math, Date, RegExp, Error,
    encodeURIComponent, decodeURIComponent, parseInt, parseFloat, isNaN, URLSearchParams,
    fetch(url, opts) {
      const method = (opts && opts.method) || 'GET';
      const body = opts && opts.body ? JSON.parse(opts.body) : null;
      calls.push({ method, url, body });
      const key = method + ' ' + url.split('?')[0];
      const r = (routes && routes[key]) || { status: 404, body: {} };
      return Promise.resolve({ status: r.status, ok: r.status < 400, json: () => Promise.resolve(r.body) });
    },
  };
  win.window = win;
  vm.createContext(win);
  if (preload) vm.runInContext(preload, win);
  if (withPlugin) vm.runInContext(SMS, win, { filename: 'sms-launch.js' });
  vm.runInContext(SD, win, { filename: 'study-detail.js' });
  return { win, dom, calls };
}

const tick = () => new Promise(r => setImmediate(r));
async function settle() { for (let i = 0; i < 20; i++) await tick(); }

// The in-page modal: the overlay study-detail.js appends to body; press its OK.
function lastModal(dom) {
  const overlay = dom.body.children[dom.body.children.length - 1];
  const box = overlay && overlay.children[0];
  if (!box) return null;
  const text = box.children[0].textContent;
  const buttons = box.children[1].children;
  return { text, ok: buttons[buttons.length - 1] };
}

(async () => {
  // ── 1. plugin ABSENT: hook is a no-op, the page is local-only ──
  {
    const { win, dom, calls } = load({
      withPlugin: false,
      routes: {
        'GET /api/source/remote-health': { status: 200, body: { configured: true } },
        'POST /api/study-run-baseline': { status: 202, body: { run_id: 'r1' } },
      },
    });
    assert.strictEqual(win.SmsLaunch, undefined);
    await settle();
    assert.strictEqual(dom.byId['study-run-composite'], undefined, 'no Dispatch composite button without the plugin');
    const p = win._dispatchCurrentSpecBaseline();
    await settle();
    const m = lastModal(dom);
    assert.ok(m && /CURRENT baseline spec/.test(m.text), 'local confirm shown');
    m.ok.click();
    const res = await p;
    assert.strictEqual(res.status, 202);
    const urls = calls.map(c => c.method + ' ' + c.url);
    assert.ok(!urls.some(u => u.includes('/api/remote-run-config')), 'no remote-run-config call without the plugin');
    assert.ok(!urls.some(u => u.includes('/api/remote-run-submit')));
    assert.ok(urls.includes('POST /api/study-run-baseline'));
  }

  // ── 1b. a BROKEN plugin (mount throws) must not take the page down ──
  {
    const { win, calls } = load({
      withPlugin: false,
      preload: 'window.SmsLaunch = { mount: function () { throw new Error("boom"); } };',
      routes: { 'POST /api/study-run-baseline': { status: 202, body: { run_id: 'r1' } } },
    });
    assert.strictEqual(typeof win._dispatchCurrentSpecBaseline, 'function', 'page still finished loading');
    // ...but "Run current spec" fails visibly; it never falls back to a local run.
    let err = null;
    await win._dispatchCurrentSpecBaseline().catch(e => { err = e; });
    assert.ok(err && /launch plugin/.test(err.message), 'dispatch rejects when the plugin failed to mount');
    assert.ok(!calls.some(c => c.url === '/api/study-run-baseline'), 'no local run after a broken plugin');
  }

  // ── 2. plugin PRESENT, remote configured, pinned: today's remote dispatch ──
  {
    const { win, dom, calls } = load({
      withPlugin: true,
      routes: {
        'GET /api/source/remote-health': { status: 200, body: { configured: true } },
        'GET /api/remote-run-config': { status: 200, body: { pinned: true, simulator_id: 7, repo_url: 'r', branch: 'b', commit: 'c' } },
        'POST /api/remote-run-submit': { status: 202, body: { simulation_id: 99 } },
      },
    });
    const btn = dom.byId['study-run-composite'];
    assert.ok(btn, 'mount created the Dispatch composite button');
    assert.strictEqual(btn.parentNode, dom.actions, 'in #sim-run-actions');
    assert.strictEqual(btn.style.display, 'none', 'hidden until remote-health answers');
    await settle();
    assert.strictEqual(btn.style.display, '', 'shown once remote-health says configured');
    const p = win._dispatchCurrentSpecBaseline();
    await settle();
    const m = lastModal(dom);
    assert.ok(m && /generations:\s+2/.test(m.text) && /seeds:\s+3/.test(m.text), 'pinned confirm shows the baseline sizing');
    m.ok.click();
    const res = await p;
    assert.strictEqual(res.status, 202);
    const submit = calls.find(c => c.url === '/api/remote-run-submit');
    assert.deepStrictEqual(submit.body, { study: 'demo', simulator_id: 7, num_generations: 2, num_seeds: 3 });
    assert.ok(!calls.some(c => c.url === '/api/study-run-baseline'));
  }

  // ── 3. plugin PRESENT, not configured / not pinned: button stays hidden, local run ──
  {
    const { win, dom, calls } = load({
      withPlugin: true,
      routes: {
        'GET /api/source/remote-health': { status: 200, body: { configured: false } },
        'GET /api/remote-run-config': { status: 200, body: { pinned: false } },
        'POST /api/study-run-baseline': { status: 202, body: { run_id: 'r2' } },
      },
    });
    await settle();
    assert.strictEqual(dom.byId['study-run-composite'].style.display, 'none');
    const p = win._dispatchCurrentSpecBaseline();
    await settle();
    lastModal(dom).ok.click();
    assert.strictEqual((await p).status, 202);
    const urls = calls.map(c => c.method + ' ' + c.url.split('?')[0]);
    assert.ok(urls.indexOf('GET /api/remote-run-config') < urls.indexOf('POST /api/study-run-baseline'),
      'remote-run-config is still asked first, as before');
  }

  // ── 4. snapshot: button never shown, remote-health never fetched ──
  {
    const { dom, calls } = load({ withPlugin: true, snapshot: true, routes: {} });
    await settle();
    assert.strictEqual(dom.byId['study-run-composite'].style.display, 'none');
    assert.ok(!calls.some(c => c.url.includes('/api/source/remote-health')));
  }

  // ── 5. the quarantine: launch terms live in sms-launch.js, not study-detail.js ──
  for (const term of ['n_generations', 'n_seeds', 'ParCa', 'multi_node_dispatch', 'mbp_dispatch',
    'nextflow_dispatch', 'AWS Batch', '_dispatchRemotePinned', '_dispatchRemoteComposite',
    'study-run-composite', '/api/remote-run-submit']) {
    assert.ok(!SD.includes(term), 'study-detail.js still mentions ' + term);
    assert.ok(SMS.includes(term), 'sms-launch.js should own ' + term);
  }
  assert.ok(!TPL.includes('study-run-composite'), 'the button is created by sms-launch.js, not the template');
  assert.ok(TPL.indexOf('src="/sms-launch.js"') !== -1
    && TPL.indexOf('src="/sms-launch.js"') < TPL.indexOf('src="/study-detail.js"'),
  'the study page loads sms-launch.js before study-detail.js');

  console.log('test_sms_launch_hook OK');
})().catch(e => { console.error(e); process.exit(1); });
