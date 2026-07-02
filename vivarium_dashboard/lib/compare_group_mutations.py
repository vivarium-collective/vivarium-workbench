"""Investigation comparison & group POST mutation builders.

Pure builders for the four comparison/group write endpoints:

    (ws_root: Path, body: dict) -> tuple[dict, int]

File side-effects only — no HTTP, no server imports, no git operations.
Mutations mirror the ``do_action`` closures from the legacy
``_post_investigation_comparison_add/update`` and
``_post_investigation_group_add/update`` server handlers verbatim, re-doing
the validation so the FastAPI route gets the correct 400/404/409 responses
without going through ``_commit_or_run``.

Batch 22 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _resolve_spec_path(ws_root: Path, inv_name: str) -> "Path | None":
    """Return the spec/study.yaml path for an investigation, or None if not found."""
    from vivarium_dashboard.lib import study_spec as _study_spec

    inv_dir = _study_spec.study_dir(ws_root, inv_name)
    sp = (
        (inv_dir / "study.yaml")
        if (inv_dir / "study.yaml").is_file()
        else (inv_dir / "spec.yaml")
    )
    return sp if sp.is_file() else None


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def comparison_add(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-comparison-add.

    Appends a comparison entry to ``spec.yaml.comparisons``.

    Body:
        investigation / study: required, the investigation slug.
        name:                  required, the comparison name.
        variants:              required, non-empty list.
        observables:           required, non-empty list.
        description:           optional string (defaults to ``""``).

    Status codes:
        400 — missing/invalid fields.
        404 — investigation not found.
        409 — comparison name already exists.
        200 — appended; returns ``{ok: True}``.
    """
    inv_name = (body.get("investigation") or body.get("study") or "").strip()
    cmp_name = (body.get("name") or "").strip()
    variants = body.get("variants") or []
    observables = body.get("observables") or []
    description = body.get("description", "")
    if not inv_name:
        return {"error": "investigation required"}, 400
    if not cmp_name:
        return {"error": "name required"}, 400
    if not isinstance(variants, list) or not variants:
        return {"error": "variants must be a non-empty list"}, 400
    if not isinstance(observables, list) or not observables:
        return {"error": "observables must be a non-empty list"}, 400
    if not isinstance(description, str):
        return {"error": "description must be a string"}, 400

    spec_path = _resolve_spec_path(ws_root, inv_name)
    if spec_path is None:
        return {"error": "investigation not found"}, 404

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    cmps = list(spec.get("comparisons") or [])
    if any(c.get("name") == cmp_name for c in cmps):
        return {"error": f"comparison {cmp_name!r} already exists"}, 409
    cmps.append({
        "name": cmp_name,
        "description": description,
        "variants": list(variants),
        "observables": list(observables),
    })
    spec["comparisons"] = cmps
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


def comparison_update(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-comparison-update.

    Replaces fields on an existing comparison entry.  ``name`` is immutable;
    only ``description``, ``variants``, and ``observables`` can be updated.

    Body:
        investigation:   required, the investigation slug.
        name:            required, the comparison to update.
        fields_to_update: optional mapping of fields to replace.

    Status codes:
        400 — missing/invalid fields.
        404 — investigation not found; OR comparison not found.
        200 — updated; returns ``{ok: True}``.
    """
    inv_name = (body.get("investigation") or "").strip()
    cmp_name = (body.get("name") or "").strip()
    fields = body.get("fields_to_update") or {}
    if not inv_name:
        return {"error": "investigation required"}, 400
    if not cmp_name:
        return {"error": "name required"}, 400
    if not isinstance(fields, dict):
        return {"error": "fields_to_update must be a mapping"}, 400

    spec_path = _resolve_spec_path(ws_root, inv_name)
    if spec_path is None:
        return {"error": "investigation not found"}, 404

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    cmps = spec.get("comparisons") or []
    idx = next((i for i, c in enumerate(cmps) if c.get("name") == cmp_name), None)
    if idx is None:
        return {"error": f"comparison {cmp_name!r} not found"}, 404
    for key in ("description", "variants", "observables"):
        if key in fields:
            cmps[idx][key] = fields[key]
    spec["comparisons"] = cmps
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


def group_add(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-group-add.

    Appends a group entry to ``spec.yaml.groups``.  Validates that every
    entry in ``variants`` is declared in ``spec.variants``.

    Body:
        investigation: required, the investigation slug.
        name:          required, the group name.
        variants:      required, non-empty list (each must be a declared variant).
        description:   optional string (defaults to ``""``).

    Status codes:
        400 — missing/invalid fields; unknown variant references.
        404 — investigation not found.
        409 — group name already exists.
        200 — appended; returns ``{ok: True}``.
    """
    inv_name = (body.get("investigation") or "").strip()
    grp_name = (body.get("name") or "").strip()
    variants = body.get("variants") or []
    description = body.get("description", "")
    if not inv_name:
        return {"error": "investigation required"}, 400
    if not grp_name:
        return {"error": "name required"}, 400
    if not isinstance(variants, list) or not variants:
        return {"error": "variants must be a non-empty list"}, 400
    if not isinstance(description, str):
        return {"error": "description must be a string"}, 400

    spec_path = _resolve_spec_path(ws_root, inv_name)
    if spec_path is None:
        return {"error": "investigation not found"}, 404

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    declared: set[str] = {
        str(v.get("name"))
        for v in (spec.get("variants") or [])
        if isinstance(v, dict) and v.get("name") is not None
    }
    unknown = [v for v in variants if v not in declared]
    if unknown:
        return {
            "error": f"unknown variant(s): {unknown}; declared: {sorted(declared)}"
        }, 400

    grps = list(spec.get("groups") or [])
    if any(g.get("name") == grp_name for g in grps):
        return {"error": f"group {grp_name!r} already exists"}, 409
    grps.append({
        "name": grp_name,
        "description": description,
        "variants": list(variants),
    })
    spec["groups"] = grps
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


def group_update(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-group-update.

    Replaces ``description`` / ``variants`` on an existing group entry.
    ``name`` is immutable.  When ``variants`` is replaced, validates the new
    list against the declared variants.

    Body:
        investigation:   required, the investigation slug.
        name:            required, the group to update.
        fields_to_update: optional mapping of fields to replace.

    Status codes:
        400 — missing/invalid fields; variants is empty / contains unknown refs.
        404 — investigation not found; OR group not found.
        200 — updated; returns ``{ok: True}``.
    """
    inv_name = (body.get("investigation") or "").strip()
    grp_name = (body.get("name") or "").strip()
    fields = body.get("fields_to_update") or {}
    if not inv_name:
        return {"error": "investigation required"}, 400
    if not grp_name:
        return {"error": "name required"}, 400
    if not isinstance(fields, dict):
        return {"error": "fields_to_update must be a mapping"}, 400

    spec_path = _resolve_spec_path(ws_root, inv_name)
    if spec_path is None:
        return {"error": "investigation not found"}, 404

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}

    # Validate the incoming variants list (if present) BEFORE writing.
    if "variants" in fields:
        new_vars = fields["variants"]
        if not isinstance(new_vars, list) or not new_vars:
            return {"error": "variants must be a non-empty list"}, 400
        declared: set[str] = {
            str(v.get("name"))
            for v in (spec.get("variants") or [])
            if isinstance(v, dict) and v.get("name") is not None
        }
        unknown = [v for v in new_vars if v not in declared]
        if unknown:
            return {
                "error": f"unknown variant(s): {unknown}; declared: {sorted(declared)}"
            }, 400

    grps = spec.get("groups") or []
    idx = next((i for i, g in enumerate(grps) if g.get("name") == grp_name), None)
    if idx is None:
        return {"error": f"group {grp_name!r} not found"}, 404
    for key in ("description", "variants"):
        if key in fields:
            grps[idx][key] = fields[key]
    spec["groups"] = grps
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


def comparison_delete(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """DELETE /api/investigation-comparison — remove a comparison entry.

    Refuses with 409 if any visualization's ``config.comparison`` references
    this comparison. Relocated from the retired
    ``server._delete_investigation_comparison`` (minus the ``_commit_or_run``
    wrapper — the git commit is the caller's concern).

    Status codes: 400 missing fields; 404 investigation not found;
    409 still-referenced; 200 removed (``{ok: True}``).
    """
    inv_name = (body.get("investigation") or "").strip()
    cmp_name = (body.get("name") or "").strip()
    if not inv_name:
        return {"error": "investigation required"}, 400
    if not cmp_name:
        return {"error": "name required"}, 400

    spec_path = _resolve_spec_path(ws_root, inv_name)
    if spec_path is None:
        return {"error": "investigation not found"}, 404

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    dependents = [
        v.get("name", "<unnamed>")
        for v in (spec.get("visualizations") or [])
        if ((v.get("config") or {}).get("comparison") == cmp_name)
    ]
    if dependents:
        return {
            "error": f"comparison {cmp_name!r} still referenced by visualization(s): {dependents}",
            "dependents": dependents,
        }, 409

    spec["comparisons"] = [
        c for c in (spec.get("comparisons") or []) if c.get("name") != cmp_name
    ]
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


def group_delete(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """DELETE /api/investigation-group — remove a group entry.

    Relocated from the retired ``server._delete_investigation_group`` (minus the
    ``_commit_or_run`` wrapper).

    Status codes: 400 missing fields; 404 investigation OR group not found;
    200 removed (``{ok: True}``).
    """
    inv_name = (body.get("investigation") or "").strip()
    grp_name = (body.get("name") or "").strip()
    if not inv_name:
        return {"error": "investigation required"}, 400
    if not grp_name:
        return {"error": "name required"}, 400

    spec_path = _resolve_spec_path(ws_root, inv_name)
    if spec_path is None:
        return {"error": "investigation not found"}, 404

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    if not any(g.get("name") == grp_name for g in (spec.get("groups") or [])):
        return {"error": f"group {grp_name!r} not found"}, 404

    spec["groups"] = [
        g for g in (spec.get("groups") or []) if g.get("name") != grp_name
    ]
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200
