// ui-modal.js — non-blocking replacements for window.confirm()/alert().
//
// Why this exists: confirm()/alert()/prompt() are the only browser APIs that
// freeze the page's own JS event loop until a human dismisses them. That's a
// real problem for anything driving this UI programmatically (browser
// automation, screenshot tools) — the page becomes unresponsive to every
// other script the instant one fires, with no way to inspect or interact
// with it except the native dialog itself. A real in-page DOM modal has the
// exact same "a human must explicitly act before this proceeds" safety
// property (still a genuine click on a real button, still blocks the
// calling code path until answered) without ever blocking the event loop —
// it's just a styled <div>, so screenshots, clicks, and automation all see
// and interact with it normally.
//
// _confirmModal(message, opts) -> Promise<boolean>, resolves true/false the
// same way `confirm()` returns true/false — swap `if (!confirm(msg)) return;`
// for `return _confirmModal(msg).then(function (ok) { if (!ok) return; ... })`.
//
// _showToast(message, opts) — fire-and-forget replacement for `alert()`.
// Several call sites elsewhere already guard on `typeof _showToast ===
// 'function'` expecting exactly this to exist; it never has, so those sites
// have always silently fallen through to alert(). This is the real
// implementation, not a new pattern.
(function () {
  'use strict';

  function _esc(s) {
    return String(s == null ? '' : s);
  }

  function _mount(el) {
    (document.body || document.documentElement).appendChild(el);
  }

  // ─── _confirmModal ────────────────────────────────────────────────────
  function _confirmModal(message, opts) {
    opts = opts || {};
    var okLabel = opts.okLabel || 'OK';
    var cancelLabel = opts.cancelLabel || 'Cancel';
    var danger = !!opts.danger;

    return new Promise(function (resolve) {
      var backdrop = document.createElement('div');
      backdrop.className = 'ui-modal-backdrop';
      backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);'
        + 'display:flex;align-items:center;justify-content:center;z-index:10000;';

      var box = document.createElement('div');
      box.className = 'ui-modal-box';
      box.style.cssText = 'background:#fff;color:#111;border-radius:8px;padding:20px 24px;'
        + 'max-width:560px;min-width:280px;max-height:80vh;overflow:auto;'
        + 'box-shadow:0 8px 30px rgba(0,0,0,0.3);font:14px/1.5 system-ui,-apple-system,sans-serif;';

      var msgEl = document.createElement('div');
      msgEl.style.cssText = 'white-space:pre-wrap;margin-bottom:18px;';
      msgEl.textContent = _esc(message); // textContent, never innerHTML — several
      // callers interpolate server-resolved values (simulator id, config
      // filename, commit) into this message; treating it as HTML here would
      // reopen exactly the injection risk plain confirm() never had.
      box.appendChild(msgEl);

      var btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;';

      var cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.textContent = cancelLabel;
      cancelBtn.style.cssText = 'padding:6px 14px;border:1px solid #ccc;border-radius:6px;'
        + 'background:#f7f7f8;cursor:pointer;font:inherit;';

      var okBtn = document.createElement('button');
      okBtn.type = 'button';
      okBtn.textContent = okLabel;
      okBtn.style.cssText = 'padding:6px 14px;border:none;border-radius:6px;color:#fff;'
        + 'cursor:pointer;font:inherit;background:' + (danger ? '#dc2626' : '#2563eb') + ';';

      btnRow.appendChild(cancelBtn);
      btnRow.appendChild(okBtn);
      box.appendChild(btnRow);
      backdrop.appendChild(box);

      function settle(val) {
        document.removeEventListener('keydown', onKey, true);
        if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
        resolve(val);
      }
      function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); settle(false); }
        else if (e.key === 'Enter') {
          // Enter confirms only when focus is already on the OK button —
          // otherwise it cancels. Native confirm() usually defaults focus to
          // OK; defaulting this modal's own focus to Cancel instead (below)
          // makes an accidental Enter press the SAFE outcome, a deliberate
          // improvement over the native dialog for destructive actions.
          e.preventDefault();
          settle(document.activeElement === okBtn);
        }
      }
      cancelBtn.onclick = function () { settle(false); };
      okBtn.onclick = function () { settle(true); };
      backdrop.onclick = function (e) { if (e.target === backdrop) settle(false); };
      document.addEventListener('keydown', onKey, true);

      _mount(backdrop);
      cancelBtn.focus();
    });
  }

  // ─── _showToast ───────────────────────────────────────────────────────
  var _toastHost = null;
  function _toastHostEl() {
    if (_toastHost && _toastHost.parentNode) return _toastHost;
    _toastHost = document.createElement('div');
    _toastHost.className = 'ui-toast-host';
    _toastHost.style.cssText = 'position:fixed;top:16px;right:16px;z-index:10001;'
      + 'display:flex;flex-direction:column;gap:8px;max-width:420px;';
    _mount(_toastHost);
    return _toastHost;
  }

  function _showToast(message, opts) {
    opts = opts || {};
    var danger = !!opts.danger;
    var ms = opts.durationMs || 4500;

    var el = document.createElement('div');
    el.className = 'ui-toast';
    el.style.cssText = 'background:' + (danger ? '#dc2626' : '#1f2937') + ';color:#fff;'
      + 'padding:10px 14px;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,0.25);'
      + 'font:13px/1.4 system-ui,-apple-system,sans-serif;white-space:pre-wrap;'
      + 'cursor:pointer;';
    el.textContent = _esc(message); // textContent, same injection-safety reasoning as above.
    el.title = 'Click to dismiss';
    el.onclick = function () { if (el.parentNode) el.parentNode.removeChild(el); };

    _toastHostEl().appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, ms);
  }

  window._confirmModal = _confirmModal;
  window._showToast = _showToast;
})();
