// branch-source.js — the Branch-tab Source panel. Organizes the dashboard's
// source as local/remote · repo · branch · commit, and re-points the active
// source in-process. Replaces the rail dropdown (source-switch.js).
(function () {
  "use strict";

  var state = { scope: "local", repo: null, branch: null, entries: [], current: null };
  var pollTimer = null;

  // Snapshot (published static bundle): no live backend. The Source panel
  // becomes a navigator across the SIBLING published workspaces listed in a
  // static manifest (default ../workspaces.json), and "Switch" navigates to the
  // chosen bundle instead of an in-process re-point.
  var SNAP = (window.__DASH_CONFIG__ || {}).mode === "snapshot";
  function _manifestUrl() {
    var cfg = window.__DASH_CONFIG__ || {};
    return cfg.workspacesManifest || "../workspaces.json";
  }
  function _currentRepoName() {
    var bp = (window.__DASH_CONFIG__ || {}).basePath || "";
    var parts = bp.replace(/\/+$/, "").split("/");
    return parts[parts.length - 1] || "";
  }
  async function _loadSnapshotEntries() {
    state.error = null;
    var cur = _currentRepoName();
    try {
      var r = await fetch(_manifestUrl());
      var d = r.ok ? await r.json() : [];
      var list = Array.isArray(d) ? d : (d.workspaces || []);
      state.entries = list.map(function (w) {
        var name = w.name || w.repo;
        return { repo: name, branch: w.branch || "", commit: w.commit || "",
                 label: name + (w.branch ? " @ " + w.branch : ""),
                 url: w.url, current: name === cur };
      });
      state.current = state.entries.filter(function (e) { return e.current; })[0] || null;
    } catch (e) { state.entries = []; }
  }

  function _el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function _short(c) { return (c || "").slice(0, 10); }
  function _dateSuffix(iso) { return iso ? " · " + String(iso).slice(0, 10) : ""; }
  // A human label for a build/workspace row: short sha + date (remote) or label (local).
  function _entryText(m) {
    if (m.simulator_id != null) {
      return m.repo + " @ " + _short(m.commit) + _dateSuffix(m.created_at)
        + " (build #" + m.simulator_id + ")" + (m.branch ? "  [" + m.branch + "]" : "");
    }
    return m.label;
  }

  async function _switchLocal(path) {
    var r = await fetch("/api/source/switch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path }),
    });
    _afterSwitch(r);
  }

  // Session-per-tab (pinned-for-life): a tab is one source for its life, so a
  // selection OPENS A NEW TAB bound to it rather than re-pointing this one.
  //   local workspace → /?workspace=<catalog-name>   (session.js binds by name)
  //   sms-api build    → /?build=<simulator_id>        (…→ switch-build, materialize)
  // session.js's bootstrap force-mints a fresh per-tab session in the new tab and
  // performs the bind; session-status.js shows the ⏳ favicon while a build
  // materializes. A local entry with no catalog name falls back to the in-place
  // switch (can't spawn by name).
  function _openEntry(entry) {
    if (!entry) return;
    // Honor the deployment base path (e.g. "/workbench" behind the ALB). The
    // global fetch/XHR shim prefixes /api/… itself, but does NOT patch
    // window.open, and "/?workspace=" wouldn't match its prefix list anyway — so
    // build the spawn URL with __BASE_PATH__ here. Empty in local/root hosting.
    var BP = window.__BASE_PATH__ || "";
    if (entry.simulator_id != null) {
      window.open(BP + "/?build=" + encodeURIComponent(entry.simulator_id), "_blank");
    } else if (entry.name) {
      window.open(BP + "/?workspace=" + encodeURIComponent(entry.name), "_blank");
    } else if (entry.path) {
      _switchLocal(entry.path);   // name-less catalog entry → in-place fallback
    }
  }

  async function _switchRemote(simulatorId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
    // First materialization downloads the build's workspace (~hundreds of MB,
    // up to a few minutes); cached builds switch instantly. Show a note so a
    // long download doesn't look stuck.
    _setBusy("Loading build " + simulatorId + " — downloading its workspace on first use (cached builds are instant)…");
    try {
      var r = await fetch("/api/source/switch-build", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ simulator_id: Number(simulatorId) }),
      });
    } catch (e) {
      _setBusy(""); if (btn) { btn.disabled = false; btn.textContent = "Switch"; }
      alert("Switch failed: network error"); return;
    }
    if (btn) { btn.disabled = false; btn.textContent = "Switch"; }
    if (!r.ok) _setBusy("");
    _afterSwitch(r);
  }

  function _setBusy(msg) {
    var el = document.getElementById("viv-bs-busy");
    if (!el) {
      var host = document.getElementById("viv-branch-source");
      if (!host) return;
      el = _el("div", "viv-bs-busy"); el.id = "viv-bs-busy";
      host.appendChild(el);
    }
    el.textContent = msg || "";
    el.style.display = msg ? "block" : "none";
  }

  async function _afterSwitch(r) {
    if (r.ok) {
      try { sessionStorage.setItem("viv-source-switched", "1"); } catch (e) {}
      window.location.reload();
    } else {
      var d = await r.json().catch(function () { return {}; });
      alert("Switch failed: " + (d.error || r.status));
    }
  }

  async function _forget(path, row) {
    var r = await fetch("/api/workspaces/forget", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path }),
    });
    if (r.ok) { row.remove(); }
    else { var d = await r.json().catch(function () { return {}; }); alert("Couldn't forget: " + (d.error || r.status)); }
  }

  async function _loadEntries() {
    state.error = null;  // start each load clean so a stale remote error never lingers
    if (state.scope === "local") {
      // Reuse the workspaces payload refresh() already fetched (it's slow); only
      // fetch again if we don't have it (e.g. a scope-toggle re-load).
      var d = state._wsData;
      if (!d) {
        var r = await fetch("/api/workspaces").catch(function () { return null; });
        d = (r && r.ok) ? await r.json() : { workspaces: [] };
      }
      state._wsData = null;  // consume it, so an explicit reload refetches
      state.entries = (d.workspaces || []).map(function (w) {
        return { repo: w.repo || w.name, branch: w.branch || "", commit: w.commit || "",
                 label: w.label || w.name, path: w.path, name: w.name,
                 current: w.status === "current" };
      });
    } else {
      var rb = await fetch("/api/source/builds").catch(function () { return null; });
      var db = (rb && rb.ok)
        ? await rb.json().catch(function () { return { error: "bad response" }; })
        : { error: "unreachable" };
      if (db.error) {
        // Transient outage (the sms-api tunnel reconnecting): ride it out. Keep
        // the last-known builds on screen instead of blanking the panel —
        // _render shows a quiet "reconnecting" chip, not the raw error.
        state.error = db.error;
      } else {
        state.error = null;
        state.entries = (db.builds || []).map(function (b) {
          return { repo: b.repo, repo_url: b.repo_url, branch: b.branch || "", commit: b.commit || "",
                   created_at: b.created_at || "", label: b.label, simulator_id: b.simulator_id,
                   current: b.simulator_id === state.currentSimId };
        });
      }
    }
  }

  function _distinct(arr, key) {
    var seen = {}, out = [];
    arr.forEach(function (x) { var v = x[key] || ""; if (!seen[v]) { seen[v] = 1; out.push(v); } });
    return out.sort();
  }

  function _render() {
    var host = document.getElementById("viv-branch-source");
    if (!host) return;
    host.innerHTML = "";
    host.appendChild(_el("h3", "viv-bs-title", "Source"));

    // Read-only (remote-server) mode: the switchable sources are the workspaces
    // checked out ON the server — these are the "local" workspace-catalog entries
    // (path-based, switched in-process via /api/source/switch). The "remote"
    // scope (sms-api repo@commit builds) is a separate capability that needs the
    // sms-api tunnel; keep it available but do NOT force it (forcing remote routed
    // the Switch button to the simulator_id path, which on-disk workspaces lack →
    // the button silently did nothing). Labels are clarified for the remote client.
    var RO = !!((window._uiConfig || {}).readonly);

    // Scope toggle
    var scopeRow = _el("div", "viv-bs-row");
    scopeRow.appendChild(_el("label", "viv-bs-key", "Scope"));
    ["local", "remote"].forEach(function (s) {
      var label = RO
        ? (s === "local" ? "Workspaces" : "sms-api builds")
        : (s === "local" ? "Local" : "Remote");
      var b = _el("button", "viv-bs-toggle" + (state.scope === s ? " active" : ""), label);
      b.addEventListener("click", function () { state.scope = s; state.repo = null; state.branch = null; refresh(); });
      scopeRow.appendChild(b);
    });
    host.appendChild(scopeRow);

    var repos = _distinct(state.entries, "repo");
    if (state.repo == null || repos.indexOf(state.repo) < 0) {
      var seed = (state.current && state.current.repo);
      state.repo = (seed && repos.indexOf(seed) >= 0) ? seed : (repos[0] || null);
    }

    host.appendChild(_selectRow("Repo", "viv-bs-repo", repos, state.repo, function (v) {
      state.repo = v; state.branch = null; _render();
    }));

    var inRepo = state.entries.filter(function (e) { return e.repo === state.repo; });
    var branches = _distinct(inRepo, "branch");
    if (state.branch == null || branches.indexOf(state.branch) < 0) state.branch = branches[0] || null;
    host.appendChild(_selectRow("Branch", "viv-bs-branch", branches, state.branch, function (v) {
      state.branch = v; _render();
    }));

    var matches = inRepo.filter(function (e) { return e.branch === state.branch; });
    // Commit line (+ a select when a branch has multiple builds)
    var commitRow = _el("div", "viv-bs-row");
    commitRow.appendChild(_el("label", "viv-bs-key", "Commit"));
    if (matches.length <= 1) {
      var c = matches[0] || {};
      commitRow.appendChild(_el("span", "viv-bs-commit", c.commit ? (_short(c.commit) + _dateSuffix(c.created_at)) : "—"));
      if (c.current) commitRow.appendChild(_el("span", "viv-bs-current", "current ✓"));
      state.selected = c;
    } else {
      var sel = _el("select", "viv-bs-commit-select");
      matches.forEach(function (m) {
        var o = _el("option", null, _short(m.commit) + _dateSuffix(m.created_at) + (m.current ? " (current)" : ""));
        o.value = m.commit; sel.appendChild(o);
      });
      sel.addEventListener("change", function () {
        state.selected = matches.filter(function (m) { return m.commit === sel.value; })[0];
      });
      var cur = matches.filter(function (m) { return m.current; })[0] || matches[0];
      state.selected = cur;
      sel.value = cur.commit;
      commitRow.appendChild(sel);
    }
    host.appendChild(commitRow);

    // Actions
    var actions = _el("div", "viv-bs-actions");
    var switchBtn = _el("button", "viv-bs-action", "Open"); switchBtn.id = "viv-bs-switch";
    switchBtn.title = "Open this source in a new tab";
    switchBtn.addEventListener("click", function () {
      var s = state.selected || {};
      // Pinned-for-life: open the selection in a new tab (build → /?build=,
      // workspace → /?workspace=). Path-first fallback so a misread scope can
      // never leave the button doing nothing.
      if (s.path || s.name || s.simulator_id != null) _openEntry(s);
      else alert("Nothing to open — pick a repo/branch with a build or workspace.");
    });
    actions.appendChild(switchBtn);

    var pushBtn = _el("button", "viv-bs-action", "Commit + Push"); pushBtn.id = "viv-bs-push";
    pushBtn.disabled = state.scope !== "local";
    if (RO) pushBtn.style.display = "none";   // local git write — gone in remote-only
    pushBtn.addEventListener("click", function () {
      if (pushBtn.disabled) return;
      var msg = window.prompt("Commit message for push:", "dashboard commit");
      if (msg == null) return;
      pushBtn.disabled = true; pushBtn.textContent = "Pushing…";
      fetch("/api/branch/push", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          pushBtn.disabled = false; pushBtn.textContent = "Commit + Push";
          if (res.ok) alert("Pushed " + (res.d.branch || "") + " @ " + (res.d.commit || "").slice(0, 7));
          else alert("Push failed: " + (res.d.error || "error"));
        })
        .catch(function () {
          pushBtn.disabled = false; pushBtn.textContent = "Commit + Push";
          alert("Push failed: network error");
        });
    });
    actions.appendChild(pushBtn);

    var buildBtn = _el("button", "viv-bs-action", "Build via sms-api"); buildBtn.id = "viv-bs-build";
    buildBtn.disabled = !(state.scope === "remote" && state.selected && state.selected.repo_url);
    buildBtn.title = buildBtn.disabled ? "Select a Remote source (full repo URL) to register a build" : "";
    buildBtn.addEventListener("click", function () {
      var repo = (state.selected && state.selected.repo_url) || "", branch = state.branch;
      if (!repo || !branch) { alert("Pick a repo and branch first"); return; }
      buildBtn.disabled = true; buildBtn.textContent = "Registering…";
      fetch("/api/source/build-remote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo: repo, branch: branch }),
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          buildBtn.disabled = false; buildBtn.textContent = "Build via sms-api";
          if (res.ok) { alert("Registered build #" + res.d.simulator_id + " @ " + (res.d.commit || "").slice(0, 7)); state.scope = "remote"; refresh(); }
          else alert("Build failed: " + (res.d.error || "error"));
        })
        .catch(function () {
          buildBtn.disabled = false; buildBtn.textContent = "Build via sms-api";
          alert("Build failed: network error");
        });
    });
    actions.appendChild(buildBtn);

    var syncBtn = _el("button", "viv-bs-action", "Sync to local"); syncBtn.id = "viv-bs-sync";
    syncBtn.title = "Materialize this exact repo@commit workspace on your machine";
    syncBtn.addEventListener("click", function () {
      fetch("/api/source/manifest").then(function (r) { return r.json(); }).then(function (m) {
        var base = window.location.origin;
        var cmd = "vivarium-dashboard sync " + base;
        var note = "Reproduce " + (m.repo || "") + " @ " + String(m.commit || "").slice(0, 7) +
                   "\n  " + cmd + "\n(verifies uv.lock " + (m.lockfile || "—") + ")";
        window.prompt("Run this locally to sync + reproduce:", cmd);
        console.log(note);
      }).catch(function () { alert("Could not fetch manifest"); });
    });
    actions.appendChild(syncBtn);

    host.appendChild(actions);

    if (state.error) {
      if (state.scope === "remote") _ensureBuildsPoll();  // keep recovering in the background
      var haveBuilds = state.entries && state.entries.length;
      if (haveBuilds) {
        // Builds already on screen → a transient blip (tunnel reconnecting).
        // Quiet indicator only; the selectors/list above keep the last-known
        // builds, and this clears itself the moment a poll succeeds.
        var chip = _el("div", "viv-bs-note", "⟳ sms-api reconnecting… showing last-known builds");
        chip.style.cssText = "color:#92740e;font-size:0.82em;opacity:0.8;margin-top:6px";
        host.appendChild(chip);
      } else {
        // No builds yet (first-load failure): a calm, actionable message — not
        // the raw urlopen error.
        var errNote = _el("div", "viv-bs-note", "sms-api not reachable — is the tunnel up?"
          + (state.scope === "remote" ? " Auto-retrying every 5s…" : ""));
        var retryBtn = _el("button", "viv-bs-toggle", "Retry");
        retryBtn.style.marginLeft = "8px";
        retryBtn.title = "Re-check sms-api now";
        retryBtn.addEventListener("click", function () { _stopBuildsPoll(); refresh(); });
        errNote.appendChild(retryBtn);
        host.appendChild(errNote);
      }
    } else {
      _stopBuildsPoll();
    }

    // Search / paste-a-commit filter. While filtering in remote scope, search
    // across ALL builds of the repo (every branch) so you can paste any commit.
    // Only shown when there's actually more than one source to pick from — a lone
    // current workspace doesn't need a filter box (it read as idle noise).
    var search = null;
    if (matches.length > 1 || (state.filter || "").trim()) {
      search = _el("input", "viv-bs-search");
      search.type = "search";
      search.placeholder = state.scope === "remote"
        ? "search or paste a commit / date / branch…" : "filter workspaces…";
      search.value = state.filter || "";
      host.appendChild(search);
    }

    var list = _el("ul", "viv-bs-list");
    host.appendChild(list);

    function _fillList() {
      list.innerHTML = "";
      var f = (state.filter || "").trim().toLowerCase();
      var rows = matches;
      if (f) {
        var pool = (state.scope === "remote") ? inRepo : state.entries;
        rows = pool.filter(function (e) {
          return [(e.commit || ""), (e.label || ""), (e.created_at || ""), (e.branch || "")]
            .join(" ").toLowerCase().indexOf(f) >= 0;
        });
      }
      rows.forEach(function (m) {
        var li = _el("li", "viv-bs-list-row" + (m.current ? " current" : ""));
        var lbl = _el("span", "viv-bs-list-label", _entryText(m));
        lbl.style.cursor = "pointer";
        lbl.title = "Open this source in a new tab";
        lbl.addEventListener("click", function () { _openEntry(m); });
        li.appendChild(lbl);
        if (state.scope === "local" && !m.current && m.path) {
          var x = _el("button", "viv-bs-forget", "✕"); x.title = "Forget";
          x.addEventListener("click", function (e) { e.stopPropagation(); _forget(m.path, li); });
          li.appendChild(x);
        }
        list.appendChild(li);
      });
      // "no matches" only while actively filtering — never as idle noise when
      // there's simply nothing else to switch to.
      if (!rows.length && f) list.appendChild(_el("li", "viv-bs-list-empty", "no matches"));
    }
    if (search) search.addEventListener("input", function () { state.filter = search.value; _fillList(); });
    _fillList();
  }

  function _selectRow(key, id, options, value, onChange) {
    var row = _el("div", "viv-bs-row");
    row.appendChild(_el("label", "viv-bs-key", key));
    var sel = _el("select", "viv-bs-select"); sel.id = id;
    options.forEach(function (o) {
      var opt = _el("option", null, o || "—"); opt.value = o;
      if (o === value) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener("change", function () { onChange(sel.value); });
    row.appendChild(sel);
    return row;
  }

  // Published (snapshot) Source panel: a navigator across the sibling published
  // workspaces. No scope toggle, no push/build/PR, no live in-process switch —
  // "Switch" navigates to the chosen bundle. "Sync to local" is kept (it's the
  // round-trip: clone this exact repo@commit locally via `vivarium-dashboard sync`).
  function _renderSnapshot() {
    var host = document.getElementById("viv-branch-source");
    if (!host) return;
    host.innerHTML = "";
    host.appendChild(_el("h3", "viv-bs-title", "Source"));

    var entries = state.entries || [];
    var names = entries.map(function (e) { return e.repo; });
    if (!state.selected || names.indexOf(state.selected.repo) < 0) {
      state.selected = state.current || entries[0] || null;
    }
    if (!entries.length) {
      host.appendChild(_el("p", "viv-bs-note", "No other published workspaces found."));
      return;
    }
    host.appendChild(_selectRow("Repo", "viv-bs-repo", names,
      state.selected ? state.selected.repo : null, function (v) {
        state.selected = entries.filter(function (e) { return e.repo === v; })[0] || null;
        _renderSnapshot();
      }));

    var sel = state.selected || {};
    var brRow = _el("div", "viv-bs-row");
    brRow.appendChild(_el("label", "viv-bs-key", "Branch"));
    brRow.appendChild(_el("span", "viv-bs-commit", sel.branch || "—"));
    host.appendChild(brRow);
    var cRow = _el("div", "viv-bs-row");
    cRow.appendChild(_el("label", "viv-bs-key", "Commit"));
    cRow.appendChild(_el("span", "viv-bs-commit", sel.commit ? _short(sel.commit) : "—"));
    if (sel.current) cRow.appendChild(_el("span", "viv-bs-current", "current ✓"));
    host.appendChild(cRow);

    var actions = _el("div", "viv-bs-actions");
    var switchBtn = _el("button", "viv-bs-action", "Switch"); switchBtn.id = "viv-bs-switch";
    switchBtn.disabled = !!sel.current;
    switchBtn.title = sel.current ? "Already viewing this workspace" : "Open this workspace";
    switchBtn.addEventListener("click", function () {
      if (!sel.current && sel.url) window.location.href = sel.url;
    });
    actions.appendChild(switchBtn);

    var syncBtn = _el("button", "viv-bs-action", "Sync to local"); syncBtn.id = "viv-bs-sync";
    syncBtn.title = "Reproduce this exact repo@commit on your machine";
    syncBtn.addEventListener("click", function () {
      var dir = (window.location.origin + window.location.pathname).replace(/[^/]*$/, "");
      var cmd = "vivarium-dashboard sync " + dir.replace(/\/$/, "");
      window.prompt("Run this locally to clone + reproduce this workspace:", cmd);
    });
    actions.appendChild(syncBtn);
    host.appendChild(actions);

    var list = _el("ul", "viv-bs-list");
    entries.forEach(function (e) {
      var li = _el("li", "viv-bs-list-row" + (e.current ? " current" : ""));
      var lbl = _el("span", "viv-bs-list-label", e.label + (e.current ? "  (this)" : ""));
      if (!e.current && e.url) {
        lbl.style.cursor = "pointer";
        lbl.title = "Open this workspace";
        lbl.addEventListener("click", function () { window.location.href = e.url; });
      }
      li.appendChild(lbl);
      list.appendChild(li);
    });
    host.appendChild(list);
  }

  function _stopBuildsPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // While a Remote source is selected but sms-api is unreachable, keep
  // re-checking in the background so a recovered tunnel clears the error and
  // populates the builds on its own — no manual page reload needed.
  function _ensureBuildsPoll() {
    if (pollTimer) return;
    pollTimer = setInterval(function () {
      if (state.scope !== "remote") { _stopBuildsPoll(); return; }
      _loadEntries().then(function () {
        if (!state.error) _stopBuildsPoll();
        _render();
      }).catch(function () {});
    }, 5000);
  }

  async function refresh() {
    var host = document.getElementById("viv-branch-source");
    if (!host) return;
    if (SNAP) { await _loadSnapshotEntries(); _renderSnapshot(); return; }
    _stopBuildsPoll();
    // Paint the Source shell (title + Scope toggle + selectors) IMMEDIATELY so it
    // appears on the first visit even while the workspace/builds fetches are in
    // flight — those can take many seconds, and the panel previously stayed blank
    // until they finished. A second _render() below fills in the loaded entries.
    state.loading = true;
    _render();
    // /api/workspaces is slow (git status across every workspace). Fetch it ONCE
    // here and reuse it for both `current` and the local entries (was fetched
    // twice, doubling the wait).
    var r = await fetch("/api/workspaces").catch(function () { return null; });
    var wsData = (r && r.ok) ? await r.json().catch(function () { return {}; }) : {};
    state._wsData = wsData;
    var cur = wsData.current || null;
    var curPath = (cur && cur.path) || "";
    // A materialized remote build lives at .../build-cache/sim<id>-<commit>.
    var bm = curPath.match(/build-cache\/sim(\d+)-/);
    state.currentSimId = bm ? Number(bm[1]) : null;
    // On first load, reflect the ACTIVE source's scope (so switching to a remote
    // build and reloading lands on Remote, not back on Local). Later refreshes
    // honor the user's explicit scope toggle.
    if (!state.inited) {
      state.inited = true;
      state.scope = state.currentSimId != null ? "remote" : "local";
      state.repo = null; state.branch = null;
    }
    state.current = cur ? { repo: cur.name } : null;
    await _loadEntries();
    state.loading = false;
    // Seed the selectors from the active remote build so it shows as current.
    if (state.currentSimId != null) {
      var cb = state.entries.filter(function (e) { return e.simulator_id === state.currentSimId; })[0];
      if (cb) {
        if (state.repo == null) state.repo = cb.repo;
        if (state.branch == null) state.branch = cb.branch;
      }
    }
    _render();
  }

  window._renderBranchSource = refresh;
  document.addEventListener("DOMContentLoaded", function () {
    if (document.getElementById("viv-branch-source")) refresh();
  });
})();
