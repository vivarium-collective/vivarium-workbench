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
    """Migrate a v2 study spec to v3 in-memory.

    Three reachable input shapes get reshaped:

    1. **Legacy `composites:` list** — each entry becomes a baseline composite.
    2. **Lone `composite:` string** (CLI bare-composite path) — wrapped as a
       single baseline entry whose `name` is the FQN.
    3. **"Variants-as-composites" v2 shape** — variants carrying `source:`
       split into the baseline list; variants carrying `extends:` /
       `intervention:` become v3 variants with `base_composite` +
       `parameter_overrides`.

    All three paths produce:
      - `schema_version: 3`
      - `baseline: [{name, composite, params}, ...]` (non-empty list)
      - `variants: [...]` (possibly empty; entries have `base_composite` +
        `parameter_overrides`)
      - `interventions: []` (default; preserved if already present)
      - `objective`, `parent_studies` defaults

    Specs already at `schema_version: 3` are returned unchanged (identity).
    """
    if spec.get("schema_version") == 3:
        return spec

    # Only migrate specs that are explicitly versioned as v2 (or have the v2
    # multi-composite ``composites:`` key).  Specs without a schema_version fall
    # through to the variants-as-composites detection below; if that doesn't
    # match, they pass through unchanged.
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
    out.setdefault("interventions", [])

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


def migrate_v3_to_v4(spec: dict) -> dict:
    """Migrate a v3 study spec to v4 in-memory by adding the tests / references /
    implementation_tasks fields. Idempotent. Only touches specs with
    ``schema_version == 3``.

    Important: there are TWO v4 shapes — legacy "v4 = v3 + extras"
    (tests is a dict of {auto_discover, data_source, pytest_args}) and
    the redesigned v4 (tests is a LIST of pass/fail criteria). The
    redesign is signaled by a top-level ``conditions:`` block. If a v3
    spec already has ``conditions:``, the author is on the redesign
    path — DON'T rewrite tests[] to the legacy dict shape, because the
    redesign validator will then reject it with "v4 study: 'tests' must
    be a list" and the study appears INVALID in the dashboard.
    """
    if spec.get("schema_version") != 3:
        return spec

    out = dict(spec)
    out["schema_version"] = 4

    # Skip the legacy-tests rewrite when the spec is on the v4-redesign path.
    # The redesign validator owns the tests[] shape (a list of dicts with
    # name/measure/pass_if). Tests are left as-is so the redesign validator
    # sees them in their authored form.
    if not isinstance(out.get("conditions"), dict):
        existing_tests = out.get("tests") or {}
        # Defensive: existing_tests might be a list authored against the
        # redesign shape even without conditions:. If so, don't clobber it.
        if isinstance(existing_tests, list):
            pass  # leave authored list intact
        else:
            out["tests"] = {
                "auto_discover": existing_tests.get("auto_discover", True),
                "data_source": existing_tests.get("data_source", "latest_run"),
                "pytest_args": existing_tests.get("pytest_args", []),
                "last_results": existing_tests.get("last_results"),
            }
    out.setdefault("references", [])
    out.setdefault("implementation_tasks", "")
    return out
