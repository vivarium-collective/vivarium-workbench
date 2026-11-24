// tests/js/test_plugin_loader.js — run with: node tests/js/test_plugin_loader.js
// plugin-loader.js fetches /api/plugins/manifest, injects each plugin's css/js from
// /plugins/<name>/, and registers each panel via VivPanelDock.make. Minimal DOM stand-in.
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
globalThis.VivPanelDock = { make(opts) { made.push(opts); return { open() {}, close() {} }; } };
let gotCtx = null;
globalThis.ExampleHelloPanel = function (ctx) { gotCtx = ctx; };

const layout = el('div');
const rail = el('div');
const head = el('head');
globalThis.document = {
  head,
  documentElement: el('html'),
  readyState: 'complete',
  createElement: el,
  querySelector(sel) {
    if (sel === '.viv-layout') return layout;
    if (sel === '.viv-main') return el('div');
    if (sel === '.viv-rail') return rail;
    return null;
  },
};
const _ha = head.appendChild.bind(head);
head.appendChild = function (c) { injected.push(c); return _ha(c); };

const MANIFEST = [{
  name: 'example',
  css: ['/plugins/example/example.css'],
  js: ['/plugins/example/example.js'],
  panels: [{ id: 'example-hello', title: 'Hello', icon: 'beaker', group: 'Example', side: 'left', entry: 'ExampleHelloPanel' }],
}];
globalThis.fetch = async function (url) {
  assert.strictEqual(url, '/api/plugins/manifest', 'loader fetches the manifest');
  return { ok: true, json: async () => MANIFEST };
};

const { loadPlugins } = require('../../vivarium_workbench/static/plugin-loader.js');

(async () => {
  await loadPlugins();
  const css = injected.filter((e) => e.tagName === 'link');
  const js = injected.filter((e) => e.tagName === 'script');
  assert.strictEqual(css.length, 1, 'one css link injected');
  assert.strictEqual(css[0].href, '/plugins/example/example.css', 'css href namespaced under /plugins/<name>/');
  assert.strictEqual(js.length, 1, 'one js script injected');
  assert.strictEqual(js[0].src, '/plugins/example/example.js', 'js src namespaced under /plugins/<name>/');
  assert.strictEqual(made.length, 1, 'one panel registered via VivPanelDock.make');
  assert.strictEqual(made[0].key, 'example-hello', 'panel key is the spec id');
  assert.strictEqual(made[0].label, 'Hello', 'panel label is the spec title');
  // the clean panel contract: the entry is called with a context, not a bare element
  assert.ok(gotCtx && gotCtx.el, 'entry receives ctx.el (the panel body)');
  assert.strictEqual(gotCtx.name, 'example', 'ctx.name is the plugin name');
  assert.strictEqual(gotCtx.apiBase, '/api/plugins/example', 'ctx.apiBase is namespaced');
  assert.strictEqual(typeof gotCtx.api, 'function', 'ctx.api is a fetch helper');
  console.log('ok test_plugin_loader');
})().catch((e) => { console.error(e); process.exit(1); });
