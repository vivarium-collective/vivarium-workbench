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
  function describeApproval(part) {
    var ap = part.approval || {};
    var body = ap.body !== undefined && ap.body !== null ? ap.body
      : (part.args && part.args.body !== undefined ? part.args.body : null);
    var query = ap.query && Object.keys(ap.query).length ? ap.query : null;
    return {
      title: ap.operation_id || (part.args && part.args.operation_id) || part.name,
      method: ap.method || '', path: ap.path || '', summary: ap.summary || '',
      query: query ? JSON.stringify(query, null, 2) : '',
      body: body === null ? '' : JSON.stringify(body, null, 2),
    };
  }

  function validMessage(m) {
    if (!m || typeof m !== 'object') return false;
    if (m.role === 'user') return typeof m.text === 'string';
    if (m.role !== 'assistant' || !Array.isArray(m.parts)) return false;
    return m.parts.every(function (p) {
      return p && typeof p === 'object' &&
        ((p.kind === 'text' || p.kind === 'error' || p.kind === 'notice') ? typeof p.text === 'string'
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

  var api = {
    esc: esc, createSplitter: createSplitter, newState: newState, startUserTurn: startUserTurn,
    startResume: startResume, applyFrame: applyFrame, decide: decide,
    buildPromptRequest: buildPromptRequest, buildResumeRequest: buildResumeRequest, canRetry: canRetry,
    retryBody: retryBody,
    statusLabel: statusLabel, describeApproval: describeApproval, snapshot: snapshot,
    restore: restore, renderMarkdown: renderMarkdown,
  };
  global.VivChatCore = api;
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
})(typeof window !== 'undefined' ? window : globalThis);
