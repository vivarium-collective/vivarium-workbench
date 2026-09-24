// process-code.js — the collapsible right-rail code panel.
//
// Shows the source of a registered process/step OR a composite (its spec YAML,
// or its @composite_generator module) and, when the file lives in the editable
// workspace tree, saves edits back. The backend refuses writes to installed
// dependencies and rejects invalid syntax before touching disk; this panel
// mirrors that with a read-only badge + a "saving modifies workspace source" note.
//
// The editor is CodeMirror 5 loaded lazily from cdnjs on first open (Python +
// YAML modes), with a plain <textarea> fallback when the CDN is unreachable.
(function () {
  'use strict';

  var CM_VERSION = '5.65.16';
  var CM_BASE = 'https://cdnjs.cloudflare.com/ajax/libs/codemirror/' + CM_VERSION;

  var state = {
    original: '',      // source as last loaded/saved (for dirty + revert)
    editable: false,
    lang: 'python',
    save: null,        // { url, base } describing how to POST edits
    cm: null,          // CodeMirror instance, or null (textarea fallback)
    cmTried: false,
    modes: {},         // which CM modes have been requested
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

  function getValue() {
    if (state.cm) return state.cm.getValue();
    var ta = textarea(); return ta ? ta.value : '';
  }
  function setValue(v) {
    if (state.cm) { state.cm.setValue(v); }
    else { var ta = textarea(); if (ta) ta.value = v; }
  }
  function setReadOnly(ro) {
    if (state.cm) { state.cm.setOption('readOnly', ro ? 'nocursor' : false); }
    else { var ta = textarea(); if (ta) ta.readOnly = ro; }
  }
  function cmMode(lang) {
    if (lang === 'yaml') return 'yaml';
    if (lang === 'json') return { name: 'javascript', json: true };
    return 'python';
  }
  function setMode(lang) {
    if (state.cm) { try { state.cm.setOption('mode', cmMode(lang)); } catch (e) {} }
  }

  // ── expand / collapse ──
  function isOpen() { var r = rail(); return r && !r.classList.contains('viv-code-collapsed'); }
  function open() {
    var r = rail(); if (!r) return;
    r.classList.remove('viv-code-collapsed');
    document.body.classList.add('viv-code-open');
    try { localStorage.setItem('viv.code.open', '1'); } catch (e) {}
    if (state.cm) setTimeout(function () { try { state.cm.refresh(); } catch (e) {} }, 30);
  }
  function collapse() {
    var r = rail(); if (!r) return;
    r.classList.add('viv-code-collapsed');
    document.body.classList.remove('viv-code-open');
    try { localStorage.setItem('viv.code.open', '0'); } catch (e) {}
  }
  function toggle() { if (isOpen()) collapse(); else open(); }

  function refreshDirty() {
    var saveBtn = $('viv-code-save'), revertBtn = $('viv-code-revert');
    var dirty = state.editable && (getValue() !== state.original);
    if (saveBtn) saveBtn.disabled = !dirty || state.loading;
    if (revertBtn) revertBtn.disabled = !dirty || state.loading;
  }
  function setStatus(msg, kind) {
    var el = $('viv-code-status'); if (!el) return;
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
    var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = url;
    document.head.appendChild(l);
  }
  function ensureCore() {
    if (window.CodeMirror) return Promise.resolve();
    if (state.cmTried) return Promise.resolve();
    state.cmTried = true;
    loadCss(CM_BASE + '/codemirror.min.css');
    return loadScript(CM_BASE + '/codemirror.min.js').catch(function () {});
  }
  function ensureMode(lang) {
    if (!window.CodeMirror) return Promise.resolve();
    var mode = (lang === 'yaml') ? 'yaml' : (lang === 'json') ? 'javascript' : 'python';
    if (state.modes[mode]) return Promise.resolve();
    state.modes[mode] = true;
    return loadScript(CM_BASE + '/mode/' + mode + '/' + mode + '.min.js').catch(function () {});
  }
  function ensureEditor(lang) {
    return ensureCore().then(function () { return ensureMode(lang); }).then(upgradeEditor);
  }
  function upgradeEditor() {
    if (state.cm || !window.CodeMirror) return;
    var ta = textarea(); if (!ta) return;
    try {
      state.cm = window.CodeMirror.fromTextArea(ta, {
        mode: cmMode(state.lang), lineNumbers: true, indentUnit: 2,
        lineWrapping: false, viewportMargin: Infinity,
      });
      state.cm.on('change', refreshDirty);
      state.cm.setSize('100%', '100%');
    } catch (e) { state.cm = null; }
  }

  // ── unified loader ──
  // ctx: { title, subtitle, getUrl, lang, save:{url, base} }
  function load(ctx) {
    open();
    state.loading = true;
    state.save = ctx.save || null;
    state.lang = ctx.lang || 'python';
    setStatus('Loading…');
    var name = $('viv-code-name'), addrEl = $('viv-code-addr');
    if (name) name.textContent = ctx.title || 'Code';
    if (addrEl) addrEl.textContent = ctx.subtitle || '';
    var empty = $('viv-code-empty'); if (empty) empty.hidden = true;
    var ta = textarea(); if (ta) ta.hidden = false;

    ensureEditor(state.lang).then(function () {
      return fetch(_api(ctx.getUrl))
        .then(function (r) { return r.json(); })
        .then(function (j) {
          state.loading = false;
          if (!j || j.ok !== true) {
            setStatus((j && j.error) || 'Could not load source.', 'error');
            state.editable = false; setReadOnly(true); refreshDirty();
            return;
          }
          if (j.lang) {
            state.lang = j.lang;
            if (state.save && state.save.base) state.save.base.lang = j.lang;
          }
          return ensureMode(state.lang).then(function () {
            setMode(state.lang);
            state.original = j.source || '';
            setValue(state.original);
            state.editable = !!j.editable && !_snapshot();
            setReadOnly(!state.editable);
            renderMeta(j);
            setStatus('');
            refreshDirty();
            if (state.cm) setTimeout(function () { try { state.cm.refresh(); } catch (e) {} }, 20);
          });
        })
        .catch(function (e) { state.loading = false; setStatus('Load failed: ' + e, 'error'); });
    });
  }

  function renderMeta(j) {
    var badge = $('viv-code-badge'), path = $('viv-code-path');
    if (path) path.textContent = (j.lang ? j.lang.toUpperCase() + ' · ' : '') + (j.path || '');
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

  // ── public open() entrypoints ──
  function openProcess(address) {
    if (!address) return;
    load({
      title: address.split(/[.:]/).pop() || 'Process',
      subtitle: address,
      getUrl: '/api/registry/process-source?address=' + encodeURIComponent(address),
      lang: 'python',
      save: { url: '/api/registry/process-source', base: { address: address } },
    });
  }
  // desc: { id, name, module, source_path }
  function openComposite(desc) {
    desc = desc || {};
    var id = desc.id || '';
    var q = '/api/composites/source?id=' + encodeURIComponent(id) +
      '&module=' + encodeURIComponent(desc.module || '') +
      '&source_path=' + encodeURIComponent(desc.source_path || '');
    load({
      title: desc.name || (id.split('.').pop()) || 'Composite',
      subtitle: id,
      getUrl: q,
      lang: desc.source_path && /\.ya?ml$/i.test(desc.source_path) ? 'yaml' : 'python',
      save: {
        url: '/api/composites/source',
        base: { id: id, module: desc.module || '', source_path: desc.source_path || '' },
      },
    });
  }

  function revert() { setValue(state.original); setStatus('Reverted.'); refreshDirty(); }

  function save() {
    if (!state.editable || !state.save) return;
    var src = getValue();
    if (src === state.original) return;
    state.loading = true; refreshDirty(); setStatus('Saving…');
    var body = {};
    var base = state.save.base || {};
    for (var k in base) if (base.hasOwnProperty(k)) body[k] = base[k];
    body.source = src;
    fetch(_api(state.save.url), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        state.loading = false;
        if (j && j.ok === true) {
          state.original = src;
          setStatus('Saved ✓ (' + (j.bytes || src.length) + ' bytes)', 'ok');
        } else { setStatus((j && j.error) || 'Save failed.', 'error'); }
        refreshDirty();
      })
      .catch(function (e) { state.loading = false; setStatus('Save failed: ' + e, 'error'); refreshDirty(); });
  }

  // ── drag-to-resize (panel grows leftward) ──
  function initResize() {
    var handle = $('viv-code-resize-handle'), r = rail();
    if (!handle || !r) return;
    var startX = 0, startW = 0, dragging = false;
    try {
      var saved = parseInt(localStorage.getItem('viv.code.width') || '0', 10);
      if (saved >= 320 && saved <= 1100) r.style.setProperty('--viv-code-w', saved + 'px');
    } catch (e) {}
    handle.addEventListener('mousedown', function (ev) {
      dragging = true; startX = ev.clientX; startW = r.getBoundingClientRect().width;
      document.body.style.userSelect = 'none'; ev.preventDefault();
    });
    window.addEventListener('mousemove', function (ev) {
      if (!dragging) return;
      var w = Math.max(320, Math.min(1100, startW + (startX - ev.clientX)));
      r.style.setProperty('--viv-code-w', w + 'px');
      if (state.cm) { try { state.cm.refresh(); } catch (e) {} }
    });
    window.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false; document.body.style.userSelect = '';
      try { localStorage.setItem('viv.code.width', String(Math.round(rail().getBoundingClientRect().width))); } catch (e) {}
    });
  }

  function init() {
    if (!rail()) return;
    initResize();
    try { if (localStorage.getItem('viv.code.open') === '1') open(); } catch (e) {}
  }

  window.ProcessCode = {
    open: openProcess,        // process/step by registry address
    openComposite: openComposite,
    toggle: toggle,
    collapse: collapse,
    save: save,
    revert: revert,
    init: init,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else { init(); }
})();
