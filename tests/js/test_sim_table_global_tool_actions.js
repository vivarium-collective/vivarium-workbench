// tests/js/test_sim_table_global_tool_actions.js — run with: node tests/js/test_sim_table_global_tool_actions.js
// The global Runs page folds the Tools column away, so a row's matched launcher tools (row.matched_tools, e.g. a
// package's Explorer) appear in its ⌄ Actions menu, in the View group, after the run's own View actions. Rows with
// no matched tool keep the menu they had; the per-study Simulations tab (_actionList) gets no copy, since it has its
// own Tools column.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'sim-table.js'), 'utf8');
const noop = () => {};

const clicks = [];
function load() {
  const document = { addEventListener: (type, fn) => { if (type === 'click') clicks.push(fn); },
                     querySelectorAll: () => [], querySelector: () => null,
                     getElementById: () => null, createElement: () => ({ style: {}, setAttribute: noop }) };
  const window = { document, addEventListener: noop, confirm: () => true,
                   location: { pathname: '/', search: '', hash: '' } };
  vm.runInContext(SRC, vm.createContext({ window, document, console, setTimeout, clearTimeout, URLSearchParams,
                                          fetch: () => new Promise(noop) }));
  return window.SimTable;
}

const T = load();
const LAUNCH = '/api/analysis-viewer/pkg%3A%3Aexplorer/launch?study=s1&run=s1%3Ar1';
const EXPLORER = { id: 'pkg::explorer', label: 'Pkg Explorer', kind: 'launcher', launch_url: LAUNCH };
const LOCAL = { run_id: 'r1', status: 'completed', study_slug: 's1', store_path: '/ws/studies/s1/runs.db' };

// Group headers and item labels, in menu order.
function menu(html) {
  const out = [];
  const re = /sim-action-menu-header[^>]*>([^<]*)<|sim-action-menu-item[^>]*>([\s\S]*?)<\/div>/g;
  let m;
  const text = (s) => s.replace(/<[^>]*>/g, ' ').replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n))
    .replace(/\s+/g, ' ').trim();
  while ((m = re.exec(html))) out.push(m[1] ? '# ' + m[1] : text(m[2]));
  return out;
}

// 1. A row with a matched launcher: the tool is the last View entry, with its button and launch URL.
const withTool = T.globalActions(Object.assign({ matched_tools: [EXPLORER] }, LOCAL));
const items = menu(withTool);
const view = items.slice(items.indexOf('# View') + 1, items.indexOf('# Download'));
assert.strictEqual(view[view.length - 1], 'Pkg Explorer ↗ Open this run in Pkg Explorer', JSON.stringify(view));
assert.ok(withTool.includes('class="action-btn js-authoring tool-launch-btn"'), 'uses the Tools column button');
assert.ok(withTool.includes('data-launch-url="' + LAUNCH.replace(/&/g, '&amp;') + '"'), 'carries the launch URL');

// 2. A row without matched tools keeps its menu exactly.
const plain = T.globalActions(LOCAL);
assert.ok(!plain.includes('tool-launch-btn'));
assert.deepStrictEqual(menu(withTool).filter((s) => !s.startsWith('Pkg Explorer')), menu(plain));

// 3. Only launcher tools with a launch URL are listed (a deep-link tool already has its own page).
const deep = { id: 'pkg::page', label: 'Page', kind: 'embed-explorer', launch_url: '/x' };
const noUrl = { id: 'pkg::nourl', label: 'NoUrl', kind: 'launcher' };
assert.ok(!T.globalActions(Object.assign({ matched_tools: [deep, noUrl] }, LOCAL)).includes('tool-launch-btn'));

// 4. A run with no other actions still gets a menu holding the tool.
const bare = T.globalActions({ status: 'queued', matched_tools: [EXPLORER] });
assert.deepStrictEqual(menu(bare), ['# View', 'Pkg Explorer ↗ Open this run in Pkg Explorer']);

// 5. The per-study list (_actionList) is unchanged: that tab shows the tool in its Tools column.
assert.ok(!Array.from(T.actionList(Object.assign({ matched_tools: [EXPLORER] }, LOCAL)))
  .some((a) => a.html.includes('tool-launch-btn')));

// 6. Labels are escaped.
const evil = Object.assign({}, EXPLORER, { label: '<img src=x onerror=1>' });
assert.ok(!T.globalActions(Object.assign({ matched_tools: [evil] }, LOCAL)).includes('<img'));

// 7. While the launch is pending the button says it is opening (and is disabled), not a bare "…".
const btn = { textContent: 'Pkg Explorer ↗', disabled: false,
              getAttribute: (k) => (k === 'data-launch-url' ? LAUNCH : null) };
btn.closest = (sel) => (sel === '.tool-launch-btn' ? btn : null);
let stopped = false;
clicks.forEach((fn) => fn({ target: btn, stopPropagation: () => { stopped = true; } }));
assert.strictEqual(btn.textContent, 'Opening…');
assert.ok(btn.disabled && stopped);

console.log('ok — global Runs Actions menu lists matched launcher tools');
