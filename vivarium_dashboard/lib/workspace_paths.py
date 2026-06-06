"""Canonical resolution of a workspace's directory layout.

Every workspace has a set of well-known directories — ``studies/``,
``investigations/``, ``composites/``, ``references/``, ``.pbg/``, the Python
package, etc. Historically each of these names was hardcoded as a string
literal at ~150 call sites across the dashboard, the pbg-superpowers skills,
and ``lint-workspace.py``. This module is the single place that knows the
layout, so the physical location of any directory can be changed in one spot
(an optional ``layout:`` map in ``workspace.yaml``) instead of everywhere.

Backward compatibility: a key left out of ``layout:`` falls back to the
conventional flat name (``studies`` -> ``studies/``). A workspace with no
``layout:`` block at all therefore keeps the classic top-level layout, so all
existing workspaces are unaffected.

Example ``workspace.yaml`` to nest research dirs under ``workspace/``::

    layout:
      studies: workspace/studies
      investigations: workspace/investigations
      composites: workspace/composites
      references: workspace/references
      datasets: workspace/datasets
      notes: workspace/notes
      experiments: workspace/experiments
      reports: workspace/reports
      pbg: workspace/.pbg
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

import yaml

# The canonical flat layout — the single source of truth for directory names.
# Keys are logical names used throughout the codebase; values are the default
# workspace-root-relative paths. The Python package (`package`) is special: it
# derives from `package_path`/`name` in workspace.yaml, so it has no fixed
# default here.
LAYOUT_DEFAULTS: dict[str, str] = {
    "studies": "studies",
    "investigations": "investigations",
    "composites": "composites",
    "references": "references",
    "datasets": "datasets",
    "notes": "notes",
    "experiments": "experiments",
    "reports": "reports",
    "pbg": ".pbg",
    "scripts": "scripts",
    "tests": "tests",
    "docs": "docs",
}

# Logical names a workspace may override via `layout:` (`package` is normally
# set through `package_path`, but may also be relocated via `layout`).
LAYOUT_KEYS = tuple(LAYOUT_DEFAULTS) + ("package",)


def package_slug(name: str | None) -> str:
    """Default Python package directory for a workspace named `name`."""
    return f"pbg_{(name or 'workspace').replace('-', '_')}"


@dataclass(frozen=True)
class WorkspacePaths:
    """Resolved directory layout for a single workspace.

    Construct via :meth:`load` (reads ``workspace.yaml``) or :meth:`from_config`
    (caller supplies the parsed dict). Access directories by attribute
    (``wp.studies``) or by name (``wp.dir("studies")``). Subpaths are formed by
    joining onto the result, e.g. ``wp.pbg / "schemas"`` or
    ``wp.reports / "figures" / study``.
    """

    root: Path
    _layout: Mapping[str, str]

    @classmethod
    def from_config(cls, root: Path | str, config: Optional[Mapping] = None) -> "WorkspacePaths":
        config = dict(config or {})
        layout = dict(LAYOUT_DEFAULTS)
        # Package directory: explicit package_path wins, else derive from name.
        layout["package"] = config.get("package_path") or package_slug(config.get("name"))
        # Apply explicit per-directory overrides.
        overrides = config.get("layout") or {}
        for key, value in overrides.items():
            if key in LAYOUT_KEYS and isinstance(value, str) and value:
                layout[key] = value
        return cls(Path(root).resolve(), layout)

    @classmethod
    def load(cls, root: Path | str) -> "WorkspacePaths":
        """Resolve layout from ``<root>/workspace.yaml`` (empty if missing)."""
        root = Path(root)
        wf = root / "workspace.yaml"
        config: dict = {}
        if wf.exists():
            config = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        return cls.from_config(root, config)

    def dir(self, name: str) -> Path:
        """Absolute path to the directory registered under logical `name`."""
        if name not in self._layout:
            raise KeyError(f"unknown workspace directory: {name!r}")
        return self.root / self._layout[name]

    def rel(self, name: str) -> str:
        """Workspace-root-relative path string for logical `name`."""
        return self._layout[name]

    # Convenience accessors -------------------------------------------------
    @property
    def studies(self) -> Path: return self.dir("studies")
    @property
    def investigations(self) -> Path: return self.dir("investigations")
    @property
    def composites(self) -> Path: return self.dir("composites")
    @property
    def references(self) -> Path: return self.dir("references")
    @property
    def datasets(self) -> Path: return self.dir("datasets")
    @property
    def notes(self) -> Path: return self.dir("notes")
    @property
    def experiments(self) -> Path: return self.dir("experiments")
    @property
    def reports(self) -> Path: return self.dir("reports")
    @property
    def pbg(self) -> Path: return self.dir("pbg")
    @property
    def scripts(self) -> Path: return self.dir("scripts")
    @property
    def tests(self) -> Path: return self.dir("tests")
    @property
    def docs(self) -> Path: return self.dir("docs")
    @property
    def package(self) -> Path: return self.dir("package")

    # Study resolution (investigation-centric structure) --------------------
    def iter_study_dirs(self):
        """Yield every study dir - nested investigations/<inv>/studies/<s>/ first,
        then legacy flat studies/<s>/. A dir is a study iff it holds study.yaml.
        Nested wins on slug collision."""
        seen: set[str] = set()
        inv_root = self.dir("investigations")
        if inv_root.is_dir():
            for inv in sorted(p for p in inv_root.iterdir() if p.is_dir()):
                sroot = inv / "studies"
                if sroot.is_dir():
                    for s in sorted(p for p in sroot.iterdir() if p.is_dir()):
                        if (s / "study.yaml").is_file() and s.name not in seen:
                            seen.add(s.name)
                            yield s
        flat = self.dir("studies")
        if flat.is_dir():
            for s in sorted(p for p in flat.iterdir() if p.is_dir()):
                if (s / "study.yaml").is_file() and s.name not in seen:
                    seen.add(s.name)
                    yield s

    def study_dir(self, slug: str) -> Path:
        """Resolve a study by slug, nested-first then flat. Raises if absent."""
        for s in self.iter_study_dirs():
            if s.name == slug:
                return s
        raise FileNotFoundError(f"study {slug!r} not found under {self.root}")

    def study_owner(self, slug: str):
        """Owning investigation slug for a study (nested layout), else the
        study.yaml investigation: back-ref, else None."""
        try:
            d = self.study_dir(slug)
        except FileNotFoundError:
            return None

