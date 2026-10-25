/*
 * argument-graph.js — the RFC-0005 graph-view toggle.
 *
 * Studies | Argument | Narrative as peer views of the investigation graph.
 *   - Studies  : the normal study DAG (restored via window._restoreStudiesDag).
 *   - Argument : the derived Claim graph — claim nodes coloured by verdict, laid
 *                out in dependency layers (sub-columns wrap crowded layers), with
 *                typed claim->claim edges. Pan with drag, zoom with the wheel.
 *   - Narrative: the ordered reading with the audit gaps marked inline.
 *
 * Fed by /api/argument-overlay. While a non-Studies view is active,
 * window._graphViewMode + window._reRenderGraphView let the Studies DAG's own
 * re-render triggers (zoom/orientation/fit/band/fonts) redraw THIS view instead
 * of snapping back to Studies. Tolerant: any failure falls back to Studies.
 */
(function () {
  "use strict";

  var MODE = "studies";
  var _lastData = null, _lastSlug = null;        // cache so re-renders don't refetch
  var _scale = 1, _tx = 0, _ty = 0, _panBound = false, _drag = null;
  var VERDICT = {
    supported:    { bg: "#dcfce7", bd: "#16a34a", mk: "✓" },
    qualified:    { bg: "#fef3c7", bd: "#d97706", mk: "≈" },
    refuted:      { bg: "#fee2e2", bd: "#dc2626", mk: "✗" },
    inconclusive: { bg: "#f1f5f9", bd: "#64748b", mk: "?" },
    unassessed:   { bg: "#f1f5f9", bd: "#94a3b8", mk: "·" }
  };
  var EDGE = { support: "#16a34a", prerequisite: "#2563eb", challenge: "#dc2626", synthesis: "#cbd5e1" };
  var NODE_W = 210, NODE_H = 62, VGAP = 16, PAD = 16, MAXROWS = 6, SUBW = 244, LAYERGAP = 30;

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function _nodes() { return document.getElementById("investigation-dag-nodes"); }
  function _edges() { return document.getElementById("investigation-dag-edges"); }
  function _shell() { return document.getElementById("investigation-dag-shell"); }
  function _vk(v) { return VERDICT[v] || VERDICT.unassessed; }

  function _ensureStyle() {
    if (document.getElementById("ag-style")) return;
    var css =
      ".ag-node{position:absolute;box-sizing:border-box;width:" + NODE_W + "px;min-height:" + NODE_H + "px;"
      + "border:1px solid #e2e8f0;border-left-width:4px;border-radius:9px;background:#fff;padding:7px 10px;"
      + "font-size:0.8em;line-height:1.25;box-shadow:0 1px 2px rgba(0,0,0,0.04);overflow:hidden}"
      + ".ag-node .ag-mk{font-weight:700;margin-right:4px}.ag-node .ag-ev{color:#94a3b8;font-size:0.82em;float:right}"
      + ".ag-narr{padding:8px 4px 24px;max-width:880px}"
      + ".ag-unit{display:flex;gap:10px;align-items:flex-start;padding:9px 12px;border:1px solid #eef2f7;"
      + "border-left-width:4px;border-radius:9px;background:#fff;margin:7px 0}"
      + ".ag-unit .ag-ord{color:#94a3b8;font-variant-numeric:tabular-nums;min-width:1.5em;text-align:right}"
      + ".ag-unit .ag-prop{flex:1;font-size:0.9em}.ag-unit .ag-badge{font-size:0.74em;padding:1px 7px;border-radius:999px;white-space:nowrap}"
      + ".ag-gap{margin:2px 0 2px 34px;padding:4px 10px;font-size:0.8em;color:#92400e;border-left:3px solid #f59e0b;background:#fffbeb;border-radius:0 7px 7px 0}"
      + ".ag-legend{position:absolute;right:10px;top:8px;font-size:0.72em;color:#64748b;background:rgba(255,255,255,0.92);"
      + "border:1px solid #eef2f7;border-radius:7px;padding:5px 8px;z-index:4;pointer-events:none}.ag-legend span{margin-right:9px;white-space:nowrap}"
      + ".ag-hint{position:absolute;left:10px;top:8px;font-size:0.72em;color:#94a3b8;background:rgba(255,255,255,0.9);"
      + "border:1px solid #eef2f7;border-radius:7px;padding:3px 7px;z-index:4;pointer-events:none}";
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

  // Pan / zoom (Argument only) — transform both hosts together.
  function _applyTransform() {
    var t = "translate(" + _tx + "px," + _ty + "px) scale(" + _scale + ")";
    var n = _nodes(), e = _edges();
    if (n) { n.style.transformOrigin = "0 0"; n.style.transform = t; }
    if (e) { e.style.transformOrigin = "0 0"; e.style.transform = t; }
  }
  function _resetTransform() { _scale = 1; _tx = 0; _ty = 0; _applyTransform(); }
  function _onWheel(ev) {
    ev.preventDefault();
    var sh = _shell(); if (!sh) return;
    var r = sh.getBoundingClientRect(), cx = ev.clientX - r.left, cy = ev.clientY - r.top;
    var ns = Math.max(0.3, Math.min(3, _scale * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
    _tx = cx - (cx - _tx) * (ns / _scale); _ty = cy - (cy - _ty) * (ns / _scale);
    _scale = ns; _applyTransform();
  }
  function _onDown(ev) { if (ev.button !== 0) return; _drag = { x: ev.clientX, y: ev.clientY, tx: _tx, ty: _ty }; }
  function _onMove(ev) { if (!_drag) return; _tx = _drag.tx + (ev.clientX - _drag.x); _ty = _drag.ty + (ev.clientY - _drag.y); _applyTransform(); }
  function _onUp() { _drag = null; }
  function _bindPanZoom() {
    if (_panBound) return; var sh = _shell(); if (!sh) return;
    sh.addEventListener("wheel", _onWheel, { passive: false });
    sh.addEventListener("mousedown", _onDown);
    window.addEventListener("mousemove", _onMove);
    window.addEventListener("mouseup", _onUp);
    sh.style.cursor = "grab"; _panBound = true;
  }
  function _unbindPanZoom() {
    if (!_panBound) return; var sh = _shell();
    if (sh) { sh.removeEventListener("wheel", _onWheel); sh.removeEventListener("mousedown", _onDown); sh.style.cursor = ""; }
    window.removeEventListener("mousemove", _onMove); window.removeEventListener("mouseup", _onUp);
    _panBound = false;
  }

  function _renderArgument(data) {
    var claims = data.claims || {}, edges = data.edges || [];
    var ids = Object.keys(claims).filter(function (c) { return !claims[c].arm_of; });
    var idset = {}; ids.forEach(function (i) { idset[i] = 1; });
    var layer = _layers(ids, edges);

    // group by layer; wrap a crowded layer into stacked sub-columns.
    var byLayer = {};
    ids.forEach(function (n) { (byLayer[layer[n]] = byLayer[layer[n]] || []).push(n); });
    var pos = {}, maxX = 0, maxY = 0, xCursor = PAD;
    Object.keys(byLayer).map(Number).sort(function (a, b) { return a - b; }).forEach(function (L) {
      var arr = byLayer[L].sort();
      var subCols = Math.max(1, Math.ceil(arr.length / MAXROWS));
      arr.forEach(function (n, i) {
        var x = xCursor + Math.floor(i / MAXROWS) * SUBW, y = PAD + (i % MAXROWS) * (NODE_H + VGAP);
        pos[n] = { x: x, y: y };
        if (x + NODE_W > maxX) maxX = x + NODE_W;
        if (y + NODE_H > maxY) maxY = y + NODE_H;
      });
      xCursor += subCols * SUBW + LAYERGAP;
    });

    var nodesHost = _nodes(), edgesSvg = _edges();
    nodesHost.innerHTML = ""; edgesSvg.innerHTML = "";
    nodesHost.style.height = (maxY + PAD) + "px"; nodesHost.style.minWidth = (maxX + PAD) + "px";
    edgesSvg.setAttribute("width", maxX + PAD); edgesSvg.setAttribute("height", maxY + PAD);
    edgesSvg.style.width = (maxX + PAD) + "px"; edgesSvg.style.height = (maxY + PAD) + "px";

    var seen = {};
    edges.forEach(function (e) {
      if (!idset[e.from] || !idset[e.to]) return;
      var a = pos[e.from], b = pos[e.to]; if (!a || !b) return;
      var k = e.from + "|" + e.to + "|" + e.type; if (seen[k]) return; seen[k] = 1;
      var x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2, mx = (x1 + x2) / 2;
      var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", "M" + x1 + "," + y1 + " C" + mx + "," + y1 + " " + mx + "," + y2 + " " + x2 + "," + y2);
      p.setAttribute("fill", "none"); p.setAttribute("stroke", EDGE[e.type] || "#cbd5e1");
      p.setAttribute("stroke-width", "1.5"); p.setAttribute("opacity", "0.8");
      if (e.type === "challenge") p.setAttribute("stroke-dasharray", "5,4");
      edgesSvg.appendChild(p);
    });

    ids.forEach(function (n) {
      var c = claims[n], vk = _vk(c.verdict), p = pos[n], prop = c.proposition || n;
      var short = prop.length > 120 ? prop.slice(0, 118) + "…" : prop;
      var div = document.createElement("div");
      div.className = "ag-node";
      div.style.left = p.x + "px"; div.style.top = p.y + "px";
      div.style.background = vk.bg; div.style.borderLeftColor = vk.bd; div.title = prop;
      div.innerHTML = '<span class="ag-mk" style="color:' + vk.bd + '">' + vk.mk + "</span>"
        + '<span class="ag-ev">' + (c.evidence_refs || []).length + " ev</span>" + _esc(short);
      nodesHost.appendChild(div);
    });

    _legend(nodesHost);
    var hint = document.createElement("div"); hint.className = "ag-hint";
    hint.textContent = "drag to pan · scroll to zoom";
    nodesHost.appendChild(hint);
  }

  function _renderNarrative(data) {
    _unbindPanZoom(); _resetTransform();
    var claims = data.claims || {}, nar = data.narrative || {}, units = nar.units || [], gaps = data.gaps || [];
    var gapByFromTo = {}; gaps.forEach(function (g) { gapByFromTo[g.from + "|" + g.to] = g; });
    var es = _edges(); if (es) { es.innerHTML = ""; es.style.height = "0"; es.removeAttribute("width"); }
    var host = _nodes(); host.innerHTML = ""; host.style.height = "auto"; host.style.minWidth = "0";
    var wrap = document.createElement("div"); wrap.className = "ag-narr";
    units.forEach(function (u, i) {
      var c = claims[u.claim_id] || {}, vk = _vk(u.verdict || c.verdict);
      var prop = u.proposition || c.proposition || u.claim_id, ev = (u.evidence_refs || c.evidence_refs || []).length;
      var unit = document.createElement("div"); unit.className = "ag-unit"; unit.style.borderLeftColor = vk.bd;
      unit.innerHTML = '<span class="ag-ord">' + (i + 1) + "</span>"
        + '<span class="ag-prop">' + _esc(prop) + "</span>"
        + '<span class="ag-badge" style="background:' + vk.bg + ";color:" + vk.bd + '">' + vk.mk + " " + _esc(u.verdict || c.verdict || "unassessed") + "</span>"
        + '<span class="ag-ord" style="min-width:3em;color:#94a3b8">' + ev + " ev</span>";
      wrap.appendChild(unit);
      if (i + 1 < units.length) {
        var g = gapByFromTo[u.claim_id + "|" + units[i + 1].claim_id];
        if (g) {
          var gd = document.createElement("div"); gd.className = "ag-gap";
          gd.textContent = "⚠ unsupported transition" + (g.verdict_escalation ? " ↑" : "") + " — " + (g.missing_evidence || "no supporting link");
          wrap.appendChild(gd);
        }
      }
    });
    if (!units.length) wrap.innerHTML = '<p class="empty-state" style="padding:24px">No claims to narrate yet.</p>';
    host.appendChild(wrap);
  }

  function _legend(host) {
    var items = [["#16a34a", "support"], ["#2563eb", "prerequisite"], ["#dc2626", "challenge"], ["#cbd5e1", "synthesis"]];
    var el = document.createElement("div"); el.className = "ag-legend";
    el.innerHTML = items.map(function (it) { return '<span><b style="color:' + it[0] + '">—</b> ' + _esc(it[1]) + "</span>"; }).join("");
    host.appendChild(el);
  }

  function _setActive(mode) {
    ["studies", "argument", "narrative"].forEach(function (m) {
      var b = document.getElementById("gview-" + m); if (b) b.classList.toggle("active", m === mode);
    });
  }

  function _fetch(slug) {
    try {
      if (window.DataSource && window.DataSource.loadArgumentOverlay) return window.DataSource.loadArgumentOverlay(slug);
      var base = window.__BASE_PATH__ || "";
      return fetch(base + "/api/argument-overlay?investigation=" + encodeURIComponent(slug)).then(function (r) { return r.ok ? r.json() : null; });
    } catch (e) { return Promise.resolve(null); }
  }

  function _draw(mode, data) { (mode === "argument" ? _renderArgument : _renderNarrative)(data); }

  window._reRenderGraphView = function () {
    if (MODE === "studies") return;
    if (_lastData && _lastSlug === (window._currentIsetSlug || "")) {
      try { _draw(MODE, _lastData); if (MODE === "argument") _applyTransform(); } catch (e) { /* keep view */ }
    }
  };

  window._setGraphView = function (mode) {
    MODE = mode; window._graphViewMode = mode; _setActive(mode);
    if (mode === "studies") {
      _unbindPanZoom(); _resetTransform();
      var es = _edges(); if (es) { es.innerHTML = ""; es.style.height = ""; es.style.transform = ""; }
      var nh = _nodes(); if (nh) { nh.style.height = ""; nh.style.minWidth = ""; nh.style.transform = ""; }
      if (window._restoreStudiesDag) window._restoreStudiesDag();
      if (nh && nh.querySelector(".ag-unit, .ag-node, .ag-legend")) nh.innerHTML = "";
      return;
    }
    var slug = window._currentIsetSlug || "";
    if (!slug) { window._setGraphView("studies"); return; }
    _ensureStyle();
    var done = function (data) {
      if (MODE !== mode) return;
      if (!data || data.available === false) { window._setGraphView("studies"); return; }
      _lastData = data; _lastSlug = slug;
      try {
        if (mode === "argument") { _resetTransform(); _draw(mode, data); _bindPanZoom(); }
        else { _draw(mode, data); }
      } catch (e) { window._setGraphView("studies"); }
    };
    if (_lastData && _lastSlug === slug) done(_lastData);
    else _fetch(slug).then(done).catch(function () { window._setGraphView("studies"); });
  };
})();
