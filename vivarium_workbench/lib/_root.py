"""Workspace-root accessor used by ``vivarium_workbench.lib.*`` helpers.

After extraction from ``pbg-template/template/scripts/_lib/_root.py``, this
module no longer walks up from ``__file__`` to find ``workspace.yaml`` (the
installed package lives in a venv, not inside the workspace). Instead, the
server / CLI entry point calls :func:`set_workspace_root` once and the
helpers read it from there.

For backward compatibility ``workspace_root()`` falls back to walking up
from CWD if no explicit root was registered.
"""
from __future__ import annotations
import contextvars
from pathlib import Path
from typing import Optional

from .workspace_paths import WorkspacePaths

_WS_ROOT: Optional[Path] = None
_WS_PATHS: Optional[WorkspacePaths] = None

# Per-request workspace root (slice 2 of the multi-workspace refactor).
#
# ``_WS_ROOT`` is the *process default* (one workspace, set at boot). This
# ContextVar is the *per-request* override the HTTP layer sets from the request's
# session (session-registry) — request-scoped, so concurrent sessions on
# different workspaces don't collide the way one process-global root does. When
# unset (serve-time render, the CLI, a detached run subprocess, any cookie-less
# client) resolution falls through to ``_WS_ROOT`` exactly as before.
#
# This is the M1 mechanism that makes the ~80 existing ``workspace_root()`` reads
# per-request-correct without threading ``ws_root`` through 75+ call sites;
# explicit threading of hot modules can follow as incremental cleanup. See
# docs/session-registry.md §7 and REFACTOR-PLAN §2A.6.
_REQUEST_WS_ROOT: contextvars.ContextVar[Optional[Path]] = contextvars.ContextVar(
    "vw_request_ws_root", default=None
)


def set_workspace_root(path: Path | str) -> None:
    """Register the active workspace root. Call once at server startup."""
    global _WS_ROOT, _WS_PATHS
    _WS_ROOT = Path(path).resolve()
    _WS_PATHS = None  # invalidate cached layout


def get_workspace_root() -> Optional[Path]:
    """Return the explicitly-registered *process-default* workspace root, or None.

    This is the boot-time global, NOT the per-request root — callers wanting the
    request's effective workspace use :func:`workspace_root`.
    """
    return _WS_ROOT


def set_request_workspace_root(path: Path | str) -> "contextvars.Token":
    """Set the per-request workspace root; returns a token to reset with.

    The HTTP layer calls this at the start of a request (from the session's
    resolved workspace) and :func:`reset_request_workspace_root` at the end.
    """
    return _REQUEST_WS_ROOT.set(Path(path).resolve())


def reset_request_workspace_root(token: "contextvars.Token") -> None:
    """Clear the per-request workspace root (pair with the ``set`` token)."""
    _REQUEST_WS_ROOT.reset(token)


def workspace_root() -> Path:
    """Return the effective workspace root for the current request/context.

    Resolution order:
      0. the per-request root (:func:`set_request_workspace_root`), when set
      1. value set via :func:`set_workspace_root` (the process default)
      2. nearest ancestor of CWD containing ``workspace.yaml``

    Raises if none yields a valid workspace.
    """
    req = _REQUEST_WS_ROOT.get()
    if req is not None:
        return req
    if _WS_ROOT is not None:
        return _WS_ROOT
    here = Path.cwd().resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "workspace.yaml").exists():
            return candidate
    raise RuntimeError(
        "no workspace root registered and workspace.yaml not found in any "
        f"ancestor of {here}; call vivarium_workbench.lib._root.set_workspace_root() "
        "or run from inside a workspace"
    )


def workspace_paths() -> WorkspacePaths:
    """Return the resolved directory layout for the active workspace.

    Reads the optional ``layout:`` map from ``workspace.yaml`` once and caches
    it; the cache is invalidated whenever :func:`set_workspace_root` is called.
    Call sites use this instead of joining literal directory names::

        from vivarium_workbench.lib._root import workspace_paths
        wp = workspace_paths()
        path = wp.studies / name          # not: workspace_root() / "studies" / name
    """
    root = workspace_root()
    # When a per-request root is active, compute fresh — the single-slot
    # ``_WS_PATHS`` cache is process-global and would race / thrash across
    # concurrent requests on different workspaces. The layout read is cheap.
    if _REQUEST_WS_ROOT.get() is not None:
        return WorkspacePaths.load(root)
    global _WS_PATHS
    if _WS_PATHS is None or _WS_PATHS.root != root:
        _WS_PATHS = WorkspacePaths.load(root)
    return _WS_PATHS
