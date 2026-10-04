// tests/js/test_study_theme_follow.js — run with: node tests/js/test_study_theme_follow.js
// The study page is an iframe; an already-open one must follow the outer page's
// theme toggle. The toggle writes localStorage 'viv.theme'; the browser then fires a
// 'storage' event in the iframe. This runs the page's REAL inline <head> scripts
// against a minimal window/document and replays that event.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(
  path.join(__dirname, '../../vivarium_workbench/templates/study-detail.html'), 'utf8');
const head = html.slice(0, html.indexOf('</head>'));
const scripts = [...head.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]).filter(s => s.includes('viv.theme'));
assert.ok(scripts.length >= 1, 'no inline theme script found in the study page head');

function openPage(stored) {
  const attrs = {};
  const handlers = {};
  const win = {
    addEventListener: (type, fn) => { (handlers[type] = handlers[type] || []).push(fn); },
    localStorage: { getItem: k => (k === 'viv.theme' ? stored : null) },
    document: { documentElement: { setAttribute: (k, v) => { attrs[k] = v; } } },
  };
  win.window = win;
  vm.createContext(win);
  scripts.forEach(s => vm.runInContext(s, win));
  return { attrs, fire: e => (handlers.storage || []).forEach(fn => fn(e)) };
}

// load: the persisted theme is applied (existing behaviour)
assert.strictEqual(openPage('dark').attrs['data-theme'], 'dark');
assert.strictEqual(openPage(null).attrs['data-theme'], undefined);

// live toggle in the outer page -> the open study page follows, both directions
let p = openPage('light');
p.fire({ key: 'viv.theme', newValue: 'dark' });
assert.strictEqual(p.attrs['data-theme'], 'dark');
p.fire({ key: 'viv.theme', newValue: 'light' });
assert.strictEqual(p.attrs['data-theme'], 'light');

// unrelated keys, cleared storage and junk values never change the theme
p = openPage('light');
p.fire({ key: 'something.else', newValue: 'dark' });
p.fire({ key: 'viv.theme', newValue: null });
p.fire({ key: 'viv.theme', newValue: 'purple' });
assert.strictEqual(p.attrs['data-theme'], 'light');

console.log('study theme follow: ok');
