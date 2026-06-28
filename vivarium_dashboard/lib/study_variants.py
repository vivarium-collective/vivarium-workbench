"""Save a run (its composite + config) as a named study variant. A study
variant IS a saved (composite, config) run — the data-model unification (SP-B)."""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_dashboard.lib import composite_runs


def _study_yaml(workspace: Path, study: str) -> Path | None:
    for base in (Path(workspace) / "studies" / study,):
        p = base / "study.yaml"
        if p.is_file():
            return p
    # nested investigations/<inv>/studies/<study>/study.yaml
    for p in Path(workspace).glob(f"investigations/*/studies/{study}/study.yaml"):
        return p
    return None


def save_run_as_variant(workspace, *, run_id, source_db, study, variant_name):
    sf = _study_yaml(Path(workspace), study)
    if sf is None:
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
