"""Thin shim over :mod:`viva_workspace` — the shared source of truth for
workspace-layout logic.

The full ``WorkspacePaths`` / ``find_workspace_root`` / ``study_dir`` /
``LAYOUT_DEFAULTS`` implementation that used to live here (its own duplicated
~260-line copy) now lives in the standalone ``viva-workspace`` package, which
consolidated the three divergent copies (this dashboard, viva-superpowers,
v2ecoli). This module re-exports those names so the ~126 call sites that import
``from vivarium_workbench.lib.workspace_paths import WorkspacePaths`` keep working
unchanged.

Workbench-local extra layered on top of the shared base: ``WorkspacePaths`` here
subclasses the shared one to restore the *forward-list* ``study_owner`` fallback
(scanning each ``investigation.yaml``'s ``studies:`` list via
``investigation_member_slugs``). viva-workspace deliberately dropped that fallback
to stay free of the vivarium-workbench dependency; the dashboard needs it for the
common flat-layout case where ownership is declared only on the investigation side
and the study.yaml carries no back-ref.

Behaviour note — ``study_dir(..., must_exist=False)``: the shared resolver now
RETURNS a computed canonical path for a not-yet-existing study by default instead
of raising. Call sites that relied on the old raise-when-absent semantics pass
``must_exist=True`` explicitly (see the migration audit in the adopting PR).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from viva_workspace import (
    LAYOUT_DEFAULTS,
    find_workspace_root,
    package_slug,
)
from viva_workspace import WorkspacePaths as _BaseWorkspacePaths
from viva_workspace import paths as _vw_paths
from viva_workspace.paths import LAYOUT_KEYS

from vivarium_workbench.lib.investigation_members import investigation_member_slugs
from vivarium_workbench.lib.yaml_io import load_yaml

__all__ = [
    "WorkspacePaths",
    "LAYOUT_DEFAULTS",
    "LAYOUT_KEYS",
    "find_workspace_root",
    "study_dir",
    "package_slug",
    "add_ws_to_sys_path",
]


def add_ws_to_sys_path(ws_root: Path) -> None:
    """Ensure the workspace root is on ``sys.path`` so its package is importable."""
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


class WorkspacePaths(_BaseWorkspacePaths):
    """Workbench ``WorkspacePaths``: the shared base plus the forward-list
    ``study_owner`` fallback the dashboard relies on.

    Subclassing the shared frozen dataclass adds only methods (no new fields), so
    the inherited ``load``/``from_config`` classmethods (which construct via
    ``cls(...)``) return instances of THIS subclass, keeping the override live.
    """

    def study_owner(self, slug: str) -> Optional[str]:
        """Owning investigation slug for a study: nested layout, else the
        study.yaml ``investigation:`` back-ref, else the investigation whose
        forward ``studies:`` list names this study, else None.

        The forward-list fallback matters for the common flat layout
        (``studies/<slug>/``) where ownership is declared only on the
        investigation side and the study.yaml carries no back-ref.
        """
        try:
            d = self.study_dir(slug, must_exist=True)
        except FileNotFoundError:
            return self._forward_study_owner(slug)
        try:
            parts = d.relative_to(self.dir("investigations")).parts
            if len(parts) >= 3 and parts[1] == "studies":
                return parts[0]
        except ValueError:
            pass
        sy = d / "study.yaml"
        if sy.is_file():
            data = load_yaml(sy) or {}
            owner = data.get("investigation")
            if owner:
                return owner
        return self._forward_study_owner(slug)

    def _forward_study_owner(self, slug: str) -> Optional[str]:
        """Scan each ``investigations/<inv>/investigation.yaml`` for one whose
        ``studies:`` list names ``slug``; return that investigation slug or None.
        Tolerates list items given as bare slugs or ``{study|slug: ...}`` dicts."""
        inv_root = self.dir("investigations")
        if not inv_root.is_dir():
            return None
        for inv_dir in sorted(inv_root.iterdir()):
            iy = inv_dir / "investigation.yaml"
            if not iy.is_file():
                continue
            try:
                data = load_yaml(iy) or {}
            except Exception:  # noqa: BLE001
                continue
            for st in investigation_member_slugs(data):
                st_slug = st if isinstance(st, str) else (st or {}).get("study") or (st or {}).get("slug")
                if st_slug == slug:
                    return inv_dir.name
        return None


def study_dir(
    root_or_paths,
    slug: str,
    must_exist: bool = False,
) -> Path:
    """Layout-map-aware study directory resolver (re-exported from viva-workspace,
    routed through the workbench :class:`WorkspacePaths` subclass so a passed root
    resolves with the dashboard's ``study_owner`` behaviour available).

    ``must_exist=False`` (default) returns the canonical write location for a
    not-yet-created study; ``must_exist=True`` raises ``FileNotFoundError`` when
    the slug is absent (the old workbench semantics).
    """
    paths = root_or_paths if isinstance(root_or_paths, _BaseWorkspacePaths) \
        else WorkspacePaths.load(root_or_paths)
    return paths.study_dir(slug, must_exist=must_exist)


# ``load`` memoization internals live in ``viva_workspace.paths`` now. Re-export
# the cache-clear helper directly, and proxy the live ``_parse_count`` counter via
# module ``__getattr__`` so ``workspace_paths._parse_count`` reflects the shared
# module's current value (a plain ``from ... import _parse_count`` would bind a
# stale copy).
_clear_load_cache = _vw_paths._clear_load_cache


def __getattr__(name: str):
    if name == "_parse_count":
        return _vw_paths._parse_count
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
