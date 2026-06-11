// data-source.js — DataSource layer for the vivarium-dashboard client-fetch seam.
//
// Sub-project #1 (client-fetch seam): the frontend reads narrative data through
// this module instead of consuming Jinja-embedded window._study / window._iset
// objects.  Local mode (default) fetches from the same-origin /api/* endpoints.
// Later sub-projects plug in SnapshotSource / SmsApiResultsSource by setting
// window.__DASH_CONFIG__ before this script loads.
//
// Usage:
//   window.__DASH_CONFIG__ = { mode: "local-server" };   // (default)
//   await window.DataSource.loadStudy("my-study");        // → study-detail spec
//   await window.DataSource.loadInvestigation("my-iset"); // → iset + studies
//   await window.DataSource.loadWorkspace();               // → workspace home data
//
// See docs/superpowers/specs/2026-06-10-read-only-online-dashboard-design.md §7.
(function (global) {
  "use strict";

  function cfg() {
    return global.__DASH_CONFIG__ || { mode: "local-server" };
  }

  async function _get(url) {
    var r = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!r.ok) throw new Error("fetch " + url + " -> " + r.status);
    return r.json();
  }

  var DataSource = {
    /** Return the current source config (default: local-server). */
    config: cfg,

    /**
     * Load the study-detail spec for the given slug.
     * Local mode: fetches GET /api/study/<slug> (returns _study_detail_spec shape).
     * @param {string} slug - the study slug (e.g. "my-study").
     */
    async loadStudy(slug) {
      return _get("/api/study/" + encodeURIComponent(slug));
    },

    /**
     * Load the investigation (iset) detail for the given id.
     * Local mode: fetches GET /api/iset/<id> (returns _get_iset_detail shape).
     * @param {string} id - the investigation id / slug.
     */
    async loadInvestigation(id) {
      return _get("/api/iset/" + encodeURIComponent(id));
    },

    /**
     * Load the workspace home data.
     * Local mode: fetches GET /api/workspace (follow-on; route tracked at end of plan).
     */
    async loadWorkspace() {
      return _get("/api/workspace");
    },
  };

  global.DataSource = DataSource;
})(window);
