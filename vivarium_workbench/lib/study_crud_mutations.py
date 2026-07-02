"""Study CRUD mutation builders — variant/baseline/intervention/run/comparison POST endpoints.

Each builder is ws_root-parameterised and AI-free:

    (ws_root: Path, body: dict) -> tuple[dict, int]

Returns (response_dict, status_code). File side-effects only — no HTTP,
no server imports, no workstream/git operations.

The 11 study-CRUD ``_for_test`` seams are moved here verbatim from server.py.
The server keeps name-shims for backward compatibility with test imports.

Batch 19 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from vivarium_workbench.lib import study_spec as _study_spec_lib


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _study_name_from_body(body: dict) -> str:
    """Extract the study/investigation identifier from a request body.

    Accepts name/study/investigation keys interchangeably (matches the
    server._study_name_from_body contract).
    """
    return (
        (body.get("name") or body.get("study") or body.get("investigation") or "")
        .strip()
    )


def _resolve_study_dir_and_sf(ws_root: Path, study: str):
    """Return (study_dir, spec_file) for the given study name."""
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_lib.study_spec_file(study_dir)
    return study_dir, sf


# ---------------------------------------------------------------------------
# Variant builders
# ---------------------------------------------------------------------------

def study_variant_add(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Add a variant entry to study.yaml. Returns (response_dict, status_code).

    Body:
      study or investigation:  <study name>
      name:                    <variant name>
      base_composite:          <baseline entry name> (required)
      parameter_overrides:     <dict>  (optional; defaults to {})
    """
    study = (body.get("study") or body.get("investigation") or "").strip()
    variant_name = (body.get("name") or "").strip()
    base_composite = (body.get("base_composite") or "").strip()
    if not study or not variant_name:
        return {"error": "missing study or variant name"}, 400
    if not base_composite:
        return {"error": "missing base_composite"}, 400
    overrides = body.get("parameter_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        return {"error": "parameter_overrides must be an object"}, 400

    # Inline ws_root-based path resolution (matches Task 5/6 pattern).
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    baseline = spec.get("baseline") or []
    baseline_names = {b.get("name") for b in baseline if isinstance(b, dict)}
    if base_composite not in baseline_names:
        return {"error": f"base_composite {base_composite!r} not in baseline"}, 404

    variants = spec.setdefault("variants", [])
    if any(v.get("name") == variant_name for v in variants if isinstance(v, dict)):
        return {"error": f"variant {variant_name!r} already exists"}, 409

    variants.append({
        "name": variant_name,
        "base_composite": base_composite,
        "parameter_overrides": overrides or {},
    })
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True, "name": variant_name}, 200


def study_variant_delete(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Remove a variant entry from study.yaml. Returns (response_dict, status_code)."""
    study = _study_name_from_body(body)
    variant_name = (body.get("variant") or "").strip()
    if not study or not variant_name:
        return {"error": "missing study or variant"}, 400
    study_dir = _study_spec_lib.study_dir(ws_root, study)
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    variants = spec.get("variants") or []
    remaining = [v for v in variants if v.get("name") != variant_name]
    if len(remaining) == len(variants):
        return {"error": f"variant {variant_name!r} not found"}, 404
    spec["variants"] = remaining
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


def study_variant_set_params(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Replace a variant's parameter_overrides. Returns (response_dict, status_code).

    Body:
      study:                <name>
      variant:              <variant name>
      parameter_overrides:  <dict>  (replaces; does not merge)
    """
    study = _study_name_from_body(body)
    variant_name = (body.get("variant") or "").strip()
    overrides = body.get("parameter_overrides")
    if not study or not variant_name:
        return {"error": "missing study or variant"}, 400
    if not isinstance(overrides, dict):
        return {"error": "parameter_overrides must be an object"}, 400

    # Inline ws_root-based path resolution (matches Task 5/6/7 pattern).
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    variants = spec.get("variants") or []
    for v in variants:
        if isinstance(v, dict) and v.get("name") == variant_name:
            v["parameter_overrides"] = dict(overrides)
            spec["variants"] = variants
            sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
            return {"ok": True}, 200
    return {"error": f"variant {variant_name!r} not found"}, 404


# ---------------------------------------------------------------------------
# Baseline builders
# ---------------------------------------------------------------------------

def study_baseline_add(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Append a composite to study.yaml.baseline[]. Returns (response_dict, status_code).

    Body:
      study:     <name>
      name:      <baseline entry name>  (unique within baseline)
      composite: <pkg.composites.x>
      params:    <dict>  (optional; defaults to {})
    """
    # Use body.get("study") directly — _study_name_from_body would pick up "name"
    # (the baseline entry name field) and misidentify it as the study name.
    study = (body.get("study") or body.get("investigation") or "").strip()
    entry_name = (body.get("name") or "").strip()
    composite = (body.get("composite") or "").strip()
    params = body.get("params")
    if not study:
        return {"error": "missing study"}, 400
    if not entry_name:
        return {"error": "missing baseline entry name"}, 400
    if not composite:
        return {"error": "missing composite"}, 400
    if params is not None and not isinstance(params, dict):
        return {"error": "params must be an object"}, 400

    # Inline ws_root-based path resolution (matches Task 5/6/7/8 pattern).
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    baseline = spec.setdefault("baseline", [])
    if any(b.get("name") == entry_name for b in baseline if isinstance(b, dict)):
        return {"error": f"baseline entry {entry_name!r} already exists"}, 409
    baseline.append({"name": entry_name, "composite": composite, "params": params or {}})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True, "name": entry_name}, 200


def study_baseline_remove(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Remove a baseline entry by name. Returns (response_dict, status_code).

    Body:
      study: <name>
      name:  <baseline entry name>

    409 if any variant has base_composite == name.
    400 if removal would leave baseline empty.
    """
    study = (body.get("study") or body.get("investigation") or "").strip()
    entry_name = (body.get("name") or "").strip()
    if not study or not entry_name:
        return {"error": "missing study or baseline entry name"}, 400

    # Inline ws_root-based path resolution.
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    baseline = spec.get("baseline") or []
    remaining = [b for b in baseline
                 if not (isinstance(b, dict) and b.get("name") == entry_name)]
    if len(remaining) == len(baseline):
        return {"error": f"baseline entry {entry_name!r} not found"}, 404

    # Check variant dependencies BEFORE checking empty — so a sole entry that is
    # referenced by a variant returns 409 (dependency) rather than 400 (empty).
    dependents: list[str] = [
        str(v.get("name")) for v in (spec.get("variants") or [])
        if isinstance(v, dict) and v.get("base_composite") == entry_name
    ]
    if dependents:
        return {
            "error": f"variants reference {entry_name!r}: {', '.join(dependents)}",
            "dependents": dependents,
        }, 409

    if not remaining:
        return {"error": "cannot leave baseline empty"}, 400

    spec["baseline"] = remaining
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


# ---------------------------------------------------------------------------
# Intervention builders
# ---------------------------------------------------------------------------

def study_intervention_add(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Append an intervention to study.yaml.interventions[]. Returns (response, code).

    Body:
      study:       <name>
      name:        <intervention name>  (unique within interventions)
      description: <freeform text>  (optional; defaults to "")
    """
    study = (body.get("study") or body.get("investigation") or "").strip()
    name = (body.get("name") or "").strip()
    description = body.get("description") or ""
    if not study or not name:
        return {"error": "missing study or intervention name"}, 400

    # Inline ws_root-based path resolution.
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    interventions = spec.setdefault("interventions", [])
    if any(i.get("name") == name for i in interventions if isinstance(i, dict)):
        return {"error": f"intervention {name!r} already exists"}, 409
    interventions.append({"name": name, "description": description})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True, "name": name}, 200


def study_intervention_update(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Update an intervention's description. Returns (response, code)."""
    study = (body.get("study") or body.get("investigation") or "").strip()
    name = (body.get("name") or "").strip()
    description = body.get("description") or ""
    if not study or not name:
        return {"error": "missing study or intervention name"}, 400

    # Inline ws_root-based path resolution.
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    for i in spec.get("interventions") or []:
        if isinstance(i, dict) and i.get("name") == name:
            i["description"] = description
            sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
            return {"ok": True}, 200
    return {"error": f"intervention {name!r} not found"}, 404


def study_intervention_delete(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Remove an intervention by name. Returns (response, code)."""
    study = (body.get("study") or body.get("investigation") or "").strip()
    name = (body.get("name") or "").strip()
    if not study or not name:
        return {"error": "missing study or intervention name"}, 400

    # Inline ws_root-based path resolution.
    studies_path = ws_root / "studies" / study
    study_dir = studies_path if studies_path.is_dir() else ws_root / "investigations" / study
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    interventions = spec.get("interventions") or []
    remaining = [i for i in interventions
                 if not (isinstance(i, dict) and i.get("name") == name)]
    if len(remaining) == len(interventions):
        return {"error": f"intervention {name!r} not found"}, 404
    spec["interventions"] = remaining
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


# ---------------------------------------------------------------------------
# Run management builders
# ---------------------------------------------------------------------------

def study_run_delete(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Remove one run from runs.db + study.yaml. Returns (response_dict, status_code)."""
    study = _study_name_from_body(body)
    run_id = (body.get("run_id") or "").strip()
    if not study or not run_id:
        return {"error": "missing study or run_id"}, 400
    study_dir = _study_spec_lib.study_dir(ws_root, study)
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    db = study_dir / "runs.db"
    if db.is_file():
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DELETE FROM runs_meta WHERE run_id = ?", (run_id,))
            has_history = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
            ).fetchone()
            if has_history:
                conn.execute("DELETE FROM history WHERE simulation_id = ?", (run_id,))
            conn.commit()
        finally:
            conn.close()

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    spec["runs"] = [r for r in (spec.get("runs") or []) if r.get("run_id") != run_id]
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


def study_runs_clear(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Remove all runs from runs.db + study.yaml. Returns (response_dict, status_code)."""
    study = _study_name_from_body(body)
    if not study:
        return {"error": "missing study"}, 400
    study_dir = _study_spec_lib.study_dir(ws_root, study)
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    db = study_dir / "runs.db"
    if db.is_file():
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DELETE FROM runs_meta")
            has_history = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
            ).fetchone()
            if has_history:
                conn.execute("DELETE FROM history")
            conn.commit()
        finally:
            conn.close()

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    spec["runs"] = []
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True}, 200


# ---------------------------------------------------------------------------
# Comparison builder
# ---------------------------------------------------------------------------

def study_comparison_add(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Add a named comparison (set of run_ids) to study.yaml['comparisons'].
    Returns (response_dict, status_code)."""
    study = _study_name_from_body(body)
    run_ids = body.get("run_ids") or []
    if not study:
        return {"error": "missing study"}, 400
    if not isinstance(run_ids, list) or len(run_ids) < 2:
        return {"error": "run_ids must be a list of at least 2 run ids"}, 400
    sf = _study_spec_lib.study_spec_file(_study_spec_lib.study_dir(ws_root, study))
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    comparisons = spec.setdefault("comparisons", [])
    name = (body.get("name") or "").strip() or f"comparison-{len(comparisons) + 1}"
    comparisons.append({"name": name, "run_ids": list(run_ids)})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"ok": True, "name": name}, 200
