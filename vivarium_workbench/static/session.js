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

  // Spawn bootstrap (session-per-tab slice 3). Opening a workspace in a new tab is
  // window.open('/?workspace=<catalog-name>') for a LOCAL workspace, or
  // window.open('/?build=<simulator_id>') for an sms-api REMOTE build. When THIS
  // tab loads with either param it must (a) take its OWN session — never the id a
  // sibling tab's sessionStorage was copied into when the browser cloned it — so
  // we force-mint fresh; (b) bind that session to the source (by name → switch, or
  // by simulator_id → switch-build, which materializes the build's workspace);
  // (c) strip the param so a reload is a plain load of this now-bound session;
  // (d) reload once bound, so the server re-renders GET / for the bound workspace
  // (the first paint was the default). A managed/remote bind returns 'materializing'
  // — session-status.js then shows the ⏳ favicon until it settles.
  function bootstrapSpawn() {
    var loc = window.location || {};
    var params;
    try { params = new URLSearchParams(loc.search || ""); } catch (e) { return null; }
    var ws = params.get("workspace");
    var build = params.get("build");
    if (!ws && !build) return null;

    clearId();          // discard any inherited (copied) id
    ensureId();         // mint this tab's own fresh id — race-free, before the bind

    var endpoint, payload, key;
    if (ws) {
      endpoint = "/api/source/switch"; payload = { name: ws }; key = "workspace";
    } else {
      endpoint = "/api/source/switch-build";
      payload = { simulator_id: Number(build) }; key = "build";
    }

    function stripParam() {
      try {
        params.delete(key);
        var qs = params.toString();
        var clean = (loc.pathname || "/") + (qs ? "?" + qs : "") + (loc.hash || "");
        window.history.replaceState(null, "", clean);
      } catch (e) { /* history unavailable — harmless */ }
    }

    return window.fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      stripParam();
      // Reload the (now clean) URL so GET / re-renders for the bound workspace.
      if (r && r.ok && window.location && typeof window.location.reload === "function") {
        window.location.reload();
      }
      return r;
    }).catch(function () {
      // Bind failed — leave the app on its default workspace. A later slice adds
      // spawn-error UI; for now just clean the URL.
      stripParam();
    });
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
    bootstrapSpawn: bootstrapSpawn,
    _sameOrigin: _sameOrigin,
  };

  // Browser: install immediately + expose on window, then run the spawn bootstrap
  // (a no-op unless ?workspace= is present). Node (tests): export the factory bits
  // without touching a real window or auto-binding.
  if (typeof window !== "undefined") {
    installFetch(window);
    window.vivSession = api;
    bootstrapSpawn();
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
    module.exports.installFetch = installFetch;
  }
})();
