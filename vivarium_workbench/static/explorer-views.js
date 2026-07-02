/* Data Explorer views — split out of explorer.js to keep each file focused.
   Assigns into window.Explorer._Views (the object renderView dispatches through). */
(function () {
  "use strict";
  var E = window.Explorer;
  if (!E) return;
  var api = E._api, j = E._j, state = E._state, observableOptions = E._obsOptions;
  var V = E._Views;

  // EcoCyc pathway names carry HTML markup (<i>, &beta;, &alpha;…). Render them
  // human-readable: strip tags, then decode entities. Keep the raw name as the
  // option value so it still keys into pathways.json.
  var _decoder = document.createElement("textarea");
  function prettyName(s) {
    _decoder.innerHTML = String(s).replace(/<[^>]+>/g, "");
    return _decoder.value;
  }

  V.timeseries = function (host, ctrls) {
    var opts = observableOptions();
    var classes = ["All", "RNA", "Protein", "Metabolite", "Flux", "Mass", "Other"];
    ctrls.innerHTML =
      '<label>Class <select id="ts-class">' +
        classes.map(function (c) { return '<option>' + c + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Pathway preset <select id="ts-pathway"><option value="">— none —</option></select></label>' +
      '<label>Labels <select id="ts-label">' +
        '<option value="id">BioCyc id</option><option value="name">common name</option>' +
      "</select></label>" +
      '<label>Search <input id="ts-search" type="text" placeholder="filter…"></label>' +
      '<label>Observables <select id="ts-obs" multiple size="10"></select></label>' +
      '<label>y-scale <select id="ts-scale">' +
        '<option value="linear">linear</option><option value="log">log</option>' +
        '<option value="symlog">symlog</option>' +
      "</select></label>" +
      '<label><input type="checkbox" id="ts-norm"> normalize</label>';
    host.innerHTML = '<div id="ts-chart" style="height:520px"></div>';

    // Vector observables (monomer_counts, rna_counts, bulk, fluxes) are expanded
    // into one selectable entry PER element (per protein/RNA/metabolite/reaction),
    // labeled by its id, so individual molecules can be plotted over time.
    var idCache = {};       // vecPath -> [ids]
    var classCache = {};    // class -> [{key,label,unit}]
    var baseUnit = {};      // base path -> unit
    var keyLabel = {};      // key -> display label (for legend/options)
    var selected = {};      // key -> 1 (selection source of truth)
    var labels = {};        // {monomer:{id:name}, rna:{id:name}}
    var pathways = {};      // {name: {reactions, proteins/compounds, genes}}
    opts.forEach(function (o) { baseUnit[o.path] = o.unit || ""; });

    // common-name lookup for a vector element id (monomers + mRNAs)
    function nameFor(id) {
      var base = id.replace(/\[[^\]]*\]$/, "");
      var m = labels.monomer || {}, r = labels.rna || {};
      return m[base] || r[base] || r[base + "_RNA"] || id;
    }
    function displayLabel(o) {
      // scalars carry a "cat · label" label; only remap raw vector-element ids
      if ((o.label || "").indexOf(" · ") >= 0) return o.label;
      return ctrls.querySelector("#ts-label").value === "name" ? nameFor(o.label) : o.label;
    }

    function fetchIds(vecPath) {
      if (idCache[vecPath]) return Promise.resolve(idCache[vecPath]);
      var u = api("/vector?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&path=" + encodeURIComponent(vecPath) + "&step=0");
      return j(u).then(function (d) { idCache[vecPath] = d.ids || []; return idCache[vecPath]; });
    }

    function optionsForClass(cls, cb) {
      if (classCache[cls]) { cb(classCache[cls]); return; }
      var inClass = opts.filter(function (o) { return cls === "All" || o.mclass === cls; });
      var vectors = (cls === "All") ? [] : inClass.filter(function (o) { return o.kind === "vector"; });
      var scalars = inClass.filter(function (o) { return vectors.indexOf(o) < 0; });
      if (!vectors.length) { classCache[cls] = inClass; cb(inClass); return; }
      Promise.all(vectors.map(function (o) {
        return fetchIds(o.path).then(function (ids) {
          return ids.map(function (id, i) {
            return { key: o.path + "#" + i, label: id, unit: o.unit || "" };
          });
        });
      })).then(function (lists) {
        var expanded = scalars.slice();
        lists.forEach(function (l) { expanded = expanded.concat(l); });
        classCache[cls] = expanded; cb(expanded);
      }).catch(function () { classCache[cls] = scalars; cb(scalars); });
    }

    function refreshList() {
      var cls = ctrls.querySelector("#ts-class").value;
      var q = ctrls.querySelector("#ts-search").value.toLowerCase();
      var sel = ctrls.querySelector("#ts-obs");
      sel.innerHTML = '<option disabled>loading…</option>';
      optionsForClass(cls, function (items) {
        var matches = items.filter(function (o) {
          if (!q) return true;
          return (o.label || "").toLowerCase().indexOf(q) >= 0 ||
                 displayLabel(o).toLowerCase().indexOf(q) >= 0;
        });
        var shown = matches.slice(0, 1500);  // cap DOM; narrow with Search
        if (!matches.length) {
          // distinguish "this run emits nothing in this class" from "search hid
          // everything" — mass-only runs legitimately have no Protein/RNA/Flux.
          sel.innerHTML = q
            ? '<option disabled>— no matches for "' + q + '" —</option>'
            : '<option disabled>— this run emits no ' +
              (cls === "All" ? "" : cls + " ") + "data (try another run) —</option>";
          return;
        }
        sel.innerHTML = shown.map(function (o) {
          var dl = displayLabel(o); keyLabel[o.key] = dl;
          return '<option value="' + o.key + '"' + (selected[o.key] ? " selected" : "") +
                 ">" + dl + (o.unit ? " [" + o.unit + "]" : "") + "</option>";
        }).join("") + (matches.length > shown.length ?
          '<option disabled>… ' + (matches.length - shown.length) + " more — refine Search —</option>" : "");
      });
    }

    function syncSelectedFromDOM() {
      selected = {};
      Array.prototype.forEach.call(
        ctrls.querySelectorAll("#ts-obs option:checked"), function (o) { selected[o.value] = 1; });
    }

    // Auto-select every observable belonging to the chosen pathway, for the
    // active class (proteins/metabolites <- compounds, RNA <- genes, Flux <- reactions).
    function applyPathway() {
      var pw = ctrls.querySelector("#ts-pathway").value;
      var cls = ctrls.querySelector("#ts-class").value;
      if (!pw || !pathways[pw]) { return; }
      var P = pathways[pw], want = {};
      function add(a) { (a || []).forEach(function (x) { want[x] = 1; }); }
      if (cls === "Protein" || cls === "Metabolite" || cls === "All") add(P.compounds);
      if (cls === "RNA" || cls === "All") {
        add(P.genes); (P.genes || []).forEach(function (g) { want[g + "_RNA"] = 1; });
      }
      if (cls === "Flux" || cls === "All") add(P.reactions);
      optionsForClass(cls, function (items) {
        selected = {};
        items.forEach(function (o) {
          var base = o.label.replace(/\[[^\]]*\]$/, ""), alt = base.replace(/_RNA$/, "");
          if (want[o.label] || want[base] || want[alt]) selected[o.key] = 1;
        });
        refreshList(); draw();
      });
    }

    function unitForKey(k) {
      var base = k.indexOf("#") >= 0 ? k.slice(0, k.indexOf("#")) : k;
      return baseUnit[base] || "";
    }

    // Plotly has no native symlog; fake it with a sign·log10(1+|y|) transform on
    // a linear axis, labeling ticks back in original units so it stays readable.
    function symTransform(y) {
      return y.map(function (v) { return (v < 0 ? -1 : 1) * Math.log10(1 + Math.abs(v)); });
    }
    function symTicks(maxAbs) {
      var vals = [0], text = ["0"], dec = 1;
      while (dec <= (maxAbs || 0)) { vals.push(Math.log10(1 + dec)); text.push(String(dec)); dec *= 10; }
      return { vals: vals, text: text };
    }

    function draw() {
      var chosen = Object.keys(selected);
      if (!chosen.length) { Plotly.purge("ts-chart"); return; }
      var unitOf = {};
      chosen.forEach(function (k) { unitOf[k] = unitForKey(k); });
      var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&paths=" + encodeURIComponent(chosen.join(",")));
      j(u).then(function (d) {
        var norm = ctrls.querySelector("#ts-norm").checked;
        var scale = ctrls.querySelector("#ts-scale").value;
        // distinct units → one stacked panel each
        var units = [];
        chosen.forEach(function (k) { var un = unitOf[k] || "(unitless)";
          if (units.indexOf(un) < 0) units.push(un); });
        var n = units.length, traces = [], unitMax = {};
        var gap = 0.08;
        var h = n > 0 ? (1 - gap * (n - 1)) / n : 1;
        var layout = {
          margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
          font: { color: "#cfd6df" }, showlegend: true,
          xaxis: { anchor: n <= 1 ? "y" : "y" + n }
        };
        Object.keys(d.series).forEach(function (k) {
          var un = unitOf[k] || "(unitless)", i = units.indexOf(un);
          var y = d.series[k];
          if (norm) { var m = Math.max.apply(null, y.map(Math.abs)) || 1; y = y.map(function (v) { return v / m; }); }
          var mx = Math.max.apply(null, y.map(Math.abs)) || 0;
          if (mx > (unitMax[un] || 0)) unitMax[un] = mx;
          if (scale === "symlog") y = symTransform(y);
          traces.push({ type: "scatter", mode: "lines", name: keyLabel[k] || k, x: d.time, y: y,
                        xaxis: "x", yaxis: i === 0 ? "y" : "y" + (i + 1) });
        });
        units.forEach(function (un, i) {
          var top = 1 - i * (h + gap);
          var bottom = Math.max(0, top - h);
          var key = i === 0 ? "yaxis" : "yaxis" + (i + 1);
          var ax = { title: un + (scale === "symlog" ? " (symlog)" : ""),
                     domain: [bottom, top], anchor: "x",
                     type: scale === "log" ? "log" : "linear" };
          if (scale === "symlog") {
            var t = symTicks(unitMax[un]);
            ax.tickmode = "array"; ax.tickvals = t.vals; ax.ticktext = t.text;
          }
          layout[key] = ax;
        });
        Plotly.react("ts-chart", traces, layout, { responsive: true });
      });
    }

    ctrls.querySelector("#ts-class").addEventListener("change", function () {
      ctrls.querySelector("#ts-pathway").value = ""; refreshList();
    });
    ctrls.querySelector("#ts-pathway").addEventListener("change", applyPathway);
    ctrls.querySelector("#ts-label").addEventListener("change", function () { refreshList(); draw(); });
    ctrls.querySelector("#ts-search").addEventListener("input", refreshList);
    ctrls.querySelector("#ts-obs").addEventListener("change", function () { syncSelectedFromDOM(); draw(); });
    ctrls.querySelector("#ts-scale").addEventListener("change", draw);
    ctrls.querySelector("#ts-norm").addEventListener("change", draw);

    // load pathway presets + label maps, then populate the pathway dropdown
    E._aux().then(function (aux) {
      labels = aux.labels || {}; pathways = aux.pathways || {};
      var sel = ctrls.querySelector("#ts-pathway");
      Object.keys(pathways).sort().forEach(function (nm) {
        var o = document.createElement("option"); o.value = nm; o.textContent = prettyName(nm); sel.appendChild(o);
      });
      refreshList();
    });
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
    function runById(id) { return state.runs.find(function (r) { return r.run_id === id; }); }
    // Default Run A = the active run; Run B = a COMPARABLE run (same emitter,
    // different store — e.g. the sibling variant) so the scatter has shared
    // observables, not the mass-only run.
    var a0 = (state.run && runById(state.run.run_id)) ? state.run.run_id : state.runs[0].run_id;
    var aR = runById(a0) || state.runs[0];
    var b0 = (state.runs.find(function (r) {
                return r.run_id !== a0 && r.db_path !== aR.db_path && r.source === aR.source;
              }) ||
              state.runs.find(function (r) {
                return r.run_id !== a0 && r.db_path !== aR.db_path;
              }) || state.runs[1]).run_id;
    ctrls.innerHTML =
      '<label>Class <select id="sc-class">' +
        classes.map(function (c) { return "<option>" + c + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Run A (x) <select id="sc-a">' + runOpts(a0) + "</select></label>" +
      '<label>Run B (y) <select id="sc-b">' + runOpts(b0) + "</select></label>" +
      '<label>Step <input id="sc-step" type="range" min="0" max="0" value="0"></label>' +
      '<label><input type="checkbox" id="sc-log" checked> log-log</label>';
    host.innerHTML = '<div id="sc-chart" style="height:520px"></div>';

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
          var miss = (!B.ids || !B.ids.length) ? (rb.label || rb.run_id) : (ra.label || ra.run_id);
          host.innerHTML = '<p class="muted" style="padding:12px"><b>' + miss +
            '</b> has no <b>' + cls + '</b> data — pick a Run with the same emitter/observables ' +
            '(e.g. another variant of this run).</p>';
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
    // Fine dry-mass composition. Water dwarfs everything, so it's EXCLUDED by
    // default (toggle to add it). rna is shown as its three sub-masses so the
    // breakdown is finer; protein/dna/smallMolecule have no emitted sub-masses.
    var ROOT_FIELDS = ["protein_mass", "rRna_mass", "tRna_mass", "mRna_mass",
                       "dna_mass", "smallMolecule_mass"];
    var MASS_TREE = {};                      // leaves only; synthetic "dry" root
    var includeWater = false;
    // available mass observable paths in this run (field -> emitted path).
    // Paths are dot-separated (sqlite/zarr) OR "__"-separated (parquet); take the
    // last segment either way so ROOT_FIELDS (short names) match both.
    function leafOf(p) { return p.split("__").pop().split(".").pop(); }
    var massPaths = {};
    Object.keys(state.observables).forEach(function (cat) {
      state.observables[cat].forEach(function (o) {
        if ((o.mclass === "Mass") || /_mass$/.test(o.path)) {
          massPaths[leafOf(o.path)] = o.path;
        }
      });
    });
    function rootFields() {
      var fs = ROOT_FIELDS.filter(function (f) { return massPaths[f]; });
      if (includeWater && massPaths["water_mass"]) fs = fs.concat("water_mass");
      return fs;
    }
    function childrenOf(field) {
      return (MASS_TREE[field] || []).filter(function (f) { return massPaths[f]; });
    }
    // protein → functional-category drill: needs the monomer_counts vector path.
    var proteinPath = null;
    Object.keys(state.observables).forEach(function (cat) {
      state.observables[cat].forEach(function (o) {
        if (!proteinPath && o.mclass === "Protein" && o.kind === "vector") proteinPath = o.path;
      });
    });
    var PROTEIN_NODE = "protein:cat";
    function isProtein(node) { return node === PROTEIN_NODE; }
    function label(f) {
      return f === "dry" ? "dry mass"
        : f === PROTEIN_NODE ? "protein"
        : f.replace(/_mass$/, "");
    }
    var path = ["dry"];                      // "dry" = synthetic root (no water)
    var cache = { time: [], byField: {} };

    function currentChildren() {
      var node = path[path.length - 1];
      if (node === "dry") return rootFields();
      var kids = childrenOf(node);
      return kids.length ? kids : [node];
    }

    function load() {
      var node = path[path.length - 1];
      if (isProtein(node)) { render(); return; }  // reuse cache.time; draw fetches breakdown
      var fields = currentChildren();
      var paths = fields.map(function (f) { return massPaths[f]; }).filter(Boolean);
      if (!paths.length) { render(); return; }
      var u = api("/series?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&paths=" + encodeURIComponent(paths.join(",")));
      j(u).then(function (d) {
        cache.time = d.time; cache.byField = {};
        fields.forEach(function (f) { cache.byField[f] = d.series[massPaths[f]] || []; });
        render();
      });
    }

    function render() {
      var atRoot = path[path.length - 1] === "dry";
      ctrls.innerHTML =
        '<div class="al-crumb">' + path.map(function (f, i) {
          return '<span class="al-bc" data-i="' + i + '">' + label(f) + "</span>";
        }).join(" ▸ ") + "</div>" +
        (atRoot ? '<label><input type="checkbox" id="al-water"' +
          (includeWater ? " checked" : "") + "> include water</label>" : "") +
        '<label>Time <input id="al-t" type="range" min="0" max="' +
          Math.max(0, cache.time.length - 1) + '" value="' +
          Math.max(0, cache.time.length - 1) + '"></label>' +
        '<span id="al-tlabel" class="muted"></span>' +
        (proteinPath && atRoot ? '<p class="muted" style="font-size:0.78em">' +
          "double-click <b>protein</b> to break it down by function</p>" : "");
      ctrls.querySelectorAll(".al-bc").forEach(function (el) {
        el.addEventListener("click", function () {
          path = path.slice(0, parseInt(el.getAttribute("data-i"), 10) + 1); load();
        });
      });
      var water = ctrls.querySelector("#al-water");
      if (water) water.addEventListener("change", function (e) {
        includeWater = e.target.checked; load();
      });
      ctrls.querySelector("#al-t").addEventListener("input", draw);
      host.innerHTML = '<svg id="al-svg" width="540" height="540"></svg>';
      draw();
    }

    // Shared voronoi renderer for a set of {name, field, value} leaves.
    function renderCells(leaves) {
      var svg = d3.select("#al-svg"); svg.selectAll("*").remove();
      if (!leaves.length) return;
      var W = 540, H = 540, R = 260, cx = W / 2, cy = H / 2, circle = [];
      for (var a = 0; a < 2 * Math.PI; a += Math.PI / 60)
        circle.push([cx + R * Math.cos(a), cy + R * Math.sin(a)]);
      var total = leaves.reduce(function (s, d) { return s + d.value; }, 0) || 1;
      var root = d3.hierarchy({ children: leaves }).sum(function (d) { return d.value; });
      d3.voronoiTreemap().clip(circle)(root);
      var color = d3.scaleOrdinal(d3.schemeTableau10);
      var cells = svg.selectAll("g").data(root.leaves()).enter().append("g");
      cells.append("path")
        .attr("d", function (d) { return "M" + d.polygon.join("L") + "Z"; })
        .attr("fill", function (d) { return color(d.data.name); })
        .attr("stroke", "#0e1116").attr("stroke-width", 1.5)
        .style("cursor", "pointer")
        .on("dblclick", function (ev, d) {
          if (d.data.field === "protein_mass" && proteinPath) {
            path.push(PROTEIN_NODE); load();
          } else if (childrenOf(d.data.field).length) {
            path.push(d.data.field); load();
          }
        })
        .append("title").text(function (d) {
          return d.data.name + ": " + (100 * d.data.value / total).toFixed(1) + "%"; });
      cells.append("text")
        .attr("text-anchor", "middle").attr("fill", "#fff")
        .style("pointer-events", "none")
        .attr("transform", function (d) {
          return "translate(" + d.polygon.site.x + "," + d.polygon.site.y + ")"; })
        .filter(function (d) { return (100 * d.data.value / total) > 0.8; })
        .each(function (d) {
          var pct = 100 * d.data.value / total, g = d3.select(this);
          g.append("tspan").attr("x", 0).attr("dy", "-0.1em")
            .attr("font-size", pct > 6 ? "13px" : "10px").text(d.data.name);
          g.append("tspan").attr("x", 0).attr("dy", "1.15em")
            .attr("font-size", "9px").attr("fill", "#cfd6df")
            .text(pct.toFixed(pct < 10 ? 1 : 0) + "%");
        });
    }

    function draw() {
      var ti = parseInt(ctrls.querySelector("#al-t").value, 10) || 0;
      ctrls.querySelector("#al-tlabel").textContent =
        cache.time.length ? "t = " + (cache.time[ti] != null ? cache.time[ti].toFixed(1) : ti) : "";
      if (isProtein(path[path.length - 1])) {
        // protein functional-category breakdown at this timepoint
        var u = api("/protein-breakdown?db=" + encodeURIComponent(state.run.db_path) +
                    "&run=" + encodeURIComponent(state.run.run_id || "") +
                    "&step=" + ti + "&path=" + encodeURIComponent(proteinPath));
        d3.select("#al-svg").selectAll("*").remove();
        j(u).then(function (d) {
          var bd = (d && d.breakdown) || {};
          var leaves = Object.keys(bd).map(function (k) {
            return { name: k, field: k, value: Math.abs(bd[k] || 0) };
          }).filter(function (x) { return x.value > 0; });
          renderCells(leaves);
        }).catch(function () {});
        return;
      }
      var fields = currentChildren();
      var leaves = fields.map(function (f) {
        return { name: label(f), field: f,
                 value: Math.abs((cache.byField[f] || [])[ti] || 0) };
      }).filter(function (d) { return d.value > 0; });
      renderCells(leaves);
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
        // escher.Builder loads the map asynchronously; calling set_reaction_data
        // before the map exists throws "this.map ... apply_reaction_data_to_map".
        // Gate readiness on first_load_callback (fall back to a tick if the build
        // is synchronous and the callback never fires).
        return new Promise(function (resolve, reject) {
          try {
            var sel = (escher.libs && escher.libs.d3_select)
              ? escher.libs.d3_select("#fx-map")
              : d3.select("#fx-map");
            var settled = false;
            var done = function (b) { if (!settled) { settled = true; builder = b; resolve(b); } };
            var b = escher.Builder(mapData, null, null, sel, {
              never_ask_before_quit: true, menu: "zoom", scroll_behavior: "zoom",
              reaction_styles: ["color", "size", "abs"], enable_editing: false,
              first_load_callback: function () { if (b && b.map) done(b); }
            });
            // Safety net: if the map is already built (no callback), resolve once
            // it has a map object; otherwise poll briefly.
            var tries = 0;
            (function check() {
              if (settled) return;
              if (b && b.map) { done(b); return; }
              if (tries++ > 100) { reject(new Error("escher map did not finish loading")); return; }
              setTimeout(check, 30);
            })();
          } catch (e) {
            reject(e);
          }
        });
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

  // Pathways — EcoCyc-pathway-grouped FBA fluxes. Shows EVERY emitted reaction
  // (including transport/uptake that the Escher central-carbon map omits),
  // organized by EcoCyc pathway. Pick a pathway -> horizontal bar of each
  // reaction's flux at the chosen step; an "unassigned" group catches transport
  // and demand reactions that belong to no small-molecule pathway.
  V.pathways = function (host, ctrls) {
    var nsteps = (state.run && state.run.n_steps) || 1;
    var last = Math.max(0, nsteps - 1);
    ctrls.innerHTML =
      '<label>Pathway <select id="pw-sel"><option value="">loading…</option></select></label>' +
      '<label>Step <input id="pw-step" type="range" min="0" max="' + last + '" value="' + last + '">' +
        '<span id="pw-tlabel" class="muted"></span></label>' +
      '<label><input type="checkbox" id="pw-nz" checked> hide zero-flux</label>' +
      '<div id="pw-stat" class="muted" style="margin-top:8px;font-size:12px"></div>';
    host.innerHTML = '<div id="pw-chart" style="height:560px"></div>';

    var UNASSIGNED = "— transport / other (no EcoCyc pathway) —";
    var pathways = {};     // name -> {reactions:[...]}
    var assigned = {};     // reaction id -> 1 if in any pathway
    var fluxCache = {};    // step -> base-fluxes response

    function loadFluxes(step) {
      if (fluxCache[step]) return Promise.resolve(fluxCache[step]);
      var u = api("/base-fluxes?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") + "&step=" + step);
      return j(u).then(function (d) { fluxCache[step] = d; return d; });
    }

    function reactionIds(name, fluxes) {
      if (name === UNASSIGNED) {
        return Object.keys(fluxes).filter(function (r) { return !assigned[r]; });
      }
      return ((pathways[name] || {}).reactions || []).filter(function (r) { return r in fluxes; });
    }

    function draw() {
      var name = ctrls.querySelector("#pw-sel").value;
      var step = parseInt(ctrls.querySelector("#pw-step").value, 10) || 0;
      var hideZero = ctrls.querySelector("#pw-nz").checked;
      var stat = ctrls.querySelector("#pw-stat");
      if (!name) { Plotly.purge("pw-chart"); return; }
      stat.textContent = "loading…";
      loadFluxes(step).then(function (d) {
        var fluxes = d.fluxes || {};
        ctrls.querySelector("#pw-tlabel").textContent =
          d.time != null ? " t=" + (+d.time).toFixed(0) : "";
        if (!Object.keys(fluxes).length) {
          Plotly.purge("pw-chart");
          stat.innerHTML = d.error ? ("⚠ " + d.error)
            : "This run emits no FBA fluxes (listeners.fba_results.base_reaction_fluxes).";
          return;
        }
        var ids = reactionIds(name, fluxes);
        var totalInGroup = ids.length;
        var rows = ids.map(function (r) { return { id: r, v: fluxes[r] || 0 }; });
        if (hideZero) rows = rows.filter(function (x) { return Math.abs(x.v) > 1e-9; });
        var nNonzero = rows.length;
        // cap very large groups (e.g. unassigned transport) to the strongest fluxes
        var capped = 0;
        if (rows.length > 60) {
          rows.sort(function (a, b) { return Math.abs(b.v) - Math.abs(a.v); });
          capped = rows.length - 60; rows = rows.slice(0, 60);
        }
        rows.sort(function (a, b) { return a.v - b.v; });  // signed; consumed at bottom
        if (!rows.length) {
          Plotly.purge("pw-chart");
          stat.innerHTML = "Pathway has " + totalInGroup + " reactions in this run, " +
            "all zero-flux at this step (uncheck “hide zero-flux” to list them).";
          return;
        }
        var trace = {
          type: "bar", orientation: "h",
          x: rows.map(function (r) { return r.v; }),
          y: rows.map(function (r) { return r.id; }),
          marker: { color: rows.map(function (r) { return r.v >= 0 ? "#4c8bf5" : "#e15759"; }) },
          hovertemplate: "%{y}<br>flux = %{x:.4g}<extra></extra>"
        };
        Plotly.react("pw-chart", [trace], {
          margin: { t: 10, r: 10, l: 220 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
          font: { color: "#cfd6df" }, bargap: 0.25,
          xaxis: { title: "flux (mmol/gDCW/h, signed)", zeroline: true, zerolinecolor: "#555" },
          yaxis: { automargin: true, tickfont: { size: 9 } }
        }, { responsive: true });
        stat.innerHTML = "<b>" + prettyName(name) + "</b> · " + nNonzero + " active / " +
          totalInGroup + " reactions" + (capped ? " · showing top 60 by |flux| (" +
          capped + " more)" : "");
      });
    }

    E._aux().then(function (aux) {
      pathways = aux.pathways || {};
      Object.keys(pathways).forEach(function (nm) {
        (pathways[nm].reactions || []).forEach(function (r) { assigned[r] = 1; });
      });
      // populate dropdown with pathways present in THIS run; default to the one
      // with the most active reactions so the chart opens populated.
      loadFluxes(last).then(function (d) {
        var fluxes = d.fluxes || {};
        var sel = ctrls.querySelector("#pw-sel");
        var present = Object.keys(pathways).filter(function (nm) {
          return (pathways[nm].reactions || []).some(function (r) { return r in fluxes; });
        });
        // rank by number of nonzero-flux reactions (most interesting first)
        function activeCount(nm) {
          return (pathways[nm].reactions || []).filter(function (r) {
            return r in fluxes && Math.abs(fluxes[r]) > 1e-9; }).length;
        }
        present.sort(function (a, b) { return activeCount(b) - activeCount(a) || a.localeCompare(b); });
        var opts = present.map(function (nm) {
          return '<option value="' + nm.replace(/"/g, "&quot;") + '">' +
                 prettyName(nm) + " (" + activeCount(nm) + ")</option>";
        });
        sel.innerHTML =
          '<option value="' + UNASSIGNED + '">' + UNASSIGNED + "</option>" + opts.join("");
        sel.value = present.length ? present[0] : UNASSIGNED;
        draw();
      });
    });

    ctrls.querySelector("#pw-sel").addEventListener("change", draw);
    ctrls.querySelector("#pw-step").addEventListener("input", draw);
    ctrls.querySelector("#pw-nz").addEventListener("change", draw);
  };

  // Validation — simulated (time-averaged) protein counts vs experimental
  // proteomics (Schmidt 2015 / Wisniewski 2014), log-log with a parity line and
  // Pearson r. Ported from sms-api's marimo explore.py. An optional pathway
  // filter narrows to one module's proteins.
  V.validation = function (host, ctrls) {
    ctrls.innerHTML =
      '<label>Dataset <select id="vl-ds">' +
        '<option value="schmidt">Schmidt 2015 (glucose)</option>' +
        '<option value="wisniewski">Wisniewski 2014</option>' +
      "</select></label>" +
      '<label>Pathway filter <select id="vl-pw"><option value="">— all proteins —</option></select></label>' +
      '<label><input type="checkbox" id="vl-log" checked> log-log</label>' +
      '<div id="vl-stat" class="muted" style="margin-top:8px;font-size:12px"></div>';
    host.innerHTML = '<div id="vl-chart" style="height:520px"></div>';

    var nsteps = (state.run && state.run.n_steps) || 0;
    var dsCache = {};         // dataset -> response
    var pwProteins = {};      // pathway name -> [monomer base ids]

    function load(ds) {
      if (dsCache[ds]) return Promise.resolve(dsCache[ds]);
      var u = api("/validation?db=" + encodeURIComponent(state.run.db_path) +
                  "&run=" + encodeURIComponent(state.run.run_id || "") +
                  "&dataset=" + ds + "&nsteps=" + nsteps);
      return j(u).then(function (d) { dsCache[ds] = d; return d; });
    }

    function pearson(xs, ys) {
      var n = xs.length, i; if (n < 2) return null;
      var mx = 0, my = 0;
      for (i = 0; i < n; i++) { mx += xs[i]; my += ys[i]; }
      mx /= n; my /= n;
      var sxx = 0, syy = 0, sxy = 0, dx, dy;
      for (i = 0; i < n; i++) { dx = xs[i] - mx; dy = ys[i] - my; sxx += dx * dx; syy += dy * dy; sxy += dx * dy; }
      if (sxx <= 0 || syy <= 0) return null;
      return sxy / Math.sqrt(sxx * syy);
    }

    function draw() {
      var ds = ctrls.querySelector("#vl-ds").value;
      var pw = ctrls.querySelector("#vl-pw").value;
      var log = ctrls.querySelector("#vl-log").checked;
      var stat = ctrls.querySelector("#vl-stat");
      stat.textContent = "loading…";
      load(ds).then(function (d) {
        var pts = d.points || [];
        if (!pts.length) {
          Plotly.purge("vl-chart");
          stat.innerHTML = d.error ? ("⚠ " + d.error) :
            "No overlapping proteins — this run has no monomer_counts, or the " +
            "validation asset is missing (run scripts/build_explorer_bio_assets.py).";
          return;
        }
        if (pw && pwProteins[pw]) {
          var set = {}; pwProteins[pw].forEach(function (id) { set[id] = 1; });
          pts = pts.filter(function (p) { return set[p.id]; });
        }
        if (!pts.length) {
          Plotly.purge("vl-chart");
          stat.textContent = "No validated proteins in pathway “" + pw + "”.";
          return;
        }
        var f = function (v) { return log ? Math.log10(v + 1) : v; };
        var xs = pts.map(function (p) { return f(p.exp); });
        var ys = pts.map(function (p) { return f(p.sim); });
        var text = pts.map(function (p) { return p.gene + (p.name ? " — " + p.name : ""); });
        var hi = 1; xs.concat(ys).forEach(function (v) { if (v > hi) hi = v; });
        var trace = { type: "scattergl", mode: "markers", x: xs, y: ys, text: text,
          hovertemplate: "%{text}<br>exp=%{x:.3g} sim=%{y:.3g}<extra></extra>",
          marker: { size: 6, opacity: 0.55, color: "#4c8bf5" } };
        var parity = { type: "scatter", mode: "lines", x: [0, hi], y: [0, hi],
          line: { color: "#e15759", dash: "dash" }, hoverinfo: "skip", showlegend: false };
        var dsLabel = ds === "wisniewski" ? "Wisniewski 2014" : "Schmidt 2015";
        var axis = log ? "log10(counts + 1)" : "counts";
        Plotly.react("vl-chart", [trace, parity], {
          margin: { t: 10, r: 10 }, paper_bgcolor: "#0e1116", plot_bgcolor: "#0e1116",
          font: { color: "#cfd6df" },
          xaxis: { title: dsLabel + " experimental " + axis },
          yaxis: { title: "Simulation avg " + axis }
        }, { responsive: true });
        var r = pearson(xs, ys);
        stat.innerHTML = "<b>Pearson r = " + (r == null ? "n/a" : r.toFixed(3)) + "</b> · " +
          pts.length + " proteins" + (d.n_steps ? " · avg over " + d.n_steps + " timepoints" : "");
      });
    }

    E._aux().then(function (aux) {
      var pw = aux.pathways || {}, sel = ctrls.querySelector("#vl-pw");
      Object.keys(pw).sort().forEach(function (nm) {
        // monomers in this pathway (compounds ending in -MONOMER) are candidate
        // validated proteins; keep all compounds — the filter intersects anyway
        pwProteins[nm] = pw[nm].compounds || [];
        var o = document.createElement("option"); o.value = nm; o.textContent = nm; sel.appendChild(o);
      });
      draw();
    });

    ["vl-ds", "vl-pw", "vl-log"].forEach(function (id) {
      ctrls.querySelector("#" + id).addEventListener("change", draw);
    });
  };
})();
