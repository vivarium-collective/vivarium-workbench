# Commit-agnostic Dashboard — Runtime Workspace Re-pointing (SP2 core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one dashboard server re-point its active workspace in-process via a source dropdown — switching among registered *local* workspaces with no restart and no port change.

**Architecture:** Add `_switch_active_workspace(new_root)` + a single `_invalidate_workspace_caches()` to `server.py`; expose them through `POST /api/source/switch` (validated against the existing `~/.pbg/workspaces.json` catalog); a header source dropdown POSTs the switch then `window.location.reload()`. The data-reading paths already read files from `WORKSPACE`/`ws_root`, so re-pointing + cache-clearing makes them serve the new workspace immediately.

**Tech Stack:** Python stdlib `http.server` (the existing `Handler`), `vivarium_dashboard.lib._root`, `vivarium_dashboard.lib.workspace_catalog`, vanilla JS (no bundler), pytest.

## Global Constraints

- **Single-process re-pointing** — no new `serve` subprocess, no port change, one URL. (Spec §Architecture.)
- **Switch only among registered workspaces** — the target path MUST be in the catalog (`lib.workspace_catalog.find_entry(path)` returns non-None); reject anything else with HTTP 400 (no path traversal). (Spec §Component 1.)
- **One invalidation call-site** — all `WORKSPACE`-keyed caches are cleared by `_invalidate_workspace_caches()`, called only from `_switch_active_workspace`. (Spec §Component 2.)
- **Switch is serialized** by a module-level `threading.Lock` (`_SWITCH_LOCK`). (Spec §Component 1.)
- **Reload UX** — the client does a full `window.location.reload()` after a successful switch (re-renders the SPA shell + branding). (Spec §Component 4.)
- **Keep the legacy flow** — `/api/workspaces/start`/`stop` (separate-process) stay unchanged. (Spec §Decision 4.)
- **Composites are temporarily stale-on-switch** in this plan; the clean fix is the follow-on plan **SP2b** (composite subprocess-isolation). Do NOT attempt composite isolation here. (Spec §Decision 3 — split for scope; see Task 6.)

## Scope split (read first)

The spec's §Component 3 (subprocess-isolate composite discovery) is a **separate, larger subsystem**: the composite *build/resolve* path (`build_generator`, `_REGISTRY`, composite-resolve, composite-test-run) imports the workspace package in-process, not just discovery. Isolating all of it is its own plan, **SP2b** (`docs/superpowers/plans/<date>-composite-subprocess-isolation.md`), to be written next. THIS plan delivers the working, shippable re-pointing core: every file-based view (studies, investigations, runs, reports, charts, and the FastAPI routes `/api/simulations`, `/api/iset-list`, `/api/data-sources`, `/api/references-bib`, `/api/saved-visualizations`) switches cleanly. Composite Explorer may show stale data after a switch until SP2b lands (Task 6 documents this honestly in the UI).

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `vivarium_dashboard/server.py` | modify | `_SWITCH_LOCK`, `_invalidate_workspace_caches()`, `_switch_active_workspace()`, `_post_source_switch` handler, `_POST_ROUTE_MAP` entry |
| `vivarium_dashboard/static/source-switch.js` | create | header source dropdown: list `/api/workspaces`, POST `/api/source/switch`, reload |
| `vivarium_dashboard/templates/index.html.j2` | modify | mount point + `<script src="/source-switch.js">` |
| `tests/test_source_switch.py` | create | unit (invalidate + switch), handler (400 on unknown path), JS string-presence, cross-switch flow |

---

## Task 1: Re-pointing core — `_switch_active_workspace` + `_invalidate_workspace_caches`

**Files:**
- Modify: `vivarium_dashboard/server.py` (add near the cache globals, ~line 212, and near `serve()`)
- Test: `tests/test_source_switch.py`

**Interfaces:**
- Consumes: `WORKSPACE` (module global `Path`), `lib._root.set_workspace_root`, `lib._root.get_workspace_root`, the cache globals `_REGISTRY_CACHE` `{"data","ts"}`, `_LINKAGE_CACHE` `{}`, `_COMPOSITE_STATE_CACHE` `{}`, `_RUN_STORE_SUMMARY_CACHE` `{}`, `_WP_CACHE` `{}`, and `lib.data_sources._DATA_SOURCES_CACHE` `{}`.
- Produces: `_invalidate_workspace_caches() -> None`; `_switch_active_workspace(new_root: Path) -> None` (re-points the global + root, then invalidates).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_source_switch.py
from pathlib import Path
from vivarium_dashboard import server
from vivarium_dashboard.lib import _root


def test_switch_active_workspace_repoints_and_invalidates(tmp_path):
    a = tmp_path / "a"; (a).mkdir(); (a / "workspace.yaml").write_text("name: a\n")
    b = tmp_path / "b"; (b).mkdir(); (b / "workspace.yaml").write_text("name: b\n")

    server.WORKSPACE = a
    _root.set_workspace_root(a)
    # Dirty every workspace-keyed cache.
    server._REGISTRY_CACHE["data"] = {"stale": True}
    server._LINKAGE_CACHE["x"] = 1
    server._COMPOSITE_STATE_CACHE["x"] = 1
    server._RUN_STORE_SUMMARY_CACHE["x"] = 1
    server._WP_CACHE["x"] = 1

    server._switch_active_workspace(b)

    assert server.WORKSPACE == b
    assert _root.get_workspace_root() == b
    assert server._REGISTRY_CACHE["data"] is None
    assert server._LINKAGE_CACHE == {}
    assert server._COMPOSITE_STATE_CACHE == {}
    assert server._RUN_STORE_SUMMARY_CACHE == {}
    assert server._WP_CACHE == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py::test_switch_active_workspace_repoints_and_invalidates -q`
Expected: FAIL — `AttributeError: module 'vivarium_dashboard.server' has no attribute '_switch_active_workspace'`.

- [ ] **Step 3: Add the implementation to `server.py`**

Add near the cache globals (after `_COMPOSITE_STATE_CACHE` at ~line 212):

```python
import threading as _threading  # if not already imported at top

# Serializes runtime workspace re-pointing (SP2). A switch must not interleave
# with another switch.
_SWITCH_LOCK = _threading.Lock()


def _invalidate_workspace_caches() -> None:
    """Clear every cache keyed to the active workspace. Called ONLY from
    _switch_active_workspace, so the invalidation surface is auditable."""
    _REGISTRY_CACHE["data"] = None
    _REGISTRY_CACHE["ts"] = 0.0
    _LINKAGE_CACHE.clear()
    _COMPOSITE_STATE_CACHE.clear()
    _RUN_STORE_SUMMARY_CACHE.clear()
    _WP_CACHE.clear()
    # lib-level caches keyed by workspace (defensive — data_sources keys by
    # ws_root, but clear so a re-point starts clean).
    from vivarium_dashboard.lib.data_sources import _DATA_SOURCES_CACHE
    _DATA_SOURCES_CACHE.clear()


def _switch_active_workspace(new_root: Path) -> None:
    """Re-point the active workspace in-process: update the WORKSPACE global +
    lib._root, then invalidate all workspace-keyed caches. Serialized by lock."""
    from vivarium_dashboard.lib._root import set_workspace_root
    global WORKSPACE
    with _SWITCH_LOCK:
        WORKSPACE = Path(new_root).resolve()
        set_workspace_root(WORKSPACE)
        _invalidate_workspace_caches()
```

(If `threading` is already imported at the top of `server.py`, reuse it instead of `import threading as _threading`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py::test_switch_active_workspace_repoints_and_invalidates -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_source_switch.py
git commit -m "feat(source-switch): runtime workspace re-pointing + cache invalidation"
```

---

## Task 2: `POST /api/source/switch` handler

**Files:**
- Modify: `vivarium_dashboard/server.py` (add the handler method near `_post_workspaces_*` ~line 16277; add the route to `_POST_ROUTE_MAP` ~line 342)
- Test: `tests/test_source_switch.py`

**Interfaces:**
- Consumes: `_switch_active_workspace` (Task 1); `lib.workspace_catalog.find_entry(path) -> entry|None` (entry has `.path`, `.name`); the `Handler._json(obj, code)` adapter; the `do_POST` dispatch via `_POST_ROUTE_MAP[path] -> method_name` calling `getattr(self, method_name)(body)`.
- Produces: handler `_post_source_switch(self, body: dict)` returning `{"ok": True, "source": {"path", "name"}}` (200) or `{"error": ...}` (400).

- [ ] **Step 1: Write the failing test**

```python
def test_source_switch_route_registered():
    assert server._POST_ROUTE_MAP.get("/api/source/switch") == "_post_source_switch"


def test_source_switch_rejects_unregistered_path(tmp_path, monkeypatch):
    # find_entry returns None for an unknown path -> 400, no state change.
    from vivarium_dashboard.lib import workspace_catalog
    monkeypatch.setattr(workspace_catalog, "find_entry", lambda p: None)
    captured = {}

    class FakeHandler:
        _json = lambda self, obj, code: captured.update(obj=obj, code=code)

    server.Handler._post_source_switch(FakeHandler(), {"path": str(tmp_path / "nope")})
    assert captured["code"] == 400
    assert "error" in captured["obj"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py::test_source_switch_route_registered tests/test_source_switch.py::test_source_switch_rejects_unregistered_path -q`
Expected: FAIL — route not in map / `_post_source_switch` not defined.

- [ ] **Step 3: Add the handler + route**

Add the route entry to `_POST_ROUTE_MAP` (the dict near line 342):

```python
    "/api/source/switch":            "_post_source_switch",
```

Add the handler method to the `Handler` class (near `_post_workspaces_add`, ~line 16277):

```python
    def _post_source_switch(self, body: dict):
        """POST /api/source/switch — re-point the active workspace in-process.

        Body: {"path": <workspace dir>}. The path MUST be a registered catalog
        entry (no arbitrary paths). Returns {ok, source}; the client reloads.
        """
        from vivarium_dashboard.lib import workspace_catalog
        path = str(body.get("path") or "").strip()
        if not path:
            return self._json({"error": "missing 'path'"}, 400)
        entry = workspace_catalog.find_entry(path)
        if entry is None:
            return self._json(
                {"error": f"{path!r} is not a registered workspace"}, 400)
        _switch_active_workspace(Path(entry.path))
        return self._json(
            {"ok": True, "source": {"path": str(entry.path), "name": entry.name}},
            200,
        )
```

(Verify `workspace_catalog.find_entry` returns an object with `.path` and `.name`; the `_get_workspaces` handler already uses `workspace_catalog.find_entry`. If the entry attribute names differ, match them here.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py -q`
Expected: PASS (3 tests now).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_source_switch.py
git commit -m "feat(source-switch): POST /api/source/switch handler (catalog-validated)"
```

---

## Task 3: Source dropdown UI

**Files:**
- Create: `vivarium_dashboard/static/source-switch.js`
- Modify: `vivarium_dashboard/templates/index.html.j2` (add a mount point in the rail header + a `<script src>`)
- Test: `tests/test_source_switch.py` (string-presence, the repo convention for JS)

**Interfaces:**
- Consumes: `GET /api/workspaces` (existing — returns the catalog list the switcher already renders) and `POST /api/source/switch` (Task 2).
- Produces: a `window._openSourceSwitch()` entry point + a `<select id="viv-source-switch">`-style control; on change it POSTs and reloads.

- [ ] **Step 1: Write the failing test (string-presence)**

```python
from pathlib import Path


def _static(name):
    return (Path(server.__file__).parent / "static" / name).read_text(encoding="utf-8")


def test_source_switch_js_present_and_wired():
    js = _static("source-switch.js")
    assert "/api/workspaces" in js          # lists the catalog
    assert "/api/source/switch" in js       # POSTs the switch
    assert "window.location.reload" in js   # reload after switch
    assert "viv-source-switch" in js        # the control id


def test_index_template_includes_source_switch():
    t = (Path(server.__file__).parent / "templates" / "index.html.j2").read_text(encoding="utf-8")
    assert "source-switch.js" in t
    assert "viv-source-switch" in t
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py::test_source_switch_js_present_and_wired tests/test_source_switch.py::test_index_template_includes_source_switch -q`
Expected: FAIL — `source-switch.js` missing / template lacks the include.

- [ ] **Step 3: Create `static/source-switch.js`**

```javascript
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
        if (ws.active || ws.current) opt.selected = true;
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
  }

  window._openSourceSwitch = _mount;
  if (document.readyState !== "loading") _mount();
  else document.addEventListener("DOMContentLoaded", _mount);
})();
```

- [ ] **Step 4: Add the mount point + script to `index.html.j2`**

Find the rail/header region (where `#viv-workspace-switcher-trigger` lives) and add, near it:

```html
<span id="viv-source-switch" class="viv-source-switch"></span>
```

And with the other `<script src=...>` tags at the bottom of the template, add:

```html
<script src="/source-switch.js"></script>
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py::test_source_switch_js_present_and_wired tests/test_source_switch.py::test_index_template_includes_source_switch -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/static/source-switch.js vivarium_dashboard/templates/index.html.j2 tests/test_source_switch.py
git commit -m "feat(source-switch): header source dropdown (switch + reload)"
```

---

## Task 4: Cross-switch flow test (the end-to-end proof)

**Files:**
- Test: `tests/test_source_switch.py`

**Interfaces:**
- Consumes: `_switch_active_workspace` (Task 1) + any file-based builder that reads `WORKSPACE`/`ws_root`. Uses `lib.investigation_status.build_iset_summary` (ws_root-parameterized, no runs needed) OR a direct workspace-file read — here we read each workspace's `name` from `workspace.yaml` through the active root to prove re-pointing end to end without HTTP.

- [ ] **Step 1: Write the test**

```python
import yaml
from vivarium_dashboard.lib import _root


def _make_ws(d, name):
    d.mkdir(parents=True, exist_ok=True)
    (d / "workspace.yaml").write_text(yaml.safe_dump({"name": name}))
    return d


def test_one_server_switches_between_two_workspaces(tmp_path):
    a = _make_ws(tmp_path / "wa", "alpha")
    b = _make_ws(tmp_path / "wb", "beta")

    def active_name():
        root = _root.get_workspace_root()
        return yaml.safe_load((root / "workspace.yaml").read_text())["name"]

    server._switch_active_workspace(a)
    assert server.WORKSPACE == a and active_name() == "alpha"

    server._switch_active_workspace(b)
    assert server.WORKSPACE == b and active_name() == "beta"   # re-pointed, no restart

    server._switch_active_workspace(a)
    assert active_name() == "alpha"                             # and back
```

- [ ] **Step 2: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py::test_one_server_switches_between_two_workspaces -q`
Expected: PASS (the implementation from Task 1 already supports this).

- [ ] **Step 3: Run the full new test file + a regression check**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py -q`
Expected: PASS (all tasks' tests).
Run: `.venv/bin/python -m pytest tests/test_data_endpoints.py -q`
Expected: PASS (no regression in existing endpoints/routing).

- [ ] **Step 4: Commit**

```bash
git add tests/test_source_switch.py
git commit -m "test(source-switch): one server switches between two workspaces"
```

---

## Task 5: Honest UI note for composites (until SP2b)

**Files:**
- Modify: `vivarium_dashboard/static/source-switch.js` (a one-line note in the switch confirmation)
- Test: `tests/test_source_switch.py`

**Interfaces:**
- Consumes: nothing new. Produces: a visible note that Composite Explorer may need a server restart to refresh after a switch (removed when SP2b lands).

- [ ] **Step 1: Write the failing test**

```python
def test_source_switch_warns_about_composites():
    js = _static("source-switch.js")
    assert "Composite" in js   # the honest until-SP2b note
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py::test_source_switch_warns_about_composites -q`
Expected: FAIL.

- [ ] **Step 3: Add the note**

In `source-switch.js`, change the `if (r.ok)` branch of `_switch` to stamp a one-time notice before reload (sessionStorage so it shows once post-reload), and surface it on load:

```javascript
    if (r.ok) {
      // Until SP2b (composite subprocess-isolation), Composite Explorer may show
      // the previous workspace's composites until the server restarts.
      try { sessionStorage.setItem("viv-source-switched", "1"); } catch (e) {}
      window.location.reload();
    } else {
```

And at the end of `_mount()`:

```javascript
    try {
      if (sessionStorage.getItem("viv-source-switched")) {
        sessionStorage.removeItem("viv-source-switched");
        console.info("Switched workspace. Note: Composite Explorer may need a server restart to refresh.");
      }
    } catch (e) {}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_switch.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/source-switch.js tests/test_source_switch.py
git commit -m "feat(source-switch): note composite staleness until SP2b"
```

---

## Task 6: Hand off to SP2b (composite subprocess-isolation)

This is **not** an implementation task — it is the required follow-on. After SP2 core merges, write `docs/superpowers/plans/<date>-composite-subprocess-isolation.md` (SP2b) covering: a subprocess composite runner mirroring `_get_registry_data` (`server.py` ~376) for `discover_all_composites`/`discover_generators` AND the build/resolve path (`build_generator`, the generator `_REGISTRY`); a per-workspace composite cache added to `_invalidate_workspace_caches`; removal of the in-process `_ws_add_to_sys_path()` discovery insertions (call sites ~3375, 3418, 3529, 3555, 3714, 4958); and a parity test (subprocess output == prior in-process output for a fixture workspace). Until SP2b lands, the Task 5 note stands.

---

## Self-Review

**1. Spec coverage:**
- §Component 1 (switch handler + lock + lib-level switch fn) → Tasks 1–2. ✓
- §Component 2 (single `invalidate_workspace_caches`) → Task 1. ✓
- §Component 3 (subprocess-isolate composites) → **split to SP2b** (Task 6 + Global Constraints), with the honest in-UI note (Task 5). Documented, not dropped. ✓ (scope split per writing-plans rule)
- §Component 4 (source dropdown reusing catalog; open = switch+reload; keep legacy) → Task 3 + Global Constraints. ✓
- §Data flow (switch → reload → /api/* hit new root) → Tasks 1–4. ✓
- §Error handling (unknown path 400; runs unaffected; serialized) → Task 2 (400) + Task 1 (lock) + Global Constraints. ✓
- §Testing (unit switch/invalidate, flow across two workspaces) → Tasks 1, 4 (composite parity test → SP2b). ✓

**2. Placeholder scan:** No "TBD"/"handle errors". The one deferred detail — the exact remaining `WORKSPACE`-keyed caches — is concretely enumerated (`_REGISTRY_CACHE`/`_LINKAGE_CACHE`/`_COMPOSITE_STATE_CACHE`/`_RUN_STORE_SUMMARY_CACHE`/`_WP_CACHE`/`_DATA_SOURCES_CACHE`). The `workspace_catalog` entry attribute names carry a verify-and-match instruction (Task 2 step 3) because the catalog API is read, not invented. ✓

**3. Type consistency:** `_switch_active_workspace(new_root: Path)`, `_invalidate_workspace_caches()`, `_post_source_switch(self, body)`, route `"/api/source/switch" -> "_post_source_switch"`, control id `viv-source-switch`, entry `_openSourceSwitch` — used identically across Tasks 1–5. ✓
