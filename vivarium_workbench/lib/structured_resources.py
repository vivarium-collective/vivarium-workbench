"""Reader for a workspace's ``structured_resources`` vg_data manifest.

Powers the **Resources tab**: a workspace may ship a curated manifest of
structured scientific resources (lipid structures, reaction pathways, paper
references, imported engines) at::

    <ws_root>/viva_genesis/data/vg_data/structured_resources.yaml

(a ``vg_data`` manifest). This module loads that file, validates it minimally,
and returns the resources **grouped by their ``type``** plus a lookup of the
``paper-reference`` entries **by id** — so the renderer can resolve each
``references: [<id>]`` elsewhere in the file into a cross-linked citation card.

Pure and self-contained: stdlib + ``yaml`` only, no workspace import, no
``build_core``. Best-effort throughout — a missing file returns ``None`` (so
the tab simply doesn't appear), and a malformed entry is skipped rather than
raising, so a hand-edited manifest never breaks the dashboard payload.

The canonical type order below drives rendering; any unknown ``type`` is kept
and appended after the known ones (alphabetically), so the manifest can grow
new resource kinds without a reader change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

# Where a workspace ships the manifest, relative to its root. The first form is
# the canonical ``viva_genesis`` layout from the brief; the glob is a tolerant
# fallback for a workspace whose package is spelled differently but still files
# the manifest under ``<pkg>/data/vg_data/``.
_PRIMARY_REL = Path("viva_genesis") / "data" / "vg_data" / "structured_resources.yaml"
_GLOB_REL = "*/data/vg_data/structured_resources.yaml"

# Render order for the grouped output. Known types first, in this order; any
# other type the manifest carries is appended after these (sorted), never
# dropped.
TYPE_ORDER: tuple[str, ...] = (
    "lipid-structure",
    "reaction-pathway",
    "evidence-dataset",
    "imported-engine",
    "paper-reference",
)

_REFERENCE_TYPE = "paper-reference"


def manifest_path(ws_root: Path) -> Optional[Path]:
    """Resolve the structured-resources manifest file for a workspace, or
    ``None`` if the workspace ships none.

    Prefers the canonical ``viva_genesis/data/vg_data/structured_resources.yaml``;
    falls back to the first ``*/data/vg_data/structured_resources.yaml`` match so
    a differently-named package still works. Never raises.
    """
    ws_root = Path(ws_root)
    primary = ws_root / _PRIMARY_REL
    try:
        if primary.is_file():
            return primary
    except OSError:
        pass
    try:
        for cand in sorted(ws_root.glob(_GLOB_REL)):
            if cand.is_file():
                return cand
    except OSError:
        pass
    return None


def _group_resources(
    resources: list[Any],
) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Group a flat resource list by ``type`` and index ``paper-reference``
    entries by ``id``.

    Each resource must be a mapping carrying a non-empty ``id`` and ``type`` —
    anything else is skipped (best-effort). Returns ``(groups, refs_by_id)``.
    """
    groups: dict[str, list[dict]] = {}
    refs_by_id: dict[str, dict] = {}
    for entry in resources:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("id")
        rtype = entry.get("type")
        if not isinstance(rid, str) or not rid.strip():
            continue
        if not isinstance(rtype, str) or not rtype.strip():
            continue
        rtype = rtype.strip()
        groups.setdefault(rtype, []).append(entry)
        if rtype == _REFERENCE_TYPE:
            refs_by_id[rid] = entry
    return groups, refs_by_id


def _ordered_types(present: list[str]) -> list[str]:
    """Known types first (in :data:`TYPE_ORDER`), then any remaining present
    types sorted alphabetically. Only types actually present are returned."""
    present_set = set(present)
    ordered = [t for t in TYPE_ORDER if t in present_set]
    extras = sorted(t for t in present_set if t not in TYPE_ORDER)
    return ordered + extras


def load_structured_resources(ws_root: Path) -> Optional[dict]:
    """Load the workspace's structured-resources manifest, grouped by type.

    Returns ``None`` when the workspace ships no manifest, when the file can't
    be read/parsed, or when it carries no valid resources — in every one of
    those cases the Resources tab simply does not appear.

    On success returns a dict shaped for the renderer::

        {
          "name": str,            # manifest name (or the file stem)
          "version": str,         # manifest version, as a string
          "description": str,     # manifest-level description
          "evidence_dataset_qa_rule": str,  # one-time QA-rule banner (may be "")
          "type_order": [str],    # types present, in canonical render order
          "groups": {type: [resource, ...]},   # resources grouped by `type`
          "references_by_id": {id: paper-reference},  # citation lookup
          "counts": {type: int},  # per-type resource count
          "n_resources": int,     # total valid resources
        }

    Never raises.
    """
    path = manifest_path(ws_root)
    if path is None:
        return None

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None

    resources = raw.get("resources")
    if not isinstance(resources, list) or not resources:
        return None

    groups, refs_by_id = _group_resources(resources)
    if not groups:
        return None

    type_order = _ordered_types(list(groups.keys()))
    counts = {t: len(groups[t]) for t in type_order}

    name = raw.get("name")
    version = raw.get("version")
    description = raw.get("description")
    qa_rule = raw.get("evidence_dataset_qa_rule")

    return {
        "name": str(name) if name else path.stem,
        "version": str(version) if version is not None else "",
        "description": str(description) if description else "",
        "evidence_dataset_qa_rule": str(qa_rule) if qa_rule else "",
        "type_order": type_order,
        "groups": groups,
        "references_by_id": refs_by_id,
        "counts": counts,
        "n_resources": sum(counts.values()),
    }
