"""Schema-aware resolution of a workspace's mechanical execution field.

Workspaces differ: the canonical schema uses ``execution_status``
(proposed/planned/running/completed/failed); older ones use
``simulation_status`` (not_run/running/ran/failed). These pure helpers find
the right field + enum and map a canonical evidence value into it.
"""

from __future__ import annotations

import json
from pathlib import Path

from vivarium_workbench.lib.workspace_paths import WorkspacePaths

_CANDIDATE_FIELDS = ("execution_status", "simulation_status")

_VALUE_FALLBACKS: dict[str, tuple[str, ...]] = {
    "completed": ("completed", "ran", "done", "complete"),
    "failed": ("failed",),
    "running": ("running",),
    "not_run": ("not_run", "proposed", "planned"),
}


def mechanical_exec_field(ws_root: Path) -> tuple[str, frozenset[str]]:
    """Return ``(field_name, enum)`` of the workspace's execution-status field."""
    paths = WorkspacePaths.load(ws_root)
    schema_path = paths.pbg / "schemas" / "study.schema.json"
    if not schema_path.is_file():
        raise ValueError(f"study.schema.json not found at {schema_path}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    props = schema.get("properties") or {}
    present = [f for f in _CANDIDATE_FIELDS if isinstance(props.get(f), dict)]
    live = [f for f in present if not props[f].get("deprecated")]
    chosen = (live or present or [None])[0]
    if chosen is None:
        raise ValueError(
            f"study schema defines neither of {_CANDIDATE_FIELDS}: {schema_path}"
        )
    return chosen, frozenset(props[chosen].get("enum") or ())


def map_execution_value(canonical: str, enum: frozenset[str]) -> str:
    """Map a canonical execution value to the nearest member of ``enum``.

    Raises ``ValueError`` when no safe mapping exists (caller skips the write).
    """
    for candidate in _VALUE_FALLBACKS.get(canonical, ()):
        if candidate in enum:
            return candidate
    raise ValueError(f"no safe mapping for {canonical!r} into {sorted(enum)}")
