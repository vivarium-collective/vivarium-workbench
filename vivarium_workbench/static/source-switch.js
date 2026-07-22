// source-switch.js — the workspace/source switcher.
//
// Triggered by the left-rail workspace-name chip (#viv-source-switch-trigger).
// Click it to open a dropdown listing Local workspaces (/api/workspaces) and
// remote sms-api Builds (/api/source/builds).
//
// Session-per-tab (pinned-for-life): picking a LOCAL workspace opens it in a NEW
// TAB — window.open('/?workspace=<catalog-name>'), which session.js's ?workspace=
// bootstrap force-mints a fresh per-tab session for and binds — rather than
// re-pointing THIS tab in place. A tab keeps its workspace for life; to view
// another, open another tab. (sms-api Builds still switch in place for now — the
// hosted spawn is a later slice.) A name-less catalog entry falls back to the
// in-place path switch. Styled with the shared .viv-iset-menu classes.
(function () {
  "use strict";

  var menu = null;
  var outsideHandler = null;
  var escHandler = null;

  function _trigger() { return document.getElementById("viv-source-switch-trigger"); }

  // Pinned-for-life: open the workspace in a NEW tab bound to it by name. Falls
  // back to an in-place switch by path when the catalog entry has no name (can't
  // spawn by name). session.js handles the fresh-session bootstrap in the new tab.
  function _openWorkspaceTab(ws) {
    _close();
    var name = ws && ws.name;
    if (name) {
      window.open("/?workspace=" + encodeURIComponent(name), "_blank");
    } else {
      _switch(ws.path);
    }
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

  async function _switchBuild(simulatorId, titleEl) {
    if (titleEl) { titleEl.textContent = "Loading build…"; }
    const r = await fetch("/api/source/switch-build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ simulator_id: Number(simulatorId) }),
    });
    if (r.ok) {
      try { sessionStorage.setItem("viv-source-switched", "1"); } catch (e) {}
      window.location.reload();
    } else {
      const d = await r.json().catch(function () { return {}; });
      _close();
      alert("Switch failed: " + (d.error || r.status));
    }
  }

  async function _forget(path, li) {
    const r = await fetch("/api/workspaces/forget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path }),
    });
    if (r.ok) {
      li.remove();
    } else {
      const d = await r.json().catch(function () { return {}; });
      // 409 = the workspace is running; stop it before forgetting.
      alert("Couldn't forget: " + (d.error || r.status));
    }
  }

  function _row(label, onClick, opts) {
    opts = opts || {};
    const li = document.createElement("li");
    li.className = "viv-iset-menu-row" + (opts.current ? " viv-iset-menu-row-current" : "");
    li.setAttribute("role", "menuitem");
    li.tabIndex = 0;
    const line = document.createElement("div");
    line.className = "viv-iset-menu-row-line1";
    const title = document.createElement("span");
    title.className = "viv-iset-menu-row-title";
    title.textContent = label;
    line.appendChild(title);
    if (opts.current) {
      const tag = document.createElement("span");
      tag.className = "viv-iset-menu-row-current-tag";
      tag.textContent = "current";
      line.appendChild(tag);
    }
    if (opts.openInTab) {
      const arr = document.createElement("span");
      arr.className = "viv-iset-menu-row-newtab";
      arr.textContent = "↗";
      arr.title = "Opens in a new tab";
      arr.setAttribute("aria-label", "opens in a new tab");
      line.appendChild(arr);
    }
    if (opts.forgetPath) {
      const x = document.createElement("button");
      x.type = "button";
      x.className = "viv-iset-menu-forget";
      x.textContent = "✕";
      x.title = "Forget this workspace (remove from the list)";
      x.setAttribute("aria-label", "Forget workspace");
      x.addEventListener("click", function (e) {
        e.stopPropagation();
        _forget(opts.forgetPath, li);
      });
      line.appendChild(x);
    }
    li.appendChild(line);
    if (!opts.current && onClick) {
      li.addEventListener("click", onClick);
      li.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); }
      });
    }
    return li;
  }

  function _section(text) {
    const li = document.createElement("li");
    li.className = "viv-iset-menu-section";
    li.textContent = text;
    return li;
  }

  async function _populate(listEl) {
    listEl.innerHTML = "";
    let hasAny = false;
    // Local workspaces (the catalog).
    try {
      const r = await fetch("/api/workspaces");
      if (r.ok) {
        const data = await r.json();
        const items = (data && data.workspaces) || data || [];
        if (items.length) {
          hasAny = true;
          listEl.appendChild(_section("Local workspaces"));
          items.forEach(function (ws) {
            const cur = ws.status === "current";
            const label = ws.label || ws.name || ws.path;
            listEl.appendChild(_row(label, function () { _openWorkspaceTab(ws); }, {
              current: cur,
              forgetPath: cur ? null : ws.path,   // can't forget the active one
              openInTab: !cur && !!ws.name,       // shows the ↗ "opens a new tab" hint
            }));
          });
        }
      }
    } catch (e) { /* offline / static mode */ }
    // Remote sms-api builds (best-effort; degrades to a note when unreachable).
    try {
      const r = await fetch("/api/source/builds");
      if (r.ok) {
        const data = await r.json();
        const builds = (data && data.builds) || [];
        if (builds.length) {
          hasAny = true;
          listEl.appendChild(_section("Builds — sms-api"));
          builds.forEach(function (b) {
            const row = _row(b.label, null);
            row.addEventListener("click", function () {
              _switchBuild(b.simulator_id, row.querySelector(".viv-iset-menu-row-title"));
            });
            listEl.appendChild(row);
          });
        } else if (data && data.error) {
          listEl.appendChild(_section("Builds — sms-api"));
          const note = document.createElement("li");
          note.className = "viv-iset-menu-empty";
          note.textContent = "No builds — sms-api unreachable (is the tunnel up?)";
          listEl.appendChild(note);
        }
      }
    } catch (e) { /* sms-api down — Local only */ }
    if (!hasAny) {
      const empty = document.createElement("li");
      empty.className = "viv-iset-menu-empty";
      empty.textContent = "No sources available";
      listEl.appendChild(empty);
    }
  }

  function _ensureMenu() {
    if (menu) return menu;
    menu = document.createElement("div");
    menu.className = "viv-iset-menu viv-source-menu";
    menu.setAttribute("role", "menu");
    menu.innerHTML =
      '<div class="viv-iset-menu-header">Switch source</div>' +
      '<ul class="viv-iset-menu-list"><li class="viv-iset-menu-loading">Loading…</li></ul>';
    const container = document.getElementById("viv-workspace-switcher") || document.body;
    container.appendChild(menu);
    return menu;
  }

  function _close() {
    if (menu) menu.classList.remove("open");
    if (outsideHandler) { document.removeEventListener("mousedown", outsideHandler); outsideHandler = null; }
    if (escHandler) { document.removeEventListener("keydown", escHandler); escHandler = null; }
  }

  function _open() {
    const m = _ensureMenu();
    _populate(m.querySelector(".viv-iset-menu-list"));
    m.classList.add("open");
    const trig = _trigger();
    outsideHandler = function (e) {
      if (!m.contains(e.target) && (!trig || !trig.contains(e.target))) _close();
    };
    escHandler = function (e) { if (e.key === "Escape") _close(); };
    // Defer so the opening click doesn't immediately close it.
    setTimeout(function () {
      document.addEventListener("mousedown", outsideHandler);
      document.addEventListener("keydown", escHandler);
    }, 0);
  }

  function _toggle() {
    const m = _ensureMenu();
    if (m.classList.contains("open")) _close();
    else _open();
  }

  function _mount() {
    const trig = _trigger();
    if (!trig) return;
    trig.addEventListener("click", function (e) {
      // The GitHub mark stays a direct link — don't open the menu for it.
      if (e.target.closest(".viv-ws-repo-link")) return;
      e.preventDefault();
      _toggle();
    });
    trig.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); _toggle(); }
    });
    try {
      if (sessionStorage.getItem("viv-source-switched")) {
        sessionStorage.removeItem("viv-source-switched");
        console.info("Switched source. Composite Explorer may need a server restart to refresh (until SP2b).");
      }
    } catch (e) {}
  }

  window._openSourceSwitch = _toggle;
  if (document.readyState !== "loading") _mount();
  else document.addEventListener("DOMContentLoaded", _mount);
})();
