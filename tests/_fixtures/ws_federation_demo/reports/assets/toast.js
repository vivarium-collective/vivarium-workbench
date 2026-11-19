// toast.js — _showToast(message, opts): a non-blocking replacement for alert().
//
// Many call sites already do `typeof _showToast === 'function' ? _showToast(msg) : alert(msg)`,
// but the helper was never defined on main (it shipped in #1052 alongside _confirmModal; #1136
// superseded the confirm half and the toast was lost), so every one of them fell through to
// alert(). alert() freezes the page's event loop until a human dismisses it, which also hangs any
// browser automation driving the page. A toast is plain DOM: it shows the same message and stays
// out of the way.
//
// opts: { danger: bool, durationMs: number }
//   - A neutral toast dismisses itself after durationMs (default 4500), or on click.
//   - A danger toast (red, announced immediately) stays until clicked: it replaces an alert() the
//     user had to acknowledge, so an error must not vanish before it has been read.
//   - durationMs: 0 keeps any toast until clicked.
(function (root) {
  'use strict';

  var DEFAULT_MS = 4500;
  var _host = null;

  function _hostEl(doc) {
    if (_host && _host.parentNode) return _host;
    _host = doc.createElement('div');
    _host.className = 'ui-toast-host';
    _host.setAttribute('role', 'status');          // polite live region for neutral toasts
    _host.setAttribute('aria-live', 'polite');
    // Same corner as session-status.js's "Preparing workspace" box (z-index 2147483000): stay above
    // it so a message raised while a workspace is being prepared is not hidden underneath.
    _host.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483001;'
      + 'display:flex;flex-direction:column;gap:8px;max-width:420px;';
    (doc.body || doc.documentElement).appendChild(_host);
    return _host;
  }

  function _remove(el) {
    if (el.parentNode) el.parentNode.removeChild(el);
  }

  function showToast(message, opts) {
    opts = opts || {};
    var doc = root.document;
    var danger = !!opts.danger;
    var el = doc.createElement('div');
    el.className = 'ui-toast' + (danger ? ' ui-toast-danger' : '');
    if (danger) el.setAttribute('role', 'alert');  // announced at once, not queued behind polite updates
    el.style.cssText = 'background:' + (danger ? '#dc2626' : '#1f2937') + ';color:#fff;'
      + 'padding:10px 14px;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,0.25);'
      + 'font:13px/1.4 system-ui,-apple-system,sans-serif;white-space:pre-wrap;cursor:pointer;';
    el.textContent = String(message == null ? '' : message);   // text, never markup
    el.title = 'Click to dismiss';
    el.onclick = function () { _remove(el); };
    _hostEl(doc).appendChild(el);
    var ms = typeof opts.durationMs === 'number' ? opts.durationMs : (danger ? 0 : DEFAULT_MS);
    if (ms > 0) setTimeout(function () { _remove(el); }, ms);
    return el;
  }

  // Create the live region now: screen readers announce changes inside a region that already exists,
  // so the first toast on a page would otherwise go unread. (Scripts load at the end of <body>.)
  if (root.document && root.document.body) _hostEl(root.document);

  root._showToast = showToast;
  if (typeof module !== 'undefined' && module.exports) module.exports = { showToast: showToast };
})(typeof window !== 'undefined' ? window : globalThis);
