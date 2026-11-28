// tab-freshness.js — a quiet, read-only "may be stale" note on the investigation
// Verdict and Narrative top tabs. Freshness is computed by the engine
// (GET /api/investigation/{slug}/freshness) and shown as a single muted line; there
// is no user-facing refresh form or required rationale — re-stamping is the engine's
// job (the divergence is surfaced, not turned into a chore).
// Mounts: <div class="inv-fresh-mount" data-fresh-tab="verdict|narrative">.
(function (global) {
  "use strict";
  var busy = false;
  function base() { return global.__BASE_PATH__ || ""; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function slugNow(m) { return (m && m.getAttribute("data-slug")) || global._currentIsetSlug || global._currentIset || ""; }
  function readonly() { return (global.__DASH_CONFIG__ || {}).mode === "snapshot"; }
  function getFresh(slug) {
    return fetch(base() + "/api/investigation/" + encodeURIComponent(slug) + "/freshness",
      { headers: { "Accept": "application/json" } }).then(function (r) { return r.json(); });
  }
  var SPEC = {
    verdict: {
      msg: function (f) {
        return "authored verdict may predate the latest runs · computed: " + esc(f.computed || "in-progress");
      } },
    narrative: { msg: function () { return "narrative may predate the latest runs"; } },
  };

  function render(mount, f) {
    var tab = mount.getAttribute("data-fresh-tab");
    var sp = SPEC[tab];
    mount.setAttribute("data-done", "1");
    if (!sp || !f || readonly()) { mount.innerHTML = ""; return; }
    if (f.state === "expired") {
      // A quiet, informational note — no banner, no action, no rationale. The
      // divergence is surfaced honestly; re-stamping stays the engine's job.
      mount.innerHTML =
        '<div class="muted tfr-note" style="margin:0 0 10px;font-size:.85em">ℹ ' + sp.msg(f) + '</div>';
    } else if (f.state === "unstamped" && tab === "narrative") {
      mount.innerHTML = '<div class="muted" style="margin:0 0 10px;font-size:.85em">not yet stamped</div>';
    } else {
      mount.innerHTML = "";
    }
  }

  function tick() {
    if (busy) return;
    var mounts = document.querySelectorAll(".inv-fresh-mount:not([data-done])");
    if (!mounts.length) return;
    var slug = slugNow(mounts[0]);
    if (!slug) return;
    busy = true;
    getFresh(slug).then(function (d) {
      busy = false;
      var fr = d && d.freshness;
      Array.prototype.forEach.call(mounts, function (m) {
        if (m.isConnected) render(m, fr ? fr[m.getAttribute("data-fresh-tab")] : null);
      });
    }).catch(function () { busy = false; });
  }
  setInterval(tick, 700);
  if (document.readyState !== "loading") tick();
  else document.addEventListener("DOMContentLoaded", tick);
})(window);
