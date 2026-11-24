"""Discover plugins (entry-points + a dev env shortcut) and resolve the enabled set.

Discovery finds *candidates*; enablement *gates* them. A plugin runs only when it is
both discovered and enabled (default: nothing enabled). See the design spec.
"""
from __future__ import annotations

import importlib
import importlib.metadata as im
import logging
import os
from collections.abc import Mapping
from pathlib import Path

import yaml

from vivarium_workbench.plugins.base import WorkbenchPlugin

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "vivarium_workbench.plugins"
#: dev/test shortcut — load an un-installed plugin by "pkg.module:ClassName" (comma-sep)
PLUGIN_MODULES_ENV = "VIVARIUM_WORKBENCH_PLUGIN_MODULES"
#: deploy allowlist of enabled plugin names (comma-sep)
PLUGINS_ENV = "VIVARIUM_WORKBENCH_PLUGINS"


def _instantiate(obj) -> WorkbenchPlugin | None:
    try:
        plugin = obj() if isinstance(obj, type) else obj
        if isinstance(plugin, WorkbenchPlugin):
            return plugin
        log.warning("plugin object %r is not a WorkbenchPlugin; skipping", obj)
    except Exception as exc:  # noqa: BLE001 - one bad plugin must not break discovery
        log.warning("failed to instantiate plugin %r: %s", obj, exc)
    return None


def discover_plugins(env: Mapping[str, str] | None = None) -> list[WorkbenchPlugin]:
    """Instantiate every plugin from the entry-point group plus the dev env list.

    Each load is isolated: a broken entry-point or module is logged and skipped.
    """
    env = os.environ if env is None else env
    found: list[WorkbenchPlugin] = []

    try:
        eps = im.entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # noqa: BLE001
        log.warning("entry-point discovery failed: %s", exc)
        eps = []
    for ep in eps:
        try:
            obj = ep.load()
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to load plugin entry-point %r: %s", getattr(ep, "name", ep), exc)
            continue
        p = _instantiate(obj)
        if p is not None:
            found.append(p)

    for spec in (s.strip() for s in env.get(PLUGIN_MODULES_ENV, "").split(",") if s.strip()):
        mod_name, _, cls_name = spec.partition(":")
        try:
            mod = importlib.import_module(mod_name)
            obj = getattr(mod, cls_name) if cls_name else mod
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to import plugin module %r: %s", spec, exc)
            continue
        p = _instantiate(obj)
        if p is not None:
            found.append(p)

    return found


def _workspace_plugins(ws_root: Path | None) -> set[str]:
    if ws_root is None:
        return set()
    wy = Path(ws_root) / "workspace.yaml"
    if not wy.exists():
        return set()
    try:
        data = yaml.safe_load(wy.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not parse %s: %s", wy, exc)
        return set()
    val = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(val, list):
        return set()
    return {str(x).strip() for x in val if isinstance(x, (str, int)) and str(x).strip()}


def enabled_plugin_names(ws_root: Path | None, env: Mapping[str, str] | None = None) -> set[str]:
    """Names enabled by the workspace's ``plugins:`` list ∪ the ``PLUGINS_ENV`` allowlist."""
    env = os.environ if env is None else env
    from_env = {s.strip() for s in env.get(PLUGINS_ENV, "").split(",") if s.strip()}
    return _workspace_plugins(ws_root) | from_env


def select_enabled(plugins: list[WorkbenchPlugin], names: set[str]) -> list[WorkbenchPlugin]:
    """Keep enabled plugins, first-wins on a duplicate ``name``."""
    out: list[WorkbenchPlugin] = []
    seen: set[str] = set()
    for p in plugins:
        if p.name not in names:
            continue
        if p.name in seen:
            log.warning("duplicate plugin name %r; keeping the first", p.name)
            continue
        seen.add(p.name)
        out.append(p)
    return out
