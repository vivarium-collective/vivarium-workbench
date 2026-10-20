"""Save a run (its composite + config) as a named study variant. A study
variant IS a saved (composite, config) run — the data-model unification (SP-B)."""
from __future__ import annotations

import yaml

from vivarium_workbench.lib import composite_runs
from vivarium_workbench.lib.workspace_paths import study_yaml_path


def save_run_as_variant(workspace, *, run_id, source_db, study, variant_name):
    sf = study_yaml_path(workspace, study)
    if not sf.is_file():
        return {"error": f"study not found: {study}"}, 404
    conn = composite_runs.connect(source_db)
    try:
        meta = composite_runs.query_run_meta(conn, run_id=run_id)
    finally:
        conn.close()
    if meta is None:
        return {"error": f"run not found: {run_id}"}, 404
    composite = meta.get("spec_id")
    config = meta.get("params") or {}
    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    if spec.get("schema_version") == 4 and isinstance(spec.get("conditions"), dict):
        variants = spec["conditions"].setdefault("variants", [])
    else:
        variants = spec.setdefault("variants", [])
    entry = {"name": variant_name, "composite": composite, "parameter_overrides": config}
    for i, v in enumerate(variants):
        if isinstance(v, dict) and v.get("name") == variant_name:
            variants[i] = entry  # idempotent overwrite by name
            break
    else:
        variants.append(entry)
    sf.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return {"study": study, "variant": variant_name, "composite": composite}, 200
