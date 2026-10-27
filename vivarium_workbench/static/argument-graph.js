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
      + ".ag-empty{color:#94a3b8;padding:14px 2px;font-size:0.9em}"
      + ".ag-sec-lead{color:#64748b;font-size:0.84em;margin:0 0 10px}"
      + ".ag-gate{border:1px solid #eef2f7;border-left-width:4px;border-radius:9px;padding:9px 12px;margin:8px 0}"
      + ".ag-gate-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}"
      + ".ag-gate-id{font-weight:700;font-size:0.82em;background:#0f172a;color:#fff;border-radius:5px;padding:1px 7px}"
      + ".ag-gate-name{font-weight:600;flex:0 1 auto}"
      + ".ag-ext,.ag-branch{font-size:0.74em;color:#64748b;background:#f1f5f9;border-radius:999px;padding:1px 7px}"
      + ".ag-branch{color:#92400e;background:#fef3c7}"
      + ".ag-gate-crit{color:#475569;font-size:0.88em;margin:5px 0 0}"
      + ".ag-gate-mem{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px 10px;font-size:0.82em;color:#64748b}"
      + ".ag-mini{font-weight:700;margin-left:3px}"
      + ".ag-dgroup{margin-top:14px}.ag-dgroup h5{margin:0 0 6px;font-size:0.9em;color:#334155}"
      + ".ag-dcall{border-left:3px solid #cbd5e1;padding:6px 12px;margin:5px 0;background:#f8fafc;border-radius:0 7px 7px 0}"
      + ".ag-dc-narrative_gap{border-left-color:#f59e0b;background:#fffbeb}"
      + ".ag-dc-divergence{border-left-color:#8b5cf6;background:#f5f3ff}"
      + ".ag-dc-open_question{border-left-color:#0ea5e9;background:#f0f9ff}"
      + ".ag-dc-title{font-size:0.9em}.ag-dc-act{font-size:0.78em;color:#64748b;margin-top:2px}";
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

  function _miniBadge(v) {
    var k = _vk(v);
    return '<span class="ag-mini" style="color:' + k.bd + '" title="' + _esc(v || "unassessed") + '">' + k.mk + "</span>";
  }

  function _renderGates(host, data) {
    var gates = (data && data.gates) || [];
    if (!gates.length) { host.insertAdjacentHTML("beforeend", '<i class="ag-spine" hidden></i>'); return; }
    var out = '<div class="ag-spine"><div class="ag-sec-lead">Synthesis spine — each gate’s verdict is rolled up from its member studies’ claims.</div>';
    gates.forEach(function (g) {
      var ext = g.extends ? '<span class="ag-ext">extends ' + _esc(g.extends) + "</span>" : "";
      var branch = g.off_spine ? '<span class="ag-branch">branch</span>' : "";
      var mem = (g.members || []).map(function (m) { return '<span>' + _esc(m.slug) + _miniBadge(m.verdict) + "</span>"; }).join("");
      out += '<div class="ag-gate" style="border-left-color:' + _vk(g.verdict).bd + '">'
        + '<div class="ag-gate-head"><span class="ag-gate-id">' + _esc(g.id || "") + "</span>"
        + '<span class="ag-gate-name">' + _esc(g.name || "") + "</span>" + _badge(g.verdict) + ext + branch + "</div>"
        + (g.criterion ? '<div class="ag-gate-crit">' + _esc(g.criterion) + "</div>" : "")
        + (mem ? '<div class="ag-gate-mem">' + mem + "</div>" : "") + "</div>";
    });
    host.innerHTML = out + "</div>";
  }

  function _renderDecisions(host, data) {
    // authored decisions keep their interactive cockpit above; surface the derived items here.
    var calls = ((data && data.decisions) || []).filter(function (c) { return c.kind !== "decision"; });
    if (!calls.length) { host.innerHTML = '<div class="ag-dec-empty" hidden></div>'; return; }
    var groups = {
      narrative_gap: { label: "Narrative gaps — unsupported transitions", items: [] },
      divergence: { label: "Verdict divergences — authored vs. coded roll-up", items: [] },
      open_question: { label: "Open questions — unassessed / inconclusive claims", items: [] }
    };
    calls.forEach(function (c) { if (groups[c.kind]) groups[c.kind].items.push(c); });
    var out = '<div class="ag-sec-lead" style="margin-top:18px">Derived open items — surfaced from the argument graph and the narrative audit.</div>';
    Object.keys(groups).forEach(function (k) {
      var g = groups[k]; if (!g.items.length) return;
      out += '<div class="ag-dgroup"><h5>' + _esc(g.label) + " (" + g.items.length + ")</h5>";
      g.items.forEach(function (c) {
        out += '<div class="ag-dcall ag-dc-' + k + '"><div class="ag-dc-title">' + _esc(c.title) + "</div>"
          + '<div class="ag-dc-act">→ ' + _esc(c.action) + "</div></div>";
      });
      out += "</div>";
    });
    host.innerHTML = out;
  }

  function _tick() {
    var m = {
      nar: document.getElementById("inv-narrative-mount"),
      ver: document.getElementById("inv-verdict-claims-mount"),
      plan: document.getElementById("inv-plan-mount"),
      dec: document.getElementById("inv-decisions-mount")
    };
    if (!m.nar && !m.ver && !m.plan && !m.dec) return;
    var slug = window._currentIsetSlug || "";
    if (!slug) return;
    var unfilled = (m.nar && !m.nar.querySelector(".ag-unit, .ag-empty"))
      || (m.ver && !m.ver.querySelector(".ag-claim, .ag-empty"))
      || (m.plan && !m.plan.querySelector(".ag-spine"))
      || (m.dec && !m.dec.querySelector(".ag-dcall, .ag-dec-empty"));
    if (slug === _slug && !unfilled) return;
    if (_busy) return;
    _busy = true;
    _fetch(slug).then(function (data) {
      _busy = false; _slug = slug; _ensureStyle();
      var avail = data && data.available !== false;
      if (m.nar) { if (avail) _renderNarrative(m.nar, data); else m.nar.innerHTML = ""; }
      if (m.ver) { if (avail) _renderClaims(m.ver, data); else m.ver.innerHTML = ""; }
      if (m.plan && avail) _renderGates(m.plan, data);      // keeps _planHtml fallback otherwise
      if (m.dec) { if (avail) _renderDecisions(m.dec, data); else m.dec.innerHTML = ""; }
    }).catch(function () { _busy = false; });
  }

  setInterval(_tick, 700);
  if (document.readyState !== "loading") _tick();
  else document.addEventListener("DOMContentLoaded", _tick);
})();

/*
 * discovery-panel — the discovery cycle's "what to do next" on the investigation
 * Decisions tab (#inv-discovery-mount). Fed by /api/investigation-discovery:
 * value-ranked opportunities, the productivity diagnosis, the gated Decide
 * execution plan, and expert-gated study suggestions. A poller fills the mount
 * when it appears; an empty {} (older viva_superpowers, no discovery engine)
 * leaves it blank. Mirrors the argument-overlay poller above.
 */
(function () {
  "use strict";
  var _busy = false;
  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function _num(x) { return (typeof x === "number" && isFinite(x)) ? (Math.round(x * 100) / 100) : x; }
  function _strip(s) { return String(s || "").replace(/^claim:/, ""); }
  function _style() {
    if (document.getElementById("disc-style")) return;
    var css =
      ".disc-wrap{font-size:0.92em;margin:0 0 18px}"
      + ".disc-wrap h4{margin:0 0 4px;font-size:0.98em;color:#0f172a}"
      + ".disc-sub{color:#64748b;font-size:0.84em;margin:12px 0 6px;font-weight:600}"
      + ".disc-chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 4px}"
      + ".disc-chip{font-size:0.76em;padding:1px 9px;border-radius:999px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0}"
      + ".disc-chip.flag{background:#fef3c7;color:#92400e;border-color:#fde68a}"
      + ".disc-chip.ok{background:#dcfce7;color:#166534;border-color:#bbf7d0}"
      + ".disc-tbl{width:100%;border-collapse:collapse;font-size:0.86em;margin:2px 0 6px}"
      + ".disc-tbl th{text-align:left;color:#64748b;font-weight:600;border-bottom:1px solid #e2e8f0;padding:4px 8px}"
      + ".disc-tbl td{border-bottom:1px solid #f1f5f9;padding:5px 8px;vertical-align:top}"
      + ".disc-tbl td.sc{font-variant-numeric:tabular-nums;color:#0f172a;white-space:nowrap}"
      + ".disc-cons{font-size:0.73em;padding:1px 7px;border-radius:999px;white-space:nowrap}"
      + ".disc-cons.safe{background:#dcfce7;color:#166534}"
      + ".disc-cons.simulation,.disc-cons.simulationstudy{background:#dbeafe;color:#1e40af}"
      + ".disc-cons.review{background:#fef3c7;color:#92400e}"
      + ".disc-cons.humanonly{background:#f3e8ff;color:#6b21a8}"
      + ".disc-cmd{font-family:ui-monospace,Menlo,monospace;font-size:0.8em;color:#475569;background:#f8fafc;padding:1px 6px;border-radius:5px;word-break:break-all}";
    var el = document.createElement("style"); el.id = "disc-style"; el.textContent = css;
    document.head.appendChild(el);
  }
  function _render(host, d) {
    if (!d || (d.frontier_size == null && !(d.opportunities || []).length)) { host.innerHTML = ""; return; }
    var H = ['<div class="disc-wrap"><h4>Discovery — what to do next</h4>'];
    var diag = d.diagnosis || {}, flags = diag.flags || [], chips = [];
    chips.push('<span class="disc-chip ' + (d.closed ? "ok" : "") + '">' + (d.closed ? "closed" : "open")
      + " · " + _esc(d.frontier_size) + " open</span>");
    flags.forEach(function (f) { chips.push('<span class="disc-chip flag">' + _esc(f) + "</span>"); });
    if (diag.validator_pass_rate != null) chips.push('<span class="disc-chip">validator pass ' + Math.round(diag.validator_pass_rate * 100) + "%</span>");
    H.push('<div class="disc-chips">' + chips.join("") + "</div>");
    var opps = d.opportunities || [];
    if (opps.length) {
      H.push('<div class="disc-sub">Most valuable to investigate</div>');
      H.push('<table class="disc-tbl"><thead><tr><th>score</th><th>action</th><th>target</th></tr></thead><tbody>');
      opps.slice(0, 8).forEach(function (o) {
        H.push('<tr><td class="sc">' + _esc(_num(o.score)) + "</td><td>" + _esc(o.action)
          + "</td><td>" + _esc(_strip(o.source)) + "</td></tr>");
      });
      H.push("</tbody></table>");
    }
    var plan = d.execution_plan || [];
    if (plan && plan.length) {
      H.push('<div class="disc-sub">Decide — the next action &amp; how it runs (gated)</div>');
      H.push('<table class="disc-tbl"><thead><tr><th>action</th><th>target</th><th>consequence</th><th>command</th></tr></thead><tbody>');
      plan.slice(0, 8).forEach(function (p) {
        var cons = String(p.consequence || "").replace(/[^a-z]/g, "");
        H.push("<tr><td>" + _esc(p.action) + "</td><td>" + _esc(_strip(p.source))
          + '</td><td><span class="disc-cons ' + cons + '">' + _esc(p.consequence) + "</span></td><td>"
          + (p.command ? '<span class="disc-cmd">' + _esc(p.command) + "</span>" : "—") + "</td></tr>");
      });
      H.push("</tbody></table>");
    }
    var sugg = (d.suggestions || []).filter(function (s) { return s.status === "awaiting_expert"; });
    if (sugg.length) {
      H.push('<div class="disc-sub">Suggested studies (awaiting expert input)</div><ul class="ag-list">');
      sugg.slice(0, 6).forEach(function (s) {
        H.push("<li>on <b>" + _esc(s.parent) + "</b>: " + _esc(String(s.needs || "").slice(0, 130)) + "</li>");
      });
      H.push("</ul>");
    }
    H.push("</div>");
    host.innerHTML = H.join("");
  }
  function _tick() {
    var host = document.getElementById("inv-discovery-mount");
    if (!host) return;
    var slug = window._currentIsetSlug || "";
    if (!slug || host.getAttribute("data-disc") === slug) return;  // no mount, or already handled this slug
    if (_busy) return;
    _busy = true;
    var base = window.__BASE_PATH__ || "";
    fetch(base + "/api/investigation-discovery/" + encodeURIComponent(slug))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { _busy = false; _style(); _render(host, data || {}); host.setAttribute("data-disc", slug); })
      .catch(function () { _busy = false; });
  }
  setInterval(_tick, 700);
  if (document.readyState !== "loading") _tick();
  else document.addEventListener("DOMContentLoaded", _tick);
})();
