"""Workbench plugin system — public surface.

Plugins are discovered via the ``vivarium_workbench.plugins`` entry-point group and
loaded (when enabled) by :func:`load_plugins`. See
``docs/superpowers/specs/2026-11-23-workbench-plugin-system-design.md``.
"""
from __future__ import annotations

from vivarium_workbench.plugins.base import (
    WORKBENCH_PLUGIN_API_VERSION,
    PanelSpec,
    PluginContributions,
    PluginRegistry,
    WorkbenchPlugin,
)

__all__ = [
    "WORKBENCH_PLUGIN_API_VERSION",
    "PanelSpec",
    "PluginContributions",
    "PluginRegistry",
    "WorkbenchPlugin",
    # re-exported lazily below to avoid import cost when only the contract is needed
    "discover_plugins",
    "enabled_plugin_names",
    "select_enabled",
    "load_plugins",
    "build_manifest",
]


def __getattr__(name: str):  # pragma: no cover - thin lazy facade
    if name in ("discover_plugins", "enabled_plugin_names", "select_enabled"):
        from vivarium_workbench.plugins import discovery

        return getattr(discovery, name)
    if name in ("load_plugins", "build_manifest"):
        from vivarium_workbench.plugins import loader

        return getattr(loader, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
