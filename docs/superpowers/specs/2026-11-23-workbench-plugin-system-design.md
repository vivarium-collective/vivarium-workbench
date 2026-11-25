# vivarium-workbench plugin system — design

**Date:** 2026-11-23
**Status:** design, pending approval
**First consumer / acceptance test:** extract Matt Akamatsu's knowledge layer into `viva-knowledge-layer`, running with zero core edits.

## Intent and why

External collaborators want to try exploratory features in the workbench, iterate
on them, and hand them off to each other — **without editing the core stack and
without the maintainer merging their branch**. Today that's impossible: a feature
is wired into a 9,133-line `api/app.py` (hand-imports ~100 `*_views.py`), a single
`index.html.j2` rail, and the shared static JS. So a collaborator's work can only
arrive as a merge, with conflicts (Matt's knowledge-layer merge hit exactly one:
`api/app.py`). His own verdict: *"built beside the workbench, not on top of it —
the real work is alignment."*

This design adds explicit, stable **extension points** a plugin registers into, so
an exploratory feature lives entirely in its own installable package and is **opt-in**
per workspace. The knowledge layer is the proof: if it fits the seams with zero core
edits, the seams are right.

## Goals

- A feature can ship as a separate pip-installable repo (uv git dep, like the rest
  of the ecosystem) and add backend routes, a rail panel/page, a workspace-data
  convention, and skills — **touching no core file.**
- **Opt-in:** a plugin runs only when both installed *and* enabled (per-workspace or
  per-deploy). Default off.
- **Transferable:** handing off a plugin = handing off its repo. No core history to
  carry.
- **Fail-soft:** a broken or incompatible plugin is logged and skipped; core and
  other plugins keep running.
- Core's own edits are **generic and one-time** — a loader + a startup hook that
  never changes when a plugin is added.

## Non-goals (v1)

- Injecting UI into an *existing* core page (only whole new panels/pages in v1).
- Out-of-process / iframe-sandboxed / language-agnostic plugins (Approach B) — may
  be offered later as one *kind* of plugin.
- A marketplace/discovery UI, hot-reload, or plugin install-from-the-dashboard.
- Sandboxing untrusted plugins. v1 trust model: in-process, collaborator-trusted.
- Changing `study.yaml` / `workspace.yaml` schema beyond adding a `plugins:` list.

## Anti-goals (these would mean the design failed)

- A plugin that requires editing `api/app.py`, `index.html.j2`, or core `lib/`.
- A plugin silently shadowing or overriding a core route or panel.
- A plugin crash taking down the server.

## Approaches considered

- **A — In-process Python plugin packages (CHOSEN).** Plugins are pip-installed
  packages discovered via Python entry-points; each registers an `APIRouter`, a
  static dir, panel descriptors, a workspace-folder convention, and skills.
  *Chosen* because it's the only option meeting all four constraints (iterate fast,
  opt-in, no core edits, transferable) while still allowing rich features that read
  workspace files and reuse internal helpers.
- **B — Out-of-process sidecar (service + reverse-proxy + iframe UI).** Hard
  isolation, language-agnostic. Rejected for v1: collaborators must run/deploy a
  service, and deep integration (reading `<workspace>/mira/`, sharing auth, calling
  helpers) is awkward over HTTP — too heavy for "try things out." Can later be
  wrapped as one A-plugin for the rare untrusted/heavy case.
- **C — Frontend-only static bundle, no backend seam.** Simplest/safest, but can't
  add storage, a write-gate, or routes — the knowledge layer needs all three.
  Rejected as too limited.

## Architecture

### 1. Discovery

A plugin package declares an entry-point:

```toml
# viva-knowledge-layer/pyproject.toml
[project.entry-points."vivarium_workbench.plugins"]
knowledge-layer = "viva_knowledge_layer.plugin:KnowledgeLayerPlugin"
```

Core discovers them with `importlib.metadata.entry_points(group="vivarium_workbench.plugins")`.

### 2. The plugin contract

```python
# vivarium_workbench/plugins/base.py  (core)
WORKBENCH_PLUGIN_API_VERSION = 1

class WorkbenchPlugin:
    name: str                       # url-safe; namespaces everything
    api_version: int = 1            # core skips on mismatch with a warning
    def register(self, reg: "PluginRegistry") -> None: ...

class PluginRegistry:
    def api_router(self, router) -> None       # FastAPI APIRouter
    def static_dir(self, path: str) -> None    # dir inside the plugin package
    def panels(self, manifest: str) -> None    # path to panels.json (see §5)
    def workspace_store(self, folder: str, *, reader=None) -> None
    def skills(self, dir: str) -> None
```

A plugin implementation:

```python
# viva_knowledge_layer/plugin.py
class KnowledgeLayerPlugin(WorkbenchPlugin):
    name = "knowledge-layer"
    def register(self, reg):
        from .api import router
        reg.api_router(router)
        reg.static_dir("static")
        reg.panels("static/panels.json")
        reg.workspace_store("mira")
        reg.skills("skills")
```

### 3. Loading (the only core runtime change)

At app startup, after core routes are mounted, core calls `load_plugins(app)`:

- resolve the **enabled set** (§6);
- for each enabled plugin: instantiate, check `api_version`, call `register`, and
  wire what it registered —
  - `app.include_router(router, prefix=f"/api/plugins/{name}")`
  - `app.mount(f"/plugins/{name}", StaticFiles(directory=<pkg>/<static_dir>))`
  - record panel manifest + workspace-folder + skills dir in a process-wide
    `PluginManifest` the frontend/skills read;
- **each plugin wrapped in try/except**: on any failure, log `plugin '<name>'
  failed to load: …` and skip it. One bad plugin never blocks startup.

This is ~1 hook call in `app.py` plus a new `vivarium_workbench/plugins/` module.
It is generic and never edited per-plugin.

### 4. Frontend loading (one generic include)

- Core adds one line to `index.html.j2`: `<script src="/static/plugin-loader.js">`.
- `plugin-loader.js` fetches `GET /api/plugins/manifest` → `[{name, panels:[…],
  css:[urls], js:[urls]}]` (enabled plugins only).
- For each: inject its CSS/JS `<link>`/`<script>` from `/plugins/<name>/…`, then
  register each panel descriptor into the existing **`VivPanelDock` REGISTRY** and
  add its rail entry. Panels dock/drag/resize exactly like core panels.
- A plugin's JS bundle exposes a global render fn named in its descriptor; the
  loader binds it to the panel body. (Exact `VivPanelDock.register(...)` signature
  bound during implementation against `static/panel-dock.js`.)

### 5. Panel descriptor (`panels.json`)

```json
[{ "id": "knowledge-graph", "title": "Graph", "icon": "graph",
   "group": "Knowledge", "side": "left", "entry": "KnowledgeGraphPanel" }]
```

`entry` is the global the plugin's bundle defines. `group` creates/joins a rail
section. Fields map 1:1 to a `VivPanelDock` registry entry.

### 6. Opt-in / enablement

A plugin runs only if **installed AND enabled**. Enabled =
- listed in the open workspace's `workspace.yaml`:
  ```yaml
  plugins: [knowledge-layer]
  ```
  (per-workspace opt-in; the common case), **or**
- allow-listed for the whole deploy via env `VIVARIUM_WORKBENCH_PLUGINS=knowledge-layer,…`
  (for a hosted/read-only instance that wants a fixed set).

Default: empty → no plugins. `GET /api/plugins/manifest` and `load_plugins` both
filter by the enabled set. A plugin that declares a `workspace_store` folder
creates it lazily on first write — never requires a template change.

### 7. Skills

A plugin's `skills/` dir is surfaced through the existing single-sourced-skills
mechanism (see memory `reference_pbg_skills_single_sourced_plugin`); core records
the dir in the `PluginManifest` and the skills loader includes enabled plugins'
dirs. Exact wiring confirmed against that mechanism in the plan.

### 8. Namespacing & collision rules

Everything a plugin adds is namespaced by `name`: routes under
`/api/plugins/<name>`, assets under `/plugins/<name>`, panel ids prefixed, workspace
folder owned by the plugin. Core refuses to load two plugins with the same `name`,
and never lets a plugin register a path outside its namespace — so a plugin can
**never** shadow a core route or panel.

### 9. Fitting core conventions (confirmed against the repo)

The seam is deliberately thin because core already supports most of it:

- **Router without importing `app.py`.** `api/deps.py` already re-exports
  `get_workspace`, `get_workspace_context`, `session_key_of` *"so a separate router
  module can declare routes without importing `api/app.py`"* (CLAUDE.md). A plugin's
  `APIRouter` uses those deps directly; `include_router` is the existing attach path.
- **CSRF / origin.** Mutating plugin routes go through the same guard as core — the
  plugin calls `lib/csrf._csrf_ok()` (or core applies it to included routers at the
  hook). v1: the loader documents the requirement and the example plugin exercises a
  guarded POST so the test locks it in.
- **Path safety.** Any request value a plugin turns into a path must go through
  `lib/path_safety` (`plain_name` / `resolve_inside`); `tests/test_traversal_sweep.py`
  walks the OpenAPI schema and will catch a plugin route that lets a path escape the
  workspace — so plugin routes are covered by that sweep for free.
- **Workspace folder resolution.** `workspace_store(folder)` resolves through
  `lib/workspace_paths` + `lib/_root` (honoring a workspace's `layout:` remap), never
  a hardcoded join — same rule as core.
- **Import allowlist.** `tests/test_plugin_import_allowlist.py` + import-linter govern
  core's *static* imports; plugins load *dynamically* via entry-points, so they don't
  enter that graph. The loader itself imports only `importlib.metadata` + stdlib.
- **Lint/type gates.** The core seam passes `ruff` + `mypy` + `lint-imports` (CI).
  Plugin packages run their own CI; core stays green whether or not any plugin is
  installed.

## The first plugin — `viva-knowledge-layer` (acceptance test)

Move Matt's knowledge-layer files out of the workbench into a new repo:

- `lib/mira_store.py`, `mira_writes.py`, `knowledge_views.py`, `tldraw_board.py`,
  `mira_validate.py` → the plugin package;
- `api/knowledge/*`, `/api/mira/*`, `/api/dg/*` → one `APIRouter` under
  `/api/plugins/knowledge-layer`;
- `static/knowledge.{js,css}` + the rail section → the plugin's static dir +
  `panels.json` (Graph, Spec, Canvas, MIRA store);
- `mira/` workspace folder → declared via `workspace_store("mira")`;
- the claim/evidence skills → the plugin's `skills/`.

**Acceptance:** with `viva-knowledge-layer` installed and `plugins: [knowledge-layer]`
in a workspace, the four pages work; with it uninstalled or not enabled, the
workbench is byte-for-byte core. Matt's 28 knowledge-layer tests pass from inside
the plugin repo. **Zero diff to `api/app.py` / `index.html.j2` / core `lib/`** beyond
the one-time generic loader.

> Note on overlap: `main` has since grown a derived knowledge store
> (`lib/knowledge_step.py`) and a discovery-cycle panel. Those stay in core; the
> knowledge-layer plugin is the *authored* discourse-graph layer (CLM claims grounded in
> literature + a write-gate), distinct from core's *derived* verdicts. Reconciling
> the two vocabularies and the two proposal queues is **out of scope here** —
> flagged for a follow-up once the plugin seam exists.

## What's left to the builder

- Exact `VivPanelDock.register(...)` call and rail-group insertion in
  `plugin-loader.js`, bound against `static/panel-dock.js`.
- The precise hook site in `api/app.py` startup and the `PluginManifest` shape.
- The skills-loader wiring against the single-sourced-skills mechanism.
- The `viva-knowledge-layer` repo scaffolding (pyproject, entry-point, tests
  moved over) — a second deliverable after the core seam lands.

## Rollout

1. **PR 1 (this branch): core seam** — `plugins/` module (base + registry +
   loader), the `/api/plugins/manifest` route, `plugin-loader.js`, the one
   `app.py` hook and one `index.html.j2` include, `workspace.yaml` `plugins:` key,
   docs. Ships with a tiny in-repo **example plugin** under `tests/` proving a
   router + panel + static load with zero core edits (keeps CI honest).
2. **PR 2 (separate repo): `viva-knowledge-layer`** — extract Matt's layer;
   the real acceptance test.
