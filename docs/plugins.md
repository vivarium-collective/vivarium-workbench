# Workbench plugins

A plugin adds namespaced extensions to the workbench — a backend router, a rail
panel/page, a workspace-data folder, and skills — **without editing core**. Plugins
are in-process Python packages, discovered via an entry-point, and run only when
**opted in**. Design: [`docs/superpowers/specs/2026-11-23-workbench-plugin-system-design.md`](superpowers/specs/2026-11-23-workbench-plugin-system-design.md).

## Quick start

Scaffold a runnable plugin — router, a dockable panel, a test, and a README — in one
command, then iterate:

```bash
vwb new-plugin my-feature            # writes ./viva-my-feature/
cd viva-my-feature
pip install -e ".[dev]" && pytest    # the generated plugin already passes its own test
```

Enable it for a workspace (add `plugins: [my-feature]` to its `workspace.yaml`),
serve, and the **My Feature** panel appears in the left rail calling its `/ping`
route. Then edit `viva_my_feature/api.py` (routes) and `static/panel.js` (panel).
The sections below explain each part the scaffold generated.

## Anatomy

A plugin is its own pip-installable package that declares one entry-point:

```toml
# viva-knowledge-layer/pyproject.toml
[project.entry-points."vivarium_workbench.plugins"]
knowledge-layer = "viva_knowledge_layer.plugin:KnowledgeLayerPlugin"
```

```python
# viva_knowledge_layer/plugin.py
from fastapi import APIRouter
from vivarium_workbench.plugins import WorkbenchPlugin

router = APIRouter()

@router.get("/claims")          # served at /api/plugins/knowledge-layer/claims
def claims():
    ...

class KnowledgeLayerPlugin(WorkbenchPlugin):
    name = "knowledge-layer"    # url-safe; namespaces everything the plugin adds

    def register(self, reg):
        reg.api_router(router)              # → /api/plugins/<name>/*
        reg.static_dir("static")           # → /plugins/<name>/*
        reg.ui("static/plugin.json")       # panels + assets to load (below)
        reg.workspace_store("mira")        # a <workspace>/mira/ folder the plugin owns
        reg.skills("skills")               # a skills/ dir surfaced to the agent
```

All `register` paths are relative to the plugin package directory.

## The UI manifest (`plugin.json`)

Declares the CSS/JS to load (relative to the static dir, served under
`/plugins/<name>/`) and the dockable panels to add to the left rail:

```json
{
  "css": ["knowledge.css"],
  "js": ["knowledge.js"],
  "panels": [
    { "id": "knowledge-graph", "title": "Graph", "icon": "graph",
      "group": "Knowledge", "side": "left", "entry": "KnowledgeGraphPanel" }
  ]
}
```

`entry` is the name of a global function the plugin's JS defines; the loader calls
it with a **panel context** (looked up lazily, so script-load order doesn't matter):

```js
window.KnowledgeGraphPanel = function (ctx) {
  // ctx.el      the panel body element to render into
  // ctx.name    the plugin name ("knowledge-layer")
  // ctx.base    static asset base  ("/plugins/knowledge-layer/")
  // ctx.apiBase API base           ("/api/plugins/knowledge-layer")
  // ctx.api(path, opts) -> Promise<json>   fetch under apiBase
  ctx.api('/knowledge/graph').then(function (d) { ctx.el.innerHTML = render(d); });
};
```

So a plugin never hardcodes its own namespace — `ctx.api('/knowledge/graph')` resolves
to `/api/plugins/knowledge-layer/knowledge/graph`. Panels dock/drag/resize via the
shared `VivPanelDock`, exactly like core panels. A missing or invalid manifest disables
the plugin's **UI only** — its API still loads.

## Enabling a plugin (opt-in, default off)

A plugin runs only when **installed AND enabled**. Enable it per workspace:

```yaml
# <workspace>/workspace.yaml
plugins: [knowledge-layer]
```

or for a whole deployment:

```bash
export VIVARIUM_WORKBENCH_PLUGINS=knowledge-layer,other-plugin
```

**Dev shortcut** — load an un-installed plugin straight from a checkout (no
entry-point, no reinstall) while iterating:

```bash
export VIVARIUM_WORKBENCH_PLUGIN_MODULES=viva_knowledge_layer.plugin:KnowledgeLayerPlugin
export VIVARIUM_WORKBENCH_PLUGINS=knowledge-layer
```

## Author obligations

- **Namespacing is automatic and enforced** — routes under `/api/plugins/<name>`,
  assets under `/plugins/<name>`. A plugin can't shadow a core route or panel.
- **Mutating routes** (`POST`/`DELETE`) must honor the CSRF/origin guard — use
  `vivarium_workbench.lib.csrf` the same way core routes do.
- **Any request value used as a path** must go through
  `vivarium_workbench.lib.path_safety` (`plain_name` / `resolve_inside`);
  `tests/test_traversal_sweep.py` walks the OpenAPI schema and will fail a plugin
  route that lets a path escape the workspace.
- **Workspace folders** resolve through the plugin's declared `workspace_store`
  name; create the folder lazily on first write (never require a template change).
- **Fail-soft:** a plugin that raises during load is logged and skipped — it never
  breaks startup or other plugins. Keep your `register` and import side effects
  cheap and defensive.

## Trust model

v1 plugins run **in-process** and are trusted (collaborator-authored). There is no
sandbox; a plugin has the same access as core. Install only plugins you trust.
