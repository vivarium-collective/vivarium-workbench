// tests/js/test_toast.js — run with: node tests/js/test_toast.js
// _showToast must exist (call sites fall back to a page-freezing alert() without it), render the
// message as text (never markup), dismiss neutral toasts on its own or on a click, and keep error
// toasts until the user dismisses them. Minimal DOM stand-in: only what toast.js touches.
const assert = require('assert');

function el(tag) {
  return {
    tagName: tag, children: [], parentNode: null, style: {}, attrs: {}, textContent: '',
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; return c; },
  };
}
const body = el('body');
globalThis.document = { body, documentElement: el('html'), createElement: el };
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };

const { showToast } = require('../../vivarium_workbench/static/toast.js');
assert.strictEqual(typeof globalThis._showToast, 'function', 'toast.js defines the global the call sites check for');

// The live region exists before any toast, so screen readers announce the first one.
assert.strictEqual(body.children.length, 1, 'live region created when the script loads');
const host = body.children[0];
assert.strictEqual(host.attrs.role, 'status', 'host is a polite live region');
assert.strictEqual(host.attrs['aria-live'], 'polite');

const t = showToast('<img src=x onerror=alert(1)> saved', { durationMs: 1000 });
assert.strictEqual(host.children[0], t, 'toast is shown inside the host');
assert.strictEqual(t.textContent, '<img src=x onerror=alert(1)> saved', 'message is set as text, not markup');
assert.strictEqual(t.attrs.role, undefined, 'a neutral toast is announced politely by the host');
assert.strictEqual(timers[0].ms, 1000, 'durationMs honoured');
timers[0].fn();
assert.strictEqual(host.children.length, 0, 'toast removes itself after its duration');

const t2 = showToast('second');
assert.strictEqual(body.children.length, 1, 'later toasts reuse the one host');
assert.strictEqual(timers[1].ms, 4500, 'neutral default duration');
assert.ok(t2.style.cssText.includes('#1f2937'), 'neutral colour by default');
t2.onclick();
assert.strictEqual(host.children.length, 0, 'a click dismisses it');

// An error replaces an alert() the user had to acknowledge: it stays until clicked.
const nTimers = timers.length;
const bad = showToast('Land failed: 500', { danger: true });
assert.ok(bad.style.cssText.includes('#dc2626'), 'danger is red');
assert.strictEqual(bad.attrs.role, 'alert', 'danger is announced at once');
assert.strictEqual(timers.length, nTimers, 'a danger toast schedules no auto-dismiss');
bad.onclick();
assert.strictEqual(host.children.length, 0, 'a danger toast is dismissed by a click');

showToast('stays', { durationMs: 0 });
assert.strictEqual(timers.length, nTimers, 'durationMs: 0 keeps the toast (not the default 4500)');
assert.strictEqual(showToast(null).textContent, '', 'null message renders empty, not "null"');

console.log('test_toast.js: ok');
