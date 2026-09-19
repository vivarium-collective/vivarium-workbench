// tests/js/test_run_redrive_poll.js — run with: node tests/js/test_run_redrive_poll.js
//
// Plan §A3′ option (c). An item gated behind an unfinished prerequisite is
// parked `waiting` and its worker RETURNS rather than holding a thread for the
// life of a Batch job — so something has to come back and release it. That
// something is this poll, and if it stops calling redrive, a gated investigation
// simply never finishes. Nothing else in the system would notice.
//
// Behavioural, not a grep: the real `maybeRedrive` source is lifted out of
// walkthrough.js and driven with a stubbed fetch. walkthrough.js is one large
// IIFE with no exports, so extraction is the only way to execute the SHIPPED
// function rather than a copy of it that can silently drift.
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
  const from = i;
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}' && --depth === 0) break;
  }
  return SRC.slice(start, i + 1);
}

function harness() {
  const posts = [];
  const stubFetch = (url, opts) => {
    posts.push({ url, opts });
    return Promise.resolve({});
  };
  // maybeRedrive now calls walkthrough.js's shared `apiFetch` client (Phase 1a).
  // Inject an apiFetch matching the shipped implementation, built on the stubbed
  // fetch, so `posts` still records the redrive call identically ({url, opts}
  // with method / Content-Type header / JSON body).
  const apiFetch = (method, path, body) => {
    const opts = { method: method };
    if (body !== undefined && body !== null) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    return stubFetch(path, opts);
  };
  // `lastDone` is closed over by the real function; thread it through `state`
  // so successive calls see the same value a live poll would.
  const state = { lastDone: -1 };
  // eslint-disable-next-line no-new-func
  const call = new Function('state', 'jobId', '_api', 'fetch', 'apiFetch',
    'var lastDone = state.lastDone;' +
    extract('maybeRedrive') +
    '; return function(job){ maybeRedrive(job); state.lastDone = lastDone; };'
  )(state, 'j1', (p) => p, stubFetch, apiFetch);
  return { posts, call };
}

function run() {
  // 1. Nothing waiting -> never POST. A normal investigation must not generate
  //    redrive traffic at all.
  let h = harness();
  h.call({ progress: { total: 2, done: 1, waiting: 0 } });
  h.call({ progress: { total: 2, done: 2, waiting: 0 } });
  assert.deepStrictEqual(h.posts, [], 'redrive fired with nothing waiting');

  // 2. Waiting, but nothing settled since last look -> no POST. This is the
  //    whole point of firing on CHANGE: a multi-hour Batch prerequisite polled
  //    every 2s would otherwise spawn a worker thread per tick, each one
  //    re-parking the same items.
  h = harness();
  h.call({ progress: { total: 2, done: 0, waiting: 1 } });  // first look: arms
  const afterFirst = h.posts.length;
  h.call({ progress: { total: 2, done: 0, waiting: 1 } });
  h.call({ progress: { total: 2, done: 0, waiting: 1 } });
  assert.strictEqual(h.posts.length, afterFirst,
    'redrive fired repeatedly while nothing changed');

  // 3. The prerequisite lands (done increases while something waits) -> POST.
  //    The status GET resolves `submitted` items upstream, so a Batch job
  //    completing shows up here exactly as `done` going up.
  h = harness();
  h.call({ progress: { total: 2, done: 0, waiting: 1 } });
  const before = h.posts.length;
  h.call({ progress: { total: 2, done: 1, waiting: 1 } });
  assert.strictEqual(h.posts.length, before + 1,
    'redrive did NOT fire when a prerequisite completed — gated items would ' +
    'never be released and the investigation would hang forever');

  const post = h.posts[h.posts.length - 1];
  assert.match(post.url, /investigation-run-redrive/, 'wrong endpoint: ' + post.url);
  assert.strictEqual(post.opts.method, 'POST');
  assert.deepStrictEqual(JSON.parse(post.opts.body), { job_id: 'j1' });

  // 4. The panel must render the two statuses that post-date its icon map,
  //    or a dispatched Batch run and a gated dependent both show as '?'.
  for (const st of ['submitted', 'waiting']) {
    assert.ok(new RegExp(st + ":\\s*'").test(SRC),
      'run-progress icon map has no entry for ' + st);
  }

  console.log('test_run_redrive_poll.js: ok');
}

run();

// --- §A5: the Run button hands a 202 to the async progress poll ------------ //
//
// The server now delegates a v3 investigation to the same background job
// machinery "Run unblocked" uses, so this button answers 202 + job_id instead
// of blocking. If the client does not hand that to _vivPollRunProgress, the run
// proceeds invisibly: no progress panel, no prerequisite re-drive, and the user
// sees a button that flicked back to idle with nothing to show.
function runA5() {
  const fn = extract('_runInvestigation');
  assert.match(fn, /202/,
    '_runInvestigation ignores the 202 async contract');
  assert.match(fn, /_vivPollRunProgress\s*\(\s*j\.job_id\s*\)/,
    '_runInvestigation does not hand job_id to the progress poll — a delegated ' +
    'run would proceed with no panel and no prerequisite re-drive');
  // The 202 branch must return before the synchronous refresh path, or the
  // panel is torn down by _loadInvestigations the moment it appears.
  const idx202 = fn.indexOf('202');
  const idxReturn = fn.indexOf('return;', idx202);
  const idxAlert = fn.indexOf("alert('Run failed", idx202);
  assert.ok(idxReturn !== -1 && idxReturn < idxAlert,
    'the 202 branch must return before the synchronous failure/refresh path');
  console.log('test_run_redrive_poll.js: A5 ok');
}

runA5();
