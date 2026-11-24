// tests/js/test_plugin_loader_isolation.js — run with: node tests/js/test_plugin_loader_isolation.js
// One plugin's panel registration throwing must NOT abort later plugins (fail-soft,
// mirroring the backend's per-plugin isolation).
const assert = require('assert');

function el(tag) {
  return {
    tagName: tag, children: [], parentNode: null, style: {}, dataset: {},
    classList: { add() {}, remove() {} },
    setAttribute(k, v) { this[k] = v; },
    addEventListener() {},
    appendChild(c) {
      c.parentNode = this;
      this.children.push(c);
      if (c.tagName === 'script' && typeof c.onload === 'function') c.onload();
      return c;
    },
    querySelector() { return null; },
  };
}

const made = [];
const injected = [];
globalThis.VivPanelDock = {
  make(opts) {
    made.push(opts.key);
    if (opts.key === 'p1') throw new Error('boom from plugin a');
    return { open() {}, close() {} };
  },
};
globalThis.E1 = function () {};
globalThis.E2 = function () {};

const layout = el('div');
const rail = el('div');
const head = el('head');
globalThis.document = {
  head, documentElement: el('html'), readyState: 'complete', createElement: el,
  querySelector(sel) {
    if (sel === '.viv-layout') return layout;
    if (sel === '.viv-main') return el('div');
    if (sel === '.viv-rail') return rail;
    return null;
  },
};
const _ha = head.appendChild.bind(head);
head.appendChild = function (c) { injected.push(c); return _ha(c); };

const MANIFEST = [
  { name: 'a', css: [], js: ['/plugins/a/a.js'], panels: [{ id: 'p1', title: 'P1', icon: 'x', group: 'A', side: 'left', entry: 'E1' }] },
  { name: 'b', css: [], js: ['/plugins/b/b.js'], panels: [{ id: 'p2', title: 'P2', icon: 'x', group: 'B', side: 'left', entry: 'E2' }] },
];
globalThis.fetch = async function () { return { ok: true, json: async () => MANIFEST }; };

const { loadPlugins } = require('../../vivarium_workbench/static/plugin-loader.js');

(async () => {
  await loadPlugins();
  assert.ok(made.indexOf('p1') >= 0, 'first plugin panel was attempted');
  assert.ok(made.indexOf('p2') >= 0, 'second plugin still registered after the first threw');
  const js = injected.filter((e) => e.tagName === 'script').map((e) => e.src);
  assert.ok(js.indexOf('/plugins/b/b.js') >= 0, "second plugin's JS still injected");
  console.log('ok test_plugin_loader_isolation');
})().catch((e) => { console.error(e); process.exit(1); });
