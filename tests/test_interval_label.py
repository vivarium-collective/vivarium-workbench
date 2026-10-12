"""_attach_process_docs stamps a human interval_label on process nodes."""

from vivarium_workbench.env_worker import _attach_process_docs


def test_attach_process_docs_adds_interval_label_for_unit():
    doc = {'grow': {'_type': 'process', 'address': 'local:X',
                    'interval': 60.0, 'interval_units': 'minute'}}
    _attach_process_docs(doc)
    assert doc['grow']['interval_label'] == '1 min'


def test_attach_process_docs_adds_seconds_label_for_bare_number():
    doc = {'grow': {'_type': 'process', 'address': 'local:X', 'interval': 90.0}}
    _attach_process_docs(doc)
    assert doc['grow']['interval_label'] == '90 s'


def test_attach_process_docs_no_label_without_interval():
    doc = {'store': {'_type': 'step', 'address': 'local:S'}}
    _attach_process_docs(doc)
    assert 'interval_label' not in doc['store']
