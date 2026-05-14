"""Tests for migrating a study spec.yaml from legacy `composites:` to v2 `variants:`."""
import textwrap
import yaml

from vivarium_dashboard.lib.spec_migration import migrate_study_to_v2_vocabulary


def _write(tmp_path, body):
    p = tmp_path / 'spec.yaml'
    p.write_text(textwrap.dedent(body).lstrip())
    return p


def test_migrate_renames_composites_to_variants(tmp_path):
    p = _write(tmp_path, """
        name: s
        composites:
          - {name: a, source: pkg.a}
    """)
    migrate_study_to_v2_vocabulary(p)
    data = yaml.safe_load(p.read_text())
    assert 'composites' not in data
    assert data['variants'] == [{'name': 'a', 'source': 'pkg.a'}]


def test_migrate_nests_overrides_into_intervention(tmp_path):
    p = _write(tmp_path, """
        name: s
        composites:
          - {name: a, source: pkg.a}
          - name: b
            extends: a
            parameter_overrides: {state.x: 1.0}
            process_overrides: {p: null}
    """)
    migrate_study_to_v2_vocabulary(p)
    data = yaml.safe_load(p.read_text())
    b = data['variants'][1]
    assert b['intervention'] == {
        'description': '',
        'parameter_overrides': {'state.x': 1.0},
        'process_overrides': {'p': None},
    }
    assert 'parameter_overrides' not in b
    assert 'process_overrides' not in b


def test_migrate_sets_baseline_from_first_source_variant(tmp_path):
    p = _write(tmp_path, """
        name: s
        composites:
          - {name: a, source: pkg.a}
          - {name: b, extends: a}
    """)
    migrate_study_to_v2_vocabulary(p)
    data = yaml.safe_load(p.read_text())
    assert data['baseline'] == 'a'


def test_migrate_initializes_blank_fields(tmp_path):
    p = _write(tmp_path, """
        name: s
        composites:
          - {name: a, source: pkg.a}
    """)
    migrate_study_to_v2_vocabulary(p)
    data = yaml.safe_load(p.read_text())
    assert data['comparisons'] == []
    assert data['groups'] == []
    assert data['conclusions'] == ''
    assert data['question'] == ''
    assert data['hypothesis'] == ''
    assert data['status'] == 'draft'
    assert data['topic'] == ''


def test_migrate_initializes_groups_blank_on_v2_spec(tmp_path):
    """A v2-shape spec missing only `groups:` gets it backfilled."""
    p = _write(tmp_path, """
        name: s
        baseline: a
        question: ""
        hypothesis: ""
        status: draft
        variants:
          - {name: a, source: pkg.a}
        comparisons: []
        conclusions: ""
    """)
    migrate_study_to_v2_vocabulary(p)
    data = yaml.safe_load(p.read_text())
    assert data['groups'] == []


def test_migrate_idempotent(tmp_path):
    p = _write(tmp_path, """
        name: s
        baseline: a
        question: ""
        hypothesis: ""
        status: draft
        topic: ""
        variants:
          - {name: a, source: pkg.a}
        comparisons: []
        groups: []
        conclusions: ""
    """)
    before = p.read_text()
    migrate_study_to_v2_vocabulary(p)
    assert p.read_text() == before


def test_migrate_v2_to_v3_lifts_first_composite_to_baseline(tmp_path):
    """v3 has `baseline: {composite, params}` + drops `composites: [...]`."""
    from vivarium_dashboard.lib.spec_migration import migrate_v2_to_v3

    v2 = {
        "schema_version": 2,
        "name": "x",
        "composites": [
            {"name": "main", "source": "pkg.composites.foo", "parameters": {"a": 1}},
        ],
        "runs": [],
        "variants": [],
        "conclusion": None,
    }
    v3 = migrate_v2_to_v3(v2)
    assert v3["schema_version"] == 3
    assert v3["baseline"] == {"composite": "pkg.composites.foo", "params": {"a": 1}}
    assert "composites" not in v3
    assert v3.get("objective") == ""
    assert v3.get("parent_studies") == []


def test_migrate_v2_to_v3_warns_on_multi_composite():
    """If v2 has >1 composite, migration keeps the first + emits a warning."""
    import warnings
    from vivarium_dashboard.lib.spec_migration import migrate_v2_to_v3

    v2 = {
        "schema_version": 2,
        "name": "y",
        "composites": [
            {"name": "main", "source": "pkg.a"},
            {"name": "alt",  "source": "pkg.b"},
        ],
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        v3 = migrate_v2_to_v3(v2)
    msgs = [str(w.message) for w in caught]
    assert any("dropped 1 extra composite" in m for m in msgs)
    assert v3["baseline"]["composite"] == "pkg.a"


def test_migrate_v2_to_v3_idempotent():
    from vivarium_dashboard.lib.spec_migration import migrate_v2_to_v3
    v3_already = {"schema_version": 3, "baseline": {"composite": "x"}}
    assert migrate_v2_to_v3(v3_already) is v3_already
