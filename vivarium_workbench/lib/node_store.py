"""Load a study's typed AIG node files (RFC-0002 Phase B), keyed by node id."""
from __future__ import annotations

from pathlib import Path

import yaml

_NODE_DIRS = ("findings", "evidence", "decisions", "conclusions")


def study_dir(ws_root: Path, slug: str) -> Path | None:
    try:
        from vivarium_workbench.lib.workspace_paths import WorkspacePaths
        wp = WorkspacePaths.load(ws_root)
        d = wp.studies / slug
        if d.is_dir():
            return d
    except Exception:  # noqa: BLE001
        pass
    d = Path(ws_root) / "studies" / slug
    return d if d.is_dir() else None


def load_study_nodes(ws_root: Path, slug: str) -> dict[str, dict]:
    sdir = study_dir(ws_root, slug)
    if sdir is None:
        return {}
    nodes: dict[str, dict] = {}
    for sub in _NODE_DIRS:
        d = sdir / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            try:
                node = yaml.safe_load(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — skip malformed, never fatal
                continue
            if isinstance(node, dict) and node.get("id"):
                nodes[node["id"]] = node
    return nodes
