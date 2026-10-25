/*
 * argument-graph.js — the RFC-0005 graph-view toggle.
 *
 * Studies | Argument | Narrative as peer views of the investigation graph.
 *   - Studies  : the normal study DAG (restored via window._restoreStudiesDag).
 *   - Argument : the derived Claim graph — claim nodes coloured by verdict, laid
 *                out in dependency layers, with typed claim->claim edges
 *                (support/challenge/prerequisite/synthesis), drawn into the same
 *                #investigation-dag-nodes / #investigation-dag-edges hosts.
 *   - Narrative: the ordered reading (claims by explanatory dependence) with the
 *                audit gaps marked inline between the claims they separate.
 *
 * Fed by /api/argument-overlay. Additive and tolerant: on any failure it falls
 * back to Studies, so the normal graph is never left broken.
 */
(function () {
  "use strict";

  var MODE = "studies";

  var VERDICT = {
    supported:    { bg: "#dcfce7", bd: "#16a34a", mk: "✓" },
    qualified:    { bg: "#fef3c7", bd: "#d97706", mk: "≈" },
    refuted:      { bg: "#fee2e2", bd: "#dc2626", mk: "✗" },
    inconclusive: { bg: "#f1f5f9", bd: "#64748b", mk: "?" },
    unassessed:   { bg: "#f1f5f9", bd: "#94a3b8", mk: "·" }
  };
  var EDGE = {
    support: "#16a34a", prerequisite: "#2563eb", challenge: "#dc2626", synthesis: "#cbd5e1"
  };
  var NODE_W = 210, NODE_H = 62, COL = 260, VGAP = 18, PAD = 16;

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function _nodes() { return document.getElementById("investigation-dag-nodes"); }
  function _edges() { return document.getElementById("investigation-dag-edges"); }
  function _vk(v) { return VERDICT[v] || VERDICT.unassessed; }

  function _ensureStyle() {
    if (document.getElementById("ag-style")) return;
    var css =
      ".ag-node{position:absolute;box-sizing:border-box;width:" + NODE_W + "px;min-height:" + NODE_H + "px;"
      + "border:1px solid #e2e8f0;border-left-width:4px;border-radius:9px;background:#fff;padding:7px 10px;"
      + "font-size:0.8em;line-height:1.25;box-shadow:0 1px 2px rgba(0,0,0,0.04);overflow:hidden}"
      + ".ag-node .ag-mk{font-weight:700;margin-right:4px}"
      + ".ag-node .ag-ev{color:#94a3b8;font-size:0.82em;float:right}"
      + ".ag-narr{padding:8px 4px 24px;max-width:860px}"
      + ".ag-unit{display:flex;gap:10px;align-items:flex-start;padding:9px 12px;border:1px solid #eef2f7;"
      + "border-left-width:4px;border-radius:9px;background:#fff;margin:7px 0}"
      + ".ag-unit .ag-ord{color:#94a3b8;font-variant-numeric:tabular-nums;min-width:1.5em;text-align:right}"
      + ".ag-unit .ag-prop{flex:1;font-size:0.9em}"
      + ".ag-unit .ag-badge{font-size:0.74em;padding:1px 7px;border-radius:999px;white-space:nowrap}"
      + ".ag-gap{margin:2px 0 2px 34px;padding:4px 10px;font-size:0.8em;color:#92400e;"
      + "border-left:3px solid #f59e0b;background:#fffbeb;border-radius:0 7px 7px 0}"
      + ".ag-legend{position:absolute;right:10px;top:8px;font-size:0.72em;color:#64748b;background:rgba(255,255,255,0.9);"
      + "border:1px solid #eef2f7;border-radius:7px;padding:5px 8px;z-index:3}"
      + ".ag-legend span{margin-right:9px;white-space:nowrap}";
    var el = document.createElement("style"); el.id = "ag-style"; el.textContent = css;
    document.head.appendChild(el);
  }

  // Longest-path layering over the ORDERING edges (prerequisite + support).
  function _layers(ids, edges) {
    var succ = {}, indeg = {}, layer = {};
    ids.forEach(function (n) { succ[n] = []; indeg[n] = 0; layer[n] = 0; });
    edges.forEach(function (e) {
      if (succ[e.from] && indeg[e.to] != null && (e.type === "prerequisite" || e.type === "support")) {
        succ[e.from].push(e.to); indeg[e.to]++;
      }
    });
    var q = ids.filter(function (n) { return indeg[n] === 0; }).sort();
    while (q.length) {
      var n = q.shift();
      succ[n].forEach(function (m) {
        if (layer[n] + 1 > layer[m]) layer[m] = layer[n] + 1;
        if (--indeg[m] === 0) q.push(m);
      });
      q.sort();
    }
    return layer;
  }

  function _renderArgument(data) {
    var claims = data.claims || {}, edges = data.edges || [];
    var ids = Object.keys(claims).filter(function (c) { return !claims[c].arm_of; });
    var idset = {}; ids.forEach(function (i) { idset[i] = 1; });
    var layer = _layers(ids, edges);

    // stack nodes within each layer (sorted by id for determinism)
    var byLayer = {};
    ids.forEach(function (n) { (byLayer[layer[n]] = byLayer[layer[n]] || []).push(n); });
    var pos = {}, maxX = 0, maxY = 0;
    Object.keys(byLayer).forEach(function (L) {
      byLayer[L].sort().forEach(function (n, i) {
        var x = PAD + (+L) * COL, y = PAD + i * (NODE_H + VGAP);
        pos[n] = { x: x, y: y };
        if (x + NODE_W > maxX) maxX = x + NODE_W;
        if (y + NODE_H > maxY) maxY = y + NODE_H;
      });
    });

    var nodesHost = _nodes(), edgesSvg = _edges();
    nodesHost.innerHTML = ""; edgesSvg.innerHTML = "";
    nodesHost.style.height = (maxY + PAD) + "px";
    nodesHost.style.minWidth = (maxX + PAD) + "px";
    edgesSvg.setAttribute("width", maxX + PAD); edgesSvg.setAttribute("height", maxY + PAD);
    edgesSvg.style.width = (maxX + PAD) + "px"; edgesSvg.style.height = (maxY + PAD) + "px";

    // edges first (under nodes)
    var seen = {};
    edges.forEach(function (e) {
      if (!idset[e.from] || !idset[e.to]) return;            // skip arm/synthesis to arms
      var a = pos[e.from], b = pos[e.to]; if (!a || !b) return;
      var k = e.from + "|" + e.to + "|" + e.type; if (seen[k]) return; seen[k] = 1;
      var x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
      var midx = (x1 + x2) / 2;
      var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", "M" + x1 + "," + y1 + " C" + midx + "," + y1 + " " + midx + "," + y2 + " " + x2 + "," + y2);
      p.setAttribute("fill", "none");
      p.setAttribute("stroke", EDGE[e.type] || "#cbd5e1");
      p.setAttribute("stroke-width", "1.5");
      if (e.type === "challenge") p.setAttribute("stroke-dasharray", "5,4");
      p.setAttribute("opacity", "0.8");
      edgesSvg.appendChild(p);
    });

    // nodes
    ids.forEach(function (n) {
      var c = claims[n], vk = _vk(c.verdict), p = pos[n];
      var prop = c.proposition || n;
      var short = prop.length > 120 ? prop.slice(0, 118) + "…" : prop;
      var ev = (c.evidence_refs || []).length;
      var div = document.createElement("div");
      div.className = "ag-node";
      div.style.left = p.x + "px"; div.style.top = p.y + "px";
      div.style.background = vk.bg; div.style.borderLeftColor = vk.bd;
      div.title = prop;
      div.innerHTML = '<span class="ag-mk" style="color:' + vk.bd + '">' + vk.mk + "</span>"
        + '<span class="ag-ev">' + ev + " ev</span>" + _esc(short);
      nodesHost.appendChild(div);
    });

    _legend(nodesHost, [
      ["#16a34a", "support"], ["#2563eb", "prerequisite"],
      ["#dc2626", "challenge (dashed)"], ["#cbd5e1", "synthesis"]
    ]);
  }

  function _renderNarrative(data) {
    var claims = data.claims || {}, nar = data.narrative || {}, units = nar.units || [];
    var gaps = data.gaps || [];
    var gapByFromTo = {};
    gaps.forEach(function (g) { gapByFromTo[g.from + "|" + g.to] = g; });

    var edgesSvg = _edges(); if (edgesSvg) { edgesSvg.innerHTML = ""; edgesSvg.style.height = "0"; }
    var host = _nodes(); host.innerHTML = ""; host.style.height = "auto"; host.style.minWidth = "0";
    var wrap = document.createElement("div"); wrap.className = "ag-narr";

    units.forEach(function (u, i) {
      var c = claims[u.claim_id] || {}, vk = _vk(u.verdict || c.verdict);
      var prop = u.proposition || c.proposition || u.claim_id;
      var ev = (u.evidence_refs || c.evidence_refs || []).length;
      var unit = document.createElement("div");
      unit.className = "ag-unit"; unit.style.borderLeftColor = vk.bd;
      unit.innerHTML = '<span class="ag-ord">' + (i + 1) + "</span>"
        + '<span class="ag-prop">' + _esc(prop) + "</span>"
        + '<span class="ag-badge" style="background:' + vk.bg + ";color:" + vk.bd + '">' + vk.mk + " " + _esc(u.verdict || c.verdict || "unassessed") + "</span>"
        + '<span class="ag-ord" style="min-width:3em;color:#94a3b8">' + ev + " ev</span>";
      wrap.appendChild(unit);
      if (i + 1 < units.length) {
        var g = gapByFromTo[u.claim_id + "|" + units[i + 1].claim_id];
        if (g) {
          var gd = document.createElement("div");
          gd.className = "ag-gap";
          gd.textContent = "⚠ unsupported transition" + (g.verdict_escalation ? " ↑" : "")
            + " — " + (g.missing_evidence || "no supporting link");
          wrap.appendChild(gd);
        }
      }
    });
    if (!units.length) wrap.innerHTML = '<p class="empty-state" style="padding:24px">No claims to narrate yet.</p>';
    host.appendChild(wrap);
  }

  function _legend(host, items) {
    var el = document.createElement("div"); el.className = "ag-legend";
    el.innerHTML = items.map(function (it) {
      return '<span><b style="color:' + it[0] + '">—</b> ' + _esc(it[1]) + "</span>";
    }).join("");
    host.appendChild(el);
  }

  function _setActive(mode) {
    ["studies", "argument", "narrative"].forEach(function (m) {
      var b = document.getElementById("gview-" + m);
      if (b) b.classList.toggle("active", m === mode);
    });
  }

  function _fetch(slug) {
    try {
      if (window.DataSource && window.DataSource.loadArgumentOverlay) return window.DataSource.loadArgumentOverlay(slug);
      var base = window.__BASE_PATH__ || "";
      return fetch(base + "/api/argument-overlay?investigation=" + encodeURIComponent(slug)).then(function (r) { return r.ok ? r.json() : null; });
    } catch (e) { return Promise.resolve(null); }
  }

  window._setGraphView = function (mode) {
    MODE = mode; _setActive(mode);
    if (mode === "studies") {
      var es = _edges(); if (es) { es.innerHTML = ""; es.style.height = ""; }
      var nh = _nodes(); if (nh) { nh.style.height = ""; nh.style.minWidth = ""; }
      if (window._restoreStudiesDag) window._restoreStudiesDag();
      // If the DAG hadn't been rendered this session (nothing to restore), the
      // restore is a no-op — clear any leftover Argument/Narrative content so the
      // view never shows stale claims under the Studies tab.
      if (nh && nh.querySelector(".ag-unit, .ag-node, .ag-legend")) nh.innerHTML = "";
      return;
    }
    var slug = window._currentIsetSlug || "";
    if (!slug) { window._setGraphView("studies"); return; }
    _ensureStyle();
    _fetch(slug).then(function (data) {
      if (MODE !== mode) return;                 // user switched again meanwhile
      if (!data || data.available === false) { window._setGraphView("studies"); return; }
      try { (mode === "argument" ? _renderArgument : _renderNarrative)(data); }
      catch (e) { window._setGraphView("studies"); }
    }).catch(function () { window._setGraphView("studies"); });
  };
})();
