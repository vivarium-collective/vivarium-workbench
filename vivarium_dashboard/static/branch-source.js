// branch-source.js — the Branch-tab Source panel. Organizes the dashboard's
// source as local/remote · repo · branch · commit, and re-points the active
// source in-process. Replaces the rail dropdown (source-switch.js).
(function () {
  "use strict";

  var state = { scope: "local", repo: null, branch: null, entries: [], current: null };

  function _el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  async function _switchLocal(path) {
    var r = await fetch("/api/source/switch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path }),
    });
    _afterSwitch(r);
  }

  async function _switchRemote(simulatorId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Loading build…"; }
    var r = await fetch("/api/source/switch-build", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ simulator_id: Number(simulatorId) }),
    });
    if (btn) { btn.disabled = false; btn.textContent = "Switch"; }
    _afterSwitch(r);
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
        return { repo: b.repo, branch: b.branch || "", commit: b.commit || "",
                 label: b.label, simulator_id: b.simulator_id };
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

    // Scope toggle
    var scopeRow = _el("div", "viv-bs-row");
    scopeRow.appendChild(_el("label", "viv-bs-key", "Scope"));
    ["local", "remote"].forEach(function (s) {
      var b = _el("button", "viv-bs-toggle" + (state.scope === s ? " active" : ""), s === "local" ? "Local" : "Remote");
      b.addEventListener("click", function () { state.scope = s; state.repo = null; state.branch = null; refresh(); });
      scopeRow.appendChild(b);
    });
    host.appendChild(scopeRow);

    var repos = _distinct(state.entries, "repo");
    if (state.repo == null) state.repo = (state.current && state.current.repo) || repos[0] || null;

    host.appendChild(_selectRow("Repo", "viv-bs-repo", repos, state.repo, function (v) {
      state.repo = v; state.branch = null; _render();
    }));

    var inRepo = state.entries.filter(function (e) { return e.repo === state.repo; });
    var branches = _distinct(inRepo, "branch");
    if (state.branch == null) state.branch = branches[0] || null;
    host.appendChild(_selectRow("Branch", "viv-bs-branch", branches, state.branch, function (v) {
      state.branch = v; _render();
    }));

    var matches = inRepo.filter(function (e) { return e.branch === state.branch; });
    // Commit line (+ a select when a branch has multiple builds)
    var commitRow = _el("div", "viv-bs-row");
    commitRow.appendChild(_el("label", "viv-bs-key", "Commit"));
    if (matches.length <= 1) {
      var c = matches[0] || {};
      commitRow.appendChild(_el("span", "viv-bs-commit", c.commit || "—"));
      if (c.current) commitRow.appendChild(_el("span", "viv-bs-current", "current ✓"));
      state.selected = c;
    } else {
      var sel = _el("select", "viv-bs-commit-select");
      matches.forEach(function (m) {
        var o = _el("option", null, (m.commit || "?") + (m.current ? " (current)" : ""));
        o.value = m.commit; sel.appendChild(o);
      });
      sel.addEventListener("change", function () {
        state.selected = matches.filter(function (m) { return m.commit === sel.value; })[0];
      });
      state.selected = matches[0];
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
    buildBtn.disabled = !(state.repo && String(state.repo).indexOf("http") === 0);
    buildBtn.title = buildBtn.disabled ? "Select a Remote source (full repo URL) to register a build" : "";
    buildBtn.addEventListener("click", function () {
      var repo = state.repo, branch = state.branch;
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
        });
    });
    actions.appendChild(buildBtn);

    host.appendChild(actions);

    if (state.error) host.appendChild(_el("div", "viv-bs-note", "sms-api: " + state.error));

    // Sibling list (forget ✕ for local)
    var list = _el("ul", "viv-bs-list");
    matches.forEach(function (m) {
      var li = _el("li", "viv-bs-list-row" + (m.current ? " current" : ""));
      li.appendChild(_el("span", "viv-bs-list-label", m.label));
      if (state.scope === "local" && !m.current && m.path) {
        var x = _el("button", "viv-bs-forget", "✕"); x.title = "Forget";
        x.addEventListener("click", function (e) { e.stopPropagation(); _forget(m.path, li); });
        li.appendChild(x);
      }
      list.appendChild(li);
    });
    host.appendChild(list);
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

  async function refresh() {
    var host = document.getElementById("viv-branch-source");
    if (!host) return;
    var r = await fetch("/api/workspaces").catch(function () { return null; });
    if (r && r.ok) { var d = await r.json(); state.current = (d.current && { repo: d.current.name }) || null; }
    await _loadEntries();
    _render();
  }

  window._renderBranchSource = refresh;
  document.addEventListener("DOMContentLoaded", function () {
    if (document.getElementById("viv-branch-source")) refresh();
  });
})();
