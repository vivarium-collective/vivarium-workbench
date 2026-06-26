"""Metadata mutation builders — investigation/study POST endpoints.

Each builder is ws_root-parameterised and AI-free:

    (ws_root: Path, body: dict) -> tuple[dict, int]

Returns (response_dict, status_code). File side-effects only — no HTTP,
no server imports, no workstream/git operations.

The two study _for_test seams (set_study_objective / set_study_narrative)
are moved here verbatim from server.py. The server keeps name-shims for
backward compatibility with test imports.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from vivarium_dashboard.lib import investigation_status as _invstatus
from vivarium_dashboard.lib import study_spec as _study_spec_lib
from vivarium_dashboard.lib.atomic_io import atomic_write_text


# ---------------------------------------------------------------------------
# Shared constants (single-sourced here; server.py imports them if needed)
# ---------------------------------------------------------------------------

_VALID_OVERVIEW_STATUSES: frozenset[str] = frozenset(
    {"draft", "in-progress", "completed", "archived"}
)

_NARRATIVE_ALLOWED_ROOTS: frozenset[str] = frozenset({
    "report",
    "study_card",
    "biological_summary",
    "conclusion_verdicts",
    "literature_anchors",
    "design_pivot_required",
})

_NARRATIVE_ENUM_LEAVES: dict[str, frozenset[str]] = {
    "report.confidence": frozenset({"high", "medium", "low"}),
    "conclusion_verdicts.regression_compatibility.result":
        frozenset({"PASS", "FAIL", "MIXED", "PENDING"}),
    "conclusion_verdicts.biological_validation.result":
        frozenset({"PASS", "FAIL", "MIXED", "PENDING"}),
    "conclusion_verdicts.explanatory_gain.result":
        frozenset({"POSITIVE", "NEUTRAL", "NEGATIVE", "PENDING"}),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _study_name_from_body(body: dict) -> str:
    """Accept name/study/investigation keys interchangeably."""
    return (
        (body.get("name") or body.get("study") or body.get("investigation") or "")
        .strip()
    )


def _investigation_spec_path(ws_root: Path, inv_name: str) -> Path:
    """Resolve study.yaml / spec.yaml for an investigation directory."""
    inv_dir = _study_spec_lib.study_dir(ws_root, inv_name)
    sp = inv_dir / "study.yaml"
    if sp.is_file():
        return sp
    return inv_dir / "spec.yaml"


# ---------------------------------------------------------------------------
# Investigation mutation builders
# ---------------------------------------------------------------------------

def set_investigation_observables(ws_root: Path, body: dict) -> tuple[dict, int]:
    """POST /api/investigation-set-observables {investigation, paths, emit_all}

    Rewrites spec.yaml/study.yaml observables[]. The orchestrator builds the
    emitter step at run time.
    """
    inv_name = (body.get("investigation") or "").strip()
    paths = body.get("paths")
    emit_all = bool(body.get("emit_all"))
    if not inv_name:
        return {"error": "investigation required"}, 400
    if paths is None or not isinstance(paths, list):
        return {"error": "paths must be a list of arrays"}, 400
    spec_path = _investigation_spec_path(ws_root, inv_name)
    if not spec_path.is_file():
        return {"error": "investigation not found"}, 404
    spec: dict = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    if emit_all:
        spec["observables"] = [{"path": []}]
    else:
        spec["observables"] = [{"path": list(p)} for p in paths if p]
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


def set_investigation_conclusions(ws_root: Path, body: dict) -> tuple[dict, int]:
    """POST /api/investigation-set-conclusions {investigation, markdown}

    Writes spec.yaml/study.yaml conclusions. Rejects bodies over 256 KB.
    """
    inv_name = _study_name_from_body(body)
    markdown = body.get("markdown", "")
    if not inv_name:
        return {"error": "investigation required"}, 400
    if not isinstance(markdown, str):
        return {"error": "markdown must be a string"}, 400
    if len(markdown.encode("utf-8")) > 256 * 1024:
        return {"error": "conclusions exceed 256KB limit"}, 400
    spec_path = _investigation_spec_path(ws_root, inv_name)
    if not spec_path.is_file():
        return {"error": "investigation not found"}, 404
    spec: dict = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    spec["conclusions"] = markdown
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


def set_investigation_overview(ws_root: Path, body: dict) -> tuple[dict, int]:
    """POST /api/investigation-set-overview {investigation, fields:{question?,hypothesis?,status?,topic?}}

    Selectively updates the Overview metadata fields on spec.yaml/study.yaml.
    """
    inv_name = (body.get("investigation") or "").strip()
    fields: Any = body.get("fields") or {}
    if not inv_name:
        return {"error": "investigation required"}, 400
    if not isinstance(fields, dict):
        return {"error": "fields must be a mapping"}, 400
    if "status" in fields and fields["status"] not in _VALID_OVERVIEW_STATUSES:
        return {
            "error": f"status must be one of {sorted(_VALID_OVERVIEW_STATUSES)}"
        }, 400
    for key in ("question", "hypothesis", "topic"):
        if key in fields and not isinstance(fields[key], str):
            return {"error": f"{key} must be a string"}, 400
    spec_path = _investigation_spec_path(ws_root, inv_name)
    if not spec_path.is_file():
        return {"error": "investigation not found"}, 404
    spec: dict = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    for key in ("question", "hypothesis", "status", "topic"):
        if key in fields:
            spec[key] = fields[key]
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


def _write_investigation_status(ws_root: Path, inv: str, status: str) -> dict:
    """Write the status field into investigations/<inv>/investigation.yaml.

    Pure inner helper. Returns a dict with _code on failure or {ok, status}
    on success (no _code key). Caller pops _code to get (result, code).
    """
    inv = (inv or "").strip()
    status = (status or "").strip()
    valid = {"active", "in-progress", "planning", "completed", "archived", "closed"}
    if not inv:
        return {"error": "investigation required", "_code": 400}
    if status not in valid:
        return {"error": f"status must be one of {sorted(valid)}", "_code": 400}
    target: Path | None = None
    for d in _invstatus.iter_iset_dirs(ws_root):
        if d.name == inv:
            target = d / "investigation.yaml"
            break
    if target is None or not target.is_file():
        return {"error": "investigation not found", "_code": 404}
    # Prefer ruamel (round-trip preserves comments) when available; fall back
    # to safe_dump (test .venv may have no ruamel; the runtime venv does).
    try:
        from ruamel.yaml import YAML as _RYAML
        _ry = _RYAML()
        _ry.preserve_quotes = True
        _ry.width = 4096
        spec_rt = _ry.load(target.read_text(encoding="utf-8")) or {}
        spec_rt["status"] = status
        with target.open("w", encoding="utf-8") as _fh:
            _ry.dump(spec_rt, _fh)
    except Exception:
        spec_fb: dict = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        spec_fb["status"] = status
        target.write_text(yaml.safe_dump(spec_fb, sort_keys=False), encoding="utf-8")
    return {"ok": True, "status": status}


def set_investigation_status(ws_root: Path, body: dict) -> tuple[dict, int]:
    """POST /api/investigation-set-status {investigation, status}

    Writes the status field into investigations/<slug>/investigation.yaml.
    """
    inv = body.get("investigation") or ""
    status = body.get("status") or ""
    result = _write_investigation_status(ws_root, inv, status)
    code = result.pop("_code", 200)
    return result, code


# ---------------------------------------------------------------------------
# Study mutation builders (moved from server._post_study_*_for_test verbatim)
# ---------------------------------------------------------------------------

def set_study_objective(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Set study.yaml objective field. POST /api/study-set-objective.

    Moved from server._post_study_set_objective_for_test verbatim.
    """
    name = (body.get("study") or "").strip()
    text = body.get("text") or ""
    if not name:
        return {"error": "missing study"}, 400
    sf = ws_root / "studies" / name / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404
    spec: dict = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    spec["objective"] = text
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200


def set_study_narrative(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Set one v4 narrative-spine field on study.yaml at a dotted path.

    POST /api/study-narrative-set {study, path, value}

    Moved from server._post_study_narrative_set_for_test verbatim. The dotted
    path's first segment must be one of the allowlisted v4 narrative-spine
    roots. Intermediate dicts are created on demand. An empty-string or null
    value REMOVES the leaf (and prunes empty parent dicts up the chain).

    Returns (response_dict, status_code).
    """
    name = (body.get("study") or "").strip()
    path = (body.get("path") or "").strip()
    if not name:
        return {"error": "missing study"}, 400
    if not path:
        return {"error": "missing path"}, 400
    if "value" not in body:
        return {"error": "missing value"}, 400
    value = body["value"]

    parts = path.split(".")
    if not parts or not parts[0]:
        return {"error": "empty path"}, 400
    if parts[0] not in _NARRATIVE_ALLOWED_ROOTS:
        return {
            "error": (
                f"path must start with one of "
                f"{sorted(_NARRATIVE_ALLOWED_ROOTS)}, got {parts[0]!r}"
            ),
        }, 400

    # Enum guard on leaves the schema strictly enums; null/empty pass through.
    if value not in (None, "") and path in _NARRATIVE_ENUM_LEAVES:
        allowed = _NARRATIVE_ENUM_LEAVES[path]
        if value not in allowed:
            return {
                "error": (
                    f"{path}: value {value!r} not in allowed enum "
                    f"{sorted(allowed)}"
                ),
            }, 400

    sf = ws_root / "studies" / name / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec: dict = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    if not isinstance(spec, dict):
        return {"error": "study.yaml is not a mapping"}, 500

    # Walk parents, creating dicts as needed.
    cur: dict = spec
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    leaf = parts[-1]

    if value in (None, ""):
        # Clear-out path: pop the leaf, then prune empty parent dicts.
        cur.pop(leaf, None)
        for i in range(len(parts) - 1, 0, -1):
            ancestor_path = parts[:i]
            ancestor: Any = spec
            for p in ancestor_path[:-1]:
                ancestor = ancestor.get(p, {})
                if not isinstance(ancestor, dict):
                    break
            else:
                last = ancestor_path[-1]
                if last in ancestor and ancestor[last] == {}:
                    ancestor.pop(last, None)
                    continue
            break
    else:
        cur[leaf] = value

    atomic_write_text(sf, yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
    return {"ok": True}, 200


def set_study_expert_input(ws_root: Path, body: dict) -> tuple[dict, int]:
    """POST /api/study-expert-input-set {study, name, current}

    Patches one conditions.model_settings[i].current value in study.yaml.
    Legacy alias conditions.expert_inputs is still accepted on read.
    Round-trip via yaml.safe_dump (comments not preserved by design).
    """
    slug = (body or {}).get("study", "").strip()
    name = (body or {}).get("name", "").strip()
    if not slug or not name:
        return {"error": "study and name are required"}, 400
    if "current" not in (body or {}):
        return {"error": "current is required (may be null)"}, 400
    new_current = body["current"]

    spec_path = _study_spec_lib.study_spec_path(ws_root, slug)
    if not spec_path or not spec_path.is_file():
        return {"error": f"study not found: {slug}"}, 404
    try:
        spec: dict = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return {"error": f"yaml parse failed: {e}"}, 500

    cond = spec.get("conditions")
    if not isinstance(cond, dict):
        return {
            "error": "study has no v4 conditions block; cannot set model setting"
        }, 400
    # Prefer the new key; fall back to the legacy alias.
    eis_key = "model_settings" if "model_settings" in cond else "expert_inputs"
    eis = cond.get(eis_key)
    if not isinstance(eis, list):
        return {
            "error": f"conditions.{eis_key} is missing or not a list"
        }, 400

    target_ei: dict | None = None
    for ei in eis:
        if isinstance(ei, dict) and ei.get("name") == name:
            target_ei = ei
            break
    if target_ei is None:
        return {"error": f"model setting not found: {name}"}, 404

    # Optional bounds check when range is declared.
    rng = target_ei.get("range")
    if (
        isinstance(rng, list) and len(rng) == 2
        and isinstance(new_current, (int, float))
        and not isinstance(new_current, bool)
    ):
        lo, hi = rng[0], rng[1]
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            if new_current < lo or new_current > hi:
                return {
                    "error": (
                        f"value {new_current} is outside declared range [{lo}, {hi}]"
                    )
                }, 400

    target_ei["current"] = new_current
    try:
        spec_path.write_text(
            yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)
        )
    except OSError as e:
        return {"error": f"write failed: {e}"}, 500

    return {"study": slug, "name": name, "current": new_current}, 200
