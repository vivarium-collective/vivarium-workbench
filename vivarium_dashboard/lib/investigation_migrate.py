"""Migrate a legacy single-composite Investigation to the new composites: list shape.

Run-once per investigation. Triggered automatically on dashboard open of an
investigation whose spec.yaml has the old `composite:` field instead of
`composites:`. The migration:
  1. Resolves the legacy `composite:` ref (e.g. `pkg.composites.foo`) to its
     source YAML at `<pkg>/composites/<foo>.composite.yaml`.
  2. Copies that document to `investigations/<name>/composites/<foo>.yaml`.
  3. Rewrites spec.yaml: replaces `composite:` with a one-entry `composites:`
     list, converts `simulations:` entries to `runs:` entries with the
     baseline composite name attached.
"""
from __future__ import annotations
import shutil
from pathlib import Path

import yaml


def needs_migration(spec_path: Path) -> bool:
    """True iff the spec has the legacy single-composite shape."""
    spec_path = Path(spec_path)
    if not spec_path.is_file():
        return False
    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    return bool(spec.get('composite')) and not spec.get('composites')


def _resolve_composite_source(ref: str, workspace_root: Path) -> tuple[Path, str]:
    """Resolve `pkg.composites.foo` -> (path-to-foo.composite.yaml, baseline-name='foo')."""
    parts = ref.split('.')
    if 'composites' not in parts:
        raise ValueError(
            f"composite ref {ref!r} does not contain 'composites' segment"
        )
    composites_idx = parts.index('composites')
    pkg_parts = parts[:composites_idx]
    stem_parts = parts[composites_idx + 1:]
    if not pkg_parts or not stem_parts:
        raise ValueError(f"composite ref {ref!r} malformed")
    stem = '.'.join(stem_parts)
    composites_dir = workspace_root.joinpath(*pkg_parts) / 'composites'
    for suffix in ('.composite.yaml', '.composite.yml', '.composite.json'):
        candidate = composites_dir / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate, stem
    raise FileNotFoundError(
        f"could not find composite document for {ref!r} under {composites_dir}"
    )


def _to_yaml_friendly(obj):
    """Round-trip a composite document through ``BigraphJSONEncoder`` so
    every non-native type (numpy arrays, structured arrays, pint
    Quantities, …) becomes plain Python and ``yaml.safe_dump`` can handle
    it.

    Generator-materialized composite docs commonly carry numpy state and
    pint-tagged parameters (e.g. wcEcoli's bulk-molecule state, growth
    rates with units). Without this normalization ``yaml.safe_dump``
    raises ``cannot represent an object`` for whichever foreign type it
    hits first.

    Falls back to a recursive duck-typed walk when bigraph-schema is not
    available (smaller test workspaces / minimal envs).
    """
    import json
    try:
        from bigraph_schema.json_codec import BigraphJSONEncoder
        return json.loads(json.dumps(obj, cls=BigraphJSONEncoder))
    except ImportError:
        pass

    # Fallback path: numpy + scalar duck-typing only; pint not supported here.
    if isinstance(obj, dict):
        return {k: _to_yaml_friendly(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_yaml_friendly(x) for x in obj]
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        try:
            return _to_yaml_friendly(tolist())
        except Exception:
            pass
    item = getattr(obj, "item", None)
    if callable(item) and not isinstance(obj, (str, bytes)):
        try:
            return obj.item()
        except Exception:
            pass
    return obj


def _resolve_composite_source_or_generate(
    ref: str, workspace_root: Path,
) -> tuple[Path | None, bool, str]:
    """Resolve a composite ref to either a YAML source path on disk or a
    registered ``@composite_generator`` entry.

    Returns ``(yaml_path, is_generator, name)``:
      - YAML found → ``(path, False, stem)`` matching the legacy lookup.
      - Generator found in ``pbg_superpowers.composite_generator._REGISTRY``
        → ``(None, True, last_segment)``. The caller decides whether to
        actually materialize the document (via :func:`materialize_generator_doc`)
        — many v3-shape callers don't need it, since the dotted ref is
        sufficient and avoids the cost / serialization hazards of
        building the full state tree.
      - Neither → ``FileNotFoundError``.
    """
    try:
        path, name = _resolve_composite_source(ref, workspace_root)
        return path, False, name
    except FileNotFoundError:
        pass  # fall through to generator lookup

    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, discover_generators,
        )
    except ImportError as e:
        raise FileNotFoundError(
            f"no YAML source for {ref!r} and pbg-superpowers is unavailable: {e}"
        )

    if not _REGISTRY:
        discover_generators()
    if ref not in _REGISTRY:
        raise FileNotFoundError(
            f"no YAML source for {ref!r} and not registered as a "
            f"@composite_generator (registry has "
            f"{len(_REGISTRY)} entries)"
        )

    # Generators use the trailing dotted segment (function name) as the
    # canonical short name — matches the catalog's id-stem convention and
    # avoids ugly `baseline.baseline` sidecars for `pkg.composites.foo.foo`
    # style refs.
    parts = ref.split('.')
    composites_idx = parts.index('composites')  # _resolve already validated
    name = parts[-1] if (composites_idx + 1) < len(parts) else parts[composites_idx]
    return None, True, name


def materialize_generator_doc(ref: str) -> dict:
    """Build the composite document for a ``@composite_generator`` ref and
    normalize it through :func:`_to_yaml_friendly` so callers can YAML-dump
    it without hitting numpy / pint serialization errors.

    Raises ``KeyError`` if ``ref`` is not in the generator registry, or
    propagates any exception from ``build_generator(entry)`` (e.g. for
    composites whose state contains live Process instances that can't be
    serialized — those need a different storage path).
    """
    from pbg_superpowers.composite_generator import (
        _REGISTRY, build_generator, discover_generators,
    )
    if not _REGISTRY:
        discover_generators()
    entry = _REGISTRY[ref]
    return _to_yaml_friendly(build_generator(entry))


def migrate_investigation(spec_path: Path, workspace_root: Path) -> dict:
    """Migrate the spec at ``spec_path`` in-place. Returns the new spec dict."""
    spec_path = Path(spec_path)
    workspace_root = Path(workspace_root)
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    if spec.get('composites'):
        return spec  # idempotent

    composite_ref = spec.get('composite')
    if not composite_ref:
        return spec

    source_path, baseline_name = _resolve_composite_source(composite_ref, workspace_root)

    inv_dir = spec_path.parent
    composites_dir = inv_dir / 'composites'
    composites_dir.mkdir(parents=True, exist_ok=True)
    sidecar = composites_dir / f"{baseline_name}.yaml"
    if not sidecar.is_file():
        shutil.copy2(source_path, sidecar)

    new_spec: dict = {'name': spec.get('name')}
    if spec.get('description') is not None:
        new_spec['description'] = spec['description']
    new_spec['composites'] = [{
        'name': baseline_name,
        'source': composite_ref,
        'document': f'./composites/{baseline_name}.yaml',
    }]

    # simulations -> runs (one entry per simulation; seeds preserved as-is)
    simulations = spec.get('simulations') or []
    new_runs: list = []
    for sim in simulations:
        if not isinstance(sim, dict):
            continue
        entry: dict = {'composite': baseline_name}
        if sim.get('overrides'):
            entry['params'] = sim['overrides']
        if sim.get('steps') is not None:
            entry['steps'] = sim['steps']
        if sim.get('seeds'):
            entry['seeds'] = sim['seeds']
        new_runs.append(entry)
    new_spec['runs'] = new_runs

    # observables: legacy flat-name list -> [{path: [name]}, ...]
    observables = spec.get('observables') or []
    new_obs: list = []
    for o in observables:
        if isinstance(o, str):
            new_obs.append({'path': [o]})
        elif isinstance(o, dict) and o.get('path'):
            new_obs.append(o)
    new_spec['observables'] = new_obs
    new_spec['visualizations'] = spec.get('visualizations') or []
    if 'status' in spec:
        new_spec['status'] = spec['status']
    if 'last_run' in spec:
        new_spec['last_run'] = spec['last_run']

    spec_path.write_text(yaml.safe_dump(new_spec, sort_keys=False))
    return new_spec
