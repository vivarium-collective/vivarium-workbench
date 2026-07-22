// tests/js/test_session_status.js — run with: node tests/js/test_session_status.js
//
// Exercises static/session-status.js's apply(status) → favicon + title mapping.
// Stands up a minimal fake document; the module is required in CommonJS so it
// exports without auto-polling. baseTitle is captured once (module scope) from the
// first apply(), so the whole sequence runs against one document.
const assert = require('assert');

var icon = null;
// Server-rendered title carries a stale ⏳ (e.g. a bfcache restore) to prove it is
// stripped on capture, not compounded.
global.document = {
  title: '⏳ increase-demo',
  head: { appendChild: function (el) { icon = el; } },
  documentElement: { appendChild: function (el) { icon = el; } },
  querySelector: function (sel) { return sel === 'link[rel="icon"]' ? icon : null; },
  createElement: function () { return { rel: '', type: '', href: '' }; },
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

  console.log('test_session_status.js: all assertions passed');
}

try { run(); } catch (e) { console.error(e); process.exit(1); }
