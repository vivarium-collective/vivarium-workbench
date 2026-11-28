// tests/js/test_stopped_card_header.js — run with: node tests/js/test_stopped_card_header.js
//
// #1783. A composite-card run that ends before its end (cancelled, timed out, failed) can have landed results
// (the run's history up to its last snapshot). The status route answers `stopped: {rows, last_time}` for it, and
// the card must show that as the completed run's header marked as a stop, not "✗ cancelled" as if nothing were kept.
//
// Behavioural, not a grep: the real `_pollCompositeRun` and `_esc` are lifted out of walkthrough.js (one large IIFE
// with no exports) and driven with a stubbed `apiFetch`, as test_run_redrive_poll.js does.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'walkthrough.js'),
  'utf8');

function extract(name) {
  const start = SRC.indexOf('function ' + name + '(');
  assert.ok(start !== -1, name + ' not found in walkthrough.js');
  let depth = 0, i = SRC.indexOf('{', start);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}' && --depth === 0) break;
  }
  return SRC.slice(start, i + 1);
}

// Poll one run whose status route answers `body`; resolve with what the card shows.
async function show(body) {
  const panel = { innerHTML: '', querySelector: () => null };
  const card = { querySelector: (sel) => (sel === '[data-role="out-panel"]' ? panel : null) };
  const seen = { indicator: null, outputsOpened: 0, retried: 0 };
  const apiFetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  // eslint-disable-next-line no-new-func
  const poll = new Function('apiFetch', '_api', '_endRunIndicator', '_openOutputsSection', 'setTimeout',
    extract('_esc') + '\n' + extract('_pollCompositeRun') + '\nreturn _pollCompositeRun;')(
    apiFetch,
    (p) => p,
    (_card, text) => { seen.indicator = text; },
    () => { seen.outputsOpened += 1; },
    () => { seen.retried += 1; });
  poll(card, 'card-1');
  await new Promise((resolve) => setImmediate(resolve));
  return { html: panel.innerHTML, seen };
}

const RUNNER_LINE = 'REMOTE RUN STOPPED: compose-api run 12966 stopped (cancelled) at t=1720 of 3000; ' +
  'the results up to the last snapshot are landed: Runs, ⬇ Raw data';

(async () => {
  // 1. A cancelled run that landed its results: the stop header with the time reached, the zip and the Runs link,
  //    and the runner's line under it. Not the failure heading.
  let r = await show({ status: 'cancelled', downloadable: true, error: RUNNER_LINE,
                       stopped: { rows: 1721, last_time: 1720.0 } });
  assert.ok(r.html.includes('■ stopped (cancelled)'), r.html);
  assert.ok(r.html.includes('results up to t=1720'), r.html);
  assert.ok(r.html.includes('Download ZIP') && r.html.includes('/api/composite-run/card-1/download'), r.html);
  assert.ok(r.html.includes('open in <a href="#simulations"'), r.html);
  assert.ok(r.html.includes(RUNNER_LINE), r.html);
  assert.ok(!r.html.includes('✗'), 'a run that kept results was shown as a bare failure: ' + r.html);
  assert.strictEqual(r.seen.indicator, '■ stopped — see Outputs');
  assert.strictEqual(r.seen.outputsOpened, 1, 'Outputs was not opened');
  assert.strictEqual(r.seen.retried, 0, 'a finished run was polled again');

  // 2. A local run that failed partway and kept history gets the same header; its status is named as it is.
  r = await show({ status: 'failed', stopped: { rows: 40, last_time: 39 } });
  assert.ok(r.html.includes('■ stopped (failed)') && r.html.includes('results up to t=39'), r.html);
  assert.ok(!r.html.includes('<pre'), 'an error block with no error: ' + r.html);

  // 3. No time reached on record: the header does not invent one.
  r = await show({ status: 'cancelled', stopped: { rows: 3, last_time: null } });
  assert.ok(r.html.includes('■ stopped (cancelled)'), r.html);
  assert.ok(!r.html.includes('results up to'), r.html);

  // 4. Nothing landed: as before, the failure heading, the error and the Runs pointer.
  r = await show({ status: 'failed', error: 'Traceback: boom' });
  assert.ok(r.html.includes('✗ failed') && r.html.includes('Traceback: boom'), r.html);
  assert.ok(r.html.includes('Details under'), r.html);
  assert.ok(!r.html.includes('■ stopped'), r.html);
  assert.strictEqual(r.seen.indicator, '✗ failed');

  // 5. A completed run is untouched.
  r = await show({ status: 'completed', stopped: { rows: 9, last_time: 8 } });
  assert.ok(r.html.includes('✓ completed') && !r.html.includes('■ stopped'), r.html);

  // 6. What the server sent is escaped, in the header (its status) as in the error block.
  r = await show({ status: 'cancelled', error: '<img src=x onerror=alert(1)>', stopped: { rows: 1, last_time: 0 } });
  assert.ok(!r.html.includes('<img'), r.html);
  assert.ok(r.html.includes('&lt;img'), r.html);
  r = await show({ status: '<i>x</i>', stopped: { rows: 1, last_time: 0 } });
  assert.ok(!r.html.includes('<i>') && r.html.includes('&lt;i&gt;x'), r.html);

  // 7. The time reached is shown as a number, without float noise; a value that is not one is left out.
  r = await show({ status: 'cancelled', stopped: { rows: 2, last_time: 0.1 + 0.2 } });
  assert.ok(r.html.includes('results up to t=0.3 ') && !r.html.includes('0.30000000000000004'), r.html);
  r = await show({ status: 'cancelled', stopped: { rows: 2, last_time: '<u>' } });
  assert.ok(!r.html.includes('results up to') && !r.html.includes('<u>'), r.html);

  console.log('ok: test_stopped_card_header');
})().catch((e) => { console.error(e); process.exit(1); });
