// discovery-cycle.js — the dockable "Discovery cycle" panel (VivPanelDock), the
// same engine the Studies / Registry / Chat / Code panels use.
//
// A live, clickable map of the discovery engine's cycle for the LOADED
// investigation. It coordinates with the rest of the SPA: one click loads the
// investigation that's open in the workbench into the cycle and runs it. Pick a
// different cycle-runnable investigation from the menu, run one analytical cycle
// (instant, read-only) or advance continuously in a sandbox (never touches your
// real files), click any step to see what ran + its result inline, and re-run a
// single step. State lives in the panel instance + localStorage, and the panel
// stays mounted while you move around the SPA, so results persist.
//
// Backed by: /api/discovery-cycle-investigations (picker), /api/discovery-cycle/{slug}
// (run), /api/discovery-cycle/{slug}/step/{id} (re-run), /api/discovery-cycle/{slug}/advance
// (sandboxed). The X-VW-Session header is added transparently by session.js.
(function (global) {
  "use strict";

  var PANEL_ID = "viv-cycle-panel", BODY_ID = "viv-cycle-body", TOGGLE_ID = "viv-cycle-toggle";
  var K_SLUG = "viv.cycle.slug";

  var ctl = null, popoutWin = null, initDone = false, built = false;
  // panel state (persists while the panel is mounted)
  var S = { slug: "", title: "", steps: [], byId: {}, open: {}, busy: false, current: "" };

  function el(id) { return document.getElementById(id); }
  function base() { return global.__BASE_PATH__ || ""; }
  function getJSON(path) {
    return fetch(base() + path, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function lsGet(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }

  // ---- CSS ----------------------------------------------------------------
  function injectStyle() {
    if (el("dcyc-style")) return;
    var css = [
      ".dcyc-wrap{display:flex;flex-direction:column;height:100%;min-height:0;font-size:13px}",
      ".dcyc-top{padding:10px 12px;border-bottom:1px solid var(--viv-border,#e5e7eb);display:flex;flex-direction:column;gap:8px;flex:none}",
      ".dcyc-current{font-size:12px;color:var(--viv-fg-muted,#667);display:flex;align-items:center;gap:8px;flex-wrap:wrap}",
      ".dcyc-current b{color:var(--viv-fg,#223)}",
      ".dcyc-controls{display:flex;align-items:center;gap:7px;flex-wrap:wrap}",
      ".dcyc-controls select{flex:1;min-width:120px;font:inherit;font-size:12.5px;padding:4px 7px;border-radius:7px;border:1px solid var(--viv-border,#ccd);background:var(--viv-bg,#fff);color:inherit}",
      ".dcyc-btn{font:inherit;font-size:12.5px;padding:5px 11px;border-radius:7px;border:1px solid var(--viv-border,#ccd);background:var(--viv-bg,#fff);color:inherit;cursor:pointer;white-space:nowrap}",
      ".dcyc-btn:disabled{opacity:.45;cursor:default}",
      ".dcyc-btn-primary{background:#2563eb;border-color:#2563eb;color:#fff}",
      ".dcyc-btn-sm{padding:3px 9px;font-size:12px}",
      ".dcyc-status{font-size:11.5px;color:var(--viv-fg-muted,#778);min-height:1em}",
      ".dcyc-graph{flex:1;overflow:auto;padding:8px 10px}",
      ".dcyc-phase-h{font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--viv-fg-muted,#9aa3af);margin:10px 0 3px}",
      ".dcyc-node{border:1px solid var(--viv-border,#e1e3ea);border-radius:9px;margin-bottom:6px;background:var(--viv-bg,#fff);overflow:hidden}",
      ".dcyc-node.st-mut{border-style:dashed}",
      ".dcyc-nh{display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:pointer;position:relative}",
      ".dcyc-nh:hover{background:var(--viv-bg-alt,#f7f8fa)}",
      ".dcyc-node.sel .dcyc-nh{background:rgba(37,99,235,.07)}",
      ".dcyc-dot{width:9px;height:9px;border-radius:50%;flex:none;background:#c9cdd6}",
      ".st-ok .dcyc-dot{background:#16a34a}.st-empty .dcyc-dot{background:#d1a019}",
      ".st-error .dcyc-dot{background:#dc2626}.st-idle .dcyc-dot{background:#94a3b8}",
      ".st-blocked .dcyc-dot{background:#b0b4bd}.st-pending .dcyc-dot{background:#cbd1da}",
      ".st-running .dcyc-dot{background:#2563eb;animation:dcycpulse 1s infinite}",
      "@keyframes dcycpulse{0%,100%{opacity:1}50%{opacity:.3}}",
      ".dcyc-nt{flex:1;min-width:0}",
      ".dcyc-nt .t{font-weight:600;font-size:12.5px}",
      ".dcyc-nt .fn{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;color:var(--viv-fg-muted,#9aa3af)}",
      ".dcyc-nt .sum{font-size:11.5px;color:var(--viv-fg,#445);margin-top:2px}",
      ".dcyc-dur{font-size:10px;color:var(--viv-fg-muted,#aab);flex:none}",
      ".dcyc-caret{flex:none;color:var(--viv-fg-muted,#aab);transition:transform .12s}",
      ".dcyc-node.exp .dcyc-caret{transform:rotate(90deg)}",
      ".dcyc-detail{border-top:1px solid var(--viv-border,#eef);padding:9px 10px;background:var(--viv-bg-alt,#fafbfc)}",
      ".dcyc-detail .meta{font-size:11px;color:var(--viv-fg-muted,#778);margin-bottom:6px}",
      ".dcyc-detail .meta code{font-family:ui-monospace,Menlo,monospace}",
      ".dcyc-pre{font-family:ui-monospace,Menlo,monospace;font-size:11px;line-height:1.45;white-space:pre-wrap;word-break:break-word;background:var(--viv-bg,#fff);border:1px solid var(--viv-border,#e3e3ea);border-radius:7px;padding:8px;max-height:40vh;overflow:auto;margin-top:6px}",
      ".dcyc-note{font-size:11.5px;color:#b4690e;margin:5px 0}",
      ".dcyc-row-actions{display:flex;gap:6px;margin-bottom:4px}",
      ".empty-state{color:var(--viv-fg-muted,#9aa);font-size:12.5px;padding:14px}"
    ].join("\n");
    var st = document.createElement("style");
    st.id = "dcyc-style"; st.textContent = css;
    document.head.appendChild(st);
  }

  // ---- body construction --------------------------------------------------
  function buildBody() {
    if (built) return true;
    var body = el(BODY_ID);
    if (!body) return false;
    body.innerHTML =
      '<div class="dcyc-wrap">' +
      '  <div class="dcyc-top">' +
      '    <div class="dcyc-current" id="dcyc-current"></div>' +
      '    <div class="dcyc-controls">' +
      '      <select id="dcyc-inv"><option value="">Loading…</option></select>' +
      '      <button id="dcyc-run" class="dcyc-btn dcyc-btn-primary" disabled>Run</button>' +
      '      <button id="dcyc-run-cont" class="dcyc-btn" disabled title="Advance toward closure in a sandboxed copy — never touches your real files">Advance ▸</button>' +
      '    </div>' +
      '    <div id="dcyc-status" class="dcyc-status"></div>' +
      '  </div>' +
      '  <div id="dcyc-graph" class="dcyc-graph"><p class="empty-state">Load an investigation to see its cycle.</p></div>' +
      '</div>';
    var sel = el("dcyc-inv");
    if (sel) sel.addEventListener("change", function () { onPick(sel.value, true); });
    var r = el("dcyc-run"); if (r) r.addEventListener("click", runCycle);
    var rc = el("dcyc-run-cont"); if (rc) rc.addEventListener("click", runContinuous);
    built = true;
    return true;
  }

  function setStatus(m) { var s = el("dcyc-status"); if (s) s.textContent = m || ""; }

  // ---- coordination with the loaded investigation -------------------------
  function currentSlug() {
    try { return global._currentIsetSlug || ""; } catch (e) { return ""; }
  }
  function currentTitle(slug) {
    // prefer the SPA's loaded investigation data, fall back to the picker labels
    try {
      var d = global._currentIsetData || global._currentIset;
      if (d && (d.title || d.name)) return d.title || d.name;
    } catch (e) { /* ignore */ }
    var sel = el("dcyc-inv");
    if (sel) for (var i = 0; i < sel.options.length; i++)
      if (sel.options[i].value === slug) return sel.options[i].textContent;
    return slug;
  }

  function syncCurrent() {
    var box = el("dcyc-current");
    if (!box) return;
    var cur = currentSlug();
    S.current = cur;
    if (!cur) { box.innerHTML = '<span>No investigation open in the workbench.</span>'; return; }
    var name = currentTitle(cur);
    if (cur === S.slug) {
      box.innerHTML = 'Cycle: <b>' + esc(name) + "</b>";
    } else {
      box.innerHTML = 'Open in workbench: <b>' + esc(name) + "</b> " +
        '<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" id="dcyc-load-current">Load into cycle ▸</button>';
      var b = el("dcyc-load-current");
      if (b) b.addEventListener("click", loadCurrent);
    }
  }

  function loadCurrent() {
    var cur = currentSlug();
    if (!cur) { setStatus("No investigation is open in the workbench."); return; }
    var sel = el("dcyc-inv");
    if (sel && !Array.prototype.some.call(sel.options, function (o) { return o.value === cur; })) {
      // not in the runnable list (e.g. a study row) — add it so it's selectable
      var o = document.createElement("option");
      o.value = cur; o.textContent = currentTitle(cur);
      sel.appendChild(o);
    }
    if (sel) sel.value = cur;
    onPick(cur, false);
    runCycle();   // one click → load + run
  }

  // ---- data ---------------------------------------------------------------
  function loadInvestigations() {
    var sel = el("dcyc-inv");
    if (!sel) return Promise.resolve();
    return getJSON("/api/discovery-cycle-investigations").then(function (j) {
      var rows = (j && j.investigations) || [];
      if (!rows.length) {
        sel.innerHTML = '<option value="">(no cycle-runnable investigations)</option>';
      } else {
        sel.innerHTML = '<option value="">Choose an investigation…</option>' +
          rows.map(function (r) {
            return '<option value="' + esc(r.slug) + '">' + esc(r.title || r.slug) + "</option>";
          }).join("");
      }
      // restore: last-used slug, else the one open in the workbench
      var want = lsGet(K_SLUG, "") || currentSlug();
      if (want && Array.prototype.some.call(sel.options, function (o) { return o.value === want; })) {
        sel.value = want; onPick(want, false);
      } else {
        syncCurrent();
      }
    }).catch(function () {
      sel.innerHTML = '<option value="">(failed to load)</option>';
    });
  }

  function onPick(slug, resetView) {
    S.slug = slug || "";
    lsSet(K_SLUG, S.slug);
    el("dcyc-run").disabled = !S.slug;
    el("dcyc-run-cont").disabled = !S.slug;
    setStatus("");
    syncCurrent();
    if (!S.slug) {
      S.steps = []; S.byId = {};
      el("dcyc-graph").innerHTML = '<p class="empty-state">Pick an investigation to see its cycle.</p>';
      return;
    }
    if (resetView || !S.steps.length) {
      S.steps = []; S.byId = {}; S.open = {};
      getJSON("/api/discovery-cycle-steps").then(function (j) {
        if (!j || j.available === false) {
          el("dcyc-graph").innerHTML = '<p class="empty-state">The discovery engine is not available in this workspace.</p>';
          el("dcyc-run").disabled = true; el("dcyc-run-cont").disabled = true;
          return;
        }
        setTrace({ steps: (j.steps || []).map(function (s) {
          return Object.assign({}, s, { status: "pending", summary: "not run yet", duration_ms: null });
        }) });
      });
    }
  }

  function setTrace(trace) {
    S.steps = (trace && trace.steps) || [];
    S.byId = {};
    S.steps.forEach(function (s) { S.byId[s.id] = s; });
    renderGraph();
  }

  // ---- render -------------------------------------------------------------
  function renderGraph() {
    var g = el("dcyc-graph");
    if (!g) return;
    if (!S.steps.length) { g.innerHTML = '<p class="empty-state">Load an investigation to see its cycle.</p>'; return; }
    var phases = [];
    S.steps.forEach(function (s) { if (phases.indexOf(s.phase) < 0) phases.push(s.phase); });
    g.innerHTML = phases.map(function (ph) {
      var nodes = S.steps.filter(function (s) { return s.phase === ph; });
      return '<div class="dcyc-phase-h">' + esc(ph) + "</div>" + nodes.map(nodeHtml).join("");
    }).join("");
    Array.prototype.forEach.call(g.querySelectorAll(".dcyc-nh"), function (h) {
      h.addEventListener("click", function () { toggleNode(h.getAttribute("data-id")); });
    });
    Array.prototype.forEach.call(g.querySelectorAll("[data-rerun]"), function (b) {
      b.addEventListener("click", function (ev) { ev.stopPropagation(); rerunStep(b.getAttribute("data-rerun")); });
    });
    Array.prototype.forEach.call(g.querySelectorAll("[data-adv]"), function (b) {
      b.addEventListener("click", function (ev) { ev.stopPropagation(); runContinuous(); });
    });
  }

  function nodeHtml(s) {
    var st = s.status || "pending";
    var exp = !!S.open[s.id];
    var cls = "dcyc-node st-" + st + (s.kind === "mutating" ? " st-mut" : "") + (exp ? " exp sel" : "");
    var dur = (s.duration_ms != null && st !== "pending" && st !== "idle" && st !== "blocked")
      ? '<span class="dcyc-dur">' + s.duration_ms + " ms</span>" : "";
    var head =
      '<div class="dcyc-nh" data-id="' + esc(s.id) + '">' +
      '<span class="dcyc-dot"></span>' +
      '<span class="dcyc-nt"><span class="t">' + esc(s.label) + "</span>" +
      '<span class="fn"> · ' + esc(s.module) + "." + esc(s.func) + "</span>" +
      '<div class="sum">' + esc(s.summary || "") + "</div></span>" +
      dur + '<span class="dcyc-caret">▸</span></div>';
    return '<div class="' + cls + '">' + head + (exp ? detailHtml(s) : "") + "</div>";
  }

  function detailHtml(s) {
    var deps = (s.depends_on || []).length ? (s.depends_on || []).join(", ") : "none";
    var meta = '<div class="meta"><code>' + esc(s.module) + "." + esc(s.func) +
      "</code> · " + esc(s.kind) + " · depends on " + esc(deps) + "</div>";
    if (s.kind === "mutating") {
      return '<div class="dcyc-detail">' + meta +
        '<p class="dcyc-note">Runs real study runs + writes results — only via Advance, ' +
        "against a sandboxed copy, so your real investigation is never touched.</p>" +
        '<div class="dcyc-row-actions"><button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-adv="1">Advance ▸ (sandbox)</button></div></div>';
    }
    var actions = (s.status === "pending" || s.status === "blocked") ? "" :
      '<div class="dcyc-row-actions"><button class="dcyc-btn dcyc-btn-sm" data-rerun="' + esc(s.id) + '">Re-run this step</button></div>';
    var result = ("result" in s) ? jsonBlock(s.result)
      : (s.status === "blocked" ? '<p class="dcyc-note">' + esc(s.error || s.summary) + "</p>"
        : '<p class="empty-state">Run the cycle to populate this step.</p>');
    return '<div class="dcyc-detail">' + meta + actions + result + "</div>";
  }

  function jsonBlock(obj) {
    var txt;
    try { txt = JSON.stringify(obj, null, 2); } catch (e) { txt = String(obj); }
    if (txt == null) txt = "null";
    var CAP = 20000, trunc = txt.length > CAP;
    if (trunc) txt = txt.slice(0, CAP) + "\n… (" + (txt.length - CAP) + " more chars)";
    return '<pre class="dcyc-pre">' + esc(txt) + "</pre>" +
      (trunc ? '<p class="dcyc-note">Result truncated for display.</p>' : "");
  }

  function toggleNode(id) {
    if (S.open[id]) delete S.open[id]; else S.open[id] = true;
    renderGraph();
  }

  // ---- actions ------------------------------------------------------------
  function runCycle() {
    if (!S.slug || S.busy) return;
    S.busy = true;
    el("dcyc-run").disabled = true; el("dcyc-run-cont").disabled = true;
    S.steps.forEach(function (s) { if (s.kind !== "mutating") s.status = "running"; });
    renderGraph();
    setStatus("Running the cycle…");
    var t0 = Date.now();
    getJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug)).then(function (j) {
      if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
      if (!j.steps || !j.steps.length) { setStatus(j.error || "No result returned."); return; }
      setTrace(j);
      var errs = j.steps.filter(function (s) { return s.status === "error"; }).length;
      var blk = j.steps.filter(function (s) { return s.status === "blocked"; }).length;
      setStatus(blk ? (j.error || "Could not load this investigation.")
        : ("Cycle complete in " + ((Date.now() - t0) / 1000).toFixed(1) + "s" +
          (errs ? " · " + errs + " step(s) errored" : "")));
    }).catch(function () { setStatus("Cycle request failed."); })
      .finally(function () {
        S.busy = false;
        el("dcyc-run").disabled = !S.slug; el("dcyc-run-cont").disabled = !S.slug;
      });
  }

  function rerunStep(id) {
    if (!S.slug || S.busy) return;
    S.busy = true;
    var s = S.byId[id]; if (s) { s.status = "running"; renderGraph(); }
    setStatus("Re-running " + id + "…");
    getJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/step/" + encodeURIComponent(id))
      .then(function (j) {
        if (j && j.step) {
          S.byId[id] = j.step;
          for (var i = 0; i < S.steps.length; i++) if (S.steps[i].id === id) S.steps[i] = j.step;
          S.open[id] = true; renderGraph(); setStatus("Re-ran " + id + ".");
        } else { setStatus((j && j.error) || "Re-run failed."); }
      }).catch(function () { setStatus("Re-run request failed."); })
      .finally(function () { S.busy = false; });
  }

  function runContinuous() {
    if (!S.slug || S.busy) return;
    S.busy = true;
    el("dcyc-run").disabled = true; el("dcyc-run-cont").disabled = true;
    setStatus("Advancing in a sandbox (never touches your real files)…");
    getJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/advance?max_runs=6")
      .then(function (j) {
        if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
        if (j.after && j.after.steps && j.after.steps.length) setTrace(j.after);
        var adv = S.byId["advance"];
        if (adv) {
          adv.status = (j.runs && j.runs.length) ? "ok" : "empty";
          adv.summary = (j.runs ? j.runs.length : 0) + " run(s) · frontier " +
            j.frontier_before + "→" + j.frontier_after;
          adv.result = { runs: j.runs, needs_build: j.needs_build,
            frontier_before: j.frontier_before, frontier_after: j.frontier_after,
            advance_error: j.advance_error };
          S.open["advance"] = true;
        }
        renderGraph();
        setStatus("Sandbox advance done · " + ((j.runs || []).length) + " run(s)" +
          (j.advance_error ? " · see the Advance step for details" : ""));
      }).catch(function () { setStatus("Advance request failed."); })
      .finally(function () {
        S.busy = false;
        el("dcyc-run").disabled = !S.slug; el("dcyc-run-cont").disabled = !S.slug;
      });
  }

  // ---- dock registration --------------------------------------------------
  function reflectLauncher() {
    var tog = el(TOGGLE_ID);
    if (!tog) return;
    var active = (ctl && ctl.isOpen()) || !!(popoutWin && !popoutWin.closed);
    tog.classList.toggle("active", active);
    tog.setAttribute("aria-pressed", active ? "true" : "false");
  }

  function init() {
    injectStyle();
    var panel = el(PANEL_ID);
    var layout = document.querySelector(".viv-layout");
    var mainEl = document.querySelector(".viv-main");
    if (!panel || !layout || !mainEl || !global.VivPanelDock) return;
    if (initDone) return;

    buildBody();
    var head = panel.querySelector(".viv-cycle-head");

    ctl = global.VivPanelDock.make({
      panel: panel, layout: layout, mainEl: mainEl, key: "cycle",
      label: "Discovery cycle",
      launcher: el(TOGGLE_ID),
      docks: ["right", "left", "bottom", "top"],
      resizeHandle: "viv-cycle-resize-handle",
      dragHandles: head ? [head] : [],
      popout: function () {
        popoutWin = global.VivPanelDock.popout("cycle");
        if (ctl) ctl.close();
        setTimeout(reflectLauncher, 60);
      },
      defaultDock: "right", defaultSize: { side: 440, bottom: 340 },
      onOpen: function () { buildBody(); syncCurrent(); renderGraph(); reflectLauncher(); if (!built || !el("dcyc-inv").options.length) loadInvestigations(); },
      onClose: function () { reflectLauncher(); if (initDone && inPopout()) returnInline(); },
    });

    global.CyclePanel = {
      toggle: function () { if (ctl) ctl.toggle(); },
      open: function () { if (ctl) ctl.open(); },
      close: function () { if (ctl) ctl.close(); },
      dockMenu: function (a) { if (ctl) ctl.openDockMenu(a); },
      collapse: function () { if (ctl) ctl.collapse(); },
      expand: function () { if (ctl) ctl.expand(); },
      isOpen: function () { return !!(ctl && ctl.isOpen()); },
    };
    global._openCyclePanel = function () { if (ctl) ctl.open(); };

    var tog = el(TOGGLE_ID);
    if (tog) tog.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (ctl.didDrag && ctl.didDrag()) return;
      ctl.toggle();
    });

    loadInvestigations();
    // keep the "open in workbench" affordance fresh as the user navigates the SPA
    global.setInterval(function () {
      if (ctl && ctl.isOpen() && currentSlug() !== S.current) syncCurrent();
      if (popoutWin && popoutWin.closed) { popoutWin = null; reflectLauncher(); }
    }, 1500);

    var isPopout = false;
    try { isPopout = /(?:^|[?&])popout=cycle(?:&|$)/.test(global.location.search || ""); } catch (e) { /* ignore */ }
    if (isPopout) ctl.open();

    reflectLauncher();
    setTimeout(reflectLauncher, 300);
    initDone = true;
  }

  function inPopout() {
    try { return !!(document.body && document.body.classList.contains("viv-popout")); }
    catch (e) { return false; }
  }
  function returnInline() {
    try {
      if (global.opener && !global.opener.closed && typeof global.opener._openCyclePanel === "function")
        global.opener._openCyclePanel();
    } catch (e) { /* ignore */ }
    try { global.close(); } catch (e) { /* ignore */ }
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  }
})(typeof window !== "undefined" ? window : globalThis);
