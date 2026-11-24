"""Scaffold a runnable workbench-plugin skeleton.

``scaffold_plugin(dest, name)`` writes a minimal, pip-installable plugin package
that loads end-to-end through the real plugin contract — a router, a static dir, a
UI manifest with one dockable panel, a test, and a README. It is the engine behind
``vwb new-plugin`` and the worked reference the authoring guide points at.
"""
from __future__ import annotations

import re
from pathlib import Path

# Mirrors ``loader._NAME_RE`` — a plugin name must be url-safe (it namespaces the
# plugin's routes and static mount).
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _derive(name: str) -> dict[str, str]:
    words = [w for w in re.split(r"[-_]", name) if w]
    camel = "".join(w.capitalize() for w in words)
    human = " ".join(w.capitalize() for w in words)
    return {
        "NAME": name,
        "PKG": "viva_" + name.replace("-", "_"),
        "CLASS": camel + "Plugin",
        "ENTRY": camel + "Panel",
        "TITLE": human,
        "GROUP": human,
        "TESTMOD": name.replace("-", "_"),
    }


def _render(template: str, subs: dict[str, str]) -> str:
    out = template
    for key, val in subs.items():
        out = out.replace("__" + key + "__", val)
    return out


_PYPROJECT = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "viva-__NAME__"
version = "0.1.0"
description = "A vivarium-workbench plugin."
requires-python = ">=3.11"
dependencies = [
    "vivarium-workbench @ git+https://github.com/vivarium-collective/vivarium-workbench@main",
    "fastapi",
]

[project.optional-dependencies]
dev = ["pytest"]

[project.entry-points."vivarium_workbench.plugins"]
__NAME__ = "__PKG__.plugin:__CLASS__"

[tool.setuptools]
packages = ["__PKG__"]

[tool.setuptools.package-data]
__PKG__ = ["static/*"]
"""

_INIT = '"""The __TITLE__ workbench plugin."""\n'

_API = '''\
"""HTTP routes for the __NAME__ plugin.

Every route here is served under ``/api/plugins/__NAME__/`` — the loader namespaces
it, so you never hardcode that prefix.
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
def ping():
    """GET /api/plugins/__NAME__/ping"""
    return {"ok": True, "plugin": "__NAME__"}
'''

_PLUGIN = '''\
"""The __NAME__ plugin — wires its router, static assets, and panel into the workbench."""
from vivarium_workbench.plugins import WorkbenchPlugin

from __PKG__.api import router


class __CLASS__(WorkbenchPlugin):
    name = "__NAME__"

    def register(self, reg):
        reg.api_router(router)        # routes → /api/plugins/__NAME__/*
        reg.static_dir("static")      # assets → /plugins/__NAME__/*
        reg.ui("static/plugin.json")  # panels + assets to load (below)
'''

_PLUGIN_JSON = """\
{
  "css": ["panel.css"],
  "js": ["panel.js"],
  "panels": [
    {
      "id": "__NAME__-home",
      "title": "__TITLE__",
      "icon": "graph",
      "group": "__GROUP__",
      "side": "left",
      "entry": "__ENTRY__"
    }
  ]
}
"""

_PANEL_JS = """\
// The panel body renderer. The loader calls it with a context (looked up lazily,
// so script-load order does not matter):
//   ctx.el              the panel body element to render into
//   ctx.name            "__NAME__"
//   ctx.api(path, opts) fetch JSON under /api/plugins/__NAME__
// Full contract: docs/plugins.md in vivarium-workbench.
window.__ENTRY__ = function (ctx) {
  ctx.el.textContent = "Loading\\u2026";
  ctx.api("/ping").then(function (data) {
    ctx.el.textContent = "__TITLE__ plugin is live: " + JSON.stringify(data);
  }).catch(function (e) {
    ctx.el.textContent = "__TITLE__ plugin error: " + e;
  });
};
"""

_PANEL_CSS = "/* styles for the __NAME__ plugin panel */\n"

_TEST = '''\
"""The __NAME__ plugin registers its router, static dir, and UI manifest."""
from vivarium_workbench.plugins import PluginRegistry

from __PKG__.plugin import __CLASS__


def test___TESTMOD___registers():
    plugin = __CLASS__()
    assert plugin.name == "__NAME__"
    reg = PluginRegistry(plugin)
    plugin.register(reg)
    c = reg.contributions()
    assert c.routers
    assert c.static_dir and c.static_dir.is_dir()
    assert c.ui_manifest and c.ui_manifest.is_file()
'''

_README = """\
# viva-__NAME__

A [vivarium-workbench](https://github.com/vivarium-collective/vivarium-workbench)
plugin.

## Install & enable

```bash
pip install -e ".[dev]"
```

Then enable it for a workspace — add to that workspace's `workspace.yaml`:

```yaml
plugins: [__NAME__]
```

(or `export VIVARIUM_WORKBENCH_PLUGINS=__NAME__`). Serve the workspace and the
**__TITLE__** panel appears in the left rail.

## Develop

```bash
pytest
```

Layout:
- `__PKG__/api.py` — HTTP routes, served under `/api/plugins/__NAME__/`
- `__PKG__/plugin.py` — the plugin object (`register()`)
- `__PKG__/static/` — `plugin.json` manifest + `panel.js` / `panel.css`

See `docs/plugins.md` in vivarium-workbench for the full authoring guide.
"""


def scaffold_plugin(dest, name: str) -> Path:
    """Write a runnable plugin skeleton named ``name`` into directory ``dest``.

    ``name`` must be url-safe (``^[a-z0-9][a-z0-9_-]*$``). ``dest`` must not be an
    existing non-empty directory. Returns ``dest``.
    """
    dest = Path(dest)
    if not _NAME_RE.match(name or ""):
        raise ValueError(
            f"invalid plugin name {name!r}: must match {_NAME_RE.pattern} "
            "(lowercase letters, digits, '-' and '_'; not leading '-'/'_')"
        )
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"destination {dest} already exists and is not empty")

    subs = _derive(name)
    pkg = subs["PKG"]
    static = dest / pkg / "static"
    static.mkdir(parents=True, exist_ok=True)
    (dest / "tests").mkdir(parents=True, exist_ok=True)

    files = {
        dest / "pyproject.toml": _PYPROJECT,
        dest / pkg / "__init__.py": _INIT,
        dest / pkg / "api.py": _API,
        dest / pkg / "plugin.py": _PLUGIN,
        static / "plugin.json": _PLUGIN_JSON,
        static / "panel.js": _PANEL_JS,
        static / "panel.css": _PANEL_CSS,
        dest / "tests" / f"test_{subs['TESTMOD']}.py": _TEST,
        dest / "README.md": _README,
    }
    for path, template in files.items():
        path.write_text(_render(template, subs))
    return dest
