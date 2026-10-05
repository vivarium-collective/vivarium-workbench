from bigraph_schema.core import allocate_core
from bigraph_schema.contract import ProcessContract, narrow_condition
from bigraph_schema.edge import Edge
from vivarium_workbench.lib.contract_catalog import contract_audit_payload, CONTRACT_AUDIT_AVAILABLE


class _Contracted(Edge):
    contract = narrow_condition(
        ProcessContract(face={'inputs': {'m': {'_type': 'float', '_min': 0, '_max': 10}},
                              'outputs': {'m': {'_type': 'float', '_min': 0}}}),
        'post', 'outputs.m >= 0', name='nonneg')
    def inputs(self): return {'m': {'_type': 'float', '_min': 0, '_max': 10}}
    def outputs(self): return {'m': {'_type': 'float', '_min': 0}}


class _Bare(Edge):
    def inputs(self): return {'m': 'float'}
    def outputs(self): return {'m': 'float'}


class _Lying(Edge):
    contract = narrow_condition(ProcessContract(face={'inputs': {'m': 'float'}, 'outputs': {'m': 'float'}}),
                                'post', 'outputs.ghost >= 0', name='bogus')   # ghost not a declared port
    def inputs(self): return {'m': 'float'}
    def outputs(self): return {'m': 'float'}


def _core_with(name, cls):
    core = allocate_core()
    core.register_link(name, cls)
    return core

def test_contracted_process_passes():
    core = _core_with('c', _Contracted)
    payload = contract_audit_payload(core, 'c')
    assert payload['status'] in ('pass', 'incomplete')
    assert any(cond['name'] == 'nonneg' for cond in payload['conditions'])
    assert payload['grade'] is not None

def test_bare_process_is_not_declared_or_incomplete():
    payload = contract_audit_payload(_core_with('b', _Bare), 'b')
    assert payload['status'] in ('not-declared', 'incomplete')
    assert payload['status'] != 'fail'

def test_lying_contract_is_fail():
    payload = contract_audit_payload(_core_with('l', _Lying), 'l')
    assert payload['status'] == 'fail'
    assert any('ghost' in f.get('message', '') for f in payload['findings'])

def test_uninstantiable_process_degrades():
    class _Boom(Edge):
        def __init__(self, config=None, core=None): raise RuntimeError('needs config')
        def inputs(self): return {'m': 'float'}
        def outputs(self): return {'m': 'float'}
    payload = contract_audit_payload(_core_with('boom', _Boom), 'boom')   # must not raise
    assert payload['status'] in ('error', 'unavailable', 'not-declared')

def test_unavailable_when_import_missing(monkeypatch):
    import vivarium_workbench.lib.contract_catalog as cc
    monkeypatch.setattr(cc, 'CONTRACT_AUDIT_AVAILABLE', False)
    assert contract_audit_payload(_core_with('c', _Contracted), 'c')['status'] == 'unavailable'


def test_local_prefixed_address_normalized():
    core = _core_with('c', _Contracted)
    prefixed = contract_audit_payload(core, 'local:c')
    assert prefixed['status'] != 'not-declared'
    assert prefixed['status'] == contract_audit_payload(core, 'c')['status']


def test_composite_contract_audit_is_wellformed_or_unavailable():
    core = _core_with('c', _Contracted)
    payload = contract_audit_payload(core, 'c') if CONTRACT_AUDIT_AVAILABLE else {'status': 'unavailable'}
    assert payload['status'] in ('pass', 'fail', 'incomplete', 'not-declared', 'unavailable', 'error')


def test_composites_data_records_carry_contract_audit(tmp_path, monkeypatch):
    import vivarium_workbench.lib.composite_lookup as cl
    (tmp_path / 'workspace.yaml').write_text('name: x\npackage_path: pbg_x_nonexistent\n')
    monkeypatch.setattr(cl, 'discover_all_composites',
                        lambda root, pkg: {'a': {'id': 'a', 'name': 'a', 'module': ''}})
    out = cl.composites_data(tmp_path)
    recs = out['composites']
    assert recs, out
    for r in recs:
        assert r['contract_audit']['status'] in ('pass', 'fail', 'incomplete', 'not-declared', 'unavailable', 'error')


def test_registry_payload_preserves_contract_audit(tmp_path, monkeypatch):
    """build_registry's annotation path must not strip worker-supplied contract_audit."""
    from vivarium_workbench.lib import registry
    (tmp_path / 'workspace.yaml').write_text('name: x\npackage_path: viva_x_nonexistent\n')
    registry._REGISTRY_CACHE.clear()
    monkeypatch.setenv('VIVARIUM_WORKBENCH_CATALOG_CACHE_DIR', str(tmp_path / 'cache'))
    raw = {'processes': [{'name': 'Bar', 'address': 'viva_x.processes.Bar', 'kind': 'process',
                          'contract_audit': {'status': 'pass', 'grade': 'A'}}], 'types': []}

    class _Pool:
        def call(self, ws_root, method, *a, **k):
            return raw

    monkeypatch.setattr('vivarium_workbench.lib.env_worker_pool.get_pool', lambda: _Pool())
    data = registry.build_registry(tmp_path)
    rec = next(p for p in data['processes'] if p['name'] == 'Bar')
    assert rec['contract_audit']['status'] == 'pass'
