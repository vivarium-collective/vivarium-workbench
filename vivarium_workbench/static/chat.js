// chat.js — the built-in Chat tab (docs/ai-chat.md). DOM only; the transcript
// state machine, NDJSON splitting and markdown live in chat-core.js.
//
// Streams POST /api/chat/turn with fetch + ReadableStream (EventSource can't
// POST). fetch goes through session.js's override, so X-VW-Session rides along.
// The browser owns the transcript (sessionStorage); the server keeps none.
(function () {
  'use strict';
  var C = window.VivChatCore;
  var root = document.getElementById('viv-chat');
  if (!C || !root) return;

  var STORE_KEY = 'viv.chat.v1';
  var SUGGESTIONS = ['List the studies in this workspace', 'What composites are available?',
                     'Summarize the latest runs', 'What needs attention?'];

  var state = load();
  var status = null;            // GET /api/ai/status
  var controller = null;        // AbortController of the in-flight stream
  var el = {};

  function api(p) {
    return (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl(p) : p;
  }
  function load() {
    try { return C.restore(JSON.parse(sessionStorage.getItem(STORE_KEY))); } catch (e) { return C.newState(); }
  }
  function save() {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(C.snapshot(state))); } catch (e) { /* private mode / quota */ }
  }

  // ── Icons (inline, currentColor) ──────────────────────────────────────────
  var ICON = {
    spin: '<svg class="vc-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 3a9 9 0 1 0 9 9"/></svg>',
    done: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.8"/></svg>',
    error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></svg>',
    denied: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9.6 9.6a2.4 2.4 0 1 1 3.4 2.2c-.6.3-1 .8-1 1.4M12 16.3v.1"/></svg>',
    chev: '<svg class="vc-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
  };
  function statusIcon(s) {
    return s === 'done' ? ICON.done : s === 'error' ? ICON.error : s === 'denied' ? ICON.denied : ICON.spin;
  }

  // ── Rendering ─────────────────────────────────────────────────────────────
  function pretty(v) {
    if (v === undefined || v === null || v === '') return '';
    if (typeof v === 'string') return v;
    try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
  }

  function toolName(p) {
    return (p.args && p.args.operation_id) || (p.approval && p.approval.operation_id) || p.name;
  }

  function renderTool(p) {
    var e = C.esc;
    if (p.status === 'awaiting') {
      var a = C.describeApproval(p);
      return '<div class="vc-approve" data-id="' + e(p.id) + '">' +
        '<div class="vc-approve-h">' + ICON.shield + '<span>Approval required: <code>' + e(a.title) + '</code></span></div>' +
        '<div class="vc-req"><span class="vc-method">' + e(a.method) + '</span><code>' + e(a.path) + '</code>' +
          (a.summary ? '<span class="vc-sum">' + e(a.summary) + '</span>' : '') + '</div>' +
        (a.query ? '<div><div class="vc-k">Query</div><pre>' + e(a.query) + '</pre></div>' : '') +
        (a.body ? '<div><div class="vc-k">Request body</div><pre>' + e(a.body) + '</pre></div>' : '') +
        '<div class="vc-actions"><button class="vc-btn" data-act="deny">Deny</button>' +
        '<button class="vc-btn vc-primary" data-act="approve">Approve</button></div></div>';
    }
    var body = '';
    if (p.args && Object.keys(p.args).length) body += '<div><div class="vc-k">Arguments</div><pre>' + e(pretty(p.args)) + '</pre></div>';
    if (p.status === 'denied') body += '<div class="vc-note">You declined this action, so it was not run.</div>';
    else if (p.result !== undefined) body += '<div><div class="vc-k">Result</div><pre>' + e(pretty(p.result)) + '</pre></div>';
    return '<details class="vc-tool vc-s-' + e(p.status) + '" data-id="' + e(p.id) + '"' + (p.open ? ' open' : '') + '>' +
      '<summary><span class="vc-ic">' + statusIcon(p.status) + '</span><span class="vc-lbl">' + e(C.statusLabel(p.status)) +
      '</span><code>' + e(toolName(p)) + '</code>' + ICON.chev + '</summary><div class="vc-tool-body">' + body + '</div></details>';
  }

  function renderAssistant(m, isLast) {
    var html = m.parts.map(function (p, i) {
      if (p.kind === 'text') return '<div class="vc-md">' + C.renderMarkdown(p.text) + '</div>';
      if (p.kind === 'tool') return renderTool(p);
      if (p.kind === 'error') {
        return '<div class="vc-error"><span>' + C.esc(p.text) + '</span>' +
          (isLast && i === m.parts.length - 1 && state.retry ? '<button class="vc-btn" data-act="retry">Retry</button>' : '') + '</div>';
      }
      if (p.kind === 'notice') return '<div class="vc-notice">' + C.esc(p.text) + '</div>';
      return '';
    }).join('');
    if (isLast && state.busy) html += '<span class="vc-typing"></span>';
    return '<div class="vc-body">' + html + '</div>' +
      '<button class="vc-btn vc-copy" data-act="copy" title="Copy">Copy</button>';
  }

  function messageNode(m, isLast) {
    var d = document.createElement('div');
    if (m.role === 'user') {
      d.className = 'vc-msg vc-user';
      d.innerHTML = '<div class="vc-bubble">' + C.esc(m.text) + '</div>';
    } else {
      d.className = 'vc-msg vc-asst';
      d.innerHTML = renderAssistant(m, isLast);
    }
    return d;
  }

  function nearBottom() {
    var l = el.list;
    return l.scrollHeight - l.scrollTop - l.clientHeight < 80;
  }
  function scrollDown(force) {
    if (force || el.pinned) el.list.scrollTop = el.list.scrollHeight;
  }

  function renderAll() {
    el.col.innerHTML = '';
    if (!state.ui.length) {
      var ready = canChat();
      var empty = document.createElement('div');
      empty.className = 'vc-empty';
      empty.innerHTML = '<h3>Ask about this workspace</h3><div>The assistant can do anything you can do by hand — ' +
        'every change asks for your approval first.</div>' +
        (ready ? '<div class="vc-suggest">' + SUGGESTIONS.map(function (s) {
          return '<button class="vc-chip" data-suggest="' + C.esc(s) + '">' + C.esc(s) + '</button>'; }).join('') + '</div>' : '');
      el.col.appendChild(empty);
    }
    state.ui.forEach(function (m, i) { el.col.appendChild(messageNode(m, i === state.ui.length - 1)); });
    renderChrome();
    scrollDown(true);
  }

  // Re-render only the assistant message being streamed (cheap; keeps the rest stable).
  function renderLast() {
    var i = state.ui.length - 1, m = state.ui[i];
    var kids = el.col.children, node = kids[kids.length - 1];
    if (!m || m.role !== 'assistant' || !node || !node.classList.contains('vc-asst')) return renderAll();
    var fresh = messageNode(m, true);
    el.col.replaceChild(fresh, node);
    renderChrome();
    scrollDown(false);
  }

  function canChat() {
    return !!(status && status.available && status.selected &&
      (status.providers || []).some(function (p) { return p.id === status.selected.provider && p.configured; }));
  }

  function renderChrome() {
    var ready = canChat();
    var chip = ready ? status.selected.provider + ' · ' + status.selected.model : 'No model selected';
    el.model.textContent = chip; el.model2.textContent = chip;
    el.input.disabled = !ready || state.busy || state.pending.length > 0;
    el.input.placeholder = state.pending.length ? 'Approve or deny the pending action to continue…'
      : 'Ask about this workspace…  (Enter to send · Shift+Enter for a new line)';
    el.send.textContent = state.busy ? 'Stop' : 'Send';
    el.send.disabled = !state.busy && (!ready || state.pending.length > 0);
    el.setup.hidden = ready || !status;
    if (!ready && status) {
      el.setup.innerHTML = status.error ? C.esc(status.error) : !status.available
        ? 'Chat needs the optional extra: <code>pip install \'vivarium-workbench[chat]\'</code>'
        : 'No AI provider is set up yet. <a data-act="setup">Add one under Account → AI provider</a>.';
    }
  }

  // ── Turn streaming ────────────────────────────────────────────────────────
  // Same loaders the UI's own create/edit flows call (walkthrough.js _submitBrowseCreate).
  var REFRESH_AFTER_MUTATION = ['_loadInvestigations', '_loadInvestigationSets', '_refreshGitStatus'];
  function refreshWorkspaceViews() {
    // Other tabs memoise their first load; clear those flags so the next visit
    // (and the rail) reflect what the assistant just changed.
    window._registryLoaded = false;
    window._investigationsLoaded = false;
    REFRESH_AFTER_MUTATION.forEach(function (fn) {
      try { if (typeof window[fn] === 'function') window[fn](); } catch (e) { /* best effort */ }
    });
  }

  function streamTurn(body) {
    controller = new AbortController();
    var splitter = C.createSplitter();
    var mutated = false;
    function handle(f) {
      C.applyFrame(state, f);
      if (f.type === 'tool-result') {
        var tool = null;
        state.ui.forEach(function (m) { (m.parts || []).forEach(function (p) { if (p.kind === 'tool' && p.id === f.tool_call_id) tool = p; }); });
        if (tool && tool.approval && tool.status === 'done') mutated = true;
      }
      renderLast();
    }
    return fetch(api('/api/chat/turn'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: controller.signal,
    }).then(function (resp) {
      if (!resp.ok) {
        return resp.json().catch(function () { return {}; }).then(function (j) {
          throw new Error(j.error || ('HTTP ' + resp.status));
        });
      }
      var reader = resp.body.getReader(), dec = new TextDecoder();
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) { splitter.flush().forEach(handle); return; }
          splitter.push(dec.decode(r.value, { stream: true })).forEach(handle);
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (err && err.name === 'AbortError') {
        var m = state.ui[state.ui.length - 1];
        if (body.deferred_results) {
          // Stopped mid-resume: the transcript is dangling until the turn is re-sent.
          if (m && m.parts) m.parts.push({ kind: 'error', text: 'Stopped before the approved action finished.' });
        } else {
          if (m && m.parts) m.parts.push({ kind: 'notice', text: 'Stopped.' });
          state.retry = null;
        }
        state.busy = false;
      } else {
        C.applyFrame(state, { type: 'error', error: (err && err.message) || 'request failed' });
      }
    }).then(function () {
      controller = null;
      state.busy = false;
      if (mutated) refreshWorkspaceViews();
      save(); renderAll();
    });
  }

  function send(prompt) {
    prompt = (prompt || '').trim();
    if (!prompt || state.busy || !canChat()) return;
    var body = C.buildPromptRequest(state, prompt);
    C.startUserTurn(state, prompt);
    el.pinned = true;
    save(); renderAll();
    streamTurn(body);
  }

  function resume() {
    var body = C.buildResumeRequest(state);
    C.startResume(state);
    save(); renderAll();
    streamTurn(body);
  }

  function decide(id, approved) {
    var allAnswered = C.decide(state, id, approved);
    save();
    if (allAnswered) resume(); else renderAll();
  }

  function newChat() {
    if (controller) controller.abort();
    state = C.newState(); save(); renderAll();
    el.input.focus();
  }

  // ── DOM + events ──────────────────────────────────────────────────────────
  function build() {
    root.innerHTML =
      '<div class="viv-chat">' +
        '<div class="vc-header"><span class="vc-title">Chat</span><button class="vc-chip" id="vc-model" data-act="setup" title="Change provider / model"></button>' +
        '<span class="vc-spacer"></span><button class="vc-btn" id="vc-new">New chat</button></div>' +
        '<div class="vc-setup" id="vc-setup" hidden></div>' +
        '<div class="vc-list" id="vc-list"><div class="vc-col" id="vc-col"></div></div>' +
        '<button class="vc-scroll" id="vc-scroll" title="Scroll to bottom" hidden>↓</button>' +
        '<div class="vc-foot"><div class="vc-box">' +
          '<textarea class="vc-input" id="vc-input" rows="1" spellcheck="true"></textarea>' +
          '<div class="vc-boxbar"><button class="vc-chip" id="vc-model2" data-act="setup" title="Change provider / model"></button>' +
          '<span class="vc-hint">Changes need your approval</span><span class="vc-spacer"></span>' +
          '<button class="vc-btn vc-primary" id="vc-send">Send</button></div>' +
        '</div></div></div>';
    el.list = root.querySelector('#vc-list'); el.col = root.querySelector('#vc-col');
    el.input = root.querySelector('#vc-input'); el.send = root.querySelector('#vc-send');
    el.setup = root.querySelector('#vc-setup'); el.scroll = root.querySelector('#vc-scroll');
    el.model = root.querySelector('#vc-model'); el.model2 = root.querySelector('#vc-model2');
    el.pinned = true;

    root.querySelector('#vc-new').addEventListener('click', newChat);
    function submit() {
      var text = el.input.value;
      el.input.value = ''; grow();
      send(text);
    }
    el.send.addEventListener('click', function () {
      if (state.busy && controller) controller.abort(); else submit();
    });
    el.input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); submit(); }
    });
    el.input.addEventListener('input', grow);
    el.list.addEventListener('scroll', function () {
      el.pinned = nearBottom(); el.scroll.hidden = el.pinned;
    });
    el.scroll.addEventListener('click', function () { el.pinned = true; scrollDown(true); el.scroll.hidden = true; });

    root.addEventListener('click', function (ev) {
      var s = ev.target.closest('[data-suggest]');
      if (s) return send(s.getAttribute('data-suggest'));
      var b = ev.target.closest('[data-act]');
      if (!b) return;
      var act = b.getAttribute('data-act');
      var host = b.closest('[data-id]');
      if (act === 'approve' && host) decide(host.getAttribute('data-id'), true);
      else if (act === 'deny' && host) decide(host.getAttribute('data-id'), false);
      else if (act === 'retry') retry();
      else if (act === 'setup') openSetup();
      else if (act === 'copy') copyMessage(b);
    });
    // Remember which tool rows the user expanded across re-renders.
    root.addEventListener('toggle', function (ev) {
      var d = ev.target;
      if (!d.matches || !d.matches('details.vc-tool')) return;
      var id = d.getAttribute('data-id');
      state.ui.forEach(function (m) { (m.parts || []).forEach(function (p) { if (p.kind === 'tool' && p.id === id) p.open = d.open; }); });
    }, true);
  }

  function grow() {
    el.input.style.height = 'auto';
    el.input.style.height = Math.min(el.input.scrollHeight, 400) + 'px';
  }

  function retry() {
    if (!state.retry || state.busy) return;
    var m = state.ui[state.ui.length - 1];
    if (m && m.parts) m.parts = m.parts.filter(function (p) { return p.kind !== 'error'; });
    state.busy = true; save(); renderAll();
    streamTurn(C.retryBody(state));
  }

  function copyMessage(btn) {
    var node = btn.closest('.vc-asst');
    var text = node ? node.querySelector('.vc-body').innerText : '';
    if (navigator.clipboard) navigator.clipboard.writeText(text).catch(function () {});
    btn.textContent = 'Copied'; setTimeout(function () { btn.textContent = 'Copy'; }, 1200);
  }

  function openSetup() {
    if (typeof window._switchPage === 'function') window._switchPage('github');
    if (window.location.hash !== '#github') window.location.hash = 'github';
    setTimeout(function () {
      var card = document.getElementById('viv-ai-card');
      if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 60);
  }

  function refreshStatus() {
    return fetch(api('/api/ai/status'))
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) return { available: false, providers: [], error: j.error || ('HTTP ' + r.status) };
          return j;
        });
      })
      .then(function (s) { status = s; }, function () { status = { available: false, providers: [], error: 'Could not reach the server.' }; })
      .then(function () { renderAll(); });
  }

  build();
  try {
    renderAll();
  } catch (e) {
    // A corrupted sessionStorage transcript must not take the tab down.
    state = C.newState(); save(); renderAll();
  }
  // Called by walkthrough.js _switchPage('chat') and after the AI card changes.
  window._loadChat = function () { refreshStatus().then(function () { el.input.focus(); }); };
  window.addEventListener('viv:ai-changed', refreshStatus);
})();
