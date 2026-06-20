/* Analyses Data Explorer — native marimo-style reactive panel.
   Views: Timeseries · Scatter · Allocation (voronoi) · Flux (escher). */
(function () {
  "use strict";
  var API = "/api/explorer";
  var state = { basePath: "", run: null, runs: [], observables: {}, view: "timeseries", el: null };

  function api(path) { return state.basePath + API + path; }
  function j(url) { return fetch(url).then(function (r) { return r.json(); }); }

  function mount(el, opts) {
    state.el = el; state.basePath = (opts && opts.basePath) || "";
    // In snapshot/read-only mode, the backend explorer endpoints don't exist;
    // degrade gracefully rather than 404-ing.
    if (opts && opts.snapshot) { renderEmpty(); return; }
    el.innerHTML = '<div class="explorer-loading">Loading runs…</div>';
    j(api("/runs")).then(function (d) {
      state.runs = (d && d.runs) || [];
      if (!state.runs.length) { renderEmpty(); return; }
      state.run = state.runs[0];
      loadObservables().then(renderShell);
    }).catch(function () { renderEmpty(); });
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
    state.el.innerHTML =
      '<div class="explorer">' +
        '<div class="exp-controls">' +
          '<label>Run <select id="exp-run">' + runOpts + "</select></label>" +
          '<div class="exp-tabs">' + tabs + "</div>" +
          '<div id="exp-view-controls"></div>' +
        "</div>" +
        '<div id="exp-view" class="exp-view"></div>' +
      "</div>";
    state.el.querySelector("#exp-run").value = state.run.run_id;
    state.el.querySelector("#exp-run").addEventListener("change", function (e) {
      state.run = state.runs.find(function (r) { return r.run_id === e.target.value; });
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

  // Filled in by later tasks.
  var Views = {
    timeseries: function (h) { h.textContent = "timeseries (todo)"; },
    scatter: function (h) { h.textContent = "scatter (todo)"; },
    allocation: function (h) { h.textContent = "allocation (todo)"; },
    flux: function (h) { h.textContent = "flux (todo)"; }
  };

  window.Explorer = { mount: mount, _state: state, _api: api, _j: j, _Views: Views };
})();
