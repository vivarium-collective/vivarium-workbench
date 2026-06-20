/* Analyses Data Explorer — native marimo-style reactive panel.
   Views: Timeseries · Scatter · Allocation (voronoi) · Flux (escher). */
(function () {
  "use strict";
  var API = "/api/explorer";
  var state = { basePath: "", run: null, runs: [], observables: {}, view: "timeseries", el: null };

  function api(path) { return state.basePath + API + path; }
  function j(url) { return fetch(url).then(function (r) { return r.json(); }); }

  function mount(el, opts) {
    opts = opts || {};
    state.el = el; state.basePath = opts.basePath || "";
    state.standalone = !!opts.standalone;  // full-window page hides the pop-out link
    // In snapshot/read-only mode, the backend explorer endpoints don't exist;
    // degrade gracefully rather than 404-ing.
    if (opts.snapshot) { renderEmpty(); return; }
    el.innerHTML = '<div class="explorer-loading">Loading runs…</div>';
    j(api("/runs")).then(function (d) {
      state.runs = (d && d.runs) || [];
      if (!state.runs.length) { renderEmpty(); return; }
      var want = opts.initialRun;
      state.run = (want && state.runs.find(function (r) {
        return String(r.run_id) === String(want);
      })) || state.runs[0];
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

  function renderShell() {
    var runOpts = state.runs.map(function (r) {
      return '<option value="' + r.run_id + '">' + (r.label || r.run_id) + '</option>';
    }).join("");
    var tabs = ["timeseries", "scatter", "allocation", "flux"].map(function (v) {
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
  }

  function observableOptions() {
    var opts = [];
    Object.keys(state.observables).forEach(function (cat) {
      state.observables[cat].forEach(function (o) {
        var key = o.path + (o.index != null ? "#" + o.index : "");
        opts.push({ key: key, label: cat + " · " + o.label, kind: o.kind, len: o.length });
      });
    });
    return opts;
  }

  // Stubs — real implementations loaded by explorer-views.js.
  var Views = {
    timeseries: function (h) { h.textContent = "timeseries (loading…)"; },
    scatter: function (h) { h.textContent = "scatter (loading…)"; },
    allocation: function (h) { h.textContent = "allocation (loading…)"; },
    flux: function (h) { h.textContent = "flux (loading…)"; }
  };

  window.Explorer = { mount: mount, _state: state, _api: api, _j: j,
                      _obsOptions: observableOptions, _Views: Views };
})();
