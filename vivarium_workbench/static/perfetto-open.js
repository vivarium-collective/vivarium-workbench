// perfetto-open.js — "⏱ Trace": open a remote run's trace in Perfetto.
//
// The workbench backend proxies the trace from viva-api (GET
// /api/remote-run-trace?simulation_id=N — Chrome Trace Event JSON, gated on the
// deployment advertising `viva-v1-trace`); this file hands those bytes to the
// Perfetto UI in a new window using Perfetto's documented postMessage protocol:
// open the UI, send "PING" until it answers "PONG" (it only answers once loaded),
// then post {perfetto: {buffer: ArrayBuffer, title, fileName, url}}. The trace is
// parsed by Perfetto's in-page WebAssembly and never leaves the browser.
//
// Which Perfetto (GET /api/remote-run-trace-support → viewer):
//   bundled  — the pinned copy this server serves at <base>/perfetto/ (same
//              origin: no outside network, no "Open trace?" prompt)
//   external — an absolute URL, normally https://ui.perfetto.dev (asks once
//              per origin whether to trust the workbench)
//   off      — no viewer: the action downloads the trace JSON instead.
//
// Progress and outcome are shown in a small status box in the workbench page
// (#viva-trace-status — never injected into Perfetto): the first open of the
// bundled viewer downloads ~30 MB and can take tens of seconds through a tunnel,
// during which the popup shows Perfetto's own empty home page. A run that
// recorded no trace events (the server's X-Trace-Events: 0, or the document
// itself when the header is absent) never reaches Perfetto: the popup is closed
// again and the status box says why, instead of an empty workspace.
//
// The action is hidden until the support check says `supported` — a deployment
// without the capability, an unreachable viva-api, and the static snapshot
// (no backend) all leave it hidden. Exposed as window.VivaPerfetto; also
// module.exports for tests/js/test_perfetto_open.js.
(function (global) {
  'use strict';

  var PING_INTERVAL_MS = 250;
  var PING_TIMEOUT_MS = 60000;   // Perfetto's first load pulls ~30 MB of wasm/js
  var HIDE_STYLE_ID = 'viva-trace-hide';
  var STATUS_ID = 'viva-trace-status';
  var STATUS_OK_HIDE_MS = 6000;  // success fades; warnings and errors stay until closed
  // The server counts events (X-Trace-Events) whenever it can; parse here only when it
  // did not say and the document is small. An empty trace is ~100 bytes, so a document
  // past this size has events and is not worth parsing just to learn that.
  var CLIENT_COUNT_MAX_BYTES = 1024 * 1024;
  var _support = null;           // cached Promise of the support body
  var _supportValue = null;      // ...and its value once resolved (sync access)

  function _bp() { return (global.__BASE_PATH__ || ''); }

  // Hide every trace action until support is confirmed. A <style> rather than
  // per-element toggling: rows are re-rendered all the time (polling, filters),
  // and each re-render must come out already in the right state.
  function _installHideStyle(doc) {
    if (!doc || !doc.head || doc.getElementById(HIDE_STYLE_ID)) return;
    var st = doc.createElement('style');
    st.id = HIDE_STYLE_ID;
    st.textContent = '.viva-trace-action{display:none !important}';
    doc.head.appendChild(st);
  }

  function _revealActions(doc) {
    var st = doc && doc.getElementById(HIDE_STYLE_ID);
    if (st && st.parentNode) st.parentNode.removeChild(st);
  }

  function support(fetchImpl) {
    if (_support) return _support;
    var f = fetchImpl || global.fetch;
    var snapshot = ((global.__DASH_CONFIG__ || {}).mode === 'snapshot');
    if (snapshot || typeof f !== 'function') {
      _support = Promise.resolve({ supported: false, reason: 'no-backend' });
      return _support;
    }
    _support = f(_bp() + '/api/remote-run-trace-support')
      .then(function (r) { return r.ok ? r.json() : { supported: false, reason: 'http-' + r.status }; })
      .catch(function (err) { return { supported: false, reason: String(err) }; })
      .then(function (body) { _supportValue = body; return body; });
    return _support;
  }

  // Perfetto's Google Analytics must never run from the workbench. Its UI (v58.3,
  // createEmbedder/initAnalytics in frontend_bundle.js) loads googletagmanager with its own
  // analytics id whenever the page's origin is `http://localhost:` / `http://127.0.0.1:` /
  // `*.perfetto.dev` -- i.e. the bundled viewer reached through an SSM tunnel on localhost,
  // and the ui.perfetto.dev fallback -- unless it runs in testing or embedded mode. Testing
  // mode (`?testing=1` in the query string) switches off analytics and nothing else in that
  // release; embedded mode would also remove the sidebar and file drop. Re-check this when
  // bumping PERFETTO_UI_VERSION (CONTRIBUTING: "Bumping the bundled Perfetto UI").
  var NO_ANALYTICS_QUERY = 'testing=1';

  function _withoutAnalytics(url) {
    var hash = '';
    var i = url.indexOf('#');
    if (i >= 0) { hash = url.slice(i); url = url.slice(0, i); }
    if (/[?&]testing=1(&|$)/.test(url)) return url + hash;
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + NO_ANALYTICS_QUERY + hash;
  }

  // The absolute Perfetto URL to open for a support body's `viewer`, or null.
  function viewerUrl(viewer, loc) {
    if (!viewer || !viewer.url || viewer.mode === 'off') return null;
    if (viewer.mode === 'bundled') {
      var origin = (loc && loc.origin) || '';
      return _withoutAnalytics(origin + _bp() + viewer.url);   // viewer.url = "/perfetto/"
    }
    return _withoutAnalytics(viewer.url);
  }

  // The origin trace bytes may be posted to, or null. Never '*': an unparseable viewer URL
  // must stop the post, not broadcast the trace to whatever origin that window holds.
  function _originOf(url) {
    try {
      var o = new URL(url).origin;
      return (o && o !== 'null') ? o : null;
    } catch (e) { return null; }
  }

  // PING `win` until it answers PONG, then post the trace. Resolves true once
  // posted, false on timeout / a closed window. `win` must be the window WE
  // opened: Perfetto only honours messages from its opener.
  function postTrace(win, targetUrl, buffer, meta, env) {
    env = env || {};
    var listenOn = env.listenOn || global;
    var setI = env.setInterval || global.setInterval;
    var clearI = env.clearInterval || global.clearInterval;
    var now = env.now || function () { return Date.now(); };
    var target = _originOf(targetUrl);
    if (!target) return Promise.resolve(false);   // no known origin -> post nothing
    return new Promise(function (resolve) {
      var started = now();
      var timer = null;
      function done(ok) {
        if (timer !== null) clearI(timer);
        timer = null;
        listenOn.removeEventListener('message', onMsg);
        resolve(ok);
      }
      function onMsg(ev) {
        if (ev.source !== win || ev.data !== 'PONG') return;
        if (ev.origin !== target) return;
        win.postMessage({ perfetto: {
          buffer: buffer,
          title: meta.title,
          fileName: meta.fileName,
          url: meta.url,
        } }, target);
        done(true);
      }
      listenOn.addEventListener('message', onMsg);
      timer = setI(function () {
        if (!win || win.closed || now() - started > PING_TIMEOUT_MS) { done(false); return; }
        try { win.postMessage('PING', target); } catch (e) { /* not loaded yet */ }
      }, PING_INTERVAL_MS);
    });
  }

  function _download(buffer, fileName, doc) {
    var blob = new Blob([buffer], { type: 'application/json' });
    var a = doc.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = fileName;
    doc.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 10000);
  }

  // The number of real (non-"M" metadata) events in a trace, or null when unknown.
  // `header` is the X-Trace-Events value (authoritative when it is a count).
  function traceEventCount(header, buffer) {
    if (header != null && /^\d+$/.test(String(header).trim())) return parseInt(header, 10);
    if (!buffer || buffer.byteLength > CLIENT_COUNT_MAX_BYTES) return null;
    var doc;
    try { doc = JSON.parse(new TextDecoder().decode(buffer)); } catch (e) { return null; }
    var events = Array.isArray(doc) ? doc : (doc && doc.traceEvents);
    if (!Array.isArray(events)) return null;
    var n = 0;
    for (var i = 0; i < events.length; i++) {
      var ev = events[i];
      if (!(ev && ev.ph === 'M')) n++;
    }
    return n;
  }

  var STATUS_COLORS = {
    loading: '#2c3e50', ok: '#2e7d32', warn: '#8a5a00', error: '#b00020',
  };

  // The status box: one fixed element, reused and updated in place, so a progress
  // message is REPLACED by its outcome rather than stacked under it.
  function _statusBox(doc) {
    if (!doc || !doc.body || typeof doc.createElement !== 'function') return null;
    var el = doc.getElementById(STATUS_ID);
    if (el) return el;
    el = doc.createElement('div');
    el.id = STATUS_ID;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:10000;max-width:440px;' +
      'padding:10px 34px 10px 14px;border-radius:6px;font:13px/1.45 system-ui,sans-serif;' +
      'color:#fff;box-shadow:0 2px 12px rgba(0,0,0,.3)';
    var text = doc.createElement('span');
    text.className = 'viva-trace-status-text';
    el.appendChild(text);
    var close = doc.createElement('button');
    close.type = 'button';
    close.className = 'viva-trace-status-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';
    close.style.cssText = 'position:absolute;top:4px;right:6px;background:none;border:0;' +
      'color:inherit;font-size:16px;cursor:pointer;line-height:1';
    close.addEventListener('click', function () { el.style.display = 'none'; });
    el.appendChild(close);
    el._text = text;
    doc.body.appendChild(el);
    return el;
  }

  // Show `msg` in the status box. `kind`: loading | ok | warn | error. `opts.notify`
  // (tests, embedders) receives every update instead of the DOM.
  function _status(opts, doc, msg, kind) {
    if (typeof opts.notify === 'function') { opts.notify(msg, kind); return; }
    var el = _statusBox(doc);
    if (!el) return;
    if (el._hideTimer) { clearTimeout(el._hideTimer); el._hideTimer = null; }
    el._text.textContent = msg;
    el.setAttribute('data-kind', kind);
    el.style.background = STATUS_COLORS[kind] || STATUS_COLORS.loading;
    el.style.display = 'block';
    if (kind === 'ok') {
      el._hideTimer = setTimeout(function () { el.style.display = 'none'; }, STATUS_OK_HIDE_MS);
    }
  }

  function _mb(bytes) {
    var mb = bytes / (1024 * 1024);
    return mb >= 1 ? mb.toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' kB';
  }

  // Open the trace of a remote run. `ref` is {simulation_id} | {composite_run_id}
  // | {compose_id}. MUST be called from the click handler itself: the Perfetto
  // window is opened synchronously so it is not popup-blocked, before the trace
  // and support fetches resolve.
  function openTrace(ref, opts) {
    opts = opts || {};
    var doc = opts.document || global.document;
    var loc = opts.location || global.location;
    var fetchImpl = opts.fetch || global.fetch;
    var btn = opts.button || null;
    var key = ref.simulation_id != null ? 'simulation_id'
      : (ref.composite_run_id != null ? 'composite_run_id' : 'compose_id');
    var id = ref[key];
    var label = (key === 'simulation_id' ? 'simulation ' : 'run ') + id;
    var traceUrl = _bp() + '/api/remote-run-trace?' + key + '=' + encodeURIComponent(id);

    // The button is only visible once support resolved, so the value is normally
    // here already and the window opens synchronously inside the click (popup
    // blockers allow that); otherwise wait for it and hope activation lasts.
    if (_supportValue) return Promise.resolve(_go(_supportValue));
    return support(fetchImpl).then(_go);

    function _go(s) {
      var url = viewerUrl(s && s.viewer, loc);
      var win = url ? (opts.open || global.open)(url, '_blank') : null;
      var bundled = !!(s && s.viewer && s.viewer.mode === 'bundled');
      var orig = btn ? btn.textContent : '';
      if (btn) {
        btn.disabled = true; btn.textContent = '⏳ Trace';
        btn.setAttribute('aria-busy', 'true');
      }
      function restore() {
        if (btn) { btn.disabled = false; btn.textContent = orig; btn.removeAttribute('aria-busy'); }
      }
      var viewerNote = bundled ? ' (the first open downloads the viewer, ~30 MB)' : '';
      _status(opts, doc, win
        ? 'Loading the trace for ' + label + ' in Perfetto…' + viewerNote
        : 'Fetching the trace for ' + label + '…', 'loading');
      return fetchImpl(traceUrl).then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (b) {
            throw new Error((b && b.error) || ('HTTP ' + r.status));
          });
        }
        var fileName = 'trace-' + String(id) + '.json';
        var get = function (h) { return (r.headers && r.headers.get) ? r.headers.get(h) : null; };
        var cd = get('Content-Disposition');
        var m = cd && /filename="([^"]+)"/.exec(cd);
        if (m) fileName = m[1];
        var header = get('X-Trace-Events');
        return r.arrayBuffer().then(function (buf) {
          return { buf: buf, fileName: fileName, events: traceEventCount(header, buf) };
        });
      }).then(function (t) {
        if (t.events === 0) {
          // Nothing to draw: Perfetto would open on an empty workspace, which reads as
          // "the viewer is broken". Close the popup we had to open up front and say why.
          if (win && !win.closed) win.close();
          restore();
          _status(opts, doc, capitalize(label) + ' recorded no trace events — runs from ' +
            'before event tracing, or run without event sinks, have none.', 'warn');
          return 'empty';
        }
        if (!win) {
          restore();
          _download(t.buf, t.fileName, doc);
          _status(opts, doc, url ? 'Popup blocked — downloaded the trace (' + t.fileName + ') instead.'
            : 'Downloaded the trace (' + t.fileName + ').', url ? 'warn' : 'ok');
          return 'downloaded';
        }
        _status(opts, doc, 'Trace for ' + label + ' fetched (' + _mb(t.buf.byteLength) +
          '); waiting for Perfetto to load…' + viewerNote, 'loading');
        var abs = ((loc && loc.origin) || '') + traceUrl;
        return postTrace(win, url, t.buf, {
          title: 'Workbench — ' + label, fileName: t.fileName, url: abs,
        }, opts.env).then(function (ok) {
          restore();
          if (ok) {
            _status(opts, doc, 'Opened the trace for ' + label + ' in Perfetto.', 'ok');
            return 'opened';
          }
          if (win.closed) {
            _status(opts, doc, 'The Perfetto window was closed before the trace for ' + label +
              ' loaded.', 'warn');
            return 'closed';
          }
          _status(opts, doc, 'Perfetto did not respond — is ' + url + ' reachable from this browser?',
            'error');
          return 'timeout';
        });
      }).catch(function (err) {
        restore();
        if (win && !win.closed) win.close();
        _status(opts, doc, 'Could not load the trace for ' + label + ': ' +
          (err && err.message || err), 'error');
        return 'error';
      });
    }
  }

  function capitalize(x) { return x.charAt(0).toUpperCase() + x.slice(1); }

  // Delegated click: any `.trace-remote-btn` inside a row carrying the remote
  // simulation id (sim-table.js renders the button, rows carry the id).
  function _onClick(e) {
    var btn = e.target && e.target.closest && e.target.closest('.trace-remote-btn');
    if (!btn) return;
    e.stopPropagation();
    e.preventDefault();
    var ref = {};
    var host = btn.closest('[data-remote-sim-id],[data-composite-run-id]');
    var sim = btn.getAttribute('data-remote-sim-id') || (host && host.getAttribute('data-remote-sim-id'));
    var comp = btn.getAttribute('data-composite-run-id') || (host && host.getAttribute('data-composite-run-id'));
    if (sim) ref.simulation_id = sim; else if (comp) ref.composite_run_id = comp; else return;
    openTrace(ref, { button: btn });
  }

  var api = {
    support: support, viewerUrl: viewerUrl, postTrace: postTrace, openTrace: openTrace,
    traceEventCount: traceEventCount,
    _reset: function () { _support = null; _supportValue = null; },
  };
  global.VivaPerfetto = api;

  if (typeof document !== 'undefined' && document.addEventListener && !document._vivaTraceWired) {
    document._vivaTraceWired = true;
    _installHideStyle(document);
    document.addEventListener('click', _onClick, true);
    support().then(function (s) { if (s && s.supported) _revealActions(document); });
  }
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
})(typeof window !== 'undefined' ? window : globalThis);
