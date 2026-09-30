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

// ── Retry is a COMPACT, persisted record: {prompt} or {deferred_results}; messages come from the transcript ──
{
  assert.strictEqual(C.canRetry({ prompt: 'x' }), true);
  assert.strictEqual(C.canRetry({ deferred_results: { approvals: { a: true } } }), true);
  assert.strictEqual(C.canRetry({ prompt: 'x', deferred_results: {} }), false, 'exactly one of the two');
  assert.strictEqual(C.canRetry({}), false);
  assert.strictEqual(C.canRetry(null), false);
  const st = C.newState(); st.transcript = [{ m: 1 }]; st.retry = { prompt: 'x' };
  assert.deepStrictEqual(C.retryBody(st), { messages: [{ m: 1 }], prompt: 'x' });
}

// ── H1: the retry record is set by the SAME calls chat.js makes before its save(),
//    so a reload at any point after the turn starts can recover. Drive the real order. ──
{
  const persist = (st) => JSON.parse(JSON.stringify(C.snapshot(st)));   // == save() -> sessionStorage
  // send(): buildPromptRequest -> startUserTurn -> save()
  const st = C.newState(); st.transcript = [{ m: 1 }];
  C.buildPromptRequest(st, 'hello'); C.startUserTurn(st, 'hello');
  let back = C.restore(persist(st));                                    // reload right after send()
  assert.deepStrictEqual(back.retry, { prompt: 'hello' }, 'send() persists the retry record');
  assert(back.ui[1].parts.some(p => p.kind === 'error' && /interrupted/.test(p.text)));

  // resume(): decide -> buildResumeRequest -> startResume -> save()
  const r = C.newState(); C.startUserTurn(r, 'create'); r.retry = null;
  C.applyFrame(r, { type: 'tool-call', tool_call_id: 'p', tool_name: 'call_operation', args: {} });
  C.applyFrame(r, { type: 'approval-required', tool_call_id: 'p', tool_name: 'call_operation', args: {}, metadata: {} });
  C.applyFrame(r, { type: 'done', pending_approval: true, messages: [{ m: 2 }] });
  C.decide(r, 'p', true);
  const body = C.buildResumeRequest(r); C.startResume(r);
  assert.deepStrictEqual(body.deferred_results, { approvals: { p: true } });
  back = C.restore(persist(r));                                          // reload mid-resume
  assert.deepStrictEqual(back.retry, { deferred_results: { approvals: { p: true } } });
  assert.deepStrictEqual(C.retryBody(back).messages, [{ m: 2 }], 'the transcript is not duplicated in storage');
  assert.strictEqual(JSON.stringify(persist(r).retry).includes('"m":2'), false);
  assert(back.ui[1].parts.some(p => p.kind === 'error' && /interrupted/.test(p.text)), 'Retry is re-offered');

  // a finished turn clears it
  C.applyFrame(r, { type: 'done', pending_approval: false, messages: [{ m: 3 }] });
  assert.strictEqual(C.restore(persist(r)).retry, null);
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

// ── reasoning (marimo's "View reasoning" accordion): its own part kind, merged deltas ──
{
  const st = C.newState(); C.startUserTurn(st, 'q');
  C.applyFrame(st, { type: 'reasoning-delta', text: 'pond' });
  C.applyFrame(st, { type: 'reasoning-delta', text: 'ering' });
  C.applyFrame(st, { type: 'text-delta', text: 'answer' });
  assert.deepStrictEqual(st.ui[1].parts, [{ kind: 'reasoning', text: 'pondering' }, { kind: 'text', text: 'answer' }]);
  C.applyFrame(st, { type: 'done', messages: [] });
  assert.strictEqual(C.restore(JSON.parse(JSON.stringify(C.snapshot(st)))).ui[1].parts.length, 2, 'reasoning survives restore');
}

// ── modes: Manual / Ask / Agent are selectable; Code Mode is listed but disabled ──
{
  assert.deepStrictEqual(C.MODES.map(m => m.id), ['manual', 'ask', 'agent', 'code']);
  assert.strictEqual(C.MODES[0].desc, 'Pure chat, no tool usage');
  assert(C.validMode('manual') && C.validMode('ask') && C.validMode('agent'));
  assert(!C.validMode('code') && !C.validMode('yolo') && !C.validMode(undefined));
}

// ── edit + resend: rewind BOTH the UI and the model transcript to before a user message ──
{
  const req = (kind) => ({ kind: 'request', parts: [{ part_kind: kind }] });
  const res = { kind: 'response', parts: [{ part_kind: 'text' }] };
  const st = C.newState();
  st.transcript = [req('user-prompt'), res, req('tool-return'), res, req('user-prompt'), res];   // 2 user turns
  st.ui = [{ role: 'user', text: 'one' }, { role: 'assistant', parts: [] }, { role: 'user', text: 'two' }, { role: 'assistant', parts: [] }];
  st.retry = { prompt: 'two' }; st.pending = ['x'];
  assert.strictEqual(C.truncateAt(st, 1), false, 'only user messages can be edited');
  assert.strictEqual(C.truncateAt(st, 2), true);
  assert.strictEqual(st.transcript.length, 4, 'the 2nd user turn is cut from the transcript');
  assert.deepStrictEqual(st.ui.map(m => m.text), ['one', undefined]);
  assert.strictEqual(st.retry, null); assert.deepStrictEqual(st.pending, []);
  assert.strictEqual(C.truncateAt(st, 0), true);
  assert.deepStrictEqual(st.transcript, []); assert.strictEqual(st.ui.length, 0);
}

// ── history: timeAgo / date groups / store lifecycle ──
{
  const NOW = new Date('2026-09-29T12:00:00').getTime(), H = 3600e3, D = 24 * H;
  assert.strictEqual(C.timeAgo(NOW - 5e3, NOW), 'just now');
  assert.strictEqual(C.timeAgo(NOW - 61e3, NOW), '1 minute ago');
  assert.strictEqual(C.timeAgo(NOW - 2 * H, NOW), '2 hours ago');
  assert.strictEqual(C.timeAgo(NOW - 3 * D, NOW), '3 days ago');
  assert.deepStrictEqual([0, H, D, 2 * D, 8 * D].map(x => C.dateGroup(NOW - x, NOW)),
    ['Today', 'Today', 'Yesterday', 'Previous 7 days', 'Older']);

  let store = C.newStore(NOW - 9 * D);
  const st = C.newState(); C.startUserTurn(st, 'first question about   studies');
  store = C.storeNew(store, st, NOW - 9 * D);                            // "New chat": keep the old one in history
  assert.strictEqual(C.storeList(store, '', NOW).total, 1);
  assert.strictEqual(C.storeList(store, '', NOW).groups[0].group, 'Older');
  assert.strictEqual(C.storeList(store, '', NOW).groups[0].items[0].title, 'first question about studies');
  const second = C.newState(); C.startUserTurn(second, 'recent one');
  store = C.storeNew(store, second, NOW - H);
  const list = C.storeList(store, '', NOW);
  assert.deepStrictEqual(list.groups.map(g => g.group), ['Today', 'Older'], 'newest group first, dividers between groups');
  assert.strictEqual(C.storeList(store, 'RECENT', NOW).total, 1, 'case-insensitive title search');
  assert.strictEqual(C.storeList(store, 'zzz', NOW).total, 0);
  // switching restores the other chat and keeps the current one
  const cur = C.newState(); C.startUserTurn(cur, 'typing now');
  const oldId = list.groups[1].items[0].id;
  const back = C.storeSwitch(store, oldId, cur, NOW);
  assert.strictEqual(back.ui[0].text, 'first question about   studies');
  assert.strictEqual(store.active, oldId);
  assert.strictEqual(C.storeList(store, 'typing', NOW).total, 1, 'the chat we left is now in history');
  assert.strictEqual(C.storeSwitch(store, 'nope', cur, NOW), null);
  // hostile/garbled storage never throws and yields a usable store
  for (const junk of [null, 'x', {}, { active: 'a' }, { active: 'a', chats: { a: 5 } }]) {
    const s2 = C.storeRestore(junk, NOW); assert(s2.chats[s2.active]);
  }
  // bounded
  let big = C.newStore(NOW);
  for (let i = 0; i < 50; i++) { const s = C.newState(); C.startUserTurn(s, 'q' + i); big = C.storeNew(big, s, NOW + i); }
  assert(Object.keys(big.chats).length <= 31);
  assert(!Object.values(big.chats).some(c => c.id !== big.active && c.snap.ui.length === 0), 'empty chats are pruned');
}

// ── "@" context: trigger detection, insertion, picker items ──
{
  assert.deepStrictEqual(C.mentionQuery('look at @stu', 12), { start: 8, query: 'stu' });
  assert.deepStrictEqual(C.mentionQuery('@', 1), { start: 0, query: '' });
  assert.strictEqual(C.mentionQuery('mail a@b.com', 12), null, 'an @ inside a word is not a trigger');
  assert.strictEqual(C.mentionQuery('done @x now', 11), null, 'caret past the token');
  assert.deepStrictEqual(C.insertMention('look at @stu please', 8, 12, '@study/demo'),
    { text: 'look at @study/demo  please', caret: 20 });
  const items = C.contextItems({ studies: ['s1', { name: 's2' }], composites: [{ slug: 'c1' }, null] });
  assert.deepStrictEqual(items.map(i => i.value), ['@study/s1', '@study/s2', '@composite/c1']);
  assert.deepStrictEqual(C.filterItems(items, 'study/s2').map(i => i.label), ['s2']);
  assert.strictEqual(C.filterItems(items, '').length, 3);
}

// ── attachments: text only, size/count limits, inlined into the prompt ──
{
  assert.strictEqual(C.attachError([], { name: 'a.md', size: 10 }), null);
  assert(/only text files/.test(C.attachError([], { name: 'a.exe', size: 10 })));
  assert(/larger than/.test(C.attachError([], { name: 'a.txt', size: 200000 })));
  assert(/At most 5/.test(C.attachError(new Array(5).fill({ name: 'x.txt', size: 1 }), { name: 'a.txt', size: 1 })));
  assert(/in total/.test(C.attachError([{ name: 'x.txt', size: 99000 }, { name: 'y.txt', size: 99000 }], { name: 'z.txt', size: 99000 })));
  const p = C.composePrompt('hi', [{ name: 'a.py', content: 'x = 1\n```\nboom' }]);
  assert(p.startsWith('hi\n\nAttached file `a.py`:\n```\nx = 1'));
  assert.strictEqual((p.match(/```/g) || []).length, 2, 'a fence inside the file cannot break out of the block');
  assert.strictEqual(C.composePrompt('plain', []), 'plain');
}

// ── providers + model dropdown helpers (marimo groups models by provider with icon + count) ──
{
  assert.deepStrictEqual(C.PROVIDERS.map(p => p.id),
    ['openai', 'anthropic', 'google', 'ollama', 'opencode', 'bedrock', 'openai-compatible']);
  assert.strictEqual(C.providerMeta('opencode').label, 'OpenCode Go');
  assert.strictEqual(C.providerMeta('ollama').label, 'Ollama');
  assert.strictEqual(C.providerMeta('mystery').label, 'mystery', 'unknown providers degrade gracefully');
  assert.deepStrictEqual(C.mergeModels(['b', 'a'], ['a', 'c'], [' ', null, 'b', 'd']), ['b', 'a', 'c', 'd'],
    'order kept, de-duplicated, junk dropped');
  assert.strictEqual(C.mergeModels(new Array(300).fill(0).map((_, i) => 'm' + i)).length, 100, 'bounded');

  // marimo's dropdown tree: registry models + custom ones, per provider, empty providers omitted
  const registry = {
    ollama: { description: 'local', url: 'https://ollama.ai/', models: [{ name: 'GLM 5.3', model: 'glm-5.3', thinking: true }] },
    anthropic: { models: [{ name: 'Claude Opus 5.5', model: 'claude-opus-5-5' }] },
  };
  const sel = { provider: 'ollama', model: 'qwen3.6:27b' };
  const tree = C.modelTree(registry, { ollama: ['my-tune'], 'openai-compatible': ['gpt-x'] }, sel);
  assert.deepStrictEqual(tree.map(g => g.id), ['anthropic', 'ollama', 'openai-compatible'], 'marimo order; empty providers omitted');
  const oll = tree.find(g => g.id === 'ollama');
  assert.deepStrictEqual(oll.models.map(m => m.model), ['qwen3.6:27b', 'my-tune', 'glm-5.3'], 'custom first (selected, then newest), then the registry');
  assert(oll.models.some(m => m.model === 'qwen3.6:27b' && m.custom && m.on), 'the selected model is listed and marked, even if custom');
  assert(oll.models.some(m => m.model === 'glm-5.3' && !m.custom && m.thinking), 'registry models keep the reasoning flag');
  assert.strictEqual(oll.description, 'local');
  assert(tree.every(g => g.mark && g.color));
  assert.deepStrictEqual(C.modelTree({}, {}, null), [], 'nothing to list');
  assert.deepStrictEqual(C.modelTree(null, null, null), []);

  // "Enter a custom model": provider/model like marimo; anything else belongs to the fallback provider
  assert.deepStrictEqual(C.parseQualified('ollama/qwen3.6:27b', 'openai'), { provider: 'ollama', model: 'qwen3.6:27b' });
  assert.deepStrictEqual(C.parseQualified('qwen3.6:27b', 'ollama'), { provider: 'ollama', model: 'qwen3.6:27b' });
  assert.deepStrictEqual(C.parseQualified('deepseek-ai/DeepSeek-V4', 'openai-compatible'), { provider: 'openai-compatible', model: 'deepseek-ai/DeepSeek-V4' });
  assert.deepStrictEqual(C.parseQualified('ollama/', 'x'), { provider: 'x', model: 'ollama/' }, 'no model after the slash');
  assert.strictEqual(C.parseQualified('  ', 'x'), null);

  // keyboard navigation wraps and supports Home/End
  assert.strictEqual(C.nextIndex(0, 3, 'ArrowDown'), 1);
  assert.strictEqual(C.nextIndex(2, 3, 'ArrowDown'), 0);
  assert.strictEqual(C.nextIndex(0, 3, 'ArrowUp'), 2);
  assert.strictEqual(C.nextIndex(1, 3, 'Home'), 0);
  assert.strictEqual(C.nextIndex(1, 3, 'End'), 2);
  assert.strictEqual(C.nextIndex(-1, 3, 'ArrowDown'), 0, 'nothing highlighted yet');
  assert.strictEqual(C.nextIndex(0, 0, 'ArrowDown'), -1, 'empty list');
}

// ── known-models storage: newest first, per provider, removable, survives junk ──
{
  const mem = {}; globalThis.localStorage = { getItem: k => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); } };
  assert.deepStrictEqual(C.loadKnown(), {});
  assert.deepStrictEqual(C.addKnown('ollama', ['a', 'b']), ['a', 'b']);
  assert.deepStrictEqual(C.addKnown('ollama', ['c', 'a']), ['c', 'a', 'b'], 'newest first, de-duplicated');
  assert.deepStrictEqual(C.addKnown('opencode', ['x']), ['x']);
  assert.deepStrictEqual(C.removeKnown('ollama', 'a'), ['c', 'b']);
  assert.deepStrictEqual(Object.keys(C.loadKnown()).sort(), ['ollama', 'opencode']);
  mem['viv.ai.models'] = '[1,2]'; assert.deepStrictEqual(C.loadKnown(), {}, 'an array is not a valid store');
  mem['viv.ai.models'] = 'not json'; assert.deepStrictEqual(C.loadKnown(), {});
  // a hostile/garbled store must never break the panel: non-list values are dropped
  mem['viv.ai.models'] = JSON.stringify({ ollama: 'abc', opencode: { a: 1 }, google: 5, bedrock: ['ok', 3, ' '] });
  assert.deepStrictEqual(C.loadKnown(), { bedrock: ['ok'] });
  assert.doesNotThrow(() => C.addKnown('ollama', ['x']));
  assert.doesNotThrow(() => C.mergeModels('abc', { a: 1 }, 5, ['fine']));
  assert.deepStrictEqual(C.mergeModels('abc', ['fine']), ['fine']);
  delete globalThis.localStorage;
}

// ── Approve all / Deny all: one click answers every pending call, then the turn resumes ──
{
  const st = C.newState(); C.startUserTurn(st, 'do three things');
  ['a', 'b', 'c'].forEach(id => {
    C.applyFrame(st, { type: 'tool-call', tool_call_id: id, tool_name: 'call_operation', args: {} });
    C.applyFrame(st, { type: 'approval-required', tool_call_id: id, tool_name: 'call_operation', args: {}, metadata: {} });
  });
  C.applyFrame(st, { type: 'done', pending_approval: true, messages: [{ m: 1 }] });
  assert.strictEqual(st.pending.length, 3);
  assert.strictEqual(C.decideAll(st, true), true);
  assert.deepStrictEqual(st.decisions, { a: true, b: true, c: true });
  assert.deepStrictEqual(st.ui[1].parts.map(p => p.status), ['running', 'running', 'running']);
  const deny = C.newState(); C.startUserTurn(deny, 'x');
  ['p', 'q'].forEach(id => { C.applyFrame(deny, { type: 'approval-required', tool_call_id: id, tool_name: 't', args: {}, metadata: {} }); });
  C.decideAll(deny, false, 'no thanks');
  assert.deepStrictEqual(deny.decisions, { p: { denied: 'no thanks' }, q: { denied: 'no thanks' } });
  assert.strictEqual(C.decideAll(C.newState(), true), true, 'nothing pending is a no-op');
}

// ── docking: which zone is the pointer over; sizes stay usable ──
{
  assert.deepStrictEqual(C.DOCKS, ['left', 'right', 'bottom']);
  assert(C.validDock('left') && C.validDock('bottom') && !C.validDock('top') && !C.validDock(undefined));
  const W = 1000, H = 800;
  assert.strictEqual(C.dropZone(50, 300, W, H), 'left');
  assert.strictEqual(C.dropZone(950, 300, W, H), 'right');
  assert.strictEqual(C.dropZone(500, 700, W, H), 'bottom');
  assert.strictEqual(C.dropZone(50, 700, W, H), 'bottom', 'the bottom band wins over the corners');
  assert.strictEqual(C.dropZone(500, 300, W, H), null, 'the middle cancels');
  assert.strictEqual(C.dropZone(-1, 10, W, H), null); assert.strictEqual(C.dropZone(10, 10, 0, 0), null);
  assert.strictEqual(C.clampDock('left', 100, 1400, 900), 340);
  assert.strictEqual(C.clampDock('right', 5000, 1400, 900), 720);
  assert.strictEqual(C.clampDock('left', 600, 800, 900), 480, 'never more than 60% of a narrow window');
  assert.strictEqual(C.clampDock('left', 440, 400, 800), 240, 'a phone-width window still leaves room for the content');
  assert.strictEqual(C.clampDock('left', 100, 1400, 900), 340);
  assert.strictEqual(C.clampDock('bottom', 50, 1400, 900), 160);
  assert.strictEqual(C.clampDock('bottom', 9999, 1400, 900), 630, 'at most 70% of the height');
  assert.strictEqual(C.clampDock('bottom', 'junk', 1400, 900), 160);
}

console.log('test_chat_core: all passed');

// ── durable, cross-tab history: open per tab, merge other tabs' saves, tombstoned deletes ──
{
  const chatWith = (id, text, at) => {
    const st = C.newState(); st.ui.push({ role: 'user', text: text, parts: [] });
    return { id: id, title: text, updatedAt: at, snap: C.snapshot(st) };
  };
  const disk = { active: 'a', chats: { a: chatWith('a', 'first question', 100), b: chatWith('b', 'second question', 200) } };

  // a fresh tab (no active id) starts on a NEW empty chat but keeps the whole history
  const t1 = C.storeOpen(disk, null, 300);
  assert.notStrictEqual(t1.active, 'a'); assert.notStrictEqual(t1.active, 'b');
  assert.deepStrictEqual(C.storeList(t1, '', 300).groups.flatMap(g => g.items.map(i => i.id)).sort(), ['a', 'b']);
  // a reload keeps the chat this tab was on
  assert.strictEqual(C.storeOpen(disk, 'b', 300).active, 'b');
  // a stale active id falls back to a new chat instead of crashing
  assert.doesNotThrow(() => C.storeOpen(disk, 'gone', 300));
  assert.doesNotThrow(() => C.storeOpen(null, 'x')); assert.doesNotThrow(() => C.storeOpen('junk', 'x'));

  // another tab saved chat c meanwhile: our save keeps it, and our newer edit of `a` wins
  const mine = C.storeOpen(disk, 'a', 300);
  mine.chats.a = chatWith('a', 'first question, edited', 400);
  const theirs = { active: 'c', chats: { a: chatWith('a', 'first question', 100), b: chatWith('b', 'second question', 200), c: chatWith('c', 'from tab two', 350) } };
  const merged = C.storeMerge(mine, theirs);
  assert.deepStrictEqual(Object.keys(merged.chats).sort(), ['a', 'b', 'c']);
  assert.strictEqual(merged.chats.a.title, 'first question, edited'); assert.strictEqual(merged.active, 'a');

  // delete: gone for good, even if a tab that still caches it saves later
  C.storeDelete(mine, 'b');
  assert(!mine.chats.b && mine.deleted.indexOf('b') >= 0);
  const afterDelete = C.storeMerge(mine, theirs);
  assert(!afterDelete.chats.b, 'a deleted chat is not resurrected from disk or from another tab');
  const other = C.storeOpen(theirs, 'c', 500);          // the other tab still caches b
  assert(!C.storeMerge(other, { active: 'a', chats: afterDelete.chats, deleted: afterDelete.deleted }).chats.b);
  // deleting the chat being viewed empties it in place
  const viewing = C.storeOpen(disk, 'a', 300); C.storeDelete(viewing, 'a');
  assert.strictEqual(viewing.active, 'a'); assert.strictEqual(viewing.chats.a.snap.ui.length, 0);
  assert.strictEqual(C.storeDelete(viewing, 'nope'), null);
  // garbage from disk never throws
  assert.doesNotThrow(() => C.storeMerge(mine, { chats: { z: 5, y: { snap: 'x' } }, deleted: 'nope' }));
  assert.doesNotThrow(() => C.storeMerge(mine, null));
}


// ── "Approve all" lists exactly what it will approve ──
{
  const st = C.newState();
  st.ui.push({ role: 'assistant', parts: [
    { kind: 'tool', id: 't1', name: 'call_operation', args: {}, approval: { method: 'POST', path: '/api/catalog-install', operation_id: 'catalog-install' } },
    { kind: 'tool', id: 't2', name: 'call_operation', args: {}, approval: { method: 'POST', path: '/api/study-create' } }] });
  st.pending = ['t1', 't2', 'gone'];
  const rows = st.pending.map(id => { const t = C.findTool(st, id); return t ? C.describeApproval(t) : null; });
  assert.deepStrictEqual(rows.map(r => r && (r.method + ' ' + r.path)), ['POST /api/catalog-install', 'POST /api/study-create', null]);
}

// ── Ollama lists what is INSTALLED, not marimo's catalogue ──
{
  const registry = { ollama: { description: 'catalogue', models: [{ name: 'GLM 5.3', model: 'glm-5.3' }] }, anthropic: { models: [{ name: 'Opus', model: 'claude-opus-5-5' }] } };
  const inst = { ollama: { models: ['qwen3.6:27b', 'nemotron-mini:latest', 7, ''], note: '' } };
  const tree = C.modelTree(registry, { ollama: ['glm-5.3'] }, { provider: 'ollama', model: 'qwen3.6:27b' }, inst);
  const oll = tree.find(g => g.id === 'ollama');
  assert.deepStrictEqual(oll.models.map(m => m.model), ['glm-5.3', 'qwen3.6:27b', 'nemotron-mini:latest'],
    'installed models replace the catalogue; a custom entry stays; junk is dropped');
  assert(!oll.models.some(m => m.model === 'glm-5.3' && !m.custom), 'a catalogue model you do not have is not offered as installed');
  assert(oll.models.find(m => m.model === 'qwen3.6:27b').on && !oll.models.find(m => m.model === 'qwen3.6:27b').custom);
  assert.strictEqual(oll.description, '', 'the catalogue blurb is not shown for a live list');
  assert(tree.find(g => g.id === 'anthropic').models.length === 1, 'other providers keep marimo\'s registry');
  // Ollama down / empty: the provider stays, with the reason, instead of vanishing
  const down = C.modelTree(registry, {}, null, { ollama: { models: [], note: 'Ollama did not answer' } }).find(g => g.id === 'ollama');
  assert(down && down.models.length === 0 && down.note === 'Ollama did not answer' && down.live);
  // no lookup at all -> the registry, as before
  assert.strictEqual(C.modelTree(registry, {}, null).find(g => g.id === 'ollama').models[0].model, 'glm-5.3');
}
