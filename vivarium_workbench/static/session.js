// session.js — per-tab session identity (session-per-tab slice 2).
//
// docs/session-binding.md §3: each browser TAB is its own session. Identity is a
// token this module keeps in `sessionStorage` (per-tab, unlike a cookie which is
// per-browser) and sends as the `X-VW-Session` request header on every same-origin
// request. The server (slice 1) prefers that header over the cookie.
//
// This module installs a `window.fetch` override so EVERY existing call site in
// the app (client.js, walkthrough.js, source-switch.js, …) carries the header
// with no per-call changes. It also captures a server-minted id echoed back in the
// `X-VW-Session` response header (the slice-1 fallback for a first request that
// went out before we had an id).
//
// Identity is CLIENT-MINTED on first load if none is stored. Reason: a fresh tab
// fires several fetches concurrently on load; if each went out header-less, the
// server would mint a DIFFERENT id per request and they would race for
// sessionStorage. Minting one id synchronously here — before any request — makes
// every request on the page carry the same id, race-free. (The server-mint echo
// stays as the no-JS fallback.)
//
// Loaded as the FIRST script in <head> (a classic blocking <script src>) so the
// override is in place before any other script or inline fetch runs.
//
// No workspace bootstrap here: reacting to a `?workspace=` spawn param (force a
// fresh session, bind, strip the param) is slice 3 — it is coupled to the
// param-value contract (a catalog id, not a raw path) and to the UX that mints
// those URLs. This slice is only the identity + fetch plumbing.
(function () {
  "use strict";

  var KEY = "viv-session-id";
  var HEADER = "X-VW-Session";

  function _store() {
    try { return window.sessionStorage; } catch (e) { return null; }
  }
  function getId() {
    var s = _store();
    try { return s ? s.getItem(KEY) : null; } catch (e) { return null; }
  }
  function setId(id) {
    var s = _store();
    try { if (s && id) s.setItem(KEY, id); } catch (e) { /* private mode */ }
  }
  function clearId() {
    var s = _store();
    try { if (s) s.removeItem(KEY); } catch (e) { /* private mode */ }
  }
  function mintId() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
    } catch (e) { /* fall through */ }
    return "vwb-" + Math.random().toString(36).slice(2) +
           "-" + Date.now().toString(36);
  }
  // The tab's id, minting + persisting one on first use so every request on this
  // page shares it (race-free).
  function ensureId() {
    var id = getId();
    if (!id) { id = mintId(); setId(id); }
    return id;
  }

  function _sameOrigin(url) {
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (e) {
      return true; // a relative URL that URL() can't parse is same-origin anyway
    }
  }

  // Return the URL of a fetch() `input` (string or Request), for the origin check.
  function _urlOf(input) {
    if (typeof input === "string") return input;
    if (input && typeof input.url === "string") return input.url;
    return "";
  }

  function installFetch(target) {
    target = target || window;
    if (!target.fetch || target.__vivFetchPatched) return;
    var orig = target.fetch.bind(target);
    target.fetch = function (input, init) {
      var out = init || {};
      if (_sameOrigin(_urlOf(input))) {
        var id = ensureId();
        // Merge onto any caller-supplied headers without clobbering them; only
        // set X-VW-Session if the caller hasn't set it explicitly.
        var srcHeaders = (init && init.headers) ||
          (typeof input !== "string" && input && input.headers) || undefined;
        var h = new Headers(srcHeaders);
        if (!h.has(HEADER)) h.set(HEADER, id);
        out = Object.assign({}, init, { headers: h });
      }
      return orig(input, out).then(function (resp) {
        try {
          var minted = resp && resp.headers && resp.headers.get(HEADER);
          if (minted && minted !== getId()) setId(minted);
        } catch (e) { /* opaque/cors response — no header access */ }
        return resp;
      });
    };
    target.__vivFetchPatched = true;
  }

  var api = {
    HEADER: HEADER,
    STORAGE_KEY: KEY,
    getId: getId,
    setId: setId,
    clearId: clearId,
    mintId: mintId,
    ensureId: ensureId,
    installFetch: installFetch,
    _sameOrigin: _sameOrigin,
  };

  // Browser: install immediately + expose on window. Node (tests): export the
  // factory bits without touching a real window.
  if (typeof window !== "undefined") {
    installFetch(window);
    window.vivSession = api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
    module.exports.installFetch = installFetch;
  }
})();
