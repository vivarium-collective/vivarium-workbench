"""Workspace-root accessor used by ``vivarium_dashboard.lib.*`` helpers.

After extraction from ``pbg-template/template/scripts/_lib/_root.py``, this
module no longer walks up from ``__file__`` to find ``workspace.yaml`` (the
installed package lives in a venv, not inside the workspace). Instead, the
server / CLI entry point calls :func:`set_workspace_root` once and the
helpers read it from there.

For backward compatibility ``workspace_root()`` falls back to walking up
from CWD if no explicit root was registered.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

from .workspace_paths import WorkspacePaths

_WS_ROOT: Optional[Path] = None
_WS_PATHS: Optional[WorkspacePaths] = None


def set_workspace_root(path: Path | str) -> None:
    """Register the active workspace root. Call once at server startup."""
    global _WS_ROOT, _WS_PATHS
    _WS_ROOT = Path(path).resolve()
    _WS_PATHS = None  # invalidate cached layout


def get_workspace_root() -> Optional[Path]:
    """Return the explicitly-registered workspace root, or None."""
    return _WS_ROOT


def workspace_root() -> Path:
    """Return the workspace root.

    Resolution order:
      1. value set via :func:`set_workspace_root`
      2. nearest ancestor of CWD containing ``workspace.yaml``

    Raises if neither path yields a valid workspace.
    """
    if _WS_ROOT is not None:
        return _WS_ROOT
    here = Path.cwd().resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "workspace.yaml").exists():
            return candidate
    raise RuntimeError(
        "no workspace root registered and workspace.yaml not found in any "
        f"ancestor of {here}; call vivarium_dashboard.lib._root.set_workspace_root() "
        "or run from inside a workspace"
    )


def workspace_paths() -> WorkspacePaths:
    """Return the resolved directory layout for the active workspace.

    Reads the optional ``layout:`` map from ``workspace.yaml`` once and caches
    it; the cache is invalidated whenever :func:`set_workspace_root` is called.
    Call sites use this instead of joining literal directory names::

        from vivarium_dashboard.lib._root import workspace_paths
        wp = workspace_paths()
        path = wp.studies / name          # not: workspace_root() / "studies" / name
    """
    global _WS_PATHS
    root = workspace_root()
    if _WS_PATHS is None or _WS_PATHS.root != root:
        _WS_PATHS = WorkspacePaths.load(root)
    return _WS_PATHS
