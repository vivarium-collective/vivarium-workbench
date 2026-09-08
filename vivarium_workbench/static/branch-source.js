// branch-source.js — the Branch-tab Source panel. Organizes the dashboard's
// source as local/remote · repo · branch · commit, and re-points the active
// source in-process. Replaces the rail dropdown (source-switch.js).
(function () {
  "use strict";

  var state = { scope: "local", repo: null, branch: null, entries: [], current: null, health: null, newBranch: "", showAllBuilds: false };
  var BUILD_LIST_LIMIT = 12;   // collapse a long remote build history to the recent N
  var NEW_BRANCH_SENTINEL = "__new_branch__";
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

  // A small, subtle per-row action button (matches the health-row's inline
  // styling convention in this file). `variant === "primary"` tints Switch here.
  function _rowBtn(text, title, variant) {
    var b = _el("button", "viv-bs-rowbtn", text);
    if (title) b.title = title;
    var primary = variant === "primary";
    b.style.cssText = "font-size:11px; line-height:1.4; padding:2px 8px; border-radius:5px; "
      + "cursor:pointer; white-space:nowrap; "
      + (primary
        ? "border:1px solid #b7c6ea; background:#eef3fd; color:#2f57b5;"
        : "border:1px solid #d5dbe4; background:#fff; color:#3a4657;");
    return b;
  }
  function _rowTag(text) {
    var t = _el("span", "viv-bs-rowtag", text);
    t.style.cssText = "font-size:11px; color:#41a06a; white-space:nowrap; padding:0 2px";
    return t;
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
    // First materialization downloads the build's workspace (~hundreds of MB, up
    // to a few minutes); cached builds switch instantly. A ticking counter + Cancel
    // + a client-side timeout matching the server's 600s download cap mean a long
    // download reads as progress, never a dead spinner (hardening for external users).
    var controller = new AbortController();
    var t0 = Date.now();
    function _msg() {
      var s = Math.round((Date.now() - t0) / 1000);
      return "Loading build " + simulatorId + " — downloading its workspace (" + s + "s; cached builds are instant)…";
    }
    _setBusy(_msg(), function () { controller.abort(); });
    var ticker = setInterval(function () { _setBusy(_msg(), function () { controller.abort(); }); }, 1000);
    var deadline = setTimeout(function () { controller.abort(); }, 610000);  // 600s server cap + buffer
    function _cleanup() { clearInterval(ticker); clearTimeout(deadline); }
    try {
      var r = await fetch("/api/source/switch-build", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ simulator_id: Number(simulatorId) }),
        signal: controller.signal,
      });
    } catch (e) {
      _cleanup(); _setBusy(""); if (btn) { btn.disabled = false; btn.textContent = "Switch"; }
      alert(e && e.name === "AbortError"
        ? "Switch cancelled or timed out — the workspace download took too long. The remote endpoint may be slow or unreachable."
        : "Switch failed: network error");
      return;
    }
    _cleanup();
    if (btn) { btn.disabled = false; btn.textContent = "Switch"; }
    if (!r.ok) _setBusy("");
    _afterSwitch(r);
  }

  function _setBusy(msg, onCancel) {
    var el = document.getElementById("viv-bs-busy");
    if (!el) {
      var host = document.getElementById("viv-branch-source");
      if (!host) return;
      el = _el("div", "viv-bs-busy"); el.id = "viv-bs-busy";
      host.appendChild(el);
    }
    el.innerHTML = "";
    if (msg) {
      el.appendChild(_el("span", "viv-bs-busy-msg", msg));
      if (typeof onCancel === "function") {
        var c = _el("button", "viv-bs-cancel", "Cancel");
        c.style.cssText = "margin-left:10px";
        c.addEventListener("click", onCancel);
        el.appendChild(c);
      }
    }
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
      // Health probe for the panel indicator — best-effort, independent of the
      // builds list (so the dot shows red-unreachable even when builds is empty).
      var rh = await fetch("/api/source/remote-health").catch(function () { return null; });
      state.health = (rh && rh.ok) ? await rh.json().catch(function () { return null; }) : null;
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
        var curC = state.currentCommit ? _short(state.currentCommit) : "";
        state.entries = (db.builds || []).map(function (b) {
          return { repo: b.repo, repo_url: b.repo_url, branch: b.branch || "", commit: b.commit || "",
                   created_at: b.created_at || "", label: b.label, simulator_id: b.simulator_id,
                   // `current` = THIS build is the tab's active source (a materialized
                   // build). `matchesWorkspace` = a build of the commit the workspace
                   // is on (may be several; shown when not the exact active build).
                   current: b.simulator_id === state.currentSimId,
                   matchesWorkspace: !!curC && _short(b.commit) === curC };
        });
      }
    }
  }

  function _distinct(arr, key) {
    var seen = {}, out = [];
    arr.forEach(function (x) { var v = x[key] || ""; if (!seen[v]) { seen[v] = 1; out.push(v); } });
    return out.sort();
  }

  // Remote sms-api health indicator row (state.health = {configured, base_url,
  // reachable, version, error} from /api/source/remote-health).
  function _healthRow() {
    var h = state.health;
    var row = _el("div", "viv-bs-row viv-bs-health");
    row.style.cssText = "align-items:center; gap:8px; font-size:12px; margin:2px 0 8px";
    var dot = _el("span", "viv-bs-health-dot");
    dot.style.cssText = "width:8px; height:8px; border-radius:50%; display:inline-block; flex:0 0 auto";
    var color, txt;
    if (!h) {
      color = "#93a1b5"; txt = "checking remote endpoint…";
    } else if (!h.configured && !h.reachable) {
      color = "#5d6b7e"; txt = "SMS_API_BASE not set — remote disabled";
    } else if (h.reachable) {
      color = "#41d886";
      txt = h.base_url + (h.version ? "  ·  sms-api v" + h.version : "") + "  ·  reachable ✓";
      dot.style.boxShadow = "0 0 6px " + color;
    } else {
      color = "#ff5d6c";
      txt = h.base_url + " — unreachable ✗ (is the tunnel up?)";
      if (h.error) row.title = h.error;
    }
    dot.style.background = color;
    var label = _el("span", "viv-bs-health-txt", txt);
    label.style.cssText = "color:#93a1b5; font-family:ui-monospace,SFMono-Regular,Menlo,monospace";
    row.appendChild(dot);
    row.appendChild(label);
    return row;
  }

  function _render() {
    var host = document.getElementById("viv-branch-source");
    if (!host) return;
    host.innerHTML = "";
    host.appendChild(_el("h3", "viv-bs-title", "Source"));
    // One-line scope cue so the two cards read as distinct jobs: this card is
    // "where the tab runs"; the GitHub card below is "sync & collaborate".
    var _sub = _el("div", "viv-bs-subtitle", "Where this tab runs — pick a local workspace or a remote build.");
    _sub.style.cssText = "color:#93a1b5; font-size:12px; margin:-4px 0 12px";
    host.appendChild(_sub);

    // Read-only (remote-server) mode: the switchable sources are the workspaces
    // checked out ON the server — these are the "local" workspace-catalog entries
    // (path-based, switched in-process via /api/source/switch). The "remote"
    // scope (sms-api repo@commit builds) is a separate capability that needs the
    // sms-api tunnel; keep it available but do NOT force it (forcing remote routed
    // the Switch button to the simulator_id path, which on-disk workspaces lack →
    // the button silently did nothing). Labels are clarified for the remote client.
    var RO = !!((window._uiConfig || {}).readonly);

    // Scope toggle — wrap the buttons in a segmented group so they share one grid
    // cell (otherwise each button lands in its own cell and "Remote" wraps).
    var scopeRow = _el("div", "viv-bs-row");
    scopeRow.appendChild(_el("label", "viv-bs-key", "Scope"));
    var scopeGroup = _el("div", "viv-bs-scope-group");
    ["local", "remote"].forEach(function (s) {
      var label = RO
        ? (s === "local" ? "Workspaces" : "sms-api builds")
        : (s === "local" ? "Local" : "Remote");
      var b = _el("button", "viv-bs-toggle" + (state.scope === s ? " active" : ""), label);
      b.addEventListener("click", function () { state.scope = s; state.repo = null; state.branch = null; state.newBranch = ""; state.showAllBuilds = false; refresh(); });
      scopeGroup.appendChild(b);
    });
    scopeRow.appendChild(scopeGroup);
    host.appendChild(scopeRow);

    // Remote endpoint health indicator: a 🟢/🔴 dot + the configured SMS_API_BASE,
    // so a user knows whether the remote deployment is even reachable *before* they
    // pick a build (no more silent hangs on an unreachable tunnel).
    if (state.scope === "remote") host.appendChild(_healthRow());

    var repos = _distinct(state.entries, "repo");
    if (state.repo == null || repos.indexOf(state.repo) < 0) {
      var seed = (state.current && state.current.repo);
      state.repo = (seed && repos.indexOf(seed) >= 0) ? seed : (repos[0] || null);
    }

    host.appendChild(_selectRow("Repo", "viv-bs-repo", repos, state.repo, function (v) {
      state.repo = v; state.branch = null; state.newBranch = ""; state.showAllBuilds = false; _render();
    }));

    var inRepo = state.entries.filter(function (e) { return e.repo === state.repo; });
    // Order the branch list for usefulness, not alphabetically (clearer remote
    // selection): `main` first, then most-recently-built branch first (recency =
    // the branch's highest sms-api build id). Local scope has no build ids, so it
    // falls back to main-first + the alphabetical order _distinct already gives.
    function _branchRecency(br) {
      var t = 0;
      inRepo.forEach(function (e) {
        if (e.branch !== br) return;
        var v = e.simulator_id != null ? Number(e.simulator_id) : 0;
        if (v > t) t = v;
      });
      return t;
    }
    var branches = _distinct(inRepo, "branch").sort(function (a, b) {
      if (a === "main" && b !== "main") return -1;
      if (b === "main" && a !== "main") return 1;
      return _branchRecency(b) - _branchRecency(a);
    });
    // Remote scope: `branches` only ever covers branches with an existing sms-api
    // build (state.entries is sourced from /api/source/builds) — registering a
    // brand-new branch's build already works server-side (/api/source/build-remote
    // resolves any real branch's live HEAD via sms-api), the picker just never
    // offered a way to type one (item 67). Append a sentinel option that reveals a
    // free-text branch input instead of forcing a pick from already-built ones.
    var branchOptions = branches.slice();
    if (state.scope === "remote" && state.repo) branchOptions.push(NEW_BRANCH_SENTINEL);
    if (state.branch == null || (branches.indexOf(state.branch) < 0 && state.branch !== NEW_BRANCH_SENTINEL)) {
      // Default to `main` when it exists (clearer remote selection) so the picker
      // opens on the mainline HEAD, not an arbitrary alphabetically-first feature
      // branch; otherwise the most-recent branch (branches[0] after the sort above).
      state.branch = (branches.indexOf("main") >= 0 ? "main" : (branches[0] || null));
    }
    host.appendChild(_selectRow("Branch", "viv-bs-branch", branchOptions, state.branch, function (v) {
      state.branch = v; state.showAllBuilds = false; _render();
    }, function (v) { return v === NEW_BRANCH_SENTINEL ? "+ New branch…" : (v || "—"); }));

    if (state.branch === NEW_BRANCH_SENTINEL) {
      var newBranchRow = _el("div", "viv-bs-row");
      newBranchRow.appendChild(_el("label", "viv-bs-key", ""));
      var nbInput = _el("input", "viv-bs-select"); nbInput.id = "viv-bs-new-branch";
      nbInput.type = "text";
      nbInput.placeholder = "branch name — no build yet, HEAD resolved on Build";
      nbInput.value = state.newBranch || "";
      // Update state only — NOT a full _render(), which would tear down and
      // recreate this input on every keystroke and drop focus/cursor position
      // (same reasoning as the existing search-filter input below).
      nbInput.addEventListener("input", function () {
        state.newBranch = nbInput.value;
        // buildBtn/repoUrlForBuild are declared later in this same _render() call
        // but already assigned by the time a keystroke fires this handler (var
        // hoisting + closure-by-reference) — keep the button's enabled state
        // live as the user types, without a full re-render.
        if (typeof buildBtn !== "undefined" && buildBtn) {
          var eb = nbInput.value.trim();
          buildBtn.disabled = !(repoUrlForBuild && eb);
          buildBtn.title = buildBtn.disabled
            ? "Select a Remote repo and branch (or type a new branch name) to register a build" : "";
        }
      });
      newBranchRow.appendChild(nbInput);
      host.appendChild(newBranchRow);
    }

    var matches = inRepo.filter(function (e) { return e.branch === state.branch; });
    // Commit line (+ a select when a branch has multiple builds)
    var commitRow = _el("div", "viv-bs-row");
    commitRow.appendChild(_el("label", "viv-bs-key", "Commit"));
    if (state.branch === NEW_BRANCH_SENTINEL) {
      commitRow.appendChild(_el("span", "viv-bs-commit", "resolved from branch HEAD on Build"));
      state.selected = {};
    } else if (matches.length <= 1) {
      var c = matches[0] || {};
      commitRow.appendChild(_el("span", "viv-bs-commit", c.commit ? (_short(c.commit) + _dateSuffix(c.created_at)) : "—"));
      if (c.current) commitRow.appendChild(_el("span", "viv-bs-current", "current ✓"));
      state.selected = c;
    } else {
      // Multiple builds for this branch (e.g. many `main` builds as it advanced):
      // list newest-first and default to the current build if loaded, else the
      // newest — so picking `main` means the LATEST main, not the oldest registered.
      var ordered = matches.slice().sort(function (a, b) {
        return (Number(b.simulator_id) || 0) - (Number(a.simulator_id) || 0);
      });
      var sel = _el("select", "viv-bs-commit-select");
      ordered.forEach(function (m, i) {
        var tag = m.current ? " (current)" : (i === 0 ? " (latest)" : "");
        var o = _el("option", null, _short(m.commit) + _dateSuffix(m.created_at) + tag);
        o.value = m.commit; sel.appendChild(o);
      });
      sel.addEventListener("change", function () {
        state.selected = ordered.filter(function (m) { return m.commit === sel.value; })[0];
      });
      var cur = ordered.filter(function (m) { return m.current; })[0] || ordered[0];
      state.selected = cur;
      sel.value = cur.commit;
      commitRow.appendChild(sel);
    }
    host.appendChild(commitRow);

    // Actions
    var actions = _el("div", "viv-bs-actions");
    // Switch HERE — re-point THIS tab to the selected source in place. A remote
    // build downloads its workspace the first time (a few minutes; cached builds
    // are instant); a local workspace switches in-process. Distinct from "Open in
    // new tab" (which leaves this tab untouched).
    var switchHereBtn = _el("button", "viv-bs-action", "Switch here"); switchHereBtn.id = "viv-bs-switch-here";
    switchHereBtn.title = "Switch THIS tab to the selected source. A remote build downloads its "
      + "workspace the first time (a few minutes; cached builds are instant); a local workspace "
      + "switches in place. Use “Open in new tab” to keep this tab as it is.";
    switchHereBtn.addEventListener("click", function () {
      var s = state.selected || {};
      if (s.simulator_id != null) _switchRemote(s.simulator_id, switchHereBtn);
      else if (s.path) _switchLocal(s.path);
      else if (s.name) _openEntry(s);   // catalog workspace with no local path → spawn by name (new tab)
      else alert("Nothing to switch to — pick a repo/branch with a build or workspace.");
    });
    actions.appendChild(switchHereBtn);

    var openBtn = _el("button", "viv-bs-action", "Open in new tab"); openBtn.id = "viv-bs-open";
    openBtn.title = "Open the selected source in a NEW browser tab, leaving this tab unchanged "
      + "(each tab stays bound to its own source).";
    openBtn.addEventListener("click", function () {
      var s = state.selected || {};
      // build → /?build=, catalog workspace → /?workspace=, path-only → in-place fallback.
      if (s.path || s.name || s.simulator_id != null) _openEntry(s);
      else alert("Nothing to open — pick a repo/branch with a build or workspace.");
    });
    actions.appendChild(openBtn);

    // Commit + Push moved to the GitHub card below (that card owns git sync;
    // this card owns "where the tab runs"). See index.html.j2 #viv-git-actions.

    var buildBtn = _el("button", "viv-bs-action", "Build via sms-api"); buildBtn.id = "viv-bs-build";
    // repo_url comes from the REPO (any entry for it carries the same repo_url),
    // not from state.selected specifically — that field only exists for branches
    // that already have a build, which is exactly the gap item 67 fixes: this
    // button IS the "register a new branch's build" action, so it must not
    // require the branch to already have a build to become enabled.
    var repoUrlForBuild = (inRepo[0] || {}).repo_url || "";
    var effectiveBranch = state.branch === NEW_BRANCH_SENTINEL ? (state.newBranch || "").trim() : state.branch;
    buildBtn.disabled = !(state.scope === "remote" && repoUrlForBuild && effectiveBranch);
    buildBtn.title = buildBtn.disabled
      ? "Select a Remote repo and branch (or type a new branch name) to register a build"
      : "Register this repo@branch’s current HEAD as a build on sms-api (GovCloud) so it can "
        + "be run remotely. Resolves the live HEAD server-side; no local checkout needed.";
    buildBtn.addEventListener("click", function () {
      var repo = repoUrlForBuild, branch = effectiveBranch;
      if (!repo || !branch) { alert("Pick a repo and branch first"); return; }
      buildBtn.disabled = true; buildBtn.textContent = "Registering…";
      fetch("/api/source/build-remote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo: repo, branch: branch }),
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          buildBtn.disabled = false; buildBtn.textContent = "Build via sms-api";
          if (res.ok) {
            alert("Registered build #" + res.d.simulator_id + " @ " + (res.d.commit || "").slice(0, 7));
            state.branch = res.d.branch || branch; state.newBranch = ""; state.scope = "remote"; refresh();
          }
          else alert("Build failed: " + (res.d.error || "error"));
        })
        .catch(function () {
          buildBtn.disabled = false; buildBtn.textContent = "Build via sms-api";
          alert("Build failed: network error");
        });
    });
    actions.appendChild(buildBtn);

    var syncBtn = _el("button", "viv-bs-action", "Reproduce locally"); syncBtn.id = "viv-bs-sync";
    syncBtn.title = "Get a copy of this exact source on YOUR machine: pops a "
      + "‘vivarium-workbench sync <url>’ command that materializes this repo@commit workspace "
      + "(pinned by its uv.lock) locally. Copy-and-run it in a terminal — it does not change this tab.";
    syncBtn.addEventListener("click", function () {
      fetch("/api/source/manifest").then(function (r) { return r.json(); }).then(function (m) {
        var base = window.location.origin;
        var cmd = "vivarium-workbench sync " + base;
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
      // Newest-first (remote builds carry a monotonic simulator_id), with the
      // active source floated to the very top, then any build of the commit the
      // workspace is on. sms-api accumulates a build per register/upload and has
      // no delete, so this history gets long — hence the sort + collapse below.
      rows = rows.slice().sort(function (a, b) {
        var ac = a.current ? 2 : (a.matchesWorkspace ? 1 : 0);
        var bc = b.current ? 2 : (b.matchesWorkspace ? 1 : 0);
        if (ac !== bc) return bc - ac;
        return (Number(b.simulator_id) || 0) - (Number(a.simulator_id) || 0);
      });
      // The single newest build (highest id) — tagged "latest" so it's obvious
      // which row is the branch HEAD without reading build numbers.
      var latestId = rows.reduce(function (mx, r) {
        var v = Number(r.simulator_id) || 0; return v > mx ? v : mx;
      }, 0);
      var total = rows.length;
      // Collapse a long history to the recent N — but never while filtering (a
      // search should reach every match), and not once "show all" is expanded.
      var truncated = !f && !state.showAllBuilds && total > BUILD_LIST_LIMIT;
      var shown = truncated ? rows.slice(0, BUILD_LIST_LIMIT) : rows;
      shown.forEach(function (m) {
        var isRemote = m.simulator_id != null;
        var li = _el("li", "viv-bs-list-row" + (m.current ? " current" : ""));
        li.style.cssText = "display:flex; align-items:center; gap:10px; padding:6px 2px";

        // Label — a bold primary line (repo@sha, or the workspace label) over a
        // muted meta line (branch · date · build#, or the path), so each source
        // in a long history is legible at a glance instead of one dense string.
        var labelWrap = _el("div", "viv-bs-list-label");
        labelWrap.style.cssText = "flex:1 1 auto; min-width:0";
        var primary = isRemote ? (m.repo + " @ " + _short(m.commit)) : (m.label || m.name || "workspace");
        var pEl = _el("div", "viv-bs-row-primary");
        pEl.style.cssText = "display:flex; align-items:center; gap:8px; min-width:0";
        var pText = _el("span", null, primary);
        pText.style.cssText = "font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis";
        pEl.appendChild(pText);
        // Status chip — which source the workspace is on, and which is newest.
        var chip = null;
        if (m.current || m.matchesWorkspace) {
          chip = _el("span", null, m.current ? "in workspace ✓" : "workspace commit");
          chip.style.cssText = "flex:0 0 auto; font-size:10px; font-weight:600; color:#1f7a44; "
            + "background:#e7f6ec; border:1px solid #b7e2c6; border-radius:10px; padding:1px 7px";
          chip.title = m.current
            ? "This build is the source loaded in this tab"
            : "A build of the commit your workspace is currently on";
        } else if (isRemote && m.simulator_id === latestId) {
          chip = _el("span", null, "latest");
          chip.style.cssText = "flex:0 0 auto; font-size:10px; font-weight:600; color:#2f57b5; "
            + "background:#eef3fd; border:1px solid #b7c6ea; border-radius:10px; padding:1px 7px";
          chip.title = "Newest build of " + (m.branch || "this branch");
        }
        if (chip) pEl.appendChild(chip);
        labelWrap.appendChild(pEl);
        var metaBits = [];
        if (isRemote) {
          if (m.branch) metaBits.push(m.branch);
          if (m.created_at) metaBits.push(String(m.created_at).slice(0, 10));
          metaBits.push("build #" + m.simulator_id);
        } else if (m.path) {
          metaBits.push(m.path);
        }
        if (metaBits.length) {
          var mEl = _el("div", "viv-bs-row-meta", metaBits.join("  ·  "));
          mEl.style.cssText = "font-size:11px; color:#93a1b5; white-space:nowrap; overflow:hidden; text-overflow:ellipsis";
          labelWrap.appendChild(mEl);
        }
        li.appendChild(labelWrap);

        // Per-row actions — the SAME two verbs as the top bar, scoped to THIS
        // row: "Switch here" re-points this tab in place; "Open ↗" spawns a new
        // tab. Every source in the history is thus directly actionable without
        // re-selecting it in the pickers above. The current source shows a badge
        // instead of Switch (you're already on it).
        var acts = _el("div", "viv-bs-row-actions");
        acts.style.cssText = "flex:0 0 auto; display:flex; align-items:center; gap:6px";
        if (m.current) {
          acts.appendChild(_rowTag("current ✓"));
        } else {
          var sw = _rowBtn("Switch here", "Switch THIS tab to this source in place"
            + (isRemote ? " — downloads its workspace the first time (cached builds are instant)" : ""),
            "primary");
          sw.addEventListener("click", function (e) {
            e.stopPropagation();
            if (isRemote) _switchRemote(m.simulator_id, sw);
            else if (m.path) _switchLocal(m.path);
            else if (m.name) _openEntry(m);   // catalog workspace, no local path → new tab
          });
          acts.appendChild(sw);
        }
        var op = _rowBtn("Open ↗", "Open this source in a NEW tab, leaving this one unchanged");
        op.addEventListener("click", function (e) { e.stopPropagation(); _openEntry(m); });
        acts.appendChild(op);
        if (state.scope === "local" && !m.current && m.path) {
          var x = _el("button", "viv-bs-forget", "✕"); x.title = "Forget this workspace";
          x.addEventListener("click", function (e) { e.stopPropagation(); _forget(m.path, li); });
          acts.appendChild(x);
        }
        li.appendChild(acts);
        list.appendChild(li);
      });
      // "no matches" only while actively filtering — never as idle noise when
      // there's simply nothing else to switch to.
      if (!shown.length && f) list.appendChild(_el("li", "viv-bs-list-empty", "no matches"));
      if (truncated) {
        var more = _el("li", "viv-bs-list-more");
        var moreBtn = _el("button", "viv-bs-toggle", "Show all " + total + " builds  ▾");
        moreBtn.title = "Showing the " + BUILD_LIST_LIMIT + " most recent — click to list every build";
        moreBtn.addEventListener("click", function () { state.showAllBuilds = true; _fillList(); });
        more.appendChild(moreBtn);
        list.appendChild(more);
      }
    }
    if (search) search.addEventListener("input", function () { state.filter = search.value; _fillList(); });
    _fillList();
  }

  function _selectRow(key, id, options, value, onChange, labelFn) {
    var row = _el("div", "viv-bs-row");
    row.appendChild(_el("label", "viv-bs-key", key));
    var sel = _el("select", "viv-bs-select"); sel.id = id;
    options.forEach(function (o) {
      var opt = _el("option", null, labelFn ? labelFn(o) : (o || "—")); opt.value = o;
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
  // The reproducibility card for THIS published workspace: GitHub repo link,
  // commit sha (linked to the commit page), branch, pinned environment (uv.lock
  // hash), build time, and the one-liner to clone + reproduce it locally. Data
  // comes from __DASH_CONFIG__.provenance, injected by publish.py. No live
  // backend needed — this is the whole point of surfacing it in read-only mode.
  function _renderProvenance(host) {
    var p = (window.__DASH_CONFIG__ || {}).provenance;
    if (!p || (!p.repo_url && !p.commit)) return;
    var card = _el("div", "viv-bs-provenance");

    if (p.repo_url) {
      var repoRow = _el("div", "viv-bs-row");
      repoRow.appendChild(_el("label", "viv-bs-key", "Repository"));
      var a = _el("a", "viv-bs-prov-repo");
      a.href = p.repo_url; a.target = "_blank"; a.rel = "noopener";
      a.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><use href="#viv-gh-mark"/></svg>';
      a.appendChild(document.createTextNode(p.repo_slug || p.repo_url));
      repoRow.appendChild(a);
      card.appendChild(repoRow);
    }
    if (p.branch) {
      var brRow = _el("div", "viv-bs-row");
      brRow.appendChild(_el("label", "viv-bs-key", "Branch"));
      brRow.appendChild(_el("span", "viv-bs-commit", p.branch));
      card.appendChild(brRow);
    }
    if (p.commit) {
      var cRow = _el("div", "viv-bs-row");
      cRow.appendChild(_el("label", "viv-bs-key", "Commit"));
      var cVal;
      if (p.commit_url) {
        cVal = _el("a", "viv-bs-commit"); cVal.href = p.commit_url;
        cVal.target = "_blank"; cVal.rel = "noopener";
        cVal.title = "View this commit on GitHub";
      } else {
        cVal = _el("span", "viv-bs-commit");
      }
      cVal.textContent = _short(p.commit);
      cRow.appendChild(cVal);
      card.appendChild(cRow);
    }
    if (p.lockfile) {
      var eRow = _el("div", "viv-bs-row");
      eRow.appendChild(_el("label", "viv-bs-key", "Environment"));
      var ev = _el("span", "viv-bs-prov-env", p.lockfile);
      ev.title = "Locked dependency set — reproduce with `uv sync` against this uv.lock";
      eRow.appendChild(ev);
      card.appendChild(eRow);
    }
    if (p.generated_at) {
      var gRow = _el("div", "viv-bs-row");
      gRow.appendChild(_el("label", "viv-bs-key", "Published"));
      gRow.appendChild(_el("span", "viv-bs-prov-env", p.generated_at));
      card.appendChild(gRow);
    }

    // Reproduce-locally one-liner (clone this exact repo@commit + verify lock).
    var repro = _el("div", "viv-bs-prov-repro");
    var dir = (window.location.origin + window.location.pathname).replace(/[^/]*$/, "");
    var cmd = "vivarium-workbench sync " + dir.replace(/\/$/, "");
    var lead = _el("div", "viv-bs-key");
    lead.style.width = "auto";
    lead.textContent = "Reproduce this workspace locally:";
    repro.appendChild(lead);
    var code = _el("code", "viv-bs-prov-cmd", cmd);
    code.title = "Click to select · clones this repo@commit and verifies " + (p.lockfile || "uv.lock");
    repro.appendChild(code);
    // Spell out exactly which commit + environment `sync` reproduces, so the
    // reader knows what they'll land without decoding the manifest behind the URL.
    if (p.commit || p.lockfile) {
      var pin = _el("div", "viv-bs-prov-pin");
      var bits = [];
      if (p.repo_slug || p.commit) bits.push("→ " + (p.repo_slug || "repo") + "@" + _short(p.commit || ""));
      if (p.lockfile) bits.push(p.lockfile);
      pin.textContent = bits.join(" · ");
      pin.title = "sync resolves the manifest at this URL, which pins this commit + locked environment";
      repro.appendChild(pin);
    }
    card.appendChild(repro);

    host.appendChild(card);
  }

  function _renderSnapshot() {
    var host = document.getElementById("viv-branch-source");
    if (!host) return;
    host.innerHTML = "";
    host.appendChild(_el("h3", "viv-bs-title", "Source"));

    _renderProvenance(host);

    var entries = state.entries || [];
    var names = entries.map(function (e) { return e.repo; });
    if (!state.selected || names.indexOf(state.selected.repo) < 0) {
      state.selected = state.current || entries[0] || null;
    }
    if (!entries.length) {
      // The provenance card above is the primary content; only add the
      // sibling-workspaces note when there genuinely could have been more.
      host.appendChild(_el("p", "viv-bs-note viv-bs-note-siblings",
        "No other published workspaces linked from this one."));
      return;
    }
    host.appendChild(_el("div", "viv-bs-prov-head", "Other published workspaces"));
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
      var cmd = "vivarium-workbench sync " + dir.replace(/\/$/, "");
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
    // The workspace's checked-out commit — lets us flag which remote build(s)
    // correspond to what's live in the workspace even when it's a LOCAL checkout
    // (no materialized-build id to match on). Short-sha compared, since builds
    // and git-status report shas at differing lengths. `current` carries only
    // {name, path}; the commit lives on the matching workspaces-list entry.
    var curEntry = (wsData.workspaces || []).filter(function (w) { return w.path === curPath; })[0] || null;
    state.currentCommit = (curEntry && curEntry.commit) ? String(curEntry.commit)
                        : ((cur && cur.commit) ? String(cur.commit) : null);
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
