/*
 * argument-graph.js — RFC-0005 argument/narrative as investigation-tab content.
 *
 * (Formerly a graph-view toggle; the claim graph proved hard to read, so the
 * synthesis/narrative products now live in tabs, as readable documents.)
 *
 *   - Narrative tab (#inv-narrative-mount): the ordered reading — claims by
 *     explanatory dependence, full text, verdict badges, audit gaps inline,
 *     plus limitations and open questions.
 *   - Verdict tab (#inv-verdict-claims-mount): the claims list — each claim, its
 *     verdict, and what it builds on / is supported by / is challenged by.
 *
 * Fed by /api/argument-overlay. A poller fills the mounts whenever they appear
 * for the current investigation; tolerant — an unavailable derivation layer just
 * leaves a quiet empty state.
 */
(function () {
  "use strict";

  var _slug = null, _busy = false;
  var VERDICT = {
    supported:    { bg: "#dcfce7", bd: "#16a34a", mk: "✓" },
    qualified:    { bg: "#fef3c7", bd: "#d97706", mk: "≈" },
    refuted:      { bg: "#fee2e2", bd: "#dc2626", mk: "✗" },
    inconclusive: { bg: "#f1f5f9", bd: "#64748b", mk: "?" },
    unassessed:   { bg: "#f1f5f9", bd: "#94a3b8", mk: "·" }
  };

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function _vk(v) { return VERDICT[v] || VERDICT.unassessed; }
  function _slugOf(cid) { return String(cid || "").replace(/^claim:/, ""); }
  function _badge(v) {
    var k = _vk(v);
    return '<span class="ag-badge" style="background:' + k.bg + ";color:" + k.bd + '">' + k.mk + " " + _esc(v || "unassessed") + "</span>";
  }

  function _ensureStyle() {
    if (document.getElementById("ag-style")) return;
    var css =
      ".inv-narrative-mount,.inv-claims-mount{font-size:0.92em}"
      + ".ag-unit{display:flex;gap:12px;align-items:flex-start;padding:11px 0;border-top:1px solid #f1f5f9}"
      + ".ag-unit:first-child{border-top:none}"
      + ".ag-ord{color:#94a3b8;font-variant-numeric:tabular-nums;min-width:1.6em;text-align:right;padding-top:2px}"
      + ".ag-body{flex:1}.ag-prop{line-height:1.45}"
      + ".ag-meta{margin-top:4px;color:#94a3b8;font-size:0.82em}"
      + ".ag-badge{font-size:0.74em;padding:1px 8px;border-radius:999px;white-space:nowrap}"
      + ".ag-gap{margin:6px 0 6px 2.6em;padding:6px 12px;font-size:0.86em;color:#92400e;border-left:3px solid #f59e0b;background:#fffbeb;border-radius:0 8px 8px 0}"
      + ".ag-sec{margin-top:18px}.ag-sec h5{margin:0 0 7px;font-size:0.92em;color:#334155}"
      + ".ag-list{margin:0;padding-left:18px;color:#475569;font-size:0.9em;display:grid;gap:5px}"
      + ".ag-claim{padding:11px 0;border-top:1px solid #f1f5f9}.ag-claim:first-child{border-top:none}"
      + ".ag-claim-head{display:flex;gap:10px;align-items:flex-start}.ag-claim-head .ag-prop{flex:1;line-height:1.45}"
      + ".ag-rel{margin-top:5px;display:flex;flex-wrap:wrap;gap:6px}"
      + ".ag-rel .chip{font-size:0.76em;padding:1px 8px;border-radius:999px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0}"
      + ".ag-rel .chip b{font-weight:600;color:#334155}"
      + ".ag-empty{color:#94a3b8;padding:14px 2px;font-size:0.9em}";
    var el = document.createElement("style"); el.id = "ag-style"; el.textContent = css;
    document.head.appendChild(el);
  }

  function _fetch(slug) {
    try {
      if (window.DataSource && window.DataSource.loadArgumentOverlay) return window.DataSource.loadArgumentOverlay(slug);
      var base = window.__BASE_PATH__ || "";
      return fetch(base + "/api/argument-overlay?investigation=" + encodeURIComponent(slug)).then(function (r) { return r.ok ? r.json() : null; });
    } catch (e) { return Promise.resolve(null); }
  }

  function _relations(claims, edges) {
    // incoming edges per claim -> what it builds on / is supported / challenged by
    var rel = {};
    edges.forEach(function (e) {
      if (!claims[e.to] || !claims[e.from] || claims[e.to].arm_of || claims[e.from].arm_of) return;
      (rel[e.to] = rel[e.to] || { prerequisite: [], support: [], challenge: [] });
      if (e.type === "prerequisite") rel[e.to].prerequisite.push(e.from);
      else if (e.type === "support") rel[e.to].support.push(e.from);
      else if (e.type === "challenge") rel[e.to].challenge.push(e.from);
    });
    return rel;
  }

  function _renderNarrative(host, data) {
    var claims = (data && data.claims) || {}, nar = (data && data.narrative) || {}, units = nar.units || [], gaps = (data && data.gaps) || [];
    if (!units.length) { host.innerHTML = '<div class="ag-empty">No narrative derived yet.</div>'; return; }
    var gapBy = {}; gaps.forEach(function (g) { gapBy[g.from + "|" + g.to] = g; });
    var out = "";
    units.forEach(function (u, i) {
      var c = claims[u.claim_id] || {}, prop = u.proposition || c.proposition || u.claim_id;
      var ev = (u.evidence_refs || c.evidence_refs || []).length;
      var scope = c.scope ? ' · scope: ' + _esc(c.scope) : "";
      out += '<div class="ag-unit"><div class="ag-ord">' + (i + 1) + "</div>"
        + '<div class="ag-body"><div class="ag-prop">' + _esc(prop) + "</div>"
        + '<div class="ag-meta">' + _badge(u.verdict || c.verdict) + " &nbsp; " + ev + " evidence refs" + scope + "</div></div></div>";
      if (i + 1 < units.length) {
        var g = gapBy[u.claim_id + "|" + units[i + 1].claim_id];
        if (g) out += '<div class="ag-gap">⚠ unsupported transition' + (g.verdict_escalation ? " ↑" : "")
          + " — " + _esc(g.missing_evidence || "no supporting link") + "</div>";
      }
    });
    var lim = (nar.limitations || []), oq = (nar.open_questions || []);
    if (lim.length) out += '<div class="ag-sec"><h5>Limitations</h5><ul class="ag-list">' + lim.map(function (l) { return "<li>" + _esc(l) + "</li>"; }).join("") + "</ul></div>";
    if (oq.length) out += '<div class="ag-sec"><h5>Open questions</h5><ul class="ag-list">' + oq.map(function (q) { return "<li>" + _esc(q.proposition || q.claim_id) + "</li>"; }).join("") + "</ul></div>";
    host.innerHTML = out;
  }

  function _renderClaims(host, data) {
    var claims = (data && data.claims) || {}, edges = (data && data.edges) || [];
    var ids = Object.keys(claims).filter(function (c) { return !claims[c].arm_of; });
    if (!ids.length) { host.innerHTML = '<div class="ag-empty">No claims derived yet.</div>'; return; }
    var rel = _relations(claims, edges);
    var order = { supported: 0, qualified: 1, inconclusive: 2, unassessed: 3, refuted: 4 };
    ids.sort(function (a, b) { return (order[claims[a].verdict] || 9) - (order[claims[b].verdict] || 9) || a.localeCompare(b); });
    var out = '<div class="ag-sec"><h5>Claims &amp; evidence (' + ids.length + ")</h5>";
    ids.forEach(function (id) {
      var c = claims[id], r = rel[id] || {}, prop = c.proposition || id;
      var chips = "";
      function chip(label, arr) {
        if (arr && arr.length) chips += '<span class="chip"><b>' + label + ":</b> " + arr.map(function (x) { return _esc(_slugOf(x)); }).join(", ") + "</span>";
      }
      chip("builds on", r.prerequisite); chip("supported by", r.support); chip("challenged by", r.challenge);
      out += '<div class="ag-claim"><div class="ag-claim-head">' + _badge(c.verdict)
        + '<div class="ag-prop">' + _esc(prop) + "</div></div>"
        + (chips ? '<div class="ag-rel">' + chips + "</div>" : "") + "</div>";
    });
    host.innerHTML = out + "</div>";
  }

  function _tick() {
    var nar = document.getElementById("inv-narrative-mount");
    var ver = document.getElementById("inv-verdict-claims-mount");
    if (!nar && !ver) return;
    var slug = window._currentIsetSlug || "";
    if (!slug) return;
    var narEmpty = nar && !nar.querySelector(".ag-unit, .ag-empty");
    var verEmpty = ver && !ver.querySelector(".ag-claim, .ag-empty");
    if (slug === _slug && !narEmpty && !verEmpty) return;      // already filled
    if (_busy) return;
    _busy = true;
    _fetch(slug).then(function (data) {
      _busy = false; _slug = slug; _ensureStyle();
      var avail = data && data.available !== false;
      if (nar) { if (avail) _renderNarrative(nar, data); else nar.innerHTML = ""; }
      if (ver) { if (avail) _renderClaims(ver, data); else ver.innerHTML = ""; }
    }).catch(function () { _busy = false; });
  }

  setInterval(_tick, 700);
  if (document.readyState !== "loading") _tick();
  else document.addEventListener("DOMContentLoaded", _tick);
})();
