"""Tests for migrating legacy single-composite Investigations."""
import yaml
from pathlib import Path

from vivarium_workbench.lib.investigation_migrate import (
    needs_migration, migrate_investigation,
)


def _seed_legacy(tmp_path):
    """Build a fixture with the legacy single-composite shape + the source
    composite YAML it points to."""
    inv = tmp_path / 'investigations' / 't1'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 't1',
        'composite': 'pbg_demo.composites.simple',
        'simulations': [{'name': 's1', 'kind': 'single', 'overrides': {}, 'steps': 10}],
        'observables': ['DnaA'],
        'visualizations': [],
    }, sort_keys=False))

    pkg = tmp_path / 'pbg_demo' / 'composites'
    pkg.mkdir(parents=True)
    (pkg / 'simple.composite.yaml').write_text(yaml.safe_dump({
        'name': 'simple-demo',
        'state': {
            'chromosome': {'DnaA_count': {'_type': 'integer', '_default': 100}},
            'replication': {
                '_type': 'process',
                'address': 'local:Foo',
                'config': {'rate': 1.0},
                'inputs': {'dna': ['chromosome']},
                'outputs': {'dna': ['chromosome']},
            },
        },
    }, sort_keys=False))
    return tmp_path, inv


def test_needs_migration_detects_legacy_shape(tmp_path):
    _, inv = _seed_legacy(tmp_path)
    assert needs_migration(inv / 'spec.yaml') is True


def test_needs_migration_skips_already_migrated(tmp_path):
    inv = tmp_path / 'investigations' / 'ok'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(
        'name: ok\ncomposites:\n  - {name: baseline, source: pkg.x, document: ./c/b.yaml}\n'
        'runs: []\n'
    )
    assert needs_migration(inv / 'spec.yaml') is False


def test_needs_migration_returns_false_when_spec_missing(tmp_path):
    assert needs_migration(tmp_path / 'nonexistent.yaml') is False


def test_migrate_copies_composite_and_rewrites_spec(tmp_path):
    ws_root, inv = _seed_legacy(tmp_path)
    migrate_investigation(inv / 'spec.yaml', workspace_root=ws_root)

    sidecar = inv / 'composites' / 'simple.yaml'
    assert sidecar.is_file()
    doc = yaml.safe_load(sidecar.read_text())
    assert 'state' in doc
    assert 'replication' in doc['state']

    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert 'composite' not in spec
    assert len(spec['composites']) == 1
    assert spec['composites'][0]['name'] == 'simple'
    assert spec['composites'][0]['source'] == 'pbg_demo.composites.simple'
    assert spec['composites'][0]['document'] == './composites/simple.yaml'

    # simulations -> runs conversion: each entry should have composite set
    runs = spec.get('runs') or []
    assert runs, 'expected runs to be present after migration'
    for r in runs:
        assert r.get('composite') == 'simple'


def test_migrate_converts_observable_names_to_path_dicts(tmp_path):
    ws_root, inv = _seed_legacy(tmp_path)
    migrate_investigation(inv / 'spec.yaml', workspace_root=ws_root)
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    obs = spec.get('observables') or []
    assert obs == [{'path': ['DnaA']}]


def test_migrate_is_idempotent(tmp_path):
    ws_root, inv = _seed_legacy(tmp_path)
    migrate_investigation(inv / 'spec.yaml', workspace_root=ws_root)
    migrate_investigation(inv / 'spec.yaml', workspace_root=ws_root)
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert len(spec['composites']) == 1


def test_migrate_handles_simulations_with_seeds(tmp_path):
    """Legacy seeds: list (parameter sweep) should round-trip via the runs entries."""
    ws_root, inv = _seed_legacy(tmp_path)
    # Replace simulations with a seeds-based one
    spec_path = inv / 'spec.yaml'
    spec = yaml.safe_load(spec_path.read_text())
    spec['simulations'] = [{'name': 's1', 'kind': 'seeds',
                             'seeds': [1, 2, 3], 'overrides': {}, 'steps': 5}]
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

    migrate_investigation(spec_path, workspace_root=ws_root)
    new = yaml.safe_load(spec_path.read_text())
    runs = new.get('runs') or []
    assert runs
    # Either one run-entry with seeds: [1,2,3] OR three separate run-entries —
    # both are acceptable. Just check seeds is somehow preserved.
    has_seeds_field = any(r.get('seeds') for r in runs)
    has_three_runs = len(runs) == 3
    assert has_seeds_field or has_three_runs
