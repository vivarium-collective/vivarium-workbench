"""Library builders for GET /api/workspace-manifest — agentic situational awareness.

Pure, ``ws_root``-parameterised functions extracted from server.py so the
FastAPI seam (``api/app.py``) can build the one-call workspace manifest without
importing the stdlib server module.

The manifest aggregates six sections into a single snapshot of workspace state
(workspace identity + git, composites, studies, registry summary, dirty-tree
health, and installed pbg-* skills) so an agent does not have to stitch together
ten separate API calls.

No imports from ``vivarium_dashboard.server`` — the stdlib server keeps thin
instance-method shims that forward into this module.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

from vivarium_dashboard.lib import git_status as _git_status
from vivarium_dashboard.lib import investigations_index as _investigations_index
from vivarium_dashboard.lib import registry as _registry


# ---------------------------------------------------------------------------
# Pure helpers (moved verbatim from server.py — no other lib copy existed)
# ---------------------------------------------------------------------------

def _add_ws_to_sys_path(ws_root: Path) -> None:
    """Make the workspace's own Python package(s) importable.

    Inline replica of server.py's ``_ws_add_to_sys_path`` (the workspace's own
    package — e.g. ``pbg_chromosome_rep1`` — lives at the workspace root, so we
    add it to ``sys.path`` so it resolves as a top-level package).
    """
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def _composite_top_pkg(rec: dict) -> str:
    """Derive a composite record's top-level package (normalized).

    A composite record carries ``module`` (its dotted Python path, e.g.
    ``v2ecoli.composites.foo`` or ``spatio_flux.composites.metabolism``) and
    sometimes ``source`` (a workspace-relative or absolute path, e.g.
    ``v2ecoli/composites/foo.composite.yaml``). The package is the first dotted
    segment of ``module``; when ``module`` is empty, fall back to the first
    path segment of ``source``. Dashes are normalized to underscores so
    ``pbg-bioreactordesign`` ↔ ``pbg_bioreactordesign`` matches the allow-list.

    Returns ``""`` when neither field yields a usable package root.
    """
    mod = str(rec.get("module") or "").strip()
    if mod:
        return mod.split(".")[0].replace("-", "_")
    src = str(rec.get("source") or "").strip()
    if src:
        segs = [s for s in src.replace("\\", "/").split("/") if s.strip()]
        # Installed-package sources are absolute paths whose package dir is the
        # segment immediately before ``composites/`` (e.g.
        # ``/…/site-packages/spatio_flux/composites/x.yaml`` → ``spatio_flux``).
        # Workspace-relative sources start at the package dir itself
        # (``v2ecoli/composites/foo.yaml`` → ``v2ecoli``). Prefer the
        # before-``composites`` segment; otherwise fall back to the first.
        for i, seg in enumerate(segs):
            if seg == "composites" and i > 0:
                return segs[i - 1].split(".")[0].replace("-", "_")
        return segs[0].split(".")[0].replace("-", "_") if segs else ""
    return ""


def filter_composites(records: list, ws_data: dict | None) -> list:
    """Apply the per-workspace registry allow-list to a list of composite dicts.

    Keeps a record when EITHER it is flagged ``workspace_local: True`` (the
    workspace's own composites are always shown) OR its top-level package (see
    :func:`_composite_top_pkg`) is in the normalized
    ``dashboard.registry.{include,modules}`` allow-list. Reuses
    ``lib.registry._registry_include_pkgs`` so dash/underscore normalization
    matches the process-registry and catalog filters.

    No-op when no allow-list is configured (``None``) → returns ``records``
    unchanged, preserving the historical "show every installed package" view.
    """
    if not isinstance(records, list):
        return records
    include = _registry._registry_include_pkgs(ws_data)
    if include is None:
        return records

    def _keep(rec: dict) -> bool:
        if not isinstance(rec, dict):
            return False
        if rec.get("workspace_local") is True:
            return True
        return _composite_top_pkg(rec) in include

    return [r for r in records if _keep(r)]


def count_viz_steps_in_state(state: dict) -> int:
    """Best-effort count of Visualization-Step entries in a composite state.

    Heuristic: a Visualization Step is any ``_type: step`` entry whose
    address matches a known Visualization class. We don't have core access
    here, so we use a name-based heuristic: address contains ``Viz`` /
    ``Plot`` / ``Heatmap`` / ``Animation`` / ``Snapshots`` /
    ``Distribution``. Best-effort - undercounts are fine; this just powers
    the manifest dashboard glance.
    """
    if not isinstance(state, dict):
        return 0
    count = 0
    for v in state.values():
        if not isinstance(v, dict):
            continue
        if v.get("_type") != "step":
            continue
        addr = v.get("address") or ""
        if re.search(r"(Viz|Plot|Heatmap|Animation|Snapshots|Distribution)",
                     addr, re.I):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def manifest_workspace_section(ws_root: Path) -> dict:
    """name, branch, commits ahead, package_path, has_origin."""
    _add_ws_to_sys_path(ws_root)
    ws = {}
    ws_path = ws_root / "workspace.yaml"
    try:
        from vivarium_dashboard.lib.workspace_yaml import load_workspace
        ws = load_workspace(ws_path)
    except Exception:
        # Fall back to raw yaml.safe_load when validation fails so the
        # manifest still surfaces basic identity for partially-formed
        # workspaces (test fixtures, migrations in progress, ...).
        try:
            ws = yaml.safe_load(ws_path.read_text(encoding="utf-8")) or {}
        except Exception:
            ws = {}
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ws_root, capture_output=True, text=True,
        ).stdout.strip() or "unknown"
    except Exception:
        branch = "unknown"
    try:
        has_origin = _git_status.has_origin_remote(ws_root)
    except Exception:
        has_origin = False
    return {
        "name":         ws.get("name", ""),
        "description":  ws.get("description", ""),
        "package_path": ws.get("package_path", ""),
        "branch":       branch,
        "has_origin":   has_origin,
    }


def manifest_composites_section(ws_root: Path) -> list:
    """One-line summary per composite: id, name, kind, module, description, viz_step_count."""
    _add_ws_to_sys_path(ws_root)
    all_comps = {}
    try:
        from vivarium_dashboard.lib.composite_lookup import discover_all_composites
        try:
            from vivarium_dashboard.lib.workspace_yaml import load_workspace
            ws = load_workspace(ws_root / "workspace.yaml")
        except Exception:
            ws = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        pkg = ws.get("package_path") or (
            "pbg_" + (ws.get("name") or "").replace("-", "_")
        )
        all_comps = discover_all_composites(ws_root, pkg)
    except Exception:
        all_comps = {}
    # Per-workspace registry allow-list (same as /api/composites): hide
    # composites from non-allow-listed packages from the manifest summary.
    # The workspace's own package is always in the include set, so its
    # composites survive the package-root check. No-op when unset.
    ws_for_filter = None
    try:
        ws_for_filter = yaml.safe_load(
            (ws_root / "workspace.yaml").read_text(encoding="utf-8")
        ) or {}
    except Exception:
        ws_for_filter = None
    if ws_for_filter is not None:
        kept = filter_composites(list(all_comps.values()), ws_for_filter)
        kept_ids = {c.get("id") for c in kept if isinstance(c, dict)}
        all_comps = {cid: c for cid, c in all_comps.items() if cid in kept_ids}
    out = []
    for cid, c in sorted(all_comps.items()):
        viz_count = count_viz_steps_in_state(c.get("state") or {})
        out.append({
            "id":              cid,
            "name":            c.get("name", ""),
            "kind":            c.get("kind", "spec"),
            "module":          c.get("module", ""),
            "description":     (c.get("description") or "")[:200],
            "viz_step_count":  viz_count,
        })
    return out


def manifest_studies_section(ws_root: Path) -> list:
    """List of studies (v3) with name, topic, status, baseline_names, n_baseline,
    n_variants, n_groups, n_interventions, n_runs, n_comparisons, conclusions_len."""
    _add_ws_to_sys_path(ws_root)
    try:
        from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError
    except Exception:
        return []
    out = []
    for d in _investigations_index._iter_study_dirs(ws_root):
        spec_path = d / "study.yaml" if (d / "study.yaml").is_file() else d / "spec.yaml"
        if not spec_path.is_file():
            continue
        try:
            spec = load_spec(spec_path)
        except (InvestigationSpecError, Exception):
            continue
        n_runs = _investigations_index._count_runs_for_study(ws_root, spec.get("name", d.name), spec)  # F2
        entry = {
            "name":             spec.get("name", d.name),
            "topic":            spec.get("topic", ""),
            "status":           spec.get("status", "draft"),
            "baseline_names":   [b.get("name", "")
                                 for b in (spec.get("baseline") or [])
                                 if isinstance(b, dict)],
            "n_baseline":       len(spec.get("baseline") or []),
            "n_variants":       len(spec.get("variants") or []),
            "n_groups":         len(spec.get("groups") or []),
            "n_interventions":  len(spec.get("interventions") or []),
            "n_runs":           n_runs,
            "n_comparisons":    len(spec.get("comparisons") or []),
            "conclusions_len":  len(spec.get("conclusions") or ""),
        }
        out.append(entry)
    return out


def manifest_registry_section(ws_root: Path) -> dict:
    """Summary of registered kinds: count per (process|step|emitter|visualization|type)."""
    try:
        data = _registry.build_registry(ws_root)
        processes = data.get("processes") or []
        by_kind = {"process": 0, "step": 0, "emitter": 0,
                   "visualization": 0, "other": 0}
        for p in processes:
            k = p.get("kind") or "other"
            by_kind[k] = by_kind.get(k, 0) + 1
        return {
            "process_count":       by_kind["process"],
            "step_count":          by_kind["step"],
            "emitter_count":       by_kind["emitter"],
            "visualization_count": by_kind["visualization"],
            "type_count":          len(data.get("types") or []),
        }
    except Exception:
        return {"process_count": 0, "step_count": 0, "emitter_count": 0,
                "visualization_count": 0, "type_count": 0}


def manifest_health_section(ws_root: Path) -> dict:
    """dirty_count + dirty file list + venv presence + python version."""
    try:
        dirty = _git_status.dirty_workspace(ws_root)
    except Exception:
        dirty = ""
    dirty_files = [line[3:] for line in dirty.splitlines() if len(line) >= 4]
    venv_py = ws_root / ".venv" / "bin" / "python3"
    return {
        "dirty_count":      len(dirty_files),
        "dirty_files":      dirty_files[:10],  # cap
        "venv_present":     venv_py.is_file(),
        "python_version":   sys.version.split()[0],
    }


def manifest_skills_section(ws_root: Path) -> list:
    """List installed pbg-* skills the agent can invoke. Reads ~/.claude/skills/."""
    skills_dir = Path.home() / ".claude" / "skills"
    if not skills_dir.is_dir():
        return []
    out = []
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir() or not d.name.startswith("pbg-"):
            continue
        skill_md = d / "SKILL.md"
        description = ""
        if skill_md.is_file():
            try:
                text = skill_md.read_text(encoding="utf-8")
            except Exception:
                text = ""
            m = re.search(r"^description:\s*(.+?)$", text, re.MULTILINE)
            if m:
                description = m.group(1).strip()
        out.append({"name": d.name, "description": description})
    return out


# ---------------------------------------------------------------------------
# Aggregator — GET /api/workspace-manifest
# ---------------------------------------------------------------------------

def workspace_manifest(ws_root: Path) -> tuple[dict, int]:
    """One-call situational awareness for agents.

    Returns a structured JSON snapshot of the workspace state without making
    the agent stitch together 10 separate API calls. Aggregates: workspace
    identity + git state, composites (kind/module), studies (status/runs/
    variants), registry summary, dirty-tree count, and available pbg-* skills.

    Always returns HTTP 200 — each section is best-effort and degrades to an
    empty default rather than failing the whole manifest.
    """
    out = {
        "workspace":  manifest_workspace_section(ws_root),
        "composites": manifest_composites_section(ws_root),
        "studies":    manifest_studies_section(ws_root),
        "registry":   manifest_registry_section(ws_root),
        "health":     manifest_health_section(ws_root),
        "skills":     manifest_skills_section(ws_root),
    }
    return out, 200
