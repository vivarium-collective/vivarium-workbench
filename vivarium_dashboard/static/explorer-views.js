/* Data Explorer views — split out of explorer.js to keep each file focused.
   Assigns into window.Explorer._Views (the object renderView dispatches through). */
(function () {
  "use strict";
  var E = window.Explorer;
  if (!E) return;
  var api = E._api, j = E._j, state = E._state, observableOptions = E._obsOptions;
  var V = E._Views;

  V.timeseries = function (host, ctrls) {
    var opts = observableOptions();
    var classes = ["All", "RNA", "Protein", "Metabolite", "Flux", "Mass", "Other"];
    ctrls.innerHTML =
      '<label>Class <select id="ts-class">' +
        classes.map(function (c) { return '<option>' + c + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Search <input id="ts-search" type="text" placeholder="filter…"></label>' +
      '<label>Observables <select id="ts-obs" multiple size="10"></select></label>' +
      '<label><input type="checkbox" id="ts-log"> log y</label>' +
      '<label><input type="checkbox" id="ts-norm"> normalize</label>';
    host.innerHTML = '<div id="ts-chart" style="height:520px"></div>';

    function refreshList() {
      var cls = ctrls.querySelector("#ts-class").value;
      var q = ctrls.querySelector("#ts-search").value.toLowerCase();
      var sel = ctrls.querySelector("#ts-obs");
      var chosen = {};
      Array.prototype.forEach.call(sel.selectedOptions, function (o) { chosen[o.value] = 1; });
      sel.innerHTML = opts.filter(function (o) {
        return (cls === "All" || o.mclass === cls) &&
               (!q || o.label.toLowerCase().indexOf(q) >= 0);
      }).map(function (o) {
        return '<option value="' + o.key + '"' + (chosen[o.key] ? " selected" : "") +
               ">" + o.label + " [" + (o.unit || "–") + "]</option>";
      }).join("");
    }

    function draw() {
      var chosen = Array.prototype.map.call(
        ctrls.querySelectorAll("#ts-obs option:checked"), function (o) { return o.value; });
      if (!chosen.length) { Plotly.purge("ts-chart"); return; }
      var unitOf = {};
      opts.forEach(function (o) { unitOf[o.key] = o.unit || ""; });
      var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&paths=" + encodeURIComponent(chosen.join(",")));
      j(u).then(function (d) {
        var norm = ctrls.querySelector("#ts-norm").checked;
        var log = ctrls.querySelector("#ts-log").checked;
        // distinct units → one stacked panel each
        var units = [];
        chosen.forEach(function (k) { var un = unitOf[k] || "(unitless)";
          if (units.indexOf(un) < 0) units.push(un); });
        var n = units.length, traces = [], layout = {
          margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
          font: { color: "#cfd6df" }, showlegend: true,
          grid: { rows: n, columns: 1, pattern: "independent", roworder: "top to bottom" }
        };
        units.forEach(function (un, i) {
          var ax = i === 0 ? "y" : "y" + (i + 1);
          layout[i === 0 ? "yaxis" : "yaxis" + (i + 1)] =
            { title: un, type: log ? "log" : "linear" };
        });
        Object.keys(d.series).forEach(function (k) {
          var un = unitOf[k] || "(unitless)", i = units.indexOf(un);
          var y = d.series[k];
          if (norm) { var m = Math.max.apply(null, y.map(Math.abs)) || 1; y = y.map(function (v) { return v / m; }); }
          traces.push({ type: "scatter", mode: "lines", name: k, x: d.time, y: y,
                        xaxis: "x", yaxis: i === 0 ? "y" : "y" + (i + 1) });
        });
        Plotly.react("ts-chart", traces, layout, { responsive: true });
      });
    }

    ctrls.querySelector("#ts-class").addEventListener("change", refreshList);
    ctrls.querySelector("#ts-search").addEventListener("input", refreshList);
    ctrls.querySelector("#ts-obs").addEventListener("change", draw);
    ctrls.querySelector("#ts-log").addEventListener("change", draw);
    ctrls.querySelector("#ts-norm").addEventListener("change", draw);
    refreshList();
  };

  V.scatter = function (host, ctrls) {
    // class -> the vector observable path that represents it
    var CLASS_PATH = {
      Protein: "listeners.monomer_counts",
      RNA: "listeners.rna_counts.mRNA_counts",
      Flux: "listeners.fba_results.base_reaction_fluxes"
    };
    var classes = Object.keys(CLASS_PATH);
    if (state.runs.length < 2) {
      ctrls.innerHTML = "";
      host.innerHTML = '<p class="muted" style="padding:12px">Run-vs-run scatter ' +
        'needs at least two runs in this workspace.</p>';
      return;
    }
    function runOpts(sel) {
      return state.runs.map(function (r) {
        return '<option value="' + r.run_id + '"' +
          (r.run_id === sel ? " selected" : "") + ">" + (r.label || r.run_id) + "</option>";
      }).join("");
    }
    var a0 = state.runs[0].run_id, b0 = state.runs[1].run_id;
    ctrls.innerHTML =
      '<label>Class <select id="sc-class">' +
        classes.map(function (c) { return "<option>" + c + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Run A (x) <select id="sc-a">' + runOpts(a0) + "</select></label>" +
      '<label>Run B (y) <select id="sc-b">' + runOpts(b0) + "</select></label>" +
      '<label>Step <input id="sc-step" type="range" min="0" max="0" value="0"></label>' +
      '<label><input type="checkbox" id="sc-log" checked> log-log</label>';
    host.innerHTML = '<div id="sc-chart" style="height:520px"></div>';

    function runById(id) { return state.runs.find(function (r) { return r.run_id === id; }); }

    function refreshSlider() {
      var ra = runById(ctrls.querySelector("#sc-a").value);
      var rb = runById(ctrls.querySelector("#sc-b").value);
      var slider = ctrls.querySelector("#sc-step");
      var newMax = Math.max(0, Math.max((ra.n_steps || 1), (rb.n_steps || 1)) - 1);
      slider.max = String(newMax);
      if (parseInt(slider.value, 10) > newMax) slider.value = String(newMax);
    }

    function draw() {
      var cls = ctrls.querySelector("#sc-class").value;
      var path = CLASS_PATH[cls];
      var ra = runById(ctrls.querySelector("#sc-a").value);
      var rb = runById(ctrls.querySelector("#sc-b").value);
      var step = parseInt(ctrls.querySelector("#sc-step").value, 10) || 0;
      function vec(r) {
        return j(api("/vector?db=" + encodeURIComponent(r.db_path) +
                     "&run=" + encodeURIComponent(r.run_id || "") +
                     "&path=" + encodeURIComponent(path) + "&step=" + step));
      }
      Promise.all([vec(ra), vec(rb)]).then(function (res) {
        var A = res[0], B = res[1];
        if (!A.ids || !B.ids || !A.ids.length || !B.ids.length) {
          host.innerHTML = '<p class="muted" style="padding:12px">No data for this class at this step.</p>';
          return;
        }
        // join by id when both provide ids, else by index
        var mapA = {}; A.ids.forEach(function (id, i) { mapA[id] = A.values[i]; });
        var xs = [], ys = [], labels = [];
        B.ids.forEach(function (id, i) {
          if (id in mapA) { xs.push(mapA[id]); ys.push(B.values[i]); labels.push(id); }
        });
        var log = ctrls.querySelector("#sc-log").checked;
        var lo = Infinity, hi = 1;
        xs.concat(ys).forEach(function (v) { if (v > hi) hi = v; if (v > 0 && v < lo) lo = v; });
        if (lo === Infinity) lo = 1e-6;
        var trace = { type: "scattergl", mode: "markers", x: xs, y: ys, text: labels,
          hovertemplate: "%{text}<br>A=%{x:.3g} B=%{y:.3g}<extra></extra>",
          marker: { size: 5, opacity: 0.6, color: "#4c8bf5" } };
        var diag = { type: "scatter", mode: "lines", x: [lo, hi], y: [lo, hi],
          line: { color: "#888", dash: "dot" }, hoverinfo: "skip", showlegend: false };
        Plotly.react("sc-chart", [trace, diag], {
          margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
          font: { color: "#cfd6df" },
          xaxis: { title: (ra.label || ra.run_id) + " (" + cls + ")", type: log ? "log" : "linear" },
          yaxis: { title: (rb.label || rb.run_id), type: log ? "log" : "linear" }
        }, { responsive: true });
      }).catch(function (e) { console.error("scatter fetch:", e); });
    }
    // initialise slider from both selected runs; default to the final step
    refreshSlider();
    ctrls.querySelector("#sc-step").value = ctrls.querySelector("#sc-step").max;
    ["sc-class", "sc-step", "sc-log"].forEach(function (id) {
      ctrls.querySelector("#" + id).addEventListener("change", draw);
    });
    ["sc-a", "sc-b"].forEach(function (id) {
      ctrls.querySelector("#" + id).addEventListener("change", function () {
        refreshSlider(); draw();
      });
    });
    draw();
  };

  V.allocation = function (host, ctrls) {
    // static mass hierarchy (intersected with what the run emits)
    var MASS_TREE = {
      "cell_mass": ["protein_mass", "rna_mass", "dna_mass", "smallMolecule_mass", "water_mass"],
      "rna_mass": ["rRna_mass", "tRna_mass", "mRna_mass"]
    };
    // available mass observable paths in this run (path endswith the field name)
    var massPaths = {};
    Object.keys(state.observables).forEach(function (cat) {
      state.observables[cat].forEach(function (o) {
        if ((o.mclass === "Mass") || /_mass$/.test(o.path)) {
          var field = o.path.split(".").pop();
          massPaths[field] = o.path;
        }
      });
    });
    function childrenOf(field) {
      return (MASS_TREE[field] || []).filter(function (f) { return massPaths[f]; });
    }
    var path = ["cell_mass"];               // breadcrumb of fields
    var cache = { time: [], byField: {} };

    function currentChildren() {
      var node = path[path.length - 1];
      var kids = childrenOf(node);
      return kids.length ? kids : [node];   // leaf → show itself
    }

    function load() {
      var fields = currentChildren();
      var paths = fields.map(function (f) { return massPaths[f]; }).filter(Boolean);
      if (!paths.length) { render(); return; }
      var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&paths=" + encodeURIComponent(paths.join(",")));
      j(u).then(function (d) {
        cache.time = d.time; cache.byField = {};
        fields.forEach(function (f) { cache.byField[f] = d.series[massPaths[f]] || []; });
        var slider = ctrls.querySelector("#al-t");
        slider.max = Math.max(0, d.time.length - 1); slider.value = slider.max;
        render();
      });
    }

    function render() {
      ctrls.innerHTML =
        '<div class="al-crumb">' + path.map(function (f, i) {
          return '<span class="al-bc" data-i="' + i + '">' + f.replace(/_mass$/, "") + "</span>";
        }).join(" ▸ ") + "</div>" +
        '<label>Time <input id="al-t" type="range" min="0" max="' +
          Math.max(0, cache.time.length - 1) + '" value="' +
          Math.max(0, cache.time.length - 1) + '"></label>' +
        '<span id="al-tlabel" class="muted"></span>' +
        '<p class="muted" style="font-size:0.78em">double-click a cell to break it down</p>';
      ctrls.querySelectorAll(".al-bc").forEach(function (el) {
        el.addEventListener("click", function () {
          path = path.slice(0, parseInt(el.getAttribute("data-i"), 10) + 1); load();
        });
      });
      ctrls.querySelector("#al-t").addEventListener("input", draw);
      host.innerHTML = '<svg id="al-svg" width="520" height="520"></svg>';
      draw();
    }

    function draw() {
      var ti = parseInt(ctrls.querySelector("#al-t").value, 10) || 0;
      ctrls.querySelector("#al-tlabel").textContent =
        cache.time.length ? "t = " + (cache.time[ti] != null ? cache.time[ti].toFixed(1) : ti) : "";
      var fields = currentChildren();
      var leaves = fields.map(function (f) {
        return { name: f.replace(/_mass$/, ""), field: f,
                 value: Math.abs((cache.byField[f] || [])[ti] || 0) };
      }).filter(function (d) { return d.value > 0; });
      var svg = d3.select("#al-svg"); svg.selectAll("*").remove();
      if (!leaves.length) return;
      var W = 520, H = 520, R = 250, cx = W / 2, cy = H / 2, circle = [];
      for (var a = 0; a < 2 * Math.PI; a += Math.PI / 60)
        circle.push([cx + R * Math.cos(a), cy + R * Math.sin(a)]);
      var total = leaves.reduce(function (s, d) { return s + d.value; }, 0) || 1;
      var root = d3.hierarchy({ children: leaves }).sum(function (d) { return d.value; });
      d3.voronoiTreemap().clip(circle)(root);
      var color = d3.scaleOrdinal(d3.schemeCategory10);
      var cells = svg.selectAll("g").data(root.leaves()).enter().append("g");
      cells.append("path")
        .attr("d", function (d) { return "M" + d.polygon.join("L") + "Z"; })
        .attr("fill", function (d) { return color(d.data.name); })
        .attr("stroke", "#0e1116").attr("stroke-width", 1.5)
        .style("cursor", "pointer")
        .on("dblclick", function (ev, d) {
          if (childrenOf(d.data.field).length) { path.push(d.data.field); load(); }
        })
        .append("title").text(function (d) {
          return d.data.name + ": " + d.data.value.toFixed(2) + " fg (" +
                 (100 * d.data.value / total).toFixed(1) + "%)"; });
      cells.append("text")
        .attr("x", function (d) { return d.polygon.site.x; })
        .attr("y", function (d) { return d.polygon.site.y; })
        .attr("text-anchor", "middle").attr("fill", "#fff")
        .attr("font-size", "12px").style("pointer-events", "none")
        .text(function (d) {
          return (100 * d.data.value / total) > 4 ? d.data.name : ""; });
    }

    load();
  };

  V.flux = function (host, ctrls) {
    if (!window.escher) {
      host.innerHTML = '<p class="muted">Flux map library failed to load.</p>'; return;
    }
    ctrls.innerHTML =
      '<label>Step <input type="range" id="fx-t" min="0" max="' +
        Math.max(0, (state.run.n_steps || 1) - 1) + '" value="0"></label>' +
      '<span id="fx-cov" class="muted"></span>';
    host.innerHTML = '<div id="fx-map" style="height:460px;background:#fff;border-radius:6px"></div>';
    var builder = null;

    function ensureBuilder() {
      if (builder) return Promise.resolve(builder);
      var mapUrl = state.basePath + "/assets/explorer/ecoli_core.map.json";
      return fetch(mapUrl).then(function (r) { return r.json(); }).then(function (mapData) {
        try {
          var sel = (escher.libs && escher.libs.d3_select)
            ? escher.libs.d3_select("#fx-map")
            : d3.select("#fx-map");
          builder = escher.Builder(mapData, null, null, sel, {
            never_ask_before_quit: true, menu: "zoom", scroll_behavior: "zoom",
            reaction_styles: ["color", "size", "abs"], enable_editing: false
          });
        } catch (e) {
          return Promise.reject(e);
        }
        return builder;
      });
    }

    function draw() {
      var step = parseInt(ctrls.querySelector("#fx-t").value, 10) || 0;
      var u = api("/flux?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") + "&step=" + step);
      Promise.all([ensureBuilder(), j(u)]).then(function (res) {
        var b = res[0], d = res[1];
        b.set_reaction_data(d.fluxes || {});
        var c = d.coverage || { mapped: 0, total: 0 };
        ctrls.querySelector("#fx-cov").textContent =
          "mapped " + c.mapped + "/" + c.total + " reactions";
      }).catch(function (e) {
        host.innerHTML = '<p class="muted">Flux map unavailable: ' + e + "</p>";
      });
    }
    ctrls.querySelector("#fx-t").addEventListener("input", draw);
    draw();
  };
})();
