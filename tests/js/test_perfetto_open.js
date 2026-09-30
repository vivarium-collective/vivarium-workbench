// tests/js/test_perfetto_open.js — run with: node tests/js/test_perfetto_open.js
//
// "⏱ Trace" (static/perfetto-open.js): hand a remote run's trace to Perfetto via
// its postMessage protocol. Drives the SHIPPED module with stub windows, a stub
// fetch and a manual clock — no browser.
const assert = require('assert');
const path = require('path');

const P = require(path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'perfetto-open.js'));

// A listener registry standing in for the opener window's `message` events.
function bus() {
  const ls = [];
  return {
    addEventListener: (t, f) => { if (t === 'message') ls.push(f); },
    removeEventListener: (t, f) => { const i = ls.indexOf(f); if (i >= 0) ls.splice(i, 1); },
    emit: (ev) => ls.slice().forEach((f) => f(ev)),
    count: () => ls.length,
  };
}

// A manual setInterval: tick() runs every registered callback once.
function clock() {
  const cbs = new Map(); let id = 0; let t = 0;
  return {
    setInterval: (f) => { cbs.set(++id, f); return id; },
    clearInterval: (i) => cbs.delete(i),
    now: () => t,
    advance: (ms) => { t += ms; },
    tick: () => Array.from(cbs.values()).forEach((f) => f()),
    active: () => cbs.size,
  };
}

// A fake Perfetto window: answers PONG to PING only once `loaded`.
function perfettoWindow(b, origin) {
  const w = { closed: false, posted: [], loaded: false };
  w.postMessage = (msg, target) => {
    w.posted.push({ msg, target });
    if (msg === 'PING' && w.loaded) b.emit({ source: w, origin, data: 'PONG' });
  };
  w.close = () => { w.closed = true; };
  return w;
}

async function testViewerUrl() {
  const loc = { origin: 'https://wb.example' };
  assert.strictEqual(P.viewerUrl({ mode: 'bundled', url: '/perfetto/' }, loc), 'https://wb.example/perfetto/?testing=1');
  assert.strictEqual(P.viewerUrl({ mode: 'external', url: 'https://ui.perfetto.dev/' }, loc), 'https://ui.perfetto.dev/?testing=1');
  // Analytics off on the tunnel's localhost origin too (Perfetto enables them there), and the
  // flag is merged into an existing query / kept before a fragment, never duplicated.
  assert.strictEqual(P.viewerUrl({ mode: 'bundled', url: '/perfetto/' }, { origin: 'http://localhost:8080' }), 'http://localhost:8080/perfetto/?testing=1');
  assert.strictEqual(P.viewerUrl({ mode: 'external', url: 'https://pf.example/ui/?x=1#!/viewer' }, loc), 'https://pf.example/ui/?x=1&testing=1#!/viewer');
  assert.strictEqual(P.viewerUrl({ mode: 'external', url: 'https://pf.example/ui/?testing=1' }, loc), 'https://pf.example/ui/?testing=1');
  assert.strictEqual(P.viewerUrl({ mode: 'off', url: null }, loc), null);
  assert.strictEqual(P.viewerUrl(undefined, loc), null);
  globalThis.__BASE_PATH__ = '/workbench';
  assert.strictEqual(P.viewerUrl({ mode: 'bundled', url: '/perfetto/' }, loc), 'https://wb.example/workbench/perfetto/?testing=1');
  delete globalThis.__BASE_PATH__;
}

async function testPingUntilPongThenPost() {
  const b = bus(); const c = clock();
  const origin = 'https://ui.perfetto.dev';
  const w = perfettoWindow(b, origin);
  const buf = new ArrayBuffer(8);
  const p = P.postTrace(w, origin + '/', buf, { title: 'T', fileName: 'f.json', url: 'u' },
    { listenOn: b, setInterval: c.setInterval, clearInterval: c.clearInterval, now: c.now });
  c.tick(); c.tick();                        // not loaded yet: PINGs go unanswered
  assert.deepStrictEqual(w.posted.map((m) => m.msg), ['PING', 'PING']);
  assert.ok(w.posted.every((m) => m.target === origin), 'PING targets the viewer origin, not *');
  w.loaded = true;
  c.tick();                                  // PING -> PONG -> trace posted
  assert.strictEqual(await p, true);
  const last = w.posted[w.posted.length - 1];
  assert.ok(last.msg.perfetto, 'posted the {perfetto: ...} envelope');
  assert.strictEqual(last.msg.perfetto.buffer, buf, 'the ArrayBuffer itself');
  assert.strictEqual(last.msg.perfetto.title, 'T');
  assert.strictEqual(last.msg.perfetto.fileName, 'f.json');
  assert.strictEqual(last.target, origin);
  assert.strictEqual(c.active(), 0, 'stops pinging');
  assert.strictEqual(b.count(), 0, 'removes its listener');
}

async function testIgnoresPongFromElsewhere() {
  const b = bus(); const c = clock();
  const w = perfettoWindow(b, 'https://ui.perfetto.dev');
  const p = P.postTrace(w, 'https://ui.perfetto.dev/', new ArrayBuffer(1), { title: 'T' },
    { listenOn: b, setInterval: c.setInterval, clearInterval: c.clearInterval, now: c.now });
  b.emit({ source: {}, origin: 'https://ui.perfetto.dev', data: 'PONG' });      // another window
  b.emit({ source: w, origin: 'https://evil.example', data: 'PONG' });          // wrong origin
  assert.ok(!w.posted.some((m) => m.msg && m.msg.perfetto), 'no trace sent to a stranger');
  c.advance(61000); c.tick();
  assert.strictEqual(await p, false, 'times out');
}

async function testUnparseableViewerUrlPostsNothing() {
  // Eran's #1214 review: never fall back to '*' -- an unknown origin posts nothing.
  const b = bus(); const c = clock();
  const w = perfettoWindow(b, 'https://ui.perfetto.dev'); w.loaded = true;
  const p = P.postTrace(w, '/perfetto/', new ArrayBuffer(1), { title: 'T' },
    { listenOn: b, setInterval: c.setInterval, clearInterval: c.clearInterval, now: c.now });
  c.tick();
  assert.strictEqual(await p, false, 'gives up at once');
  assert.strictEqual(w.posted.length, 0, 'not even a PING to an unknown origin');
  assert.strictEqual(b.count(), 0, 'no listener left behind');
}

async function testClosedWindowGivesUp() {
  const b = bus(); const c = clock();
  const w = perfettoWindow(b, 'https://ui.perfetto.dev');
  const p = P.postTrace(w, 'https://ui.perfetto.dev/', new ArrayBuffer(1), { title: 'T' },
    { listenOn: b, setInterval: c.setInterval, clearInterval: c.clearInterval, now: c.now });
  w.closed = true; c.tick();
  assert.strictEqual(await p, false);
}

function fetchStub(routes) {
  const seen = [];
  const f = (url) => {
    seen.push(url);
    const r = routes[url.split('?')[0]];
    if (!r) return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ error: 'nope' }) });
    return Promise.resolve(r(url));
  };
  f.seen = seen;
  return f;
}

async function testOpenTraceBundledEndToEnd() {
  P._reset();
  const b = bus(); const c = clock();
  const loc = { origin: 'https://wb.example' };
  const trace = new TextEncoder().encode(
    '{"traceEvents":[{"name":"run","ph":"X","ts":0,"dur":1,"pid":1,"tid":1}]}').buffer;
  const updates = [];
  const f = fetchStub({
    '/api/remote-run-trace-support': () => ({ ok: true, json: () => Promise.resolve({
      supported: true, viewer: { mode: 'bundled', url: '/perfetto/', version: 'v58' } }) }),
    '/api/remote-run-trace': () => ({ ok: true, status: 200,
      headers: { get: (h) => (h === 'Content-Disposition' ? 'inline; filename="simulation-42-trace.json"' : null) },
      arrayBuffer: () => Promise.resolve(trace) }),
  });
  let opened = null;
  const w = perfettoWindow(b, loc.origin); w.loaded = true;
  const done = P.openTrace({ simulation_id: 42 }, {
    fetch: f, location: loc, notify: (m, k) => updates.push([k, m]),
    open: (url, name) => { opened = { url, name }; return w; },
    env: { listenOn: b, setInterval: c.setInterval, clearInterval: c.clearInterval, now: c.now },
  });
  // let the fetches resolve, then tick the PING loop
  for (let i = 0; i < 20 && c.active() === 0; i++) await new Promise((r) => setImmediate(r));
  c.tick();
  assert.strictEqual(await done, 'opened');
  assert.deepStrictEqual(opened, { url: 'https://wb.example/perfetto/?testing=1', name: '_blank' });  // analytics off
  assert.ok(f.seen.includes('/api/remote-run-trace?simulation_id=42'));
  const post = w.posted.find((m) => m.msg && m.msg.perfetto);
  assert.strictEqual(post.msg.perfetto.buffer, trace);
  assert.strictEqual(post.msg.perfetto.fileName, 'simulation-42-trace.json');
  assert.strictEqual(post.msg.perfetto.url, 'https://wb.example/api/remote-run-trace?simulation_id=42');
  assert.strictEqual(post.target, 'https://wb.example');
  // Progress, then the outcome: the first update names the ~30 MB first-load cost of the
  // bundled viewer, and the last one replaces it with "opened".
  assert.strictEqual(updates[0][0], 'loading');
  assert.ok(/Loading the trace for simulation 42 in Perfetto/.test(updates[0][1]), updates[0][1]);
  assert.ok(/~30 MB/.test(updates[0][1]));
  assert.deepStrictEqual(updates[updates.length - 1][0], 'ok');
  assert.ok(/Opened the trace for simulation 42/.test(updates[updates.length - 1][1]));
}

function traceRoutes(body, headers) {
  const buf = new TextEncoder().encode(body).buffer;
  return fetchStub({
    '/api/remote-run-trace-support': () => ({ ok: true, json: () => Promise.resolve({
      supported: true, viewer: { mode: 'bundled', url: '/perfetto/', version: 'v58' } }) }),
    '/api/remote-run-trace': () => ({ ok: true, status: 200,
      headers: { get: (h) => (headers || {})[h] || null },
      arrayBuffer: () => Promise.resolve(buf) }),
  });
}

async function testEmptyTraceNeverReachesPerfetto() {
  // viva-api's document for a run that recorded nothing (dev sims 1507-1519): Perfetto
  // would open on an empty workspace. The popup is closed and the user is told why.
  const empty = '{"traceEvents": [], "displayTimeUnit": "ms", "otherData": {"simulation_id": 1519}}';
  for (const headers of [{ 'X-Trace-Events': '0' }, {}]) {   // server-counted, and the client fallback
    P._reset();
    const b = bus(); const c = clock();
    const w = perfettoWindow(b, 'https://wb.example'); w.loaded = true;
    const updates = [];
    const btn = { textContent: '⏱ Trace', disabled: false, setAttribute() {}, removeAttribute() {} };
    const res = await P.openTrace({ simulation_id: 1519 }, {
      fetch: traceRoutes(empty, headers), location: { origin: 'https://wb.example' },
      open: () => w, button: btn, notify: (m, k) => updates.push([k, m]),
      env: { listenOn: b, setInterval: c.setInterval, clearInterval: c.clearInterval, now: c.now },
    });
    assert.strictEqual(res, 'empty', JSON.stringify(headers));
    assert.ok(w.closed, 'the Perfetto popup is closed again');
    assert.ok(!w.posted.length, 'nothing is posted to it, not even a PING');
    const last = updates[updates.length - 1];
    assert.strictEqual(last[0], 'warn');
    assert.ok(/Simulation 1519 recorded no trace events/.test(last[1]), last[1]);
    assert.ok(/event sinks/.test(last[1]));
    assert.strictEqual(btn.disabled, false); assert.strictEqual(btn.textContent, '⏱ Trace');
  }
}

async function testServerCountWinsOverTheDocument() {
  // A count from the server is authoritative: a non-zero header opens Perfetto without the
  // client parsing anything (even an unparseable body is not second-guessed).
  P._reset();
  const b = bus(); const c = clock();
  const w = perfettoWindow(b, 'https://wb.example'); w.loaded = true;
  const done = P.openTrace({ simulation_id: 7 }, {
    fetch: traceRoutes('not json', { 'X-Trace-Events': '3' }), location: { origin: 'https://wb.example' },
    open: () => w, notify: () => {},
    env: { listenOn: b, setInterval: c.setInterval, clearInterval: c.clearInterval, now: c.now },
  });
  for (let i = 0; i < 20 && c.active() === 0; i++) await new Promise((r) => setImmediate(r));
  c.tick();
  assert.strictEqual(await done, 'opened');
}

async function testTraceEventCount() {
  const enc = (o) => new TextEncoder().encode(JSON.stringify(o)).buffer;
  assert.strictEqual(P.traceEventCount('0', null), 0);
  assert.strictEqual(P.traceEventCount('12', null), 12);
  assert.strictEqual(P.traceEventCount(null, enc({ traceEvents: [] })), 0);
  // metadata-only (process/thread names) draws nothing: still empty
  assert.strictEqual(P.traceEventCount(null, enc({ traceEvents: [
    { ph: 'M', name: 'process_name', pid: 1, args: { name: 'sim' } }] })), 0);
  assert.strictEqual(P.traceEventCount(null, enc([{ ph: 'X', ts: 0, dur: 1 }, { ph: 'M' }])), 1);
  assert.strictEqual(P.traceEventCount(null, new TextEncoder().encode('nope').buffer), null);
  assert.strictEqual(P.traceEventCount('garbage', enc({ traceEvents: [] })), 0, 'bad header -> parse');
  assert.strictEqual(P.traceEventCount(null, new ArrayBuffer(2 * 1024 * 1024)), null, 'big: not parsed');
}

async function testProgressStaysUntilPerfettoAnswers() {
  // The first open of the bundled viewer takes ~15 s through a tunnel: the button stays busy
  // and the status keeps saying "loading" until Perfetto answers, then says "opened".
  P._reset();
  const b = bus(); const c = clock();
  const w = perfettoWindow(b, 'https://wb.example');     // not loaded yet
  const updates = [];
  const attrs = {};
  const btn = { textContent: '⏱ Trace', disabled: false,
    setAttribute: (k, v) => { attrs[k] = v; }, removeAttribute: (k) => { delete attrs[k]; } };
  const done = P.openTrace({ simulation_id: 1485 }, {
    fetch: traceRoutes('{"traceEvents":[{"ph":"X","ts":0,"dur":1}]}', { 'X-Trace-Events': '1' }),
    location: { origin: 'https://wb.example' }, open: () => w, button: btn,
    notify: (m, k) => updates.push([k, m]),
    env: { listenOn: b, setInterval: c.setInterval, clearInterval: c.clearInterval, now: c.now },
  });
  for (let i = 0; i < 20 && c.active() === 0; i++) await new Promise((r) => setImmediate(r));
  c.advance(5000); c.tick(); c.advance(5000); c.tick();   // Perfetto still loading
  assert.strictEqual(btn.disabled, true, 'busy while Perfetto loads');
  assert.strictEqual(attrs['aria-busy'], 'true');
  assert.ok(updates.every(([k]) => k === 'loading'), JSON.stringify(updates));
  assert.ok(/fetched .*waiting for Perfetto to load/.test(updates[updates.length - 1][1]));
  w.loaded = true; c.tick();
  assert.strictEqual(await done, 'opened');
  assert.strictEqual(btn.disabled, false); assert.strictEqual(btn.textContent, '⏱ Trace');
  assert.ok(!('aria-busy' in attrs));
  assert.strictEqual(updates[updates.length - 1][0], 'ok');
}

async function testStatusBoxInTheDom() {
  // Without a notify hook the status is one element in the workbench page, updated in place.
  P._reset();
  const els = {};
  function mk(tag) {
    const e = { tag, children: [], style: {}, attrs: {}, textContent: '',
      setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
      appendChild(ch) { this.children.push(ch); if (ch.id) els[ch.id] = ch; return ch; },
      addEventListener() {} };
    return e;
  }
  const doc = { body: mk('body'), createElement: mk, getElementById: (id) => els[id] || null };
  const f = traceRoutes('{"traceEvents":[]}', { 'X-Trace-Events': '0' });
  const w = { closed: false, close() { this.closed = true; }, postMessage() {} };
  await P.openTrace({ simulation_id: 3 }, { fetch: f, location: { origin: 'https://wb.example' },
    open: () => w, document: doc });
  const box = els['viva-trace-status'];
  assert.ok(box, 'status box created');
  assert.strictEqual(doc.body.children.length, 1, 'one box, reused for every update');
  assert.strictEqual(box.attrs['data-kind'], 'warn');
  assert.ok(/no trace events/.test(box._text.textContent));
}

async function testOpenTraceErrorClosesWindow() {
  P._reset();
  const toasts = [];
  const f = fetchStub({
    '/api/remote-run-trace-support': () => ({ ok: true, json: () => Promise.resolve({
      supported: true, viewer: { mode: 'external', url: 'https://ui.perfetto.dev/' } }) }),
    '/api/remote-run-trace': () => ({ ok: false, status: 409,
      json: () => Promise.resolve({ error: 'does not support: viva-v1-trace' }) }),
  });
  const w = { closed: false, close() { this.closed = true; }, postMessage() {} };
  const res = await P.openTrace({ simulation_id: 5 }, { fetch: f, location: { origin: 'x' }, open: () => w,
    notify: (m, k) => toasts.push([k, m]) });
  assert.strictEqual(res, 'error');
  assert.ok(w.closed, 'the empty Perfetto window is closed again');
  const last = toasts[toasts.length - 1];
  assert.strictEqual(last[0], 'error');
  assert.ok(/viva-v1-trace/.test(last[1]), 'the server error is shown');
}

async function testSnapshotNeverAsks() {
  P._reset();
  globalThis.__DASH_CONFIG__ = { mode: 'snapshot' };
  const f = fetchStub({});
  const s = await P.support(f);
  assert.strictEqual(s.supported, false);
  assert.strictEqual(f.seen.length, 0, 'no request in the static snapshot');
  delete globalThis.__DASH_CONFIG__;
  P._reset();
}

(async () => {
  for (const t of [testViewerUrl, testPingUntilPongThenPost, testIgnoresPongFromElsewhere,
    testClosedWindowGivesUp, testUnparseableViewerUrlPostsNothing, testOpenTraceBundledEndToEnd, testOpenTraceErrorClosesWindow,
    testEmptyTraceNeverReachesPerfetto, testServerCountWinsOverTheDocument, testTraceEventCount,
    testProgressStaysUntilPerfettoAnswers, testStatusBoxInTheDom, testSnapshotNeverAsks]) {
    await t();
    console.log('ok -', t.name);
  }
})().catch((e) => { console.error(e); process.exit(1); });
