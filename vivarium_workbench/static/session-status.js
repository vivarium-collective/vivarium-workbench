// session-status.js — favicon + title by materialization status (session-per-tab
// slice 3c).
//
// A tab is one workspace (pinned for life). This encodes that workspace's env
// state in the browser TAB itself — the favicon + title — so you can tell a
// preparing / failed tab from a ready one WITHOUT switching to it. Status comes
// from GET /api/source/materialization (materialization-lifecycle §4):
//
//   ready         → the workbench mark, plain title
//   materializing → hourglass favicon + "⏳ <title>", an in-page ProgressTrack
//                   stages panel (item 70 ph.2 — real queued/cloning/syncing
//                   phases from the poll's `phase` field, never fabricated),
//                   and POLL until it settles
//   failed        → red favicon + "⚠ <title>"
//
// A plain local workspace is `ready` at once (no poll). Managed/hosted sources
// clone + uv sync for minutes, so the hourglass is their progress surface. In a
// published snapshot (no live endpoint) the fetch just fails → ready mark, no poll.
(function () {
  "use strict";

  var POLL_MS = 2500;

  function svgDataUri(inner) {
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
      inner + '</svg>';
    return 'data:image/svg+xml,' + encodeURIComponent(svg);
  }

  // Consistent rounded-square marks so the three states read as one family.
  var FAVICONS = {
    ready: svgDataUri(
      '<rect width="32" height="32" rx="7" fill="#157a70"/>' +
      '<text x="16" y="23" font-family="system-ui,sans-serif" font-size="20"' +
      ' font-weight="700" fill="#fff" text-anchor="middle">V</text>'),
    preparing: svgDataUri(
      '<rect width="32" height="32" rx="7" fill="#f4e6c8"/>' +
      '<text x="16" y="25" font-size="20" text-anchor="middle">⏳</text>'),
    failed: svgDataUri(
      '<rect width="32" height="32" rx="7" fill="#c14a34"/>' +
      '<text x="16" y="24" font-family="system-ui,sans-serif" font-size="23"' +
      ' font-weight="800" fill="#fff" text-anchor="middle">!</text>'),
  };

  function setFavicon(uri) {
    var link = document.querySelector('link[rel="icon"]');
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      (document.head || document.documentElement).appendChild(link);
    }
    link.type = "image/svg+xml";
    link.href = uri;
  }

  // Capture the server-rendered title once (stripped of any status glyph) so we
  // can prefix/restore without compounding.
  var baseTitle = null;
  function setTitlePrefix(prefix) {
    if (baseTitle === null) {
      baseTitle = String(document.title || "").replace(/^[⏳⚠️!]+\s*/, "");
    }
    document.title = prefix ? prefix + " " + baseTitle : baseTitle;
  }

  // Map a materialization status → the tab treatment. Returns the normalized
  // state ('ready' | 'preparing' | 'failed').
  function apply(status) {
    if (status === "materializing" || status === "preparing") {
      setFavicon(FAVICONS.preparing);
      setTitlePrefix("⏳");
      return "preparing";
    }
    if (status === "failed") {
      setFavicon(FAVICONS.failed);
      setTitlePrefix("⚠️");
      return "failed";
    }
    setFavicon(FAVICONS.ready);
    setTitlePrefix("");
    return "ready";
  }

  // ── Failed-state panel (slice 3c: "nothing silently disappears") ─────────────
  // When materialization fails, the favicon alone can't say WHY. Render a
  // dismissible panel with the reason + the uv/git tail the backend already
  // returns (session_env._map_job → {error, tail}), and a Retry that re-triggers
  // the SAME managed materialization via the existing POST /api/source/materialize-
  // repo (no new endpoint). A non-managed (in-place) failure has no repo/ref, so
  // Retry falls back to reloading the tab (which re-runs the bind).
  var PANEL_ID = "viv-session-failure";

  function clearFailure() {
    var p = document.getElementById(PANEL_ID);
    if (p && p.parentNode) p.parentNode.removeChild(p);
  }

  // ── Materialization progress panel (item 70 phase 2) ───────────────────────
  // Wires ProgressTrack's `stages` mode to the REAL job phases already carried
  // by GET /api/source/materialization's `phase` field (session_env._map_job
  // passes the async job's queued/cloning/syncing phase straight through — no
  // new backend surface needed). Driven only by real poll responses: no client
  // timer, no interpolated/invented stage. Managed-source case only; a plain
  // local switch is `ready` at once and this panel never appears for it.
  var PROGRESS_ID = "viv-session-progress";
  var MATERIALIZE_STAGES = [
    { key: "queued", label: "Queued" },
    { key: "cloning", label: "Cloning" },
    { key: "syncing", label: "Installing" },
  ];
  var STAGE_ORDER = ["queued", "cloning", "syncing"];

  // Pure: real phase string → {done, active} milestone arrays. An unrecognized
  // or missing phase conservatively reports zero done stages with the first
  // stage active — never invented partial credit.
  function stageProgress(phase) {
    var idx = STAGE_ORDER.indexOf(phase);
    if (idx < 0) idx = 0;
    return { done: STAGE_ORDER.slice(0, idx), active: STAGE_ORDER[idx] };
  }

  function clearProgress() {
    var p = document.getElementById(PROGRESS_ID);
    if (p && p.parentNode) p.parentNode.removeChild(p);
  }

  function _progressMount() {
    var m = document.getElementById(PROGRESS_ID);
    if (m) return m;
    m = document.createElement("div");
    m.id = PROGRESS_ID;
    m.style.cssText = "position:fixed; right:16px; top:16px; z-index:2147483000;" +
      " width:min(92vw,300px)";
    (document.body || document.documentElement).appendChild(m);
    return m;
  }

  // `.ptrack`'s own card (background/border, light+dark themed) is the only
  // visual chrome — deliberately no second wrapper "card" here, just position.
  function renderProgress(d) {
    if (!d || d.status !== "materializing") { clearProgress(); return; }
    if (typeof window === "undefined" || !window.ProgressTrack) return;
    var sp = stageProgress(d.phase);
    window.ProgressTrack.render(_progressMount(), {
      mode: "stages", stages: MATERIALIZE_STAGES,
      done: sp.done, active: sp.active,
      note: "<strong>Preparing workspace…</strong>",
    });
  }

  function retry(d) {
    clearFailure();
    if (d && d.repo && d.ref) {
      apply("materializing");
      renderProgress({ status: "materializing", phase: "queued" });
      fetch("/api/source/materialize-repo", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo: d.repo, ref: d.ref }),
      }).then(function () { setTimeout(poll, POLL_MS); })
        .catch(function () { apply("failed"); clearProgress(); });
    } else if (typeof location !== "undefined" && location.reload) {
      location.reload();
    }
  }

  function renderFailure(d) {
    clearFailure();
    d = d || {};
    var panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.setAttribute("role", "alert");
    panel.style.cssText = "position:fixed; right:16px; bottom:16px; z-index:2147483000;" +
      " max-width:min(92vw,440px); background:#1b1420; color:#f3e7ea;" +
      " border:1px solid #5a2530; border-left:3px solid #ff5d6c; border-radius:10px;" +
      " padding:13px 15px; font:13px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;" +
      " box-shadow:0 10px 34px rgba(0,0,0,.5)";

    var h = document.createElement("div");
    h.style.cssText = "font-weight:700; color:#ff8a95; margin-bottom:5px";
    h.textContent = "Workspace failed to prepare";
    panel.appendChild(h);

    var msg = document.createElement("div");
    msg.className = "viv-sf-error";
    msg.textContent = d.error || "materialization failed (no reason reported)";
    panel.appendChild(msg);

    if (d.tail) {
      var pre = document.createElement("pre");
      pre.className = "viv-sf-tail";
      pre.style.cssText = "margin:8px 0 0; max-height:160px; overflow:auto; white-space:pre-wrap;" +
        " background:#0f0a12; border:1px solid #3a2530; border-radius:6px; padding:8px;" +
        " font:11.5px/1.45 ui-monospace,Menlo,monospace; color:#c9b8bd";
      pre.textContent = d.tail;
      panel.appendChild(pre);
    }

    var actions = document.createElement("div");
    actions.style.cssText = "margin-top:10px; display:flex; gap:8px";
    var retryBtn = document.createElement("button");
    retryBtn.className = "viv-sf-retry";
    retryBtn.textContent = "Retry";
    retryBtn.style.cssText = "cursor:pointer; border:1px solid #5a2530; background:#2a1319;" +
      " color:#ff8a95; border-radius:6px; padding:5px 12px; font-weight:600";
    retryBtn.addEventListener("click", function () { retry(d); });
    actions.appendChild(retryBtn);
    var dismissBtn = document.createElement("button");
    dismissBtn.className = "viv-sf-dismiss";
    dismissBtn.textContent = "Dismiss";
    dismissBtn.style.cssText = "cursor:pointer; border:1px solid #3a2f3a; background:transparent;" +
      " color:#93a1b5; border-radius:6px; padding:5px 12px";
    dismissBtn.addEventListener("click", clearFailure);
    actions.appendChild(dismissBtn);
    panel.appendChild(actions);

    (document.body || document.documentElement).appendChild(panel);
    return panel;
  }

  function poll() {
    fetch("/api/source/materialization")
      .then(function (r) { return r && r.ok ? r.json() : null; })
      .then(function (d) {
        var state = apply((d && d.status) || "ready");
        renderProgress(d || {});
        if (state === "failed") renderFailure(d || {});
        else clearFailure();
        if (state === "preparing") setTimeout(poll, POLL_MS);
      })
      .catch(function () { apply("ready"); clearProgress(); });
  }

  var api = { apply: apply, poll: poll, renderFailure: renderFailure,
              clearFailure: clearFailure, svgDataUri: svgDataUri, FAVICONS: FAVICONS,
              renderProgress: renderProgress, clearProgress: clearProgress,
              stageProgress: stageProgress };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;                 // Node (tests): no auto-run
  } else {
    window.vivSessionStatus = api;        // browser: run on load
    if (document.readyState !== "loading") poll();
    else document.addEventListener("DOMContentLoaded", poll);
  }
})();
