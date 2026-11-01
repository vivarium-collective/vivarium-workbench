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
  var S = { slug: "", title: "", steps: [], byId: {}, open: {}, busy: false, current: "", summary: null, applyReport: null, delta: null, gatesOpen: true, decideFor: null };

  function el(id) { return document.getElementById(id); }
  function base() { return global.__BASE_PATH__ || ""; }
  function refreshViews() {
    try { if (typeof global._vivRefreshDiscoveryViews === "function") global._vivRefreshDiscoveryViews(S.slug); } catch (e) {}
  }
  function getJSON(path) {
    return fetch(base() + path, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); });
  }
  function postJSON(path, body) {
    // session.js adds X-VW-Session; same-origin Origin satisfies the CSRF guard.
    return fetch(base() + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (r) { return r.json(); });
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
      // Height chain so the graph scrolls inside the dock (these viv-cycle-*
      // classes carry no CSS otherwise; mirrors style.css's .viv-studies-* rules).
      "#viv-cycle-panel .viv-cycle-panel-inner{display:flex;flex-direction:column;height:100%;min-width:0;flex:1}",
      "#viv-cycle-body{flex:1;min-height:0;overflow:hidden;display:flex;flex-direction:column}",
      ".dcyc-wrap{display:flex;flex-direction:column;flex:1;min-height:0;font-size:13px}",
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
      ".dcyc-graph{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:8px 10px 40px}",
      ".dcyc-banner{border-radius:9px;padding:9px 11px;margin:2px 0 8px;border:1px solid var(--viv-border,#e1e3ea)}",
      ".dcyc-bn-closed{background:rgba(22,163,74,.08);border-color:rgba(22,163,74,.35)}",
      ".dcyc-bn-open{background:rgba(37,99,235,.07);border-color:rgba(37,99,235,.3)}",
      ".dcyc-bn-applied{background:rgba(16,185,129,.1);border-color:rgba(16,185,129,.4)}",
      ".dcyc-bn-applied code{font-family:ui-monospace,Menlo,monospace}",
      ".dcyc-bn-h{font-weight:650;font-size:13px}",
      ".dcyc-bn-sub{font-size:11.5px;color:var(--viv-fg,#445);margin-top:3px}",
      ".dcyc-chip{display:inline-block;font-size:10px;padding:0 6px;border-radius:999px;border:1px solid var(--viv-border,#cdd);background:var(--viv-bg,#fff);vertical-align:1px}",
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
      ".empty-state{color:var(--viv-fg-muted,#9aa);font-size:12.5px;padding:14px}",
      // per-stage short description, shown muted in the node
      ".dcyc-nt .desc{font-size:11px;color:var(--viv-fg-muted,#8a93a0);margin-top:2px;line-height:1.35}",
      ".dcyc-detail .desc{font-size:12px;color:var(--viv-fg,#334);margin-bottom:6px;line-height:1.4}",
      ".dcyc-gate-note{font-size:11.5px;color:var(--viv-fg,#445);background:rgba(234,179,8,.08);border:1px solid rgba(234,179,8,.3);border-radius:7px;padding:6px 8px;margin:2px 0 6px}",
      ".dcyc-gate-note b{color:#8a5a00}",
      // gate / frontier inspector
      ".dcyc-gates{border:1px solid var(--viv-border,#e1e3ea);border-radius:9px;margin:2px 0 8px;overflow:hidden}",
      ".dcyc-gates-h{display:flex;align-items:center;gap:7px;padding:7px 10px;cursor:pointer;background:var(--viv-bg-alt,#f7f8fa);font-size:12px}",
      ".dcyc-gates-h:hover{background:var(--viv-bg-alt,#eef1f5)}",
      ".dcyc-gates-h .gt{font-weight:650}",
      ".dcyc-gates-h .gcount{color:var(--viv-fg-muted,#778);font-size:11px}",
      ".dcyc-gates-h .dcyc-caret{margin-left:auto}",
      ".dcyc-gates.exp .dcyc-caret{transform:rotate(90deg)}",
      ".dcyc-gates-body{padding:6px 8px 8px;display:flex;flex-direction:column;gap:5px}",
      ".dcyc-fitem{display:flex;align-items:flex-start;gap:8px;padding:6px 7px;border-radius:7px;border:1px solid var(--viv-border,#eceef3);background:var(--viv-bg,#fff)}",
      ".dcyc-fitem.blk{border-color:rgba(220,38,38,.35);background:rgba(220,38,38,.04)}",
      ".dcyc-fdot{width:8px;height:8px;border-radius:50%;flex:none;margin-top:4px;background:#94a3b8}",
      ".dcyc-fitem.blk .dcyc-fdot{background:#dc2626}",
      ".dcyc-fbody{flex:1;min-width:0}",
      ".dcyc-fkind{font-weight:600;font-size:11.5px}",
      ".dcyc-ftarget{font-size:11px;color:var(--viv-fg-muted,#778);margin-top:1px;word-break:break-word}",
      ".dcyc-fchips{margin-top:3px;display:flex;gap:5px;flex-wrap:wrap}",
      ".dcyc-fchip{font-size:10px;padding:0 6px;border-radius:999px;border:1px solid var(--viv-border,#cdd);background:var(--viv-bg,#fff);white-space:nowrap}",
      ".dcyc-fchip.op{border-color:#c7d2fe;background:rgba(37,99,235,.06);color:#1d4ed8}",
      ".dcyc-fchip.human{border-color:rgba(234,179,8,.5);background:rgba(234,179,8,.1);color:#8a5a00}",
      ".dcyc-fchip.run{border-color:rgba(22,163,74,.45);background:rgba(22,163,74,.08);color:#15803d}",
      ".dcyc-fchip.blkc{border-color:rgba(220,38,38,.45);background:rgba(220,38,38,.08);color:#b91c1c}",
      ".dcyc-gates-foot{font-size:11px;color:var(--viv-fg-muted,#889);padding:2px 2px 0}",
      ".dcyc-fgroup-h{font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--viv-fg-muted,#9aa3af);margin:7px 2px 3px}",
      ".dcyc-factions{margin-top:7px;display:flex;gap:6px;flex-wrap:wrap}",
      ".dcyc-flink{border-color:#c7d2fe;background:rgba(37,99,235,.05);color:#1d4ed8}",
      ".dcyc-decide{margin-top:8px;border-top:1px dashed var(--viv-border,#e3e6ec);padding-top:8px}",
      ".dcyc-decide-ta{width:100%;box-sizing:border-box;font:inherit;font-size:12px;min-height:58px;padding:6px 8px;border-radius:7px;border:1px solid var(--viv-border,#ccd);background:var(--viv-bg,#fff);color:inherit;resize:vertical}",
      ".dcyc-decide .dcyc-row-actions{margin-top:6px}"
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
      '      <button id="dcyc-switch" class="dcyc-btn" disabled title="Show this investigation in the main window (its investigation graph) — switch freely between the graph and the cycle">Switch ▸</button>' +
      '      <button id="dcyc-run" class="dcyc-btn dcyc-btn-primary" disabled>Run</button>' +
      '      <button id="dcyc-run-cont" class="dcyc-btn" disabled title="Advance toward closure in a sandboxed copy — never touches your real files">Advance ▸</button>' +
      '      <button id="dcyc-apply" class="dcyc-btn" disabled title="Write the cycle\'s findings back to the real investigation + studies (verdicts, edges, cycle-state), committed to git">Apply to investigation ▸</button>' +
      '    </div>' +
      '    <div id="dcyc-status" class="dcyc-status"></div>' +
      '  </div>' +
      '  <div id="dcyc-graph" class="dcyc-graph"><p class="empty-state">Load an investigation to see its cycle.</p></div>' +
      '</div>';
    var sel = el("dcyc-inv");
    if (sel) sel.addEventListener("change", function () { onPick(sel.value, true); });
    var r = el("dcyc-run"); if (r) r.addEventListener("click", runCycle);
    var rc = el("dcyc-run-cont"); if (rc) rc.addEventListener("click", runContinuous);
    var ap = el("dcyc-apply"); if (ap) ap.addEventListener("click", applyCycle);
    var sw = el("dcyc-switch"); if (sw) sw.addEventListener("click", switchToInvestigation);
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
    if (isSnapshot()) { renderSnapshotNote(); return; }
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

  // ---- read-only snapshot ------------------------------------------------
  // The cycle is interactive (it runs a live sandbox + writes back on Apply), so a
  // published static bundle has no backend to drive it. Rather than let the picker
  // 404 into "(failed to load)" with dead buttons, show an informative read-only note
  // naming the open investigation + how to run the cycle for real.
  function renderSnapshotNote() {
    var sel = el("dcyc-inv");
    if (sel) { sel.innerHTML = '<option value="">Live workbench only</option>'; sel.disabled = true; }
    ["dcyc-switch", "dcyc-run", "dcyc-run-cont", "dcyc-apply"].forEach(function (id) {
      var b = el(id); if (b) b.disabled = true;
    });
    var cur = currentSlug();
    var box = el("dcyc-current");
    if (box) box.innerHTML = cur
      ? 'Investigation: <b>' + esc(currentTitle(cur)) + "</b>"
      : "<span>Open an investigation to see which cycle would run.</span>";
    var g = el("dcyc-graph");
    if (g) g.innerHTML =
      '<div class="empty-state" style="text-align:left;line-height:1.5">' +
      "<p><b>The discovery cycle is interactive.</b></p>" +
      '<p class="muted">It runs the investigation\'s build → simulate → compare → score → steer ' +
      "loop in a live sandbox, proposes the next experiments, and (on Apply) writes verdicts and " +
      "edges back to the studies — so it works in the running workbench, not in this read-only snapshot.</p>" +
      '<p class="muted">Run the workbench locally on this workspace to load ' +
      (cur ? "“" + esc(currentTitle(cur)) + "”" : "an investigation") +
      " into the cycle and Run / Advance / Apply it.</p>" +
      '<p><a href="https://github.com/vivarium-collective/vivarium-workbench" target="_blank" rel="noopener">Get vivarium-workbench →</a></p>' +
      "</div>";
  }

  // ---- data ---------------------------------------------------------------
  function loadInvestigations() {
    var sel = el("dcyc-inv");
    if (!sel) return Promise.resolve();
    if (isSnapshot()) { renderSnapshotNote(); return Promise.resolve(); }
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

  // Show the panel's selected investigation in the MAIN window (its investigation
  // graph), so you can move freely between the graph and the cycle on the same
  // investigation without re-picking it in the switcher.
  function switchToInvestigation() {
    var slug = S.slug || (el("dcyc-inv") && el("dcyc-inv").value) || "";
    if (!slug) return;
    try {
      if (typeof window._openInvestigationDetail === "function") {
        window._openInvestigationDetail(slug);
        setStatus("Showing “" + currentTitle(slug) + "” in the main window.");
      }
    } catch (e) { /* ignore */ }
  }

  function onPick(slug, resetView) {
    S.slug = slug || "";
    lsSet(K_SLUG, S.slug);
    el("dcyc-run").disabled = !S.slug;
    el("dcyc-run-cont").disabled = !S.slug;
    var sw = el("dcyc-switch"); if (sw) sw.disabled = !S.slug;
    var ap = el("dcyc-apply"); if (ap) ap.disabled = true;   // re-enabled after a run
    S.applyReport = null;
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
    if (trace && "summary" in trace) S.summary = trace.summary || null;
    S.byId = {};
    S.steps.forEach(function (s) { S.byId[s.id] = s; });
    renderGraph();
    loadDelta();
  }


  // "What changed this cycle": the engine's discovery_delta vs the persisted
  // evaluation snapshot (GET /api/discovery-delta/{slug}). Live-only.
  function isSnapshot() { return ((global.__DASH_CONFIG__ || {}).mode) === "snapshot"; }
  function loadDelta() {
    if (isSnapshot() || !S.slug) { S.delta = null; return; }
    var slug = S.slug;
    getJSON("/api/discovery-delta/" + encodeURIComponent(slug)).then(function (d) {
      if (slug !== S.slug) return;
      S.delta = d && d.available ? d : null;
      renderGraph();
    }).catch(function () { /* optional surface */ });
  }
  function deltaHtml() {
    var d = S.delta;
    if (!d) return "";
    var p = d.proposals || {};
    var n = function (a) { return (a || []).length; };
    if (d.unchanged) {
      return '<div class="dcyc-banner dcyc-bn-open"><div class="dcyc-bn-h">What changed this cycle</div>' +
        '<div class="dcyc-bn-sub">' + (d.first_cycle ? "No prior snapshot yet (first cycle) — no change to report." : "No change since last cycle.") + "</div></div>";
    }
    var chips = [
      n(d.added_or_qualified) + " added/qualified", n(d.gated) + " gated",
      n(p.evaluated) + " proposals evaluated", n(d.regressions) + " regressions"
    ].map(function (t) { return '<span class="dcyc-chip">' + esc(t) + "</span>"; }).join(" ");
    var names = (d.changed || []).map(function (c) {
      return esc(c.study) + ": " + esc(c.from) + " → " + esc(c.to);
    }).concat((d.new_model_versions || []).map(function (m) { return "new model " + esc(m); }));
    return '<div class="dcyc-banner dcyc-bn-open"><div class="dcyc-bn-h">What changed this cycle</div>' +
      '<div class="dcyc-bn-sub">' + chips + "</div>" +
      (names.length ? '<div class="dcyc-bn-sub">' + names.join(" · ") + "</div>" : "") + "</div>";
  }
  document.addEventListener("viv:discovery-changed", function () { if (S.slug) loadDelta(); });

  // Viz-appropriateness: census line + the opt-in toggle for the `viz_untied` rollout flag.
  function vizApprHtml() {
    var m = S.summary;
    if (!m || m.viz_untied_enabled == null) return "";
    var va = m.viz_appropriateness || {};
    var parts = [];
    if (va.misplaced) parts.push(va.misplaced + " misplaced");
    if (va.duplicated) parts.push(va.duplicated + " duplicated");
    if (va.untied_surfaced && va.untied) parts.push(va.untied + " untied");
    var census = parts.length ? parts.join(" · ") : "no issues";
    var na = va.not_assessed ? " · " + va.not_assessed + " not assessed" : "";
    return '<div class="dcyc-bn-sub" id="dcyc-viz-appr">Figure appropriateness: ' + esc(census + na) +
      ' <label title="Surface figures that are not tied to any observable (rollout flag cycle_flags.viz_untied)">' +
      '<input type="checkbox" id="dcyc-untied-toggle"' + (m.viz_untied_enabled ? " checked" : "") +
      "> flag untied figures</label></div>";
  }

  function toggleUntied(enabled) {
    if (!S.slug || S.busy) return;
    S.busy = true; setStatus((enabled ? "Enabling" : "Disabling") + " untied-figure flagging…");
    postJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/toggle-untied", { enabled: !!enabled })
      .then(function (j) {
        if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
        if (j.after && j.after.steps && j.after.steps.length) setTrace(j.after);
        setStatus(j.ok ? ("Untied-figure flagging " + (j.enabled ? "on." : "off.")) : (j.detail || "Toggle failed."));
      }).catch(function () { setStatus("Toggle request failed."); })
      .finally(function () { S.busy = false; });
  }

  function bannerHtml() {
    var m = S.summary;
    if (!m || m.closed == null) return "";
    var closed = !!m.closed;
    var na = m.next_action;
    var cls = closed ? "dcyc-bn-closed" : "dcyc-bn-open";
    var head = closed
      ? "✓ Closed · " + (m.frontier_size || 0) + " open (non-blocking)"
      : "◷ Open · " + (m.frontier_size || 0) + " on the frontier";
    var runline = "";
    if (m.runnable === 0 && m.human_decisions > 0) {
      runline = '<div class="dcyc-bn-sub">Nothing to auto-run — ' + m.human_decisions +
        " human decision(s) pending. Running a cycle analyzes; it won't advance these.</div>";
    } else if (m.runnable > 0) {
      runline = '<div class="dcyc-bn-sub">' + m.runnable + " runnable step(s) — use Advance (sandbox).</div>";
    }
    var naline = na
      ? '<div class="dcyc-bn-sub">Next: <b>' + esc(na.op || na.kind) + "</b>" +
        (na.consequence ? ' <span class="dcyc-chip">' + esc(na.consequence) + "</span>" : "") +
        (na.target ? " — " + esc(String(na.target).slice(0, 90)) : "") + "</div>"
      : "";
    return '<div class="dcyc-banner ' + cls + '"><div class="dcyc-bn-h">' + head + "</div>" +
      naline + runline + vizApprHtml() + "</div>";
  }

  // The gate inspector: every open item on the engine's frontier, split into the
  // gates that BLOCK closure vs the ones merely tracked, each showing what it is,
  // what it's waiting on, which studies it points at (jump-in links), and — for
  // decisions — the controls to decide it here or let the AI steer it.
  function fitemHtml(it) {
    var chips = [];
    if (it.op) chips.push('<span class="dcyc-fchip op">' + esc(it.op) + "</span>");
    if (it.consequence)
      chips.push('<span class="dcyc-fchip ' + (it.human ? "human" : "run") + '">' +
        esc(it.consequence) + (it.human ? " · human" : " · runnable") + "</span>");
    if (it.blocking) chips.push('<span class="dcyc-fchip blkc">blocks closure</span>');
    // jump-in links to the studies this gate points at
    var links = (it.studies || []).map(function (s) {
      return '<button class="dcyc-btn dcyc-btn-sm dcyc-flink" data-study="' + esc(s.slug) +
        '" data-tab="' + esc(s.tab || "overview") + '" title="Open ' + esc(s.slug) +
        " · " + esc(s.tab || "overview") + '">Open ' + esc(s.label) + " ▸</button>";
    }).join("");
    // decide / resolve / AI-steer controls
    var acts = [];
    if (it.decision) {
      acts.push('<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-decide="' +
        esc(it.target) + '" data-kind="' + esc(it.kind) + '">Decide…</button>');
      acts.push('<button class="dcyc-btn dcyc-btn-sm" data-steer="' + esc(it.target) +
        '" title="Steer this decision with the AI assistant (opens the chat)">AI steer</button>');
    } else if (it.resolvable && !/^viz_/.test(it.kind || "")) {
      acts.push('<button class="dcyc-btn dcyc-btn-sm" data-decide="' + esc(it.target) +
        '" data-kind="' + esc(it.kind) + '">Resolve…</button>');
    }
    if (it.buildable) {
      acts.push('<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-build="' +
        esc(it.target) + '" title="Render this figure now (in-process)">Build ▸</button>');
    } else if (it.declarable) {
      acts.push('<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-fix="' +
        esc(it.target) + '" title="Declare an address / render recipe for this figure">Apply fix ▸</button>');
    }
    if (it.acceptable) {
      acts.push('<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-vp-accept="' +
        esc(it.target) + '" title="Declare this visualization (rationale required)">Accept ▸</button>');
      acts.push('<button class="dcyc-btn dcyc-btn-sm" data-vp-reject="' +
        esc(it.target) + '" title="Reject this proposal (terminal; rationale required)">Reject ▸</button>');
    }
    if (it.acceptable_gap) {
      acts.push('<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-cg-accept="' +
        esc(it.target) + '" title="Accept this capability gap — authorizes a build (rationale required)">Accept ▸</button>');
      acts.push('<button class="dcyc-btn dcyc-btn-sm" data-cg-reject="' +
        esc(it.target) + '" title="Reject this gap (terminal; rationale required)">Reject ▸</button>');
    }
    if (it.buildable_gap) {
      acts.push('<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-cg-build="' +
        esc(it.target) + '" title="Build this mechanism via the workspace build hook">' +
        (it.gap_status === "failed" ? "Retry build ▸" : "Build ▸") + "</button>");
    }
    var desc = it.rationale || it.target || "";
    var form = (S.decideFor === it.target) ? decideFormHtml(it)
      : ((S.fixFor === it.target && it.declarable) ? fixFormHtml(it)
      : ((S.vpFor && S.vpFor.id === it.target && it.acceptable) ? vpFormHtml(it)
      : ((S.cgFor && S.cgFor.id === it.target && it.acceptable_gap) ? cgFormHtml(it) : "")));
    var actionRow = (links || acts.length)
      ? ('<div class="dcyc-factions">' + links + acts.join("") + "</div>") : "";
    return '<div class="dcyc-fitem' + (it.blocking ? " blk" : "") + '">' +
      '<span class="dcyc-fdot"></span>' +
      '<div class="dcyc-fbody">' +
      '<div class="dcyc-fkind">' + esc(it.kind_label || it.kind || "") + "</div>" +
      (desc ? '<div class="dcyc-ftarget">' + esc(desc) + "</div>" : "") +
      '<div class="dcyc-fchips">' + chips.join("") + "</div>" +
      actionRow + form +
      "</div></div>";
  }

  function fixFormHtml(it) {
    var fx = it.derivable_fix || {};
    return '<div class="dcyc-decide">' +
      '<input class="dcyc-fix-addr" id="dcyc-fix-addr" placeholder="address, e.g. html:viz/name.html" value="' +
      esc(fx.address || "") + '" style="width:100%;margin-bottom:4px">' +
      '<input class="dcyc-fix-render" id="dcyc-fix-render" placeholder="render command (optional), e.g. python viz.py {chart}" ' +
      'value="' + esc(fx.render || "") + '" style="width:100%;margin-bottom:4px">' +
      '<textarea class="dcyc-decide-ta" id="dcyc-fix-why" placeholder="Why this fix (required)"></textarea>' +
      '<div class="dcyc-row-actions">' +
      '<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-fix-save="' + esc(it.target) + '">Apply</button>' +
      '<button class="dcyc-btn dcyc-btn-sm" data-fix-cancel="1">Cancel</button>' +
      "</div></div>";
  }

  function vpFormHtml(it) {
    var accept = S.vpFor.mode === "accept";
    var sug = it.suggested || {};
    var addr = accept
      ? ('<input class="dcyc-fix-addr" id="dcyc-vp-addr" placeholder="' +
         (it.needs_author ? "address (required — no catalog class matches), e.g. local:MyViz"
                          : "address (optional — defaults to the suggested one)") +
         '" value="' + esc(sug.address || "") + '" style="width:100%;margin-bottom:4px">')
      : "";
    return '<div class="dcyc-decide">' + addr +
      '<textarea class="dcyc-decide-ta" id="dcyc-vp-why" placeholder="' +
      (accept ? "Why accept this visualization (required)" : "Why reject this proposal (required)") +
      '"></textarea><div class="dcyc-row-actions">' +
      '<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-vp-save="' + esc(it.target) + '">' +
      (accept ? "Accept" : "Reject") + '</button>' +
      '<button class="dcyc-btn dcyc-btn-sm" data-vp-cancel="1">Cancel</button></div></div>';
  }

  function cgFormHtml(it) {
    var accept = S.cgFor.mode === "accept";
    return '<div class="dcyc-decide">' +
      '<textarea class="dcyc-decide-ta" id="dcyc-cg-why" placeholder="' +
      (accept ? "Why accept this capability gap / authorize building the mechanism (required)"
              : "Why reject this gap (required)") +
      '"></textarea><div class="dcyc-row-actions">' +
      '<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-cg-save="' + esc(it.target) + '">' +
      (accept ? "Accept" : "Reject") + '</button>' +
      '<button class="dcyc-btn dcyc-btn-sm" data-cg-cancel="1">Cancel</button></div></div>';
  }

  function submitVizProposal(id) {
    var mode = S.vpFor && S.vpFor.mode;
    var why = el("dcyc-vp-why"), addr = el("dcyc-vp-addr");
    var rationale = why ? why.value.trim() : "";
    if (!rationale) { setStatus("Enter a rationale."); if (why) why.focus(); return; }
    if (!S.slug || S.busy) return;
    var body = { proposal: id, rationale: rationale };
    if (mode === "accept" && addr && addr.value.trim()) body.address = addr.value.trim();
    S.busy = true; setStatus((mode === "accept" ? "Accepting " : "Rejecting ") + id + "…");
    postJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/" + mode + "-viz-proposal", body)
      .then(function (j) {
        if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
        if (!j.ok) { setStatus(j.detail || "Could not record the decision."); return; }
        S.vpFor = null;
        if (j.after && j.after.steps && j.after.steps.length) setTrace(j.after);
        var c = j.commit || {};
        setStatus((mode === "accept" ? "Accepted" : "Rejected") + (c.committed ? (" · committed " + c.sha) : "") + ".");
        refreshViews();
      }).catch(function () { setStatus("Request failed."); })
      .finally(function () { S.busy = false; });
  }

  function buildViz(target) {
    if (!S.slug || S.busy) return;
    S.busy = true; setStatus("Rendering " + target + "…");
    postJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/build-viz", { target: target })
      .then(function (j) {
        if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
        if (j.after && j.after.steps && j.after.steps.length) setTrace(j.after);
        setStatus(j.ok ? ("Rendered " + target + ".") : ("Build failed: " + (j.detail || "unknown")));
        if (j.ok) refreshViews();
      }).catch(function () { setStatus("Build request failed."); })
      .finally(function () { S.busy = false; });
  }

  function submitCapabilityGap(id) {
    var mode = S.cgFor && S.cgFor.mode;
    var why = el("dcyc-cg-why");
    var rationale = why ? why.value.trim() : "";
    if (!rationale) { setStatus("Enter a rationale."); if (why) why.focus(); return; }
    if (!S.slug || S.busy) return;
    S.busy = true; setStatus((mode === "accept" ? "Accepting " : "Rejecting ") + id + "…");
    postJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/" + mode + "-capability-gap",
      { gap: id, rationale: rationale })
      .then(function (j) {
        if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
        if (!j.ok) { setStatus(j.detail || "Could not record the decision."); return; }
        S.cgFor = null;
        if (j.after && j.after.steps && j.after.steps.length) setTrace(j.after);
        var c = j.commit || {};
        setStatus((mode === "accept" ? "Accepted — build it next" : "Rejected") +
          (c.committed ? (" · committed " + c.sha) : "") + ".");
        refreshViews();
      }).catch(function () { setStatus("Request failed."); })
      .finally(function () { S.busy = false; });
  }

  function buildCapabilityGap(target) {
    if (!S.slug || S.busy) return;
    S.busy = true; setStatus("Building " + target + "…");
    postJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/build-capability-gap", { gap: target })
      .then(function (j) {
        if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
        if (!j.ok) { setStatus(j.detail || "Build could not start."); return; }
        if (j.after && j.after.steps && j.after.steps.length) setTrace(j.after);
        setStatus(j.built ? ("Built " + target + " — closes on coverage next cycle.")
          : ("Build outcome: " + (j.status || "needs author") + " — a human must author the mechanism."));
        refreshViews();
      }).catch(function () { setStatus("Build request failed."); })
      .finally(function () { S.busy = false; });
  }

  function submitFix(target) {
    var why = el("dcyc-fix-why"), addr = el("dcyc-fix-addr"), rend = el("dcyc-fix-render");
    var rationale = why ? why.value.trim() : "";
    var declare = {};
    if (addr && addr.value.trim()) declare.address = addr.value.trim();
    if (rend && rend.value.trim()) declare.render = rend.value.trim();
    if (!declare.address && !declare.render) { setStatus("Enter an address or a render command."); return; }
    if (!rationale) { setStatus("Enter a rationale for the fix."); if (why) why.focus(); return; }
    if (!S.slug || S.busy) return;
    S.busy = true; setStatus("Applying fix…");
    postJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/declare-viz",
      { target: target, declare: declare, rationale: rationale })
      .then(function (j) {
        if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
        if (!j.ok) { setStatus(j.detail || "Could not apply the fix."); return; }
        S.fixFor = null;
        if (j.after && j.after.steps && j.after.steps.length) setTrace(j.after);
        var c = j.commit || {};
        setStatus("Fix applied" + (c.committed ? (" · committed " + c.sha) : "") + ".");
        refreshViews();
      }).catch(function () { setStatus("Declare request failed."); })
      .finally(function () { S.busy = false; });
  }

  function decideFormHtml(it) {
    var ph = it.decision
      ? "Your decision + why — e.g. “Syntrophy is primary (strongest energetic coupling); test commensalism as the main alternative.”"
      : "Why this item is resolved…";
    return '<div class="dcyc-decide">' +
      '<textarea class="dcyc-decide-ta" id="dcyc-decide-ta" placeholder="' + esc(ph) + '"></textarea>' +
      '<div class="dcyc-row-actions">' +
      '<button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-decide-save="' + esc(it.target) +
      '" data-kind="' + esc(it.kind) + '">Record decision</button>' +
      '<button class="dcyc-btn dcyc-btn-sm" data-decide-cancel="1">Cancel</button>' +
      "</div></div>";
  }

  function frontierHtml() {
    var m = S.summary;
    if (!m || !m.frontier || !m.frontier.length) return "";
    var items = m.frontier.slice();
    var blocking = items.filter(function (x) { return x.blocking; })
      .sort(function (a, b) { return (b.priority || 0) - (a.priority || 0); });
    var tracked = items.filter(function (x) { return !x.blocking; })
      .sort(function (a, b) { return (b.priority || 0) - (a.priority || 0); });
    var exp = !!S.gatesOpen;
    var count = items.length + " open · " + blocking.length + " blocking · " + tracked.length + " tracked";
    var dotColor = m.closed ? "#16a34a" : (blocking.length ? "#dc2626" : "#2563eb");
    function group(title, arr) {
      if (!arr.length) return "";
      return '<div class="dcyc-fgroup-h">' + esc(title) + " (" + arr.length + ")</div>" +
        arr.map(fitemHtml).join("");
    }
    var body = group("Blocking — must clear to close", blocking) +
      group("Tracked — non-blocking", tracked);
    var foot = blocking.length
      ? ""
      : '<div class="dcyc-gates-foot">No blocking gates — the investigation is closed. ' +
        "The items above are tracked (decisions, debts, questions) but don't block closure.</div>";
    return '<div class="dcyc-gates' + (exp ? " exp" : "") + '">' +
      '<div class="dcyc-gates-h" id="dcyc-gates-h">' +
      '<span class="dcyc-dot" style="background:' + dotColor + '"></span>' +
      '<span class="gt">Gates &amp; frontier</span> <span class="gcount">' + count + "</span>" +
      '<span class="dcyc-caret">▸</span></div>' +
      (exp ? ('<div class="dcyc-gates-body">' + body + foot + "</div>") : "") +
      "</div>";
  }

  // ---- gate-item actions --------------------------------------------------
  function openStudyLink(slug, tab) {
    if (!slug) return;
    try {
      if (typeof global._openStudyEmbeddedNewTab === "function") {
        global._openStudyEmbeddedNewTab(slug, tab || "overview");
        setStatus("Opened " + slug + ".");
      } else if (typeof global._openStudyEmbedded === "function") {
        global._openStudyEmbedded(slug);
      } else {
        setStatus("Can't open a study from here (no live workbench).");
      }
    } catch (e) { setStatus("Couldn't open the study."); }
  }

  function submitDecision(target, kind) {
    var ta = el("dcyc-decide-ta");
    var rationale = ta ? ta.value.trim() : "";
    if (!rationale) { setStatus("Enter your decision + rationale first."); if (ta) ta.focus(); return; }
    if (!S.slug || S.busy) return;
    S.busy = true; setStatus("Recording decision…");
    postJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/resolve",
      { kind: kind, target: target, rationale: rationale })
      .then(function (j) {
        if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
        if (!j.ok) { setStatus(j.detail || "Could not record the decision."); return; }
        S.decideFor = null;
        if (j.after && j.after.steps && j.after.steps.length) setTrace(j.after);
        var c = j.commit || {};
        setStatus("Decision recorded" + (c.committed ? (" · committed " + c.sha) : "") + ".");
        refreshViews();
      }).catch(function () { setStatus("Resolve request failed."); })
      .finally(function () { S.busy = false; });
  }

  function findFrontierItem(target) {
    var fr = (S.summary && S.summary.frontier) || [];
    for (var i = 0; i < fr.length; i++) if (fr[i].target === target) return fr[i];
    return null;
  }

  // The built-in AI assistant is "ready" when the chat extra is installed, a
  // provider+model is selected, and that provider is configured (mirrors chat.js).
  function aiReady(st) {
    return !!(st && st.available && st.selected &&
      (st.providers || []).some(function (p) {
        return st.selected && p.id === st.selected.provider && p.configured;
      }));
  }

  function decisionPrompt(it) {
    var slug = S.slug, title = currentTitle(slug) || slug;
    var studyList = (it.studies || []);
    var studies = studyList.map(function (s) { return s.label + " (" + s.slug + ")"; }).join(", ");
    var firstStudy = studyList[0] ? studyList[0].label : "";
    var L = [
      'You are helping steer an open decision in the discovery cycle for the investigation "' +
        title + '" (' + slug + ").",
      "This is a human-only decision: give me your recommendation, then record it.",
      "",
      "Decision: " + (it.target || ""),
    ];
    if (it.context) L.push("Why it's open: " + it.context);
    if (studies) L.push("Involved study: " + studies);
    L.push("");
    L.push("First review this investigation" +
      (firstStudy ? (" and study " + firstStudy) : "") +
      " — the hypotheses, evidence, and current verdicts — with your workspace tools. Then:");
    L.push("1. Recommend the primary choice and the competing alternatives to run head-to-head, " +
      "named as the study's own hypotheses where possible, with a 1-2 sentence rationale grounded in the evidence.");
    L.push("2. Record it: call the discovery-cycle resolve operation " +
      "(POST /api/discovery-cycle/" + slug + "/resolve) with kind=\"open_decision\", " +
      "target set to the exact decision text above, and rationale = your choice + why. " +
      "(I'll be asked to approve the write.)");
    L.push("3. Tell me what you recorded.");
    return L.join("\n");
  }

  // "AI steer" routes to the built-in chat assistant: if it's set up, seed it with
  // the decision (the assistant can record it via the workbench API tools); if not,
  // open the chat so the user can configure it (claude-code needs no API key).
  function steerDecision(target) {
    if (!S.slug) return;
    var it = findFrontierItem(target) || { target: target, studies: [] };
    setStatus("Opening the AI assistant…");
    getJSON("/api/ai/status").then(function (st) {
      if (!aiReady(st)) {
        if (typeof global._openAiPanel === "function") global._openAiPanel();
        var why = (st && st.available)
          ? "pick a provider in the chat (claude-code needs no key)"
          : "install the chat extra + pick a provider (claude-code needs no key)";
        setStatus("AI assistant isn't set up yet — " + why + ", then try AI steer again.");
        return;
      }
      if (typeof global._aiSeedPrompt === "function") {
        global._aiSeedPrompt(decisionPrompt(it));
        setStatus("Decision sent to the AI assistant — review and send it in the chat.");
      } else if (typeof global._openAiPanel === "function") {
        global._openAiPanel();
        setStatus("Opened the AI assistant.");
      }
    }).catch(function () {
      if (typeof global._openAiPanel === "function") global._openAiPanel();
      setStatus("Opened the AI assistant for setup.");
    });
  }

  // ---- render -------------------------------------------------------------
  function renderGraph() {
    var g = el("dcyc-graph");
    if (!g) return;
    if (!S.steps.length) { g.innerHTML = '<p class="empty-state">Load an investigation to see its cycle.</p>'; return; }
    var phases = [];
    S.steps.forEach(function (s) { if (phases.indexOf(s.phase) < 0) phases.push(s.phase); });
    g.innerHTML = applyReportHtml() + deltaHtml() + bannerHtml() + frontierHtml() + phases.map(function (ph) {
      var nodes = S.steps.filter(function (s) { return s.phase === ph; });
      return '<div class="dcyc-phase-h">' + esc(ph) + "</div>" + nodes.map(nodeHtml).join("");
    }).join("");
    var dm = el("dcyc-apply-dismiss");
    if (dm) dm.addEventListener("click", function () { S.applyReport = null; renderGraph(); });
    var gh = el("dcyc-gates-h");
    if (gh) gh.addEventListener("click", function () { S.gatesOpen = !S.gatesOpen; renderGraph(); });
    // gate-item actions: jump to a study, open the decide form, record / AI-steer
    Array.prototype.forEach.call(g.querySelectorAll("[data-study]"), function (b) {
      b.addEventListener("click", function (ev) {
        ev.stopPropagation(); openStudyLink(b.getAttribute("data-study"), b.getAttribute("data-tab"));
      });
    });
    Array.prototype.forEach.call(g.querySelectorAll("[data-decide]"), function (b) {
      b.addEventListener("click", function (ev) {
        ev.stopPropagation(); S.decideFor = b.getAttribute("data-decide"); renderGraph();
        var ta = el("dcyc-decide-ta"); if (ta) ta.focus();
      });
    });
    var ut = g.querySelector("#dcyc-untied-toggle");
    if (ut) ut.addEventListener("change", function () { toggleUntied(ut.checked); });
    Array.prototype.forEach.call(g.querySelectorAll("[data-build]"), function (b) {
      b.addEventListener("click", function (ev) { ev.stopPropagation(); buildViz(b.getAttribute("data-build")); });
    });
    Array.prototype.forEach.call(g.querySelectorAll("[data-fix]"), function (b) {
      b.addEventListener("click", function (ev) {
        ev.stopPropagation(); S.fixFor = b.getAttribute("data-fix"); S.decideFor = null; renderGraph();
        var a = el("dcyc-fix-why"); if (a) a.focus();
      });
    });
    ["accept", "reject"].forEach(function (mode) {
      Array.prototype.forEach.call(g.querySelectorAll("[data-vp-" + mode + "]"), function (b) {
        b.addEventListener("click", function (ev) {
          ev.stopPropagation(); S.vpFor = { id: b.getAttribute("data-vp-" + mode), mode: mode };
          S.fixFor = null; S.decideFor = null; renderGraph();
          var a = el("dcyc-vp-why"); if (a) a.focus();
        });
      });
    });
    var vcl = g.querySelector("[data-vp-cancel]");
    if (vcl) vcl.addEventListener("click", function (ev) { ev.stopPropagation(); S.vpFor = null; renderGraph(); });
    var vsv = g.querySelector("[data-vp-save]");
    if (vsv) vsv.addEventListener("click", function (ev) { ev.stopPropagation(); submitVizProposal(vsv.getAttribute("data-vp-save")); });
    ["accept", "reject"].forEach(function (mode) {
      Array.prototype.forEach.call(g.querySelectorAll("[data-cg-" + mode + "]"), function (b) {
        b.addEventListener("click", function (ev) {
          ev.stopPropagation(); S.cgFor = { id: b.getAttribute("data-cg-" + mode), mode: mode };
          S.vpFor = null; S.fixFor = null; S.decideFor = null; renderGraph();
          var a = el("dcyc-cg-why"); if (a) a.focus();
        });
      });
    });
    var ccl = g.querySelector("[data-cg-cancel]");
    if (ccl) ccl.addEventListener("click", function (ev) { ev.stopPropagation(); S.cgFor = null; renderGraph(); });
    var csv = g.querySelector("[data-cg-save]");
    if (csv) csv.addEventListener("click", function (ev) { ev.stopPropagation(); submitCapabilityGap(csv.getAttribute("data-cg-save")); });
    Array.prototype.forEach.call(g.querySelectorAll("[data-cg-build]"), function (b) {
      b.addEventListener("click", function (ev) { ev.stopPropagation(); buildCapabilityGap(b.getAttribute("data-cg-build")); });
    });
    var fcl = g.querySelector("[data-fix-cancel]");
    if (fcl) fcl.addEventListener("click", function (ev) { ev.stopPropagation(); S.fixFor = null; renderGraph(); });
    var fsv = g.querySelector("[data-fix-save]");
    if (fsv) fsv.addEventListener("click", function (ev) { ev.stopPropagation(); submitFix(fsv.getAttribute("data-fix-save")); });
    var dcl = g.querySelector("[data-decide-cancel]");
    if (dcl) dcl.addEventListener("click", function (ev) { ev.stopPropagation(); S.decideFor = null; renderGraph(); });
    var dsv = g.querySelector("[data-decide-save]");
    if (dsv) dsv.addEventListener("click", function (ev) {
      ev.stopPropagation(); submitDecision(dsv.getAttribute("data-decide-save"), dsv.getAttribute("data-kind"));
    });
    Array.prototype.forEach.call(g.querySelectorAll("[data-steer]"), function (b) {
      b.addEventListener("click", function (ev) { ev.stopPropagation(); steerDecision(b.getAttribute("data-steer")); });
    });
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
      (s.desc ? '<div class="desc">' + esc(s.desc) + "</div>" : "") +
      '<div class="sum">' + esc(s.summary || "") + "</div></span>" +
      dur + '<span class="dcyc-caret">▸</span></div>';
    return '<div class="' + cls + '">' + head + (exp ? detailHtml(s) : "") + "</div>";
  }

  function detailHtml(s) {
    var deps = (s.depends_on || []).length ? (s.depends_on || []).join(", ") : "none";
    var desc = s.desc ? '<div class="desc">' + esc(s.desc) + "</div>" : "";
    var meta = '<div class="meta"><code>' + esc(s.module) + "." + esc(s.func) +
      "</code> · " + esc(s.kind) + " · depends on " + esc(deps) + "</div>";
    var gate = s.gate ? '<div class="dcyc-gate-note"><b>Gate:</b> ' + esc(s.gate) + "</div>" : "";
    if (s.kind === "mutating") {
      return '<div class="dcyc-detail">' + desc + meta + gate +
        '<p class="dcyc-note">Runs real study runs + writes results — only via Advance, ' +
        "against a sandboxed copy, so your real investigation is never touched.</p>" +
        '<div class="dcyc-row-actions"><button class="dcyc-btn dcyc-btn-sm dcyc-btn-primary" data-adv="1">Advance ▸ (sandbox)</button></div></div>';
    }
    var actions = (s.status === "pending" || s.status === "blocked") ? "" :
      '<div class="dcyc-row-actions"><button class="dcyc-btn dcyc-btn-sm" data-rerun="' + esc(s.id) + '">Re-run this step</button></div>';
    var result = ("result" in s) ? jsonBlock(s.result)
      : (s.status === "blocked" ? '<p class="dcyc-note">' + esc(s.error || s.summary) + "</p>"
        : '<p class="empty-state">Run the cycle to populate this step.</p>');
    return '<div class="dcyc-detail">' + desc + meta + gate + actions + result + "</div>";
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
      var ap = el("dcyc-apply"); if (ap) ap.disabled = !!blk;   // can apply once a real run exists
      setStatus(blk ? (j.error || "Could not load this investigation.")
        : ("Cycle complete in " + ((Date.now() - t0) / 1000).toFixed(1) + "s" +
          (errs ? " · " + errs + " step(s) errored" : "")));
      if (!blk) refreshViews();
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
        refreshViews();
      }).catch(function () { setStatus("Advance request failed."); })
      .finally(function () {
        S.busy = false;
        el("dcyc-run").disabled = !S.slug; el("dcyc-run-cont").disabled = !S.slug;
      });
  }

  function applyCycle() {
    if (!S.slug || S.busy) return;
    var name = currentTitle(S.slug);
    if (!global.confirm(
      "Apply the cycle's findings to “" + name + "”?\n\n" +
      "This writes the engine's verdicts onto the study cards, enriches the builds-on " +
      "edges, and persists the cycle-state to the REAL investigation + study files, then " +
      "commits them to the workspace git (revertible). Human-only decisions are left for you.")) return;
    S.busy = true;
    ["dcyc-run", "dcyc-run-cont", "dcyc-apply"].forEach(function (id) { var e = el(id); if (e) e.disabled = true; });
    setStatus("Applying to the investigation…");
    postJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/apply", { scope: "all" })
      .then(function (j) {
        if (!j || j.available === false) { setStatus("Discovery engine unavailable."); return; }
        if (j.error) { setStatus(j.error); return; }
        S.applyReport = j.report || {};
        if (j.after && j.after.steps && j.after.steps.length) setTrace(j.after); else renderGraph();
        var r = j.report || {};
        var nv = (r.verdicts || []).length;
        var commit = r.commit || {};
        var ctxt = commit.committed ? (" · committed " + commit.sha)
          : (commit.reason ? (" · " + commit.reason) : "");
        setStatus("Applied · " + nv + " verdict(s)" + ctxt);
        refreshViews();
      })
      .catch(function () { setStatus("Apply request failed."); })
      .finally(function () {
        S.busy = false;
        el("dcyc-run").disabled = !S.slug; el("dcyc-run-cont").disabled = !S.slug;
        el("dcyc-apply").disabled = !S.slug;
      });
  }

  function applyReportHtml() {
    var r = S.applyReport;
    if (!r) return "";
    var parts = [];
    var nv = (r.verdicts || []).length;
    if (nv) parts.push("<b>" + nv + "</b> verdict(s) written: " +
      r.verdicts.map(function (v) { return esc(v.study) + " → " + esc(v.to); }).join(", "));
    else parts.push("no verdicts changed");
    var enr = (r.edges && r.edges.enriched) ? Object.keys(r.edges.enriched).length : 0;
    if (enr) parts.push(enr + " edge(s) enriched");
    if (r.cycle_state === true) parts.push("cycle-state persisted");
    if (r.resolutions && r.resolutions.left_for_human)
      parts.push(r.resolutions.left_for_human + " human decision(s) left for you");
    var commit = r.commit || {};
    var cline = commit.committed ? ("committed <code>" + esc(commit.sha) + "</code> to the workspace git")
      : ("not committed — " + esc(commit.reason || "unknown"));
    var skip = (r.skipped || []).length;
    return '<div class="dcyc-banner dcyc-bn-applied"><div class="dcyc-bn-h">✓ Applied to the investigation</div>' +
      '<div class="dcyc-bn-sub">' + parts.join(" · ") + "</div>" +
      '<div class="dcyc-bn-sub">' + cline + (skip ? (" · " + skip + " study/studies skipped (not decisive / unchanged)") : "") + "</div>" +
      '<div class="dcyc-bn-sub"><button class="dcyc-btn dcyc-btn-sm" id="dcyc-apply-dismiss">Dismiss</button></div></div>';
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
