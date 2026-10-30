/* study-feedback.js — inline expert-feedback capture engine.
 *
 * ONE config-driven engine shared by two surfaces:
 *   1. the study detail page (loaded as an external /study-feedback.js) — a
 *      default config decorates every region across the six study tabs;
 *   2. the investigation report (this same source inlined into the
 *      self-contained downloadable HTML) — which sets window.__VIVA_FB_CONFIG__
 *      before this runs, with its own region selectors.
 *
 * A reviewer hovers any region, clicks the corner 💬, leaves a disposition
 * (👍 / ✏️) and a note. Notes collect into ONE YAML. The floating export
 * button appears in the bottom-right ONLY once something is saved, showing the
 * count; it opens a tray with Export / Copy and (live only) Save to workspace.
 *
 * Works identically on the LIVE workbench and the READ-ONLY published bundle /
 * downloaded report: collection + export are pure client-side (localStorage);
 * only "Save to workspace" needs the backend and hides in snapshot mode.
 *
 * The exported YAML is the canonical ingest shape read by
 * viva_superpowers.feedback_import / feedback_tracking / feedback_actions and
 * the /viva-* responder skills:
 *
 *   meta: {investigation, study?, reviewer, generated_at, source}
 *   annotations:
 *     <section-id>:
 *       - {author, text, ts, disposition, region, group}
 *
 * Section ids prefixed `study-<slug>` attribute to that study
 * (study_feedback_actions matches `study-<slug>` and `study-<slug>-*`);
 * investigation-level sections use other prefixes and stay as report-level
 * notes.
 */
(function () {
  "use strict";

  if (window.__vivaStudyFeedbackLoaded) return;
  window.__vivaStudyFeedbackLoaded = true;

  // ── generic helpers ──────────────────────────────────────────────────────────
  function val(x) {
    return typeof x === "function" ? x() : x;
  }
  function isSnapshot() {
    try {
      if (document.body && document.body.classList.contains("snapshot")) return true;
      var c = window.__DASH_CONFIG__;
      if (c && c.mode === "snapshot") return true;
    } catch (e) {}
    return false;
  }
  function nowIso() {
    return new Date().toISOString().replace(/\.\d+Z$/, "Z");
  }
  function slugify(s) {
    return (
      String(s || "")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 48) || "x"
    );
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function cssEscape(s) {
    return String(s).replace(/"/g, '\\"');
  }
  // The study detail page renders inside a full-height iframe in the SPA (the
  // outer page scrolls, not the iframe), so position:fixed would pin the
  // floating button to the iframe's document — i.e. the bottom of the content,
  // not the screen. When embedded in a same-origin iframe, mount the viewport
  // chrome (FAB + tray + toast) into the TOP window so it's fixed to the screen.
  // Standalone pages (direct study page, report, published bundle) are not
  // framed, so topDoc() === document and behavior is unchanged.
  function topDoc() {
    try {
      if (window.self !== window.top && window.top && window.top.document) return window.top.document;
    } catch (e) {}
    return document;
  }
  function headingText(el) {
    var h = el.querySelector(
      "h1,h2,h3,h4,.card-h,.card-title,.figure-title,.finding-statement,.expert-question-title,.di-card-title,.followup-title,.sec-h,summary"
    );
    var t = (h && h.textContent) || el.getAttribute("aria-label") || "";
    return t.replace(/\s+/g, " ").trim().slice(0, 80);
  }
  function textKey(el, fallback) {
    return slugify(headingText(el) || fallback || "");
  }

  function reviewerName() {
    try {
      return localStorage.getItem("viva.fb.reviewer") || "";
    } catch (e) {
      return "";
    }
  }
  function setReviewerName(v) {
    try {
      localStorage.setItem("viva.fb.reviewer", v || "");
    } catch (e) {}
  }

  // ── default (study-detail) config ────────────────────────────────────────────
  function studySlug() {
    return (
      (window._study && (window._study.name || window._study.slug)) ||
      window._studyName ||
      "study"
    );
  }
  function studyInvestigation() {
    var s = window._study || {};
    var remembered = "";
    try {
      remembered = localStorage.getItem("viva.fb.inv." + slugify(studySlug())) || "";
    } catch (e) {}
    var cand =
      window.__STUDY_INVESTIGATION__ ||
      remembered ||
      s.investigation ||
      (Array.isArray(s.investigations) && s.investigations[0]) ||
      (window.__DASH_CONFIG__ && window.__DASH_CONFIG__.investigation) ||
      "";
    if (cand && typeof cand === "object") cand = cand.slug || cand.name || "";
    return cand || "";
  }

  var STUDY_TABS = {
    overview: { panel: "#panel-overview", name: "Overview", regions: [
      { sel: ".finding-card", key: function (el) { var i = el.querySelector(".finding-id"); return "finding-" + slugify((i && i.textContent) || textKey(el, "finding")); }, label: function (el) { var i = el.querySelector(".finding-id"); return "Finding " + (((i && i.textContent) || "").trim() || headingText(el)); } },
      { sel: ".ctr-block", key: function (el) { return "block-" + textKey(el, "block"); }, label: function (el) { return headingText(el) || "Overview block"; } },
      { sel: ".ov-src", key: function (el) { return "source-" + slugify((el.id || "").replace(/^sref-/, "") || textKey(el, "source")); }, label: function (el) { return "Source " + ((el.id || "").replace(/^sref-/, "") || headingText(el)); } },
    ] },
    visualize: { panel: "#panel-visualize", name: "Visualizations", regions: [
      { sel: ".figure-card", key: function (el) { var t = el.querySelector(".figure-title"); var r = el.querySelector("[data-run-id]"); return "figure-" + slugify(((t && t.textContent) || "") + "-" + ((r && r.getAttribute("data-run-id")) || "")); }, label: function (el) { var t = el.querySelector(".figure-title"); return "Figure: " + (((t && t.textContent) || "").trim() || "untitled"); } },
    ] },
    compose: { panel: "#panel-compose", name: "Model", regions: [
      { sel: ".cond-block", key: function (el) { return "model-" + slugify(el.getAttribute("data-model-composite") || textKey(el, "model")); }, label: function (el) { return "Model: " + (el.getAttribute("data-model-composite") || headingText(el) || "composite"); } },
      { sel: "#model-composite-cards > *", key: function (el) { return "composite-" + textKey(el, "composite"); }, label: function (el) { return "Composite: " + (headingText(el) || "card"); } },
      { sel: "#model-readouts-section", key: function () { return "readouts"; }, label: function () { return "Model readouts"; } },
    ] },
    simulate: { panel: "#panel-simulate", name: "Simulations", regions: [
      { sel: "#study-sim-table", key: function () { return "runs"; }, label: function () { return "Simulation runs"; } },
      { sel: "#results-summary", key: function () { return "results-summary"; }, label: function () { return "Results summary"; } },
      { sel: "#gate-results", key: function () { return "gate-results"; }, label: function () { return "Gate results"; } },
      { sel: "#study-analyses-list", key: function () { return "analyses"; }, label: function () { return "Analyses"; } },
    ] },
    audit: { panel: "#panel-audit", name: "Audit", regions: [
      { sel: ".expected-behavior-item", key: function (el) { return "test-" + slugify((el.id || "").replace(/^bt-/, "") || el.getAttribute("data-pytest-node") || textKey(el, "test")); }, label: function (el) { return "Test: " + ((el.id || "").replace(/^bt-/, "") || el.getAttribute("data-pytest-node") || headingText(el)); } },
      { sel: ".report-card-block", key: function (el) { return "report-card-" + textKey(el, "card"); }, label: function (el) { return "Report card: " + (headingText(el) || "card"); } },
      { sel: ".audit-check-item", key: function (el) { var c = el.querySelector("code"); return "check-" + slugify((c && c.textContent) || textKey(el, "check")); }, label: function (el) { var c = el.querySelector("code"); return "Check: " + (((c && c.textContent) || "").trim() || headingText(el)); } },
    ] },
    conclusions: { panel: "#panel-conclusions", name: "Decisions", regions: [
      { sel: "[data-narrative-card='conclusion_verdicts']", key: function () { return "verdicts"; }, label: function () { return "Three-track verdict"; } },
      { sel: ".expert-question-card", key: function (el) { var i = el.querySelector(".expert-question-id"); return "debt-" + slugify((i && i.textContent) || textKey(el, "debt")); }, label: function (el) { var i = el.querySelector(".expert-question-id"); return "Open debt " + (((i && i.textContent) || "").trim() || headingText(el)); } },
      { sel: ".di-card", key: function (el) { return "implication-" + textKey(el, "implication"); }, label: function (el) { return "Implication: " + (headingText(el) || "card"); } },
      { sel: ".followup-card", key: function (el) { return "followup-" + textKey(el, "followup"); }, label: function (el) { return "Follow-up: " + (headingText(el) || "study"); } },
    ] },
  };

  function defaultStudyConfig() {
    var slug = studySlug();
    var groups = [];
    var regions = [];
    Object.keys(STUDY_TABS).forEach(function (tab) {
      var t = STUDY_TABS[tab];
      groups.push({ id: tab, name: t.name });
      t.regions.forEach(function (cfg) {
        regions.push({
          scope: t.panel,
          sel: cfg.sel,
          group: tab,
          sid: (function (c, tabId) {
            return function (el) {
              return "study-" + slugify(slug) + "-" + tabId + "-" + c.key(el);
            };
          })(cfg, tab),
          label: cfg.label,
        });
      });
    });
    return {
      context: "study",
      meta: { investigation: studyInvestigation, study: slug },
      storeId: "study." + slugify(slug),
      title: "Feedback on " + slug,
      allowInvestigationEdit: true,
      groups: groups,
      regions: regions,
      groupSwitch: function (gid) {
        if (typeof window._setStudyTab === "function") {
          try { window._setStudyTab(gid); } catch (e) {}
        }
      },
    };
  }

  var CONFIG = window.__VIVA_FB_CONFIG__ || defaultStudyConfig();

  function metaInvestigation() {
    return val(CONFIG.meta && CONFIG.meta.investigation) || "";
  }
  function metaStudy() {
    return val(CONFIG.meta && CONFIG.meta.study) || "";
  }
  function groupName(gid) {
    var g = (CONFIG.groups || []).filter(function (x) { return x.id === gid; })[0];
    return (g && g.name) || gid || "Other";
  }

  // ── store (localStorage) ──────────────────────────────────────────────────────
  // Each surface (a study, the report, the SPA investigation view) persists its
  // own store keyed `viva.fb.store.<ws>.<storeId>`, with meta under
  // `viva.fb.meta.<ws>.<storeId>`. localStorage is shared across same-origin
  // frames, so the single shared button AGGREGATES every store in the workspace
  // — one count, one export ("all at once"), even when a study detail iframe and
  // the SPA investigation view are on screen together.
  // FB (this surface) = { sectionId: {note, disposition, label, group, ts} }
  var FB = {};
  // Guarantee unique section ids by checking the LIVE DOM — removed/re-rendered
  // elements don't inflate the suffix, so ids stay stable across re-renders
  // (only genuine same-view duplicates, e.g. Question + Hypothesis, get a -2).
  function uniqueSid(sid) {
    var base = sid, n = 1;
    while (document.querySelector('[data-fb-sid="' + cssEscape(sid) + '"]')) {
      n++;
      sid = base + "-" + n;
    }
    return sid;
  }
  var STORE_PREFIX = "viva.fb.store.";
  var META_PREFIX = "viva.fb.meta.";
  function WS() {
    return slugify((window.__DASH_CONFIG__ && window.__DASH_CONFIG__.workspace) || "");
  }
  function storeId() {
    return val(CONFIG.storeId) || "x";
  }
  function storeKey() {
    return STORE_PREFIX + WS() + "." + storeId();
  }
  function metaKey() {
    return META_PREFIX + WS() + "." + storeId();
  }
  var _loadedKey = null;
  function loadStore() {
    _loadedKey = storeKey();
    try {
      FB = JSON.parse(localStorage.getItem(_loadedKey) || "{}") || {};
    } catch (e) {
      FB = {};
    }
  }
  // The SPA investigation instance is long-lived across investigation switches
  // (storeId is a function of the current slug) — reload FB when it changes.
  function ensureStore() {
    if (storeKey() !== _loadedKey) loadStore();
  }
  function persistMeta() {
    try {
      localStorage.setItem(metaKey(), JSON.stringify({
        investigation: metaInvestigation(),
        study: metaStudy(),
        title: val(CONFIG.title) || "",
        context: CONFIG.context || "",
        noSave: !!CONFIG.noSave,
        storeId: storeId(),
      }));
    } catch (e) {}
  }
  function saveStore() {
    try {
      localStorage.setItem(storeKey(), JSON.stringify(FB));
    } catch (e) {}
    persistMeta();
    refreshChrome();
  }
  function setFB(key, patch) {
    FB[key] = Object.assign({}, FB[key] || {}, patch);
    if (!FB[key].note && !FB[key].disposition) delete FB[key];
    saveStore();
  }
  // Aggregate every store for this workspace (investigation first, then studies).
  function allStores() {
    var pfx = STORE_PREFIX + WS() + ".";
    var out = [];
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (!k || k.indexOf(pfx) !== 0) continue;
        var entries = {};
        try { entries = JSON.parse(localStorage.getItem(k) || "{}") || {}; } catch (e) { entries = {}; }
        if (!Object.keys(entries).length) continue;
        var storeId = k.slice(pfx.length);
        var meta = {};
        try { meta = JSON.parse(localStorage.getItem(META_PREFIX + WS() + "." + storeId) || "{}") || {}; } catch (e) {}
        out.push({ storeId: storeId, meta: meta, entries: entries });
      }
    } catch (e) {}
    out.sort(function (a, b) {
      var ai = a.meta.context === "investigation" ? 0 : 1;
      var bi = b.meta.context === "investigation" ? 0 : 1;
      if (ai !== bi) return ai - bi;
      return String(a.meta.title || a.storeId).localeCompare(String(b.meta.title || b.storeId));
    });
    return out;
  }
  function totalCount() {
    return allStores().reduce(function (n, s) { return n + Object.keys(s.entries).length; }, 0);
  }
  function pickInvestigation() {
    var stores = allStores();
    for (var i = 0; i < stores.length; i++) if (stores[i].meta.context === "investigation" && stores[i].meta.investigation) return stores[i].meta.investigation;
    for (var j = 0; j < stores.length; j++) if (stores[j].meta.investigation) return stores[j].meta.investigation;
    return metaInvestigation();
  }
  function anySaveable() {
    return allStores().some(function (s) { return !s.meta.noSave; });
  }

  // ── decoration: hover 💬 corners on every region ──────────────────────────────
  function decorateRegion(el, region) {
    if (!el || el.__fbDecorated) return;
    if (el.closest(".fb-corner") || el.closest(".fb-pop") || el.closest("#fb-tray")) return;
    el.__fbDecorated = true;
    var sid, label, group;
    try { sid = region.sid(el); } catch (e) { sid = "x"; }
    sid = uniqueSid(sid);
    try { label = region.label(el); } catch (e) { label = ""; }
    group = typeof region.group === "function" ? region.group(el) : region.group || "";
    el.classList.add("fb-region");
    el.setAttribute("data-fb-sid", sid);
    el.setAttribute("data-fb-label", label || sid);
    el.setAttribute("data-fb-group", group);
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fb-corner";
    btn.setAttribute("data-fb-sid", sid);
    btn.setAttribute("title", "Leave feedback on: " + (label || sid));
    btn.setAttribute("aria-label", "Leave feedback on " + (label || sid));
    btn.textContent = "💬";
    el.appendChild(btn);
    markCorner(sid);
  }
  function decorateAll() {
    ensureStore();
    (CONFIG.regions || []).forEach(function (region) {
      var roots = region.scope ? document.querySelectorAll(region.scope) : [document];
      for (var r = 0; r < roots.length; r++) {
        var nodes = roots[r].querySelectorAll(region.sel);
        for (var i = 0; i < nodes.length; i++) decorateRegion(nodes[i], region);
      }
    });
  }
  function markCorner(sid) {
    var has = !!FB[sid];
    document.querySelectorAll('.fb-corner[data-fb-sid="' + cssEscape(sid) + '"]').forEach(function (b) {
      b.classList.toggle("has", has);
      b.textContent = has ? "📝" : "💬";
    });
    var reg = document.querySelector('.fb-region[data-fb-sid="' + cssEscape(sid) + '"]');
    if (reg) reg.classList.toggle("fb-has", has);
  }

  // ── inline editor popover ──────────────────────────────────────────────────────
  var curPop = null;
  function closePop() {
    if (curPop) { curPop.remove(); curPop = null; }
  }
  function openEditor(sid, label, group, anchorBtn) {
    closePop();
    var f = FB[sid] || {};
    var pop = document.createElement("div");
    pop.className = "fb-pop";
    pop.innerHTML =
      '<div class="fb-pop-h">' + esc(label) + "</div>" +
      '<div class="fb-disp">' +
      '<button type="button" data-disp="ok" class="' + (f.disposition === "ok" ? "on-ok" : "") + '">👍 Looks right</button>' +
      '<button type="button" data-disp="revise" class="' + (f.disposition === "revise" ? "on-rev" : "") + '">✏️ Needs revision</button>' +
      "</div>" +
      '<textarea class="fb-note" rows="4" placeholder="Note for the team / AI agent…">' + esc(f.note || "") + "</textarea>" +
      '<div class="fb-pop-foot">' +
      '<button type="button" class="fb-clear">Clear</button>' +
      '<span class="fb-spacer"></span>' +
      '<button type="button" class="fb-done">Done</button>' +
      "</div>";
    document.body.appendChild(pop);
    curPop = pop;
    pop._anchor = anchorBtn;
    positionPop(pop, anchorBtn);

    var ta = pop.querySelector(".fb-note");
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    ta.addEventListener("input", function () {
      setFB(sid, { note: ta.value, label: label, group: group, ts: f.ts || nowIso() });
      markCorner(sid);
    });
    pop.querySelectorAll(".fb-disp button").forEach(function (b) {
      b.addEventListener("click", function () {
        var d = b.getAttribute("data-disp");
        var cur = (FB[sid] || {}).disposition;
        var nd = cur === d ? null : d;
        setFB(sid, { disposition: nd, label: label, group: group, ts: (FB[sid] || {}).ts || nowIso() });
        pop.querySelectorAll(".fb-disp button").forEach(function (x) { x.classList.remove("on-ok", "on-rev"); });
        if (nd === "ok") pop.querySelector('[data-disp="ok"]').classList.add("on-ok");
        if (nd === "revise") pop.querySelector('[data-disp="revise"]').classList.add("on-rev");
        markCorner(sid);
      });
    });
    pop.querySelector(".fb-clear").addEventListener("click", function () {
      delete FB[sid]; saveStore(); markCorner(sid); closePop();
    });
    pop.querySelector(".fb-done").addEventListener("click", closePop);
  }
  function positionPop(pop, anchor) {
    var r = anchor.getBoundingClientRect();
    var w = 320;
    var left = Math.max(12, Math.min(r.right - w, window.innerWidth - w - 12));
    var top = r.bottom + 8;
    var maxTop = window.innerHeight - pop.offsetHeight - 12;
    if (top > maxTop) top = Math.max(12, r.top - pop.offsetHeight - 8);
    pop.style.left = left + "px";
    pop.style.top = top + window.scrollY + "px";
  }

  // ── floating button + tray (singleton in the TOP window; aggregates stores) ───
  function buildChrome() {
    var TD = topDoc();
    if (TD.getElementById("fb-fab")) { refreshChrome(); return; } // one shared button
    var fab = TD.createElement("button");
    fab.id = "fb-fab";
    fab.type = "button";
    fab.title = "Review & export collected feedback";
    fab.innerHTML = '💬 Feedback <span id="fb-count">0</span>';
    fab.addEventListener("click", toggleTray);
    TD.body.appendChild(fab);
    var tray = TD.createElement("div");
    tray.id = "fb-tray";
    tray.hidden = true;
    TD.body.appendChild(tray);
    refreshChrome();
  }
  function refreshChrome() {
    var TD = topDoc();
    var n = totalCount();
    var fab = TD.getElementById("fb-fab");
    if (fab) {
      fab.style.display = n > 0 ? "flex" : "none"; // appears only when there's feedback
      var c = fab.querySelector("#fb-count");
      if (c) c.textContent = n;
    }
    var tray = TD.getElementById("fb-tray");
    if (tray) {
      if (n === 0) tray.hidden = true;
      if (!tray.hidden) renderTray();
    }
  }
  function toggleTray() {
    var tray = topDoc().getElementById("fb-tray");
    if (!tray) return;
    tray.hidden = !tray.hidden;
    if (!tray.hidden) renderTray();
  }
  function renderTray() {
    var tray = topDoc().getElementById("fb-tray");
    if (!tray) return;
    var stores = allStores();

    var html = '<div class="fb-tray-h"><b>Feedback</b>' +
      '<button type="button" class="fb-tray-x" title="Close">✕</button></div>';
    html += '<div class="fb-tray-meta"><label>Reviewer <input id="fb-reviewer" type="text" value="' +
      esc(reviewerName()) + '" placeholder="your name"></label></div>';

    if (!stores.length) {
      html += '<div class="fb-tray-empty">No notes yet. Hover any region (a figure, a finding, a tab section…) and click the 💬 in its corner.</div>';
    } else {
      html += '<div class="fb-tray-list">';
      stores.forEach(function (s) {
        var gtitle = s.meta.title || (s.meta.study ? "Study: " + s.meta.study : s.meta.investigation || s.storeId);
        html += '<div class="fb-tray-group">' + esc(gtitle) + "</div>";
        Object.keys(s.entries).sort().forEach(function (sid) {
          var e = s.entries[sid];
          var disp = e.disposition === "ok" ? "👍" : e.disposition === "revise" ? "✏️" : "•";
          html += '<div class="fb-item" data-store="' + esc(s.storeId) + '" data-sid="' + esc(sid) + '">' +
            '<span class="fb-item-disp">' + disp + "</span>" +
            '<span class="fb-item-body"><span class="fb-item-label">' + esc(e.region || e.label || sid) + "</span>" +
            '<span class="fb-item-note">' + esc(e.note != null ? e.note : e.text || "(no note)") + "</span></span>" +
            '<button type="button" class="fb-item-del" title="Remove">✕</button></div>';
        });
      });
      html += "</div>";
    }

    html += '<div class="fb-tray-actions">' +
      '<button type="button" id="fb-export">⬇ Export .yaml</button>' +
      '<button type="button" id="fb-copy">⧉ Copy</button>' +
      (!isSnapshot() && anySaveable() ? '<button type="button" id="fb-save" class="primary">⤴ Save to workspace</button>' : "") +
      "</div>";
    tray.innerHTML = html;

    tray.querySelector(".fb-tray-x").addEventListener("click", toggleTray);
    var rev = tray.querySelector("#fb-reviewer");
    if (rev) rev.addEventListener("input", function () { setReviewerName(rev.value); });
    tray.querySelectorAll(".fb-item").forEach(function (row) {
      var sid = row.getAttribute("data-sid");
      var store = row.getAttribute("data-store");
      row.querySelector(".fb-item-body").addEventListener("click", function () { jumpTo(store, sid); });
      row.querySelector(".fb-item-del").addEventListener("click", function (ev) {
        ev.stopPropagation(); deleteItem(store, sid);
      });
    });
    tray.querySelector("#fb-export").addEventListener("click", exportYaml);
    tray.querySelector("#fb-copy").addEventListener("click", copyYaml);
    var saveBtn = tray.querySelector("#fb-save");
    if (saveBtn) saveBtn.addEventListener("click", saveToWorkspace);
  }
  function deleteItem(store, sid) {
    var key = STORE_PREFIX + WS() + "." + store;
    try {
      var o = JSON.parse(localStorage.getItem(key) || "{}") || {};
      delete o[sid];
      if (Object.keys(o).length) localStorage.setItem(key, JSON.stringify(o));
      else localStorage.removeItem(key);
    } catch (e) {}
    if (store === (CONFIG.storeId || "x")) delete FB[sid];
    markCorner(sid);
    refreshChrome();
    renderTray();
  }
  function jumpTo(store, sid) {
    // Only this surface's regions are reachable from its own engine instance;
    // items from another frame/surface simply don't scroll (cross-frame).
    var reg = document.querySelector('.fb-region[data-fb-sid="' + cssEscape(sid) + '"]');
    if (!reg) return;
    var e = FB[sid];
    if (e && e.group && typeof CONFIG.groupSwitch === "function") CONFIG.groupSwitch(e.group);
    setTimeout(function () {
      reg.scrollIntoView({ behavior: "smooth", block: "center" });
      reg.classList.add("fb-flash");
      setTimeout(function () { reg.classList.remove("fb-flash"); }, 1400);
    }, 120);
  }

  // ── YAML emit (canonical meta + annotations) ──────────────────────────────────
  function yq(s) {
    return '"' + String(s == null ? "" : s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, " ") + '"';
  }
  function payloadObj() {
    // Aggregate every store in the workspace into ONE canonical payload. Section
    // ids are globally unique (study-<slug>-… / investigation-…), so study notes
    // attribute to their study and investigation notes stay report-level.
    var author = reviewerName() || "reviewer";
    var annotations = {};
    var stores = allStores();
    stores.forEach(function (s) {
      Object.keys(s.entries).sort().forEach(function (sid) {
        var e = s.entries[sid];
        var entry = { author: author, text: e.note != null ? e.note : e.text || "", ts: e.ts || nowIso() };
        if (e.disposition) entry.disposition = e.disposition;
        if (e.region || e.label) entry.region = e.region || e.label;
        if (e.group) entry.group = e.group;
        annotations[sid] = [entry];
      });
    });
    var meta = {
      investigation: pickInvestigation(),
      reviewer: author,
      generated_at: nowIso(),
      source: "workbench-inline-feedback",
    };
    // Single study in scope (no investigation store) → keep the study hint.
    if (stores.length === 1 && stores[0].meta.study) meta.study = stores[0].meta.study;
    return { meta: meta, annotations: annotations };
  }
  function genYaml() {
    var p = payloadObj();
    var L = ["meta:"];
    L.push("  investigation: " + yq(p.meta.investigation));
    if (p.meta.study) L.push("  study: " + yq(p.meta.study));
    L.push("  reviewer: " + yq(p.meta.reviewer));
    L.push("  generated_at: " + yq(p.meta.generated_at));
    L.push("  source: " + yq(p.meta.source));
    L.push("annotations:");
    var keys = Object.keys(p.annotations);
    if (!keys.length) {
      L.push("  {}  # no notes — hover a region and click 💬");
    } else {
      keys.forEach(function (sid) {
        var e = p.annotations[sid][0];
        L.push("  " + sid + ":");
        L.push("    - author: " + yq(e.author));
        L.push("      text: " + yq(e.text));
        L.push("      ts: " + yq(e.ts));
        if (e.disposition) L.push("      disposition: " + yq(e.disposition));
        if (e.region) L.push("      region: " + yq(e.region));
        if (e.group) L.push("      group: " + yq(e.group));
      });
    }
    return L.join("\n") + "\n";
  }
  function exportName() {
    return "feedback-" + slugify(pickInvestigation() || "report") + ".yaml";
  }
  function download(name, text) {
    var TD = topDoc();
    var blob = new Blob([text], { type: "application/x-yaml" });
    var url = URL.createObjectURL(blob);
    var a = TD.createElement("a");
    a.href = url;
    a.download = name;
    TD.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  function toast(msg, ok) {
    if (window.Toast && typeof window.Toast.show === "function") {
      window.Toast.show(msg, ok === false ? "error" : "success");
      return;
    }
    var TD = topDoc();
    var t = TD.createElement("div");
    t.className = "fb-toast" + (ok === false ? " err" : "");
    t.textContent = msg;
    TD.body.appendChild(t);
    setTimeout(function () { t.classList.add("in"); }, 10);
    setTimeout(function () { t.classList.remove("in"); setTimeout(function () { t.remove(); }, 300); }, 2600);
  }
  function exportYaml() {
    download(exportName(), genYaml());
    toast("Feedback YAML downloaded");
  }
  function copyYaml() {
    var y = genYaml();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(y).then(
        function () { toast("YAML copied to clipboard"); },
        function () { download(exportName(), y); }
      );
    } else {
      download(exportName(), y);
    }
  }
  // >>> FB_LIVE_SAVE — the only network code. The self-contained investigation
  // report must make no network calls, so render_html STRIPS this block (down to
  // a no-op) when it inlines this engine. Never show the Save button where this
  // is stripped (the report sets noSave). Do not add fetch()/"/api/" elsewhere.
  function saveToWorkspace() {
    var inv = pickInvestigation();
    if (!inv) { toast("No investigation resolved — export instead", false); return; }
    if (!totalCount()) { toast("No notes to save", false); return; }
    var payload = payloadObj();
    payload.meta.investigation = inv;
    var url = "/api/feedback-save";
    try {
      // apiUrl only prefixes the base path for an absolute "/api/..." path; a
      // bare name is returned unchanged and would POST relative to the page.
      if (window.DataSource && typeof window.DataSource.apiUrl === "function") url = window.DataSource.apiUrl("/api/feedback-save");
    } catch (e) {}
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (res.ok && res.j && res.j.saved) toast("Saved to " + (res.j.path || "workspace"));
        else toast((res.j && res.j.error) || "Save failed", false);
      })
      .catch(function (err) { toast("Save failed: " + err, false); });
  }
  // <<< FB_LIVE_SAVE

  // ── styles ─────────────────────────────────────────────────────────────────────
  function injectStyle() {
    if (document.getElementById("fb-style")) return;
    var css = [
      ".fb-region{position:relative;}",
      ".fb-corner{position:absolute;top:4px;right:4px;z-index:6;width:26px;height:26px;padding:0;border:none;border-radius:7px;",
      "  background:var(--card,#fff);box-shadow:0 1px 4px rgba(0,0,0,.18);cursor:pointer;font-size:14px;line-height:26px;text-align:center;",
      "  opacity:0;transform:translateY(-2px);transition:opacity .12s,transform .12s;}",
      ".fb-region:hover>.fb-corner,.fb-corner:focus{opacity:.92;transform:none;}",
      ".fb-corner:hover{opacity:1;box-shadow:0 2px 8px rgba(0,0,0,.28);}",
      ".fb-corner.has{opacity:1;background:#2d6cdf;}",
      ".fb-region.fb-has{box-shadow:inset 0 0 0 1.5px rgba(45,108,223,.45);border-radius:8px;}",
      ".fb-region.fb-flash{outline:2px solid #2d6cdf;outline-offset:2px;transition:outline .2s;}",
      ".fb-pop{position:absolute;z-index:99999;width:320px;max-width:calc(100vw - 24px);background:var(--card,#fff);color:var(--ink,#111);",
      "  border:1px solid var(--line,#ddd);border-radius:12px;box-shadow:0 10px 34px rgba(0,0,0,.28);padding:12px;font-size:13px;}",
      ".fb-pop-h{font-weight:600;margin-bottom:8px;font-size:13px;line-height:1.3;}",
      ".fb-disp{display:flex;gap:6px;margin-bottom:8px;}",
      ".fb-disp button{flex:1;padding:6px 4px;border:1px solid var(--line,#ddd);border-radius:7px;background:var(--bg,#f7f7f8);cursor:pointer;font-size:12px;color:var(--ink,#111);}",
      ".fb-disp button.on-ok{background:#1f9d55;border-color:#1f9d55;color:#fff;}",
      ".fb-disp button.on-rev{background:#e0a106;border-color:#e0a106;color:#1a1a1a;}",
      ".fb-note{width:100%;box-sizing:border-box;border:1px solid var(--line,#ddd);border-radius:8px;padding:7px 9px;font:inherit;resize:vertical;background:var(--bg,#fff);color:var(--ink,#111);}",
      ".fb-pop-foot{display:flex;align-items:center;gap:8px;margin-top:9px;}",
      ".fb-pop-foot .fb-spacer{flex:1;}",
      ".fb-pop-foot button{padding:6px 12px;border:1px solid var(--line,#ddd);border-radius:7px;background:var(--bg,#f2f2f4);cursor:pointer;font-size:12px;color:var(--ink,#111);}",
      ".fb-pop-foot .fb-done{background:#2d6cdf;border-color:#2d6cdf;color:#fff;}",
      "#fb-fab{position:fixed;right:18px;bottom:18px;z-index:99998;padding:10px 15px;border:none;border-radius:22px;",
      "  background:#2d6cdf;color:#fff;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 4px 16px rgba(45,108,223,.4);display:none;gap:7px;align-items:center;}",
      "#fb-fab #fb-count{background:rgba(255,255,255,.26);border-radius:10px;padding:0 7px;min-width:14px;text-align:center;font-size:12px;}",
      "#fb-tray{position:fixed;right:18px;bottom:66px;z-index:99998;width:360px;max-width:calc(100vw - 24px);max-height:72vh;overflow:auto;",
      "  background:var(--card,#fff);color:var(--ink,#111);border:1px solid var(--line,#ddd);border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.3);}",
      ".fb-tray-h{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid var(--line,#eee);position:sticky;top:0;background:var(--card,#fff);}",
      ".fb-tray-x{border:none;background:none;cursor:pointer;font-size:15px;color:var(--ink-2,#666);}",
      ".fb-tray-meta{display:flex;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line,#eee);}",
      ".fb-tray-meta label{flex:1;font-size:11px;color:var(--ink-2,#666);display:flex;flex-direction:column;gap:3px;}",
      ".fb-tray-meta input{border:1px solid var(--line,#ddd);border-radius:6px;padding:5px 7px;font:inherit;font-size:12px;background:var(--bg,#fff);color:var(--ink,#111);}",
      ".fb-tray-list{padding:6px 8px;}",
      ".fb-tray-group{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink-2,#888);padding:9px 8px 3px;}",
      ".fb-item{display:flex;gap:8px;align-items:flex-start;padding:7px 8px;border-radius:8px;cursor:pointer;}",
      ".fb-item:hover{background:var(--bg,#f5f6f8);}",
      ".fb-item-disp{font-size:13px;line-height:1.3;}",
      ".fb-item-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;}",
      ".fb-item-label{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}",
      ".fb-item-note{font-size:12px;color:var(--ink-2,#666);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}",
      ".fb-item-del{border:none;background:none;cursor:pointer;color:var(--ink-2,#999);font-size:12px;}",
      ".fb-tray-actions{display:flex;gap:7px;padding:11px 14px;border-top:1px solid var(--line,#eee);position:sticky;bottom:0;background:var(--card,#fff);flex-wrap:wrap;}",
      ".fb-tray-actions button{flex:1;min-width:92px;padding:8px 6px;border:1px solid var(--line,#ddd);border-radius:8px;background:var(--bg,#f2f2f4);cursor:pointer;font-size:12px;color:var(--ink,#111);}",
      ".fb-tray-actions button.primary{background:#2d6cdf;border-color:#2d6cdf;color:#fff;}",
      ".fb-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(12px);z-index:100000;background:#1f9d55;color:#fff;",
      "  padding:9px 16px;border-radius:9px;font-size:13px;box-shadow:0 6px 20px rgba(0,0,0,.3);opacity:0;transition:opacity .25s,transform .25s;}",
      ".fb-toast.in{opacity:1;transform:translateX(-50%) translateY(0);}",
      ".fb-toast.err{background:#d23f3f;}",
      "@media print{.fb-corner,#fb-fab,#fb-tray,.fb-pop{display:none !important;}}",
    ].join("\n");
    // Inject into this document (regions/corners/popover live here) AND the top
    // document (where the FAB/tray/toast are mounted when embedded in an iframe).
    [document, topDoc()].forEach(function (doc) {
      if (!doc || doc.getElementById("fb-style")) return;
      var st = doc.createElement("style");
      st.id = "fb-style";
      st.textContent = css;
      (doc.head || doc.documentElement).appendChild(st);
    });
  }

  // ── wiring ───────────────────────────────────────────────────────────────────
  var decorateTimer = null;
  function scheduleDecorate() {
    if (decorateTimer) return;
    decorateTimer = setTimeout(function () { decorateTimer = null; decorateAll(); }, 150);
  }
  function init() {
    if (!CONFIG || !CONFIG.regions || !CONFIG.regions.length) return;
    // study page gate: only run on an actual study page for the default config
    if (CONFIG.context === "study" && !document.querySelector("#panel-overview")) return;
    injectStyle();
    loadStore();
    buildChrome();
    decorateAll();
    var root =
      document.querySelector(CONFIG.observeRoot || "main.study-page") ||
      document.querySelector("#app") ||
      document.body;
    new MutationObserver(scheduleDecorate).observe(root, { childList: true, subtree: true });

    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".fb-corner");
      if (btn) {
        ev.preventDefault();
        ev.stopPropagation();
        var sid = btn.getAttribute("data-fb-sid");
        var reg = btn.closest(".fb-region");
        var label = (reg && reg.getAttribute("data-fb-label")) || sid;
        var group = (reg && reg.getAttribute("data-fb-group")) || "";
        openEditor(sid, label, group, btn);
        return;
      }
      if (curPop && !ev.target.closest(".fb-pop") && !ev.target.closest(".fb-corner")) closePop();
    });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") closePop(); });
    // On resize, REPOSITION the open popover — never close it. The study page
    // renders in an auto-growing iframe, and focusing the note textarea opens
    // the mobile keyboard; both fire resize, and closing here made the popover
    // vanish right after it opened (worse on mobile). Reposition to the anchor
    // if it's still in the DOM; otherwise leave the popover where it is.
    var _rzTimer = null;
    window.addEventListener("resize", function () {
      if (!curPop || _rzTimer) return;
      _rzTimer = setTimeout(function () {
        _rzTimer = null;
        if (curPop && curPop._anchor && curPop._anchor.isConnected) positionPop(curPop, curPop._anchor);
      }, 80);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
