// tests/js/test_run_target_badge_goldens.js — run with: node tests/js/test_run_target_badge_goldens.js
//
// Goldens for the composite card's run-target badge (static/composite-card.js `_computeRunTarget` and
// `_applyRunTargetBadges`), step P3 F4b (docs/backend-adapters.md §2.5): the label, colours and tooltip for each
// backend's /api/remote-dispatch-preflight body — SMS (a selected build, and the image preflight), viva-core,
// compose-api, unknown (no preflight answer) and the published snapshot (no VivEnv, no preflight). With the SMS
// plugin on, the SMS tooltips must stay word for word what they were before F4b moved their text to the plugin
// (the preflight's `tip`).
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

function computeWith(win) {
  // eslint-disable-next-line no-new-func
  return new Function('window', extract('_computeRunTarget') + '\nreturn _computeRunTarget;')(win);
}

// The SMS preflight's `tip` (vivarium_workbench_sms/cloud_dispatch.SELECTED_BUILD_TIP): the card fills in the
// selected build. Pinned here so the card's SMS tooltip is the pre-F4b text, word for word.
const SMS_TIP = '▶ Run dispatches to the Cloud (GovCloud) against build #{build}{commit}. ' +
  'It runs the build’s committed code — local edits not in that build won’t apply. ' +
  'The dispatch itself takes ~15-25s (sms-api registers the run over the SSM tunnel); the card tracks it robustly once it lands.';

const SMS_IMAGE = { target: 'deployment', ok: true, reason: 'image', dispatch: 'image', simulator_id: 124,
  message: 'Cloud image (build #124) is ready — runs execute its pre-built image, no push needed.', tip: SMS_TIP };
const VIVA_CORE = { target: 'deployment', ok: true, reason: 'ok', dispatch: 'viva-v1-document',
  message: 'Environment id:env-1 runs its prebuilt image -- nothing is shipped from the workspace\'s git tree, so ' +
    'nothing needs to be committed or pushed first.' };
const COMPOSE = { target: 'deployment', dispatch: 'compose', ok: true, reason: 'ok',
  message: "compose-api runs this workspace in environment 'runtime' (img:1): ready." };

function cloudEnv(build) {
  return { VivEnv: { isCloud: () => true, runBuild: () => build, setScope: () => {} } };
}

const GREEN = { bg: '#e7f6ec', fg: '#1a7f4b' };
const AMBER = { bg: '#fdf0e3', fg: '#a15c12' };
const BLUE = { bg: '#e6f0fb', fg: '#1e5fa4' };
const GREY = { bg: '#eef1f4', fg: '#667085' };

function check(got, want, what) {
  assert.deepStrictEqual(got, want, what + '\n got: ' + JSON.stringify(got) + '\nwant: ' + JSON.stringify(want));
}

function run() {
  // SMS, the Environment scope on Cloud with a build selected (the preflight carries the plugin's tip).
  check(computeWith(cloudEnv({ simulator_id: 124, commit: 'abcdef1234567' }))(SMS_IMAGE), Object.assign({
    label: '▶ Run → ☁ Cloud · build #124',
    tip: '▶ Run dispatches to the Cloud (GovCloud) against build #124 (abcdef1). ' +
      'It runs the build’s committed code — local edits not in that build won’t apply. ' +
      'The dispatch itself takes ~15-25s (sms-api registers the run over the SSM tunnel); the card tracks it robustly once it lands.',
  }, GREEN), 'SMS, Cloud scope, a build with a commit');
  check(computeWith(cloudEnv({ simulator_id: 7 }))(SMS_IMAGE), Object.assign({
    label: '▶ Run → ☁ Cloud · build #7',
    tip: '▶ Run dispatches to the Cloud (GovCloud) against build #7. ' +
      'It runs the build’s committed code — local edits not in that build won’t apply. ' +
      'The dispatch itself takes ~15-25s (sms-api registers the run over the SSM tunnel); the card tracks it robustly once it lands.',
  }, GREEN), 'SMS, Cloud scope, a build with no commit');
  check(computeWith(cloudEnv(null))(SMS_IMAGE), Object.assign({
    label: '☁ Cloud · no build ⚠',
    tip: 'Cloud is active but no build is selected — a Run is blocked. Pick or build one, or switch to Local.',
  }, AMBER), 'SMS, Cloud scope, no build');

  // SMS, the Environment scope on Local: the workspace's own preflight (an image resolves).
  const local = computeWith({ VivEnv: { isCloud: () => false, runBuild: () => null, setScope: () => {} } });
  check(local(SMS_IMAGE), Object.assign({ label: 'Runs: Cloud', tip: SMS_IMAGE.message }, BLUE), 'SMS image preflight');
  check(local({ error: 'no build', reason: 'no-build', run_target: 'deployment', actions: [], target: 'deployment',
    ok: false, tip: SMS_TIP }), Object.assign({ label: 'Runs: Cloud · not ready ⚠',
    tip: 'A Cloud Run would be refused: no build.' }, AMBER), 'SMS no-build preflight');

  // viva-core (no tip), with and without the B2b warning.
  const none = computeWith({});
  check(none(VIVA_CORE), Object.assign({ label: 'Runs: Cloud', tip: VIVA_CORE.message }, BLUE), 'viva-core');
  check(none(Object.assign({}, VIVA_CORE, { warnings: ['w1', 'w2'] })), Object.assign({ label: 'Runs: Cloud ⓘ',
    tip: VIVA_CORE.message + ' Note (does not block a Run): w1; w2.' }, BLUE), 'viva-core with warnings');

  // compose-api: ready, and not ready (amber).
  check(none(COMPOSE), Object.assign({ label: 'Runs: Cloud', tip: COMPOSE.message }, BLUE), 'compose ready');
  check(none({ target: 'deployment', dispatch: 'compose', ok: false, reason: 'unknown-environment',
    message: "compose-api has no prebuilt environment 'missing'" }), Object.assign({
    label: 'Runs: Cloud · not ready ⚠',
    tip: "A Cloud Run would be refused: compose-api has no prebuilt environment 'missing'." }, AMBER),
    'compose not ready');

  // Local.
  check(none({ target: 'local', ok: true, reason: 'local', message: 'This workspace runs locally — no push needed.' }),
    Object.assign({ label: 'Runs: Local', tip: 'This workspace runs locally — no push needed. Switch the Environment ' +
      'scope to Cloud (with a build selected) to dispatch a Run remotely instead.' }, GREY), 'local');

  // Unknown: the preflight request failed (null), with or without VivEnv on Local.
  const unknown = { label: 'Runs: —', bg: '#eef1f4', fg: '#8a97a4', tip: 'Could not determine where a Run will execute.' };
  check(none(null), unknown, 'unknown, no VivEnv');
  check(local(null), unknown, 'unknown, VivEnv on Local');

  // Snapshot (the published bundle): no VivEnv, the preflight fetch fails → the unknown badge, passive (not
  // clickable, no "click to switch" suffix).
  const badge = { textContent: '', title: '', style: {}, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
  const doc = { querySelectorAll: () => [badge] };
  // eslint-disable-next-line no-new-func
  const apply = new Function('window', 'document',
    extract('_computeRunTarget') + '\n' + extract('_runTargetClickable') + '\n' + extract('_applyRunTargetBadges') +
    '\nreturn _applyRunTargetBadges;')({}, doc);
  apply(null);
  check({ text: badge.textContent, title: badge.title, bg: badge.style.background, fg: badge.style.color,
    clickable: badge.attrs['data-clickable'] },
  { text: 'Runs: —', title: 'Could not determine where a Run will execute.', bg: '#eef1f4', fg: '#8a97a4',
    clickable: '0' }, 'snapshot badge');

  console.log('test_run_target_badge_goldens.js: all assertions passed');
}

run();
