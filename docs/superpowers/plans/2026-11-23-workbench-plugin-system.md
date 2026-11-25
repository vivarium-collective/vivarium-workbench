# Workbench Plugin System (PR 1 — core seam) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a thin, generic plugin seam to vivarium-workbench so an external package can contribute backend routes, a rail panel/page, a workspace-data folder, and skills — opt-in, with zero per-plugin edits to core.

**Architecture:** In-process Python plugins discovered via the `vivarium_workbench.plugins` entry-point. A new `vivarium_workbench/plugins/` module defines the `WorkbenchPlugin` contract + a `load_plugins(app, ws_root)` loader that, for each *enabled* plugin, includes its `APIRouter` under `/api/plugins/<name>`, mounts its static dir under `/plugins/<name>`, and records a UI manifest served at `/api/plugins/manifest`. One generic frontend loader (`plugin-loader.js`) reads that manifest and registers panels into the existing `VivPanelDock`. `api/app.py` and `index.html.j2` each get one generic, never-per-plugin edit.

**Tech Stack:** Python 3.12, FastAPI, `importlib.metadata` (stdlib), vanilla JS (no bundler), pytest, Node test scripts under `tests/js/`.

**Spec:** `docs/superpowers/specs/2026-11-23-workbench-plugin-system-design.md`

## Global Constraints

- Import package is `vivarium_workbench`; **no new runtime dependency** (discovery uses stdlib `importlib.metadata`).
- Every plugin contribution is namespaced by `name`: routes under `/api/plugins/<name>`, assets under `/plugins/<name>`; a plugin may never register outside its namespace or shadow a core route/panel.
- **Fail-soft:** each plugin's load is wrapped so one plugin's failure logs and is skipped — never aborts startup or other plugins.
- **Default off:** a plugin runs only if *discovered* AND *enabled*. Enabled = listed in the open workspace's `workspace.yaml` `plugins:` list, or in env `VIVARIUM_WORKBENCH_PLUGINS` (comma-separated deploy allowlist).
- Core stays green — `ruff` + `mypy` + `lint-imports` + full pytest — with **zero plugins installed**.
- Mutating plugin routes honor the CSRF/origin guard (`lib/csrf`); any request value used as a path goes through `lib/path_safety` (covered by `tests/test_traversal_sweep.py`). Plugin authors' responsibility; documented in Task 6.
- Env var names use the `VIVARIUM_WORKBENCH_*` prefix (repo rename convention).
- **No AI attribution** in commit messages (no `Co-Authored-By`, no "Generated with" footer).

## Review Focus

- **Duplicate `name`:** two discovered plugins share a `name` → core loads the first and skips the second with a warning; it never mounts two routers on one prefix. (Task 3)
- **Missing/めmalformed `workspace.yaml` `plugins:`** (absent key, non-list, non-string entries) → treated as "none enabled", no crash. (Task 2)
- **Declared static dir / UI manifest absent or invalid JSON** → the plugin's API still loads; the UI contribution is skipped with a warning (partial isolation within one plugin). (Task 3)
- **`GET /api/plugins/manifest` with nothing enabled** → `200` + `[]`; `plugin-loader.js` no-ops cleanly. (Task 3, Task 5)
- **A plugin whose `register()` raises** → logged and skipped; core and other plugins unaffected (fail-soft). (Task 3)

---

### Task 1: Plugin contract — `WorkbenchPlugin` + `PluginRegistry`

**Files:**
- Create: `vivarium_workbench/plugins/__init__.py` (re-exports the public names)
- Create: `vivarium_workbench/plugins/base.py`
- Test: `tests/test_plugin_base.py`

**Interfaces:**
- Produces:
  - `WORKBENCH_PLUGIN_API_VERSION: int = 1`
  - `@dataclass PanelSpec(id, title, icon, group, side, entry: str)`
  - `@dataclass PluginContributions(name: str, routers: list, static_dir: Path | None, ui_manifest: Path | None, workspace_folder: str | None, skills_dir: Path | None)`
  - `class WorkbenchPlugin` — attrs `name: str`, `api_version: int = WORKBENCH_PLUGIN_API_VERSION`; `register(self, reg: PluginRegistry) -> None` (raises `NotImplementedError`); `package_dir(self) -> Path` (dir of the file defining the concrete subclass, via `inspect.getfile(type(self))`).
  - `class PluginRegistry(plugin: WorkbenchPlugin)` with `api_router(router)`, `static_dir(path: str)`, `ui(manifest: str)`, `workspace_store(folder: str, *, reader=None)`, `skills(dir: str)`, and `contributions() -> PluginContributions`. Relative paths resolve against `plugin.package_dir()`.
  - *(Refines spec §2/§5: the single `reg.ui(manifest)` replaces `reg.panels(...)`; the manifest JSON is `{"css": [...], "js": [...], "panels": [PanelSpec...]}` so one file declares both assets and panels.)*

- [ ] **Step 1: Write the failing test**

```python
def test_registry_records_contributions(tmp_path):
    from vivarium_workbench.plugins import WorkbenchPlugin, PluginRegistry
    from fastapi import APIRouter
    r = APIRouter()
    class P(WorkbenchPlugin):
        name = "demo"
        def register(self, reg):
            reg.api_router(r); reg.static_dir("static")
            reg.ui("static/plugin.json"); reg.workspace_store("demo"); reg.skills("skills")
    p = P(); reg = PluginRegistry(p); p.register(reg)
    c = reg.contributions()
    assert c.name == "demo" and r in c.routers
    assert c.static_dir == p.package_dir() / "static"
    assert c.ui_manifest == p.package_dir() / "static/plugin.json"
    assert c.workspace_folder == "demo" and c.skills_dir == p.package_dir() / "skills"

def test_base_register_is_abstract():
    from vivarium_workbench.plugins import WorkbenchPlugin, PluginRegistry
    import pytest
    with pytest.raises(NotImplementedError):
        WorkbenchPlugin().register(PluginRegistry(WorkbenchPlugin()))
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_plugin_base.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement `base.py` + `__init__.py`** with the signatures above. `PluginRegistry` accumulates into a `PluginContributions`; path setters join against `plugin.package_dir()`.
- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_plugin_base.py -v` → PASS.
- [ ] **Step 5: Commit** — `feat(plugins): WorkbenchPlugin contract + PluginRegistry`.

---

### Task 2: Discovery + enablement — `plugins/discovery.py`

**Files:**
- Create: `vivarium_workbench/plugins/discovery.py`
- Test: `tests/test_plugin_discovery.py`

**Interfaces:**
- Consumes: `WorkbenchPlugin` (Task 1).
- Produces:
  - `ENTRY_POINT_GROUP = "vivarium_workbench.plugins"`
  - `discover_plugins() -> list[WorkbenchPlugin]` — instantiates each entry-point in `ENTRY_POINT_GROUP`, **plus** any `module:Class` in env `VIVARIUM_WORKBENCH_PLUGIN_MODULES` (comma-separated; a dev/test path to load an un-installed plugin). Each load wrapped: a broken entry-point is logged and skipped.
  - `enabled_plugin_names(ws_root: Path | None, env: Mapping[str, str] | None = None) -> set[str]` — union of `workspace.yaml` `plugins:` (read with `yaml.safe_load`; absent/non-list/non-str-safe → empty) and `VIVARIUM_WORKBENCH_PLUGINS` (comma-split, stripped).
  - `select_enabled(plugins: list[WorkbenchPlugin], names: set[str]) -> list[WorkbenchPlugin]` — keep those whose `name ∈ names`; on duplicate `name` keep first, warn.

- [ ] **Step 1: Write the failing tests**

```python
def test_enabled_names_union(tmp_path, monkeypatch):
    (tmp_path / "workspace.yaml").write_text("plugins: [a, b]\n")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PLUGINS", "b, c")
    from vivarium_workbench.plugins.discovery import enabled_plugin_names
    assert enabled_plugin_names(tmp_path) == {"a", "b", "c"}

def test_enabled_names_missing_or_malformed(tmp_path, monkeypatch):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_PLUGINS", raising=False)
    from vivarium_workbench.plugins.discovery import enabled_plugin_names
    assert enabled_plugin_names(None) == set()
    (tmp_path / "workspace.yaml").write_text("plugins: not-a-list\n")
    assert enabled_plugin_names(tmp_path) == set()

def test_select_enabled_dedups_by_name():
    from vivarium_workbench.plugins import WorkbenchPlugin
    from vivarium_workbench.plugins.discovery import select_enabled
    class A(WorkbenchPlugin): name="x"
    class B(WorkbenchPlugin): name="x"
    out = select_enabled([A(), B()], {"x"})
    assert len(out) == 1 and isinstance(out[0], A)
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_plugin_discovery.py -v` → FAIL.
- [ ] **Step 3: Implement `discovery.py`.** `discover_plugins` uses `importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)` + the env module list; `enabled_plugin_names` reads the two sources defensively.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `feat(plugins): entry-point discovery + opt-in enablement`.

---

### Task 3: Loader, wiring, manifest route — `plugins/loader.py`

**Files:**
- Create: `vivarium_workbench/plugins/loader.py`
- Test: `tests/test_plugin_loader.py`

**Interfaces:**
- Consumes: Task 1 (`PluginRegistry`, `PluginContributions`, `WORKBENCH_PLUGIN_API_VERSION`), Task 2 (`discover_plugins`, `enabled_plugin_names`, `select_enabled`).
- Produces:
  - `load_plugins(app, ws_root: Path | None, *, discovered: list[WorkbenchPlugin] | None = None, env=None) -> list[PluginContributions]` — resolve enabled set; for each: skip (warn) if `api_version != WORKBENCH_PLUGIN_API_VERSION`; build `PluginRegistry`, call `register`; `app.include_router(router, prefix=f"/api/plugins/{name}")`; if `static_dir` exists, `app.mount(f"/plugins/{name}", StaticFiles(directory=static_dir))` (missing dir → warn, skip UI); store contributions on `app.state.plugin_contribs`; **each plugin wrapped in try/except**. Adds the manifest route once (idempotent).
  - `build_manifest(contribs: list[PluginContributions]) -> list[dict]` — per plugin `{name, css:[url...], js:[url...], panels:[...]}` read from its `ui_manifest` JSON (absent/invalid → `{name, css:[], js:[], panels:[]}` + warn); asset paths rewritten to `/plugins/<name>/<rel>`.
  - Registers `GET /api/plugins/manifest` → `build_manifest(app.state.plugin_contribs)`.

- [ ] **Step 1: Write the failing tests** (bare `FastAPI()` + `starlette.testclient.TestClient`, inject `discovered=`):

```python
def _app_with(plugins, ws=None, enabled=("ex",)):
    from fastapi import FastAPI
    from vivarium_workbench.plugins.loader import load_plugins
    app = FastAPI()
    load_plugins(app, ws, discovered=plugins, env={"VIVARIUM_WORKBENCH_PLUGINS": ",".join(enabled)})
    return app

def test_router_mounted_under_namespace_and_manifest():
    from fastapi import APIRouter
    from vivarium_workbench.plugins import WorkbenchPlugin
    from starlette.testclient import TestClient
    r = APIRouter(); r.add_api_route("/ping", lambda: {"ok": True})
    class Ex(WorkbenchPlugin):
        name = "ex"
        def register(self, reg): reg.api_router(r)
    c = TestClient(_app_with([Ex()]))
    assert c.get("/api/plugins/ex/ping").json() == {"ok": True}
    assert any(p["name"] == "ex" for p in c.get("/api/plugins/manifest").json())

def test_manifest_empty_when_nothing_enabled():
    from starlette.testclient import TestClient
    c = TestClient(_app_with([], enabled=()))
    assert c.get("/api/plugins/manifest").json() == []

def test_failsoft_one_plugin_raises_others_survive():
    from fastapi import APIRouter
    from vivarium_workbench.plugins import WorkbenchPlugin
    from starlette.testclient import TestClient
    good = APIRouter(); good.add_api_route("/ping", lambda: {"ok": True})
    class Bad(WorkbenchPlugin):
        name = "bad"
        def register(self, reg): raise RuntimeError("boom")
    class Good(WorkbenchPlugin):
        name = "good"
        def register(self, reg): reg.api_router(good)
    c = TestClient(_app_with([Bad(), Good()], enabled=("bad", "good")))
    assert c.get("/api/plugins/good/ping").status_code == 200
    assert [p["name"] for p in c.get("/api/plugins/manifest").json()] == ["good"]
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement `loader.py`** per the Interfaces block. Use `fastapi.staticfiles.StaticFiles`. Log via the module logger used elsewhere in `lib/`.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `feat(plugins): loader, namespaced wiring, /api/plugins/manifest, fail-soft`.

---

### Task 4: Integrate into the app + end-to-end example plugin

**Files:**
- Modify: `vivarium_workbench/api/app.py` (one hook at startup)
- Create fixture plugin: `tests/_fixtures/plugins/exampleplugin/__init__.py`, `.../static/plugin.json`, `.../static/example.js`, `.../static/example.css`
- Test: `tests/test_plugin_integration.py`

**Interfaces:**
- Consumes: `load_plugins` (Task 3), `api/deps.py` workspace-root access.
- Produces: at app startup (a FastAPI `lifespan`/`on_event("startup")` so env + workspace are read at boot, not import), core calls `plugins.load_plugins(app, <workspace root>)`. The example fixture defines `ExamplePlugin(name="example")` registering a router with `GET /ping`, a `static/` dir, and `reg.ui("static/plugin.json")` (`plugin.json` = `{"css":["example.css"],"js":["example.js"],"panels":[{"id":"example-hello","title":"Hello","icon":"beaker","group":"Example","side":"left","entry":"ExampleHelloPanel"}]}`).

- [ ] **Step 1: Write the failing test** — use `dashboard_client` (real uvicorn boot reads env); set `VIVARIUM_WORKBENCH_PLUGIN_MODULES=exampleplugin:ExamplePlugin` (ensure `tests/_fixtures/plugins` on `sys.path`) and `VIVARIUM_WORKBENCH_PLUGINS=example`:

```python
def test_example_plugin_end_to_end(dashboard_client_with_example):  # fixture sets env + ws
    c = dashboard_client_with_example
    assert c.get("/api/plugins/example/ping").json() == {"ok": True}
    names = [p["name"] for p in c.get("/api/plugins/manifest").json()]
    assert "example" in names
    assert c.get("/plugins/example/example.js").status_code == 200

def test_core_unaffected_when_disabled(dashboard_client):  # no plugins enabled
    assert dashboard_client.get("/api/plugins/manifest").json() == []
    assert dashboard_client.get("/api/plugins/example/ping").status_code == 404
```

- [ ] **Step 2: Run to verify fail** → FAIL (startup hook not wired).
- [ ] **Step 3: Wire the startup hook in `app.py`** (import `plugins`; call `load_plugins` in the startup handler with the workspace root) and add the example fixture files + the `dashboard_client_with_example` fixture in `tests/conftest.py` (clone of `dashboard_client` with the two env vars + `sys.path` entry).
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Run the broader guards** — `pytest tests/test_traversal_sweep.py tests/test_plugin_import_allowlist.py -q` (plugin routes join the traversal sweep; dynamic loading must not break the allowlist) and `uv run ruff check vivarium_workbench && uv run mypy` → all green.
- [ ] **Step 6: Commit** — `feat(plugins): load enabled plugins at startup + example fixture`.

---

### Task 5: Frontend loader — `static/plugin-loader.js`

**Files:**
- Create: `vivarium_workbench/static/plugin-loader.js`
- Modify: `vivarium_workbench/templates/index.html.j2` (one `<script src="/static/plugin-loader.js" defer>` include)
- Test: `tests/js/test_plugin_loader.js` (plain Node, matching the existing `tests/js/` style)

**Interfaces:**
- Consumes: `GET /api/plugins/manifest` (Task 3); `global.VivPanelDock.make(...)` (`static/panel-dock.js`).
- Produces: on load, fetch the manifest; for each plugin inject its `css` as `<link>` and `js` as `<script>` (from `/plugins/<name>/…`), then after its JS loads, for each panel call `VivPanelDock.make(opts)` where `opts` is built from the `PanelSpec` and the render fn is `window[panel.entry]`. Empty manifest → no-op. (Exact `make` option keys bound against `panel-dock.js:~600-680`.)

- [ ] **Step 1: Write the failing Node test** — stub `global.fetch` to return a one-plugin manifest, stub `global.document` (record injected tags) and `global.VivPanelDock = { make: calls.push }`, `require` the loader, assert: one css + one js tag injected with `/plugins/example/` URLs, and `VivPanelDock.make` called once with `title: "Hello"`. Add the file to `scripts/run_js_tests.sh`’s list if it isn't globbed.
- [ ] **Step 2: Run to verify fail** — `bash scripts/run_js_tests.sh` → the new test FAILs.
- [ ] **Step 3: Implement `plugin-loader.js`** (IIFE, no bundler; guard when `VivPanelDock` absent) and add the template include.
- [ ] **Step 4: Run to verify pass** — `bash scripts/run_js_tests.sh` → all PASS.
- [ ] **Step 5: Commit** — `feat(plugins): frontend plugin-loader registers panels via VivPanelDock`.

---

### Task 6: Author guide — `docs/plugins.md`

**Files:**
- Create: `docs/plugins.md`
- Modify: `CLAUDE.md` (one "Plugins" subsection pointer under Architecture)
- Test: (docs) `tests/test_docs_no_dead_refs.py` if present, else none.

**Interfaces:** Consumes everything above (documents the contract).

- [ ] **Step 1:** Write `docs/plugins.md`: the entry-point snippet, the `WorkbenchPlugin.register` surface (`api_router` / `static_dir` / `ui` / `workspace_store` / `skills`), the `plugin.json` shape, enablement (`workspace.yaml plugins:` + `VIVARIUM_WORKBENCH_PLUGINS`), the dev `VIVARIUM_WORKBENCH_PLUGIN_MODULES` shortcut, and the author's obligations (CSRF on mutating routes via `lib/csrf`, path inputs via `lib/path_safety`, namespacing). Link the design spec. Add a short "Plugins" pointer to `CLAUDE.md`.
- [ ] **Step 2:** `pytest -q` (whole suite) → green; if `tests/test_docs_no_dead_refs.py` exists, it passes.
- [ ] **Step 3: Commit** — `docs(plugins): plugin authoring guide`.

---

## Self-Review

- **Spec coverage:** discovery (T2) · contract/registry (T1) · loader + namespacing + manifest + fail-soft (T3) · opt-in enablement (T2) · app/template integration + acceptance-style example (T4) · frontend VivPanelDock registration (T5) · conventions/author guide (T6). Spec §"first plugin / viva-knowledge-layer extraction" is **PR 2**, a separate plan — out of scope here by design.
- **Review Focus:** duplicate name (T2 `test_select_enabled_dedups_by_name`), malformed `plugins:` (T2), missing static/UI manifest (T3 `build_manifest` default + warn — add an assertion in T3 step 1 if not already), empty manifest (T3 `test_manifest_empty…`, T5 no-op), register() raises (T3 `test_failsoft…`). All owned.
- **Type consistency:** `PluginContributions` fields (`routers`, `static_dir`, `ui_manifest`, `workspace_folder`, `skills_dir`) and `reg.ui(...)` are used identically in T1/T3/T4. `VivPanelDock.make` (not `register`) per the real global.
- **Proportion:** plan ≈ spec length; bodies appear only for test assertions and the fixture's `plugin.json` (exact values), not for implementation the signatures determine.
