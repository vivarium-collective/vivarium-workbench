"""Study CRUD mutation builders — variant/baseline/intervention/run/comparison POST endpoints.

Each builder is ws_root-parameterised and AI-free:

    (ws_root: Path, body: dict) -> tuple[dict, int]

Returns (response_dict, status_code). File side-effects only — no HTTP,
no server imports, no workstream/git operations.

The 11 study-CRUD ``_for_test`` seams are moved here verbatim from server.py.
The tests and the FastAPI routes import these builders from here; ``server.py``
no longer has the seams.

Batch 19 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import yaml

from vivarium_workbench.lib import study_spec as _study_spec_lib
from vivarium_workbench.lib.path_safety import plain_name
from vivarium_workbench.lib.yaml_roundtrip import load_yaml_preserving

# Status vocabulary the study-detail finding cards render (glyph + color):
# confirms (✓) / partial (◐) / contradicts (✗) / novel (◆). Default: novel.
_FINDING_STATUSES = frozenset({"confirms", "partial", "contradicts", "novel"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _study_name_from_body(body: dict) -> str:
    """Extract the study/investigation identifier from a request body.

    Accepts name/study/investigation keys interchangeably (matches the
    server._study_name_from_body contract).
    """
    name = (
        (body.get("name") or body.get("study") or body.get("investigation") or "")
        .strip()
    )
    # Always joined onto the studies/investigations directory, so it must be a plain name.
    return plain_name(name, "name") if name else name


def _v4_baseline_not_yet_materialized(spec: dict) -> bool:
    """True when ``spec`` is a v4 conditions-based study whose ``baseline``
    the frontend only ever sees as a synthesized, never-persisted view.

    ``investigations._project_v4_redesign_to_legacy_view`` builds
    ``out["baseline"]`` from ``conditions.baseline`` purely in-memory,
    ONLY when the raw on-disk spec has no ``baseline`` key of its own
    (``if bl.get("composite") and not out.get("baseline")``) — the
    synthesized entry's ``name`` (the study's own name, or the literal
    string ``"baseline"`` as a last-resort fallback) is never written back.
    A caller here operates on ``spec`` freshly loaded straight from disk
    (never through that projection), so it would see no ``baseline`` key at
    all for such a study — `study_baseline_add` would then silently create
    one, orphaned from `conditions.baseline` and never kept in sync, and any
    later `study_baseline_remove` call for the "old" entry name the frontend
    displayed would 404 (that name never existed as real data). Both
    `study_baseline_add`/`study_baseline_remove` check this first and refuse
    cleanly rather than let that happen.
    """
    if spec.get("schema_version") != 4 or not isinstance(spec.get("conditions"), dict):
        return False
    if spec.get("baseline"):
        return False  # already materialized as real data — safe to edit directly
    return bool(_study_spec_lib.baseline_head(spec["conditions"].get("baseline")).get("composite"))


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

    study_dir = _study_spec_lib.study_dir(ws_root, study)
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

    study_dir = _study_spec_lib.study_dir(ws_root, study)
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
# Finding builders — write to study.yaml `findings[]` (the list the study page
# renders), NOT the node-store `/api/finding` graph model. Blocker B of #1618:
# findings/conclusions had no GUI; this backs an authoring form on the Decide
# tab.
# ---------------------------------------------------------------------------

def study_finding_add(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Append a finding to study.yaml ``findings[]``. Returns (response, code).

    Body:
      study or investigation: <study name>
      statement:              <the finding text> (required)
      status:                 confirms | partial | contradicts | novel
                              (optional; defaults to "novel")
      kind:                   <free label> (optional; defaults to "observation")
      explanation:            <why / mechanism> (optional)

    The written entry is the minimal shape the study-detail finding card
    renders: ``{id, statement, status, kind, explanation?}``. A generated
    ``id`` (``f<hex>``) lets the card anchor and lets ``study-finding-delete``
    target it.
    """
    study = _study_name_from_body(body)
    statement = (body.get("statement") or "").strip()
    status = (body.get("status") or "novel").strip() or "novel"
    kind = (body.get("kind") or "observation").strip() or "observation"
    explanation = (body.get("explanation") or "").strip()
    if not study:
        return {"error": "missing study"}, 400
    if not statement:
        return {"error": "statement is required"}, 400
    if status not in _FINDING_STATUSES:
        return {"error": f"status must be one of {sorted(_FINDING_STATUSES)}"}, 400

    study_dir = _study_spec_lib.study_dir(ws_root, study)
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    findings = spec.setdefault("findings", [])
    if not isinstance(findings, list):
        return {"error": "study.yaml findings is not a list"}, 422
    fid = "f" + uuid.uuid4().hex[:8]
    entry: dict = {"id": fid, "statement": statement, "status": status, "kind": kind}
    if explanation:
        entry["explanation"] = explanation
    findings.append(entry)
    sf.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True, "id": fid}, 200


def study_finding_delete(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Remove a finding from study.yaml ``findings[]`` by id. (response, code).

    Body: ``{study|investigation, id}``. 404 when no finding has that id.
    """
    study = _study_name_from_body(body)
    fid = (body.get("id") or "").strip()
    if not study or not fid:
        return {"error": "missing study or finding id"}, 400

    study_dir = _study_spec_lib.study_dir(ws_root, study)
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    findings = spec.get("findings") or []
    remaining = [f for f in findings
                 if not (isinstance(f, dict) and f.get("id") == fid)]
    if len(remaining) == len(findings):
        return {"error": f"finding {fid!r} not found"}, 404
    spec["findings"] = remaining
    sf.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True}, 200


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

    study_dir = _study_spec_lib.study_dir(ws_root, study)
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    # Comment-preserving round-trip so pointing a study at a composite from the
    # GUI doesn't strip the scaffold's guide comments (issue #1618, #7).
    spec, dump = load_yaml_preserving(sf.read_text(encoding="utf-8"))
    if _v4_baseline_not_yet_materialized(spec):
        return {
            "error": "This study's baseline is defined under conditions.baseline "
                     "(v4 format) and has never been saved as a standalone "
                     "baseline[] entry — adding one here would create a second, "
                     "disconnected copy that never stays in sync. Edit "
                     "conditions.baseline.params directly for now.",
        }, 422
    baseline = spec.setdefault("baseline", [])
    if any(b.get("name") == entry_name for b in baseline if isinstance(b, dict)):
        return {"error": f"baseline entry {entry_name!r} already exists"}, 409
    baseline.append({"name": entry_name, "composite": composite, "params": params or {}})
    sf.write_text(dump(spec), encoding="utf-8")
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

    study_dir = _study_spec_lib.study_dir(ws_root, study)
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    # Comment-preserving round-trip (issue #1618, #7).
    spec, dump = load_yaml_preserving(sf.read_text(encoding="utf-8"))
    if _v4_baseline_not_yet_materialized(spec):
        return {
            "error": "This study's baseline is defined under conditions.baseline "
                     "(v4 format) and has never been saved as a standalone "
                     "baseline[] entry, so there is nothing real to remove — the "
                     "name the frontend displayed was a synthesized, never-"
                     "persisted view. Edit conditions.baseline.params directly.",
        }, 422
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
    sf.write_text(dump(spec), encoding="utf-8")
    return {"ok": True}, 200


def study_baseline_set(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Repoint an existing baseline entry IN PLACE, preserving its name.

    POST /api/study-baseline-set. Body:
      study:     <name>
      name:      <existing baseline entry name>
      composite: <pkg.composites.x>   (optional; unchanged when absent)
      params:    <dict>               (optional; replaced when present)

    Unlike the add-then-remove dance the frontend used to perform — which
    renamed the entry to ``<name>-<timestamp-suffix>`` to dodge the add's 409
    and the remove's "can't leave baseline empty" 400 — this updates the
    entry's ``composite`` / ``params`` WITHOUT changing its ``name``, so a
    study keeps a stable, legible baseline name after a model change (issue
    #1618, blocker A). Comment-preserving round-trip (#7).

    404 when the named entry doesn't exist; 422 for an unmaterialized v4
    conditions.baseline (same as add/remove).
    """
    study = (body.get("study") or body.get("investigation") or "").strip()
    entry_name = (body.get("name") or "").strip()
    composite = (body.get("composite") or "").strip()
    params = body.get("params")
    if not study:
        return {"error": "missing study"}, 400
    if not entry_name:
        return {"error": "missing baseline entry name"}, 400
    if params is not None and not isinstance(params, dict):
        return {"error": "params must be an object"}, 400

    study_dir = _study_spec_lib.study_dir(ws_root, study)
    sf = _study_spec_lib.study_spec_file(study_dir)
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec, dump = load_yaml_preserving(sf.read_text(encoding="utf-8"))
    if _v4_baseline_not_yet_materialized(spec):
        return {
            "error": "This study's baseline is defined under conditions.baseline "
                     "(v4 format) and has never been saved as a standalone "
                     "baseline[] entry. Edit conditions.baseline.params directly.",
        }, 422
    baseline = spec.get("baseline") or []
    entry = next(
        (b for b in baseline
         if isinstance(b, dict) and b.get("name") == entry_name),
        None,
    )
    if entry is None:
        return {"error": f"baseline entry {entry_name!r} not found"}, 404
    if composite:
        entry["composite"] = composite
    if params is not None:
        entry["params"] = params
    sf.write_text(dump(spec), encoding="utf-8")
    return {"ok": True, "name": entry_name}, 200


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

    study_dir = _study_spec_lib.study_dir(ws_root, study)
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

    study_dir = _study_spec_lib.study_dir(ws_root, study)
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

    study_dir = _study_spec_lib.study_dir(ws_root, study)
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
