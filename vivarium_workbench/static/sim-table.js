// sim-table.js — the single Simulations-DB table renderer.
//
// One source of truth for a run row (status chip, emitter/origin pills, location,
// time, ⬇Data/⬇Analysis actions) so the global "Simulations DB" page and the
// per-study Simulations tab render IDENTICAL rows. The study tab drops the
// Investigation + Study columns (redundant when scoped to one study) via
// `opts.scope === 'study'`. walkthrough.js delegates its row/cell helpers here.
(function () {
  "use strict";

  // Close any open row-action "⋯" menu when clicking elsewhere (native
  // <details> otherwise stays open). Wired once at module load.
  if (typeof document !== "undefined" && !document._simActionMenuWired) {
    document._simActionMenuWired = true;
    document.addEventListener("click", function (e) {
      var openMenus = document.querySelectorAll("details.sim-action-menu[open]");
      for (var i = 0; i < openMenus.length; i++) {
        if (!openMenus[i].contains(e.target)) openMenus[i].removeAttribute("open");
      }
    });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function statusChip(status) {
    var colors = {
      completed: ["#dcfce7", "#166534"], running: ["#dbeafe", "#1e40af"],
      failed: ["#fee2e2", "#991b1b"], orphaned: ["#e5e7eb", "#374151"],
    };
    var c = colors[status] || ["#e5e7eb", "#374151"];
    return '<span style="background:' + c[0] + ";color:" + c[1] +
      ';padding:2px 8px;border-radius:10px;font-size:12px;">' + esc(status || "?") + "</span>";
  }

  function emitterPill(t) {
    t = t || "SQLite";
    if (t === "—" || t === "none" || t === "") {
      return '<span class="emitter-pill emitter-none" title="no emitter (summary-only run)">—</span>';
    }
    return '<span class="emitter-pill emitter-' + t.toLowerCase() +
      '" title="emitter / persistence format">' + esc(t) + "</span>";
  }

  function originLabel(row) {
    var o = row && row.remote_origin;
    return o ? String(o.deployment || "remote") : "local";
  }

  function originPill(row) {
    var o = row && row.remote_origin;
    if (!o) return '<span class="origin-pill origin-local" title="local run">local</span>';
    var dep = originLabel(row);
    var tip = "Remote run on " + dep + " (AWS GovCloud)" +
      (o.simulation_id != null ? " — sim " + o.simulation_id : "") +
      (o.experiment_id ? "\nexperiment: " + o.experiment_id : "") +
      (o.s3_uri ? "\nS3: " + o.s3_uri : "");
    return '<span class="origin-pill origin-remote" title="' + esc(tip) + '">' + esc(dep) + "</span>";
  }

  function fmtTime(sec) { return sec ? new Date(sec * 1000).toLocaleString() : "—"; }

  function investigation(row) { return row.investigation_slug || ""; }
  function study(row) {
    return row.study_slug || (row.studies && row.studies.length ? row.studies[0] : "");
  }

  function location(row) {
    var loc = row.store_path || row.db_path || "";
    if (!loc) return '<span style="color:#9ca3af;">—</span>';
    var norm = String(loc).replace(/\\/g, "/");
    var parts = norm.split("/");
    var tail = parts.length > 2 ? "…/" + parts.slice(-2).join("/") : norm;
    // Clickable: reveals the full path (wraps) and copies it to the clipboard —
    // wired in renderTable(). Truncated by default to keep the row compact.
    return '<code class="sim-loc" data-loc="' + esc(loc) + '" role="button" tabindex="0" ' +
      'style="font-size:11px;color:#6b7280;display:block;overflow:hidden;text-overflow:ellipsis;' +
      'white-space:nowrap;cursor:pointer;" title="Click to show the full path &amp; copy">' + esc(tail) + "</code>";
  }

  // Composite cell — enforcement: every simulation must map to exactly one
  // REGISTERED composite. Registered → a link that opens it in the Composite
  // Explorer; missing/unregistered → a red flag (title explains the rule).
  function composite(row) {
    var cid = row && row.spec_id ? String(row.spec_id) : "";
    if (!cid) {
      return '<span title="No composite associated — every simulation must map to one registered composite." ' +
        'style="color:#b91c1c;font-size:12px;white-space:nowrap;">⚠ none</span>';
    }
    var short = cid.split(".").pop();
    if (row.composite_registered) {
      // In-app link (keeps the left nav) that opens THIS run in the Composite
      // Explorer with its saved config pre-filled — wired in renderTable().
      return '<span class="sim-composite-link" data-run-id="' + esc(row.run_id || "") + '" ' +
        'title="' + esc(cid) + ' — open this run in the Composite Explorer (its saved config pre-filled)" ' +
        'style="text-decoration:underline;text-underline-offset:2px;cursor:pointer;white-space:nowrap;color:#2563eb;">' +
        '<code style="font-size:11px;color:inherit;">' + esc(short) + "</code> ↗</span>";
    }
    return '<span title="' + esc(cid) + ' — not a registered composite. Every simulation must map to one registered composite." ' +
      'style="color:#b91c1c;font-size:12px;white-space:nowrap;">⚠ <code style="font-size:11px;color:inherit;">' + esc(short) + "</code></span>";
  }

  // Source cell — the repo + commit the run launched from (source provenance,
  // attached server-side by lib/simulations_index.py as `row.source_ref` from
  // the run manifest's code_version, or the inferred workspace HEAD). Shows
  // `repo@shortsha`; the sha links to the commit on GitHub when resolvable.
  // An inferred/backfilled value (the workspace's current HEAD, not the run's
  // exact commit) is dimmed and prefixed with ~ so it reads as approximate.
  function sourceCell(row) {
    var s = row && row.source_ref;
    if (!s || (!s.repo && !s.commit_short && !s.commit)) {
      return '<span style="color:#9ca3af;">—</span>';
    }
    var inferred = !!s.inferred;
    var color = inferred ? "#9ca3af" : "#374151";
    var short = s.commit_short || (s.commit ? String(s.commit).slice(0, 7) : "");
    var tip = (s.remote_url ? s.remote_url + "\n" : "") +
      (s.commit ? "commit " + s.commit : "") +
      (s.package ? "\npackage " + s.package : "") +
      (inferred ? "\n(inferred from workspace HEAD — approximate, not the run's exact commit)" : "");
    var commitHtml = "";
    if (short) {
      if (s.commit_url) {
        commitHtml = '<a href="' + esc(s.commit_url) + '" target="_blank" rel="noopener" ' +
          'title="Open commit on GitHub" style="color:' + (inferred ? "#9ca3af" : "#2563eb") +
          ';text-decoration:underline;text-underline-offset:2px;font-size:11px;">' + esc(short) + "</a>";
      } else {
        commitHtml = '<code style="font-size:11px;color:' + color + ';">' + esc(short) + "</code>";
      }
    }
    var prefix = inferred
      ? '<span style="color:#9ca3af;" title="approximate — inferred from workspace HEAD">~</span>' : "";
    var repoHtml = s.repo
      ? '<span style="font-size:11px;color:' + color + ';">' + esc(s.repo) + "</span>" : "";
    var sep = (repoHtml && commitHtml) ? '<span style="color:#d1d5db;">@</span>' : "";
    return '<span title="' + esc(tip) + '" style="white-space:nowrap;overflow:hidden;' +
      'text-overflow:ellipsis;display:block;">' + prefix + repoHtml + sep + commitHtml + "</span>";
  }

  // Config cell — the exact generator params that reproduce this run. Shows the
  // first few key=value chips (repro-relevant keys first); full config in the
  // hover title. Empty config → grey em-dash.
  function config(row) {
    var c = row && row.config;
    if (!c || typeof c !== "object" || !Object.keys(c).length) {
      return '<span style="color:#9ca3af;">—</span>';
    }
    var order = ["condition", "media", "seed", "n_steps", "config_overrides"];
    var keys = Object.keys(c).sort(function (a, b) {
      var ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    var parts = keys.map(function (k) {
      var v = c[k];
      if (v && typeof v === "object") {
        v = Object.keys(v).length ? JSON.stringify(v) : "{}";
      }
      return esc(k) + "=" + esc(String(v));
    });
    var shown = parts.slice(0, 4).join(" · ");
    var more = parts.length > 4 ? " +" + (parts.length - 4) : "";
    var full = JSON.stringify(c, null, 2);
    // Clickable: opens a popover with the full config + "Copy JSON" — wired in
    // renderTable() and the #simulations delegated handler.
    return '<code class="sim-config" data-config="' + esc(full) + '" role="button" tabindex="0" ' +
      'style="font-size:11px;color:#6b7280;display:block;overflow:hidden;text-overflow:ellipsis;' +
      'white-space:nowrap;cursor:pointer;" title="Click to show the full config &amp; copy JSON">' +
      shown + esc(more) + "</code>";
  }

  // Compatible-analysis-tools cell — a compact launch chip per entry in
  // `row.matched_tools` (attached server-side by lib/simulations_index.py's
  // `_attach_matched_tools`, capability-matched against the workspace's
  // installed tools/viewers). Empty when nothing matches — no clutter.
  //   - "launcher" tools: the launch_url is the resolve endpoint
  //     (GET /api/analysis-viewer/{uid}/launch) — fetch, then open the
  //     returned {"url": ...} in a new tab (mirrors static/walkthrough.js's
  //     `_launchViewer`).
  //   - everything else (embed-explorer, embed-3d, deep-links): launch_url
  //     is already the concrete page to open — a plain new-tab link.
  function toolsCell(row) {
    var tools = (row && row.matched_tools) || [];
    if (!tools.length) return "";
    var BP = window.__BASE_PATH__ || "";
    return tools.map(function (t) {
      var label = esc(t.label || t.id || "Tool");
      var url = t.launch_url || "";
      if (t.kind === "launcher") {
        return '<button type="button" class="action-btn js-authoring tool-launch-btn" ' +
          'data-launch-url="' + esc(url) + '" title="Launch ' + label + '">' + label + " &#8599;</button>";
      }
      // Direct deep-link (embed-explorer, embed-3d, static viewer page). It's
      // plain markup the base-path shim never sees, so prefix the workspace-root
      // absolute URL with __BASE_PATH__ so it resolves under a hosting prefix.
      var href = /^https?:|^\/\//.test(url) ? url : (BP + url);
      return '<a class="action-btn js-authoring" title="Open ' + label + '" target="_blank" ' +
        'rel="noopener" href="' + esc(href) + '" style="text-decoration:none;">' + label + " &#8599;</a>";
    }).join(" ");
  }

  // Launch a "launcher"-kind tool chip: fetch the resolve endpoint, then open
  // the returned URL. Delegated at the document level (capture phase) so it
  // works for both the global Sim-DB tbody and the per-study renderTable()
  // mount, and so it can stopPropagation before the row's own click-to-open
  // handler fires — same pattern as `_onRerunButtonClick` below.
  function _onToolLaunchClick(e) {
    var btn = e.target.closest(".tool-launch-btn");
    if (!btn) return;
    e.stopPropagation();
    var url = btn.getAttribute("data-launch-url");
    if (!url) return;
    var origLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";
    fetch(url).then(function (r) {
      return r.text().then(function (t) {
        var d = {};
        try { d = t ? JSON.parse(t) : {}; }
        catch (e2) { d = { error: "server returned " + r.status }; }
        return { status: r.status, body: d };
      });
    }).then(function (res) {
      btn.disabled = false;
      btn.textContent = origLabel;
      var b = res.body || {};
      if (res.status === 200 && b.url) window.open(b.url, "_blank", "noopener");
      else {
        var msg = "Launch failed: " + (b.error || res.status);
        if (typeof _showToast === "function") _showToast(msg); else alert(msg);
      }
    }).catch(function (err) {
      btn.disabled = false;
      btn.textContent = origLabel;
      var msg = "Launch failed: " + err;
      if (typeof _showToast === "function") _showToast(msg); else alert(msg);
    });
  }
  document.addEventListener("click", _onToolLaunchClick, true);

  function _actionList(row) {
    var runIdEnc = encodeURIComponent(row.run_id || "");
    var studySlug = study(row);
    // Per-run output retrieval — Visualizations / Report card / Analyses /
    // Results — for ANY completed run, including the ad-hoc composite-test-runs
    // (ecoli_colony, ecoli_baseline) that have no study. Every run writes these
    // under .pbg/runs/<run_id>/ (viz.json / report.html / analyses.json / the
    // emitter store); the artifact endpoint serves them by name.
    var completed = String(row.status || "").toLowerCase() === "completed";
    var hasRun = !!row.run_id;
    // href/download attributes are plain markup, not fetch/XHR/EventSource, so
    // the base-path shim (report.py's _base_path_shim) never sees them — prefix
    // explicitly with window.__BASE_PATH__ (same idiom as branch-source.js).
    var BP = window.__BASE_PATH__ || "";
    function _art(name, label, title, download) {
      return '<a class="action-btn js-authoring" title="' + title + '" ' +
        (download ? 'download ' : 'target="_blank" rel="noopener" ') +
        'href="' + BP + '/api/composite-run/' + runIdEnc + '/artifact/' + name +
        '" style="text-decoration:none;">' + label + '</a>';
    }
    var viz    = (completed && hasRun) ? _art("viz",    "📊 Viz",      "Open this run's visualizations (GIF + plots)", false) : "";
    var report = (completed && hasRun) ? _art("report", "📋 Report",   "Open this run's report card", false) : "";
    var analyses = (completed && hasRun) ? _art("analyses", "⬇ Analyses",   "Download this run's analyses (JSON)", true) : "";
    var data = (row.run_id && (row.store_path || row.db_path))
      ? '<a class="action-btn js-authoring" title="Download this run\'s results / raw emitter data (.zip)" ' +
        'href="' + BP + '/api/simulation-run-download?run_id=' + runIdEnc + '" download style="text-decoration:none;">⬇ Results</a>' : "";
    // "Run analysis" — fires the analysis phase (cd1_*/ptools_*) on an existing,
    // already-completed REMOTE simulation, via POST /api/remote-run-analysis ->
    // viva-api POST /simulations/{id}/analysis. Remote-only and completed-only:
    // a local run's analyses already ran in-process, and there is nothing to
    // analyse before the sweep exists. viva-api auto-runs this as the dispatch
    // DAG's last node, so the button is for RE-running it (a failed analysis, a
    // study whose `analyses` changed since the run, or a simulation dispatched
    // before the auto-trigger existed) — hence the label. No id is interpolated
    // into markup: the delegated handler reads it back from the enclosing
    // <tr data-remote-sim-id>, same idiom as ↻ Rerun below.
    var isSnapshot = (window.__DASH_CONFIG__ || {}).mode === "snapshot";
    var remoteSimId = row && row.remote_origin && row.remote_origin.simulation_id;
    var analysis = (remoteSimId != null && completed && !isSnapshot)
      ? '<button type="button" class="action-btn js-authoring run-analysis-btn" ' +
        'title="Re-run this simulation\'s analysis phase (cd1_*/ptools_*) on the remote deployment">' +
        '🧪 Analysis</button>' : "";
    // "Land" — land-on-demand for a REMOTE run that lives only on the deployment
    // (S3): pull its results here (fold analyses.json + copy ptools/*.tsv into
    // .pbg/runs/<run_id>/) so the ⬇ Analyses artifact resolves and the run shows
    // in the PTools Omics Viewer's run menu. Idempotent; refreshes the table on
    // success. simulation_id + run_id are read from the enclosing <tr> (same
    // idiom as 🧪 Analysis / ↻ Rerun), never interpolated into markup.
    var land = (remoteSimId != null && completed && !isSnapshot)
      ? '<button type="button" class="action-btn js-authoring land-remote-btn" ' +
        'title="Pull this remote run\'s results here (analyses + PTools exports) so Analyses and the PTools viewer work locally">' +
        '⬇ Land</button>' : "";
    // Rerun — REPRODUCES this run (replays its recorded manifest verbatim —
    // params/seed/emitter/emit_paths/runtime exactly as launched, ignoring
    // whatever the study's spec currently says) via POST /api/study-reproduce
    // (reproducible-rerun-spine Task 4; distinct from "Run current spec",
    // which re-derives from the live study.yaml — see study-detail.html's
    // header buttons). Not available against a published read-only snapshot
    // (no live backend to launch against). No run_id is interpolated into
    // markup/attributes here: embedding it in an inline onclick= JS string
    // would need JS escaping, not esc()'s HTML-entity escaping (the browser
    // HTML-decodes the attribute before compiling it as JS, so a literal `'`
    // in run_id would decode back and terminate the string early). Instead
    // the button carries no id at all — the document-level delegated
    // listener below resolves run_id (+ study, for the request body) from
    // the enclosing <tr data-run-id data-study> (already safely HTML-escaped
    // there), and calls stopPropagation itself so the row's own
    // click-to-open handler (the <tr> is clickable) never fires.
    var rerun = (row.run_id && !isSnapshot)
      ? '<button type="button" class="action-btn js-authoring rerun-btn" ' +
        'title="Reproduce this run — replays its recorded manifest exactly, as a brand-new run">↻ Rerun</button>' : "";
    return [viz, report, analyses, data, land, analysis, rerun].filter(function (h) { return !!h; });
  }

  // Legacy inline actions (study-detail Simulations tab): every button in a row.
  function _actions(row) { return _actionList(row).join(" "); }

  // Global Runs page: one primary action (the first available — Viz for a
  // completed run) plus a "⋯" overflow menu holding the rest, so the Actions
  // column stops wrapping into a crowded 7-button block. The menu is a native
  // <details> (no toggle JS); its <summary>/items are .action-btn, which the
  // row's click-to-open handler already ignores.
  function _globalActions(row) {
    var list = _actionList(row);
    if (!list.length) return '<span style="color:#9ca3af;">—</span>';
    var primary = list[0];
    var more = list.slice(1);
    if (!more.length) return primary;
    var items = more.map(function (h) {
      return '<div class="sim-action-menu-item">' + h + '</div>';
    }).join("");
    return primary +
      '<details class="sim-action-menu">' +
        '<summary class="action-btn" title="More actions" aria-label="More actions">⋯</summary>' +
        '<div class="sim-action-menu-list" role="menu">' + items + '</div>' +
      '</details>';
  }

  // Global handler for the ⬇/↻ action buttons rendered above (sim-table.js is
  // an IIFE, so expose on window like the other row helpers). One-click
  // reproduce: POST /api/study-reproduce (reproducible-rerun-spine Task 4 —
  // replays the run's recorded manifest, never the study's current spec),
  // then refresh whichever Simulations table is mounted (global Sim-DB page
  // and/or per-study tab — both expose a refresh hook when present).
  function _rerunSim(runId, btnEl, studySlug) {
    if (!runId) return;
    var origLabel = btnEl ? btnEl.textContent : "";
    if (btnEl) { btnEl.disabled = true; btnEl.textContent = "… rerunning"; }
    fetch("/api/study-reproduce", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId, study: studySlug || "" }),
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; })
        .catch(function () { return { ok: r.ok, status: r.status, body: {} }; });
    }).then(function (res) {
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = origLabel || "↻ Rerun"; }
      var body = res.body || {};
      if (!res.ok) {
        var errMsg = "Rerun failed: " + (body.error || res.status);
        if (typeof _showToast === "function") _showToast(errMsg);
        else alert(errMsg);
        return;
      }
      var okMsg = "Rerun launched" + (body.run_id ? " — new run " + body.run_id : "");
      if (typeof _showToast === "function") _showToast(okMsg);
      else alert(okMsg);
      // Refresh whichever Simulations table(s) are on the current page.
      if (typeof window._initSimulations === "function") window._initSimulations(true);
      if (typeof window._loadStudySims === "function") window._loadStudySims(true);
    }).catch(function (err) {
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = origLabel || "↻ Rerun"; }
      var netMsg = "Rerun failed: network error — " + err;
      if (typeof _showToast === "function") _showToast(netMsg);
      else alert(netMsg);
    });
  }
  window._rerunSim = _rerunSim;

  // Delegated ↻ Rerun click handling — wired ONCE at the document level
  // (not per-mount inside renderTable) because rows rendered by this module
  // reach the DOM through two different paths that don't share a common
  // container: the per-study SimTable.renderTable() mount AND the global
  // Sim-DB page's own tbody (walkthrough.js's _applySimFilter sets
  // tbody.innerHTML from renderRow() output directly, never calling
  // renderTable()). One document-level listener covers both without
  // duplicating wiring — and, critically, avoids double-firing that would
  // happen if a second listener were also added inside renderTable.
  //
  // Capture phase (the trailing `true`) is required, not just convenient:
  // the enclosing <tr> is itself clickable (opens the run) via its OWN
  // bubble-phase listener, so stopping propagation from a bubble-phase
  // document listener would run too late — the <tr> handler bubbles through
  // before an event reaches document. Capturing at document first lets
  // stopPropagation() here pre-empt the <tr> handler entirely.
  //
  // The run_id is read back from the enclosing <tr data-run-id="...">
  // (already safely HTML-escaped when rendered — see renderRow) rather than
  // interpolated into an inline onclick= JS string, which would need JS
  // string-escaping, not esc()'s HTML-entity escaping: the browser
  // HTML-decodes an attribute value before compiling it as JS, so a literal
  // `'` in run_id would decode back to `'` and terminate the string early.
  function _onRerunButtonClick(e) {
    var btn = e.target.closest(".rerun-btn");
    if (!btn) return;
    e.stopPropagation();
    var tr = btn.closest("tr[data-run-id]");
    var runId = tr ? tr.getAttribute("data-run-id") : "";
    if (!runId) return;
    var studySlug = tr ? tr.getAttribute("data-study") : "";
    _rerunSim(runId, btn, studySlug);
  }
  document.addEventListener("click", _onRerunButtonClick, true);

  // Land-on-demand for a remote run: POST /api/remote-run-land-artifacts (fold
  // analyses.json + copy ptools/*.tsv into .pbg/runs/<run_id>/), then refresh so
  // ⬇ Analyses resolves and the run appears in the PTools viewer. Same delegated,
  // capture-phase, read-id-from-<tr> idiom as ↻ Rerun above.
  function _landRemote(runId, simId, btn) {
    if (!runId || simId == null || simId === "") return;
    var orig = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "… landing"; }
    function _reset() { if (btn) { btn.disabled = false; btn.textContent = orig || "⬇ Land"; } }
    fetch("/api/remote-run-land-artifacts", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ simulation_id: Number(simId), run_id: runId }),
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; })
        .catch(function () { return { ok: r.ok, status: r.status, body: {} }; });
    }).then(function (res) {
      _reset();
      var b = res.body || {};
      if (!res.ok) {
        var em = "Land failed: " + (b.error || res.status);
        if (typeof _showToast === "function") _showToast(em); else alert(em);
        return;
      }
      var n = b.ptools || 0;
      var msg = "Landed " + runId + " — " + n + " PTools file" + (n === 1 ? "" : "s")
        + (b.analyses ? " + analyses" : "") + ". Analyses + PTools viewer now available.";
      if (typeof _showToast === "function") _showToast(msg); else alert(msg);
      if (typeof window._initSimulations === "function") window._initSimulations(true);
      if (typeof window._loadStudySims === "function") window._loadStudySims(true);
    }).catch(function (err) {
      _reset();
      var nm = "Land failed: network error — " + err;
      if (typeof _showToast === "function") _showToast(nm); else alert(nm);
    });
  }
  window._landRemote = _landRemote;

  function _onLandButtonClick(e) {
    var btn = e.target.closest(".land-remote-btn");
    if (!btn) return;
    e.stopPropagation();
    var tr = btn.closest("tr[data-run-id]");
    var runId = tr ? tr.getAttribute("data-run-id") : "";
    var simId = tr ? tr.getAttribute("data-remote-sim-id") : "";
    _landRemote(runId, simId, btn);
  }
  document.addEventListener("click", _onLandButtonClick, true);

  // One-click analysis re-run for a completed REMOTE simulation:
  // POST /api/remote-run-analysis -> viva-api POST /simulations/{id}/analysis.
  // Then poll /api/remote-run-poll?analysis_id=… so the operator gets a real
  // terminal signal (the analysis job pulls a multi-GB image before it runs, so
  // "launched" alone is not useful feedback). Polling is a courtesy: giving up
  // never means the analysis failed, and the toast says so.
  var ANALYSIS_POLL_MS = 10000;
  var ANALYSIS_POLL_MAX = 30;  // ~5 min ceiling, matching lib/remote_run_views.py

  function _toast(msg) {
    if (typeof _showToast === "function") _showToast(msg); else alert(msg);
  }

  function _pollAnalysis(analysisId, attempt) {
    if (attempt >= ANALYSIS_POLL_MAX) {
      _toast("Analysis " + analysisId + " still running — check back shortly.");
      return;
    }
    fetch("/api/remote-run-poll?analysis_id=" + encodeURIComponent(analysisId))
      .then(function (r) { return r.json(); })
      .then(function (body) {
        var phase = body && body.phase;
        if (phase === "done") {
          _toast("Analysis " + analysisId + " completed.");
          if (typeof window._initSimulations === "function") window._initSimulations(true);
          if (typeof window._loadStudySims === "function") window._loadStudySims(true);
          return;
        }
        if (phase === "failed") {
          _toast("Analysis " + analysisId + " failed: " + (body.error || "see viva-api logs"));
          return;
        }
        setTimeout(function () { _pollAnalysis(analysisId, attempt + 1); }, ANALYSIS_POLL_MS);
      })
      .catch(function () {
        setTimeout(function () { _pollAnalysis(analysisId, attempt + 1); }, ANALYSIS_POLL_MS);
      });
  }

  function _runSimAnalysis(simulationId, btnEl, studySlug) {
    if (!simulationId) return;
    var origLabel = btnEl ? btnEl.textContent : "";
    if (btnEl) { btnEl.disabled = true; btnEl.textContent = "… analyzing"; }
    fetch("/api/remote-run-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ simulation_id: Number(simulationId), study: studySlug || "" }),
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; })
        .catch(function () { return { ok: r.ok, status: r.status, body: {} }; });
    }).then(function (res) {
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = origLabel || "🧪 Analysis"; }
      var body = res.body || {};
      if (!res.ok) {
        _toast("Analysis failed to start: " + (body.error || res.status));
        return;
      }
      _toast("Analysis launched for simulation " + simulationId +
        (body.analysis_id ? " — analysis " + body.analysis_id : ""));
      if (body.analysis_id) _pollAnalysis(body.analysis_id, 0);
    }).catch(function (err) {
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = origLabel || "🧪 Analysis"; }
      _toast("Analysis failed to start: network error — " + err);
    });
  }
  window._runSimAnalysis = _runSimAnalysis;

  // Same delegation/capture-phase rationale as _onRerunButtonClick above.
  function _onRunAnalysisButtonClick(e) {
    var btn = e.target.closest(".run-analysis-btn");
    if (!btn) return;
    e.stopPropagation();
    var tr = btn.closest("tr[data-remote-sim-id]");
    var simId = tr ? tr.getAttribute("data-remote-sim-id") : "";
    if (!simId) return;
    _runSimAnalysis(simId, btn, tr ? tr.getAttribute("data-study") : "");
  }
  document.addEventListener("click", _onRunAnalysisButtonClick, true);

  // Render one <tr>. opts.scope === 'study' drops Investigation + Study columns.
  // opts.dropIds (set by renderTable() after dropEmptyColumns()) skips the
  // STUDY_COLS cells that were determined dead across the whole table; global
  // Sim-DB callers that build rows via renderRow() directly (not renderTable())
  // never pass it, so their columns are unaffected.
  // Global Runs page row (6-column redesign): Run (name + study·investigation·
  // composite subtext) · Config · Kind (origin+emitter) · Time · Status ·
  // Actions (primary + ⋯ menu). Source moves to the run-name tooltip; Location/
  // Tools fold away. The per-study Simulations tab keeps the legacy wide layout
  // (renderRow's studyScope branch below) since it drops Study/Investigation.
  function _renderGlobalRow(row) {
    var runId = row.run_id || "";
    var runLabel = row.sim_name || row.label || runId;
    var st = study(row), inv = investigation(row);
    var sep = ' <span style="color:#d1d5db;">·</span> ';
    var subBits = [];
    if (st) subBits.push('<span style="color:#4b5563;">' + esc(st) + "</span>");
    if (inv) subBits.push('<span style="color:#9ca3af;">' + esc(inv) + "</span>");
    var comp = composite(row);
    var sub = subBits.join(sep);
    if (comp) sub += (sub ? sep : "") + comp;
    var titleTip = runId + (row.db_path ? "\n" + row.db_path : "");
    var runCell =
      '<div style="min-width:0;">' +
        '<div style="font-size:12px;color:#111827;font-weight:500;overflow:hidden;' +
          'text-overflow:ellipsis;white-space:nowrap;" title="' + esc(titleTip) + '">' +
          esc(runLabel) + "</div>" +
        '<div style="font-size:11px;color:#6b7280;overflow:hidden;text-overflow:ellipsis;' +
          'white-space:nowrap;margin-top:2px;">' + (sub || "") + "</div>" +
      "</div>";
    var kindCell =
      '<div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start;">' +
        originPill(row) + emitterPill(row.emitter_type) + "</div>";
    var td = function (h, extra) {
      return '<td style="padding:8px;' + (extra || "") + '">' + h + "</td>";
    };
    var cells =
      td(runCell, "overflow:hidden;") +
      td(config(row), "overflow:hidden;") +
      td(kindCell) +
      td(esc(fmtTime(row.completed_at || row.started_at)), "color:#6b7280;white-space:nowrap;") +
      td('<span class="run-status-live">' + statusChip(row.status) + "</span>") +
      td('<div class="run-actions">' + _globalActions(row) + "</div>", "vertical-align:middle;");
    var remoteSimId = row.remote_origin && row.remote_origin.simulation_id;
    var remoteAttr = remoteSimId != null ? ' data-remote-sim-id="' + esc(remoteSimId) + '"' : "";
    return '<tr data-run-id="' + esc(runId) + '" data-study="' + esc(study(row)) + '"' + remoteAttr +
      ' style="border-bottom:1px solid #f3f4f6;cursor:pointer;" ' +
      'title="Click to open this run">' + cells + "</tr>";
  }

  function renderRow(row, opts) {
    opts = opts || {};
    var studyScope = opts.scope === "study";
    if (!studyScope) return _renderGlobalRow(row);
    var dropIds = opts.dropIds || null;
    var keep = function (id) { return !dropIds || dropIds.indexOf(id) === -1; };
    var runId = row.run_id || "";
    var runLabel = row.sim_name || row.label || runId;
    var td = function (h, extra) { return '<td style="padding:6px 8px;' + (extra || "") + '">' + h + "</td>"; };
    var cells = "";
    if (!studyScope) {
      var inv = investigation(row), st = study(row);
      cells += td(inv ? '<code style="font-size:12px;color:#374151;">' + esc(inv) + "</code>" : '<span style="color:#9ca3af;">—</span>', "overflow-wrap:anywhere;");
      cells += td(st ? '<code style="font-size:12px;color:#374151;">' + esc(st) + "</code>" : '<span style="color:#9ca3af;">—</span>', "overflow-wrap:anywhere;");
    }
    if (keep("run")) cells += td('<code style="font-size:11px;color:#6b7280;display:block;overflow:hidden;' +
      'text-overflow:ellipsis;white-space:nowrap;" title="' + esc(runId + (row.db_path ? "\n" + row.db_path : "")) +
      '">' + esc(runLabel) + "</code>", "overflow:hidden;");
    if (keep("composite")) cells += td(composite(row), "overflow:hidden;");
    if (keep("source")) cells += td(sourceCell(row), "overflow:hidden;");
    if (keep("config")) cells += td(config(row), "overflow:hidden;max-width:320px;");
    if (keep("location")) cells += td(location(row), "overflow:hidden;");
    if (keep("origin")) cells += td(originPill(row));
    if (keep("emitter")) cells += td(emitterPill(row.emitter_type));
    if (keep("time")) cells += td(esc(fmtTime(row.completed_at || row.started_at)), "color:#6b7280;");
    // .run-status-live: a stable hook so live-status polling (item 84) can
    // replace just this chip in place once a remote row's real phase is
    // known, without knowing this column's position among the others (which
    // varies — dropEmptyColumns can remove neighboring columns per-page).
    if (keep("status")) cells += td('<span class="run-status-live">' + statusChip(row.status) + "</span>");
    if (keep("tools")) cells += td(toolsCell(row), "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;");
    if (keep("actions")) cells += td('<div class="run-actions">' + _actions(row) + '</div>', "vertical-align:middle;");
    // data-remote-sim-id: the remote deployment's simulation database id, present
    // only for remote rows. The 🧪 Analysis handler reads it back from here rather
    // than having it interpolated into an inline onclick= string (same reason as
    // data-run-id — see _actions).
    var remoteSimId = row.remote_origin && row.remote_origin.simulation_id;
    var remoteAttr = remoteSimId != null ? ' data-remote-sim-id="' + esc(remoteSimId) + '"' : "";
    return '<tr data-run-id="' + esc(runId) + '" data-study="' + esc(study(row)) + '"' + remoteAttr + " " +
      'style="border-bottom:1px solid #f3f4f6;cursor:pointer;" ' +
      'title="Click to open this run — its study, or the Composite Explorer">' + cells + "</tr>";
  }

  var STUDY_COLS = [
    { label: "Run", key: "run", id: "run" }, { label: "Composite", key: "composite", id: "composite" },
    // Source (repo@commit) — carries an html() accessor so an all-"—" column
    // (a workspace with no resolvable checkout) is dropped like Location/Emitter.
    { label: "Source", key: "source", id: "source", html: function (row) { return sourceCell(row); } },
    { label: "Config", key: "config", id: "config" },
    // `html` = the same render fn used for the cell. dropEmptyColumns() only
    // considers columns that carry one (see below) — Location/Emitter render
    // a plain "—" when unset, so an all-"—" column is genuinely dead; other
    // columns (e.g. Composite's "⚠ none") render meaningful content even
    // when the underlying value is missing, so they're left unmarked and
    // always kept.
    { label: "Location", key: "location", id: "location", html: function (row) { return location(row); } },
    { label: "Origin", key: "origin", id: "origin" },
    { label: "Emitter", key: "emitter", id: "emitter", html: function (row) { return emitterPill(row.emitter_type); } },
    { label: "Time", key: "time", id: "time" }, { label: "Status", key: "status", id: "status" },
    { label: "Tools", key: "tools", id: "tools" }, { label: "", key: null, id: "actions" },
  ];

  // Generic pre-render pass (Fable A #2 / study-design-fable-pass spec R3):
  // a table column whose rendered content is empty/"—" for EVERY row is
  // dropped rather than shown as a column of dashes. Only columns that carry
  // an `html(row)` accessor participate — columns without one (blank header,
  // interactive controls, or a warning-style empty state) always stay.
  // Shared by this module's Simulations runs table and study-detail.js's
  // Readouts table (window.SimTable.dropEmptyColumns).
  function _cellTextEmpty(html) {
    var text = String(html == null ? "" : html).replace(/<[^>]*>/g, "").trim();
    return text === "" || text === "—" || text === "-";
  }
  function dropEmptyColumns(rows, cols) {
    rows = rows || [];
    if (!rows.length) return cols;
    return cols.filter(function (c) {
      if (typeof c.html !== "function") return true;
      return rows.some(function (r) { return !_cellTextEmpty(c.html(r)); });
    });
  }

  function sortValue(row, key) {
    if (key === "time") return row.completed_at || row.started_at || 0;
    if (key === "composite") return String(row.spec_id || "").toLowerCase();
    if (key === "source") {
      var s = row.source_ref || {};
      return ((s.repo || "") + " " + (s.commit_short || "")).toLowerCase();
    }
    if (key === "emitter") return String(row.emitter_type || "").toLowerCase();
    if (key === "origin") return originLabel(row).toLowerCase();
    if (key === "status") return String(row.status || "").toLowerCase();
    if (key === "location") return String(row.store_path || row.db_path || "").toLowerCase();
    if (key === "run") return String(row.sim_name || row.label || row.run_id || "").toLowerCase();
    if (key === "config") {
      var c = row.config || {};
      return Object.keys(c).length ? JSON.stringify(c).toLowerCase() : "";
    }
    // Tools: matched-tool label, tool-less rows to the end (see walkthrough.js's
    // _simSortValue for the same convention) so clicking Tools groups the
    // tool-linked runs and floats them up on the first (ascending) click.
    if (key === "tools") {
      var mt = row.matched_tools || [];
      return mt.length ? String(mt[0].label || mt[0].id || "").toLowerCase() : "\uffff";
    }
    return "";
  }

  // Render a sortable, clickable <table> of rows into `mount` (study Simulations
  // tab). Clicking a header toggles asc/desc; clicking a row opens the run. State
  // is stashed on the mount so re-sorts don't re-fetch.
  function renderTable(mount, rows, opts) {
    opts = opts || { scope: "study" };
    if (!mount) return;
    if (!rows || !rows.length) {
      mount.innerHTML = '<p class="empty-state muted" style="margin:0">No simulations recorded for this study yet.</p>';
      return;
    }
    mount._simRows = rows;
    var sort = mount._simSort || { key: "time", dir: "desc" };
    mount._simSort = sort;
    var sorted = rows.slice().sort(function (a, b) {
      var av = sortValue(a, sort.key), bv = sortValue(b, sort.key);
      var c = av < bv ? -1 : av > bv ? 1 : 0;
      return sort.dir === "asc" ? c : -c;
    });
    // R3 — no dead columns: drop any STUDY_COLS entry whose cell is empty
    // across every row in this table (see dropEmptyColumns above).
    var cols = dropEmptyColumns(rows, STUDY_COLS);
    var dropIds = cols.length === STUDY_COLS.length ? null : STUDY_COLS
      .filter(function (c) { return cols.indexOf(c) === -1; })
      .map(function (c) { return c.id; });
    var rowOpts = dropIds ? { scope: opts.scope, onRowClick: opts.onRowClick, dropIds: dropIds } : opts;
    var head = "<thead><tr>" + cols.map(function (c) {
      var arrow = (c.key && c.key === sort.key) ? (sort.dir === "asc" ? " ▲" : " ▼") : "";
      var cursor = c.key ? "cursor:pointer;" : "";
      return '<th data-sort-key="' + (c.key || "") + '" style="text-align:left;padding:6px 8px;' +
        "border-bottom:2px solid #e5e7eb;font-size:12px;color:#6b7280;user-select:none;" + cursor +
        '">' + esc(c.label) + arrow + "</th>";
    }).join("") + "</tr></thead>";
    mount.innerHTML = '<table style="width:100%;border-collapse:collapse;">' + head +
      "<tbody>" + sorted.map(function (r) { return renderRow(r, rowOpts); }).join("") + "</tbody></table>";
    mount.querySelectorAll("th[data-sort-key]").forEach(function (th) {
      var key = th.getAttribute("data-sort-key");
      if (!key) return;
      th.addEventListener("click", function () {
        mount._simSort = { key: key, dir: (sort.key === key && sort.dir === "desc") ? "asc" : "desc" };
        renderTable(mount, mount._simRows, opts);
      });
    });
    mount.querySelectorAll("tr[data-run-id]").forEach(function (tr) {
      tr.addEventListener("click", function (e) {
        if (e.target.closest("a")) return;  // let ⬇ links work
        var id = tr.getAttribute("data-run-id");
        var row = rows.find(function (r) { return (r.run_id || "") === id; });
        if (!row) return;
        // Custom handler (study tab → per-run detail panel) wins; else the global
        // Sim-DB behavior (navigate to the run's study / Composite Explorer).
        if (typeof opts.onRowClick === "function") opts.onRowClick(row, tr);
        else if (window._openSimulation) window._openSimulation(row);
      });
    });
    // Composite links always open the run in the Composite Explorer (in-app,
    // nav preserved) with its saved config seeded — regardless of study assoc.
    mount.querySelectorAll(".sim-composite-link").forEach(function (link) {
      link.addEventListener("click", function (e) {
        e.stopPropagation();  // don't fall through to the row's default handler
        var rid = link.getAttribute("data-run-id");
        var row = rows.find(function (r) { return (r.run_id || "") === rid; });
        if (row && window._openCompositeFromRun) window._openCompositeFromRun(row);
      });
    });
    // Config cell: click to open a popover with the full config + Copy JSON.
    mount.querySelectorAll(".sim-config").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.stopPropagation();
        if (window._showConfigPopover) window._showConfigPopover(el);
      });
    });
    // Location cell: click to reveal the full path (wrap) and copy it.
    mount.querySelectorAll(".sim-loc").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.stopPropagation();  // don't trigger the row's open handler
        var full = el.getAttribute("data-loc") || "";
        if (!full) return;
        el.textContent = full;               // reveal the full path in place
        el.style.whiteSpace = "normal";
        el.style.wordBreak = "break-all";
        el.style.overflow = "visible";
        el.style.textOverflow = "clip";
        el.title = full;
        var done = function (ok) {
          var badge = document.createElement("span");
          badge.textContent = ok ? "  ✓ copied" : "  (copy failed)";
          badge.style.cssText = "color:" + (ok ? "#16a34a" : "#b91c1c") + ";font-size:10px;white-space:nowrap";
          el.appendChild(badge);
          setTimeout(function () { if (badge.parentNode) badge.parentNode.removeChild(badge); }, 1800);
        };
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(full).then(function () { done(true); }, function () { done(false); });
          } else {
            var ta = document.createElement("textarea");
            ta.value = full; document.body.appendChild(ta); ta.select();
            var ok = false; try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
            document.body.removeChild(ta); done(ok);
          }
        } catch (e3) { done(false); }
      });
    });
    // Drag-resizable columns, widths persisted per table namespace. Re-applied
    // on every renderTable() (the table is rebuilt each sort) — ColResize reads
    // the stored widths back, so a user's sizing survives sorts and reloads.
    if (window.ColResize) {
      var _tbl = mount.querySelector("table");
      if (_tbl) window.ColResize.apply(_tbl, "sim-" + (opts.scope || "study"));
    }
  }

  window.SimTable = {
    esc: esc, statusChip: statusChip, emitterPill: emitterPill, originPill: originPill,
    originLabel: originLabel, fmtTime: fmtTime, location: location, study: study,
    investigation: investigation, composite: composite, sourceCell: sourceCell, toolsCell: toolsCell,
    renderRow: renderRow, renderTable: renderTable, dropEmptyColumns: dropEmptyColumns,
  };
})();
