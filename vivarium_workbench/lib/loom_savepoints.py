"""Server-side persistence for bigraph-loom **save-points**.

A save-point is a full bigraph STATE captured at one frame of a composite run,
named and stored so the user can return to it later — **View** it (load the
state back into the graph) or **rerun from it** (fork a new run seeded with that
state, via ``run_runner._apply_seed_state``). The loom also keeps save-points in
``localStorage``; these endpoints are the workspace-persisted (shareable,
survives-everything) alternative the user picks per save.

Layout: ``<ws>/.pbg/loom-savepoints/<safe-composite-id>/<point-id>.json``. One
JSON file per save-point so listing is a cheap directory scan and delete is an
``unlink``. The composite id is slugified for the directory name; the real id is
kept inside each record.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from vivarium_workbench.lib.atomic_io import atomic_write_text
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _root(ws_root: Path) -> Path:
    return WorkspacePaths.load(ws_root).pbg / "loom-savepoints"


def _safe(composite_id: str) -> str:
    """Slugify a composite id into a filesystem-safe directory name."""
    s = re.sub(r"[^A-Za-z0-9._-]", "_", (composite_id or "").strip())
    return "_unknown" if not s.strip(".") else s  # "", ".", ".." would name this directory or its parent


def _dir(ws_root: Path, composite_id: str) -> Path:
    return _root(ws_root) / _safe(composite_id)


def save(ws_root: Path, *, composite_id: str, name: str, frame: int | None,
         state: dict, n_frames: int | None = None) -> dict[str, Any]:
    """Persist a save-point; returns the stored record (including its new id)."""
    if not composite_id:
        raise ValueError("composite_id is required")
    if not isinstance(state, dict) or not state:
        raise ValueError("state must be a non-empty object")
    point_id = uuid.uuid4().hex[:12]
    record = {
        "id": point_id,
        "composite_id": composite_id,
        "name": (name or "").strip() or f"frame {frame}",
        "frame": frame,
        "n_frames": n_frames,
        "created_at": time.time(),
        "origin": "server",
        "state": state,
    }
    d = _dir(ws_root, composite_id)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_text(d / f"{point_id}.json", json.dumps(record, indent=2))
    return record


def list_points(ws_root: Path, composite_id: str) -> list[dict[str, Any]]:
    """All save-points for a composite, newest first."""
    d = _dir(ws_root, composite_id)
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for f in d.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return out


def delete(ws_root: Path, *, composite_id: str, point_id: str) -> bool:
    """Delete one save-point. Returns True if a file was removed."""
    if not point_id or "/" in point_id or "\\" in point_id or ".." in point_id:
        return False
    f = _dir(ws_root, composite_id) / f"{point_id}.json"
    if f.is_file():
        f.unlink()
        return True
    return False
