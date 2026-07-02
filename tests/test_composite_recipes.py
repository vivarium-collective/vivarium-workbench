"""Tests for composite_recipes helpers (parameter/process overrides, state walk)."""
import sys
from pathlib import Path

import pytest

# Make repo root importable for vivarium_workbench.lib.composite_recipes
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "template"))

from vivarium_workbench.lib.composite_recipes import (
    apply_parameter_overrides,
    apply_process_overrides,
    walk_state_tree,
)


def _doc():
    return {
        'name': 'demo',
        'parameters': {
            'rate': {'type': 'float', 'default': 1.0},
            'initial_count': {'type': 'integer', 'default': 100},
        },
        'state': {
            'chromosome': {
                'DnaA_count': {'_type': 'integer', '_default': 100},
                'free_DnaA': {'_type': 'float', '_default': 50.0},
            },
            'replication': {
                '_type': 'process',
                'address': 'local:Foo',
                'config': {'rate': 1.0},
                'inputs': {'dna': ['chromosome']},
                'outputs': {'dna': ['chromosome']},
            },
        },
    }


def test_apply_parameter_overrides_on_declared_parameters():
    doc = _doc()
    apply_parameter_overrides(doc, {'rate': 2.5, 'initial_count': 200})
    assert doc['parameters']['rate']['default'] == 2.5
    assert doc['parameters']['initial_count']['default'] == 200


def test_apply_parameter_overrides_dotted_state_path():
    doc = _doc()
    apply_parameter_overrides(doc, {'state.chromosome.DnaA_count._default': 300})
    assert doc['state']['chromosome']['DnaA_count']['_default'] == 300


def test_apply_parameter_overrides_dotted_process_config():
    doc = _doc()
    apply_parameter_overrides(doc, {'state.replication.config.rate': 5.0})
    assert doc['state']['replication']['config']['rate'] == 5.0


def test_apply_parameter_overrides_missing_path_raises():
    doc = _doc()
    with pytest.raises(KeyError, match='nonexistent'):
        apply_parameter_overrides(doc, {'state.nonexistent.field': 1})


def test_apply_parameter_overrides_undeclared_bare_name_raises():
    doc = _doc()
    with pytest.raises(KeyError, match='undeclared'):
        apply_parameter_overrides(doc, {'undeclared': 1})


def test_apply_process_overrides_swap_address():
    doc = _doc()
    apply_process_overrides(doc, {'replication': 'local:NewProcess'})
    assert doc['state']['replication']['address'] == 'local:NewProcess'
    assert doc['state']['replication']['config'] == {'rate': 1.0}


def test_apply_process_overrides_swap_address_and_config():
    doc = _doc()
    apply_process_overrides(doc, {
        'replication': {'address': 'local:NewProcess', 'config': {'rate': 9.0}},
    })
    assert doc['state']['replication']['address'] == 'local:NewProcess'
    assert doc['state']['replication']['config']['rate'] == 9.0


def test_apply_process_overrides_remove():
    doc = _doc()
    apply_process_overrides(doc, {'replication': None})
    assert 'replication' not in doc['state']


def test_apply_process_overrides_unknown_process_raises():
    doc = _doc()
    with pytest.raises(KeyError, match='unknown'):
        apply_process_overrides(doc, {'unknown': None})


def test_walk_state_tree_yields_leaves_and_processes():
    doc = _doc()
    leaves = walk_state_tree(doc)
    paths = {tuple(l['path']) for l in leaves}
    assert ('chromosome', 'DnaA_count') in paths
    assert ('chromosome', 'free_DnaA') in paths
    repli = next(l for l in leaves if tuple(l['path']) == ('replication',))
    assert repli['kind'] == 'process'
    assert repli['address'] == 'local:Foo'


def test_walk_state_tree_handles_plain_value_leaves():
    """Some composites use raw scalar/placeholder values (e.g. '${rate}') for store defaults."""
    doc = {'state': {'stores': {'chromosome': {'count': '${initial_count}'}}}}
    leaves = walk_state_tree(doc)
    paths = {tuple(l['path']) for l in leaves}
    assert ('stores', 'chromosome', 'count') in paths
