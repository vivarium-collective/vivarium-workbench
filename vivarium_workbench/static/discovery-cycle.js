/* discovery-cycle.js — the "Cycle" rail panel.
 *
 * A live, clickable map of the discovery engine's cycle. Pick an investigation,
 * run one analytical cycle (or advance continuously in a sandbox), watch each
 * step light up, click a node to inspect what ran and its result, and re-run any
 * single step. Pure vanilla JS, self-contained (injects its own CSS), exposed as
 * window.DiscoveryCycle. Backed by:
 *   GET /api/investigations                      (the picker)
 *   GET /api/discovery-cycle-steps               (static graph before a run)
 *   GET /api/discovery-cycle/{slug}              (run one full cycle → trace)
 *   GET /api/discovery-cycle/{slug}/step/{id}    (re-run one step)
 *   GET /api/discovery-cycle/{slug}/advance      (sandboxed continuous advance)
 *
 * The X-VW-Session header is injected transparently by session.js's fetch patch,
 * so every call below is routed to this tab's workspace.
 */
(function () {
  "use strict";

  var S = { inited: false, slug: "", steps: [], byId: {}, selected: null, busy: false };

  function base() { return window.__BASE_PATH__ || ""; }

  function getJSON(path) {
    return fetch(base() + path, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); });
  }

  function el(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // ---- CSS (injected once) -------------------------------------------------
  function injectStyle() {
    if (el("dcyc-style")) return;
    var css = [
      ".dcyc-root{display:flex;flex-direction:column;height:100%;min-height:0;gap:12px;padding:14px 16px}",
      ".dcyc-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap}",
      ".dcyc-pick{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--viv-fg-muted,#555)}",
      ".dcyc-pick select{font-size:13px;padding:4px 8px;border-radius:7px;border:1px solid var(--viv-border,#ccd);background:var(--viv-bg,#fff);color:inherit;max-width:340px}",
      ".dcyc-btn{font:inherit;font-size:13px;padding:6px 12px;border-radius:8px;border:1px solid var(--viv-border,#ccd);background:var(--viv-bg,#fff);color:inherit;cursor:pointer}",
      ".dcyc-btn:disabled{opacity:.45;cursor:default}",
      ".dcyc-btn-primary{background:#2563eb;border-color:#2563eb;color:#fff}",
      ".dcyc-btn:not(:disabled):hover{filter:brightness(.97)}",
      ".dcyc-status{font-size:12.5px;color:var(--viv-fg-muted,#667)}",
      ".dcyc-body{display:flex;gap:14px;flex:1;min-height:0}",
      ".dcyc-graph{flex:1 1 60%;overflow:auto;padding:4px 2px}",
      ".dcyc-inspect{flex:1 1 40%;max-width:46%;overflow:auto;border-left:1px solid var(--viv-border,#e3e3ea);padding-left:14px}",
      ".dcyc-phase{margin-bottom:6px}",
      ".dcyc-phase-h{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--viv-fg-muted,#8891a0);margin:10px 0 4px}",
      ".dcyc-row{display:flex;flex-wrap:wrap;gap:8px}",
      ".dcyc-node{position:relative;min-width:180px;max-width:280px;text-align:left;padding:9px 11px;border-radius:10px;border:1px solid var(--viv-border,#d7d9e0);background:var(--viv-bg,#fff);cursor:pointer;transition:box-shadow .12s,border-color .12s;font:inherit}",
      ".dcyc-node:hover{box-shadow:0 1px 6px rgba(0,0,0,.09)}",
      ".dcyc-node.sel{border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.25)}",
      ".dcyc-node .t{font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px}",
      ".dcyc-node .fn{font-size:11px;color:var(--viv-fg-muted,#8891a0);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin-top:2px}",
      ".dcyc-node .sum{font-size:12px;color:var(--viv-fg,#334);margin-top:5px}",
      ".dcyc-node .dur{position:absolute;top:8px;right:10px;font-size:10.5px;color:var(--viv-fg-muted,#99a)}",
      ".dcyc-dot{width:9px;height:9px;border-radius:50%;flex:none;background:#c9cdd6}",
      ".st-ok .dcyc-dot{background:#16a34a}.st-empty .dcyc-dot{background:#d1a019}",
      ".st-error .dcyc-dot{background:#dc2626}.st-idle .dcyc-dot{background:#94a3b8}",
      ".st-running .dcyc-dot{background:#2563eb;animation:dcycpulse 1s infinite}",
      ".st-mut{border-style:dashed}",
      "@keyframes dcycpulse{0%,100%{opacity:1}50%{opacity:.3}}",
      ".dcyc-ins-h{font-size:15px;font-weight:650;margin:2px 0 2px}",
      ".dcyc-ins-meta{font-size:12px;color:var(--viv-fg-muted,#667);margin-bottom:8px}",
      ".dcyc-ins-meta code{font-family:ui-monospace,Menlo,monospace}",
      ".dcyc-pill{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:999px;border:1px solid var(--viv-border,#dde);margin-right:5px}",
      ".dcyc-pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;line-height:1.45;white-space:pre-wrap;word-break:break-word;background:var(--viv-bg-alt,#f6f7f9);border:1px solid var(--viv-border,#e3e3ea);border-radius:8px;padding:10px;max-height:52vh;overflow:auto}",
      ".dcyc-note{font-size:12px;color:#b4690e;margin:6px 0}",
      ".empty-state{color:var(--viv-fg-muted,#99a);font-size:13px;padding:14px}"
    ].join("\n");
    var st = document.createElement("style");
    st.id = "dcyc-style";
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ---- data ---------------------------------------------------------------
  function loadInvestigations() {
    var sel = el("dcyc-inv");
    return getJSON("/api/investigations").then(function (j) {
      var rows = (j && (j.investigations || j)) || [];
      if (!Array.isArray(rows)) rows = [];
      if (!rows.length) {
        sel.innerHTML = '<option value="">(no investigations in this workspace)</option>';
        return;
      }
      sel.innerHTML = '<option value="">Choose an investigation…</option>' +
        rows.map(function (r) {
          var slug = r.slug || r.name || r.id || "";
          var label = r.title || r.name || slug;
          return '<option value="' + esc(slug) + '">' + esc(label) + "</option>";
        }).join("");
      // preselect the investigation the rest of the SPA is already on
      var cur = window._currentIsetSlug;
      if (cur && rows.some(function (r) { return (r.slug || r.name) === cur; })) {
        sel.value = cur;
        onPick(cur);
      }
    }).catch(function () {
      sel.innerHTML = '<option value="">(failed to load investigations)</option>';
    });
  }

  function onPick(slug) {
    S.slug = slug || "";
    S.steps = []; S.byId = {}; S.selected = null;
    el("dcyc-run").disabled = !S.slug;
    el("dcyc-run-cont").disabled = !S.slug;
    setStatus("");
    el("dcyc-inspect").innerHTML = '<p class="empty-state">Click a step to see what ran and its result.</p>';
    if (!S.slug) {
      el("dcyc-graph").innerHTML = '<p class="empty-state">Pick an investigation to load the cycle.</p>';
      return;
    }
    // draw the empty graph from the static step metadata
    getJSON("/api/discovery-cycle-steps").then(function (j) {
      if (!j || j.available === false) {
        el("dcyc-graph").innerHTML = '<p class="empty-state">The discovery engine is not available in this workspace.</p>';
        el("dcyc-run").disabled = true; el("dcyc-run-cont").disabled = true;
        return;
      }
      setTrace({ steps: (j.steps || []).map(function (s) {
        return Object.assign({}, s, { status: "pending", summary: "not run yet", duration_ms: null });
      }) }, true);
    });
  }

  function setStatus(msg) { el("dcyc-status").textContent = msg || ""; }

  // ---- render -------------------------------------------------------------
  function setTrace(trace, isEmpty) {
    S.steps = (trace && trace.steps) || [];
    S.byId = {};
    S.steps.forEach(function (s) { S.byId[s.id] = s; });
    renderGraph();
    if (!isEmpty && S.selected && S.byId[S.selected]) inspectStep(S.selected);
  }

  function renderGraph() {
    var g = el("dcyc-graph");
    if (!S.steps.length) { g.innerHTML = '<p class="empty-state">No steps.</p>'; return; }
    var phases = [];
    S.steps.forEach(function (s) { if (phases.indexOf(s.phase) < 0) phases.push(s.phase); });
    g.innerHTML = phases.map(function (ph) {
      var nodes = S.steps.filter(function (s) { return s.phase === ph; });
      return '<div class="dcyc-phase"><div class="dcyc-phase-h">' + esc(ph) + "</div>" +
        '<div class="dcyc-row">' + nodes.map(nodeHtml).join("") + "</div></div>";
    }).join("");
    Array.prototype.forEach.call(g.querySelectorAll(".dcyc-node"), function (n) {
      n.addEventListener("click", function () { inspectStep(n.getAttribute("data-id")); });
    });
  }

  function nodeHtml(s) {
    var st = s.status || "pending";
    var cls = "dcyc-node st-" + st + (s.kind === "mutating" ? " st-mut" : "") +
      (S.selected === s.id ? " sel" : "");
    var dur = (s.duration_ms != null && st !== "pending" && st !== "idle")
      ? '<span class="dur">' + s.duration_ms + " ms</span>" : "";
    return '<button class="' + cls + '" data-id="' + esc(s.id) + '" type="button">' +
      dur +
      '<div class="t"><span class="dcyc-dot"></span>' + esc(s.label) + "</div>" +
      '<div class="fn">' + esc(s.module) + "." + esc(s.func) + "</div>" +
      '<div class="sum">' + esc(s.summary || "") + "</div>" +
      "</button>";
  }

  function jsonBlock(obj) {
    var txt;
    try { txt = JSON.stringify(obj, null, 2); } catch (e) { txt = String(obj); }
    if (txt == null) txt = "null";
    var CAP = 24000;
    var truncated = txt.length > CAP;
    if (truncated) txt = txt.slice(0, CAP) + "\n… (" + (txt.length - CAP) + " more chars)";
    return '<pre class="dcyc-pre">' + esc(txt) + "</pre>" +
      (truncated ? '<p class="dcyc-note">Result truncated for display.</p>' : "");
  }

  function inspectStep(id) {
    S.selected = id;
    renderGraph(); // repaint selection
    var s = S.byId[id];
    var box = el("dcyc-inspect");
    if (!s) { box.innerHTML = '<p class="empty-state">Unknown step.</p>'; return; }
    var deps = (s.depends_on || []).length ? (s.depends_on || []).join(", ") : "none";
    var meta = '<div class="dcyc-ins-meta">' +
      '<span class="dcyc-pill">' + esc(s.phase) + "</span>" +
      '<span class="dcyc-pill">' + esc(s.kind) + "</span>" +
      '<span class="dcyc-pill">' + esc(s.status) + "</span>" +
      (s.duration_ms != null ? '<span class="dcyc-pill">' + s.duration_ms + " ms</span>" : "") +
      "<br><code>" + esc(s.module) + "." + esc(s.func) + "</code> · depends on: " + esc(deps) +
      "</div>";
    var head = '<div class="dcyc-ins-h">' + esc(s.label) + "</div>" + meta;

    if (s.kind === "mutating") {
      box.innerHTML = head +
        '<p class="dcyc-note">This is the execute/advance phase — it runs real study ' +
        "runs and writes results. It only runs via <b>Run continuously</b>, against a " +
        "sandboxed copy, so your real investigation is never touched.</p>" +
        '<button class="dcyc-btn dcyc-btn-primary" id="dcyc-ins-cont">Run continuously (sandbox)</button>';
      var b = el("dcyc-ins-cont");
      if (b) b.addEventListener("click", runContinuous);
      return;
    }

    var rerun = (s.status === "pending")
      ? ""
      : '<button class="dcyc-btn" id="dcyc-ins-rerun">Re-run this step</button>';
    var result = ("result" in s)
      ? jsonBlock(s.result)
      : (s.status === "pending"
        ? '<p class="empty-state">Run the cycle to populate this step.</p>'
        : '<p class="empty-state">No result captured.</p>');
    box.innerHTML = head + rerun + result;
    var rb = el("dcyc-ins-rerun");
    if (rb) rb.addEventListener("click", function () { rerunStep(id); });
  }

  // ---- actions ------------------------------------------------------------
  function runCycle() {
    if (!S.slug || S.busy) return;
    S.busy = true;
    el("dcyc-run").disabled = true; el("dcyc-run-cont").disabled = true;
    // paint everything as running
    S.steps.forEach(function (s) { if (s.kind !== "mutating") s.status = "running"; });
    renderGraph();
    setStatus("Running the cycle…");
    var t0 = Date.now();
    getJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug)).then(function (j) {
      if (!j || j.available === false) {
        setStatus("Discovery engine unavailable.");
        return;
      }
      setTrace(j);
      var errs = (j.steps || []).filter(function (s) { return s.status === "error"; }).length;
      setStatus("Cycle complete in " + ((Date.now() - t0) / 1000).toFixed(1) + "s" +
        (errs ? " · " + errs + " step(s) errored" : ""));
    }).catch(function () {
      setStatus("Cycle request failed.");
    }).finally(function () {
      S.busy = false;
      el("dcyc-run").disabled = !S.slug; el("dcyc-run-cont").disabled = !S.slug;
    });
  }

  function rerunStep(id) {
    if (!S.slug || S.busy) return;
    S.busy = true;
    var s = S.byId[id];
    if (s) { s.status = "running"; renderGraph(); }
    setStatus("Re-running " + id + "…");
    getJSON("/api/discovery-cycle/" + encodeURIComponent(S.slug) + "/step/" + encodeURIComponent(id))
      .then(function (j) {
        if (j && j.step) {
          S.byId[id] = j.step;
          for (var i = 0; i < S.steps.length; i++) if (S.steps[i].id === id) S.steps[i] = j.step;
          renderGraph();
          inspectStep(id);
          setStatus("Re-ran " + id + ".");
        } else {
          setStatus((j && j.error) || "Re-run failed.");
        }
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
        if (j.after) setTrace(j.after);
        renderAdvance(j);
        setStatus("Sandbox advance done · " + ((j.runs || []).length) + " run(s)" +
          (j.advance_error ? " · see note" : ""));
      }).catch(function () { setStatus("Advance request failed."); })
      .finally(function () {
        S.busy = false;
        el("dcyc-run").disabled = !S.slug; el("dcyc-run-cont").disabled = !S.slug;
      });
  }

  function renderAdvance(j) {
    var box = el("dcyc-inspect");
    var fb = j.frontier_before, fa = j.frontier_after;
    var html = '<div class="dcyc-ins-h">Continuous advance (sandboxed)</div>' +
      '<div class="dcyc-ins-meta"><span class="dcyc-pill">max ' + esc(j.max_runs) +
      '</span><span class="dcyc-pill">' + ((j.runs || []).length) + " run(s)</span>" +
      (fb != null || fa != null
        ? '<span class="dcyc-pill">frontier ' + esc(fb) + " → " + esc(fa) + "</span>" : "") +
      "</div>";
    if (j.advance_error) {
      html += '<p class="dcyc-note">Advance reported: ' + esc(j.advance_error) +
        " — this environment may lack the simulation runner; the plan above is what it " +
        "would execute. No real files were changed.</p>";
    }
    if ((j.needs_build || []).length) {
      html += '<p class="dcyc-note">' + (j.needs_build.length) +
        " study/studies need a model build before they can run.</p>";
    }
    html += jsonBlock({ runs: j.runs, needs_build: j.needs_build,
      closed_before: j.closed_before, closed_after: j.closed_after });
    box.innerHTML = html;
  }

  // ---- init ---------------------------------------------------------------
  function init() {
    injectStyle();
    if (S.inited) { return; }
    S.inited = true;
    var sel = el("dcyc-inv");
    if (sel) sel.addEventListener("change", function () { onPick(sel.value); });
    var r = el("dcyc-run"); if (r) r.addEventListener("click", runCycle);
    var rc = el("dcyc-run-cont"); if (rc) rc.addEventListener("click", runContinuous);
    loadInvestigations();
  }

  window.DiscoveryCycle = { init: init };
})();
