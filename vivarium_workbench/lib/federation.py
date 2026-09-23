"""Read-only federation of installed ecosystem modules' content.

A "linked workspace" is an installed module that ships a workspace.yaml — landed
on disk at <ws_root>/external/<name>/ by the marketplace's full-repo install
path (or editable-installed to a repo root). Its studies, investigation-sets,
and composites are surfaced read-only in the host workspace, each tagged with
its origin repo. All helpers are best-effort: a malformed linked workspace is
skipped, never raising, so the host workspace's own listings always render.
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


def linked_workspaces(ws_root: Path) -> list[LinkedWorkspace]:
    ws_root = Path(ws_root).resolve()
    seen: set[Path] = {ws_root}
    out: list[LinkedWorkspace] = []
    ext = ws_root / "external"
    if ext.is_dir():
        for child in sorted(ext.iterdir()):
            if not child.is_dir() or not (child / "workspace.yaml").is_file():
                continue
            root = child.resolve()
            if root in seen:
                continue
            try:
                layout = WorkspacePaths.load(root)
            except Exception:
                continue
            seen.add(root)
            out.append(LinkedWorkspace(repo=_repo_name(root), root=root, layout=layout))
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
