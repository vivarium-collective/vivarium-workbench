"""Load enabled plugins into a FastAPI app and serve their UI manifest.

The only runtime hook core needs: :func:`load_plugins(app, ws_root)` at startup.
Everything a plugin contributes is namespaced by its ``name`` and each plugin's load
is isolated — one plugin failing never aborts startup or the others.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from vivarium_workbench.plugins.base import (
    WORKBENCH_PLUGIN_API_VERSION,
    PluginContributions,
    PluginRegistry,
    WorkbenchPlugin,
)
from vivarium_workbench.plugins.discovery import (
    discover_plugins,
    enabled_plugin_names,
    select_enabled,
)

log = logging.getLogger(__name__)

_MANIFEST_ROUTE = "/api/plugins/manifest"


def _manifest_entry(c: PluginContributions) -> dict:
    """UI manifest for one plugin: {name, css, js, panels}, asset paths rewritten
    to ``/plugins/<name>/<rel>``. A missing/invalid manifest yields empty lists."""
    base = {"name": c.name, "css": [], "js": [], "panels": []}
    if not c.ui_manifest or not c.ui_manifest.exists():
        if c.ui_manifest:
            log.warning("plugin %r: UI manifest %s not found; UI skipped", c.name, c.ui_manifest)
        return base
    try:
        data = json.loads(c.ui_manifest.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("plugin %r: UI manifest invalid (%s); UI skipped", c.name, exc)
        return base
    pfx = f"/plugins/{c.name}/"
    base["css"] = [pfx + str(x).lstrip("/") for x in data.get("css", []) or []]
    base["js"] = [pfx + str(x).lstrip("/") for x in data.get("js", []) or []]
    base["panels"] = list(data.get("panels", []) or [])
    return base


def build_manifest(contribs: list[PluginContributions]) -> list[dict]:
    """The payload served at ``/api/plugins/manifest`` (enabled plugins only)."""
    return [_manifest_entry(c) for c in contribs]


def _ensure_manifest_route(app: FastAPI) -> None:
    if any(getattr(r, "path", None) == _MANIFEST_ROUTE for r in app.router.routes):
        return

    def plugins_manifest() -> list[dict]:
        return build_manifest(getattr(app.state, "plugin_contribs", []))

    app.add_api_route(_MANIFEST_ROUTE, plugins_manifest, methods=["GET"])


def _wire(app: FastAPI, c: PluginContributions) -> None:
    for router in c.routers:
        app.include_router(router, prefix=f"/api/plugins/{c.name}")
    if c.static_dir:
        if c.static_dir.is_dir():
            app.mount(f"/plugins/{c.name}", StaticFiles(directory=str(c.static_dir)), name=f"plugin-{c.name}")
        else:
            log.warning("plugin %r: static dir %s missing; assets skipped", c.name, c.static_dir)


def load_plugins(
    app: FastAPI,
    ws_root: Optional[Path],
    *,
    discovered: Optional[list[WorkbenchPlugin]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> list[PluginContributions]:
    """Discover, gate by the enabled set, register + wire each — fail-soft."""
    plugins = discover_plugins(env) if discovered is None else list(discovered)
    names = enabled_plugin_names(ws_root, env)
    enabled = select_enabled(plugins, names)

    contribs: list[PluginContributions] = list(getattr(app.state, "plugin_contribs", []))
    for p in enabled:
        try:
            if p.api_version != WORKBENCH_PLUGIN_API_VERSION:
                log.warning(
                    "plugin %r targets API v%s (core v%s); skipping",
                    p.name, p.api_version, WORKBENCH_PLUGIN_API_VERSION,
                )
                continue
            reg = PluginRegistry(p)
            p.register(reg)
            c = reg.contributions()
            _wire(app, c)
            contribs.append(c)
            log.info("loaded plugin %r", p.name)
        except Exception as exc:  # noqa: BLE001 - fail-soft: isolate one plugin's failure
            log.warning("plugin %r failed to load: %s", getattr(p, "name", p), exc)

    app.state.plugin_contribs = contribs
    _ensure_manifest_route(app)
    return contribs
