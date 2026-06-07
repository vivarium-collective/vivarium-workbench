# VENDORED COPY — canonical source is pbg_superpowers/investigation_inputs.py.
# Keep identical to the canonical; drift is caught by tests/test_investigation_inputs_mirror.py.
"""Resolve an investigation's owned inputs (datasets / references / expert docs)
from investigations/<slug>/inputs/ + investigation.yaml's `inputs:` block, with an
optional transitional read-through to repo-level datasets/ during migration."""
from __future__ import annotations

from pathlib import Path
import yaml

from .workspace_paths import WorkspacePaths


def investigation_inputs(ws_root: Path, slug: str, *, repo_fallback: bool = False) -> dict:
    wp = WorkspacePaths.load(Path(ws_root))
    inv_yaml = wp.dir("investigations") / slug / "investigation.yaml"
    spec = {}
    if inv_yaml.is_file():
        try:
            spec = yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            spec = {}
    block = spec.get("inputs") or {}
    out = {
        "datasets": list(block.get("datasets") or []),
        "references": list(block.get("references") or []),
        "expert_docs": list(block.get("expert_docs") or []),
        "_repo_fallback": False,
    }
    if not (out["datasets"] or out["references"] or out["expert_docs"]) and repo_fallback:
        repo_ds = Path(ws_root) / "datasets"
        if repo_ds.is_dir():
            out["datasets"] = [{"name": p.name, "path": f"datasets/{p.name}"}
                               for p in sorted(repo_ds.iterdir()) if p.is_file()]
            out["_repo_fallback"] = True
    return out
