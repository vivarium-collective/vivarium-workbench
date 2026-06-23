// source-switch.js — header dropdown to re-point the dashboard's active
// workspace in-process (SP2). Lists the workspace catalog (/api/workspaces),
// POSTs /api/source/switch, then reloads so the SPA re-renders for the new
// workspace. One server, one URL — no port change.
(function () {
  "use strict";

  function _localOption(ws) {
    const opt = document.createElement("option");
    opt.value = "local:" + ws.path;
    opt.textContent = ws.name || ws.path;
    if (ws.status === "current") opt.selected = true;
    return opt;
  }

  async function _populate(sel) {
    sel.innerHTML = "";
    // Local workspaces (existing catalog).
    try {
      const r = await fetch("/api/workspaces");
      if (r.ok) {
        const data = await r.json();
        const items = (data && data.workspaces) || data || [];
        if (items.length) {
          const g = document.createElement("optgroup");
          g.label = "Local";
          items.forEach(function (ws) { g.appendChild(_localOption(ws)); });
          sel.appendChild(g);
        }
      }
    } catch (e) { /* offline */ }
    // Remote sms-api builds (best-effort).
    try {
      const r = await fetch("/api/source/builds");
      if (r.ok) {
        const data = await r.json();
        const builds = (data && data.builds) || [];
        if (builds.length) {
          const g = document.createElement("optgroup");
          g.label = "Builds";
          builds.forEach(function (b) {
            const opt = document.createElement("option");
            opt.value = "build:" + b.simulator_id;
            opt.textContent = b.label;
            g.appendChild(opt);
          });
          sel.appendChild(g);
        }
      }
    } catch (e) { /* sms-api down — Local only */ }
  }

  async function _switch(path) {
    const r = await fetch("/api/source/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path }),
    });
    if (r.ok) {
      // Until SP2b (composite subprocess-isolation), Composite Explorer may show
      // the previous workspace's composites until the server restarts.
      try { sessionStorage.setItem("viv-source-switched", "1"); } catch (e) {}
      window.location.reload();
    } else {
      const d = await r.json().catch(function () { return {}; });
      alert("Switch failed: " + (d.error || r.status));
    }
  }

  async function _switchBuild(simulatorId, sel) {
    if (sel) { sel.disabled = true; }   // "Loading build…" — first select downloads
    const r = await fetch("/api/source/switch-build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ simulator_id: Number(simulatorId) }),
    });
    if (r.ok) {
      try { sessionStorage.setItem("viv-source-switched", "1"); } catch (e) {}
      window.location.reload();
    } else {
      if (sel) { sel.disabled = false; }
      const d = await r.json().catch(function () { return {}; });
      alert("Switch failed: " + (d.error || r.status));
    }
  }

  function _onChange(sel) {
    const v = sel.value || "";
    if (v.indexOf("build:") === 0) { _switchBuild(v.slice(6), sel); }
    else if (v.indexOf("local:") === 0) { _switch(v.slice(6)); }
  }

  function _mount() {
    const host = document.getElementById("viv-source-switch");
    if (!host) return;
    const sel = document.createElement("select");
    sel.id = "viv-source-switch-select";
    sel.addEventListener("change", function () { _onChange(sel); });
    host.appendChild(sel);
    _populate(sel);
    try {
      if (sessionStorage.getItem("viv-source-switched")) {
        sessionStorage.removeItem("viv-source-switched");
        console.info("Switched workspace. Note: Composite Explorer may need a server restart to refresh.");
      }
    } catch (e) {}
  }

  window._openSourceSwitch = _mount;
  if (document.readyState !== "loading") _mount();
  else document.addEventListener("DOMContentLoaded", _mount);
})();
