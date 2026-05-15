"""Migration helper: legacy `composites:` shape → v2 `variants:` shape,
and v2 → v3 study shape (list-of-composites baseline)."""
from __future__ import annotations
import pathlib, os
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
      - Convert the ``composites: [...]`` multi-composite list into a
        ``baseline`` **list**, where every entry becomes a dict shaped
        ``{name, composite, params}``.  All composites are preserved; none are
        dropped and no ``UserWarning`` is emitted.
      - Lift each composite's ``parameters: {...}`` into its ``params`` field in
        the resulting ``baseline`` entry.
      - Add empty ``objective: ""`` and ``parent_studies: []`` (reserved for
        future inter-study linkage).
      - Bump schema_version to 3.

    Example output shape for the ``baseline`` list::

        baseline:
          - name: my-composite
            composite: pkg.composites.my_composite
            params: {rate: 0.5}
          - name: other-composite
            composite: pkg.composites.other_composite
            params: {}

    Idempotent: returns *the same object* unchanged if ``schema_version`` is
    already 3.

    Two calling paths reach this function:

    1. ``load_spec`` (in-memory only) — runs after ``migrate_study_to_v2_vocabulary``
       which converts ``composites: [...]`` → ``variants:``.  By the time this
       function is called from ``load_spec`` the spec always has ``variants:`` or
       a bare ``composite:`` (legacy single-composite shape); the latter is
       guarded by the ``version != 2 and not has_composites_key`` early-return so
       the ``elif "composite" in spec`` branch is **not** reachable via this path.

    2. ``migrate_investigations_to_studies`` (Task 5.1 CLI) — loads raw YAML from
       disk and calls ``migrate_v2_to_v3(spec)`` directly, *without* first running
       ``migrate_study_to_v2_vocabulary``.  A v2 investigation that was hand-authored
       with a top-level ``composite: "pkg.composites.foo"`` string instead of the
       ``composites: [...]`` list **will** have ``schema_version: 2`` and a bare
       ``composite:`` key, making the ``elif`` branch reachable.  Example raw YAML::

           schema_version: 2
           name: my-study
           composite: pkg.composites.chemotaxis
           parameters: {rate: 0.5}

       In this case the branch promotes ``composite`` + ``parameters`` into a
       one-element ``baseline`` list — ``baseline: [{name: ..., composite: ...,
       params: {...}}]`` — matching the shape produced by the multi-composite
       path.
    """
    if spec.get("schema_version") == 3:
        return spec

    # Only migrate specs that are explicitly versioned as v2 (or have the v2
    # multi-composite ``composites:`` key).  Specs without a schema_version
    # (legacy single-composite shape) are passed through unchanged so that the
    # existing load_spec validator can handle them.
    version = spec.get("schema_version")
    has_composites_key = "composites" in spec
    # The "variants-as-composites" v2 shape: a `variants:` list whose entries
    # are composites (carry `source`), with a string `baseline:` naming one.
    variants_in = spec.get("variants")
    is_variants_as_composites = (
        isinstance(variants_in, list)
        and isinstance(spec.get("baseline"), str)
        and any(isinstance(v, dict) and v.get("source") for v in variants_in)
    )
    if version != 2 and not has_composites_key and not is_variants_as_composites:
        return spec

    out = dict(spec)
    out["schema_version"] = 3
    out.setdefault("objective", "")
    out.setdefault("parent_studies", [])

    if is_variants_as_composites:
        baseline_list = []
        new_variants = []
        for v in variants_in:
            if not isinstance(v, dict):
                continue
            if v.get("source") and not v.get("extends"):
                baseline_list.append({
                    "name": v.get("name", ""),
                    "composite": v.get("source", ""),
                    "params": v.get("parameter_overrides", {}) or {},
                })
            else:
                iv = v.get("intervention") or {}
                new_variants.append({
                    "name": v.get("name", ""),
                    "base_composite": v.get("extends", ""),
                    "parameter_overrides": (
                        v.get("parameter_overrides")
                        or iv.get("parameter_overrides")
                        or {}
                    ),
                })
        out["baseline"] = baseline_list
        out["variants"] = new_variants
        return out

    composites = spec.get("composites") or []
    if composites:
        out["baseline"] = [
            {
                "name": c.get("name") or c.get("source", ""),
                "composite": c.get("source") or c.get("name", ""),
                "params": c.get("parameters", {}) or {},
            }
            for c in composites
        ]
        out.pop("composites", None)
    elif "composite" in spec:
        # Lone top-level `composite:` key (explicit v2 with composite key).
        out["baseline"] = [{
            "name": spec["composite"],
            "composite": spec["composite"],
            "params": spec.get("parameters", {}) or {},
        }]
        out.pop("composite", None)
        out.pop("parameters", None)

    return out
