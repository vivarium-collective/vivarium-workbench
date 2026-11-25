"""The workbench plugin contract.

A plugin is an in-process Python object that contributes namespaced extensions to
the workbench: a FastAPI router, a static asset dir, a UI manifest (panels + assets),
a workspace-data folder convention, and a skills dir. It does so through a
:class:`PluginRegistry` handed to :meth:`WorkbenchPlugin.register`; the registry
collects the contributions into a :class:`PluginContributions` the loader wires up.

See ``docs/superpowers/specs/2026-11-23-workbench-plugin-system-design.md``.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

WORKBENCH_PLUGIN_API_VERSION = 1


@dataclass
class PanelSpec:
    """One dockable rail panel a plugin contributes (maps 1:1 to a VivPanelDock entry)."""

    id: str
    title: str
    icon: str
    group: str
    side: str
    entry: str


@dataclass
class PluginContributions:
    """Everything one plugin registered, resolved to absolute paths."""

    name: str
    routers: list = field(default_factory=list)
    static_dir: Optional[Path] = None
    ui_manifest: Optional[Path] = None
    workspace_folder: Optional[str] = None
    workspace_reader: Optional[Callable[..., Any]] = None
    skills_dir: Optional[Path] = None
    #: routes (relative to this plugin's API base) to hide from the in-app AI agent
    ai_excluded: list = field(default_factory=list)


class WorkbenchPlugin:
    """Base class for a workbench plugin. Subclasses set ``name`` and implement
    :meth:`register`."""

    name: str = ""
    api_version: int = WORKBENCH_PLUGIN_API_VERSION

    def register(self, reg: "PluginRegistry") -> None:
        raise NotImplementedError("a WorkbenchPlugin must implement register()")

    def package_dir(self) -> Path:
        """Directory of the file defining the concrete subclass — the root that
        relative paths in :meth:`register` resolve against."""
        return Path(inspect.getfile(type(self))).resolve().parent


class PluginRegistry:
    """Handed to :meth:`WorkbenchPlugin.register`; accumulates contributions.

    Relative paths resolve against the plugin's :meth:`WorkbenchPlugin.package_dir`.
    """

    def __init__(self, plugin: WorkbenchPlugin) -> None:
        self._plugin = plugin
        self._base = plugin.package_dir().resolve()
        self._c = PluginContributions(name=plugin.name)

    def _resolve(self, path: str) -> Path:
        """Resolve a registered path against the plugin package dir, refusing one that
        escapes it (so a plugin can only serve files it ships)."""
        p = (self._base / path).resolve()
        if p != self._base and not p.is_relative_to(self._base):
            raise ValueError(
                f"plugin {self._plugin.name!r}: path {path!r} escapes the package directory"
            )
        return p

    def api_router(self, router: Any) -> None:
        self._c.routers.append(router)

    def static_dir(self, path: str) -> None:
        self._c.static_dir = self._resolve(path)

    def ui(self, manifest: str) -> None:
        self._c.ui_manifest = self._resolve(manifest)

    def workspace_store(self, folder: str, *, reader: Optional[Callable[..., Any]] = None) -> None:
        self._c.workspace_folder = folder
        self._c.workspace_reader = reader

    def skills(self, dir: str) -> None:
        self._c.skills_dir = self._resolve(dir)

    def ai_excluded(self, paths) -> None:
        """Hide these routes from the in-app AI agent. Paths are relative to this
        plugin's API base (``/api/plugins/<name>``); the loader namespaces them. Use
        for approval-sensitive or destructive plugin routes the model should not call."""
        for p in paths:
            self._c.ai_excluded.append("/" + str(p).lstrip("/"))

    def contributions(self) -> PluginContributions:
        return self._c
