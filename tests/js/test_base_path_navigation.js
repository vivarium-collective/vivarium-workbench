// tests/js/test_base_path_navigation.js — run with: node tests/js/test_base_path_navigation.js
//
// Drift guard for a bug that is invisible in local development and only appears
// on a deployment served under a URL prefix.
//
// `report.py`'s _base_path_shim patches fetch / EventSource / XMLHttpRequest so
// root-absolute *requests* get the deployment's base path. It does NOT patch
// `window.open` or `window.location` — those are navigations, not requests. So a
// literal `window.open("/?workspace=…")` escapes the workbench entirely: under
// `--base-path /workbench` it resolves to the ALB root, which on the Stanford
// deployments serves PTools. The user gets another application in the new tab,
// and the browser reports 404/504 for workbench assets that were never there.
//
// Verified against the live dev ALB: `GET /?workspace=v2ecoli` returns 200 with
// PTools' HTML, while `GET /workbench/?workspace=v2ecoli` returns the workbench.
//
// This passes trivially at root hosting (BP === ""), which is why local testing
// never catches it. Hence a source-level assertion rather than a behavioural one.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const STATIC_DIR = path.join(__dirname, '..', '..', 'vivarium_workbench', 'static');

// Navigations the shim cannot reach, written with a root-absolute literal.
const BAD = /(window\.open|window\.location\.assign|window\.location\.replace|location\.href\s*=)\s*\(?\s*["'`]\//g;

function run() {
  const offenders = [];
  for (const name of fs.readdirSync(STATIC_DIR).filter(f => f.endsWith('.js'))) {
    const src = fs.readFileSync(path.join(STATIC_DIR, name), 'utf8');
    for (const line of src.split('\n')) {
      if (line.trim().startsWith('//')) continue;   // comments describe the pattern
      BAD.lastIndex = 0;
      if (BAD.test(line)) offenders.push(`${name}: ${line.trim()}`);
    }
  }
  assert.deepStrictEqual(
    offenders, [],
    'navigation with a root-absolute URL escapes the base path — prefix it with ' +
    '`window.__BASE_PATH__ || ""`:\n  ' + offenders.join('\n  '));

  // And the fix is actually present where the bug was.
  for (const f of ['workspace-picker.js']) {
    const src = fs.readFileSync(path.join(STATIC_DIR, f), 'utf8');
    assert.ok(src.includes('__BASE_PATH__'),
      `${f} navigates by URL and must consult window.__BASE_PATH__`);
  }

  console.log('test_base_path_navigation.js: all assertions passed');
}

run();
