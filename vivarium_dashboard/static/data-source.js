// data-source.js — DataSource layer for the vivarium-dashboard client-fetch seam.
//
// Sub-project #1 (client-fetch seam): the frontend reads narrative data through
// this module instead of consuming Jinja-embedded window._study / window._iset
// objects.  Local mode (default) fetches from the same-origin /api/* endpoints.
// Later sub-projects plug in SnapshotSource / SmsApiResultsSource by setting
// window.__DASH_CONFIG__ before this script loads.
//
// Sub-project #2 (narrative export): adds snapshot mode — when __DASH_CONFIG__.mode
// is "snapshot", the URL helpers point at the static JSON files in the bundle
// (api/study/<slug>.json, api/iset/<id>.json, api/workspace.json) instead of
// the live /api/* endpoints.
//
// Usage:
//   window.__DASH_CONFIG__ = { mode: "local-server" };   // (default)
//   window.__DASH_CONFIG__ = { mode: "snapshot" };        // static bundle
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

  // ---------------------------------------------------------------------------
  // URL helpers — snapshot mode appends .json and reads static files;
  // local-server mode uses the live /api/* endpoints.
  // ---------------------------------------------------------------------------

  function _studyUrl(slug) {
    return cfg().mode === "snapshot"
      ? "/api/study/" + encodeURIComponent(slug) + ".json"
      : "/api/study/" + encodeURIComponent(slug);
  }

  function _isetUrl(id) {
    return cfg().mode === "snapshot"
      ? "/api/iset/" + encodeURIComponent(id) + ".json"
      : "/api/iset/" + encodeURIComponent(id);
  }

  function _workspaceUrl() {
    return cfg().mode === "snapshot" ? "/api/workspace.json" : "/api/workspace";
  }

  var DataSource = {
    /** Return the current source config (default: local-server). */
    config: cfg,

    /**
     * Load the study-detail spec for the given slug.
     * Local mode:    fetches GET /api/study/<slug>
     * Snapshot mode: fetches /api/study/<slug>.json from the static bundle
     * @param {string} slug - the study slug (e.g. "my-study").
     */
    async loadStudy(slug) {
      return _get(_studyUrl(slug));
    },

    /**
     * Load the investigation (iset) detail for the given id.
     * Local mode:    fetches GET /api/iset/<id>
     * Snapshot mode: fetches /api/iset/<id>.json from the static bundle
     * @param {string} id - the investigation id / slug.
     */
    async loadInvestigation(id) {
      return _get(_isetUrl(id));
    },

    /**
     * Load the workspace home data.
     * Local mode:    fetches GET /api/workspace
     * Snapshot mode: fetches /api/workspace.json from the static bundle
     */
    async loadWorkspace() {
      return _get(_workspaceUrl());
    },
  };

  global.DataSource = DataSource;
})(window);
