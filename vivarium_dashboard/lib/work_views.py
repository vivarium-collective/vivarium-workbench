"""Workspace/work-branch read-only builders extracted from server.py.

These are the ``ws_root``-parameterised public builders for three stateless
workspace/git read routes:

  build_pending           → GET /api/pending
  build_generation        → GET /api/generation
  build_work_composite_diff → GET /api/work-composite-diff

All three are behaviour-preserving ports of their server.py counterparts:
``_pending_entries`` / ``_serve_pending``, ``_get_generation``, and
``_get_work_composite_diff``.  No ``import server`` anywhere in this file.
"""

from __future__ import annotations

import subprocess
from typing import Any
import yaml
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helper: ws_root-aware work state reader
# ---------------------------------------------------------------------------

def _load_work_state(ws_root: Path) -> dict:
    """Return the .pbg/state.json dict for *ws_root*, or {} on any failure.

    Byte-identical to ``lib.git_status._load_work_state`` — we inline here to
    avoid a circular dependency between two lib modules.
    """
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
    import json

    state_path = WorkspacePaths.load(ws_root).pbg / "state.json"
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# build_pending
# ---------------------------------------------------------------------------

def build_pending(ws_root: Path) -> tuple[dict, int]:
    """Build the GET /api/pending payload for *ws_root*.

    Walks unmerged ``stage/*`` branches and diffs each against ``main``'s
    ``workspace.yaml`` to surface new observables, visualizations, phases,
    datasets, references_pdfs, expert_docs, and imports.

    Returns ``(payload_dict, 200)`` on success or ``({"error": ...}, 500)``
    when an unexpected exception escapes the inner git walk.

    Mirrors ``server._serve_pending`` → ``server._pending_entries``.
    """
    ws_root = Path(ws_root)
    try:
        payload = _pending_entries_impl(ws_root)
        return payload, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def _pending_entries_impl(ws_root: Path) -> dict:
    """Inner implementation of the pending-entries git walk.

    Internal errors (git failures) return ``{}`` — not an exception — so the
    route stays HTTP 200 for expected "no stage branches" / "not a git repo"
    situations, matching the legacy ``_pending_entries`` behaviour.
    """
    try:
        main_text = subprocess.run(
            ["git", "show", "main:workspace.yaml"],
            cwd=ws_root, capture_output=True, text=True, check=True,
        ).stdout
        main_ws = yaml.safe_load(main_text) or {}
    except Exception:
        return {}

    # Build uniqueness-key sets for main.
    def _key_set(items: Any, key: str) -> set:
        return {item.get(key) for item in (items or []) if isinstance(item, dict)}

    main_obs_names = _key_set(main_ws.get("observables"), "name")
    main_viz_names = _key_set(main_ws.get("visualizations"), "name")
    main_phase_ns = {p.get("n") for p in (main_ws.get("phases") or []) if isinstance(p, dict)}
    main_ds_names = _key_set(main_ws.get("datasets"), "name")
    main_pdf_keys = _key_set(main_ws.get("references_pdfs"), "bib_key")
    main_edoc_names = _key_set(main_ws.get("expert_docs"), "name")
    main_import_names = set((main_ws.get("imports") or {}).keys())

    # Get all stage/* branches.
    try:
        raw = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/stage/"],
            cwd=ws_root, capture_output=True, text=True, check=True,
        ).stdout
        stage_branches = [b.strip() for b in raw.splitlines() if b.strip()]
    except Exception:
        return {}

    pending: dict = {
        "observables": [],
        "visualizations": [],
        "phases": [],
        "datasets": [],
        "references_pdfs": [],
        "expert_docs": [],
        "imports": [],
    }

    for branch in stage_branches:
        try:
            branch_text = subprocess.run(
                ["git", "show", f"{branch}:workspace.yaml"],
                cwd=ws_root, capture_output=True, text=True, check=True,
            ).stdout
            branch_ws = yaml.safe_load(branch_text) or {}
        except Exception:
            continue

        # Find new observables.
        for item in (branch_ws.get("observables") or []):
            if isinstance(item, dict) and item.get("name") not in main_obs_names:
                pending["observables"].append({"entry": item, "branch": branch})

        # Find new visualizations.
        for item in (branch_ws.get("visualizations") or []):
            if isinstance(item, dict) and item.get("name") not in main_viz_names:
                pending["visualizations"].append({"entry": item, "branch": branch})

        # Find new phases.
        for item in (branch_ws.get("phases") or []):
            if isinstance(item, dict) and item.get("n") not in main_phase_ns:
                pending["phases"].append({"entry": item, "branch": branch})

        # Find new datasets.
        for item in (branch_ws.get("datasets") or []):
            if isinstance(item, dict) and item.get("name") not in main_ds_names:
                pending["datasets"].append({"entry": item, "branch": branch})

        # Find new reference PDFs.
        for item in (branch_ws.get("references_pdfs") or []):
            if isinstance(item, dict) and item.get("bib_key") not in main_pdf_keys:
                pending["references_pdfs"].append({"entry": item, "branch": branch})

        # Find new expert docs.
        for item in (branch_ws.get("expert_docs") or []):
            if isinstance(item, dict) and item.get("name") not in main_edoc_names:
                pending["expert_docs"].append({"entry": item, "branch": branch})

        # Find new imports.
        for imp_name, imp_val in (branch_ws.get("imports") or {}).items():
            if imp_name not in main_import_names:
                pending["imports"].append({"entry": {"name": imp_name, **imp_val}, "branch": branch})

    return pending


# ---------------------------------------------------------------------------
# build_generation
# ---------------------------------------------------------------------------

def build_generation(ws_root: Path) -> dict:
    """Build the GET /api/generation payload for *ws_root*.

    Returns ``{generation: {generation_id, git_sha, param_set_hash,
    created_at, label, n_runs}}`` or ``{generation: null}`` when no
    generation is active.  Best-effort: any exception → ``{generation: null}``
    rather than raising.

    Mirrors ``server.Handler._get_generation``.
    """
    ws_root = Path(ws_root)
    try:
        from pbg_superpowers import generation as _gen
        g = _gen.current_generation(ws_root)
    except Exception:  # noqa: BLE001
        g = None
    if g is None:
        return {"generation": None}
    return {"generation": {
        "generation_id": g.generation_id,
        "git_sha": g.git_sha,
        "param_set_hash": g.param_set_hash,
        "created_at": g.created_at,
        "label": g.label,
        "n_runs": len(g.runs),
    }}


# ---------------------------------------------------------------------------
# build_work_composite_diff
# ---------------------------------------------------------------------------

# Category mapping: a file is included only if it matches one of these path
# patterns (model code in the v2ecoli layout). Mirrors the hardcoded constant
# in server.Handler._get_work_composite_diff.
_CATEGORIES = [
    ("composites/",      "composite"),
    ("/composites/",     "composite"),
    ("processes/",       "process"),
    ("/processes/",      "process"),
    ("steps/",           "step"),
    ("/steps/",          "step"),
    ("library/",         "library helper"),
    ("/library/",        "library helper"),
    ("types/",           "type definition"),
    ("/types/",          "type definition"),
]


def build_work_composite_diff(ws_root: Path) -> dict:
    """Build the GET /api/work-composite-diff payload for *ws_root*.

    Returns ``{base, branch, changes: [{path, lines_added, lines_removed,
    category}, ...]}`` (sorted by largest diff, capped at 500 entries from
    the numstat output).  Empty changes list when the branch is at base.

    On git failure returns ``{base, branch, changes: [], error: <msg>}``.
    Always HTTP 200 (never raises).

    Mirrors ``server.Handler._get_work_composite_diff``.
    """
    ws_root = Path(ws_root)
    state = _load_work_state(ws_root)
    branch = state.get("active_branch") or ""
    if not branch:
        head = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ws_root, capture_output=True, text=True, timeout=5,
        )
        if head.returncode == 0:
            branch = head.stdout.strip()
    base = state.get("base") or "main"

    # Get numstat (per-file lines added/removed) vs the merge-base with base.
    mb = subprocess.run(
        ["git", "merge-base", base, "HEAD"],
        cwd=ws_root, capture_output=True, text=True, timeout=10,
    )
    if mb.returncode != 0:
        return {
            "base": base, "branch": branch, "changes": [],
            "error": f"merge-base failed: {(mb.stderr or mb.stdout)[:200]}",
        }
    ref = mb.stdout.strip() or base
    diff = subprocess.run(
        ["git", "diff", "--numstat", f"{ref}...HEAD"],
        cwd=ws_root, capture_output=True, text=True, timeout=15,
    )
    if diff.returncode != 0:
        return {
            "base": base, "branch": branch, "changes": [],
            "error": f"diff failed: {(diff.stderr or diff.stdout)[:200]}",
        }

    changes: list[dict] = []
    for line in diff.stdout.splitlines()[:500]:
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        try:
            a = int(added) if added != "-" else 0
            r = int(removed) if removed != "-" else 0
        except ValueError:
            continue
        cat = None
        for sub, label in _CATEGORIES:
            if sub in "/" + path:
                cat = label
                break
        if cat is None:
            continue
        changes.append({
            "path": path,
            "lines_added": a,
            "lines_removed": r,
            "category": cat,
        })

    # Sort by largest diff first (lines_added + lines_removed).
    changes.sort(key=lambda c: -(c["lines_added"] + c["lines_removed"]))
    return {"base": base, "branch": branch, "changes": changes}
