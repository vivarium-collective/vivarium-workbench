// chat-core.js — the DOM-free logic of the built-in chat (docs/ai-chat.md).
//
//   * createSplitter()      NDJSON stream chunks -> frames (handles split lines)
//   * newState / applyFrame the transcript state machine driven by the frames
//                           POST /api/chat/turn streams (lib/ai_chat.py)
//   * decide / buildRequest approvals -> the next request body
//   * renderMarkdown        small, HTML-escaping markdown -> safe HTML
//
// chat.js owns the DOM; this file is pure so tests/js/test_chat_core.js can run
// it under plain node (same convention as progress-track.js).
(function (global) {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ── NDJSON ────────────────────────────────────────────────────────────────
  function createSplitter() {
    var buf = '';
    function parse(line) {
      line = line.trim();
      if (!line) return null;
      try { return JSON.parse(line); } catch (e) {
        return { type: 'error', error: 'malformed frame from server' };
      }
    }
    return {
      push: function (text) {
        buf += text;
        var lines = buf.split('\n');
        buf = lines.pop();
        return lines.map(parse).filter(Boolean);
      },
      flush: function () {
        var f = parse(buf); buf = '';
        return f ? [f] : [];
      },
    };
  }

  // ── State ─────────────────────────────────────────────────────────────────
  // ui:         [{role:'user', text} | {role:'assistant', parts:[...]}]
  //             part = {kind:'text', text} | {kind:'error', text}
  //                  | {kind:'tool', id, name, args, status, approval?, result?}
  //             tool status: running | awaiting | done | denied | error
  // transcript: the pydantic-ai messages (JSON) the server returned in `done`
  // pending:    tool_call_ids awaiting the user's Approve/Deny
  // decisions:  tool_call_id -> true | {denied: reason}   (sent as deferred_results)
  // retry:      the request body of the turn in flight (persisted): re-sending it after a
  //             failure/stop/reload is safe — approvals are single-use server-side
  function newState() {
    return { ui: [], transcript: [], pending: [], decisions: {}, busy: false, retry: null };
  }

  function lastAssistant(state) {
    var m = state.ui[state.ui.length - 1];
    if (!m || m.role !== 'assistant') {
      m = { role: 'assistant', parts: [] };
      state.ui.push(m);
    }
    return m;
  }

  // Tool calls are only ever updated within the assistant message being built (a
  // resumed turn continues that same message). Never search earlier messages: a
  // reused id must not rewrite a previous turn's row.
  function findTool(state, id) {
    var m = state.ui[state.ui.length - 1];
    if (!m || m.role !== 'assistant') return null;
    for (var j = 0; j < m.parts.length; j++) {
      if (m.parts[j].kind === 'tool' && m.parts[j].id === id) return m.parts[j];
    }
    return null;
  }

  function ensureTool(state, id, name) {
    var t = findTool(state, id);
    if (!t) {
      t = { kind: 'tool', id: id, name: name || 'tool', args: {}, status: 'running' };
      lastAssistant(state).parts.push(t);
    }
    return t;
  }

  function startUserTurn(state, prompt) {
    state.ui.push({ role: 'user', text: prompt });
    state.ui.push({ role: 'assistant', parts: [] });
    state.pending = []; state.decisions = {}; state.busy = true;
    // The retry record is set HERE (not later, in the network code) so the save() that
    // follows persists it: a reload at any point after the turn starts can recover.
    state.retry = { prompt: prompt };
    return state;
  }

  function startResume(state) { state.busy = true; return state; }

  function isFailure(content) {
    return !!content && typeof content === 'object' &&
      (typeof content.error === 'string' ||
       (typeof content.status === 'number' && content.status >= 400));
  }

  function applyFrame(state, f) {
    var m = lastAssistant(state);
    switch (f.type) {
      case 'text-delta': {
        var last = m.parts[m.parts.length - 1];
        if (last && last.kind === 'text') last.text += f.text;
        else m.parts.push({ kind: 'text', text: f.text });
        break;
      }
      case 'reasoning-delta': {
        var lp = m.parts[m.parts.length - 1];
        if (lp && lp.kind === 'reasoning') lp.text += f.text;
        else m.parts.push({ kind: 'reasoning', text: f.text });
        break;
      }
      case 'tool-call': {
        var t = ensureTool(state, f.tool_call_id, f.tool_name);
        t.args = f.args || {};
        break;
      }
      case 'approval-required': {
        var a = ensureTool(state, f.tool_call_id, f.tool_name);
        a.args = f.args || a.args;
        a.status = 'awaiting';
        a.approval = f.metadata || {};
        if (state.pending.indexOf(f.tool_call_id) < 0) state.pending.push(f.tool_call_id);
        break;
      }
      case 'tool-result': {
        var r = ensureTool(state, f.tool_call_id, f.tool_name);
        r.result = f.content;
        if (r.status !== 'denied') r.status = (f.ok === false || isFailure(f.content)) ? 'error' : 'done';
        break;
      }
      case 'done':
        // The turn ended (normally, paused for approval, or checkpointed after a
        // failure): the transcript advanced and there is nothing left to retry.
        state.transcript = f.messages || [];
        state.busy = false;
        state.retry = null;
        break;
      case 'error':
        m.parts.push({ kind: 'error', text: f.error || 'error' });
        state.busy = false;
        break;
    }
    return state;
  }

  // ── Approvals ─────────────────────────────────────────────────────────────
  // Records the user's decision. Returns true once EVERY pending call has been
  // answered (i.e. the caller should now resume the turn).
  function decide(state, id, approved, reason) {
    var t = findTool(state, id);
    state.decisions[id] = approved ? true : { denied: reason || 'The user declined this action.' };
    if (t) t.status = approved ? 'running' : 'denied';
    state.pending = state.pending.filter(function (p) { return p !== id; });
    return state.pending.length === 0;
  }

  // Answer EVERY pending approval the same way (the "Approve all / Deny all" click).
  function decideAll(state, approved, reason) {
    state.pending.slice().forEach(function (id) { decide(state, id, approved, reason); });
    return state.pending.length === 0;
  }

  function buildPromptRequest(state, prompt) {
    return { messages: state.transcript, prompt: prompt };
  }

  // Retry is a COMPACT record — {prompt} or {deferred_results} — persisted with the
  // snapshot; the messages come from state.transcript (which only advances on `done`,
  // and `done` clears the record), so the transcript is never stored twice. A resume is
  // safe to re-send: the server claims each approved call once (tool_call_id + digest).
  function canRetry(r) {
    if (!r || typeof r !== 'object') return false;
    var prompt = typeof r.prompt === 'string';
    var resume = !!r.deferred_results && typeof r.deferred_results === 'object';
    return prompt !== resume;
  }

  function retryBody(state) {
    return Object.assign({ messages: state.transcript }, state.retry);
  }

  function buildPromptRequest(state, prompt) {
    return { messages: state.transcript, prompt: prompt };
  }

  function buildResumeRequest(state) {
    var approvals = state.decisions;
    state.retry = { deferred_results: { approvals: approvals } };   // persisted by the save() that follows
    var body = { messages: state.transcript, deferred_results: { approvals: approvals } };
    state.decisions = {};
    return body;
  }

  var LABELS = { running: 'Running', awaiting: 'Awaiting approval', done: 'Done',
                 denied: 'Denied', error: 'Failed' };
  function statusLabel(status) { return LABELS[status] || status; }

  // What the approval card shows: the operation, its method/path and the body.
  // Bidirectional-control and zero-width characters can make the text shown differ from the text that runs
  // (a reordered or invisible character), so an approval card shows them as \uXXXX instead of rendering them.
  // Every format (Cf: bidi marks/overrides, zero-width, soft hyphen, tag characters), control (Cc, except tab/newline),
  // line/paragraph separator and the common invisible fillers and variation selectors.
  var HIDDEN = /(?![\t\n\r])[\p{Cc}\p{Cf}\p{Zl}\p{Zp}͏ᅟᅠㅤ⠀︀-️\u{E0100}-\u{E01EF}]/gu;
  function revealHidden(s) {
    return String(s).replace(HIDDEN, function (c) {
      var cp = c.codePointAt(0).toString(16).toUpperCase();
      return cp.length > 4 ? '\\u{' + cp + '}' : '\\u' + ('0000' + cp).slice(-4);
    });
  }
  function revealDeep(v) {
    if (typeof v === 'string') return revealHidden(v);
    if (Array.isArray(v)) return v.map(revealDeep);
    if (v && typeof v === 'object') { var o = {}; Object.keys(v).forEach(function (k) { o[revealHidden(k)] = revealDeep(v[k]); }); return o; }
    return v;
  }

  function describeApproval(part) {
    var ap = part.approval || {};
    var body = ap.body !== undefined && ap.body !== null ? ap.body
      : (part.args && part.args.body !== undefined ? part.args.body : null);
    var query = ap.query && Object.keys(ap.query).length ? ap.query : null;
    var raw = JSON.stringify({ q: query, b: body, p: ap.path, s: ap.summary, e: ap.effect || null, o: ap.operation_id, m: ap.method });
    var bodyText = body === null ? '' : revealHidden(JSON.stringify(body, null, 2));
    return {
      title: revealHidden(ap.operation_id || (part.args && part.args.operation_id) || part.name || ''),
      method: revealHidden(ap.method || ''), path: revealHidden(ap.path || ''), summary: revealHidden(ap.summary || ''),
      query: query ? revealHidden(JSON.stringify(query, null, 2)) : '',
      body: bodyText,
      effect: ap.effect ? revealDeep(ap.effect) : null,
      hidden: (HIDDEN.lastIndex = 0, HIDDEN.test(raw)) && !(HIDDEN.lastIndex = 0),
      stats: { chars: bodyText.length, lines: bodyText ? bodyText.split('\n').length : 0 },
    };
  }

  function validMessage(m) {
    if (!m || typeof m !== 'object') return false;
    if (m.role === 'user') return typeof m.text === 'string';
    if (m.role !== 'assistant' || !Array.isArray(m.parts)) return false;
    return m.parts.every(function (p) {
      return p && typeof p === 'object' &&
        ((p.kind === 'text' || p.kind === 'error' || p.kind === 'notice' || p.kind === 'reasoning') ? typeof p.text === 'string'
          : p.kind === 'tool' ? typeof p.id === 'string' && typeof p.status === 'string' : false);
    });
  }

  // What is safe/worth keeping in sessionStorage.
  function snapshot(state) {
    return { ui: state.ui, transcript: state.transcript, pending: state.pending,
             decisions: state.decisions, retry: state.retry };
  }
  function restore(saved) {
    var s = newState();
    if (!saved || typeof saved !== 'object') return s;
    // sessionStorage is user-controllable and unversioned: keep only well-formed messages.
    if (Array.isArray(saved.ui)) s.ui = saved.ui.filter(validMessage);
    if (Array.isArray(saved.transcript)) s.transcript = saved.transcript;
    if (Array.isArray(saved.pending)) s.pending = saved.pending;
    if (saved.decisions && typeof saved.decisions === 'object') s.decisions = saved.decisions;
    if (canRetry(saved.retry)) s.retry = saved.retry;
    // A reload mid-stream leaves 'running' tools that will never report back;
    // anything not awaiting approval is settled as failed.
    s.ui.forEach(function (m) {
      (m.parts || []).forEach(function (p) {
        if (p.kind === 'tool' && p.status === 'running' && s.pending.indexOf(p.id) < 0) p.status = 'error';
      });
    });
    // A reload mid-turn left a turn that will never finish: say so, and (retry was
    // persisted) let the user re-send it instead of stranding the chat.
    var lastMsg = s.ui[s.ui.length - 1];
    if (s.retry && lastMsg && lastMsg.role === 'assistant' && s.pending.length === 0) {
      var tail = lastMsg.parts[lastMsg.parts.length - 1];
      if (!tail || tail.kind !== 'error') {
        lastMsg.parts.push({ kind: 'error', text: 'This turn was interrupted before it finished.' });
      }
    }
    return s;
  }

  // ── Markdown (escape first; only http(s) links) ───────────────────────────
  function inline(s) {
    var codes = [];
    s = s.replace(/`([^`\n]+)`/g, function (_, c) { codes.push('<code>' + c + '</code>'); return '\u0000C' + (codes.length - 1) + '\u0000'; });
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
         .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
         .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
                  '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return s.replace(/\u0000C(\d+)\u0000/g, function (_, i) { return codes[+i]; });
  }

  function renderMarkdown(src) {
    var blocks = [];
    var text = String(src == null ? '' : src);
    function stash(code) { blocks.push('<pre><code>' + esc(code.replace(/\n$/, '')) + '</code></pre>'); return '\u0000B' + (blocks.length - 1) + '\u0000'; }
    text = text.replace(/```[\w-]*\n([\s\S]*?)```/g, function (_, c) { return stash(c); });
    text = text.replace(/```[\w-]*\n([\s\S]*)$/, function (_, c) { return stash(c); });   // still-streaming fence
    text = esc(text);
    var out = [], para = [], list = null;
    function flushPara() { if (para.length) { out.push('<p>' + inline(para.join('<br>')) + '</p>'); para = []; } }
    function flushList() { if (list) { out.push('<' + list.tag + '>' + list.items.map(function (i) { return '<li>' + inline(i) + '</li>'; }).join('') + '</' + list.tag + '>'); list = null; } }
    text.split('\n').forEach(function (line) {
      var b = /^\u0000B(\d+)\u0000$/.exec(line.trim());
      var ul = /^\s*[-*]\s+(.*)$/.exec(line), ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      var h = /^(#{1,4})\s+(.*)$/.exec(line);
      if (b) { flushPara(); flushList(); out.push(blocks[+b[1]]); }
      else if (ul || ol) {
        flushPara();
        var tag = ul ? 'ul' : 'ol';
        if (list && list.tag !== tag) flushList();
        if (!list) list = { tag: tag, items: [] };
        list.items.push((ul || ol)[1]);
      }
      else if (h) { flushPara(); flushList(); out.push('<h4>' + inline(h[2]) + '</h4>'); }
      else if (!line.trim()) { flushPara(); flushList(); }
      else { flushList(); para.push(line); }
    });
    flushPara(); flushList();
    return out.join('');
  }


  // ── Modes (marimo's footer dropdown, mapped onto what the workbench can do) ──────────
  // manual = pure chat, no tools · ask = read-only tools · agent = read+write tools (every
  // write still pauses for approval — a standing decision) · code = n/a (no kernel).
  var MODES = [
    { id: 'manual', label: 'Manual', desc: 'Pure chat, no tool usage', icon: 'message' },
    { id: 'ask', label: 'Ask', desc: 'AI with access to read-only workspace tools', icon: 'book' },
    { id: 'agent', label: 'Agent', desc: 'AI with access to read and write tools — every change still asks for your approval', icon: 'hat' },
    { id: 'code', label: 'Code Mode (beta)', desc: 'Not available in the workbench: there is no kernel to run code in', icon: 'code', disabled: true },
  ];
  function validMode(id) {
    return MODES.some(function (m) { return m.id === id && !m.disabled; });
  }

  // ── Edit a previous user message and resend from there ───────────────────────────────
  function isUserRequest(m) {
    return !!m && m.kind === 'request' && Array.isArray(m.parts) &&
      m.parts.some(function (p) { return p && p.part_kind === 'user-prompt'; });
  }
  // Rewind to just before the user message at ui[index]: drops it and everything after,
  // in BOTH the UI and the model transcript. Returns false if index isn't a user message.
  function truncateAt(state, index) {
    var target = state.ui[index];
    if (!target || target.role !== 'user') return false;
    var k = state.ui.slice(0, index).filter(function (m) { return m.role === 'user'; }).length;
    var seen = 0, cut = -1;
    for (var i = 0; i < state.transcript.length; i++) {
      if (isUserRequest(state.transcript[i])) {
        if (seen === k) { cut = i; break; }
        seen++;
      }
    }
    if (cut >= 0) state.transcript = state.transcript.slice(0, cut);
    state.ui = state.ui.slice(0, index);
    state.pending = []; state.decisions = {}; state.busy = false; state.retry = null;
    return true;
  }

  // ── Chat history (marimo's "Previous chats" popover) ─────────────────────────────────
  function timeAgo(ts, now) {
    var s = Math.max(0, Math.floor(((now || Date.now()) - ts) / 1000));
    if (s < 60) return 'just now';
    var m = Math.floor(s / 60);
    if (m < 60) return m + (m === 1 ? ' minute ago' : ' minutes ago');
    var h = Math.floor(m / 60);
    if (h < 24) return h + (h === 1 ? ' hour ago' : ' hours ago');
    var d = Math.floor(h / 24);
    return d + (d === 1 ? ' day ago' : ' days ago');
  }
  function dateGroup(ts, now) {
    var a = new Date(now || Date.now()); a.setHours(0, 0, 0, 0);
    var t = new Date(ts); t.setHours(0, 0, 0, 0);
    var d = Math.round((a - t) / 86400000);
    return d <= 0 ? 'Today' : d === 1 ? 'Yesterday' : d < 7 ? 'Previous 7 days' : 'Older';
  }
  var MAX_CHATS = 30;
  function newId() { return 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
  function titleOf(state) {
    var u = state.ui.filter(function (m) { return m.role === 'user'; })[0];
    return u ? String(u.text).replace(/\s+/g, ' ').trim().slice(0, 60) || 'New chat' : 'New chat';
  }
  function newStore(now) {
    var id = newId(), s = { active: id, chats: {} };
    s.chats[id] = { id: id, title: 'New chat', updatedAt: now || Date.now(), snap: snapshot(newState()) };
    return s;
  }
  function storeUpsert(store, state, now) {
    var c = store.chats[store.active];
    c.snap = snapshot(state); c.title = titleOf(state);
    if (state.ui.length) c.updatedAt = now || Date.now();
    return store;
  }
  function storePrune(store) {
    var ids = Object.keys(store.chats).filter(function (id) { return id !== store.active; });
    ids.forEach(function (id) { if (!store.chats[id].snap.ui.length) delete store.chats[id]; });
    ids = Object.keys(store.chats).filter(function (id) { return id !== store.active; });
    ids.sort(function (a, b) { return store.chats[b].updatedAt - store.chats[a].updatedAt; });
    ids.slice(MAX_CHATS - 1).forEach(function (id) { delete store.chats[id]; });
    return store;
  }
  // Start a new chat (the current one is kept in history if it has any messages).
  function storeNew(store, state, now) {
    storeUpsert(store, state, now);
    var id = newId();
    store.chats[id] = { id: id, title: 'New chat', updatedAt: now || Date.now(), snap: snapshot(newState()) };
    store.active = id;
    return storePrune(store);
  }
  // Switch to a stored chat; returns its restored state (or null).
  function storeSwitch(store, id, state, now) {
    if (!store.chats[id]) return null;
    storeUpsert(store, state, now);
    store.active = id;
    return restore(store.chats[id].snap);
  }
  function storeRestore(saved, now) {
    if (!saved || typeof saved !== 'object' || typeof saved.active !== 'string' ||
        !saved.chats || typeof saved.chats !== 'object' || !saved.chats[saved.active]) return newStore(now);
    var out = { active: saved.active, chats: {} };
    Object.keys(saved.chats).forEach(function (id) {
      var c = saved.chats[id];
      if (c && typeof c === 'object' && c.snap && typeof c.snap === 'object') {
        out.chats[id] = { id: id, title: String(c.title || 'New chat'), updatedAt: +c.updatedAt || 0,
                          snap: snapshot(restore(c.snap)) };
      }
    });
    return out.chats[out.active] ? out : newStore(now);
  }
  // Open the shared history for THIS tab: the saved chats, with `activeId` (this tab's own
  // conversation, kept in sessionStorage) active if it still exists — otherwise a fresh chat, so a
  // new tab starts empty while the history stays reachable.
  function storeOpen(saved, activeId, now) {
    var probe = saved && typeof saved === 'object' && saved.chats && typeof saved.chats === 'object' ? saved.chats : null;
    if (!probe) return newStore(now);
    var ids = Object.keys(probe);
    var keep = ids.filter(function (id) { return id === activeId; })[0] || ids[0];
    if (!keep) return newStore(now);
    var out = storeRestore({ active: keep, chats: probe }, now);
    if (Array.isArray(saved.deleted)) out.deleted = saved.deleted.filter(function (x) { return typeof x === 'string'; }).slice(-200);
    if (out.active !== activeId) storeNew(out, restore(out.chats[out.active].snap), now);
    return out;
  }
  // Fold in what other tabs saved since we last looked: their chats are kept, ours wins where it is
  // newer (and always for the chat this tab is editing). A deleted chat is remembered by id
  // (`deleted`, in both copies) so a tab that still caches it cannot bring it back.
  function storeMerge(mine, theirs) {
    var dead = {};
    [mine.deleted, theirs && theirs.deleted].forEach(function (l) {
      (Array.isArray(l) ? l : []).forEach(function (id) { if (typeof id === 'string') dead[id] = true; });
    });
    var disk = theirs && typeof theirs === 'object' && theirs.chats && typeof theirs.chats === 'object' ? theirs.chats : {};
    var chats = {};
    Object.keys(disk).forEach(function (id) {
      if (dead[id] || !disk[id] || typeof disk[id] !== 'object' || !disk[id].snap) return;
      var one = {}; one[id] = disk[id];
      var r = storeRestore({ active: id, chats: one }, 0).chats[id];
      if (r && r.snap.ui.length) chats[id] = r;
    });
    Object.keys(mine.chats).forEach(function (id) {
      var m = mine.chats[id];
      if (dead[id] || !m) return;
      if (id === mine.active || (m.snap.ui.length && (!chats[id] || m.updatedAt >= chats[id].updatedAt))) chats[id] = m;
    });
    return { active: mine.active, chats: chats, deleted: Object.keys(dead).slice(-200) };
  }
  // Delete a chat from history; deleting the one being viewed leaves an empty chat in its place.
  function storeDelete(store, id) {
    if (!store.chats[id]) return null;
    if (id === store.active) { store.chats[id] = { id: id, title: 'New chat', updatedAt: Date.now(), snap: snapshot(newState()) }; return store; }
    delete store.chats[id];
    store.deleted = (store.deleted || []).concat(id).slice(-200);
    return store;
  }
  // History rows: newest first, filtered by title, grouped by date (empty chats hidden).
  function storeList(store, query, now) {
    var q = String(query || '').toLowerCase();
    var rows = Object.keys(store.chats).map(function (id) { return store.chats[id]; })
      .filter(function (c) { return c.snap.ui.length > 0 && c.title.toLowerCase().indexOf(q) >= 0; })
      .sort(function (a, b) { return b.updatedAt - a.updatedAt; });
    var groups = [];
    rows.forEach(function (c) {
      var g = dateGroup(c.updatedAt, now);
      var last = groups[groups.length - 1];
      if (!last || last.group !== g) { last = { group: g, items: [] }; groups.push(last); }
      last.items.push({ id: c.id, title: c.title, updatedAt: c.updatedAt, active: c.id === store.active });
    });
    return { groups: groups, total: rows.length };
  }

  // ── "@" context mentions (marimo's context trigger) ──────────────────────────────────
  function mentionQuery(text, caret) {
    var m = /(^|\s)@([\w\/.\-]*)$/.exec(String(text).slice(0, caret));
    return m ? { start: caret - m[2].length - 1, query: m[2] } : null;
  }
  function insertMention(text, start, caret, mention) {
    return { text: text.slice(0, start) + mention + ' ' + text.slice(caret), caret: start + mention.length + 1 };
  }
  // Items for the picker, from GET /api/workspace-manifest.
  function contextItems(manifest) {
    var out = [];
    function names(list) {
      return (Array.isArray(list) ? list : []).map(function (x) {
        return typeof x === 'string' ? x : (x && (x.name || x.slug || x.id)) || '';
      }).filter(Boolean);
    }
    names(manifest && manifest.studies).forEach(function (n) { out.push({ group: 'Studies', value: '@study/' + n, label: n }); });
    names(manifest && manifest.composites).forEach(function (n) { out.push({ group: 'Composites', value: '@composite/' + n, label: n }); });
    return out;
  }
  function filterItems(items, query) {
    var q = String(query || '').toLowerCase().replace(/^(study|composite)\//, '');
    return items.filter(function (i) { return i.label.toLowerCase().indexOf(q) >= 0; });
  }

  // ── File attachments: text files are inlined into the prompt ─────────────────────────
  var ATTACH = { maxFiles: 5, maxBytes: 100000, maxTotal: 200000,
    exts: ['txt', 'md', 'json', 'yaml', 'yml', 'csv', 'tsv', 'py', 'log', 'toml', 'xml', 'html', 'ini', 'cfg'] };
  function attachError(files, next) {
    var ext = String(next.name).split('.').pop().toLowerCase();
    if (ATTACH.exts.indexOf(ext) < 0) return next.name + ': only text files can be attached (' + ATTACH.exts.join(', ') + ')';
    if (next.size > ATTACH.maxBytes) return next.name + ' is larger than ' + (ATTACH.maxBytes / 1000) + ' KB';
    if (files.length >= ATTACH.maxFiles) return 'At most ' + ATTACH.maxFiles + ' files per message';
    var total = files.reduce(function (n, f) { return n + f.size; }, next.size);
    return total > ATTACH.maxTotal ? 'Attachments are limited to ' + (ATTACH.maxTotal / 1000) + ' KB in total' : null;
  }
  function composePrompt(text, files) {
    var out = String(text || '');
    (files || []).forEach(function (f) {
      out += '\n\nAttached file `' + f.name + '`:\n```\n' + f.content.replace(/```/g, '``​`') + '\n```';
    });
    return out;
  }


  // ── Providers (marimo's AI Providers order) + the model dropdown ─────────────────────
  var PROVIDERS = [
    { id: 'openai', label: 'OpenAI', color: '#10a37f', mark: 'O' },
    { id: 'anthropic', label: 'Anthropic', color: '#d97757', mark: 'A' },
    { id: 'google', label: 'Google', color: '#4285f4', mark: 'G' },
    { id: 'ollama', label: 'Ollama', color: '#4b5563', mark: 'Ol' },
    { id: 'opencode', label: 'OpenCode Go', color: '#2563eb', mark: 'OC' },
    { id: 'bedrock', label: 'AWS Bedrock', color: '#f59e0b', mark: 'AWS' },
    { id: 'openai-compatible', label: 'OpenAI-compatible', color: '#6366f1', mark: '⇄' },
  ];
  function providerMeta(id) {
    var m = PROVIDERS.filter(function (p) { return p.id === id; })[0];
    return m || { id: id, label: String(id), color: '#6b7280', mark: String(id).slice(0, 2).toUpperCase() };
  }
  // Merge model-id lists: order kept, de-duplicated, blanks/non-strings dropped, bounded.
  function mergeModels() {
    var seen = {}, out = [];
    for (var i = 0; i < arguments.length; i++) {
      if (!Array.isArray(arguments[i])) continue;       // localStorage is untrusted: a non-list is ignored
      arguments[i].forEach(function (m) {
        if (typeof m !== 'string') return;
        m = m.trim();
        if (m && !seen[m]) { seen[m] = true; out.push(m); }
      });
    }
    return out.slice(0, 100);
  }
  // marimo's model dropdown tree: one entry per provider, each with its registry models
  // (static/ai-models.js, generated from marimo's llm-info) plus the user's custom models
  // (`known`, browser-local) and the selected model when it is neither. Providers with nothing
  // to list are omitted, as in marimo — except an endpoint provider (OpenAI-compatible): it has no catalogue, but the
  // Model menu is the only provider chooser, so it stays listed with a hint on how to use it.
  var ENDPOINT_NOTES = {
    'openai-compatible': 'Any OpenAI-style endpoint (vLLM, OpenRouter, …): enter openai-compatible/<model> below, then set its Base URL.',
  };
  function modelTree(registry, known, selected, installed) {
    registry = registry || {};
    installed = installed || {};
    return PROVIDERS.map(function (p) {
      var reg = registry[p.id] || {};
      var here = installed[p.id];          // {models:[names], note} — what THIS machine actually has (Ollama)
      var models = here && Array.isArray(here.models)
        ? here.models.filter(function (n) { return typeof n === 'string' && n; })
            .map(function (n) { return { model: n, name: n, description: '', thinking: false, custom: false }; })
        : (reg.models || []).map(function (m) {
            return { model: m.model, name: m.name || m.model, description: m.description || '', thinking: !!m.thinking, custom: false };
          });
      var have = {};
      models.forEach(function (m) { have[m.model] = true; });
      var extra = mergeModels(selected && selected.provider === p.id ? [selected.model] : [], known && known[p.id]);
      var customs = extra.filter(function (id) { return !have[id]; }).map(function (id) {
        return { model: id, name: id, description: '', thinking: false, custom: true };
      });
      models = customs.concat(models);
      models.forEach(function (m) { m.on = !!(selected && selected.provider === p.id && selected.model === m.model); });
      return { id: p.id, label: p.label, color: p.color, mark: p.mark, description: here ? '' : (reg.description || ''),
               url: reg.url || '', note: (here && here.note) || ENDPOINT_NOTES[p.id] || '', models: models, live: !!here };
    }).filter(function (g) { return g.models.length > 0 || g.live || !!ENDPOINT_NOTES[g.id]; });   // a provider we asked about stays, with its note
  }

  // marimo qualifies custom models as "provider/model" (e.g. ollama/qwen3.6:27b). A first segment
  // that is not a known provider id means the whole text is the model, for `fallback`.
  function parseQualified(text, fallback) {
    var t = String(text || '').trim();
    var i = t.indexOf('/');
    if (i > 0 && PROVIDERS.some(function (p) { return p.id === t.slice(0, i); }) && t.slice(i + 1).trim()) {
      return { provider: t.slice(0, i), model: t.slice(i + 1).trim() };
    }
    return t ? { provider: fallback || null, model: t } : null;
  }
  // Keyboard index for a listbox (wraps; Home/End; -1 = nothing highlighted yet).
  function nextIndex(i, n, key) {
    if (n <= 0) return -1;
    if (key === 'Home') return 0;
    if (key === 'End') return n - 1;
    if (key === 'ArrowDown') return i < 0 ? 0 : (i + 1) % n;
    if (key === 'ArrowUp') return i < 0 ? n - 1 : (i - 1 + n) % n;
    return i;
  }

  // Known models per provider (marimo's "Add model" list): browser-local, non-secret.
  var KNOWN_KEY = 'viv.ai.models';
  function loadKnown() {
    try {
      var k = JSON.parse(localStorage.getItem(KNOWN_KEY)), out = {};
      if (!k || typeof k !== 'object' || Array.isArray(k)) return {};
      Object.keys(k).forEach(function (p) { if (Array.isArray(k[p])) out[p] = mergeModels(k[p]); });
      return out;
    } catch (e) { return {}; }
  }
  function saveKnown(k) { try { localStorage.setItem(KNOWN_KEY, JSON.stringify(k)); } catch (e) { /* private mode */ } }
  function addKnown(provider, ids) {
    var k = loadKnown();
    k[provider] = mergeModels(ids, k[provider]);          // newest first
    saveKnown(k);
    return k[provider];
  }
  function removeKnown(provider, id) {
    var k = loadKnown();
    k[provider] = (k[provider] || []).filter(function (m) { return m !== id; });
    saveKnown(k);
    return k[provider];
  }

  // ── Docking (PyCharm-style: the AI chip can dock left, right or bottom) ──────────────
  var DOCKS = ['left', 'right', 'bottom'];
  function validDock(d) { return DOCKS.indexOf(d) >= 0; }
  // Which drop zone is the pointer over? Bottom band first, then the left/right thirds; the
  // middle is "cancel". (Zones are shown as edge overlays while dragging.)
  function dropZone(x, y, w, h) {
    if (!(w > 0 && h > 0) || x < 0 || y < 0 || x > w || y > h) return null;
    if (y > h * 0.72) return 'bottom';
    if (x < w * 0.33) return 'left';
    if (x > w * 0.67) return 'right';
    return null;
  }
  // Panel thickness (width for left/right, height for bottom), kept usable at any window size.
  function clampDock(dock, size, vw, vh) {
    var n = Math.round(+size) || 0;
    if (dock === 'bottom') return Math.max(160, Math.min(n, Math.max(160, Math.floor(vh * 0.7))));
    var cap = Math.floor(vw * 0.6);                              // never more than 60% of the window...
    return Math.max(Math.min(340, cap), Math.min(n, Math.min(720, cap)));   // ...even below 340px on a phone-width window
  }

  var api = {
    esc: esc, revealHidden: revealHidden, createSplitter: createSplitter, newState: newState, startUserTurn: startUserTurn,
    startResume: startResume, applyFrame: applyFrame, decide: decide, decideAll: decideAll,
    buildPromptRequest: buildPromptRequest, buildResumeRequest: buildResumeRequest, canRetry: canRetry,
    retryBody: retryBody,
    statusLabel: statusLabel, describeApproval: describeApproval, snapshot: snapshot,
    restore: restore, renderMarkdown: renderMarkdown,
    MODES: MODES, validMode: validMode, truncateAt: truncateAt, timeAgo: timeAgo, dateGroup: dateGroup,
    newStore: newStore, storeUpsert: storeUpsert, storeNew: storeNew, storeSwitch: storeSwitch,
    findTool: findTool, storeRestore: storeRestore, storeOpen: storeOpen, storeMerge: storeMerge, storeDelete: storeDelete, storeList: storeList, storePrune: storePrune, titleOf: titleOf,
    mentionQuery: mentionQuery, insertMention: insertMention, contextItems: contextItems,
    PROVIDERS: PROVIDERS, providerMeta: providerMeta, mergeModels: mergeModels, modelTree: modelTree, parseQualified: parseQualified,
    DOCKS: DOCKS, validDock: validDock, dropZone: dropZone, clampDock: clampDock,
    nextIndex: nextIndex, loadKnown: loadKnown, addKnown: addKnown, removeKnown: removeKnown,
    filterItems: filterItems, ATTACH: ATTACH, attachError: attachError, composePrompt: composePrompt,
  };
  global.VivChatCore = api;
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
})(typeof window !== 'undefined' ? window : globalThis);
