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
  extract('_selectedBuildTip') + '\n' + extract('_computeRunTarget') + '\nreturn _computeRunTarget;')({});
// The Environment scope on Cloud with a selected build: the tooltip comes from the preflight's `tip` (step P3 F4b).
// eslint-disable-next-line no-new-func
const computeCloud = new Function('window',
  extract('_selectedBuildTip') + '\n' + extract('_computeRunTarget') + '\nreturn _computeRunTarget;')({
  VivEnv: { isCloud: () => true, runBuild: () => ({ simulator_id: 9, commit: '0123456789' }) } });

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

  // Not ready (compose-api has no such environment): amber, with the reason in the tooltip.
  const notReady = compute({ target: 'deployment', dispatch: 'compose-api', ok: false,
    reason: 'unknown-environment', error: "compose-api has no prebuilt environment 'missing' (it has: runtime)",
    message: "compose-api has no prebuilt environment 'missing' (it has: runtime)" });
  assert.strictEqual(notReady.label, 'Runs: Cloud · not ready ⚠', 'ok: false -> not-ready label');
  assert.ok(/no prebuilt environment 'missing'/.test(notReady.tip), 'the reason is in the tooltip');
  assert.notStrictEqual(notReady.bg, plain.bg, 'not ready leaves the ready styling');
  assert.strictEqual(compute({ target: 'deployment', ok: false, reason: 'backend error', error: 'boom' }).tip,
    'A Cloud Run would be refused: boom.', 'no message -> the error text');
  assert.strictEqual(compute({ target: 'local', ok: false, message: 'x' }).label, 'Runs: Local',
    'ok: false on a local target is not a cloud warning');

  assert.strictEqual(compute(Object.assign({}, ready, { warnings: [] })).label, 'Runs: Cloud',
    'an empty warnings list is no warning');
  assert.strictEqual(compute({ target: 'local', ok: true }).label, 'Runs: Local', 'local unchanged');
  assert.strictEqual(compute(null).label, 'Runs: —', 'unknown unchanged');

  // `tip`: the backend's template, filled with the selected build; absent → the neutral core text.
  assert.strictEqual(computeCloud(Object.assign({}, ready, { tip: 'On X: build {build}{commit}, again {build}.' })).tip,
    'On X: build 9 (0123456), again 9.', 'tip placeholders are filled (every occurrence)');
  assert.ok(/remote backend against build #9 \(0123456\)/.test(computeCloud(ready).tip), 'no tip -> neutral text');
  assert.ok(!/GovCloud|sms-api/i.test(computeCloud(ready).tip), 'the neutral text names no backend');
  assert.strictEqual(compute(Object.assign({}, ready, { tip: 'ignored' })).tip, ready.message,
    'tip is only for a selected build: the preflight badge keeps its message');
  assert.ok(!/GovCloud/.test(compute({ target: 'deployment', ok: true }).tip), 'no message -> a neutral cloud tip');

  console.log('test_run_target_badge.js: all assertions passed');
}

run();
