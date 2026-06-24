# Source & Branch Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relocate the dashboard's source switcher out of the cramped rail dropdown into an organized `local/remote · repo · branch · commit` panel in the Branch tab, and add commit+push and build-via-sms-api actions.

**Architecture:** A new isolated front-end module (`static/branch-source.js`) renders the panel into the existing `#page-github` section; it drives the existing switch endpoints plus two new ones (`/api/branch/push`, `/api/source/build-remote`). The rail chip becomes a read-only display. Backend reuses the existing `_remote_push_and_sha()` push helper and `SmsApiClient`.

**Tech Stack:** Python stdlib `http.server` (server.py), vanilla JS (no build step), pydantic only where already used; tests are pytest (string-presence for JS, per repo convention).

## Global Constraints

- Worktree `/Users/eranagmon/code/vdash-sp3`, branch `feat/commit-agnostic-remote-builds`.
- Run tests with the worktree venv directly: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest <files> -q` (NOT `uv run` — a stray `VIRTUAL_ENV` hijacks it).
- JS is vanilla, served raw (no bundler); JS is tested by string-presence in a python test file, the repo convention (see `tests/test_remote_build_source.py`).
- The rail chip (`#viv-source-switch-trigger`) must end as a PLAIN DISPLAY: no `role="button"`, `tabindex`, caret, hover, or cursor.
- Push is LOCAL-ONLY and must never force-push; a non-git active source (materialized remote build) → HTTP 409.
- Build-via-sms-api registers the branch HEAD only (no arbitrary commit), does NOT wait on the async AWS Batch image build, and degrades to 502-with-reason when sms-api is unreachable.
- `node --check <file.js>` must pass for any changed JS.

---

### Task 1: `/api/workspaces` rows carry repo / branch / commit

**Files:**
- Modify: `vivarium_dashboard/server.py` (the `_get_workspaces` method ~16223 and add a module-level helper)
- Test: `tests/test_source_branch.py` (new)

**Interfaces:**
- Produces: module-level `_git_branch_commit(path: str) -> tuple[str, str]` returning `(branch, short_commit)` (`("","")` when unresolvable); each `/api/workspaces` row gains `"repo": str`, `"branch": str`, `"commit": str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_source_branch.py
import subprocess
from pathlib import Path
from vivarium_dashboard import server


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_git_branch_commit_resolves(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "checkout", "-q", "-b", "feat/x")
    _git(tmp_path, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "m")
    branch, commit = server._git_branch_commit(str(tmp_path))
    assert branch == "feat/x"
    assert len(commit) >= 4 and commit.isalnum()


def test_git_branch_commit_non_git(tmp_path):
    assert server._git_branch_commit(str(tmp_path)) == ("", "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py -q`
Expected: FAIL — `module 'vivarium_dashboard.server' has no attribute '_git_branch_commit'`.

- [ ] **Step 3: Add the module-level helper**

Add directly above the `_branch_label` nested function is not possible (it's nested); instead add this module-level function just above the `Handler` class definition (search for `class Handler(BaseHTTPRequestHandler):`):

```python
def _git_branch_commit(path: str) -> tuple[str, str]:
    """(branch, short_commit) for a git workspace; ('', '') when unresolvable."""

    def _run(args: list[str]) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", path, *args], capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    return _run(["rev-parse", "--abbrev-ref", "HEAD"]), _run(["rev-parse", "--short", "HEAD"])
```

- [ ] **Step 4: Use it in `_get_workspaces` and DRY the label**

In `_get_workspaces`, replace the existing `_branch_label` nested function with one that takes a precomputed branch (no second git call):

```python
        def _branch_label(name: str, branch: str, path: str) -> str:
            """Disambiguate same-repo worktrees by branch/leaf."""
            variant = branch if branch and branch not in ("main", "master", "HEAD") else None
            if variant is None:
                leaf = Path(path).name
                if leaf and leaf != name:
                    variant = leaf
            return f"{name}:{variant}" if variant else name
```

Then in the per-entry loop, find:

```python
            name = entry.get("name") or Path(path).name
            row = {"name": name, "path": path}
            row["label"] = _branch_label(name, path) if Path(path).is_dir() else name
```

and replace with:

```python
            name = entry.get("name") or Path(path).name
            row = {"name": name, "path": path}
            branch, commit = _git_branch_commit(path) if Path(path).is_dir() else ("", "")
            row["repo"] = name
            row["branch"] = branch
            row["commit"] = commit
            row["label"] = _branch_label(name, branch, path) if Path(path).is_dir() else name
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_source_branch.py
git commit -m "feat(workspaces): add repo/branch/commit fields to /api/workspaces rows"
```

---

### Task 2: `branch-source.js` Source panel in the Branch tab

**Files:**
- Create: `vivarium_dashboard/static/branch-source.js`
- Modify: `vivarium_dashboard/templates/index.html.j2` (add a container at the top of `#page-github` ~line 945; add the script include near the other rail scripts ~line 1653)
- Test: `tests/test_source_branch.py`

**Interfaces:**
- Consumes: `GET /api/workspaces` (rows with `repo`/`branch`/`commit`/`label`/`status`/`path`), `GET /api/source/builds` (`builds[]` with `simulator_id`/`repo`/`branch`/`commit`/`label`), `POST /api/source/switch`, `POST /api/source/switch-build`, `POST /api/workspaces/forget`.
- Produces: a panel mounted into `#viv-branch-source`; global `window._renderBranchSource()`. Buttons with ids `viv-bs-switch`, `viv-bs-push`, `viv-bs-build` (push/build wired in Tasks 4/5).

- [ ] **Step 1: Write the failing test**

```python
def test_branch_source_js_present_and_wired():
    from pathlib import Path
    from vivarium_dashboard import server
    js = (Path(server.__file__).parent / "static" / "branch-source.js").read_text()
    for needle in ("/api/workspaces", "/api/source/builds", "/api/source/switch",
                   "/api/source/switch-build", "/api/workspaces/forget",
                   "viv-bs-switch", "Local", "Remote"):
        assert needle in js, needle


def test_branch_source_mounted_in_github_page():
    from pathlib import Path
    from vivarium_dashboard import server
    tpl = (Path(server.__file__).parent / "templates" / "index.html.j2").read_text()
    assert 'id="viv-branch-source"' in tpl
    assert "assets/branch-source.js" in tpl
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py -q`
Expected: FAIL — `branch-source.js` does not exist / FileNotFoundError.

- [ ] **Step 3: Create `vivarium_dashboard/static/branch-source.js`**

```javascript
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
    actions.appendChild(pushBtn);   // wired in Task 4

    var buildBtn = _el("button", "viv-bs-action", "Build via sms-api"); buildBtn.id = "viv-bs-build";
    actions.appendChild(buildBtn);  // wired in Task 5

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
```

- [ ] **Step 4: Add the container + script include to the template**

In `vivarium_dashboard/templates/index.html.j2`, find the Branch page section opener (~line 945):

```html
<section id="page-github" class="page" data-page="github">
```

and insert immediately after it:

```html
  <div id="viv-branch-source" class="viv-branch-source"></div>
```

Then near the other rail scripts (search for `<script src="assets/source-switch.js`), add on the next line:

```html
<script src="assets/branch-source.js{% if asset_version %}?v={{ asset_version }}{% endif %}"></script>
```

- [ ] **Step 5: Add minimal CSS**

In the same template, find `.viv-iset-menu-row-title { flex: 1; font-size: 13px; color: #111827; }` and add after it:

```css
.viv-branch-source { padding: 4px 0 16px; max-width: 640px; }
.viv-bs-title { font-size: 15px; margin: 0 0 12px; }
.viv-bs-row { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
.viv-bs-key { width: 64px; color: #6b7280; font-size: 13px; }
.viv-bs-select, .viv-bs-commit-select { padding: 4px 8px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 13px; }
.viv-bs-toggle { padding: 4px 12px; border: 1px solid #d0d7de; background: #fff; cursor: pointer; border-radius: 6px; font-size: 13px; }
.viv-bs-toggle.active { background: #eef4ff; border-color: #94b4ff; color: #1e40af; }
.viv-bs-commit { font-family: ui-monospace, monospace; font-size: 13px; }
.viv-bs-current { color: #1e40af; font-weight: 600; font-size: 12px; }
.viv-bs-actions { display: flex; gap: 8px; margin: 14px 0; }
.viv-bs-action { padding: 6px 14px; border: 1px solid #d0d7de; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.viv-bs-action:disabled { opacity: 0.45; cursor: default; }
.viv-bs-note { color: #b45309; font-size: 12px; margin: 6px 0; }
.viv-bs-list { list-style: none; margin: 10px 0 0; padding: 0; border-top: 1px solid #f3f4f6; }
.viv-bs-list-row { display: flex; align-items: center; gap: 8px; padding: 6px 4px; border-bottom: 1px solid #f3f4f6; font-size: 13px; }
.viv-bs-list-row.current { background: #eef4ff; }
.viv-bs-list-label { flex: 1; }
.viv-bs-forget { border: none; background: none; cursor: pointer; color: #c0c4cc; }
.viv-bs-forget:hover { color: #d1242f; }
```

- [ ] **Step 6: Verify JS syntax + tests pass**

Run: `node --check vivarium_dashboard/static/branch-source.js && echo OK`
Expected: `source-switch.js syntax OK`-style `OK`.
Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py -q`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/static/branch-source.js vivarium_dashboard/templates/index.html.j2 tests/test_source_branch.py
git commit -m "feat(branch-tab): Source panel (local/remote · repo · branch · commit + switch + forget)"
```

---

### Task 3: Rail chip → plain display; drop `source-switch.js`

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2` (the `#viv-source-switch-trigger` chip ~line 373; the `.viv-workspace-name` CSS ~line 144; remove the `source-switch.js` script include ~line 1653)
- Test: `tests/test_source_branch.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: chip with NO `role="button"`; `source-switch.js` not loaded.

- [ ] **Step 1: Write the failing test**

```python
def test_chip_is_display_only_and_no_source_switch_js():
    from pathlib import Path
    from vivarium_dashboard import server
    tpl = (Path(server.__file__).parent / "templates" / "index.html.j2").read_text()
    # The chip block keeps the source label but is no longer a button/dropdown trigger.
    assert "assets/source-switch.js" not in tpl
    assert 'id="viv-source-switch-trigger"' not in tpl
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py::test_chip_is_display_only_and_no_source_switch_js -q`
Expected: FAIL — both strings currently present.

- [ ] **Step 3: Revert the chip to a plain display**

Find:

```html
      <div class="viv-workspace-name" id="viv-source-switch-trigger" role="button"
           tabindex="0" aria-haspopup="menu" title="Switch workspace or build source">
        <span class="viv-workspace-switcher-glyph">●</span>
        <strong>{{ workspace_name }}</strong>
        {% if repo_url %}
        <a class="viv-ws-repo-link" href="{{ repo_url }}" target="_blank" rel="noopener"
           title="Open {{ workspace_name }} on GitHub" aria-label="Open on GitHub">
          <svg class="viv-ws-gh-mark" width="12" height="12"><use href="#viv-gh-mark"/></svg>
        </a>
        {% endif %}
        <span class="viv-ws-switch-caret" aria-hidden="true">▾</span>
      </div>
```

Replace with:

```html
      <div class="viv-workspace-name" title="Current source (manage in the Branch tab)">
        <span class="viv-workspace-switcher-glyph">●</span>
        <strong>{{ workspace_name }}</strong>
        {% if repo_url %}
        <a class="viv-ws-repo-link" href="{{ repo_url }}" target="_blank" rel="noopener"
           title="Open {{ workspace_name }} on GitHub" aria-label="Open on GitHub">
          <svg class="viv-ws-gh-mark" width="12" height="12"><use href="#viv-gh-mark"/></svg>
        </a>
        {% endif %}
      </div>
```

- [ ] **Step 4: Restore the non-interactive CSS + drop the script**

Find the `.viv-workspace-name` block (it was made interactive earlier) and replace:

```css
.viv-workspace-name {
  display: flex; align-items: center; gap: 7px;
  padding: 6px 16px 6px; margin: 4px 8px 8px;
  font-size: 12.5px; font-weight: 600;
  color: #64748b; cursor: pointer; user-select: none;
  background: none; border: 1px solid transparent; border-radius: 6px;
}
.viv-workspace-name:hover { background: #f6f8fa; border-color: #e5e7eb; }
.viv-workspace-name strong { font-weight: 600; }
.viv-ws-switch-caret { margin-left: auto; color: #9ca3af; font-size: 10px; }
.viv-workspace-name:hover .viv-ws-switch-caret { color: #6b7280; }
```

with:

```css
.viv-workspace-name {
  display: flex; align-items: center; gap: 7px;
  padding: 0 16px 10px; margin: 0;
  font-size: 12.5px; font-weight: 600;
  color: #64748b; cursor: default;
  background: none; border: none; border-radius: 0;
}
.viv-workspace-name strong { font-weight: 600; }
```

Then delete the line:

```html
<script src="assets/source-switch.js{% if asset_version %}?v={{ asset_version }}{% endif %}"></script>
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py -q`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/index.html.j2 tests/test_source_branch.py
git commit -m "feat(rail): workspace chip is a plain source display; switching moves to the Branch tab"
```

---

### Task 4: `POST /api/branch/push` (commit + push) + Push button

**Files:**
- Modify: `vivarium_dashboard/server.py` (add `_remote_commit_and_push` near `_remote_push_and_sha` ~6567; add handler `_post_branch_push` near other POST handlers; register route in `_POST_ROUTE_MAP` ~383)
- Modify: `vivarium_dashboard/static/branch-source.js` (wire `#viv-bs-push`)
- Test: `tests/test_source_branch.py`

**Interfaces:**
- Consumes: existing `_remote_push_and_sha()` (pushes current branch with GH token, returns SHA).
- Produces: `POST /api/branch/push {message}` → `{ok, branch, pushed, commit}` (200), `{error}` 409 when WORKSPACE is not a git repo, `{error}` 500 on git/gh failure.

- [ ] **Step 1: Write the failing test**

```python
def test_branch_push_commits_and_pushes(tmp_path, monkeypatch):
    from vivarium_dashboard import server
    # bare remote + working clone on a named branch with a dirty tree
    bare = tmp_path / "remote.git"; _git(tmp_path, "init", "-q", "--bare", str(bare))
    ws = tmp_path / "ws"; ws.mkdir()
    _git(ws, "init", "-q"); _git(ws, "checkout", "-q", "-b", "feat/x")
    _git(ws, "remote", "add", "origin", str(bare))
    _git(ws, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    (ws / "f.txt").write_text("hi")
    monkeypatch.setattr(server, "WORKSPACE", ws)
    captured = {}

    class H:
        def _json(self, obj, code): captured.update(obj=obj, code=code)

    server.Handler._post_branch_push(H(), {"message": "add f"})
    assert captured["code"] == 200 and captured["obj"]["pushed"] is True
    log = subprocess.run(["git", "-C", str(ws), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "add f" in log


def test_branch_push_non_git_409(tmp_path, monkeypatch):
    from vivarium_dashboard import server
    monkeypatch.setattr(server, "WORKSPACE", tmp_path)  # not a git repo
    captured = {}

    class H:
        def _json(self, obj, code): captured.update(obj=obj, code=code)

    server.Handler._post_branch_push(H(), {"message": "x"})
    assert captured["code"] == 409
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py -q`
Expected: FAIL — `_post_branch_push` not defined.

- [ ] **Step 3: Add the commit+push helper (module-level, near `_remote_push_and_sha`)**

```python
def _remote_commit_and_push(message: str) -> dict:
    """Stage+commit WORKSPACE changes (skip if clean), push current branch, return result."""
    inside = subprocess.run(
        ["git", "-C", str(WORKSPACE), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise _NotAGitRepo("active source is not a git workspace (no commit/push)")
    subprocess.run(["git", "-C", str(WORKSPACE), "add", "-A"], capture_output=True, text=True, timeout=30)
    status = subprocess.run(
        ["git", "-C", str(WORKSPACE), "status", "--porcelain"], capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    if status:
        c = subprocess.run(
            ["git", "-C", str(WORKSPACE), "commit", "-m", message or "dashboard commit"],
            capture_output=True, text=True, timeout=30,
        )
        if c.returncode != 0:
            raise RuntimeError(f"git commit failed: {(c.stderr or c.stdout)[-300:]}")
    sha = _remote_push_and_sha()
    return {"ok": True, "pushed": bool(status), "commit": sha,
            "branch": subprocess.run(["git", "-C", str(WORKSPACE), "rev-parse", "--abbrev-ref", "HEAD"],
                                     capture_output=True, text=True).stdout.strip()}


class _NotAGitRepo(RuntimeError):
    pass
```

(Place `class _NotAGitRepo(RuntimeError): pass` ABOVE `_remote_commit_and_push`.)

- [ ] **Step 4: Add the handler + register the route**

Add the handler method to the `Handler` class (near `_post_source_switch`):

```python
    def _post_branch_push(self, body: dict):
        """POST /api/branch/push — commit WORKSPACE changes + push current branch."""
        message = (body or {}).get("message") or "dashboard commit"
        try:
            return self._json(_remote_commit_and_push(message), 200)
        except _NotAGitRepo as e:
            return self._json({"error": str(e)}, 409)
        except Exception as e:
            return self._json({"error": str(e)}, 500)
```

In `_POST_ROUTE_MAP` (after `"/api/source/switch": "_post_source_switch",`), add:

```python
    "/api/branch/push":              "_post_branch_push",
```

- [ ] **Step 5: Wire the Push button in `branch-source.js`**

In `_render()`, replace the `pushBtn` block (the line `actions.appendChild(pushBtn);   // wired in Task 4`) — add a click handler before appending:

```javascript
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
        });
    });
    actions.appendChild(pushBtn);
```

- [ ] **Step 6: Run tests + JS syntax**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py -q`
Expected: PASS (7 passed).
Run: `node --check vivarium_dashboard/static/branch-source.js && echo OK`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/server.py vivarium_dashboard/static/branch-source.js tests/test_source_branch.py
git commit -m "feat(branch): POST /api/branch/push (commit + push) + Push action"
```

---

### Task 5: `POST /api/source/build-remote` (register via sms-api) + Build button

**Files:**
- Modify: `vivarium_dashboard/lib/sms_api_client.py` (add `register_simulator`)
- Modify: `vivarium_dashboard/server.py` (add handler `_post_source_build_remote`; register route)
- Modify: `vivarium_dashboard/static/branch-source.js` (wire `#viv-bs-build`)
- Test: `tests/test_source_branch.py`

**Interfaces:**
- Consumes: existing `SmsApiClient.latest_simulator(repo_url, branch)`, `SmsApiClient._post(...)`, `SmsApiError`, `_sms_api_base()`.
- Produces: `SmsApiClient.register_simulator(repo_url, branch, commit) -> dict`; `POST /api/source/build-remote {repo, branch}` → `{ok, simulator_id, repo, branch, commit}` (200) / `{error}` 502 on SmsApiError / `{error}` 400 on missing repo|branch.

- [ ] **Step 1: Write the failing test**

```python
def test_register_simulator_posts_upload(monkeypatch):
    from vivarium_dashboard.lib import sms_api_client as sac
    seen = {}
    monkeypatch.setattr(sac.SmsApiClient, "_post",
                        lambda self, path, params=None, json_body=None: seen.update(path=path, body=json_body) or {"database_id": 99})
    out = sac.SmsApiClient("http://x").register_simulator("https://github.com/o/r", "main", "abc1234")
    assert out["database_id"] == 99
    assert seen["path"] == "/core/v1/simulator/upload"
    assert seen["body"]["git_branch"] == "main" and seen["body"]["git_commit_hash"] == "abc1234"


def test_build_remote_endpoint(monkeypatch):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import sms_api_client as sac
    monkeypatch.setattr(sac.SmsApiClient, "latest_simulator",
                        lambda self, repo, branch: {"git_commit_hash": "deadbee"})
    monkeypatch.setattr(sac.SmsApiClient, "register_simulator",
                        lambda self, repo, branch, commit: {"database_id": 64, "git_commit_hash": commit})
    captured = {}

    class H:
        def _json(self, obj, code): captured.update(obj=obj, code=code)

    server.Handler._post_source_build_remote(H(), {"repo": "https://github.com/o/v2ecoli", "branch": "main"})
    assert captured["code"] == 200
    assert captured["obj"]["simulator_id"] == 64 and captured["obj"]["commit"] == "deadbee"


def test_build_remote_missing_args_400(monkeypatch):
    from vivarium_dashboard import server
    captured = {}

    class H:
        def _json(self, obj, code): captured.update(obj=obj, code=code)

    server.Handler._post_source_build_remote(H(), {"repo": ""})
    assert captured["code"] == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py -q`
Expected: FAIL — `register_simulator` / `_post_source_build_remote` not defined.

- [ ] **Step 3: Add `register_simulator` to the client**

In `vivarium_dashboard/lib/sms_api_client.py`, directly after `latest_simulator` (line ~41):

```python
    def register_simulator(self, repo_url: str, branch: str, commit: str) -> dict:
        """POST /core/v1/simulator/upload — register a repo@commit build (async image build)."""
        return self._post("/core/v1/simulator/upload", json_body={
            "git_repo_url": repo_url, "git_branch": branch, "git_commit_hash": commit,
        })
```

- [ ] **Step 4: Add the handler + register the route**

Add to the `Handler` class (near `_post_branch_push`):

```python
    def _post_source_build_remote(self, body: dict):
        """POST /api/source/build-remote — register a repo+branch's HEAD as an sms-api build."""
        from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError
        repo = (body or {}).get("repo") or ""
        branch = (body or {}).get("branch") or ""
        if not repo or not branch:
            return self._json({"error": "repo and branch are required"}, 400)
        client = SmsApiClient(_sms_api_base())
        try:
            latest = client.latest_simulator(repo, branch)
            commit = latest.get("git_commit_hash") or ""
            reg = client.register_simulator(repo, branch, commit)
        except SmsApiError as e:
            return self._json({"error": f"sms-api: {e}"}, 502)
        return self._json({"ok": True, "simulator_id": reg.get("database_id"),
                           "repo": repo, "branch": branch, "commit": commit}, 200)
```

In `_POST_ROUTE_MAP` (after `"/api/branch/push": "_post_branch_push",`), add:

```python
    "/api/source/build-remote":      "_post_source_build_remote",
```

- [ ] **Step 5: Wire the Build button in `branch-source.js`**

In `_render()`, replace the `buildBtn` block (`actions.appendChild(buildBtn);  // wired in Task 5`) with a handler before appending:

```javascript
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
```

Note: the Build button uses the selected **repo** (a full GitHub URL on the Remote scope; on Local scope the repo selector value is the catalog NAME, not a URL). Guard: only enable Build when `state.scope === "remote"` OR the repo value looks like a URL. Set, in the buildBtn creation line:

```javascript
    buildBtn.disabled = !(state.repo && String(state.repo).indexOf("http") === 0);
    buildBtn.title = buildBtn.disabled ? "Select a Remote source (full repo URL) to register a build" : "";
```

- [ ] **Step 6: Run tests + JS syntax**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_source_branch.py -q`
Expected: PASS (10 passed).
Run: `node --check vivarium_dashboard/static/branch-source.js && echo OK`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/lib/sms_api_client.py vivarium_dashboard/server.py vivarium_dashboard/static/branch-source.js tests/test_source_branch.py
git commit -m "feat(source): POST /api/source/build-remote + Build via sms-api action"
```

---

## Notes for the executor

- After all tasks, do a live smoke test: restart the dashboard (`unset VIRTUAL_ENV && .venv/bin/python -m vivarium_dashboard.cli serve --workspace /Users/eranagmon/code/v2ecoli --port 8780`), open the Branch tab, confirm the Source panel renders, Local/Remote toggles, Switch works; Push needs a local git workspace; Build needs the sms-api tunnel up.
- The Build action only applies to Remote sources (repo value is a GitHub URL). Local repo values are catalog names — Build is disabled there by design.
- Do NOT bump `asset_version` manually; it is stamped at server start, and the new `?v=` on `branch-source.js` busts the cache on restart.
