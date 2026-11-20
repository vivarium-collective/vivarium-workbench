// tests/js/test_study_gallery_features.js — run: node tests/js/test_study_gallery_features.js
// Step P3 F2 (docs/backend-adapters.md §2.1): the study page's remote figure gallery
// (Visualizations tab, _loadRemoteFigures) and remote ptools list (Analyses tab,
// _loadRemoteAnalyses) ask /api/study-remote-figures only when the active backends
// declare `artifacts`. Features only HIDE this optional panel, so:
//   - declared `artifacts`            → fetch (as today);
//   - known and without `artifacts`   → no gallery request (providers off);
//   - unknown (features fetch failed) → fetch (as today);
//   - no BackendFeatures on the page  → fetch (as today);
//   - snapshot bundle                 → no features request, gallery behaviour as today.
// Runs the REAL study-detail.js and backend-features.js against a minimal fake DOM.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dir = path.join(__dirname, '../../vivarium_workbench/static');
const SD = fs.readFileSync(path.join(dir, 'study-detail.js'), 'utf8');
const BF = fs.readFileSync(path.join(dir, 'backend-features.js'), 'utf8');

function makeDom() {
  const byId = {};
  function el(tag) {
    const e = {
      tagName: (tag || 'div').toUpperCase(), style: {}, children: [], parentNode: null,
      dataset: {}, _id: '', textContent: '', innerHTML: '',
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
      appendChild(c) { c.parentNode = e; e.children.push(c); if (c._id) byId[c._id] = c; return c; },
      insertBefore(c) { return e.appendChild(c); },
      addEventListener() {}, removeEventListener() {},
      querySelector() { return null; }, querySelectorAll() { return []; },
    };
    Object.defineProperty(e, 'id', {
      get() { return e._id; }, set(v) { e._id = v; byId[v] = e; },
    });
    return e;
  }
  const body = el('body');
  const document = {
    readyState: 'loading', // keep the page's own DOMContentLoaded init out of this test
    body, head: el('head'), documentElement: el('html'),
    createElement: el,
    getElementById: id => byId[id] || null,
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
  };
  for (const id of ['native-gallery-panel', 'data-files']) { const a = el('div'); a.id = id; body.appendChild(a); }
  return { document, byId };
}

const GALLERY = {
  available: true, shown_sims: 1, total_completed_remote_sims: 1,
  sims: [{ simulation_id: 7, sim_name: 'sim7',
    analyses: [{ name: 'multigen', figures: ['viz/a.html'], ptools: ['ptools/a.tsv'], n_figures: 1, n_ptools: 1 }] }],
};

// features: a body for /api/backend/features, or 'fail' (both tries fail), or undefined (no backend-features.js).
function load({ features, snapshot }) {
  const dom = makeDom();
  const calls = [];
  const win = {
    document: dom.document,
    location: { hash: '', pathname: '/studies/demo', search: '' },
    _studyName: 'demo',
    _study: null,
    __DASH_CONFIG__: snapshot ? { mode: 'snapshot' } : { mode: 'local-server' },
    DataSource: { loadStudy: () => Promise.resolve(null) },
    addEventListener() {}, removeEventListener() {},
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    requestAnimationFrame: () => 0,
    console: { log() {}, warn() {}, error() {} }, Promise, JSON, Object, Array, String, Number, Math, Date, RegExp, Error,
    encodeURIComponent, decodeURIComponent, parseInt, parseFloat, isNaN, URLSearchParams,
    fetch(url) {
      calls.push(url);
      const p = url.split('?')[0];
      if (p === '/api/backend/features') {
        if (features === 'fail') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(features) });
      }
      if (p === '/api/study-remote-figures') {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(GALLERY) });
      }
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
    },
  };
  win.window = win;
  vm.createContext(win);
  if (features !== undefined) vm.runInContext(BF, win, { filename: 'backend-features.js' });
  vm.runInContext(SD, win, { filename: 'study-detail.js' });
  return { win, dom, calls };
}

const tick = () => new Promise(r => setImmediate(r));
async function settle() { for (let i = 0; i < 20; i++) await tick(); }

function galleryCalls(calls) { return calls.filter(u => u.startsWith('/api/study-remote-figures')).length; }
function featureCalls(calls) { return calls.filter(u => u.startsWith('/api/backend/features')).length; }

async function run(opts) {
  const ctx = load(opts);
  ctx.win._loadRemoteFigures();
  ctx.win._loadRemoteAnalyses();
  await settle();
  return ctx;
}

(async () => {
  // ── 1. providers on: `artifacts` declared → both panels fetch and render, as today ──
  {
    const { dom, calls } = await run({ features: { features: ['analyses', 'artifacts', 'builds', 'listing', 'sms-launch'] } });
    assert.strictEqual(galleryCalls(calls), 2, 'figures + analyses each ask once');
    assert.strictEqual(featureCalls(calls), 1, 'features fetched once (memoised)');
    assert.ok(/figure-card/.test(dom.byId['remote-figures-panel'].innerHTML), 'figures rendered');
    assert.ok(/a\.tsv/.test(dom.byId['remote-analyses-panel'].innerHTML), 'ptools listed');
    assert.ok(/Rendered on the remote backend/.test(dom.byId['remote-analyses-panel'].innerHTML), 'neutral wording');
    assert.ok(!/GovCloud/i.test(dom.byId['remote-analyses-panel'].innerHTML));
  }

  // ── 2. providers off: features known and empty → no gallery request, panels empty ──
  {
    const { win, dom, calls } = await run({ features: { features: [] } });
    assert.strictEqual(galleryCalls(calls), 0, 'no /api/study-remote-figures without `artifacts`');
    assert.ok(!dom.byId['remote-figures-panel'] || dom.byId['remote-figures-panel'].innerHTML === '');
    assert.ok(!dom.byId['remote-analyses-panel'] || dom.byId['remote-analyses-panel'].innerHTML === '');
    // Re-opening the tabs does not ask again either.
    win._loadRemoteFigures(); win._loadRemoteAnalyses(); await settle();
    assert.strictEqual(galleryCalls(calls), 0);
  }

  // ── 2b. known features without `artifacts` (e.g. only `listing`) → no gallery request ──
  {
    const { calls } = await run({ features: { features: ['listing', 'builds'] } });
    assert.strictEqual(galleryCalls(calls), 0);
  }

  // ── 3. unknown: the features fetch fails (and its one retry) → fetch as today ──
  {
    const { dom, calls } = await run({ features: 'fail' });
    assert.strictEqual(featureCalls(calls), 2, 'one try + one retry');
    assert.strictEqual(galleryCalls(calls), 2, 'unknown features keep today\'s behaviour');
    assert.ok(/figure-card/.test(dom.byId['remote-figures-panel'].innerHTML));
  }

  // ── 4. no backend-features.js on the page → fetch as today ──
  {
    const { calls } = await run({ features: undefined });
    assert.strictEqual(galleryCalls(calls), 2);
  }

  // ── 5. snapshot bundle: no features request; gallery behaviour unchanged (it asks, as today) ──
  {
    const { calls } = await run({ features: { features: ['artifacts'] }, snapshot: true });
    assert.strictEqual(featureCalls(calls), 0, 'snapshot never fetches features');
    assert.strictEqual(galleryCalls(calls), 2, 'snapshot gallery as today');
  }

  console.log('test_study_gallery_features OK');
})().catch(e => { console.error(e); process.exit(1); });
