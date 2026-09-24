// process-code.js — the collapsible right-rail Python code panel.
//
// Shows the source of a registered process/step (GET /api/registry/process-source)
// and, when the file lives inside the editable workspace tree, saves edits back
// (POST /api/registry/process-source). The backend refuses writes to installed
// dependencies and rejects syntax errors before touching disk; this panel mirrors
// that with a read-only badge + a "saving modifies workspace source" note.
//
// The editor is CodeMirror 5 loaded lazily from cdnjs on first open, with a plain
// <textarea> fallback when the CDN is unreachable (offline workbench).
(function () {
  'use strict';

  var CM_VERSION = '5.65.16';
  var CM_BASE = 'https://cdnjs.cloudflare.com/ajax/libs/codemirror/' + CM_VERSION;

  var state = {
    address: null,     // currently loaded address
    original: '',      // source as last loaded/saved (for dirty + revert)
    editable: false,
    cm: null,          // CodeMirror instance, or null (textarea fallback)
    cmTried: false,    // have we attempted to load CodeMirror?
    loading: false,
  };

  function _api(p) {
    return (window.DataSource && window.DataSource.apiUrl)
      ? window.DataSource.apiUrl(p) : p;
  }
  function _snapshot() {
    return !!(document.body && document.body.classList.contains('snapshot'));
  }
  function $(id) { return document.getElementById(id); }

  function rail() { return $('viv-code-rail'); }
  function textarea() { return $('viv-code-textarea'); }

  // ── editor value get/set (works for both CodeMirror and the textarea) ──
  function getValue() {
    if (state.cm) return state.cm.getValue();
    var ta = textarea();
    return ta ? ta.value : '';
  }
  function setValue(v) {
    if (state.cm) { state.cm.setValue(v); }
    else { var ta = textarea(); if (ta) ta.value = v; }
  }
  function setReadOnly(ro) {
    if (state.cm) { state.cm.setOption('readOnly', ro ? 'nocursor' : false); }
    else { var ta = textarea(); if (ta) ta.readOnly = ro; }
  }

  // ── expand / collapse ──
  function isOpen() {
    var r = rail();
    return r && !r.classList.contains('viv-code-collapsed');
  }
  function open() {
    var r = rail();
    if (!r) return;
    r.classList.remove('viv-code-collapsed');
    document.body.classList.add('viv-code-open');
    try { localStorage.setItem('viv.code.open', '1'); } catch (e) {}
    if (state.cm) setTimeout(function () { try { state.cm.refresh(); } catch (e) {} }, 30);
  }
  function collapse() {
    var r = rail();
    if (!r) return;
    r.classList.add('viv-code-collapsed');
    document.body.classList.remove('viv-code-open');
    try { localStorage.setItem('viv.code.open', '0'); } catch (e) {}
  }
  function toggle() { if (isOpen()) collapse(); else open(); }

  // ── dirty tracking ──
  function refreshDirty() {
    var saveBtn = $('viv-code-save');
    var revertBtn = $('viv-code-revert');
    var dirty = state.editable && (getValue() !== state.original);
    if (saveBtn) saveBtn.disabled = !dirty || state.loading;
    if (revertBtn) revertBtn.disabled = !dirty || state.loading;
  }

  function setStatus(msg, kind) {
    var el = $('viv-code-status');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'viv-code-status' + (kind ? ' viv-code-status-' + kind : '');
  }

  // ── CodeMirror lazy loader ──
  function loadScript(url) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = url; s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }
  function loadCss(url) {
    var l = document.createElement('link');
    l.rel = 'stylesheet'; l.href = url;
    document.head.appendChild(l);
  }
  function ensureCodeMirror() {
    if (state.cm || state.cmTried) return Promise.resolve();
    state.cmTried = true;
    if (window.CodeMirror) return Promise.resolve();
    loadCss(CM_BASE + '/codemirror.min.css');
    return loadScript(CM_BASE + '/codemirror.min.js')
      .then(function () { return loadScript(CM_BASE + '/mode/python/python.min.js'); })
      .catch(function () { /* offline: stay on the textarea fallback */ });
  }
  function upgradeEditor() {
    if (state.cm || !window.CodeMirror) return;
    var ta = textarea();
    if (!ta) return;
    try {
      state.cm = window.CodeMirror.fromTextArea(ta, {
        mode: 'python',
        lineNumbers: true,
        indentUnit: 4,
        lineWrapping: false,
        viewportMargin: Infinity,
      });
      state.cm.on('change', refreshDirty);
      state.cm.setSize('100%', '100%');
    } catch (e) { state.cm = null; }
  }

  // ── load a process's source ──
  function open_(address) {
    if (!address) return;
    open();
    state.address = address;
    state.loading = true;
    setStatus('Loading…');
    var name = $('viv-code-name');
    var addrEl = $('viv-code-addr');
    if (name) name.textContent = address.split(/[.:]/).pop() || 'Process code';
    if (addrEl) addrEl.textContent = address;
    var empty = $('viv-code-empty');
    if (empty) empty.hidden = true;
    var ta = textarea();
    if (ta) ta.hidden = false;

    ensureCodeMirror().then(function () {
      upgradeEditor();
      return fetch(_api('/api/registry/process-source?address=' + encodeURIComponent(address)))
        .then(function (r) { return r.json(); })
        .then(function (j) {
          state.loading = false;
          if (!j || j.ok !== true) {
            setStatus((j && j.error) || 'Could not load source.', 'error');
            state.editable = false;
            setReadOnly(true);
            refreshDirty();
            return;
          }
          state.original = j.source || '';
          setValue(state.original);
          state.editable = !!j.editable && !_snapshot();
          setReadOnly(!state.editable);
          renderMeta(j);
          setStatus('');
          refreshDirty();
          if (state.cm) setTimeout(function () { try { state.cm.refresh(); } catch (e) {} }, 20);
        })
        .catch(function (e) {
          state.loading = false;
          setStatus('Load failed: ' + e, 'error');
        });
    });
  }

  function renderMeta(j) {
    var badge = $('viv-code-badge');
    var path = $('viv-code-path');
    if (path) path.textContent = j.path || '';
    if (!badge) return;
    if (_snapshot()) {
      badge.textContent = 'read-only snapshot';
      badge.className = 'viv-code-badge viv-code-badge-ro';
    } else if (j.editable) {
      badge.textContent = 'editable · saving modifies workspace source';
      badge.className = 'viv-code-badge viv-code-badge-edit';
    } else {
      badge.textContent = 'read-only · outside the workspace';
      badge.className = 'viv-code-badge viv-code-badge-ro';
    }
  }

  function revert() {
    setValue(state.original);
    setStatus('Reverted.');
    refreshDirty();
  }

  function save() {
    if (!state.editable || !state.address) return;
    var src = getValue();
    if (src === state.original) return;
    state.loading = true;
    refreshDirty();
    setStatus('Saving…');
    fetch(_api('/api/registry/process-source'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address: state.address, source: src }),
    })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        state.loading = false;
        if (j && j.ok === true) {
          state.original = src;
          setStatus('Saved ✓ (' + (j.bytes || src.length) + ' bytes)', 'ok');
        } else {
          setStatus((j && j.error) || 'Save failed.', 'error');
        }
        refreshDirty();
      })
      .catch(function (e) {
        state.loading = false;
        setStatus('Save failed: ' + e, 'error');
        refreshDirty();
      });
  }

  // ── drag-to-resize (the panel grows leftward) ──
  function initResize() {
    var handle = $('viv-code-resize-handle');
    var r = rail();
    if (!handle || !r) return;
    var startX = 0, startW = 0, dragging = false;
    try {
      var saved = parseInt(localStorage.getItem('viv.code.width') || '0', 10);
      if (saved >= 320 && saved <= 1100) r.style.setProperty('--viv-code-w', saved + 'px');
    } catch (e) {}
    handle.addEventListener('mousedown', function (ev) {
      dragging = true; startX = ev.clientX;
      startW = r.getBoundingClientRect().width;
      document.body.style.userSelect = 'none';
      ev.preventDefault();
    });
    window.addEventListener('mousemove', function (ev) {
      if (!dragging) return;
      var w = Math.max(320, Math.min(1100, startW + (startX - ev.clientX)));
      r.style.setProperty('--viv-code-w', w + 'px');
      if (state.cm) { try { state.cm.refresh(); } catch (e) {} }
    });
    window.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      document.body.style.userSelect = '';
      try {
        localStorage.setItem('viv.code.width',
          String(Math.round(rail().getBoundingClientRect().width)));
      } catch (e) {}
    });
  }

  function init() {
    if (!rail()) return;
    initResize();
    // Restore last open/closed state (default: collapsed).
    try {
      if (localStorage.getItem('viv.code.open') === '1') open();
    } catch (e) {}
  }

  window.ProcessCode = {
    open: open_,
    toggle: toggle,
    collapse: collapse,
    save: save,
    revert: revert,
    init: init,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
