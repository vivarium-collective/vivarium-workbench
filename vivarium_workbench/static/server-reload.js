// server-reload.js — notice when the workbench server has been restarted and
// offer a reload, so a long-lived browser tab self-heals instead of silently
// breaking.
//
// Why: the SPA is a single long-lived page. When the server process is replaced
// (a restart — e.g. to pick up merged work), a tab that loaded against the old
// process keeps its cached views but its click handlers start firing requests
// the new process never saw a session for; panels like the Code view and the
// loom </> buttons then quietly stop working. There is no code bug — the tab is
// just stale. This watcher polls the per-process boot id from
// GET /api/server-version and, when it changes, shows a persistent toast that
// reloads the page on click. It never auto-reloads (that could drop unsaved
// edits); the person chooses when.
//
// Live mode only (a published snapshot has no server to watch). Fully
// defensive: any fetch error just schedules the next check — during the restart
// gap the server is briefly unreachable, then returns with a new boot id.
(function () {
  "use strict";

  var cfg = (window.__DASH_CONFIG__ || {});
  if (cfg.mode === "snapshot") return;            // nothing to watch offline

  var BASE = window.__BASE_PATH__ || cfg.basePath || "";
  var URL = BASE + "/api/server-version";
  var POLL_MS = 20000;                            // 20s steady-state cadence
  var baseline = null;                            // boot id of the server we loaded against
  var notified = false;                           // show the prompt at most once
  var timer = null;

  function fetchBootId() {
    return fetch(URL, { cache: "no-store", credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { return j && j.boot_id ? String(j.boot_id) : null; })
      .catch(function () { return null; });       // unreachable (likely mid-restart)
  }

  function prompt() {
    if (notified) return;
    notified = true;
    var msg = "Workbench server restarted — click to reload this tab.";
    // Reuse the app's toast when present (danger + durationMs:0 => stays until
    // clicked); fall back to a minimal fixed banner otherwise.
    if (typeof window._showToast === "function") {
      try {
        window._showToast(msg, { danger: true, durationMs: 0,
                                 onClick: function () { location.reload(); } });
        // _showToast dismisses on click regardless; also bind a reload so the
        // click both dismisses and reloads even on older toast builds.
        var host = document.querySelector(".ui-toast-host .ui-toast-danger");
        if (host) host.addEventListener("click", function () { location.reload(); });
        return;
      } catch (e) { /* fall through to the banner */ }
    }
    var bar = document.createElement("div");
    bar.textContent = msg;
    bar.setAttribute("role", "alert");
    bar.style.cssText = [
      "position:fixed", "left:50%", "bottom:18px", "transform:translateX(-50%)",
      "z-index:2147483647", "background:#b42318", "color:#fff",
      "padding:10px 16px", "border-radius:8px", "font:600 13px system-ui,sans-serif",
      "box-shadow:0 4px 16px rgba(0,0,0,.25)", "cursor:pointer",
    ].join(";");
    bar.addEventListener("click", function () { location.reload(); });
    document.body.appendChild(bar);
  }

  function check() {
    return fetchBootId().then(function (id) {
      if (id == null) return;                     // unreachable — try again next tick
      if (baseline == null) { baseline = id; return; }
      if (id !== baseline) prompt();
    });
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    if (notified) return;
    timer = setTimeout(function () { check().then(schedule); }, POLL_MS);
  }

  // Check immediately when the tab regains focus (the common case: the person
  // comes back to the tab after a restart) — then resume the steady cadence.
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && !notified) check().then(schedule);
  });

  function start() { check().then(schedule); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
