/* study-feedback.js — inline expert-feedback capture for the study detail page.
 *
 * Adds a hover-corner 💬 button to every "region" across all six study tabs
 * (Overview, Visualizations, Model, Simulations, Audit, Decisions). A reviewer
 * clicks it, leaves a disposition (👍 / ✏️) and a note, and every note is
 * collected into one YAML document that can be exported (download / copy) or,
 * on the live server, saved straight into the workspace feedback dir.
 *
 * Works identically on the LIVE workbench and the READ-ONLY published bundle:
 * collection + export are pure client-side (localStorage); only "Save to
 * workspace" needs the backend and is hidden in snapshot mode.
 *
 * The exported YAML is the canonical ingest shape read by
 * viva_superpowers.feedback_import / feedback_tracking / feedback_actions and
 * by the /viva-* responder skills:
 *
 *   meta:
 *     investigation: <inv-slug>
 *     study: <study-slug>
 *     reviewer: <name>
 *     generated_at: <iso>
 *   annotations:
 *     study-<slug>-<tab>-<region>:
 *       - {author, text, ts, disposition, region, tab}
 *
 * Section ids are all prefixed `study-<slug>` so the aggregator attributes them
 * to the study (study_feedback_actions matches `study-<slug>` and
 * `study-<slug>-*`).
 */
(function () {
  "use strict";

  if (window.__vivaStudyFeedbackLoaded) return;
  window.__vivaStudyFeedbackLoaded = true;

  // ── context ────────────────────────────────────────────────────────────────
  function isSnapshot() {
    try {
      if (document.body && document.body.classList.contains("snapshot")) return true;
      var c = window.__DASH_CONFIG__;
      if (c && c.mode === "snapshot") return true;
    } catch (e) {}
    return false;
  }

  function studySlug() {
    return (
      (window._study && (window._study.name || window._study.slug)) ||
      window._studyName ||
      "study"
    );
  }

  function investigationSlug() {
    var s = window._study || {};
    var remembered = "";
    try {
      remembered = localStorage.getItem("viva.fb.inv." + slugify(studySlug())) || "";
    } catch (e) {}
    var cand =
      window.__STUDY_INVESTIGATION__ ||
      remembered ||
      s.investigation ||
      s.investigation_slug ||
      (Array.isArray(s.investigations) && s.investigations[0]) ||
      (s.investigations && typeof s.investigations === "object" && Object.keys(s.investigations)[0]) ||
      (window.__DASH_CONFIG__ && window.__DASH_CONFIG__.investigation) ||
      "";
    if (cand && typeof cand === "object") cand = cand.slug || cand.name || "";
    return cand || "";
  }
  function rememberInvestigation(v) {
    try {
      localStorage.setItem("viva.fb.inv." + slugify(studySlug()), v || "");
    } catch (e) {}
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

  function nowIso() {
    return new Date().toISOString().replace(/\.\d+Z$/, "Z");
  }

  function slugify(s) {
    return String(s || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "x";
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── store (localStorage, per workspace+study) ────────────────────────────────
  // FB = { sectionId: {note, disposition, label, tab, ts} }
  var FB = {};
  function storeKey() {
    var base = "viva.fb.store";
    var ws = (window.__DASH_CONFIG__ && window.__DASH_CONFIG__.workspace) || "";
    return base + "." + slugify(ws) + "." + slugify(studySlug());
  }
  function loadStore() {
    try {
      FB = JSON.parse(localStorage.getItem(storeKey()) || "{}") || {};
    } catch (e) {
      FB = {};
    }
  }
  function saveStore() {
    try {
      localStorage.setItem(storeKey(), JSON.stringify(FB));
    } catch (e) {}
    refreshCount();
  }
  function setFB(key, patch) {
    var cur = FB[key] || {};
    FB[key] = Object.assign({}, cur, patch);
    if (!FB[key].note && !FB[key].disposition) delete FB[key];
    saveStore();
  }
  function fbCount() {
    return Object.keys(FB).length;
  }

  // ── region config: per tab, the selectors a reviewer can comment on ──────────
  // key(el): stable id within the tab (prefer a real id/text, never DOM order).
  // label(el): human label shown in the editor + tray.
  function headingText(el) {
    var h = el.querySelector(
      "h1,h2,h3,h4,.card-h,.figure-title,.finding-statement,.expert-question-title,.di-card-title,.followup-title,summary"
    );
    var t = (h && h.textContent) || el.getAttribute("aria-label") || "";
    t = t.replace(/\s+/g, " ").trim();
    return t.slice(0, 80);
  }
  function textKey(el, fallback) {
    var t = headingText(el);
    return slugify(t || fallback || "");
  }

  var TABS = {
    overview: { panel: "#panel-overview", regions: [
      { sel: ".finding-card", key: function (el) { var i = el.querySelector(".finding-id"); return "finding-" + slugify((i && i.textContent) || textKey(el, "finding")); }, label: function (el) { var i = el.querySelector(".finding-id"); return "Finding " + (((i && i.textContent) || "").trim() || headingText(el)); } },
      { sel: ".ctr-block", key: function (el) { return "block-" + textKey(el, "block"); }, label: function (el) { return headingText(el) || "Overview block"; } },
      { sel: ".ov-src", key: function (el) { return "source-" + slugify((el.id || "").replace(/^sref-/, "") || textKey(el, "source")); }, label: function (el) { return "Source " + ((el.id || "").replace(/^sref-/, "") || headingText(el)); } },
    ] },
    visualize: { panel: "#panel-visualize", regions: [
      { sel: ".figure-card", key: function (el) { var t = el.querySelector(".figure-title"); var r = el.querySelector("[data-run-id]"); return "figure-" + slugify(((t && t.textContent) || "") + "-" + ((r && r.getAttribute("data-run-id")) || "")); }, label: function (el) { var t = el.querySelector(".figure-title"); return "Figure: " + (((t && t.textContent) || "").trim() || "untitled"); } },
    ] },
    compose: { panel: "#panel-compose", regions: [
      { sel: ".cond-block", key: function (el) { return "model-" + slugify(el.getAttribute("data-model-composite") || textKey(el, "model")); }, label: function (el) { return "Model: " + (el.getAttribute("data-model-composite") || headingText(el) || "composite"); } },
      { sel: "#model-composite-cards > *", key: function (el) { return "composite-" + textKey(el, "composite"); }, label: function (el) { return "Composite: " + (headingText(el) || "card"); } },
      { sel: "#model-readouts-section", key: function () { return "readouts"; }, label: function () { return "Model readouts"; } },
    ] },
    simulate: { panel: "#panel-simulate", regions: [
      { sel: "#study-sim-table", key: function () { return "runs"; }, label: function () { return "Simulation runs"; } },
      { sel: "#results-summary", key: function () { return "results-summary"; }, label: function () { return "Results summary"; } },
      { sel: "#gate-results", key: function () { return "gate-results"; }, label: function () { return "Gate results"; } },
      { sel: "#study-analyses-list", key: function () { return "analyses"; }, label: function () { return "Analyses"; } },
    ] },
    audit: { panel: "#panel-audit", regions: [
      { sel: ".expected-behavior-item", key: function (el) { return "test-" + slugify((el.id || "").replace(/^bt-/, "") || el.getAttribute("data-pytest-node") || textKey(el, "test")); }, label: function (el) { return "Test: " + ((el.id || "").replace(/^bt-/, "") || el.getAttribute("data-pytest-node") || headingText(el)); } },
      { sel: ".report-card-block", key: function (el) { return "report-card-" + textKey(el, "card"); }, label: function (el) { return "Report card: " + (headingText(el) || "card"); } },
      { sel: ".audit-check-item", key: function (el) { var c = el.querySelector("code"); return "check-" + slugify((c && c.textContent) || textKey(el, "check")); }, label: function (el) { var c = el.querySelector("code"); return "Check: " + (((c && c.textContent) || "").trim() || headingText(el)); } },
    ] },
    conclusions: { panel: "#panel-conclusions", regions: [
      { sel: "[data-narrative-card='conclusion_verdicts']", key: function () { return "verdicts"; }, label: function () { return "Three-track verdict"; } },
      { sel: ".expert-question-card", key: function (el) { var i = el.querySelector(".expert-question-id"); return "debt-" + slugify((i && i.textContent) || textKey(el, "debt")); }, label: function (el) { var i = el.querySelector(".expert-question-id"); return "Open debt " + (((i && i.textContent) || "").trim() || headingText(el)); } },
      { sel: ".di-card", key: function (el) { return "implication-" + textKey(el, "implication"); }, label: function (el) { return "Implication: " + (headingText(el) || "card"); } },
      { sel: ".followup-card", key: function (el) { return "followup-" + textKey(el, "followup"); }, label: function (el) { return "Follow-up: " + (headingText(el) || "study"); } },
    ] },
  };

  function sectionId(tab, regionKey) {
    return "study-" + slugify(studySlug()) + "-" + tab + "-" + regionKey;
  }

  // ── decoration: add hover 💬 corners to every region ─────────────────────────
  function decorateRegion(el, tab, cfg) {
    if (!el || el.__fbDecorated) return;
    // never decorate our own chrome or nested already-decorated parents' widgets
    if (el.closest(".fb-corner") || el.closest(".fb-pop") || el.closest("#fb-tray")) return;
    el.__fbDecorated = true;
    var key;
    try {
      key = cfg.key(el);
    } catch (e) {
      key = "x";
    }
    var label;
    try {
      label = cfg.label(el);
    } catch (e) {
      label = tab;
    }
    var sid = sectionId(tab, key);
    el.classList.add("fb-region");
    el.setAttribute("data-fb-sid", sid);
    el.setAttribute("data-fb-label", label || tab);
    el.setAttribute("data-fb-tab", tab);
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fb-corner";
    btn.setAttribute("data-fb-sid", sid);
    btn.setAttribute("title", "Leave feedback on: " + (label || tab));
    btn.setAttribute("aria-label", "Leave feedback on " + (label || tab));
    btn.textContent = "💬";
    el.appendChild(btn);
    markCorner(sid);
  }

  function decorateAll() {
    Object.keys(TABS).forEach(function (tab) {
      var panel = document.querySelector(TABS[tab].panel);
      if (!panel) return;
      TABS[tab].regions.forEach(function (cfg) {
        var nodes = panel.querySelectorAll(cfg.sel);
        for (var i = 0; i < nodes.length; i++) decorateRegion(nodes[i], tab, cfg);
      });
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
  function cssEscape(s) {
    return String(s).replace(/"/g, '\\"');
  }

  // ── inline editor popover ────────────────────────────────────────────────────
  var curPop = null;
  function closePop() {
    if (curPop) {
      curPop.remove();
      curPop = null;
    }
  }
  function openEditor(sid, label, tab, anchorBtn) {
    closePop();
    var f = FB[sid] || {};
    var pop = document.createElement("div");
    pop.className = "fb-pop";
    pop.innerHTML =
      '<div class="fb-pop-h">' + esc(label) + '</div>' +
      '<div class="fb-disp">' +
      '<button type="button" data-disp="ok" class="' + (f.disposition === "ok" ? "on-ok" : "") + '">👍 Looks right</button>' +
      '<button type="button" data-disp="revise" class="' + (f.disposition === "revise" ? "on-rev" : "") + '">✏️ Needs revision</button>' +
      '</div>' +
      '<textarea class="fb-note" rows="4" placeholder="Note for the team / AI agent…">' + esc(f.note || "") + "</textarea>" +
      '<div class="fb-pop-foot">' +
      '<button type="button" class="fb-clear">Clear</button>' +
      '<span class="fb-spacer"></span>' +
      '<button type="button" class="fb-done">Done</button>' +
      "</div>";
    document.body.appendChild(pop);
    curPop = pop;
    positionPop(pop, anchorBtn);

    var ta = pop.querySelector(".fb-note");
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    ta.addEventListener("input", function () {
      setFB(sid, { note: ta.value, label: label, tab: tab, ts: f.ts || nowIso() });
      markCorner(sid);
    });
    pop.querySelectorAll(".fb-disp button").forEach(function (b) {
      b.addEventListener("click", function () {
        var d = b.getAttribute("data-disp");
        var cur = (FB[sid] || {}).disposition;
        var nd = cur === d ? null : d;
        setFB(sid, { disposition: nd, label: label, tab: tab, ts: (FB[sid] || {}).ts || nowIso() });
        pop.querySelectorAll(".fb-disp button").forEach(function (x) {
          x.classList.remove("on-ok", "on-rev");
        });
        if (nd === "ok") pop.querySelector('[data-disp="ok"]').classList.add("on-ok");
        if (nd === "revise") pop.querySelector('[data-disp="revise"]').classList.add("on-rev");
        markCorner(sid);
      });
    });
    pop.querySelector(".fb-clear").addEventListener("click", function () {
      delete FB[sid];
      saveStore();
      markCorner(sid);
      closePop();
    });
    pop.querySelector(".fb-done").addEventListener("click", closePop);
  }

  function positionPop(pop, anchor) {
    var r = anchor.getBoundingClientRect();
    var w = 320;
    var left = Math.min(r.right - w, window.innerWidth - w - 12);
    left = Math.max(12, left);
    var top = r.bottom + 8;
    var maxTop = window.innerHeight - pop.offsetHeight - 12;
    if (top > maxTop) top = Math.max(12, r.top - pop.offsetHeight - 8);
    pop.style.left = left + "px";
    pop.style.top = top + window.scrollY + "px";
  }

  // ── floating button + tray ───────────────────────────────────────────────────
  function buildChrome() {
    if (document.getElementById("fb-fab")) return;
    var fab = document.createElement("button");
    fab.id = "fb-fab";
    fab.type = "button";
    fab.title = "Review feedback collected on this study";
    fab.innerHTML = '💬 Feedback <span id="fb-count">0</span>';
    fab.addEventListener("click", toggleTray);
    document.body.appendChild(fab);

    var tray = document.createElement("div");
    tray.id = "fb-tray";
    tray.hidden = true;
    document.body.appendChild(tray);
    refreshCount();
  }

  function refreshCount() {
    var c = document.getElementById("fb-count");
    if (c) c.textContent = fbCount();
    var fab = document.getElementById("fb-fab");
    if (fab) fab.classList.toggle("has", fbCount() > 0);
    var tray = document.getElementById("fb-tray");
    if (tray && !tray.hidden) renderTray();
  }

  function toggleTray() {
    var tray = document.getElementById("fb-tray");
    if (!tray) return;
    tray.hidden = !tray.hidden;
    if (!tray.hidden) renderTray();
  }

  function renderTray() {
    var tray = document.getElementById("fb-tray");
    if (!tray) return;
    var keys = Object.keys(FB);
    var byTab = {};
    keys.forEach(function (sid) {
      var t = FB[sid].tab || "other";
      (byTab[t] = byTab[t] || []).push(sid);
    });
    var tabOrder = ["overview", "visualize", "compose", "simulate", "audit", "conclusions", "other"];
    var tabNames = { overview: "Overview", visualize: "Visualizations", compose: "Model", simulate: "Simulations", audit: "Audit", conclusions: "Decisions", other: "Other" };

    var html = '<div class="fb-tray-h"><b>Feedback on ' + esc(studySlug()) + "</b>" +
      '<button type="button" class="fb-tray-x" title="Close">✕</button></div>';
    html += '<div class="fb-tray-meta">' +
      '<label>Reviewer <input id="fb-reviewer" type="text" value="' + esc(reviewerName()) + '" placeholder="your name"></label>' +
      '<label>Investigation <input id="fb-inv" type="text" value="' + esc(investigationSlug()) + '" placeholder="inv-slug"></label>' +
      "</div>";

    if (!keys.length) {
      html += '<div class="fb-tray-empty">No notes yet. Hover any region (a figure, a finding, a test, a decision…) and click the 💬 in its corner.</div>';
    } else {
      html += '<div class="fb-tray-list">';
      tabOrder.forEach(function (t) {
        if (!byTab[t]) return;
        html += '<div class="fb-tray-group">' + esc(tabNames[t] || t) + "</div>";
        byTab[t].forEach(function (sid) {
          var e = FB[sid];
          var disp = e.disposition === "ok" ? "👍" : e.disposition === "revise" ? "✏️" : "•";
          html += '<div class="fb-item" data-sid="' + esc(sid) + '">' +
            '<span class="fb-item-disp">' + disp + "</span>" +
            '<span class="fb-item-body"><span class="fb-item-label">' + esc(e.label || sid) + "</span>" +
            '<span class="fb-item-note">' + esc(e.note || "(no note)") + "</span></span>" +
            '<button type="button" class="fb-item-del" title="Remove">✕</button>' +
            "</div>";
        });
      });
      html += "</div>";
    }

    html += '<div class="fb-tray-actions">' +
      '<button type="button" id="fb-export">⬇ Export .yaml</button>' +
      '<button type="button" id="fb-copy">⧉ Copy YAML</button>' +
      (isSnapshot() ? "" : '<button type="button" id="fb-save" class="primary">⤴ Save to workspace</button>') +
      "</div>";
    tray.innerHTML = html;

    tray.querySelector(".fb-tray-x").addEventListener("click", toggleTray);
    var rev = tray.querySelector("#fb-reviewer");
    if (rev) rev.addEventListener("input", function () { setReviewerName(rev.value); });
    var invInput = tray.querySelector("#fb-inv");
    if (invInput) invInput.addEventListener("input", function () { rememberInvestigation(invInput.value.trim()); });
    tray.querySelectorAll(".fb-item").forEach(function (row) {
      var sid = row.getAttribute("data-sid");
      row.querySelector(".fb-item-body").addEventListener("click", function () { jumpTo(sid); });
      row.querySelector(".fb-item-del").addEventListener("click", function (ev) {
        ev.stopPropagation();
        delete FB[sid];
        saveStore();
        markCorner(sid);
        renderTray();
      });
    });
    tray.querySelector("#fb-export").addEventListener("click", exportYaml);
    tray.querySelector("#fb-copy").addEventListener("click", copyYaml);
    var saveBtn = tray.querySelector("#fb-save");
    if (saveBtn) saveBtn.addEventListener("click", saveToWorkspace);
  }

  function jumpTo(sid) {
    var e = FB[sid];
    var tab = e && e.tab;
    if (tab && typeof window._setStudyTab === "function") {
      try { window._setStudyTab(tab); } catch (err) {}
    }
    setTimeout(function () {
      var reg = document.querySelector('.fb-region[data-fb-sid="' + cssEscape(sid) + '"]');
      if (reg) {
        reg.scrollIntoView({ behavior: "smooth", block: "center" });
        reg.classList.add("fb-flash");
        setTimeout(function () { reg.classList.remove("fb-flash"); }, 1400);
      }
    }, 120);
  }

  // ── YAML emit (canonical meta + annotations shape) ───────────────────────────
  function yq(s) {
    return '"' + String(s == null ? "" : s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, " ") + '"';
  }
  function genYaml() {
    var L = [];
    L.push("meta:");
    L.push("  investigation: " + yq(investigationSlug()));
    L.push("  study: " + yq(studySlug()));
    L.push("  reviewer: " + yq(reviewerName()));
    L.push("  generated_at: " + yq(nowIso()));
    L.push("  source: " + yq("workbench-inline-feedback"));
    L.push("annotations:");
    var keys = Object.keys(FB).sort();
    if (!keys.length) {
      L.push("  {}  # no notes — hover a region and click 💬");
    } else {
      var author = reviewerName() || "reviewer";
      keys.forEach(function (sid) {
        var e = FB[sid];
        L.push("  " + sid + ":");
        L.push("    - author: " + yq(author));
        L.push("      text: " + yq(e.note || ""));
        L.push("      ts: " + yq(e.ts || nowIso()));
        if (e.disposition) L.push("      disposition: " + yq(e.disposition));
        if (e.label) L.push("      region: " + yq(e.label));
        if (e.tab) L.push("      tab: " + yq(e.tab));
      });
    }
    return L.join("\n") + "\n";
  }

  function download(name, text) {
    var blob = new Blob([text], { type: "application/x-yaml" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function toast(msg, ok) {
    if (window.Toast && typeof window.Toast.show === "function") {
      window.Toast.show(msg, ok === false ? "error" : "success");
      return;
    }
    var t = document.createElement("div");
    t.className = "fb-toast" + (ok === false ? " err" : "");
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.classList.add("in"); }, 10);
    setTimeout(function () { t.classList.remove("in"); setTimeout(function () { t.remove(); }, 300); }, 2600);
  }

  function exportYaml() {
    download("feedback-" + slugify(studySlug()) + ".yaml", genYaml());
    toast("Feedback YAML downloaded");
  }
  function copyYaml() {
    var y = genYaml();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(y).then(
        function () { toast("YAML copied to clipboard"); },
        function () { toast("Copy failed — use Export", false); }
      );
    } else {
      download("feedback-" + slugify(studySlug()) + ".yaml", y);
    }
  }

  function saveToWorkspace() {
    var inv = (document.getElementById("fb-inv") && document.getElementById("fb-inv").value.trim()) || investigationSlug();
    if (!inv) {
      toast("Set the investigation slug first", false);
      return;
    }
    if (!fbCount()) {
      toast("No notes to save", false);
      return;
    }
    var annotations = {};
    var author = reviewerName() || "reviewer";
    Object.keys(FB).forEach(function (sid) {
      var e = FB[sid];
      var entry = { author: author, text: e.note || "", ts: e.ts || nowIso() };
      if (e.disposition) entry.disposition = e.disposition;
      if (e.label) entry.region = e.label;
      if (e.tab) entry.tab = e.tab;
      annotations[sid] = [entry];
    });
    var payload = {
      meta: {
        investigation: inv,
        study: studySlug(),
        reviewer: author,
        generated_at: nowIso(),
        source: "workbench-inline-feedback",
      },
      annotations: annotations,
    };
    var url = "/api/feedback-save";
    try {
      if (window.DataSource && typeof window.DataSource.apiUrl === "function") {
        url = window.DataSource.apiUrl("feedback-save");
      }
    } catch (e) {}
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (res.ok && res.j && res.j.saved) {
          toast("Saved to " + (res.j.path || "workspace"));
        } else {
          toast((res.j && res.j.error) || "Save failed", false);
        }
      })
      .catch(function (err) { toast("Save failed: " + err, false); });
  }

  // ── wiring ───────────────────────────────────────────────────────────────────
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
      ".fb-pop{position:absolute;z-index:9999;width:320px;max-width:calc(100vw - 24px);background:var(--card,#fff);color:var(--ink,#111);",
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
      "#fb-fab{position:fixed;right:18px;bottom:18px;z-index:9998;padding:10px 15px;border:none;border-radius:22px;",
      "  background:#2d6cdf;color:#fff;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 4px 16px rgba(45,108,223,.4);display:flex;gap:7px;align-items:center;}",
      "#fb-fab #fb-count{background:rgba(255,255,255,.26);border-radius:10px;padding:0 7px;min-width:14px;text-align:center;font-size:12px;}",
      "#fb-fab.has{background:#1b55c0;}",
      "#fb-tray{position:fixed;right:18px;bottom:66px;z-index:9998;width:360px;max-width:calc(100vw - 24px);max-height:72vh;overflow:auto;",
      "  background:var(--card,#fff);color:var(--ink,#111);border:1px solid var(--line,#ddd);border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.3);}",
      ".fb-tray-h{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid var(--line,#eee);position:sticky;top:0;background:var(--card,#fff);}",
      ".fb-tray-x{border:none;background:none;cursor:pointer;font-size:15px;color:var(--ink-2,#666);}",
      ".fb-tray-meta{display:flex;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line,#eee);}",
      ".fb-tray-meta label{flex:1;font-size:11px;color:var(--ink-2,#666);display:flex;flex-direction:column;gap:3px;}",
      ".fb-tray-meta input{border:1px solid var(--line,#ddd);border-radius:6px;padding:5px 7px;font:inherit;font-size:12px;background:var(--bg,#fff);color:var(--ink,#111);}",
      ".fb-tray-empty{padding:18px 16px;color:var(--ink-2,#777);font-size:13px;line-height:1.5;}",
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
      ".fb-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(12px);z-index:10000;background:#1f9d55;color:#fff;",
      "  padding:9px 16px;border-radius:9px;font-size:13px;box-shadow:0 6px 20px rgba(0,0,0,.3);opacity:0;transition:opacity .25s,transform .25s;}",
      ".fb-toast.in{opacity:1;transform:translateX(-50%) translateY(0);}",
      ".fb-toast.err{background:#d23f3f;}",
      "@media print{.fb-corner,#fb-fab,#fb-tray,.fb-pop{display:none !important;}}",
    ].join("\n");
    var st = document.createElement("style");
    st.id = "fb-style";
    st.textContent = css;
    document.head.appendChild(st);
  }

  var decorateTimer = null;
  function scheduleDecorate() {
    if (decorateTimer) return;
    decorateTimer = setTimeout(function () {
      decorateTimer = null;
      decorateAll();
    }, 150);
  }

  function init() {
    var root = document.querySelector("main.study-page") || document.body;
    if (!document.querySelector("#panel-overview")) return; // not a study page
    injectStyle();
    loadStore();
    buildChrome();
    decorateAll();

    // catch JS-re-rendered regions (charts, audit checks, report cards, galleries)
    var obs = new MutationObserver(function () { scheduleDecorate(); });
    obs.observe(root, { childList: true, subtree: true });

    // corner click → open editor
    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".fb-corner");
      if (btn) {
        ev.preventDefault();
        ev.stopPropagation();
        var sid = btn.getAttribute("data-fb-sid");
        var reg = btn.closest(".fb-region");
        var label = (reg && reg.getAttribute("data-fb-label")) || sid;
        var tab = (reg && reg.getAttribute("data-fb-tab")) || "";
        openEditor(sid, label, tab, btn);
        return;
      }
      // click outside popover closes it
      if (curPop && !ev.target.closest(".fb-pop") && !ev.target.closest(".fb-corner")) {
        closePop();
      }
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") closePop();
    });
    window.addEventListener("resize", closePop);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
