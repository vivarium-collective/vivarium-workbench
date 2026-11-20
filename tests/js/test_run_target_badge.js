// tests/js/test_run_target_badge.js — run with: node tests/js/test_run_target_badge.js
//
// Guards the composite card's run-target badge (static/composite-card.js,
// `_computeRunTarget`) over the /api/remote-dispatch-preflight body. Builds B2b
// (Finding F): a viva-core `id:` environment is ready with a dirty tree, and the
// preflight adds a non-blocking `warnings` list — the badge shows it (an ⓘ on the
// label, the note in the tooltip) WITHOUT switching to a blocked styling.
// composite-card.js is an IIFE with no exports, so — like test_cloud_run_chip.js —
// the real function is lifted out and executed rather than a drift-prone copy.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'composite-card.js'),
  'utf8');

function extract(name) {
  const start = SRC.indexOf('function ' + name + '(');
  assert.ok(start !== -1, name + ' not found in composite-card.js');
  let depth = 0, i = SRC.indexOf('{', start);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}' && --depth === 0) break;
  }
  return SRC.slice(start, i + 1);
}

// `window` is a free variable (VivEnv); no VivEnv → the badge reflects the preflight.
// eslint-disable-next-line no-new-func
const compute = new Function('window',
  extract('_computeRunTarget') + '\nreturn _computeRunTarget;')({});

function run() {
  const ready = { target: 'deployment', ok: true, reason: 'ok', dispatch: 'viva-v1-document',
    message: 'Environment id:env-1 runs its prebuilt image.' };

  const plain = compute(ready);
  assert.strictEqual(plain.label, 'Runs: Cloud', 'no warnings -> plain Cloud label');
  assert.ok(!/Note/.test(plain.tip), 'no warnings -> no note in the tooltip');

  const warned = compute(Object.assign({}, ready, {
    warnings: ['uncommitted changes are not used by this environment (id:env-1 runs its prebuilt image)'] }));
  assert.strictEqual(warned.label, 'Runs: Cloud ⓘ', 'warnings -> an info mark on the label');
  assert.ok(/does not block a Run/.test(warned.tip), 'the note says it does not block');
  assert.ok(/uncommitted changes are not used/.test(warned.tip), 'the warning text is in the tooltip');
  assert.strictEqual(warned.bg, plain.bg, 'a warning keeps the ready background');
  assert.strictEqual(warned.fg, plain.fg, 'a warning keeps the ready colour');

  assert.strictEqual(compute(Object.assign({}, ready, { warnings: [] })).label, 'Runs: Cloud',
    'an empty warnings list is no warning');
  assert.strictEqual(compute({ target: 'local', ok: true }).label, 'Runs: Local', 'local unchanged');
  assert.strictEqual(compute(null).label, 'Runs: —', 'unknown unchanged');

  console.log('test_run_target_badge.js: all assertions passed');
}

run();
