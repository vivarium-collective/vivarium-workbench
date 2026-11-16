"""Unit tests for lib.composite_audit.audit_composite — the composite-level
WIRING audit (shared-store type + unit agreement).

Drives the pure logic with a tiny fake ``Composite`` (just ``.process_paths``)
and a real core, so no heavy composite build is needed.
"""
from bigraph_schema.core import allocate_core
from bigraph_schema.contract import ProcessContract
from bigraph_schema.edge import Edge
from vivarium_workbench.lib.composite_audit import audit_composite

_STATUSES = ('pass', 'fail', 'warn', 'unavailable')


class _P(Edge):
    contract = ProcessContract(
        face={'inputs': {'m': {'_type': 'float'}}, 'outputs': {'m': {'_type': 'float'}}})
    def inputs(self): return {'m': 'float'}
    def outputs(self): return {'m': 'float'}


class _FakeComp:
    def __init__(self, process_paths):
        self.process_paths = process_paths


def _core():
    core = allocate_core()
    core.register_link('P', _P)
    return core


def test_rollup_and_consistent_wiring():
    core = _core()
    f = core.access('float')
    pp = {
        ('a',): {'address': 'local:P', 'inputs': {'m': ['store']}, 'outputs': {'m': ['store']},
                 '_inputs': {'m': f}, '_outputs': {'m': f}},
        ('b',): {'address': 'local:P', 'inputs': {'m': ['store']}, 'outputs': {'m': ['store']},
                 '_inputs': {'m': f}, '_outputs': {'m': f}},
    }
    res = audit_composite(core, _FakeComp(pp))
    assert res['status'] in _STATUSES
    assert res['n_processes'] == 2
    # two processes agree on the shared 'store' type → no wiring findings
    assert res['wiring']['status'] == 'pass'
    assert res['wiring']['findings'] == []


def test_wiring_type_mismatch_is_fail():
    core = _core()
    f = core.access('float')
    m = core.access('map[float]')
    pp = {
        ('a',): {'address': 'local:P', 'outputs': {'m': ['store']}, 'inputs': {},
                 '_outputs': {'m': f}, '_inputs': {}},
        ('b',): {'address': 'local:P', 'inputs': {'m': ['store']}, 'outputs': {},
                 '_inputs': {'m': m}, '_outputs': {}},
    }
    res = audit_composite(core, _FakeComp(pp))
    assert res['status'] == 'fail'
    assert res['wiring']['status'] == 'fail'
    assert any(fd['code'] == 'wire-type-mismatch' and fd['severity'] == 'error'
               for fd in res['wiring']['findings'])


def test_single_port_store_is_not_flagged():
    # A store wired by only one port can't disagree with itself → no finding.
    core = _core()
    f = core.access('float')
    pp = {('a',): {'address': 'local:P', 'outputs': {'m': ['solo']}, 'inputs': {},
                   '_outputs': {'m': f}, '_inputs': {}}}
    res = audit_composite(core, _FakeComp(pp))
    assert res['wiring']['findings'] == []


def test_empty_composite_is_wellformed():
    res = audit_composite(_core(), _FakeComp({}))
    assert res['status'] == 'pass'
    assert res['n_processes'] == 0
    assert res['wiring']['status'] == 'pass'


def test_missing_process_paths_degrades():
    class _Broken:
        @property
        def process_paths(self):
            raise RuntimeError('no build')
    res = audit_composite(_core(), _Broken())
    assert res['status'] == 'unavailable'
    assert 'notice' in res
