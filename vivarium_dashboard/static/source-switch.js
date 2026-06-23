// source-switch.js — header dropdown to re-point the dashboard's active
// workspace in-process (SP2). Lists the workspace catalog (/api/workspaces),
// POSTs /api/source/switch, then reloads so the SPA re-renders for the new
// workspace. One server, one URL — no port change.
(function () {
  "use strict";

  async function _populate(sel) {
    try {
      const r = await fetch("/api/workspaces");
      if (!r.ok) return;
      const data = await r.json();
      const items = (data && data.workspaces) || data || [];
      sel.innerHTML = "";
      items.forEach(function (ws) {
        const opt = document.createElement("option");
        opt.value = ws.path;
        opt.textContent = ws.name || ws.path;
        if (ws.status === "current") opt.selected = true;
        sel.appendChild(opt);
      });
    } catch (e) { /* offline / static mode — leave empty */ }
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

  function _mount() {
    const host = document.getElementById("viv-source-switch");
    if (!host) return;
    const sel = document.createElement("select");
    sel.id = "viv-source-switch-select";
    sel.addEventListener("change", function () { _switch(sel.value); });
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
