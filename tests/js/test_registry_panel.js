// run with: node tests/js/test_registry_panel.js
// The Registry panel runs init() on load against VivPanelDock + DataSource; we
// stub both (and a minimal DOM) so init completes, then assert the public API,
// the popout hook, that it fetched the registry, and the source wiring.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const noop = () => {};
function stubEl() {
  const e = {
    _html: '',
    classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    addEventListener: noop, removeEventListener: noop,
    setAttribute: noop, getAttribute: () => null,
    appendChild: noop, removeChild: noop,
    querySelector: () => stubEl(), querySelectorAll: () => [],
    closest: () => null, focus: noop, scrollIntoView: noop,
    hidden: false, parentNode: null, style: {},
  };
  Object.defineProperty(e, 'innerHTML', { get() { return this._html; }, set(v) { this._html = v; } });
  return e;
}

let registryFetched = 0, compositesFetched = 0;
const document = {
  readyState: 'complete',
  addEventListener: noop,
  getElementById: () => stubEl(),
  querySelector: () => stubEl(),
  createElement: () => stubEl(),
  get activeElement() { return null; },
};
const ctl = {
  open: noop, close: noop, toggle: noop, isOpen: () => false, didDrag: () => false,
  openDockMenu: noop, collapse: noop, expand: noop, toggleCollapsed: noop,
};
const window = {
  document,
  addEventListener: noop,
  setInterval: () => 0, clearInterval: noop,
  matchMedia: () => ({ matches: false }),
  location: { search: '' },
  localStorage: { getItem: () => null, setItem: noop },
  VivPanelDock: { make: () => ctl, popout: () => ({ closed: false }) },
  DataSource: {
    loadRegistry: () => { registryFetched++; return Promise.resolve({ processes: [
      { address: 'pkg.a.Foo', name: 'Foo', kind: 'process', source: 'in_workspace', description: 'a process' },
      { address: 'pkg.analyses.Bar', name: 'Bar', kind: 'step', source: 'framework', description: '' },
    ] }); },
    loadComposites: () => { compositesFetched++; return Promise.resolve({ composites: [
      { id: 'pkg.composites.baz', name: 'Baz', read_only: false, description: 'a composite' },
    ] }); },
  },
  _showRegistryCard: noop,
};
const sandbox = { window, document, console, setTimeout, clearTimeout };
vm.createContext(sandbox);

const file = path.join(__dirname, '../../vivarium_workbench/static/registry-panel.js');
const src = fs.readFileSync(file, 'utf8');
vm.runInContext(src, sandbox);

// init() ran on load (readyState 'complete'): the public API + popout hook exist.
assert.ok(window.RegistryPanel && typeof window.RegistryPanel === 'object', 'RegistryPanel defined');
['toggle', 'open', 'close', 'dockMenu', 'collapse', 'expand', 'isOpen', 'refresh'].forEach(function (m) {
  assert.strictEqual(typeof window.RegistryPanel[m], 'function', 'RegistryPanel.' + m + ' is a function');
});
assert.strictEqual(typeof window._openRegistryPanel, 'function', '_openRegistryPanel popout hook');

// it fetched BOTH registry sources to build its list.
assert.ok(registryFetched >= 1, 'loadRegistry called');
assert.ok(compositesFetched >= 1, 'loadComposites called');

// source wiring (the behaviour a full DOM test would exercise).
assert.ok(src.includes("key: 'registry'"), "VivPanelDock key 'registry'");
assert.ok(src.includes('VivPanelDock.make'), 'builds a dock panel');
assert.ok(src.includes('_showRegistryCard'), 'row click focuses the item');
assert.ok(src.includes("label: 'Registry'"), "panel labelled 'Registry'");
assert.ok(src.includes("['process', 'Processes']"), 'Processes group label preserved');

console.log('ok - registry-panel: API, popout hook, registry fetch, wiring');
