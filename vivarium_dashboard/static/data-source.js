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

  // Return the configured base path (e.g. "/v2ecoli/dashboard") or "".
  // Used in snapshot mode to prefix /api/*.json URLs so the bundle works
  // when hosted at a URL subpath rather than the domain root.
  function _base() {
    return cfg().basePath || "";
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
      ? _base() + "/api/study/" + encodeURIComponent(slug) + ".json"
      : "/api/study/" + encodeURIComponent(slug);
  }

  function _studyChartsUrl(slug) {
    return cfg().mode === "snapshot"
      ? _base() + "/api/study-charts/" + encodeURIComponent(slug) + ".json"
      : "/api/study-charts/" + encodeURIComponent(slug);
  }

  function _isetUrl(id) {
    return cfg().mode === "snapshot"
      ? _base() + "/api/iset/" + encodeURIComponent(id) + ".json"
      : "/api/iset/" + encodeURIComponent(id);
  }

  function _workspaceUrl() {
    return cfg().mode === "snapshot"
      ? _base() + "/api/workspace.json"
      : "/api/workspace";
  }

  function _isetListUrl() {
    return cfg().mode === "snapshot"
      ? _base() + "/api/iset-list.json"
      : "/api/iset-list";
  }

  function _inputsUrl(slug) {
    if (!slug) {
      // No investigation context → global/shared inputs.
      return cfg().mode === "snapshot"
        ? _base() + "/api/inputs/_global.json"
        : "/api/inputs";
    }
    return cfg().mode === "snapshot"
      ? _base() + "/api/inputs/" + encodeURIComponent(slug) + ".json"
      : "/api/inputs?investigation=" + encodeURIComponent(slug);
  }

  function _dataSourcesUrl() {
    return cfg().mode === "snapshot"
      ? _base() + "/api/data-sources.json"
      : "/api/data-sources";
  }

  function _investigationsUrl() {
    return cfg().mode === "snapshot"
      ? _base() + "/api/investigations.json"
      : "/api/investigations";
  }

  function _catalogUrl() {
    return cfg().mode === "snapshot"
      ? _base() + "/api/catalog.json"
      : "/api/catalog";
  }

  function _compositesUrl() {
    return cfg().mode === "snapshot"
      ? _base() + "/api/composites.json"
      : "/api/composites";
  }

  function _registryUrl(refresh) {
    if (cfg().mode === "snapshot") return _base() + "/api/registry.json";
    return "/api/registry" + (refresh ? "?refresh=1" : "");
  }

  function _compositeResolveUrl(id) {
    return cfg().mode === "snapshot"
      ? _base() + "/api/composite-state/" + encodeURIComponent(id) + ".json"
      : "/api/composite-resolve?id=" + encodeURIComponent(id);
  }

  function _simulationsUrl() {
    return cfg().mode === "snapshot"
      ? _base() + "/api/simulations.json"
      : "/api/simulations";
  }

  function _visualizationClassesUrl() {
    return cfg().mode === "snapshot"
      ? _base() + "/api/visualization-classes.json"
      : "/api/visualization-classes";
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
     * Load the study's Visualizations-tab charts payload.
     * Local mode:    fetches GET /api/study-charts/<slug> (live + static)
     * Snapshot mode: fetches /api/study-charts/<slug>.json (static charts
     *                base64-embedded at publish time) from the static bundle.
     * @param {string} slug - the study slug.
     */
    async loadStudyCharts(slug) {
      return _get(_studyChartsUrl(slug));
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

    /**
     * Load the investigations summary list.
     * Local mode:    fetches GET /api/iset-list
     * Snapshot mode: fetches /api/iset-list.json from the static bundle
     */
    async loadIsetList() {
      return _get(_isetListUrl());
    },

    /**
     * Load the sources/inputs for a given investigation slug.
     * Local mode:    fetches GET /api/inputs?investigation=<slug>
     * Snapshot mode: fetches /api/inputs/<slug>.json from the static bundle
     * @param {string} slug - the investigation slug.
     */
    async loadInputs(slug) {
      return _get(_inputsUrl(slug || ""));
    },

    /**
     * Load the curated module catalog.
     * Local mode:    fetches GET /api/catalog
     * Snapshot mode: fetches /api/catalog.json from the static bundle
     */
    async loadCatalog() {
      return _get(_catalogUrl());
    },

    /**
     * Load the composite specs.
     * Local mode:    fetches GET /api/composites
     * Snapshot mode: fetches /api/composites.json from the static bundle
     */
    async loadComposites() {
      return _get(_compositesUrl());
    },

    /**
     * Load the discovered process/type registry.
     * Local mode:    fetches GET /api/registry (+ optional ?refresh=1)
     * Snapshot mode: fetches /api/registry.json from the static bundle
     * @param {boolean} [refresh] - when true, bypass server cache (local mode only).
     */
    async loadRegistry(refresh) {
      return _get(_registryUrl(refresh));
    },

    /**
     * Load the repo-wide data-source bundle (workspace.yaml provider hook).
     * Local mode:    fetches GET /api/data-sources
     * Snapshot mode: fetches /api/data-sources.json from the static bundle
     */
    async loadDataSources() {
      return _get(_dataSourcesUrl());
    },

    /**
     * Load the flat per-study investigations list with DAG edges.
     * Used by the studies left-rail and the Studies tab grid.
     * Local mode:    fetches GET /api/investigations
     * Snapshot mode: fetches /api/investigations.json from the static bundle
     */
    async loadInvestigationsFlat() {
      return _get(_investigationsUrl());
    },

    /**
     * Load the pre-resolved composite state for the given id.
     * Local mode:    fetches GET /api/composite-resolve?id=<id>  (no overrides)
     * Snapshot mode: fetches /api/composite-state/<id>.json from the static bundle
     * For overrides in live mode, use the raw fetch with overrides param directly.
     * @param {string} id - the composite spec id.
     */
    async loadCompositeResolve(id) {
      return _get(_compositeResolveUrl(id));
    },

    /**
     * Load the simulations index (all pre-run sims across the workspace).
     * Local mode:    fetches GET /api/simulations
     * Snapshot mode: fetches /api/simulations.json from the static bundle
     */
    async loadSimulations() {
      return _get(_simulationsUrl());
    },

    /**
     * Load the registered visualization/analysis classes.
     * Local mode:    fetches GET /api/visualization-classes
     * Snapshot mode: fetches /api/visualization-classes.json from the static bundle
     */
    async loadVisualizationClasses() {
      return _get(_visualizationClassesUrl());
    },
  };

  global.DataSource = DataSource;
})(window);
