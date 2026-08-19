// workspace-picker.js — the sleek, self-describing workspace switcher.
//
// A always-visible "Workspace: <name> ▾" control in the rail header opens a
// searchable dropdown of workspaces. Picking one honors session-per-tab
// (pinned-for-life): it SPAWNS a new browser tab bound to that workspace via
// window.open('/?workspace=<catalog-name>') — session.js's ?workspace= bootstrap
// force-mints a fresh per-tab session and binds it — rather than re-pointing this
// tab. The current tab's workspace shows a "current" badge and is inert.
//
// Reads GET /api/workspaces (name, label, status, path). Best-effort: if that
// fails (snapshot / offline) the trigger stays but the menu shows an empty note.
// Status → dot: current/running ●green · stopped ○grey · stale ⚠amber · missing ⊘.
//
// Testable: filterWorkspaces() + statusMeta() are exported for Node tests; the
// DOM wiring auto-runs only in the browser.
(function () {
  "use strict";

  var STATUS = {
    current: { cls: "ready", dot: "●", label: "current" },
    running: { cls: "ready", dot: "●", label: "running" },
    stopped: { cls: "stopped", dot: "○", label: "stopped" },
    stale: { cls: "stale", dot: "⚠", label: "stale" },
    missing: { cls: "missing", dot: "⊘", label: "missing" },
    remote: { cls: "remote", dot: "☁", label: "remote build" },
  };

  function statusMeta(status) {
    return STATUS[status] || STATUS.stopped;
  }

  // Case-insensitive filter over name/label; empty query returns all, current first.
  function filterWorkspaces(items, query) {
    var q = String(query || "").trim().toLowerCase();
    var list = (items || []).filter(function (w) {
      if (!q) return true;
      var hay = ((w.label || "") + " " + (w.name || "")).toLowerCase();
      return hay.indexOf(q) !== -1;
    });
    return list.slice().sort(function (a, b) {
      var ac = a.status === "current" ? 0 : 1, bc = b.status === "current" ? 0 : 1;
      if (ac !== bc) return ac - bc;
      return String(a.label || a.name || "").localeCompare(String(b.label || b.name || ""));
    });
  }

  // Reduce sms-api's flat build history (GET /api/source/builds) to one row
  // per distinct repo — its MOST RECENT build — normalized into the same
  // {name, label, status, path} shape a local workspace row has, so it can
  // share render()/openWs()/pin/recent-use machinery. Older builds of the
  // same repo (different branches/commits) stay reachable via the row's
  // "Branch settings" link into the full Source panel; this list's job is
  // "get me into a repo quickly," not replicate that panel's full picker.
  function normalizeRemoteBuilds(builds) {
    var latest = {};
    (builds || []).forEach(function (b) {
      if (!b || !b.repo) return;
      var prev = latest[b.repo];
      if (!prev || String(b.created_at || "") > String(prev.created_at || "")) {
        latest[b.repo] = b;
      }
    });
    return Object.keys(latest).sort().map(function (repo) {
      var b = latest[repo];
      return {
        name: null, path: "remote:" + repo, kind: "remote",
        status: "remote", simulator_id: b.simulator_id,
        label: b.repo + " @ " + String(b.commit || "").slice(0, 7)
          + " (build #" + b.simulator_id + ") [" + (b.branch || "") + "]",
      };
    });
  }

  // ── Pins + last-used (per-browser, localStorage) ────────────────────────────
  // Pinned workspaces float to the top; the rest sort by most-recently-opened.
  // Both are keyed by workspace PATH (stable across relabels).
  function _wsPins() {
    try { return JSON.parse(localStorage.getItem('viv.pinnedWorkspaces') || '[]') || []; }
    catch (e) { return []; }
  }
  function _wsPinned(path) { return _wsPins().indexOf(path) !== -1; }
  function _wsTogglePin(path) {
    var a = _wsPins(), i = a.indexOf(path);
    if (i >= 0) a.splice(i, 1); else a.push(path);
    try { localStorage.setItem('viv.pinnedWorkspaces', JSON.stringify(a)); } catch (e) { /* private mode */ }
    return i < 0;
  }
  function _wsLastUsed() {
    try { return JSON.parse(localStorage.getItem('viv.wsLastUsed') || '{}') || {}; }
    catch (e) { return {}; }
  }
  function _wsRecordUsed(path) {
    if (!path) return;
    var m = _wsLastUsed(); m[path] = Date.now();
    try { localStorage.setItem('viv.wsLastUsed', JSON.stringify(m)); } catch (e) { /* private mode */ }
  }
  // Order for the dropdown: current → pinned → most-recently-used → label.
  function _sortWorkspaces(list) {
    var pins = _wsPins(), lu = _wsLastUsed();
    return list.slice().sort(function (a, b) {
      var ac = a.status === 'current' ? 0 : 1, bc = b.status === 'current' ? 0 : 1;
      if (ac !== bc) return ac - bc;
      var ap = pins.indexOf(a.path) >= 0 ? 0 : 1, bp = pins.indexOf(b.path) >= 0 ? 0 : 1;
      if (ap !== bp) return ap - bp;
      var al = lu[a.path] || 0, bl = lu[b.path] || 0;
      if (al !== bl) return bl - al;   // most-recent first
      return String(a.label || a.name || '').localeCompare(String(b.label || b.name || ''));
    });
  }

  var api = { filterWorkspaces: filterWorkspaces, statusMeta: statusMeta,
              normalizeRemoteBuilds: normalizeRemoteBuilds };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  if (typeof window !== "undefined") window.vivWorkspacePicker = api;

  // ── browser DOM wiring ──────────────────────────────────────────────────────
  function boot() {
    var trigger = document.getElementById("viv-workspace-picker-trigger");
    if (!trigger) return;

    // Read-only snapshot: there are no other workspaces to switch to, so the
    // dropdown is pointless (and cramped when the rail is collapsed). The trigger
    // instead opens the Source page directly, and its status dot goes grey (not a
    // running local workspace).
    var isSnap = (document.body && document.body.classList.contains("snapshot"))
      || (window.__DASH_CONFIG__ && window.__DASH_CONFIG__.mode === "snapshot");
    function goSource() {
      try { window.location.hash = "#github"; } catch (e) { /* ignore */ }
      if (typeof window._switchPage === "function") window._switchPage("github");
    }
    if (isSnap) {
      var d = trigger.querySelector(".viv-wsp-dot");
      if (d) { d.classList.remove("viv-wsp-ready", "viv-wsp-remote"); d.classList.add("viv-wsp-stopped"); }
    }

    var menu = null, searchEl = null, listEl = null, all = [], activeIdx = -1;

    function close() {
      if (!menu) return;
      menu.parentNode && menu.parentNode.removeChild(menu);
      menu = null; activeIdx = -1;
      trigger.setAttribute("aria-expanded", "false");
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onOutside, true);
    }

    // Open a workspace in a NEW tab, or SWITCH the current one.
    //  - New tab: prefer the workspace's own running-server URL; else spawn via
    //    the ?workspace= session bootstrap (session.js binds it).
    //  - Switch this tab: RE-POINT the local server to the workspace's path
    //    (/api/source/switch) then reload, so the whole workbench — name AND
    //    content — reflects it. (A ?workspace= session-switch left the
    //    server-rendered header name stale.)
    function openWs(ws, newTab) {
      close();
      _wsRecordUsed(ws && ws.path);   // stamp before we navigate/switch away
      if (ws && ws.kind === "remote") {
        // Same URL shape branch-source.js's Open button already uses —
        // session.js's ?build= bootstrap materializes it and binds this new
        // tab's session, honoring the base path behind the shared ALB.
        var bp = window.__BASE_PATH__ || "";
        window.open(bp + "/?build=" + encodeURIComponent(ws.simulator_id), "_blank");
        return;
      }
      if (newTab) {
        var url = ws && ws.url ? ws.url
          : (ws && ws.name ? "/?workspace=" + encodeURIComponent(ws.name) : null);
        if (url) window.open(url, "_blank");
        else window.alert("No running server for \"" + ((ws && (ws.label || ws.name)) || "this workspace") +
          "\" to open in a new tab. Start it from that repo, then it'll appear here.");
        return;
      }
      if (ws && ws.path) {
        fetch("/api/source/switch", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: ws.path }) })
          .then(function (r) {
            if (r.ok) {
              // Item 70 phase 3: flag the branded splash for the reload's
              // first paint (index.html.j2's inline body script reads +
              // clears this) — covers the boot gap a plain reload otherwise
              // leaves blank while the SPA's own scripts load.
              try { sessionStorage.setItem("viv-switch-splash", "1"); } catch (e) { /* private mode */ }
              location.reload();
            } else window.alert("Switch failed.");
          })
          .catch(function () { window.alert("Switch failed (network)."); });
      } else if (ws && ws.name) {
        window.location.assign("/?workspace=" + encodeURIComponent(ws.name));
      }
    }

    function rows() { return listEl ? listEl.querySelectorAll(".viv-wsp-row") : []; }
    function setActive(i) {
      var rs = rows(); if (!rs.length) { activeIdx = -1; return; }
      activeIdx = (i + rs.length) % rs.length;
      for (var k = 0; k < rs.length; k++) rs[k].classList.toggle("active", k === activeIdx);
      rs[activeIdx].scrollIntoView({ block: "nearest" });
    }

    function render() {
      if (!listEl) return;
      listEl.innerHTML = "";
      var list = _sortWorkspaces(filterWorkspaces(all, searchEl ? searchEl.value : ""));
      if (!list.length) {
        var e = document.createElement("li");
        e.className = "viv-wsp-empty";
        e.textContent = all.length ? "No workspaces match" : "No workspaces available";
        listEl.appendChild(e);
        return;
      }
      list.forEach(function (ws) {
        var isCur = ws.status === "current";
        var m = statusMeta(ws.status);
        var li = document.createElement("li");
        li.className = "viv-wsp-row" + (isCur ? " is-current" : "");
        li.setAttribute("role", "option");
        li.tabIndex = -1;

        var dot = document.createElement("span");
        dot.className = "viv-wsp-dot viv-wsp-" + m.cls;
        dot.textContent = m.dot;
        dot.title = m.label;
        li.appendChild(dot);

        var name = document.createElement("span");
        name.className = "viv-wsp-name";
        name.textContent = ws.label || ws.name || ws.path || "(unnamed)";
        li.appendChild(name);

        var isRemote = ws.kind === "remote";
        if (isCur) {
          var tail = document.createElement("span");
          tail.className = "viv-wsp-tail";
          tail.textContent = "current";
          li.appendChild(tail);
        } else {
          // Local workspaces get two actions (Switch this tab / Open in a new
          // tab); remote sms-api builds are session-per-tab only (branch-source.js
          // established this: window.open('/?build=<id>') spawns a fresh
          // per-tab session — there is no in-place "switch this tab" for a
          // remote build), so they get Open ↗ only.
          var acts = document.createElement("span");
          acts.className = "viv-wsp-actions";
          if (!isRemote) {
            var switchBtn = document.createElement("button");
            switchBtn.type = "button"; switchBtn.className = "viv-wsp-act";
            switchBtn.textContent = "Switch";
            switchBtn.title = "Switch this tab to " + (ws.label || ws.name || "this workspace");
            switchBtn.addEventListener("click", function (e) { e.stopPropagation(); openWs(ws, false); });
            acts.appendChild(switchBtn);
          }
          if (ws.name || isRemote) {   // a new tab needs a bindable ?workspace=/?build=
            var openBtn = document.createElement("button");
            openBtn.type = "button"; openBtn.className = "viv-wsp-act viv-wsp-act-open";
            openBtn.textContent = "Open ↗";
            openBtn.title = "Open " + (ws.label || ws.name) + " in a new tab";
            openBtn.addEventListener("click", function (e) { e.stopPropagation(); openWs(ws, true); });
            acts.appendChild(openBtn);
          }
          li.appendChild(acts);
          // Clicking the row defaults to Open (remote) or Switch (local).
          li.addEventListener("click", function () { openWs(ws, isRemote); });
          li.addEventListener("mouseenter", function () {
            var rs = rows(); for (var k = 0; k < rs.length; k++) if (rs[k] === li) setActive(k);
          });
        }
        // Pin toggle (far right) — pinned workspaces float to the top.
        var pinned = _wsPinned(ws.path);
        var pin = document.createElement("button");
        pin.type = "button";
        pin.className = "viv-wsp-pin" + (pinned ? " pinned" : "");
        pin.textContent = "📌";
        pin.title = pinned ? "Unpin" : "Pin to top";
        pin.setAttribute("aria-pressed", pinned ? "true" : "false");
        pin.addEventListener("click", function (e) {
          e.stopPropagation(); _wsTogglePin(ws.path); render();
        });
        li.appendChild(pin);
        listEl.appendChild(li);
      });
      activeIdx = -1;
    }

    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); close(); trigger.focus(); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(activeIdx + 1); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setActive(activeIdx - 1); return; }
      if (e.key === "Enter") {
        var rs = rows();
        if (activeIdx >= 0 && rs[activeIdx]) { e.preventDefault(); rs[activeIdx].click(); }
      }
    }
    function onOutside(e) { if (menu && !menu.contains(e.target) && !trigger.contains(e.target)) close(); }

    function open() {
      if (menu) { close(); return; }
      menu = document.createElement("div");
      menu.className = "viv-wsp-menu";
      menu.setAttribute("role", "listbox");

      var head = document.createElement("div");
      head.className = "viv-wsp-head";
      searchEl = document.createElement("input");
      searchEl.type = "text";
      searchEl.className = "viv-wsp-search";
      searchEl.placeholder = "Search workspaces…";
      searchEl.setAttribute("aria-label", "Search workspaces");
      searchEl.addEventListener("input", render);
      head.appendChild(searchEl);
      menu.appendChild(head);

      listEl = document.createElement("ul");
      listEl.className = "viv-wsp-list";
      menu.appendChild(listEl);

      var foot = document.createElement("div");
      foot.className = "viv-wsp-foot";
      foot.innerHTML = '<span class="viv-wsp-legend"><b class="viv-wsp-ready">●</b> ready ' +
        '<b class="viv-wsp-stopped">○</b> stopped <b class="viv-wsp-stale">⚠</b> stale</span>' +
        '<a href="#github" class="viv-wsp-settings" title="Repo / branch / GitHub settings">Branch settings ↗</a>';
      menu.appendChild(foot);

      // Mount on <body> and position at the trigger (fixed) so the rail's
      // overflow-x:hidden can't clip/squish it — the menu can be wider than the
      // sidebar.
      document.body.appendChild(menu);
      var r = trigger.getBoundingClientRect();
      menu.style.top = Math.round(r.bottom + 4) + "px";
      menu.style.left = Math.round(r.left) + "px";
      trigger.setAttribute("aria-expanded", "true");
      document.addEventListener("keydown", onKey, true);
      document.addEventListener("mousedown", onOutside, true);

      var loading = document.createElement("li");
      loading.className = "viv-wsp-empty";
      loading.textContent = "Loading…";
      listEl.appendChild(loading);
      searchEl.focus();

      // Local catalog workspaces + remote sms-api builds, fetched in parallel
      // and merged into one list — previously remote builds were reachable
      // only through the separate Source panel, which a user switching
      // workspaces had no reason to know existed.
      var wsFetch = fetch("/api/workspaces").then(function (r) { return r && r.ok ? r.json() : null; })
        .catch(function () { return null; });
      var buildsFetch = fetch("/api/source/builds").then(function (r) { return r && r.ok ? r.json() : null; })
        .catch(function () { return null; });
      Promise.all([wsFetch, buildsFetch]).then(function (results) {
        var wsData = results[0], buildsData = results[1];
        var local = (wsData && wsData.workspaces) || wsData || [];
        var remote = normalizeRemoteBuilds(buildsData && buildsData.builds);
        all = local.concat(remote);
        var cur = local.filter(function (w) { return w.status === "current"; })[0];
        if (cur) _wsRecordUsed(cur.path);   // seed "last used" for the active one
        render();
      });
    }

    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      if (isSnap) { goSource(); return; }   // read-only → Source page (no switcher)
      open();
    });
    // The trigger is a role="button" div (so the Source <button> can nest inside
    // it); wire keyboard activation like a real button.
    trigger.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); trigger.click(); }
    });
    trigger.setAttribute("aria-haspopup", isSnap ? "false" : "listbox");
    trigger.setAttribute("aria-expanded", "false");
    if (isSnap) trigger.title = "Open Source — repository, branch, commit";
    // The dedicated Source button (next to the name) always jumps to the Source
    // page, in both live and read-only. Wired here so it works regardless of the
    // trigger's mode.
    var srcBtn = document.getElementById("viv-wsp-source-btn");
    if (srcBtn) srcBtn.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); goSource(); });
  }

  if (document.readyState !== "loading") boot();
  else document.addEventListener("DOMContentLoaded", boot);
})();
