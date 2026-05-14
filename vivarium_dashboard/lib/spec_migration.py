"""Migration helper: legacy `composites:` shape → v2 `variants:` shape,
and v2 → v3 study shape (single-composite baseline)."""
from __future__ import annotations
import pathlib, os
import warnings
import yaml


def migrate_study_to_v2_vocabulary(spec_path: pathlib.Path) -> bool:
    """Migrate one spec.yaml from legacy composites-shape to v2 variants-shape.

    Returns True if a migration was applied, False if the file was already v2.
    """
    text = spec_path.read_text()
    data = yaml.safe_load(text) or {}
    if 'variants' in data:
        # Ensure new top-level fields present for idempotency.
        defaults = {
            'comparisons': [],
            'groups': [],
            'conclusions': '',
            'question': '',
            'hypothesis': '',
            'status': 'draft',
            'topic': '',
        }
        changed = False
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
                changed = True
        if not changed:
            return False
        _atomic_write(spec_path, yaml.safe_dump(data, sort_keys=False))
        return True
    if 'composites' not in data:
        return False
    composites = data.pop('composites') or []
    variants = []
    baseline_name = None
    for entry in composites:
        entry = dict(entry)
        intervention = {}
        if 'parameter_overrides' in entry:
            intervention['parameter_overrides'] = entry.pop('parameter_overrides')
        if 'process_overrides' in entry:
            intervention['process_overrides'] = entry.pop('process_overrides')
        if intervention:
            description = entry.pop('intervention_description', '')
            entry['intervention'] = {'description': description, **intervention}
        if baseline_name is None and entry.get('source') and not entry.get('extends'):
            baseline_name = entry['name']
        variants.append(entry)
    data['baseline'] = baseline_name or (variants[0]['name'] if variants else '')
    data['variants'] = variants
    data.setdefault('comparisons', [])
    data.setdefault('groups', [])
    data.setdefault('conclusions', '')
    data.setdefault('question', '')
    data.setdefault('hypothesis', '')
    data.setdefault('status', 'draft')
    data.setdefault('topic', '')
    _atomic_write(spec_path, yaml.safe_dump(data, sort_keys=False))
    return True


def _atomic_write(path: pathlib.Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text)
    os.replace(tmp, path)


def migrate_v2_to_v3(spec: dict) -> dict:
    """Migrate a schema_version=2 investigation spec to schema_version=3 study.

    Transforms:
      - Drop ``composites: [...]`` multi-composite list; promote the first entry
        as ``baseline.composite``.  Emit a UserWarning if more than one was
        present (only the first is preserved; recreate extras as variants if
        needed).
      - Lift the first composite's ``parameters: {...}`` into ``baseline.params``.
      - Add empty ``objective: ""`` and ``parent_studies: []`` (reserved for
        future inter-study linkage).
      - Bump schema_version to 3.

    Idempotent: returns *the same object* unchanged if ``schema_version`` is
    already 3.
    """
    if spec.get("schema_version") == 3:
        return spec

    # Only migrate specs that are explicitly versioned as v2 (or have the v2
    # multi-composite ``composites:`` key).  Specs without a schema_version
    # (legacy single-composite shape) are passed through unchanged so that the
    # existing load_spec validator can handle them.
    version = spec.get("schema_version")
    has_composites_key = "composites" in spec
    if version != 2 and not has_composites_key:
        return spec

    out = dict(spec)
    out["schema_version"] = 3
    out.setdefault("objective", "")
    out.setdefault("parent_studies", [])

    composites = spec.get("composites") or []
    if composites:
        first = composites[0]
        out["baseline"] = {
            "composite": first.get("source") or first.get("name", ""),
            "params": first.get("parameters", {}) or {},
        }
        if len(composites) > 1:
            warnings.warn(
                f"v2→v3 migration: dropped {len(composites) - 1} extra composite(s) "
                f"from study {spec.get('name', '?')!r}; Phase 1 Studies are "
                f"single-composite. Recreate as variants if needed.",
                UserWarning,
                stacklevel=2,
            )
        out.pop("composites", None)
    elif "composite" in spec:
        # Handle lone top-level `composite:` key (explicit v2 with composite key)
        out["baseline"] = {
            "composite": spec["composite"],
            "params": spec.get("parameters", {}) or {},
        }
        out.pop("composite", None)
        out.pop("parameters", None)

    return out
