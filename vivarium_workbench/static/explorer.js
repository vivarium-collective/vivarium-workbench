/* Analyses Data Explorer — native marimo-style reactive panel.
   Views: Timeseries · Scatter · Allocation (voronoi) · Flux (escher). */
(function () {
  "use strict";
  var API = "/api/explorer";
  var state = { basePath: "", run: null, runs: [], observables: {}, view: "timeseries", el: null };

  function api(path) { return state.basePath + API + path; }

  // Snapshot mode (published read-only dashboard): the live /api/explorer/*
  // endpoints don't exist, so rewrite each request to the static JSON file that
  // publish.py pre-rendered. Filenames sanitize every non-alphanumeric run/old to
  // "-" (must match publish.py's _snap()). Returns a list of file URLs to fetch
  // (series fans out to one file per requested path), or null if unsupported.
  function snap(s) { return String(s == null ? "" : s).replace(/[^A-Za-z0-9]+/g, "-"); }
  function nearestSnapStep(run, step) {
    var arr = state.snapSteps && state.snapSteps[run];
    if (!arr || !arr.length) return step;
    var t = parseInt(step, 10) || 0, best = arr[0];
    for (var i = 1; i < arr.length; i++)
      if (Math.abs(arr[i] - t) < Math.abs(best - t)) best = arr[i];
    return best;
  }
  function snapUrls(url) {
    var tail = url.split("/api/explorer/")[1];
    if (!tail) return null;
    var qi = tail.indexOf("?");
    var ep = qi < 0 ? tail : tail.slice(0, qi);
    var p = {};
    (qi < 0 ? "" : tail.slice(qi + 1)).split("&").forEach(function (kv) {
      var i = kv.indexOf("="); if (i > 0) p[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1));
    });
    var b = state.basePath + "/api/explorer/", run = snap(p.run);
    // per-step views are snapshotted at only a few row indices — snap the
    // requested step to the nearest available one (state.snapSteps[run]).
    var step = snap(nearestSnapStep(p.run, p.step || "0"));
    if (ep === "runs") return [b + "runs.json"];
    if (ep === "observables") return [b + "observables/" + run + ".json"];
    if (ep === "flux") return [b + "flux/" + run + "/" + step + ".json"];
    if (ep === "base-fluxes") return [b + "base-fluxes/" + run + "/" + step + ".json"];
    if (ep === "validation") return [b + "validation/" + run + "/" + snap(p.dataset || "schmidt") + ".json"];
    if (ep === "vector") return [b + "vector/" + run + "/" + snap(p.path) + "/" + step + ".json"];
    if (ep === "protein-breakdown")
      return [b + "protein-breakdown/" + run + "/" + snap(p.path) + "/" + step + ".json"];
    if (ep === "series")
      return (p.paths || "").split(",").map(function (pp) {
        return b + "series/" + run + "/" + snap(pp) + ".json";
      });
    return null;
  }
  function getJSON(u) {
    return fetch(u).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }
  function j(url) {
    if (!state.snapshot || url.indexOf("/api/explorer/") < 0) {
      return fetch(url).then(function (r) { return r.json(); });
    }
    var urls = snapUrls(url);
    if (!urls) return Promise.resolve({});
    if (urls.length === 1) return getJSON(urls[0]).then(function (d) { return d || {}; });
    // series fan-out: fetch each per-path file, merge into {time, series}
    return Promise.all(urls.map(getJSON)).then(function (parts) {
      var time = [], series = {};
      parts.forEach(function (d) {
        if (!d || !d.series) return;
        if (!time.length && d.time) time = d.time;
        Object.keys(d.series).forEach(function (k) { series[k] = d.series[k]; });
      });
      return { time: time, series: series };
    });
  }

  function mount(el, opts) {
    opts = opts || {};
    state.el = el; state.basePath = opts.basePath || "";
    state.standalone = !!opts.standalone;  // full-window page hides the pop-out link
    // Snapshot/read-only mode: j() rewrites /api/explorer/* to static JSON files
    // that publish.py pre-rendered. If those files are absent (older bundle), the
    // runs fetch resolves empty and we renderEmpty() gracefully.
    state.snapshot = !!opts.snapshot;
    el.innerHTML = '<div class="explorer-loading">Loading runs…</div>';
    j(api("/runs")).then(function (d) {
      state.runs = (d && d.runs) || [];
      // snapshot bundles carry the snapshotted row indices per run; the step
      // sliders snap to these (see nearestSnapStep).
      state.snapSteps = {};
      state.runs.forEach(function (r) { if (r.snap_steps) state.snapSteps[r.run_id] = r.snap_steps; });
      var want = opts.initialRun;
      var run = want && state.runs.find(function (r) {
        return String(r.run_id) === String(want);
      });
      // Per-run launch (e.g. a study's "View" button): open an explicit store
      // path even if the picker didn't discover it — synthesize + prepend it so
      // the explorer acts as a single-run viewer for that run.
      if (!run && opts.initialDb) {
        run = { run_id: want || opts.initialDb,
                label: opts.initialRunLabel || want || "run",
                db_path: opts.initialDb, source: "parquet", n_steps: 0 };
        state.runs = [run].concat(state.runs);
      }
      run = run || state.runs[0];
      if (!run) { renderEmpty(); return; }
      state.run = run;
      loadObservables().then(renderShell);
    }).catch(function () { renderEmpty(); });
  }

  // Full-window pop-out URL for the current run (mirrors the parsimony viewer's
  // "Open ↗"). The standalone page (assets/explorer.html) re-mounts this same
  // controller full-bleed, pre-selecting the run.
  function popoutHref() {
    return state.basePath + "/assets/explorer.html?run=" +
      encodeURIComponent(state.run.run_id || "");
  }

  function renderEmpty() {
    state.el.innerHTML =
      '<p class="muted">Interactive exploration is available in the local dashboard ' +
      '(no simulation runs found here).</p>';
  }

  function loadObservables() {
    var u = api("/observables?db=" + encodeURIComponent(state.run.db_path) +
                "&run=" + encodeURIComponent(state.run.run_id || ""));
    return j(u).then(function (d) { state.observables = (d && d.categories) || {}; });
  }

  // Static biological reference assets (pathway presets + id<->name label maps),
  // fetched once and cached. Built by scripts/build_explorer_bio_assets.py and
  // served from /assets/explorer/. Missing assets degrade to empty maps.
  var auxCache = null;
  function loadAux() {
    if (auxCache) return Promise.resolve(auxCache);
    var base = state.basePath + "/assets/explorer/";
    function grab(f) {
      return fetch(base + f).then(function (r) { return r.ok ? r.json() : {}; })
                            .catch(function () { return {}; });
    }
    return Promise.all([grab("pathways.json"), grab("explorer_labels.json")])
      .then(function (res) {
        auxCache = { pathways: res[0] || {}, labels: res[1] || {} };
        return auxCache;
      });
  }

  function renderShell() {
    var runOpts = state.runs.map(function (r) {
      return '<option value="' + r.run_id + '">' + (r.label || r.run_id) + '</option>';
    }).join("");
    var tabs = ["timeseries", "scatter", "allocation", "flux", "pathways", "validation"].map(function (v) {
      return '<button class="exp-tab' + (v === state.view ? " active" : "") +
             '" data-view="' + v + '">' + v + "</button>";
    }).join("");
    var popout = state.standalone ? "" :
      '<a class="exp-popout" id="exp-popout" target="_blank" rel="noopener" ' +
      'href="' + popoutHref() + '" title="Open full-window in a new tab">Open &#8599;</a>';
    state.el.innerHTML =
      '<div class="explorer">' +
        '<div class="exp-topbar">' +
          '<label class="exp-runsel">Run <select id="exp-run">' + runOpts + "</select></label>" +
          '<div class="exp-tabs">' + tabs + "</div>" +
          popout +
        "</div>" +
        '<div class="exp-body">' +
          '<div id="exp-view-controls" class="exp-rail"></div>' +
          '<div id="exp-view" class="exp-view"></div>' +
        "</div>" +
      "</div>";
    state.el.querySelector("#exp-run").value = state.run.run_id;
    state.el.querySelector("#exp-run").addEventListener("change", function (e) {
      state.run = state.runs.find(function (r) { return r.run_id === e.target.value; });
      var po = state.el.querySelector("#exp-popout");
      if (po) po.href = popoutHref();
      loadObservables().then(renderView);
    });
    state.el.querySelectorAll(".exp-tab").forEach(function (b) {
      b.addEventListener("click", function () {
        state.view = b.getAttribute("data-view");
        state.el.querySelectorAll(".exp-tab").forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        renderView();
      });
    });
    renderView();
  }

  function renderView() {
    var host = state.el.querySelector("#exp-view");
    var ctrls = state.el.querySelector("#exp-view-controls");
    host.innerHTML = ""; ctrls.innerHTML = "";
    if (state.view === "timeseries") Views.timeseries(host, ctrls);
    else if (state.view === "scatter") Views.scatter(host, ctrls);
    else if (state.view === "allocation") Views.allocation(host, ctrls);
    else if (state.view === "flux") Views.flux(host, ctrls);
    else if (state.view === "pathways") Views.pathways(host, ctrls);
    else if (state.view === "validation") Views.validation(host, ctrls);
  }

  function observableOptions() {
    var opts = [];
    Object.keys(state.observables).forEach(function (cat) {
      state.observables[cat].forEach(function (o) {
        var key = o.path + (o.index != null ? "#" + o.index : "");
        // include `path`/`index`: the timeseries & allocation views expand
        // vector observables via o.path (fetchIds, massPaths) — without it they
        // see undefined and silently render no Protein/RNA/Flux/mass data.
        opts.push({ key: key, path: o.path, index: o.index,
                    label: cat + " · " + o.label, kind: o.kind,
                    len: o.length, unit: o.unit || "", mclass: o.mclass || "Other" });
      });
    });
    return opts;
  }

  // Stubs — real implementations loaded by explorer-views.js.
  var Views = {
    timeseries: function (h) { h.textContent = "timeseries (loading…)"; },
    scatter: function (h) { h.textContent = "scatter (loading…)"; },
    allocation: function (h) { h.textContent = "allocation (loading…)"; },
    flux: function (h) { h.textContent = "flux (loading…)"; },
    pathways: function (h) { h.textContent = "pathways (loading…)"; },
    validation: function (h) { h.textContent = "validation (loading…)"; }
  };

  window.Explorer = { mount: mount, _state: state, _api: api, _j: j,
                      _obsOptions: observableOptions, _aux: loadAux, _Views: Views };
})();
