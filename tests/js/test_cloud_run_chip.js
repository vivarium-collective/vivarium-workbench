// tests/js/test_cloud_run_chip.js — run with: node tests/js/test_cloud_run_chip.js
//
// Guards the parent-owned Cloud-run tracking in static/loom-embed.js. A
// composite-card Cloud Run dispatches a build's image via sms-api (a slow ~20s
// POST over the SSM tunnel); the loom bar times out during it and mislabels a
// run that actually landed on GovCloud. So the PARENT owns the tracking: a chip
// driven by /api/composite-run/remote-sim-<id>/status.
//
// The crux is the status->phase mapping: it must terminate ONLY on a real
// completed/failed and NEVER flip to a false "complete" on a slow/absent/unknown
// status. loom-embed.js is an IIFE with no exports, so — like
// test_run_redrive_poll.js — the real functions are lifted out and executed
// rather than a drift-prone copy.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'loom-embed.js'),
  'utf8');

function extract(name) {
  const start = SRC.indexOf('function ' + name + '(');
  assert.ok(start !== -1, name + ' not found in loom-embed.js');
  let depth = 0, i = SRC.indexOf('{', start);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}' && --depth === 0) break;
  }
  return SRC.slice(start, i + 1);
}

// `_esc` is a free variable in loom-embed.js's IIFE scope; inject it (mirrors
// the redrive test injecting `_api`/`fetch`).
const _esc = (s) => String(s == null ? '' : s).replace(/[<>&"]/g, (c) =>
  ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

// eslint-disable-next-line no-new-func
const mod = new Function('_esc',
  extract('_cloudPhaseFromStatus') + '\n' +
  extract('_cloudRunChipHtml') + '\n' +
  'return { _cloudPhaseFromStatus: _cloudPhaseFromStatus, _cloudRunChipHtml: _cloudRunChipHtml };'
)(_esc);

const phase = mod._cloudPhaseFromStatus;
const chip = mod._cloudRunChipHtml;

function run() {
  // --- status -> phase: the never-false-complete contract -----------------
  assert.strictEqual(phase({ status: 'completed' }), 'completed', 'completed -> completed');
  assert.strictEqual(phase({ status: 'failed' }), 'failed', 'failed -> failed');
  assert.strictEqual(phase({ status: 'orphaned' }), 'failed', 'orphaned -> failed');
  assert.strictEqual(phase({ status: 'running' }), 'running', 'running -> running');
  assert.strictEqual(phase({ status: 'running', raw_status: 'QUEUED' }), 'queued',
    'running+queued raw_status -> queued');
  assert.strictEqual(phase({ status: 'running', raw_status: 'submitted' }), 'queued',
    'running+submitted -> queued');

  // A slow tunnel / not-yet-registered sim yields no body (fetch returned null on
  // a non-200) or an unknown status. NONE of these may ever be a terminal phase —
  // that is exactly the false "complete" the loom bar produced.
  assert.strictEqual(phase(null), null, 'null body -> keep polling (no false terminal)');
  assert.strictEqual(phase(undefined), null, 'undefined body -> keep polling');
  assert.strictEqual(phase('nope'), null, 'non-object body -> keep polling');
  assert.strictEqual(phase({}), null, 'empty body -> keep polling');
  assert.strictEqual(phase({ status: 'weird' }), null, 'unknown status -> keep polling');

  // --- chip HTML ----------------------------------------------------------
  const dispatching = chip({ phase: 'dispatching', buildSim: 212 });
  assert.ok(/Dispatching to Cloud build #212/.test(dispatching), 'dispatching shows the build #');
  assert.ok(!/View in Runs DB/.test(dispatching), 'dispatching has no Runs-DB link yet (no sim id)');

  const queued = chip({ phase: 'queued', simId: 4567 });
  assert.ok(/Cloud run #4567 · queued/.test(queued), 'queued chip labels the sim id + phase');
  assert.ok(/_viewCloudRunInRuns\(4567\)/.test(queued), 'queued chip links into the Runs tab by sim id');

  const running = chip({ phase: 'running', simId: 4567 });
  assert.ok(/Cloud run #4567 · running/.test(running), 'running chip label');

  const completed = chip({ phase: 'completed', simId: 4567 });
  assert.ok(/✓ completed/.test(completed), 'completed chip shows a check');
  assert.ok(/#166534/.test(completed), 'completed chip uses the green palette (matches statusChip)');

  const failed = chip({ phase: 'failed', simId: 4567 });
  assert.ok(/✗ failed/.test(failed), 'failed chip shows a cross');
  assert.ok(/#991b1b/.test(failed), 'failed chip uses the red palette');

  const dfail = chip({ phase: 'dispatch-failed', error: '<boom>' });
  assert.ok(/Cloud dispatch failed/.test(dfail), 'dispatch-failed chip label');
  assert.ok(/&lt;boom&gt;/.test(dfail) && !/<boom>/.test(dfail),
    'dispatch-failed error text is HTML-escaped');
  assert.ok(!/View in Runs DB/.test(dfail), 'dispatch-failed has no Runs-DB link (never got a sim id)');

  console.log('test_cloud_run_chip.js: all assertions passed');
}

run();
