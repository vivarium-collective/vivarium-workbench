// tests/js/test_progress_track.js — run with: node tests/js/test_progress_track.js
// Unit tests for the pure helpers + html() string builder of the ProgressTrack
// component (Plan 7 / WS-1). No DOM required (mirrors test_chain_block.js).
const assert = require('assert');
const PT = require('../../vivarium_workbench/static/progress-track.js');

// ── softFraction: clamped to [0, cap], monotonic, honest (never reaches 1) ──
assert.strictEqual(PT.softFraction(0, 480000), 0, 'zero elapsed -> 0');
assert.strictEqual(PT.softFraction(240000, 480000), 0.5, 'half elapsed -> 0.5');
assert.strictEqual(PT.softFraction(-5, 480000), 0, 'negative elapsed clamps to 0');
assert.strictEqual(PT.softFraction(99999999, 480000), 0.9, 'far past typical caps at 0.9');
assert.strictEqual(PT.softFraction(1000, 0), 0, 'zero typical -> 0 (no divide-by-zero)');
assert.strictEqual(PT.softFraction(300000, 400000, 0.5), 0.5, 'custom cap honoured');
// monotonic non-decreasing up to the cap
let prev = -1;
for (let e = 0; e <= 600000; e += 60000) {
  const f = PT.softFraction(e, 480000);
  assert(f >= prev, 'softFraction monotonic non-decreasing at ' + e);
  assert(f <= 0.9, 'softFraction never exceeds cap');
  prev = f;
}

// ── measuredFraction: value/max clamped to [0, 1] ──
assert.strictEqual(PT.measuredFraction(42, 100), 0.42, 'measured 42/100');
assert.strictEqual(PT.measuredFraction(150, 100), 1, 'measured over-max clamps to 1');
assert.strictEqual(PT.measuredFraction(-3, 100), 0, 'measured negative clamps to 0');
assert.strictEqual(PT.measuredFraction(5, 0), 0, 'measured zero max -> 0');

// ── stageFraction: done-count + active soft-fill, all over total ──
const NOW = 1000000;
const stages = [
  { key: 'resolve', label: 'Resolve' }, { key: 'submit', label: 'Submit' },
  { key: 'queued', label: 'Queued' }, { key: 'running', label: 'Running' },
  { key: 'done', label: 'Done' }, { key: 'landed', label: 'Landed' },
];
// 2 done, active queued at 50% soft => (2 + 0.5) / 6 = 0.41666…
const queuedModel = {
  mode: 'stages', stages: stages, done: ['resolve', 'submit'], active: 'queued',
  soft: { startedAt: NOW - 240000, typicalMs: 480000 },
};
assert(Math.abs(PT.stageFraction(queuedModel, NOW) - (2.5 / 6)) < 1e-9, 'stageFraction = done + active soft, over total');
// no soft => pure done-count fraction
assert.strictEqual(PT.stageFraction({ stages: stages, done: ['resolve', 'submit'], active: 'queued' }, NOW), 2 / 6, 'stageFraction w/o soft = done/total');
// unknown done keys are ignored
assert.strictEqual(PT.stageFraction({ stages: stages, done: ['bogus'] }, NOW), 0, 'unknown done keys ignored');
// empty stages -> 0 (no divide-by-zero)
assert.strictEqual(PT.stageFraction({ stages: [] }, NOW), 0, 'empty stages -> 0');
// all done -> 1
assert.strictEqual(PT.stageFraction({ stages: stages, done: stages.map(s => s.key) }, NOW), 1, 'all done -> 1');

// ── html(): a11y contract + segment states ──
const h = PT.html(queuedModel, NOW);
assert(h.indexOf('role="progressbar"') !== -1, 'emits role=progressbar');
assert(/aria-valuemin="0"/.test(h) && /aria-valuemax="100"/.test(h), 'emits aria-valuemin/max');
assert.strictEqual((h.match(/aria-valuenow="(\d+)"/) || [])[1], '42', 'aria-valuenow rounds stageFraction (42%)');
assert(/aria-valuetext="[^"]+"/.test(h), 'emits aria-valuetext');
assert(h.indexOf('aria-live="polite"') !== -1, 'emits an aria-live region');
assert(h.indexOf('ptrack-seg-done') !== -1, 'renders done segments');
assert(h.indexOf('ptrack-seg-active') !== -1, 'renders the active segment');
assert(h.indexOf('ptrack-spin') !== -1, 'active (non-failed) stage shows a spinner');
assert(h.indexOf('Queued…') !== -1, 'aria-valuetext announces the active stage label');

// ── failed stage: renders the failed class, no spinner ──
const failed = PT.html({
  mode: 'stages', stages: stages, done: ['resolve', 'submit', 'queued'], active: null, failed: 'running',
}, NOW);
assert(failed.indexOf('ptrack-seg-failed') !== -1, 'renders the failed class');
assert(failed.indexOf('ptrack-spin') === -1, 'failed state has no spinner');
assert(failed.indexOf('Running failed') !== -1, 'announces which stage failed');

// ── measured mode: maps value/max, real bar, step text ──
const meas = PT.html({ mode: 'measured', value: 3, max: 12 }, NOW);
assert.strictEqual((meas.match(/aria-valuenow="(\d+)"/) || [])[1], '25', 'measured aria-valuenow = 3/12 = 25%');
assert(meas.indexOf('step 3 of 12') !== -1, 'measured mode shows step X of Y');
assert(meas.indexOf('role="progressbar"') !== -1, 'measured mode is also a progressbar');

// ── note is passed through as HTML (callers build escaped markup) ──
const noted = PT.html(Object.assign({ note: '<strong>Queued on AWS Batch…</strong>' }, queuedModel), NOW);
assert(noted.indexOf('<strong>Queued on AWS Batch…</strong>') !== -1, 'note rendered as HTML');

// ── data-sig excludes soft progress (so the tween does not force a rebuild) ──
const m1 = Object.assign({}, queuedModel, { soft: { startedAt: NOW - 100000, typicalMs: 480000 } });
const m2 = Object.assign({}, queuedModel, { soft: { startedAt: NOW - 400000, typicalMs: 480000 } });
const sig = s => (s.match(/data-sig="([^"]*)"/) || [])[1];
assert.strictEqual(sig(PT.html(m1, NOW)), sig(PT.html(m2, NOW)), 'signature is stable across soft-fill progress');

console.log('ok');
