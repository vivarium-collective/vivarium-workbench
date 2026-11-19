// tab-freshness.js — derived "expired — refresh" affordance on the investigation
// Verdict and Narrative top tabs. Freshness is computed by the engine
// (GET /api/investigation/{slug}/freshness); the only writes are the rationale-gated
// POST accept-verdict / refresh-narrative. Mirrors the viz Accept form in
// discovery-cycle.js. Decisions has no affordance (it is mechanically reconciled).
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
  function post(slug, verb, rationale) {
    return fetch(base() + "/api/investigation/" + encodeURIComponent(slug) + "/" + verb, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ rationale: rationale }),
    }).then(function (r) { return r.json().catch(function () { return {}; }); });
  }

  var SPEC = {
    verdict: { verb: "accept-verdict", label: "Refresh ▸",
      msg: function (f) {
        return "⚠ expired — authored verdict diverges from the computed " + esc(f.computed || "verdict");
      } },
    narrative: { verb: "refresh-narrative", label: "Refresh ▸",
      msg: function () { return "⚠ expired — narrative basis changed since it was written"; } },
  };

  function render(mount, f) {
    var tab = mount.getAttribute("data-fresh-tab");
    var sp = SPEC[tab];
    mount.setAttribute("data-done", "1");
    if (!sp || !f || readonly()) { mount.innerHTML = ""; return; }
    if (f.state === "expired") {
      mount.innerHTML =
        '<div class="tfr-bar" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px;' +
        'padding:6px 10px;border:1px solid #d97706;border-radius:6px;background:rgba(217,119,6,.08);font-size:.9em">' +
        '<span class="tfr-msg">' + sp.msg(f) + '</span>' +
        '<button type="button" class="tfr-open" title="Refresh (rationale required)">' + sp.label + '</button></div>' +
        '<div class="tfr-form" style="display:none;margin:0 0 10px">' +
        '<textarea class="tfr-why" rows="2" style="width:100%" placeholder="Rationale (required) — what you re-read and why this is now correct"></textarea>' +
        '<div style="margin-top:4px;display:flex;gap:8px;align-items:center">' +
        '<button type="button" class="tfr-go">Confirm refresh</button>' +
        '<button type="button" class="tfr-cancel">Cancel</button>' +
        '<span class="tfr-status muted"></span></div></div>';
      var form = mount.querySelector(".tfr-form");
      var status = mount.querySelector(".tfr-status");
      mount.querySelector(".tfr-open").onclick = function () {
        form.style.display = ""; var t = mount.querySelector(".tfr-why"); if (t) t.focus();
      };
      mount.querySelector(".tfr-cancel").onclick = function () { form.style.display = "none"; };
      mount.querySelector(".tfr-go").onclick = function () {
        var why = mount.querySelector(".tfr-why");
        var rationale = why ? why.value.trim() : "";
        if (!rationale) { status.textContent = "Enter a rationale."; if (why) why.focus(); return; }
        status.textContent = "Refreshing…";
        post(slugNow(mount), sp.verb, rationale).then(function (r) {
          if (!r || r.ok !== true) { status.textContent = "Failed: " + ((r && (r.detail || r.error)) || "error"); return; }
          try { if (typeof global._vivRefreshDiscoveryViews === "function") global._vivRefreshDiscoveryViews(slugNow(mount)); } catch (e) {}
          mount.removeAttribute("data-done");   // re-read FRESH on the next tick
          mount.innerHTML = '<div class="muted" style="margin:0 0 10px">Refreshed.</div>';
        }).catch(function (e) { status.textContent = "Failed: " + e; });
      };
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
