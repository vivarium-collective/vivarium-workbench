// tests/js/test_chat_core.js — run with: node tests/js/test_chat_core.js
// Unit tests for the DOM-free chat logic (static/chat-core.js). The frames here
// are hand-written to pin the reducer's behaviour; the contract with the REAL
// server frames is covered by tests/test_chat_frames_contract.py.
const assert = require('assert');
const C = require('../../vivarium_workbench/static/chat-core.js');

// ── NDJSON splitter: lines split across chunks, blanks, malformed ──
{
  const s = C.createSplitter();
  assert.deepStrictEqual(s.push('{"type":"text-delta","te'), [], 'partial line held back');
  assert.deepStrictEqual(s.push('xt":"hi"}\n{"type":"done"}\n\n'),
    [{ type: 'text-delta', text: 'hi' }, { type: 'done' }]);
  assert.deepStrictEqual(s.push('{"type":"done"}'), [], 'unterminated line waits');
  assert.deepStrictEqual(s.flush(), [{ type: 'done' }], 'flush emits the tail');
  assert.strictEqual(s.push('not json\n')[0].type, 'error', 'malformed line becomes an error frame');
}

// ── read turn ──
{
  const st = C.newState();
  C.startUserTurn(st, 'list the studies');
  assert.strictEqual(st.busy, true);
  C.applyFrame(st, { type: 'tool-call', tool_call_id: 'c1', tool_name: 'call_operation', args: { operation_id: 'x' } });
  C.applyFrame(st, { type: 'tool-result', tool_call_id: 'c1', tool_name: 'call_operation', ok: true, content: { status: 200, body: {} } });
  C.applyFrame(st, { type: 'text-delta', text: 'You ' });
  C.applyFrame(st, { type: 'text-delta', text: 'have 2.' });
  C.applyFrame(st, { type: 'done', pending_approval: false, messages: [{ kind: 'request' }] });
  const a = st.ui[1];
  assert.strictEqual(a.parts[0].status, 'done');
  assert.strictEqual(a.parts[1].text, 'You have 2.', 'deltas merge into one text part');
  assert.deepStrictEqual(st.transcript, [{ kind: 'request' }]);
  assert.strictEqual(st.busy, false);
  assert.deepStrictEqual(C.buildPromptRequest(st, 'again'), { messages: [{ kind: 'request' }], prompt: 'again' });
}

// ── an HTTP failure or error body marks the tool failed, not done ──
{
  const st = C.newState(); C.startUserTurn(st, 'x');
  C.applyFrame(st, { type: 'tool-call', tool_call_id: 'a', tool_name: 't', args: {} });
  C.applyFrame(st, { type: 'tool-result', tool_call_id: 'a', tool_name: 't', ok: true, content: { status: 409 } });
  C.applyFrame(st, { type: 'tool-call', tool_call_id: 'b', tool_name: 't', args: {} });
  C.applyFrame(st, { type: 'tool-result', tool_call_id: 'b', tool_name: 't', ok: true, content: { error: 'unknown operation' } });
  assert.deepStrictEqual(st.ui[1].parts.map(p => p.status), ['error', 'error']);
}

// ── approval: pause, approve one / deny one, resume only when all answered ──
{
  const st = C.newState(); C.startUserTurn(st, 'make two studies');
  ['p1', 'p2'].forEach((id, i) => {
    C.applyFrame(st, { type: 'tool-call', tool_call_id: id, tool_name: 'call_operation', args: {} });
    C.applyFrame(st, { type: 'approval-required', tool_call_id: id, tool_name: 'call_operation', args: {},
      metadata: { operation_id: 'op', method: 'POST', path: '/api/study-create', body: { name: 's' + i } } });
  });
  C.applyFrame(st, { type: 'done', pending_approval: true, messages: [{ m: 1 }] });
  assert.deepStrictEqual(st.pending, ['p1', 'p2']);
  assert.deepStrictEqual(st.ui[1].parts.map(p => p.status), ['awaiting', 'awaiting']);
  const card = C.describeApproval(st.ui[1].parts[0]);
  assert.strictEqual(card.method, 'POST'); assert.strictEqual(card.path, '/api/study-create');
  assert.strictEqual(card.body, JSON.stringify({ name: 's0' }, null, 2));

  assert.strictEqual(C.decide(st, 'p1', true), false, 'one still pending → do not resume yet');
  assert.strictEqual(C.decide(st, 'p2', false, 'not now'), true, 'all answered → resume');
  assert.deepStrictEqual(st.ui[1].parts.map(p => p.status), ['running', 'denied']);
  const req = C.buildResumeRequest(st);
  assert.deepStrictEqual(req, { messages: [{ m: 1 }], deferred_results: { approvals: { p1: true, p2: { denied: 'not now' } } } });
  assert.deepStrictEqual(st.decisions, {}, 'decisions are consumed by the request');

  // the resumed stream: a late tool-result never resurrects a denied call
  C.startResume(st);
  C.applyFrame(st, { type: 'tool-result', tool_call_id: 'p2', tool_name: 'call_operation', ok: true, content: 'The user declined' });
  C.applyFrame(st, { type: 'tool-result', tool_call_id: 'p1', tool_name: 'call_operation', ok: true, content: { status: 200 } });
  assert.deepStrictEqual(st.ui[1].parts.map(p => p.status), ['done', 'denied']);
  assert.strictEqual(st.ui.length, 2, 'a resume continues the same assistant message');
}

// ── a reused tool_call_id in a later turn must not rewrite an earlier turn's row ──
{
  const st = C.newState();
  C.startUserTurn(st, 'first');
  C.applyFrame(st, { type: 'tool-call', tool_call_id: 'same', tool_name: 'call_operation', args: { operation_id: 'a' } });
  C.applyFrame(st, { type: 'tool-result', tool_call_id: 'same', tool_name: 'call_operation', ok: true, content: { status: 200 } });
  C.applyFrame(st, { type: 'done', pending_approval: false, messages: [] });
  C.startUserTurn(st, 'second');
  C.applyFrame(st, { type: 'tool-call', tool_call_id: 'same', tool_name: 'call_operation', args: { operation_id: 'b' } });
  C.applyFrame(st, { type: 'approval-required', tool_call_id: 'same', tool_name: 'call_operation', args: {}, metadata: {} });
  assert.strictEqual(st.ui[1].parts[0].status, 'done', 'earlier turn untouched');
  assert.strictEqual(st.ui[1].parts[0].args.operation_id, 'a');
  assert.strictEqual(st.ui[3].parts[0].status, 'awaiting', 'the new call lands in the new message');
}

// ── error frame ──
{
  const st = C.newState(); C.startUserTurn(st, 'x');
  C.applyFrame(st, { type: 'error', error: 'boom' });
  assert.deepStrictEqual(st.ui[1].parts[0], { kind: 'error', text: 'boom' });
  assert.strictEqual(st.busy, false);
}

// ── sessionStorage round trip; a reload mid-stream settles orphaned 'running' tools ──
{
  const st = C.newState(); C.startUserTurn(st, 'x');
  C.applyFrame(st, { type: 'tool-call', tool_call_id: 'r', tool_name: 't', args: {} });
  C.applyFrame(st, { type: 'tool-call', tool_call_id: 'w', tool_name: 't', args: {} });
  C.applyFrame(st, { type: 'approval-required', tool_call_id: 'w', tool_name: 't', args: {}, metadata: {} });
  const back = C.restore(JSON.parse(JSON.stringify(C.snapshot(st))));
  assert.deepStrictEqual(back.ui[1].parts.map(p => p.status), ['error', 'awaiting']);
  assert.deepStrictEqual(back.pending, ['w']);
  assert.strictEqual(C.restore(null).ui.length, 0);
  assert.strictEqual(C.restore('garbage').ui.length, 0);
}

// ── markdown: escaped, minimal, safe ──
{
  const md = C.renderMarkdown;
  assert(!/<script/i.test(md('<script>alert(1)</script>')), 'raw HTML is escaped');
  assert.strictEqual(md('a **b** and `c<d>`'), '<p>a <strong>b</strong> and <code>c&lt;d&gt;</code></p>');
  assert(md('[x](https://e.com/a?b=1&c=2)').includes('href="https://e.com/a?b=1&amp;c=2"'));
  assert(!md('[x](javascript:alert(1))').includes('<a '), 'only http(s) links are linked');
  assert(!md('[x](https://e.com/" onmouseover="alert(1))').includes('onmouseover="'), 'no attribute injection');
  assert.strictEqual(md('- a\n- b\n\ntext'), '<ul><li>a</li><li>b</li></ul><p>text</p>');
  assert.strictEqual(md('1. a\n2. b'), '<ol><li>a</li><li>b</li></ol>');
  assert.strictEqual(md('```py\nx = "<1>"\n```'), '<pre><code>x = &quot;&lt;1&gt;&quot;</code></pre>');
  assert.strictEqual(md('```py\nx = 1'), '<pre><code>x = 1</code></pre>', 'an unfinished streaming fence still renders as code');
  assert.strictEqual(md('`a **not bold** b`'), '<p><code>a **not bold** b</code></p>', 'inline code is not re-parsed');
  assert.strictEqual(md(''), '');
}

// ── Retry: prompt AND resume bodies are retryable (approvals are single-use server-side) ──
{
  assert.strictEqual(C.canRetry({ messages: [], prompt: 'x' }), true);
  assert.strictEqual(C.canRetry({ messages: [], deferred_results: { approvals: { a: true } } }), true);
  assert.strictEqual(C.canRetry({ messages: [], prompt: 'x', deferred_results: {} }), false, 'exactly one of the two');
  assert.strictEqual(C.canRetry({ prompt: 'x' }), false, 'needs messages');
  assert.strictEqual(C.canRetry(null), false);
}

// ── a reload mid-turn: the persisted retry body re-offers the turn instead of wedging the chat ──
{
  const st = C.newState(); C.startUserTurn(st, 'x');
  st.retry = { messages: [], prompt: 'x' };
  C.applyFrame(st, { type: 'tool-call', tool_call_id: 't', tool_name: 'call_operation', args: {} });
  const back = C.restore(JSON.parse(JSON.stringify(C.snapshot(st))));
  assert.deepStrictEqual(back.retry, { messages: [], prompt: 'x' });
  const last = back.ui[1].parts[back.ui[1].parts.length - 1];
  assert.strictEqual(last.kind, 'error');
  assert(/interrupted/.test(last.text));
  // a turn that finished (retry cleared) restores without any interruption notice
  const done = C.newState(); C.startUserTurn(done, 'y'); done.retry = { messages: [], prompt: 'y' };
  C.applyFrame(done, { type: 'text-delta', text: 'ok' });
  C.applyFrame(done, { type: 'done', messages: [] });
  assert.strictEqual(done.retry, null);
  const clean = C.restore(JSON.parse(JSON.stringify(C.snapshot(done))));
  assert.strictEqual(clean.ui[1].parts.length, 1);
  // a tampered retry is dropped
  assert.strictEqual(C.restore({ ui: [], retry: { prompt: 5 } }).retry, null);
}

// ── corrupted sessionStorage: malformed messages are dropped, never thrown on ──
{
  const bad = { ui: [null, 3, { role: 'assistant' }, { role: 'assistant', parts: 'x' },
    { role: 'assistant', parts: [{ kind: 'tool' }] }, { role: 'user' },
    { role: 'user', text: 'kept' }, { role: 'assistant', parts: [{ kind: 'text', text: 'ok' }] }],
    transcript: 'nope', pending: 7, decisions: [] };
  const s = C.restore(bad);
  assert.deepStrictEqual(s.ui.map(m => m.role), ['user', 'assistant']);
  assert.deepStrictEqual(s.transcript, []); assert.deepStrictEqual(s.pending, []);
  assert.doesNotThrow(() => JSON.stringify(C.snapshot(s)));
}

console.log('test_chat_core: all passed');
