// confirm-modal.js — window._confirmModal(message, opts) -> Promise<bool>, and
// window._alertModal(message, opts) -> Promise<true>. Non-blocking replacements
// for native confirm()/alert().
//
// Native confirm()/alert() freeze the page's event loop until a human dismisses
// them, which also hangs any browser automation driving the page (#1051). This
// is plain DOM: the same prompt, promise-based so callers `.then(ok => …)`.
// study-detail.js already shipped a module-local version of this; lifting it to
// a shared global lets walkthrough.js (and anything else loaded on the page) use
// it too, instead of falling back to native confirm().
//
// opts: { okLabel, cancelLabel, danger }  (danger reserved for styling parity).
(function (root) {
  'use strict';

  function _modal(message, opts) {
    opts = opts || {};
    var isConfirm = opts.confirm !== false;  // confirm = OK + Cancel; alert = OK only
    return new Promise(function (resolve) {
      var doc = root.document;
      var overlay = doc.createElement('div');
      overlay.className = 'ui-confirm-overlay';
      // Above the toast host (2147483001) so a prompt raised alongside a toast
      // is still reachable.
      overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483002;'
        + 'background:rgba(0,0,0,0.35);display:flex;align-items:center;justify-content:center';
      var box = doc.createElement('div');
      box.setAttribute('role', isConfirm ? 'alertdialog' : 'dialog');
      box.setAttribute('aria-modal', 'true');
      box.style.cssText = 'background:var(--panel-bg,#fff);color:var(--text,#1e293b);'
        + 'border:1px solid var(--border,#e2e8f0);border-radius:6px;'
        + 'box-shadow:0 8px 32px rgba(0,0,0,0.25);padding:16px 20px;max-width:520px;width:90%;'
        + 'font:13px/1.5 system-ui,-apple-system,sans-serif';
      var text = doc.createElement('div');
      // textContent, never innerHTML — messages embed user-typed values and must
      // not be interpreted as markup (native confirm() never did either).
      text.style.cssText = 'white-space:pre-wrap;margin-bottom:14px';
      text.textContent = String(message == null ? '' : message);
      var actions = doc.createElement('div');
      actions.style.cssText = 'display:flex;gap:8px;justify-content:flex-end';
      var cancelBtn = null;
      if (isConfirm) {
        cancelBtn = doc.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn-mini';
        cancelBtn.textContent = opts.cancelLabel || 'Cancel';
        actions.appendChild(cancelBtn);
      }
      var okBtn = doc.createElement('button');
      okBtn.type = 'button';
      okBtn.className = 'btn-mini';
      okBtn.textContent = opts.okLabel || 'OK';
      actions.appendChild(okBtn);
      box.appendChild(text);
      box.appendChild(actions);
      overlay.appendChild(box);

      function done(result) {
        doc.removeEventListener('keydown', onKey);
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        resolve(result);
      }
      // Escape on a confirm = Cancel (false); on an alert there's nothing to
      // cancel, so Escape acknowledges (true), matching native alert().
      function onKey(ev) { if (ev.key === 'Escape') done(!isConfirm); }
      okBtn.addEventListener('click', function () { done(true); });
      if (cancelBtn) cancelBtn.addEventListener('click', function () { done(false); });
      overlay.addEventListener('click', function (ev) { if (ev.target === overlay) done(!isConfirm); });
      doc.addEventListener('keydown', onKey);
      (doc.body || doc.documentElement).appendChild(overlay);
      okBtn.focus();
    });
  }

  root._confirmModal = function (message, opts) {
    var o = {};
    if (opts) { for (var k in opts) { if (Object.prototype.hasOwnProperty.call(opts, k)) o[k] = opts[k]; } }
    o.confirm = true;
    return _modal(message, o);
  };
  root._alertModal = function (message, opts) {
    var o = {};
    if (opts) { for (var k in opts) { if (Object.prototype.hasOwnProperty.call(opts, k)) o[k] = opts[k]; } }
    o.confirm = false;
    return _modal(message, o);
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { confirmModal: root._confirmModal, alertModal: root._alertModal };
  }
})(typeof window !== 'undefined' ? window : globalThis);
