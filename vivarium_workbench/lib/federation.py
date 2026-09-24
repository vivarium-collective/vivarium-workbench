"""Read-only federation of installed ecosystem modules' content.

A "linked workspace" is an installed module that ships a workspace.yaml. It is
discovered two ways (see :func:`linked_workspaces`):
  * landed on disk at <ws_root>/external/<name>/ by the marketplace's full-repo
    install path; or
  * a declared ``workspace.yaml`` import whose installed Python package carries a
    workspace.yaml — a wheel/PyPI install that packages its studies/investigations
    inside the dist, or an editable install pointing at a repo root.
Its studies, investigation-sets, and composites are surfaced read-only in the
host workspace, each tagged with its origin repo. All helpers are best-effort: a
malformed linked workspace is skipped, never raising, so the host workspace's own
listings always render.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from vivarium_workbench.lib.workspace_paths import WorkspacePaths
from vivarium_workbench.lib.composite_lookup import discover_workspace_composites


@dataclass
class LinkedWorkspace:
    repo: str          # display name (workspace.yaml `name`, else dir name)
    root: Path         # repo root on disk
    layout: WorkspacePaths


def _repo_name(root: Path) -> str:
    try:
        data = yaml.safe_load((root / "workspace.yaml").read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("name"):
            return str(data["name"])
    except Exception:
        pass
    return root.name


def _imported_module_roots(ws_root: Path):
    """Yield the on-disk workspace root of each ``workspace.yaml`` import whose
    installed Python package carries a ``workspace.yaml`` — i.e. an installed
    module that ships its own studies/investigations/composites.

    Two install shapes are covered:
      * **wheel / PyPI** — the module force-includes its ``workspace.yaml`` +
        ``studies/`` + ``investigations/`` INSIDE the package dir, so the
        package dir itself is the workspace root.
      * **editable** — the package dir's parent is the repo root, which holds
        the ``workspace.yaml`` + ``studies/`` at top level.

    Scoped to the workspace's *declared imports* (a handful), not the whole
    installed environment, so this stays cheap — no full ``distributions()``
    walk. Best-effort: unresolvable / non-workspace imports are skipped.
    """
    import importlib.util

    try:
        data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return
    imports = data.get("imports")
    if isinstance(imports, dict):
        names = [(v or {}).get("package") or str(k) for k, v in imports.items()]
    elif isinstance(imports, list):
        names = [str(x) for x in imports]
    else:
        return

    seen_pkgs: set[str] = set()
    for raw in names:
        pkg = str(raw or "").replace("-", "_")
        if not pkg or pkg in seen_pkgs:
            continue
        seen_pkgs.add(pkg)
        try:
            spec = importlib.util.find_spec(pkg)
        except (ImportError, ValueError, ModuleNotFoundError):
            continue
        if not spec or not spec.submodule_search_locations:
            continue
        for loc in spec.submodule_search_locations:
            p = Path(loc)
            for cand in (p, p.parent):  # wheel: inside pkg; editable: repo root
                if (cand / "workspace.yaml").is_file():
                    yield cand
                    break
            else:
                continue
            break


def linked_workspaces(ws_root: Path) -> list[LinkedWorkspace]:
    ws_root = Path(ws_root).resolve()
    seen: set[Path] = {ws_root}
    seen_repos: set[str] = set()
    out: list[LinkedWorkspace] = []

    def _add(root: Path) -> None:
        root = root.resolve()
        if root in seen:
            return
        repo = _repo_name(root)
        # A module present BOTH under external/ and as an installed package
        # shares a repo name at two roots; external/ is scanned first, so the
        # installed duplicate is skipped and its studies aren't listed twice.
        if repo in seen_repos:
            return
        try:
            layout = WorkspacePaths.load(root)
        except Exception:
            return
        seen.add(root)
        seen_repos.add(repo)
        out.append(LinkedWorkspace(repo=repo, root=root, layout=layout))

    # 1. Full-repo installs landed under <ws_root>/external/<name>/.
    ext = ws_root / "external"
    if ext.is_dir():
        for child in sorted(ext.iterdir()):
            if child.is_dir() and (child / "workspace.yaml").is_file():
                _add(child)

    # 2. Installed modules (wheel or editable) that ship a workspace.yaml with
    #    studies/investigations. `external/` above wins on a duplicate root
    #    (added first, so `seen` skips the installed copy).
    for root in _imported_module_roots(ws_root):
        _add(root)

    return out


def _iter_study_specs(lw: LinkedWorkspace):
    """Yield (study_name, spec_dict) for a linked workspace's studies."""
    sdir = lw.layout.studies
    if not sdir.is_dir():
        return
    for d in sorted(p for p in sdir.iterdir() if p.is_dir()):
        f = d / "study.yaml" if (d / "study.yaml").is_file() else d / "spec.yaml"
        if not f.is_file():
            continue
        try:
            spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        name = spec.get("name") or d.name
        yield name, spec


def find_federated_study(ws_root: Path, name: str):
    """Locate a read-only study named ``name`` in a linked workspace.

    Returns ``(study_dir, LinkedWorkspace, spec_path)`` or ``None``. Matches
    on the study directory name OR the spec's ``name`` field -- the same bare
    name :func:`federated_studies` renders its card by. ``name`` may also be
    the qualified id ``<repo>::<name>`` (split on ``::`` and matched only
    within that repo). Shared by every STUDY read builder that needs to fall
    back off a host-path miss (study detail, report, grade, download) --
    mirrors ``report_views._federated_investigation_detail`` (#1164).
    Best-effort: a malformed linked workspace is skipped, never raising.
    """
    ws_root = Path(ws_root)
    repo_filter, bare = (name.split("::", 1) if "::" in name else (None, name))
    for lw in linked_workspaces(ws_root):
        if repo_filter is not None and lw.repo != repo_filter:
            continue
        try:
            sdir = lw.layout.studies
            if not sdir.is_dir():
                continue
            for d in sorted(p for p in sdir.iterdir() if p.is_dir()):
                f = d / "study.yaml" if (d / "study.yaml").is_file() else d / "spec.yaml"
                if not f.is_file():
                    continue
                try:
                    spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                except Exception:
                    continue
                if d.name == bare or spec.get("name") == bare:
                    return d, lw, f
        except Exception:
            continue
    return None


def federated_studies(ws_root: Path) -> list[dict]:
    out: list[dict] = []
    for lw in linked_workspaces(ws_root):
        try:
            for name, spec in _iter_study_specs(lw):
                out.append({
                    "name": name,
                    "id": f"{lw.repo}::{name}",
                    "origin_repo": lw.repo,
                    "read_only": True,
                    "spec": spec,
                })
        except Exception:
            continue
    return out


def federated_investigation_sets(ws_root: Path) -> list[dict]:
    out: list[dict] = []
    for lw in linked_workspaces(ws_root):
        try:
            idir = lw.layout.investigations
            if not idir.is_dir():
                continue
            for d in sorted(p for p in idir.iterdir() if p.is_dir()):
                f = d / "investigation.yaml"
                if not f.is_file():
                    continue
                try:
                    spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                except Exception:
                    continue
                name = spec.get("name") or d.name
                members = [f"{lw.repo}::{s}" for s in (spec.get("studies") or [])]
                out.append({
                    "name": name,
                    "id": f"{lw.repo}::{name}",
                    "origin_repo": lw.repo,
                    "read_only": True,
                    "spec": spec,
                    "member_studies": members,
                })
        except Exception:
            continue
    return out


def federated_composites(ws_root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for lw in linked_workspaces(ws_root):
        pkg = None
        try:
            data = yaml.safe_load((lw.root / "workspace.yaml").read_text(encoding="utf-8"))
            pkg = (data or {}).get("package_path")
        except Exception:
            pkg = None
        if not pkg:
            continue
        try:
            recs = discover_workspace_composites(lw.root, pkg)
        except Exception:
            continue
        for spec_id, rec in recs.items():
            rec = dict(rec)
            rec["origin_repo"] = lw.repo
            rec["read_only"] = True
            out.setdefault(spec_id, rec)
    return out
