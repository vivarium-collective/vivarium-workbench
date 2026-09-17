// tests/js/test_session_status.js — run with: node tests/js/test_session_status.js
//
// Exercises static/session-status.js's apply(status) → favicon + title mapping.
// Stands up a minimal fake document; the module is required in CommonJS so it
// exports without auto-polling. baseTitle is captured once (module scope) from the
// first apply(), so the whole sequence runs against one document.
const assert = require('assert');

var icon = null;

// Minimal fake DOM node — enough for both the favicon <link> path and the
// failed-state panel (createElement tree + body + getElementById).
function makeNode(tag) {
  return {
    tagName: tag, children: [], parentNode: null,
    rel: '', type: '', href: '', className: '', id: '', textContent: '',
    style: { cssText: '' },
    setAttribute: function () {},
    addEventListener: function () {},
    appendChild: function (c) { c.parentNode = this; this.children.push(c); return c; },
    removeChild: function (c) { var i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentNode = null; },
  };
}
function findById(node, id) {
  if (node.id === id) return node;
  for (var i = 0; i < node.children.length; i++) { var f = findById(node.children[i], id); if (f) return f; }
  return null;
}
function allText(node) {
  var t = node.textContent || '';
  for (var i = 0; i < node.children.length; i++) t += ' ' + allText(node.children[i]);
  return t;
}
function findByClass(node, cls) {
  if ((node.className || '').indexOf(cls) !== -1) return node;
  for (var i = 0; i < node.children.length; i++) { var f = findByClass(node.children[i], cls); if (f) return f; }
  return null;
}
var fakeBody = makeNode('body');
// Server-rendered title carries a stale ⏳ (e.g. a bfcache restore) to prove it is
// stripped on capture, not compounded.
global.document = {
  title: '⏳ increase-demo',
  body: fakeBody,
  head: { appendChild: function (el) { icon = el; } },
  documentElement: { appendChild: function (el) { icon = el; } },
  querySelector: function (sel) { return sel === 'link[rel="icon"]' ? icon : null; },
  getElementById: function (id) { return findById(fakeBody, id); },
  createElement: function (tag) { return makeNode(tag); },
};

const sess = require('../../vivarium_workbench/static/session-status.js');

function href() { return icon ? icon.href : ''; }

function run() {
  // ready: base title captured + stale glyph stripped; workbench V mark.
  assert.strictEqual(sess.apply('ready'), 'ready', 'ready → ready');
  assert.strictEqual(document.title, 'increase-demo', 'stale ⏳ stripped on capture, not doubled');
  assert(href().indexOf('data:image/svg+xml,') === 0, 'favicon is an inline SVG data URI');
  assert(decodeURIComponent(href()).indexOf('>V<') !== -1, 'ready favicon is the V mark');

  // materializing: hourglass favicon + ⏳ prefix on the captured base title.
  assert.strictEqual(sess.apply('materializing'), 'preparing', 'materializing → preparing');
  assert.strictEqual(document.title, '⏳ increase-demo', 'preparing prefixes ⏳');
  assert(decodeURIComponent(href()).indexOf('⏳') !== -1, 'preparing favicon carries the hourglass');

  // failed: red mark + ⚠️ prefix.
  assert.strictEqual(sess.apply('failed'), 'failed', 'failed → failed');
  assert.strictEqual(document.title, '⚠️ increase-demo', 'failed prefixes ⚠️');

  // back to ready: prefix cleared, no compounding.
  assert.strictEqual(sess.apply('ready'), 'ready', 'settles back to ready');
  assert.strictEqual(document.title, 'increase-demo', 'ready clears the prefix');

  // unknown status degrades to ready.
  assert.strictEqual(sess.apply('bogus'), 'ready', 'unknown status → ready');

  // favicon element is reused (single <link rel="icon">), not duplicated.
  assert(icon && typeof icon.href === 'string', 'one favicon link element is maintained');

  // ── failed-state panel (slice 3c: reason + tail + Retry; "nothing silently
  //    disappears") ──────────────────────────────────────────────────────────
  var panel = sess.renderFailure({
    status: 'failed', error: 'clone failed: bad ref refs/heads/nope',
    tail: 'uv sync: error\nno solution found', repo: 'https://github.com/x/y', ref: 'main',
  });
  assert(document.getElementById('viv-session-failure') === panel, 'failure panel is mounted');
  var txt = allText(panel);
  assert(txt.indexOf('clone failed: bad ref') !== -1, 'panel shows the failure reason');
  assert(txt.indexOf('uv sync: error') !== -1, 'panel shows the uv/git tail');
  assert(findByClass(panel, 'viv-sf-retry'), 'panel has a Retry control');

  // Dismiss/clear removes it.
  sess.clearFailure();
  assert(document.getElementById('viv-session-failure') === null, 'clearFailure removes the panel');

  // Even with NO reason reported, the panel still renders (nothing silently
  // disappears) with a fallback message.
  var p2 = sess.renderFailure({ status: 'failed' });
  assert(allText(p2).toLowerCase().indexOf('fail') !== -1, 'panel renders even with no reason');
  sess.clearFailure();

  // ── in-page materialization progress (item 70 phase 2) ───────────────────
  var mount = makeNode('div'); mount.id = 'viv-materialize-progress';
  fakeBody.appendChild(mount);

  // Without window.ProgressTrack (e.g. the script failed to load), degrades
  // to hidden — never throws, never shows a half-built widget.
  sess.renderMaterializeProgress({ status: 'materializing', phase: 'cloning' });
  assert.strictEqual(mount.style.display, 'none', 'no ProgressTrack -> mount stays hidden');

  // With it available: real phase -> real stage state, no invented timer.
  global.window = { ProgressTrack: require('../../vivarium_workbench/static/progress-track.js') };
  sess.renderMaterializeProgress({ status: 'materializing', phase: 'cloning' });
  assert.notStrictEqual(mount.style.display, 'none', 'mount shown while materializing');
  assert(mount.innerHTML.indexOf('ptrack-seg-done') !== -1, 'queued renders as done once cloning is active');
  assert(mount.innerHTML.indexOf('ptrack-seg-active') !== -1, 'cloning renders as the active segment');
  assert(mount.innerHTML.indexOf('ptrack-spin') !== -1, 'active phase shows the spinner');

  // ready/failed/no-response all hide it again (each has its own treatment
  // elsewhere — favicon, or the failure panel above).
  sess.renderMaterializeProgress({ status: 'ready' });
  assert.strictEqual(mount.style.display, 'none', 'ready -> progress panel hidden');
  sess.renderMaterializeProgress(null);
  assert.strictEqual(mount.style.display, 'none', 'no response -> progress panel hidden');

  console.log('test_session_status.js: all assertions passed');
}

try { run(); } catch (e) { console.error(e); process.exit(1); }
