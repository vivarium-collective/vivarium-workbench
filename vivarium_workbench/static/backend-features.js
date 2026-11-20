// backend-features.js — the features the active backends declare (docs/backend-adapters.md §2.1, step P3 F0).
//
// One GET /api/backend/features per page, memoised:
//
//   window.BackendFeatures.ready()   → Promise<{features: string[], known: boolean}>
//   window.BackendFeatures.has(name) → true only once ready() has resolved KNOWN features that include `name`
//   window.BackendFeatures.known()   → whether the features are known (false before ready() resolves, or after a
//                                      failed fetch)
//
// `known: false` means "unknown" (the request failed, then failed again on the one retry), which is NOT "no
// features": a consumer that hides an optional control must keep showing it as today when the features are
// unknown. Features only ever HIDE optional controls; no dispatch or run-target decision may read them (the server
// decides those and refuses what it cannot do).
//
// Snapshot mode (the published read-only bundle, window.__DASH_CONFIG__.mode === "snapshot") has no server, so
// ready() resolves {features: [], known: true} with no request.
(function (global) {
  "use strict";

  var URL_PATH = "/api/backend/features";
  var promise = null;
  var state = { features: [], known: false };

  function isSnapshot() {
    var c = global.__DASH_CONFIG__;
    return !!(c && c.mode === "snapshot");
  }

  function url() {
    var ds = global.DataSource;
    return ds && typeof ds.apiUrl === "function" ? ds.apiUrl(URL_PATH) : URL_PATH;
  }

  function fetchOnce() {
    return global.fetch(url(), { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r || !r.ok) throw new Error("HTTP " + (r && r.status));
        return r.json();
      })
      .then(function (body) {
        if (!body || !Array.isArray(body.features)) throw new Error("malformed features payload");
        return body.features.filter(function (f) { return typeof f === "string"; });
      });
  }

  function settle(features, known) {
    state = { features: features, known: known };
    return { features: features.slice(), known: known };
  }

  function ready() {
    if (promise) return promise;
    if (isSnapshot() || typeof global.fetch !== "function") {
      promise = Promise.resolve(settle([], isSnapshot()));
      return promise;
    }
    promise = fetchOnce()
      .catch(function () { return fetchOnce(); })   // one retry
      .then(function (features) { return settle(features, true); },
            function () { return settle([], false); });
    return promise;
  }

  function has(name) {
    return state.known && state.features.indexOf(name) !== -1;
  }

  function known() {
    return state.known;
  }

  // Tests only: forget the memoised answer.
  function _reset() {
    promise = null;
    state = { features: [], known: false };
  }

  global.BackendFeatures = { ready: ready, has: has, known: known, _reset: _reset };
})(typeof window !== "undefined" ? window : this);
