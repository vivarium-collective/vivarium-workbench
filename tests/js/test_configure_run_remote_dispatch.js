// tests/js/test_configure_run_remote_dispatch.js — run with: node tests/js/test_configure_run_remote_dispatch.js
//
// A study run on a named backend (serve --backend-base-url) is dispatched, not run: /api/study-run-baseline
// answers 202 {run_id}. configure-run.js's _runStudy used to treat anything but 200 as an error and showed a red
// "202". The real function is lifted out of the IIFE and executed against an injected _post, as
// test_cloud_run_chip.js does for loom-embed.js.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'configure-run.js'), 'utf8');

function extract(name) {
  const start = SRC.indexOf('function ' + name + '(');
  assert.ok(start !== -1, name + ' not found in configure-run.js');
  let depth = 0, i = SRC.indexOf('{', start);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}' && --depth === 0) break;
  }
  return SRC.slice(start, i + 1);
}

const esc = (s) => String(s == null ? '' : s).replace(/[<>&"]/g, (c) =>
  ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

async function run(response) {
  const shown = [];
  const button = { disabled: true };
  const el = { querySelector: () => button };
  const posted = [];
  const _post = (url, body) => { posted.push([url, body]); return Promise.resolve(response); };
  const _status = (_el, html) => shown.push(html);
  const unexpected = () => { throw new Error('a dispatched run must not be polled as a local one'); };
  // eslint-disable-next-line no-new-func
  const runStudy = new Function('_post', '_status', 'esc', 'ctxState', '_poll', '_api',
    extract('_runStudy') + '\nreturn _runStudy;')(_post, _status, esc, { study: 'demo' }, unexpected, unexpected);
  runStudy(el, { x: 1 }, 3);
  await new Promise((r) => setImmediate(r));
  return { shown, button, posted };
}

(async () => {
  const dispatched = await run({ status: 202, body: { run_id: 'compose-4706', phase: 'running',
    warning: 'HEAD is not <the pinned commit>' } });
  assert.deepStrictEqual(dispatched.posted, [['/api/study-run-baseline', { study: 'demo', overrides: { x: 1 }, steps: 3 }]]);
  const last = dispatched.shown[dispatched.shown.length - 1];
  assert.ok(last.includes('Dispatched') && last.includes('compose-4706'), last);
  assert.ok(last.includes('HEAD is not &lt;the pinned commit&gt;'), 'the warning is shown, escaped: ' + last);
  assert.ok(!last.startsWith('<span class="inv-run-err">'), 'not shown as an error: ' + last);
  assert.strictEqual(dispatched.button.disabled, false);

  const refused = await run({ status: 409, body: { error: 'no environment runs this workspace' } });
  assert.ok(refused.shown[refused.shown.length - 1].includes('inv-run-err'));
  console.log('ok test_configure_run_remote_dispatch');
})().catch((e) => { console.error(e); process.exit(1); });
