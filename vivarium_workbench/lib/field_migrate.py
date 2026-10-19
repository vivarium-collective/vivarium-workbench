"""Format-preserving, schema-driven migration of deprecated study fields to
their canonical replacements.

Reads the workspace's own ``.pbg/schemas/study.schema.json`` and applies ONLY
the 1:1 top-level key renames it can prove non-destructive:

* the deprecated property declares a scalar ``x-replaced-by`` that is a bare
  top-level field name (not a dotted path like ``runs.db``);
* no other deprecated property maps to the same replacement (the status
  collapse ``*_status -> execution_status`` is many-to-one, so it is skipped);
* the deprecated and replacement properties are enum-compatible (an enum-valued
  field whose vocabulary differs from its replacement, e.g.
  ``expert_review_status -> evidence_tier``, needs a value-aware migration, so
  it is skipped).

Renames happen on the raw YAML text (a regex on the top-level key line) so
comments, key ordering, and the value block are preserved byte-for-byte. The
rename is idempotent and conflict-guarded: if a file already has BOTH the old
and the new key, that field is left untouched and reported as a conflict for
manual resolution.

Fields the command deliberately does NOT touch (they need a value-aware, not a
key-rename, migration) are reported under ``excluded`` so the operator can see
what was left for a dedicated pass: the ``*_status -> execution_status``
collapse, ``expert_review_status -> evidence_tier``, and ``runs -> runs.db``.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from vivarium_workbench.lib.workspace_paths import WorkspacePaths

_BARE_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_renames_from_schema(schema: dict) -> Tuple[Dict[str, str], List[dict]]:
    """Extract provably-safe 1:1 key renames from a JSON-Schema ``properties`` map.

    Returns ``({old: new}, [{field, replacement, reason}, ...])`` — the safe
    rename map plus the deprecated fields that were excluded (with why).
    """
    props = schema.get("properties", {})
    candidates: Dict[str, str] = {}
    for key, spec in props.items():
        if not isinstance(spec, dict) or not spec.get("deprecated"):
            continue
        repl = spec.get("x-replaced-by")
        if isinstance(repl, str):
            candidates[key] = repl

    target_count = Counter(candidates.values())
    renames: Dict[str, str] = {}
    excluded: List[dict] = []
    for old, new in candidates.items():
        reason = None
        if not _BARE_FIELD.match(new):
            reason = f"replacement {new!r} is not a bare top-level field (structural change)"
        elif target_count[new] > 1:
            reason = f"{new!r} is the target of multiple deprecated fields (many-to-one collapse)"
        else:
            old_enum = (props.get(old) or {}).get("enum")
            new_enum = (props.get(new) or {}).get("enum")
            if old_enum is not None and new_enum is not None and set(old_enum) != set(new_enum):
                reason = f"enum vocabulary differs from {new!r} (value-aware migration needed)"
        if reason:
            excluded.append({"field": old, "replacement": new, "reason": reason})
        else:
            renames[old] = new
    return renames, excluded


def rename_top_level_keys(text: str, renames: Dict[str, str]) -> Tuple[str, List[str], List[str]]:
    """Rename top-level YAML keys in ``text`` (format-preserving, idempotent).

    Returns ``(new_text, changed, conflicts)`` where ``changed`` is a list of
    ``"old->new"`` applied and ``conflicts`` a list of ``"old+new"`` left
    untouched because both keys were already present.
    """
    changed: List[str] = []
    conflicts: List[str] = []
    for old, new in renames.items():
        has_old = re.search(rf"^{re.escape(old)}:", text, re.M)
        has_new = re.search(rf"^{re.escape(new)}:", text, re.M)
        if has_old and has_new:
            conflicts.append(f"{old}+{new}")
        elif has_old:
            text = re.sub(rf"^{re.escape(old)}:", f"{new}:", text, count=1, flags=re.M)
            changed.append(f"{old}->{new}")
    return text, changed, conflicts


def _authored_study_files(paths: WorkspacePaths) -> List[Path]:
    """Authored ``study.yaml`` files only — flat (``studies/<slug>/``) and nested
    (``investigations/<inv>/studies/<slug>/``). Deliberately NOT a recursive walk:
    that keeps regenerated/published copies under ``.pbg/``, ``reports/``, bundle
    output, etc. out of the migration.
    """
    files: List[Path] = []
    studies_dir = paths.dir("studies")
    if studies_dir.is_dir():
        files.extend(sorted(studies_dir.glob("*/study.yaml")))
    inv_dir = paths.dir("investigations")
    if inv_dir.is_dir():
        files.extend(sorted(inv_dir.glob("*/studies/*/study.yaml")))
    # De-dup (a layout map could alias the two) while preserving order.
    seen = set()
    unique: List[Path] = []
    for f in files:
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(f)
    return unique


def migrate_fields(ws_root: Path, dry_run: bool = False) -> dict:
    """Migrate deprecated study fields to their canonical names across a workspace.

    Schema-driven: the rename map is derived from the workspace's own
    ``.pbg/schemas/study.schema.json``. Returns a JSON-friendly summary.
    """
    paths = WorkspacePaths.load(ws_root)
    schema_path = paths.pbg / "schemas" / "study.schema.json"

    count_key = "would_migrate" if dry_run else "migrated"
    result: dict = {
        "dry_run": dry_run,
        "schema": str(schema_path),
        "renames": {},
        "excluded": [],
        count_key: 0,
        "files": [],
        "conflicts": [],
        "errors": [],
    }

    if not schema_path.is_file():
        result["errors"].append(
            {"schema": str(schema_path),
             "error": "study.schema.json not found — cannot determine renames"}
        )
        return result

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        result["errors"].append({"schema": str(schema_path), "error": f"unreadable schema: {e}"})
        return result

    renames, excluded = safe_renames_from_schema(schema)
    result["renames"] = renames
    result["excluded"] = excluded
    if not renames:
        return result  # schema declares no safe 1:1 renames — nothing to do

    for study_file in _authored_study_files(paths):
        slug = study_file.parent.name
        try:
            original = study_file.read_text(encoding="utf-8")
            new_text, changed, conflicts = rename_top_level_keys(original, renames)
            if conflicts:
                result["conflicts"].append({"study": slug, "fields": conflicts})
            if changed:
                result["files"].append({"study": slug, "renamed": changed})
                result[count_key] += 1
                if not dry_run:
                    study_file.write_text(new_text, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            result["errors"].append({"study": slug, "error": str(e)})

    return result
