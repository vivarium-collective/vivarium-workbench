"""Write evidence-derived execution state back into study.yaml.

Touches exactly one field (the workspace's mechanical execution field) in
exactly one file per study. Never writes ``verdict``/``expert_review``/any
human axis, never downgrades on empty evidence, never writes an out-of-enum
value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from vivarium_workbench.lib.atomic_io import atomic_write_text
from vivarium_workbench.lib.investigation_members import (
    investigation_member_slugs, member_slug,
)
from vivarium_workbench.lib.study_evidence import evidence_for_study
from vivarium_workbench.lib.study_schema import (
    map_execution_value, mechanical_exec_field,
)
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _result(changed: bool, field: str, before: Any, after: Any,
            skipped: str | None) -> dict:
    return {"changed": changed, "field": field, "before": before,
            "after": after, "skipped": skipped}


def reconcile_study_status(ws_root: Path, slug: str) -> dict:
    ws_root = Path(ws_root)
    path = WorkspacePaths.load(ws_root).study_dir(slug, must_exist=True) / "study.yaml"
    study = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        field, enum = mechanical_exec_field(ws_root)
    except ValueError:
        return {"changed": False, "field": None, "before": None,
                "after": None, "skipped": "no schema"}
    current = study.get(field)

    ev = evidence_for_study(ws_root, slug)
    if ev.execution == "not_run":
        return _result(False, field, current, current, "no run evidence")
    try:
        target = map_execution_value(ev.execution, enum)
    except ValueError:
        return _result(False, field, current, current,
                       "no schema mapping for " + ev.execution)
    if current == target:
        return _result(False, field, current, current, "already fresh")

    study[field] = target
    atomic_write_text(path, yaml.safe_dump(
        study, sort_keys=False, allow_unicode=True, width=10**6))
    return _result(True, field, current, target, None)


def reconcile_investigation(ws_root: Path, inv_slug: str) -> dict:
    ws_root = Path(ws_root)
    path = (WorkspacePaths.load(ws_root).dir("investigations")
            / inv_slug / "investigation.yaml")
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    results = []
    for entry in investigation_member_slugs(spec):
        slug = member_slug(entry)
        if not slug:
            continue
        try:
            results.append({"slug": slug, **reconcile_study_status(ws_root, slug)})
        except Exception as exc:  # record, keep going
            results.append({"slug": slug, "changed": False, "skipped": None,
                            "error": f"{type(exc).__name__}: {exc}"})
    return {"studies": results}
