// branch-source.js — the Branch-tab Source panel. Organizes the dashboard's
// source as local/remote · repo · branch · commit, and re-points the active
// source in-process. Replaces the rail dropdown (source-switch.js).
(function () {
  "use strict";

  var state = { scope: "local", repo: null, branch: null, entries: [], current: null };
  var pollTimer = null;

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
      var r = await fetch("/api/workspaces");
      var d = r.ok ? await r.json() : { workspaces: [] };
      state.entries = (d.workspaces || []).map(function (w) {
        return { repo: w.repo || w.name, branch: w.branch || "", commit: w.commit || "",
                 label: w.label || w.name, path: w.path, current: w.status === "current" };
      });
    } else {
      var rb = await fetch("/api/source/builds");
      var db = rb.ok ? await rb.json() : { builds: [], error: "unreachable" };
      state.error = db.error || null;
      state.entries = (db.builds || []).map(function (b) {
        return { repo: b.repo, repo_url: b.repo_url, branch: b.branch || "", commit: b.commit || "",
                 created_at: b.created_at || "", label: b.label, simulator_id: b.simulator_id,
                 current: b.simulator_id === state.currentSimId };
      });
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

    // Remote-only (read-only) mode: force remote scope, no Local source option.
    var RO = !!((window._uiConfig || {}).readonly);
    if (RO) state.scope = "remote";

    // Scope toggle
    var scopeRow = _el("div", "viv-bs-row");
    scopeRow.appendChild(_el("label", "viv-bs-key", "Scope"));
    (RO ? ["remote"] : ["local", "remote"]).forEach(function (s) {
      var b = _el("button", "viv-bs-toggle" + (state.scope === s ? " active" : ""), s === "local" ? "Local" : "Remote");
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
    var switchBtn = _el("button", "viv-bs-action", "Switch"); switchBtn.id = "viv-bs-switch";
    switchBtn.addEventListener("click", function () {
      var s = state.selected || {};
      if (state.scope === "local" && s.path) _switchLocal(s.path);
      else if (state.scope === "remote" && s.simulator_id != null) _switchRemote(s.simulator_id, switchBtn);
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

    host.appendChild(actions);

    if (state.error) {
      var errNote = _el("div", "viv-bs-note", "sms-api: " + state.error);
      var retryBtn = _el("button", "viv-bs-toggle", "Retry");
      retryBtn.style.marginLeft = "8px";
      retryBtn.title = "Re-check sms-api now";
      retryBtn.addEventListener("click", function () { _stopBuildsPoll(); refresh(); });
      errNote.appendChild(retryBtn);
      if (state.scope === "remote") {
        var hint = _el("span", "viv-bs-retry-hint", " · auto-retrying every 5s…");
        hint.style.opacity = "0.7";
        errNote.appendChild(hint);
        _ensureBuildsPoll();   // recover automatically when the tunnel comes back
      }
      host.appendChild(errNote);
    } else {
      _stopBuildsPoll();
    }

    // Search / paste-a-commit filter. While filtering in remote scope, search
    // across ALL builds of the repo (every branch) so you can paste any commit.
    var search = _el("input", "viv-bs-search");
    search.type = "search";
    search.placeholder = state.scope === "remote"
      ? "search or paste a commit / date / branch…" : "filter workspaces…";
    search.value = state.filter || "";
    host.appendChild(search);

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
        lbl.title = "Switch to this source";
        lbl.addEventListener("click", function () {
          if (m.simulator_id != null) _switchRemote(m.simulator_id);
          else if (m.path) _switchLocal(m.path);
        });
        li.appendChild(lbl);
        if (state.scope === "local" && !m.current && m.path) {
          var x = _el("button", "viv-bs-forget", "✕"); x.title = "Forget";
          x.addEventListener("click", function (e) { e.stopPropagation(); _forget(m.path, li); });
          li.appendChild(x);
        }
        list.appendChild(li);
      });
      if (!rows.length) list.appendChild(_el("li", "viv-bs-list-empty", "no matches"));
    }
    search.addEventListener("input", function () { state.filter = search.value; _fillList(); });
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
    _stopBuildsPoll();
    var r = await fetch("/api/workspaces").catch(function () { return null; });
    var cur = (r && r.ok) ? ((await r.json()).current || null) : null;
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
