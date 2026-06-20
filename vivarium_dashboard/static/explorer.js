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

  // Filled in by later tasks.
  var Views = {
    timeseries: function (host, ctrls) {
      var opts = observableOptions();
      ctrls.innerHTML =
        '<label>Observables <select id="ts-obs" multiple size="6">' +
        opts.map(function (o) { return '<option value="' + o.key + '">' + o.label + "</option>"; }).join("") +
        "</select></label>" +
        '<label><input type="checkbox" id="ts-log"> log y</label>' +
        '<label><input type="checkbox" id="ts-norm"> normalize</label>';
      host.innerHTML = '<div id="ts-chart" style="height:460px"></div>';

      function draw() {
        var chosen = Array.prototype.map.call(
          ctrls.querySelectorAll("#ts-obs option:checked"), function (o) { return o.value; });
        if (!chosen.length) { Plotly.purge("ts-chart"); return; }
        var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                    "&run=" + encodeURIComponent(state.run.run_id || "") +
                    "&paths=" + encodeURIComponent(chosen.join(",")));
        j(u).then(function (d) {
          var norm = ctrls.querySelector("#ts-norm").checked;
          var traces = Object.keys(d.series).map(function (k) {
            var y = d.series[k];
            if (norm) { var m = Math.max.apply(null, y.map(Math.abs)) || 1; y = y.map(function (v) { return v / m; }); }
            return { type: "scatter", mode: "lines", name: k, x: d.time, y: y };
          });
          Plotly.react("ts-chart", traces, {
            margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
            font: { color: "#cfd6df" },
            yaxis: { type: ctrls.querySelector("#ts-log").checked ? "log" : "linear" }
          }, { responsive: true });
        });
      }
      ctrls.querySelector("#ts-obs").addEventListener("change", draw);
      ctrls.querySelector("#ts-log").addEventListener("change", draw);
      ctrls.querySelector("#ts-norm").addEventListener("change", draw);
    },
    scatter: function (host, ctrls) {
      var opts = observableOptions();
      function sel(id, label) {
        return '<label>' + label + ' <select id="' + id + '">' +
          opts.map(function (o) { return '<option value="' + o.key + '">' + o.label + "</option>"; }).join("") +
          "</select></label>";
      }
      ctrls.innerHTML = sel("sc-x", "X") + sel("sc-y", "Y") +
        '<label><input type="checkbox" id="sc-time" checked> color by time</label>';
      host.innerHTML = '<div id="sc-chart" style="height:460px"></div>';

      function draw() {
        var x = ctrls.querySelector("#sc-x").value, y = ctrls.querySelector("#sc-y").value;
        if (!x || !y) return;
        var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                    "&run=" + encodeURIComponent(state.run.run_id || "") +
                    "&paths=" + encodeURIComponent([x, y].join(",")));
        j(u).then(function (d) {
          var trace = {
            type: "scatter", mode: "markers", x: d.series[x], y: d.series[y],
            marker: ctrls.querySelector("#sc-time").checked
              ? { color: d.time, colorscale: "Viridis", showscale: true, size: 6 }
              : { size: 6 }
          };
          Plotly.react("sc-chart", [trace], {
            margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
            font: { color: "#cfd6df" }, xaxis: { title: x }, yaxis: { title: y }
          }, { responsive: true });
        });
      }
      ["sc-x", "sc-y", "sc-time"].forEach(function (id) {
        ctrls.querySelector("#" + id).addEventListener("change", draw);
      });
      draw();
    },
    allocation: function (h) { h.textContent = "allocation (todo)"; },
    flux: function (h) { h.textContent = "flux (todo)"; }
  };

  window.Explorer = { mount: mount, _state: state, _api: api, _j: j, _Views: Views };
})();
