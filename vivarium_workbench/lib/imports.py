"""Helpers for the workspace's `imports` catalog (workspace.yaml.imports).

Shape-only — git/submodule operations live in scaffold.py and the SKILL.md flow.
"""
from __future__ import annotations
from pathlib import Path
from typing import Literal

from .workspace_yaml import load_workspace, save_workspace, WorkspaceValidationError


ImportMode = Literal["reference", "fork-source", "in-place"]


def get_import(ws_root: Path, name: str) -> dict | None:
    """Return the catalog entry for `name`, or None."""
    ws = load_workspace(ws_root / "workspace.yaml")
    return (ws.get("imports") or {}).get(name)


def list_imports(ws_root: Path) -> dict:
    """Return the imports catalog (empty dict if none registered)."""
    ws = load_workspace(ws_root / "workspace.yaml")
    return ws.get("imports") or {}


def register_import(
    ws_root: Path, *,
    name: str, source: str, ref: str, mode: ImportMode,
    path: str | None = None, description: str | None = None,
    overwrite: bool = False,
) -> None:
    """Add an entry to workspace.yaml.imports.<name>.

    Raises WorkspaceValidationError if `name` already exists and `overwrite` is False.
    Validates the resulting workspace.yaml against the schema before writing.
    """
    ws_file = ws_root / "workspace.yaml"
    ws = load_workspace(ws_file)
    imports = ws.setdefault("imports", {})
    if name in imports and not overwrite:
        raise WorkspaceValidationError(
            f"import '{name}' already registered (use overwrite=True to replace)"
        )
    entry = {"source": source, "ref": ref, "mode": mode}
    if path is not None:
        entry["path"] = path
    if description is not None:
        entry["description"] = description
    imports[name] = entry
    save_workspace(ws_file, ws)


def unregister_import(ws_root: Path, name: str) -> bool:
    """Remove an import entry. Returns True if it existed, False otherwise."""
    ws_file = ws_root / "workspace.yaml"
    ws = load_workspace(ws_file)
    imports = ws.get("imports") or {}
    if name not in imports:
        return False
    del imports[name]
    if not imports:
        ws.pop("imports", None)
    save_workspace(ws_file, ws)
    return True
