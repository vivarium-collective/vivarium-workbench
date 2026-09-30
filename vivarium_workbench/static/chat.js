// chat.js — the built-in AI panel, docked on the right (docs/ai-chat.md). DOM only: the
// transcript state machine, history store, NDJSON splitting, modes, mentions, attachments
// and markdown live in chat-core.js (unit-tested under node).
//
// Layout and controls follow marimo's AI panel: "AI" header + close; toolbar (new chat,
// provider status plug, settings gear, previous-chats clock); message column; Stop strip;
// composer with mode pill, model pill, capabilities (sliders), @ context, attach, send.
// Streams POST /api/chat/turn with fetch + ReadableStream (EventSource can't POST); fetch
// goes through session.js's override, so X-VW-Session rides along. The browser owns the
// transcripts (sessionStorage); the server keeps none.
(function () {
  'use strict';
  const C = window.VivChatCore;
  const panel = document.getElementById('viv-ai-panel');
  const root = document.getElementById('viv-ai');
  if (!C || !panel || !root) return;
  if ((window.__DASH_CONFIG__ || {}).mode === 'snapshot') return;   // published bundle: no live server

  const STORE_KEY = 'viv.chat.v2';
  const DOCS = 'https://github.com/vivarium-collective/vivarium-workbench/blob/main/docs/';
  const REFRESH_AFTER_MUTATION = ['_loadInvestigations', '_loadInvestigationSets', '_refreshGitStatus'];
  const PLACEHOLDER_NEW = 'Ask anything, @ to include context about studies or composites';
  const PLACEHOLDER = 'Type your message...';

  // ── State ─────────────────────────────────────────────────────────────────
  let store = loadStore();
  let state = C.restore(store.chats[store.active].snap);
  let status = null;              // GET /api/ai/status
  let controller = null;          // AbortController of the in-flight stream
  let editing = -1;               // ui index of the user message being edited
  let queued = [];                // messages sent while a turn was running
  let attached = [];              // [{name,size,content}] pending attachments
  let ctxItems = null;            // @ picker items (from the workspace manifest)
  let pop = null;                 // the open popover
  const prefs = {
    mode: lsGet('viv.ai.mode', 'manual'),
    manifest: lsGet('viv.ai.manifest', '1') !== '0',
  };
  if (!C.validMode(prefs.mode)) prefs.mode = 'manual';
  const el = {};
  // Docking: the panel is a flex sibling that can live left of, right of, or below the content.
  const layout = document.querySelector('.viv-layout');
  const mainEl = layout && layout.querySelector('.viv-main');
  const codeRail = document.getElementById('viv-code-rail');
  let dock = C.validDock(lsGet('viv.ai.dock', 'left')) ? lsGet('viv.ai.dock', 'left') : 'left';

  function lsGet(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (x) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (x) { /* private mode */ } }
  function api(p) { return (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl(p) : p; }
  function loadStore() {
    try { return C.storeRestore(JSON.parse(sessionStorage.getItem(STORE_KEY))); } catch (x) { return C.newStore(); }
  }
  function save() {
    C.storeUpsert(store, state);
    for (let attempt = 0; attempt < 2; attempt++) {
      try { sessionStorage.setItem(STORE_KEY, JSON.stringify(store)); return; }
      catch (x) { C.storePrune(store); if (attempt === 0) dropOldest(); }   // quota: shed history, retry once
    }
  }
  function dropOldest() {
    const ids = Object.keys(store.chats).filter(function (id) { return id !== store.active; })
      .sort(function (a, b) { return store.chats[a].updatedAt - store.chats[b].updatedAt; });
    ids.slice(0, Math.max(1, Math.ceil(ids.length / 2))).forEach(function (id) { delete store.chats[id]; });
  }
  function canChat() {
    return !!(status && status.available && status.selected &&
      (status.providers || []).some(function (p) { return p.id === status.selected.provider && p.configured; }));
  }

  // ── Icons (inline, currentColor; lucide-style) ────────────────────────────
  const S = (d, extra) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"' + (extra || '') + '>' + d + '</svg>';
  const ICON = {
    plus: S('<path d="M12 5v14M5 12h14"/>'),
    x: S('<path d="M6 6l12 12M18 6L6 18"/>'),
    plug: S('<path d="M9 2v6M15 2v6"/><path d="M6 8h12v4a6 6 0 0 1-12 0z"/><path d="M12 18v4"/>'),
    gear: S('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    clock: S('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    send: S('<path d="M3 11l18-8-8 18-2-8z"/><path d="M11 13L21 3"/>'),
    stop: S('<circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx="1" fill="currentColor"/>'),
    bot: S('<path d="M12 6V2H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>'),
    sliders: S('<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>'),
    at: S('<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>'),
    clip: S('<path d="M21 12.5l-8.5 8.5a5.5 5.5 0 0 1-7.8-7.8l9-9a3.7 3.7 0 0 1 5.2 5.2l-9 9a1.8 1.8 0 0 1-2.6-2.6l8.5-8.5"/>'),
    file: S('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'),
    chev: S('<path d="M6 9l6 6 6-6"/>', ' class="vp-chev"'),
    chevs: S('<path d="M6 9l6 6 6-6"/>'),
    copy: S('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>'),
    message: S('<path d="M21 12a8 8 0 0 1-11.5 7.2L4 21l1.8-5.5A8 8 0 1 1 21 12z"/>'),
    book: S('<path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v18H6.5A2.5 2.5 0 0 0 4 22z"/><path d="M8 7h8M8 11h8"/>'),
    hat: S('<path d="M3 12h18M6 12l1.5-6a2 2 0 0 1 2-1.5h5a2 2 0 0 1 2 1.5L18 12"/><circle cx="8" cy="16" r="3"/><circle cx="16" cy="16" r="3"/>'),
    code: S('<path d="M8 8l-5 4 5 4M16 8l5 4-5 4M14 4l-4 16"/>'),
    sparkles: S('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>'),
    shield: S('<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9.6 9.6a2.4 2.4 0 1 1 3.4 2.2c-.6.3-1 .8-1 1.4M12 16.3v.1"/>'),
    spin: S('<path d="M12 3a9 9 0 1 0 9 9"/>', ' class="vp-spin"'),
    done: S('<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.8"/>'),
    error: S('<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>'),
    denied: S('<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>'),
    down: S('<path d="M12 5v14M6 13l6 6 6-6"/>'),
    check: S('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
    dock: S('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>'),
  };
  const statusIcon = (s) => s === 'done' ? ICON.done : s === 'error' ? ICON.error : s === 'denied' ? ICON.denied : ICON.spin;
  const e = C.esc;

  // ── Rendering: messages ───────────────────────────────────────────────────
  function pretty(v) {
    if (v === undefined || v === null || v === '') return '';
    if (typeof v === 'string') return v;
    try { return JSON.stringify(v, null, 2); } catch (x) { return String(v); }
  }
  const toolName = (p) => (p.args && p.args.operation_id) || (p.approval && p.approval.operation_id) || p.name;

  function renderTool(p) {
    if (p.status === 'awaiting') {
      const a = C.describeApproval(p);
      return '<div class="vp-approve" data-id="' + e(p.id) + '">' +
        '<div class="vp-approve-h">' + ICON.shield + '<span>Approval required: <code>' + e(a.title) + '</code></span></div>' +
        '<div class="vp-req"><span class="vp-method">' + e(a.method) + '</span><code>' + e(a.path) + '</code>' +
          (a.summary ? '<span class="vp-sum">' + e(a.summary) + '</span>' : '') + '</div>' +
        (a.query ? '<div><div class="vp-k">Query</div><pre>' + e(a.query) + '</pre></div>' : '') +
        (a.body ? '<div><div class="vp-k">Request body</div><pre>' + e(a.body) + '</pre></div>' : '') +
        '<div class="vp-actions"><button class="vp-btn" data-act="deny">Deny</button>' +
        '<button class="vp-btn vp-primary" data-act="approve">Approve</button></div></div>';
    }
    let body = '';
    if (p.args && Object.keys(p.args).length) body += '<div><div class="vp-k">Arguments</div><pre>' + e(pretty(p.args)) + '</pre></div>';
    if (p.status === 'denied') body += '<div class="vp-note">You declined this action, so it was not run.</div>';
    else if (p.result !== undefined) body += '<div><div class="vp-k">Result</div><pre>' + e(pretty(p.result)) + '</pre></div>';
    return '<details class="vp-tool vp-s-' + e(p.status) + '" data-id="' + e(p.id) + '"' + (p.open ? ' open' : '') + '>' +
      '<summary><span class="vp-ic">' + statusIcon(p.status) + '</span><span class="vp-lbl">' + e(C.statusLabel(p.status)) +
      '</span><code>' + e(toolName(p)) + '</code>' + ICON.chev + '</summary><div class="vp-tool-body">' + body + '</div></details>';
  }

  // "Thinking" (open) while streaming; "View reasoning (N chars)" once done.
  function renderReasoning(p, streaming) {
    const label = streaming ? 'Thinking' : 'View reasoning' + (p.text ? ' (' + p.text.length + ' chars)' : '');
    return '<details class="vp-reason"' + (streaming || p.open ? ' open' : '') + '><summary>' + ICON.bot +
      '<span>' + e(label) + '</span>' + ICON.chev + '</summary>' +
      '<div class="vp-reason-body vp-md">' + C.renderMarkdown(p.text) + '</div></details>';
  }

  function renderAssistant(m, isLast) {
    const busy = isLast && state.busy;
    const html = m.parts.map(function (p, i) {
      if (p.kind === 'text') return '<div class="vp-md">' + C.renderMarkdown(p.text) + '</div>';
      if (p.kind === 'reasoning') return renderReasoning(p, busy && i === m.parts.length - 1);
      if (p.kind === 'tool') return renderTool(p);
      if (p.kind === 'error') {
        return '<div class="vp-error"><div class="vp-error-msg">' + e(p.text) + '</div>' +
          (isLast && i === m.parts.length - 1 && state.retry ? '<button class="vp-btn" data-act="retry">Retry</button>' : '') + '</div>';
      }
      if (p.kind === 'notice') return '<div class="vp-notice">' + e(p.text) + '</div>';
      return '';
    }).join('');
    const waiting = isLast ? state.pending.length : 0;
    const bulk = waiting >= 2
      ? '<div class="vp-bulk"><span>' + waiting + ' actions are waiting for your approval</span>' +
        '<button class="vp-btn" data-act="deny-all">Deny all</button>' +
        '<button class="vp-btn vp-primary" data-act="approve-all">Approve all</button></div>' : '';
    return '<div class="vp-body">' + html + bulk + (busy ? '<span class="vp-typing"></span>' : '') + '</div>' +
      '<button class="vp-icon vp-copy" data-act="copy" title="Copy">' + ICON.copy + '</button>';
  }

  // A user message is a bordered monospace box; click to edit and resend from that point.
  function userBox(m, idx) {
    if (idx === editing) {
      return '<div class="vp-userbox"><textarea data-edit-input rows="1">' + e(m.text) + '</textarea>' +
        '<div class="vp-editbar"><button class="vp-btn" data-act="edit-cancel">Cancel</button>' +
        '<button class="vp-btn vp-primary" data-act="edit-send">Resend</button></div></div>';
    }
    const files = (m.files && m.files.length)
      ? '<div class="vp-files">' + m.files.map(function (n) { return '<span class="vp-file">' + ICON.file + '<span>' + e(n) + '</span></span>'; }).join('') + '</div>' : '';
    return '<div class="vp-userbox" data-act="edit" data-idx="' + idx + '" title="Click to edit and resend">' + e(m.text) + files + '</div>';
  }

  function messageNode(m, idx, isLast) {
    const d = document.createElement('div');
    if (m.role === 'user') { d.className = 'vp-msg-row vp-user'; d.innerHTML = userBox(m, idx); }
    else { d.className = 'vp-msg-row vp-asst'; d.innerHTML = renderAssistant(m, isLast); }
    return d;
  }

  function queuedNode(q) {
    const d = document.createElement('div');
    d.className = 'vp-msg-row vp-user vp-queued';
    d.innerHTML = '<div class="vp-userbox">' + ICON.spin + '<span>' + e(q.text) + '</span></div>';
    return d;
  }

  const nearBottom = () => el.list.scrollHeight - el.list.scrollTop - el.list.clientHeight < 80;
  function scrollDown(force) { if (force || el.pinned) el.list.scrollTop = el.list.scrollHeight; }

  function renderAll() {
    const fresh = state.ui.length === 0;
    el.new.hidden = !fresh; el.list.hidden = fresh; el.foot.hidden = fresh;
    el.list.innerHTML = '';
    state.ui.forEach(function (m, i) { el.list.appendChild(messageNode(m, i, i === state.ui.length - 1)); });
    queued.forEach(function (q) { el.list.appendChild(queuedNode(q)); });
    renderChrome();
    const ta = el.list.querySelector('[data-edit-input]');
    if (ta) { autosize(ta); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
    scrollDown(true);
  }

  // Re-render only the assistant message being streamed.
  function renderLast() {
    const i = state.ui.length - 1, m = state.ui[i];
    const kids = el.list.querySelectorAll('.vp-msg-row:not(.vp-queued)');
    const node = kids[kids.length - 1];
    if (!m || m.role !== 'assistant' || !node || !node.classList.contains('vp-asst')) return renderAll();
    el.list.replaceChild(messageNode(m, i, true), node);
    renderChrome();
    scrollDown(false);
  }

  // ── Rendering: chrome (toolbar, composer, new-thread state) ───────────────
  function renderChrome() {
    const ready = canChat();
    const fresh = state.ui.length === 0;
    // plug: the provider connection (red until a provider + model are usable)
    el.plug.className = 'vp-icon ' + (status ? (ready ? 'vp-ok' : 'vp-red') : '');
    el.plug.title = ready ? 'Connected: ' + status.selected.provider + ' · ' + status.selected.model
      : 'Not connected — open AI settings';
    // composer placement (only move it when the layout changes: moving steals focus)
    const target = fresh ? el.newHost : el.foot;
    if (el.composer.parentNode !== target) target.appendChild(el.composer);
    el.newCopy.innerHTML = status && !ready
      ? '<h3>Chat with AI</h3><p>' + e(status.error || (status.available ? 'No AI provider configured or Chat model not selected' :
          "The chat needs the optional extra: pip install 'vivarium-workbench[chat]'")) + '</p>' +
        (status.available ? '<button class="vp-callout" data-act="settings">Edit AI settings</button>' : '')
      : '<h3>Chat with AI</h3>';
    el.composer.hidden = !!(fresh && status && !ready);
    el.callout.hidden = !fresh;
    // composer controls
    const mode = C.MODES.filter(function (m) { return m.id === prefs.mode; })[0];
    el.mode.innerHTML = ICON[mode.icon] + '<span>' + e(mode.label) + '</span>' + ICON.chevs;
    el.model.innerHTML = ICON.bot + '<span>' + e(ready ? status.selected.model : 'Model') + '</span>' + ICON.chevs;
    el.input.placeholder = fresh ? PLACEHOLDER_NEW : PLACEHOLDER;
    el.send.innerHTML = state.busy ? ICON.stop : ICON.send;
    el.send.title = state.busy ? 'Stop' : 'Submit';
    el.send.className = 'vp-icon' + (state.busy ? ' vp-red' : '');
    el.stop.hidden = !state.busy;
    renderPills();
  }

  function renderPills() {
    el.pills.innerHTML = attached.map(function (f, i) {
      return '<span class="vp-file">' + ICON.file + '<span>' + e(f.name) + '</span>' +
        '<button data-act="unattach" data-idx="' + i + '" title="Remove">' + ICON.x.replace('<svg', '<svg width="12" height="12"') + '</button></span>';
    }).join('');
  }

  function autosize(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 400) + 'px'; }

  // ── Popovers ──────────────────────────────────────────────────────────────
  function closePop() {
    if (!pop) return;
    pop.node.remove();
    document.removeEventListener('mousedown', pop.off, true);
    pop = null;
  }
  // Popovers live on <body> with position:fixed, so a short (bottom-docked) panel or an
  // overflow:hidden ancestor can never clip them. opts.up === false forces below; opts.align
  // 'right' aligns the popover's right edge to the anchor's.
  function place(node, anchor, opts) {
    const ar = anchor.getBoundingClientRect();
    const width = Math.min(opts.width || 260, innerWidth - 16);
    node.style.width = width + 'px';
    let left = opts.align === 'right' ? ar.right - width : ar.left;
    left = Math.max(8, Math.min(left, innerWidth - width - 8));
    node.style.left = left + 'px';
    node.style.maxHeight = '';
    const h = Math.min(node.offsetHeight, opts.maxHeight || 340);
    const roomBelow = innerHeight - ar.bottom - 8, roomAbove = ar.top - 8;
    const below = opts.up === false || (opts.up !== true && roomBelow >= h) || roomBelow >= roomAbove && roomAbove < h;
    node.style.maxHeight = Math.max(120, Math.min(opts.maxHeight || 340, below ? roomBelow : roomAbove)) + 'px';
    if (below) { node.style.top = (ar.bottom + 4) + 'px'; node.style.bottom = 'auto'; }
    else { node.style.bottom = (innerHeight - ar.top + 4) + 'px'; node.style.top = 'auto'; }
  }
  function openPop(anchor, node, opts) {
    opts = opts || {};
    closePop();
    node.classList.add('vp-pop');
    document.body.appendChild(node);
    place(node, anchor, opts);
    const off = function (ev) { if (!node.contains(ev.target) && !anchor.contains(ev.target)) closePop(); };
    document.addEventListener('mousedown', off, true);
    pop = { node: node, off: off, anchor: anchor };
    return node;
  }
  const div = (html) => { const d = document.createElement('div'); d.innerHTML = html; return d; };

  // A real dropdown (like marimo's Radix Select): the trigger is a bordered select-like button,
  // the menu is a listbox flush under it. spec: {groups:[{id,label,color,mark,count,models:[{id,on}]}]
  // | items:[{id,label,desc,icon,on,disabled}], onPick(id, groupId), footer:{label,onClick}, empty}.
  function dropdown(trigger, spec) {
    closePop();
    const menu = document.createElement('div');
    menu.className = 'vp-dd'; menu.setAttribute('role', 'listbox');
    let html = '', n = 0;
    (spec.groups || []).forEach(function (g) {
      html += '<div class="vp-dd-group"><span class="vp-badge" style="background:' + e(g.color) + '">' + e(g.mark) + '</span>' +
        '<span class="vp-dd-glabel">' + e(g.label) + '</span><span class="vp-dd-count">' + g.count + (g.count === 1 ? ' model' : ' models') + '</span></div>';
      g.models.forEach(function (m) {
        html += '<div role="option" class="vp-dd-item' + (m.on ? ' on' : '') + '" data-i="' + (n++) + '" data-group="' + e(g.id) + '" data-id="' + e(m.id) + '" aria-selected="' + !!m.on + '">' +
          '<span class="vp-dd-check">' + (m.on ? ICON.check : '') + '</span><span class="vp-dd-label">' + e(m.id) + '</span></div>';
      });
    });
    (spec.items || []).forEach(function (it) {
      html += '<div role="option" class="vp-dd-item' + (it.on ? ' on' : '') + (it.disabled ? ' disabled' : '') + '" data-i="' + (n++) + '" data-id="' + e(it.id) + '" aria-selected="' + !!it.on +
        '" aria-disabled="' + !!it.disabled + '"><span class="vp-dd-check">' + (it.on ? ICON.check : '') + '</span>' +
        (it.icon ? '<span class="vp-dd-icon">' + it.icon + '</span>' : '') +
        '<span class="vp-dd-label">' + e(it.label) + (it.desc ? '<small>' + e(it.desc) + '</small>' : '') + '</span></div>';
    });
    if (!n) html += '<div class="vp-dd-empty">' + e(spec.empty || 'Nothing to choose from yet') + '</div>';
    if (spec.footer) html += '<div class="vp-dd-foot" role="button" tabindex="0" data-footer="1">' + e(spec.footer.label) + '</div>';
    menu.innerHTML = html;
    openPop(trigger, menu, { width: Math.max(trigger.getBoundingClientRect().width, spec.width || 230), up: spec.up, maxHeight: 340 });
    menu.classList.add('vp-dd-menu');
    const rows = function () { return Array.prototype.slice.call(menu.querySelectorAll('.vp-dd-item:not(.disabled)')); };
    let hi = Math.max(0, rows().findIndex(function (r) { return r.classList.contains('on'); }));
    const mark = function () {
      rows().forEach(function (r, i) { r.classList.toggle('hi', i === hi); });
      const cur = rows()[hi]; if (cur) cur.scrollIntoView({ block: 'nearest' });
    };
    mark();
    const pick = function (r) { if (!r || r.classList.contains('disabled')) return; closePop(); spec.onPick(r.getAttribute('data-id'), r.getAttribute('data-group')); };
    menu.addEventListener('mousemove', function (ev) {
      const r = ev.target.closest('.vp-dd-item:not(.disabled)');
      if (r) { hi = rows().indexOf(r); mark(); }
    });
    menu.addEventListener('click', function (ev) {
      if (ev.target.closest('[data-footer]')) { closePop(); spec.footer.onClick(); return; }
      pick(ev.target.closest('.vp-dd-item'));
    });
    const onKey = function (ev) {
      if (!pop || pop.node !== menu) { document.removeEventListener('keydown', onKey, true); return; }
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); closePop(); trigger.focus(); }
      else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].indexOf(ev.key) >= 0) { ev.preventDefault(); hi = C.nextIndex(hi, rows().length, ev.key); mark(); }
      else if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(rows()[hi]); }
      else if (ev.key === 'Tab') closePop();
    };
    document.addEventListener('keydown', onKey, true);
    pop.dd = true;
    return menu;
  }

  function openModeMenu(anchor) {
    dropdown(anchor, {
      items: C.MODES.map(function (m) { return { id: m.id, label: m.label, desc: m.desc, icon: ICON[m.icon], on: m.id === prefs.mode, disabled: !!m.disabled }; }),
      width: 300,
      onPick: function (id) { prefs.mode = id; lsSet('viv.ai.mode', id); renderChrome(); },
    });
  }

  // Provider endpoints that list their models (Ollama tags, OpenCode Go /models) are asked once
  // per page load when the menu opens, so the dropdown fills itself without a trip to Settings.
  const asked = {};
  function autoDiscover(repaint) {
    ((status && status.providers) || []).forEach(function (p) {
      if (!p.configured || asked[p.id] || ['ollama', 'opencode'].indexOf(p.id) < 0) return;
      asked[p.id] = true;
      fetch(api('/api/ai/models?provider=' + encodeURIComponent(p.id)))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { if (j && j.models && j.models.length) { C.addKnown(p.id, j.models); repaint(); } }, function () {});
    });
  }
  function openModelMenu(anchor) {
    const paint = function () {
      dropdown(anchor, {
        groups: C.groupModels(status, C.loadKnown(), status && status.selected),
        empty: 'No models yet — open AI Settings to add or discover some',
        width: 280,
        footer: { label: 'Add or edit models…', onClick: openSettings },
        onPick: function (id, group) { selectModel(group, id); },
      });
    };
    paint();
    autoDiscover(function () { if (pop && pop.anchor === anchor && pop.dd) paint(); });
  }
  function selectModel(provider, model) {
    fetch(api('/api/ai/select'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: provider, model: model }) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || r.status); }); })
      .then(refreshStatus, function (err) { flash(err.message); });
  }

  function openCapabilities(anchor) {
    const n = div('<label class="vp-switch"><span><strong>Workspace summary</strong><small>Include a live summary of the workspace in every message ' +
      '(uses more tokens). <a href="' + DOCS + 'ai-chat.md" target="_blank" rel="noopener noreferrer">Learn more</a></small></span>' +
      '<input type="checkbox" id="vp-cap-manifest"' + (prefs.manifest ? ' checked' : '') + '></label>' +
      '<hr><div class="vp-caps" id="vp-caps">Loading…</div>');
    openPop(anchor, n, { width: 300 });
    n.querySelector('#vp-cap-manifest').addEventListener('change', function (ev) {
      prefs.manifest = ev.target.checked; lsSet('viv.ai.manifest', prefs.manifest ? '1' : '0');
    });
    fetch(api('/api/ai/capabilities')).then(function (r) { return r.ok ? r.json() : null; }).then(function (c) {
      const box = n.querySelector('#vp-caps');
      if (!box) return;
      box.innerHTML = c ? 'Reachable: <strong>' + c.reads + '</strong> read operations · <strong>' + c.writes +
        '</strong> write operations (each needs your approval).<br>Withheld: GitHub auth, workspace/source switching, remote pushes, ' +
        'package installs, downloads and streams.' : 'Capabilities are unavailable.';
    }, function () { const box = n.querySelector('#vp-caps'); if (box) box.textContent = 'Capabilities are unavailable.'; });
  }

  function openConnect(anchor) {
    const n = div('<div class="vp-pop-h">Connect your own agent</div><div class="vp-caps">Drive this workspace from Claude Code instead: ' +
      'install the <code>viva-superpowers</code> plugin, run <code>/viva-init</code> once, then <code>/viva-workbench start</code>. ' +
      '<a href="' + DOCS + 'ai-onboarding.md" target="_blank" rel="noopener noreferrer">Setup guide</a></div>');
    openPop(anchor, n, { width: 300, up: false });
  }

  function fetchContext() {
    if (ctxItems) return Promise.resolve(ctxItems);
    return fetch(api('/api/workspace-manifest')).then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (m) { ctxItems = C.contextItems(m); return ctxItems; }, function () { return []; });
  }
  function ctxMenuHtml(items) {
    if (!items.length) return '<div class="vp-pop-empty">No studies or composites found</div>';
    let out = '', last = '';
    items.slice(0, 60).forEach(function (i) {
      if (i.group !== last) { out += '<div class="vp-pop-h">' + e(i.group) + '</div>'; last = i.group; }
      out += '<button class="vp-pop-item" data-mention="' + e(i.value) + '"><span>' + e(i.label) + '</span></button>';
    });
    return out;
  }
  // From the @ button (with a search box) or from typing "@" in the input (filters as you type).
  function openContext(anchor, trigger) {
    fetchContext().then(function (all) {
      const n = div(trigger ? '<div class="vp-pop-scroll" id="vp-ctx-list"></div>'
        : '<input class="vp-pop-input" id="vp-ctx-q" placeholder="Search studies and composites…"><div class="vp-pop-scroll" id="vp-ctx-list"></div>');
      openPop(anchor, n, { width: 300 });
      const paint = function (q) {
        n.querySelector('#vp-ctx-list').innerHTML = ctxMenuHtml(C.filterItems(all, q));
        const first = n.querySelector('[data-mention]'); if (first) first.classList.add('hi');
      };
      paint(trigger ? trigger.query : '');
      const qbox = n.querySelector('#vp-ctx-q');
      if (qbox) { qbox.addEventListener('input', function () { paint(qbox.value); }); qbox.focus(); }
      pop.paint = paint;
      n.addEventListener('click', function (ev) {
        const b = ev.target.closest('[data-mention]'); if (b) insertMention(b.getAttribute('data-mention'));
      });
    });
  }
  function insertMention(value) {
    const ta = el.input, caret = ta.selectionStart || ta.value.length;
    const t = C.mentionQuery(ta.value, caret);
    let out;
    if (t) out = C.insertMention(ta.value, t.start, caret, value);
    else {
      const pre = ta.value.slice(0, caret), sp = pre && !/\s$/.test(pre) ? ' ' : '';
      out = { text: pre + sp + value + ' ' + ta.value.slice(caret), caret: pre.length + sp.length + value.length + 1 };
    }
    ta.value = out.text; ta.setSelectionRange(out.caret, out.caret); autosize(ta); closePop(); ta.focus();
  }

  // "Previous chats": search, newest first, grouped by date with dividers, time-ago per row.
  function openHistory(anchor) {
    const n = div('<input class="vp-pop-input" id="vp-h-q" placeholder="Search chat history..."><div class="vp-pop-scroll" id="vp-h-list"></div>');
    openPop(anchor, n, { width: 480, up: false, align: 'right' });
    const q = n.querySelector('#vp-h-q');
    const paint = function () {
      C.storeUpsert(store, state);
      const res = C.storeList(store, q.value);
      const list = n.querySelector('#vp-h-list');
      if (!res.total) {
        list.innerHTML = q.value
          ? '<div class="vp-pop-empty"><strong>No chats found</strong>No chats match "' + e(q.value) + '"</div>'
          : '<div class="vp-pop-empty"><strong>No chats yet</strong>Start a new chat to get started</div>';
        return;
      }
      list.innerHTML = res.groups.map(function (g, gi) {
        return (gi ? '<hr>' : '') + '<div class="vp-pop-h">' + e(g.group) + '</div>' + g.items.map(function (c) {
          return '<button class="vp-pop-item' + (c.active ? ' on' : '') + '" data-chat="' + e(c.id) + '"><span class="vp-hist-row" style="width:100%">' +
            '<span class="t">' + e(c.title) + '</span><span class="a">' + e(C.timeAgo(c.updatedAt)) + '</span></span></button>';
        }).join('');
      }).join('');
    };
    paint();
    q.addEventListener('input', paint);
    q.focus();
    n.addEventListener('click', function (ev) {
      const b = ev.target.closest('[data-chat]'); if (!b) return;
      closePop(); switchChat(b.getAttribute('data-chat'));
    });
  }

  // brief inline message in the composer (attachment errors, select failures)
  function flash(msg) {
    const n = div('<span class="vp-note">' + e(msg) + '</span>');
    el.flash.appendChild(n);                       // own element: renderPills() must not wipe it
    setTimeout(function () { n.remove(); }, 4000);
  }

  // ── Chats: new / switch ───────────────────────────────────────────────────
  function abortStream() { if (controller) controller.abort(); }
  function resetTransient() { queued = []; attached = []; editing = -1; }
  function newChat() {
    abortStream(); closePop();
    store = C.storeNew(store, state);
    state = C.restore(store.chats[store.active].snap);
    resetTransient(); save(); renderAll();
    el.input.focus();
  }
  function switchChat(id) {
    abortStream();
    const next = C.storeSwitch(store, id, state);
    if (!next) return;
    state = next; resetTransient(); save(); renderAll();
  }

  // ── Turn streaming ────────────────────────────────────────────────────────
  function refreshWorkspaceViews() {
    // Other tabs memoise their first load; clear those flags so the next visit
    // (and the rail) reflect what the assistant just changed.
    window._registryLoaded = false;
    window._investigationsLoaded = false;
    REFRESH_AFTER_MUTATION.forEach(function (fn) {
      try { if (typeof window[fn] === 'function') window[fn](); } catch (x) { /* best effort */ }
    });
  }
  const withPrefs = (body) => Object.assign({}, body, { mode: prefs.mode, include_manifest: prefs.manifest });

  function streamTurn(rawBody) {
    const body = withPrefs(rawBody);
    controller = new AbortController();
    const splitter = C.createSplitter();
    let mutated = false;
    function handle(f) {
      if (f.type === 'ping') return;          // keep-alive while a long tool call runs
      C.applyFrame(state, f);
      if (f.type === 'tool-result') {
        const last = state.ui[state.ui.length - 1];
        const tool = last && (last.parts || []).filter(function (p) { return p.kind === 'tool' && p.id === f.tool_call_id; })[0];
        if (tool && tool.approval && tool.status === 'done') mutated = true;
      }
      renderLast();
    }
    return fetch(api('/api/chat/turn'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: controller.signal,
    }).then(function (resp) {
      if (!resp.ok) {
        return resp.json().catch(function () { return {}; }).then(function (j) { throw new Error(j.error || ('HTTP ' + resp.status)); });
      }
      const reader = resp.body.getReader(), dec = new TextDecoder();
      const pump = function () {
        return reader.read().then(function (r) {
          if (r.done) { splitter.flush().forEach(handle); return; }
          splitter.push(dec.decode(r.value, { stream: true })).forEach(handle);
          return pump();
        });
      };
      return pump();
    }).catch(function (err) {
      const m = state.ui[state.ui.length - 1];
      if (err && err.name === 'AbortError') {
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
      drainQueue();
    });
  }

  function drainQueue() {
    if (!queued.length || state.busy || state.pending.length || !canChat()) return;
    const next = queued.shift();
    sendPrompt(next.text, next.files);
  }

  function sendPrompt(text, files) {
    text = (text || '').trim();
    if (!text && !(files && files.length)) return;
    if (!canChat()) { openSettings(); return; }
    const composed = C.composePrompt(text, files);
    const body = C.buildPromptRequest(state, composed);
    C.startUserTurn(state, composed);
    // the box shows what the user typed (+ file pills); the model gets the composed prompt
    const u = state.ui[state.ui.length - 2];
    u.text = text; u.files = (files || []).map(function (f) { return f.name; });
    el.pinned = true;
    save(); renderAll();
    streamTurn(body);
  }

  function submit() {
    const text = el.input.value.trim();
    if (!text && !attached.length) return;
    const files = attached.slice();
    el.input.value = ''; attached = []; autosize(el.input); closePop();
    // Sent while a turn runs (or approvals are pending)? Queue it (dashed, spinner).
    if (state.busy || state.pending.length) { queued.push({ text: text, files: files }); renderAll(); return; }
    sendPrompt(text, files);
  }

  function resume() {
    const body = C.buildResumeRequest(state);
    C.startResume(state);
    save(); renderAll();
    streamTurn(body);
  }
  function decide(id, approved) {
    const all = C.decide(state, id, approved);
    save();
    if (all) resume(); else renderAll();
  }
  function decideEvery(approved) {
    const all = C.decideAll(state, approved);
    save();
    if (all) resume(); else renderAll();
  }
  function retry() {
    if (!state.retry || state.busy) return;
    const m = state.ui[state.ui.length - 1];
    if (m && m.parts) m.parts = m.parts.filter(function (p) { return p.kind !== 'error'; });
    state.busy = true; save(); renderAll();
    streamTurn(C.retryBody(state));
  }
  function resendEdited(idx, text) {
    text = text.trim();
    if (!text || state.busy) return;
    if (!C.truncateAt(state, idx)) return;
    editing = -1;
    sendPrompt(text, []);
  }

  // ── Panel: open/close, resize, settings sheet ─────────────────────────────
  function setOpen(open) {
    panel.hidden = !open;
    document.body.classList.toggle('viv-ai-open', open);
    el.toggle.classList.toggle('viv-ai-on', open);
    el.toggle.setAttribute('aria-pressed', open ? 'true' : 'false');
    lsSet('viv.ai.open', open ? '1' : '0');
    syncDockVars();
    if (open) {
      refreshStatus().then(function () { if (el.card.hidden) { el.input.focus(); scrollDown(true); } });
    } else closePop();
  }
  function openSettings() {
    closePop();
    el.card.hidden = false;
    if (typeof window._loadAiLogin === 'function') window._loadAiLogin();
  }
  function closeSettings() { el.card.hidden = true; refreshStatus(); }

  // ── Docking ───────────────────────────────────────────────────────────────
  const sizeKey = (d) => d === 'bottom' ? 'viv.ai.h' : 'viv.ai.w';
  function applySize() {
    const n = parseInt(lsGet(sizeKey(dock), ''), 10);
    const size = n ? C.clampDock(dock, n, innerWidth, innerHeight) : (dock === 'bottom' ? 320 : 440);
    panel.style.setProperty(dock === 'bottom' ? '--viv-ai-h' : '--viv-ai-w', size + 'px');
  }
  // Published on <html> so fixed-position/viewport-height layouts elsewhere leave room for the panel.
  function syncDockVars() {
    const r = panel.getBoundingClientRect(), on = !panel.hidden;
    const root = document.documentElement.style;
    root.setProperty('--viv-ai-left', on && dock === 'left' ? Math.round(r.width) + 'px' : '0px');
    // distance from the viewport's right edge to the panel's left edge: includes whatever sits to its
    // right (the code rail — collapsed edge tab or open panel), which fixed overlays must also clear
    root.setProperty('--viv-ai-right', on && dock === 'right' ? Math.round(innerWidth - r.left) + 'px' : '0px');
    // the panel's own width when docked right (the maximized-card + open-code-rail case pins the rail beside it)
    root.setProperty('--viv-ai-rw', on && dock === 'right' ? Math.round(r.width) + 'px' : '0px');
    root.setProperty('--viv-ai-bottom', on && dock === 'bottom' ? Math.round(r.height) + 'px' : '0px');
    window.dispatchEvent(new CustomEvent('viv:ai-layout'));
  }
  function dockTo(zone, persist) {
    if (!C.validDock(zone) || !layout || !mainEl) return;
    closePop();
    dock = zone;
    panel.dataset.dock = zone;
    if (zone === 'left') layout.insertBefore(panel, mainEl);
    else if (zone === 'right') layout.insertBefore(panel, codeRail || null);
    else mainEl.appendChild(panel);                       // below <main>, inside the flex column
    applySize();
    if (persist) lsSet('viv.ai.dock', zone);
    syncDockVars();
    if (!panel.hidden) scrollDown(true);
  }

  function initResize() {
    const h = document.getElementById('viv-ai-resize');
    h.addEventListener('mousedown', function (ev) {
      ev.preventDefault(); h.classList.add('dragging');
      const r0 = panel.getBoundingClientRect();
      const move = function (m) {
        const raw = dock === 'left' ? m.clientX - r0.left : dock === 'right' ? r0.right - m.clientX : r0.bottom - m.clientY;
        const size = C.clampDock(dock, raw, innerWidth, innerHeight);
        panel.style.setProperty(dock === 'bottom' ? '--viv-ai-h' : '--viv-ai-w', size + 'px');
      };
      const up = function () {
        h.classList.remove('dragging');
        document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
        const r = panel.getBoundingClientRect();
        lsSet(sizeKey(dock), String(Math.round(dock === 'bottom' ? r.height : r.width)));
      };
      document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
    });
  }

  // Drag the AI chip (rail item or panel header) to an edge to re-dock it, like PyCharm's tool
  // windows. A small movement threshold keeps a plain click a click. Esc cancels.
  let justDragged = false;
  function initChipDrag() {
    const THRESH = 6;
    function begin(ev, source) {
      if (ev.button !== undefined && ev.button !== 0) return;
      if (ev.target.closest && ev.target.closest('button')) return;      // header buttons keep their own clicks
      const x0 = ev.clientX, y0 = ev.clientY;
      let ghost = null, zones = null, zone = null, active = false;
      const mk = function () {
        ghost = document.createElement('div');
        ghost.className = 'vp-ghost'; ghost.innerHTML = ICON.bot + '<span>AI</span>';
        zones = document.createElement('div');
        zones.className = 'vp-zones';
        zones.innerHTML = ['left', 'right', 'bottom'].map(function (z) { return '<div class="vp-zone vp-zone-' + z + '" data-zone="' + z + '"><span>Dock ' + z + '</span></div>'; }).join('');
        document.body.appendChild(zones); document.body.appendChild(ghost);
      };
      const move = function (m) {
        if (!active) {
          if (Math.hypot(m.clientX - x0, m.clientY - y0) < THRESH) return;
          active = true; mk(); document.body.classList.add('vp-dragging');
        }
        ghost.style.left = (m.clientX + 10) + 'px'; ghost.style.top = (m.clientY + 10) + 'px';
        zone = C.dropZone(m.clientX, m.clientY, innerWidth, innerHeight);
        Array.prototype.forEach.call(zones.children, function (z) { z.classList.toggle('on', z.getAttribute('data-zone') === zone); });
      };
      const end = function (commit) {
        document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up, true);
        document.removeEventListener('pointercancel', cancel, true); window.removeEventListener('blur', cancel);
        document.removeEventListener('keydown', key, true);
        if (!active) return;
        document.body.classList.remove('vp-dragging');
        ghost.remove(); zones.remove();
        justDragged = true; setTimeout(function () { justDragged = false; }, 0);   // swallow the click that follows a drag
        if (commit && zone) { dockTo(zone, true); if (panel.hidden) setOpen(true); }
      };
      // The release position is authoritative (a fast flick may end far from the last pointermove).
      const up = function (u) {
        if (active && u && u.clientX !== undefined) zone = C.dropZone(u.clientX, u.clientY, innerWidth, innerHeight);
        end(true);
      };
      const cancel = function () { end(false); };            // the browser took the gesture away: never stay stuck
      const key = function (k) { if (k.key === 'Escape') { k.preventDefault(); end(false); } };
      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up, true);
      document.addEventListener('pointercancel', cancel, true);
      window.addEventListener('blur', cancel);
      document.addEventListener('keydown', key, true);
    }
    // The rail item is an <a>: without this the browser starts a native link drag, which cancels
    // the pointer events and would strand the ghost chip and drop zones on screen.
    el.toggle.setAttribute('draggable', 'false');
    el.toggle.addEventListener('dragstart', function (ev) { ev.preventDefault(); });
    el.toggle.addEventListener('pointerdown', function (ev) { begin(ev, 'rail'); });
    panel.querySelector('.vp-head').addEventListener('pointerdown', function (ev) { begin(ev, 'header'); });
  }

  function openDockMenu(anchor) {
    const glyph = (d) => S('<rect x="3" y="4" width="18" height="16" rx="2"/>' +
      (d === 'left' ? '<path d="M9 4v16"/>' : d === 'right' ? '<path d="M15 4v16"/>' : '<path d="M3 14h18"/>'));
    dropdown(anchor, {
      items: [['left', 'Dock left'], ['right', 'Dock right'], ['bottom', 'Dock bottom']].map(function (d) {
        return { id: d[0], label: d[1], icon: glyph(d[0]), on: dock === d[0] };
      }),
      width: 180,
      onPick: function (id) { dockTo(id, true); },
    });
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
      .then(function () {
        if (status.selected) C.addKnown(status.selected.provider, [status.selected.model]);
        renderAll();
        drainQueue();
      });
  }

  // ── DOM ───────────────────────────────────────────────────────────────────
  function build() {
    root.innerHTML =
      '<div class="vp-head" title="Drag to dock left, right or bottom"><span>AI</span><span class="vp-spacer"></span>' +
        '<button class="vp-icon" data-act="dock" title="Move panel" aria-label="Move panel">' + ICON.dock + '</button>' +
        '<button class="vp-icon" data-act="close" title="Close" aria-label="Close">' + ICON.x + '</button></div>' +
      '<div class="vp-toolbar">' +
        '<button class="vp-icon" data-act="new" title="New chat" aria-label="New chat">' + ICON.plus + '</button><span class="vp-spacer"></span>' +
        '<button class="vp-icon" id="vp-plug" data-act="settings" aria-label="Provider status">' + ICON.plug + '</button>' +
        '<button class="vp-icon" data-act="settings" title="AI Settings" aria-label="AI Settings">' + ICON.gear + '</button>' +
        '<button class="vp-icon" id="vp-hist" data-act="history" title="Previous chats" aria-label="Previous chats">' + ICON.clock + '</button>' +
      '</div>' +
      '<div class="vp-main">' +
        '<div class="vp-new" id="vp-new"><div id="vp-new-copy"></div><div id="vp-new-host"></div>' +
          '<button class="vp-callout" id="vp-callout" data-act="connect">' + ICON.sparkles + '<span>Connect your own agent to this workspace</span></button></div>' +
        '<div class="vp-list" id="vp-list" hidden></div>' +
        '<button class="vp-scroll" id="vp-scroll" title="Scroll to bottom" hidden>' + ICON.down + '</button>' +
      '</div>' +
      '<div class="vp-stop" id="vp-stop" hidden><button data-act="stop">Stop</button></div>' +
      '<div class="vp-foot" id="vp-foot" hidden></div>';

    const composer = document.createElement('div');
    composer.className = 'vp-composer'; composer.id = 'vp-composer';
    composer.innerHTML =
      '<div class="vp-flash" id="vp-flash"></div>' +
      '<div class="vp-pills" id="vp-pills"></div>' +
      '<textarea class="vp-input-area" id="vp-input" rows="1" spellcheck="true"></textarea>' +
      '<div class="vp-composer-bar">' +
        '<button class="vp-pill" id="vp-mode" data-act="mode" title="Mode"></button>' +
        '<button class="vp-pill" id="vp-model" data-act="model" title="Model"></button>' +
        '<button class="vp-icon" data-act="caps" title="Capabilities" aria-label="Capabilities">' + ICON.sliders + '</button>' +
        '<span class="vp-spacer"></span>' +
        '<button class="vp-icon" data-act="ctx" title="Add context" aria-label="Add context">' + ICON.at + '</button>' +
        '<button class="vp-icon" data-act="attach" title="Attach a file" aria-label="Attach a file">' + ICON.clip + '</button>' +
        '<button class="vp-icon" id="vp-send" data-act="send" title="Submit" aria-label="Submit">' + ICON.send + '</button>' +
        '<input type="file" id="vp-file" multiple hidden accept="' + C.ATTACH.exts.map(function (x) { return '.' + x; }).join(',') + '">' +
      '</div>';
    el.composer = composer;
    el.new = root.querySelector('#vp-new'); el.newCopy = root.querySelector('#vp-new-copy'); el.newHost = root.querySelector('#vp-new-host');
    el.callout = root.querySelector('#vp-callout'); el.list = root.querySelector('#vp-list'); el.foot = root.querySelector('#vp-foot');
    el.scroll = root.querySelector('#vp-scroll'); el.stop = root.querySelector('#vp-stop'); el.plug = root.querySelector('#vp-plug');
    el.input = composer.querySelector('#vp-input'); el.pills = composer.querySelector('#vp-pills'); el.flash = composer.querySelector('#vp-flash');
    el.mode = composer.querySelector('#vp-mode'); el.model = composer.querySelector('#vp-model');
    el.send = composer.querySelector('#vp-send'); el.file = composer.querySelector('#vp-file');
    el.card = document.getElementById('viv-ai-card'); el.toggle = document.getElementById('viv-ai-toggle');
    el.pinned = true;

    el.list.addEventListener('scroll', function () { el.pinned = nearBottom(); el.scroll.hidden = el.pinned; });
    el.scroll.addEventListener('click', function () { el.pinned = true; scrollDown(true); el.scroll.hidden = true; });
    el.input.addEventListener('input', function () {
      autosize(el.input);
      const t = C.mentionQuery(el.input.value, el.input.selectionStart);
      if (t) { if (pop && pop.paint) pop.paint(t.query); else openContext(el.input, t); } else if (pop && pop.paint) closePop();
    });
    el.input.addEventListener('keydown', function (ev) {
      const list = pop && pop.paint ? pop.node.querySelectorAll('[data-mention]') : null;
      if (list && list.length && (ev.key === 'ArrowDown' || ev.key === 'ArrowUp')) {
        ev.preventDefault();
        const items = Array.prototype.slice.call(list);
        let i = items.findIndex(function (x) { return x.classList.contains('hi'); });
        items.forEach(function (x) { x.classList.remove('hi'); });
        i = (i + (ev.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items[i].classList.add('hi'); items[i].scrollIntoView({ block: 'nearest' });
        return;
      }
      if (list && list.length && (ev.key === 'Enter' || ev.key === 'Tab')) {
        ev.preventDefault();
        insertMention((pop.node.querySelector('[data-mention].hi') || list[0]).getAttribute('data-mention'));
        return;
      }
      if (ev.key === 'Escape') { if (pop) closePop(); else el.input.blur(); return; }
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); submit(); }
    });
    el.file.addEventListener('change', function () {
      Array.prototype.slice.call(el.file.files).forEach(function (f) {
        const err = C.attachError(attached, { name: f.name, size: f.size });
        if (err) return flash(err);
        const rd = new FileReader();
        rd.onload = function () { attached.push({ name: f.name, size: f.size, content: String(rd.result) }); renderPills(); };
        rd.readAsText(f);
      });
      el.file.value = '';
    });

    panel.addEventListener('keydown', function (ev) {
      if (ev.target && ev.target.matches && ev.target.matches('[data-edit-input]')) {
        if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); resendEdited(editing, ev.target.value); }
        else if (ev.key === 'Escape') { editing = -1; renderAll(); }
      }
    });
    panel.addEventListener('input', function (ev) { if (ev.target.matches && ev.target.matches('[data-edit-input]')) autosize(ev.target); });
    panel.addEventListener('click', function (ev) {
      const b = ev.target.closest('[data-act]'); if (!b) return;
      const act = b.getAttribute('data-act');
      const host = b.closest('[data-id]');
      switch (act) {
        case 'close': setOpen(false); break;
        case 'dock': openDockMenu(b); break;
        case 'new': newChat(); break;
        case 'settings': openSettings(); break;
        case 'history': openHistory(b); break;
        case 'mode': openModeMenu(b); break;
        case 'model': openModelMenu(b); break;
        case 'caps': openCapabilities(b); break;
        case 'ctx': openContext(b, null); break;
        case 'connect': openConnect(b); break;
        case 'attach': el.file.click(); break;
        case 'unattach': attached.splice(+b.getAttribute('data-idx'), 1); renderPills(); break;
        case 'send': if (state.busy) abortStream(); else submit(); break;
        case 'stop': abortStream(); break;
        case 'approve': if (host) decide(host.getAttribute('data-id'), true); break;
        case 'deny': if (host) decide(host.getAttribute('data-id'), false); break;
        case 'approve-all': decideEvery(true); break;
        case 'deny-all': decideEvery(false); break;
        case 'retry': retry(); break;
        case 'edit': if (!state.busy && !window.getSelection().toString()) { editing = +b.getAttribute('data-idx'); renderAll(); } break;
        case 'edit-cancel': editing = -1; renderAll(); break;
        case 'edit-send': { const ta = el.list.querySelector('[data-edit-input]'); if (ta) resendEdited(editing, ta.value); break; }
        case 'copy': {
          const row = b.closest('.vp-asst');
          const text = row ? row.querySelector('.vp-body').innerText : '';
          if (navigator.clipboard) navigator.clipboard.writeText(text).catch(function () {});
          break;
        }
      }
    });
    // Remember which tool rows / reasoning blocks the user expanded across re-renders.
    panel.addEventListener('toggle', function (ev) {
      const d = ev.target;
      if (!d.matches) return;
      const last = state.ui[state.ui.length - 1];
      if (d.matches('details.vp-tool')) {
        const id = d.getAttribute('data-id');
        state.ui.forEach(function (m) { (m.parts || []).forEach(function (p) { if (p.kind === 'tool' && p.id === id) p.open = d.open; }); });
      } else if (d.matches('details.vp-reason') && last && last.parts && !state.busy) {
        const all = Array.prototype.slice.call(d.closest('.vp-body').querySelectorAll('details.vp-reason'));
        const p = last.parts.filter(function (x) { return x.kind === 'reasoning'; })[all.indexOf(d)];
        if (p) p.open = d.open;
      }
    }, true);

    el.toggle.addEventListener('click', function (ev) { ev.preventDefault(); if (justDragged) return; setOpen(panel.hidden); });
    el.toggle.title = 'AI — click to toggle, drag to dock left, right or bottom';
    document.getElementById('viv-ai-back').addEventListener('click', closeSettings);
    window.addEventListener('viv:ai-changed', function () { refreshStatus(); });
    initResize();
    initChipDrag();
    if (window.ResizeObserver) new ResizeObserver(syncDockVars).observe(panel);
    window.addEventListener('resize', function () { applySize(); syncDockVars(); });
  }

  build();
  try { renderAll(); } catch (x) {
    // A corrupted sessionStorage transcript must not take the panel down.
    store = C.newStore(); state = C.restore(store.chats[store.active].snap); save(); renderAll();
  }
  dockTo(dock, false);
  window._openAiPanel = function () { setOpen(true); };
  if (lsGet('viv.ai.open', '0') === '1') setOpen(true);
})();
