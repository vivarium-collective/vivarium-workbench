"""Tests for migrating a study spec.yaml from legacy `composites:` to v2 `variants:`."""
import textwrap
import yaml

from vivarium_dashboard.lib.spec_migration import migrate_study_to_v2_vocabulary, migrate_v2_to_v3


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


def test_migrate_v2_to_v3_lifts_first_composite_to_baseline():
    """v3 has `baseline: [{name, composite, params}]` + drops `composites: [...]`."""
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
    assert v3["baseline"] == [{"name": "main", "composite": "pkg.composites.foo", "params": {"a": 1}}]
    assert "composites" not in v3
    assert v3.get("objective") == ""
    assert v3.get("parent_studies") == []


def test_migrate_v2_to_v3_preserves_all_composites_in_baseline_list():
    """v2→v3: all composites are preserved — no composites are dropped."""
    v2 = {
        "schema_version": 2,
        "name": "y",
        "composites": [
            {"name": "main", "source": "pkg.a"},
            {"name": "alt",  "source": "pkg.b"},
        ],
    }
    v3 = migrate_v2_to_v3(v2)
    assert v3["baseline"] == [
        {"name": "main", "composite": "pkg.a", "params": {}},
        {"name": "alt", "composite": "pkg.b", "params": {}},
    ]


def test_migrate_v2_to_v3_idempotent():
    v3_already = {"schema_version": 3, "baseline": {"composite": "x"}}
    assert migrate_v2_to_v3(v3_already) is v3_already


def test_migrate_v2_to_v3_bare_composite_key():
    """elif branch: v2 spec with a lone `composite:` string (not a composites list).

    This shape is reachable when the Task 5.1 CLI calls migrate_v2_to_v3 directly
    on raw YAML — without running migrate_study_to_v2_vocabulary first.
    """
    v2 = {
        "schema_version": 2,
        "name": "my-study",
        "composite": "pkg.composites.chemotaxis",
        "parameters": {"rate": 0.5},
    }
    v3 = migrate_v2_to_v3(v2)
    assert v3["schema_version"] == 3
    assert v3["baseline"] == [
        {"name": "pkg.composites.chemotaxis", "composite": "pkg.composites.chemotaxis", "params": {"rate": 0.5}}
    ]
    assert "composite" not in v3
    assert "parameters" not in v3
    assert v3.get("objective") == ""
    assert v3.get("parent_studies") == []


def test_migrate_v2_to_v3_baseline_is_a_list_of_all_composites():
    """v2→v3: a multi-entry composites: list becomes a baseline LIST with one
    {name, composite, params} entry each — no composites are dropped."""
    spec = {
        "schema_version": 2,
        "name": "s",
        "composites": [
            {"name": "a", "source": "pkg.a", "parameters": {"rate": 1.0}},
            {"name": "b", "source": "pkg.b"},
        ],
    }
    out = migrate_v2_to_v3(spec)
    assert out["schema_version"] == 3
    assert out["baseline"] == [
        {"name": "a", "composite": "pkg.a", "params": {"rate": 1.0}},
        {"name": "b", "composite": "pkg.b", "params": {}},
    ]
    assert "composites" not in out


def test_migrate_v2_to_v3_lone_composite_key_becomes_one_element_list():
    """A bare top-level composite: string becomes a one-element baseline list."""
    spec = {
        "schema_version": 2,
        "name": "s",
        "composite": "pkg.chemotaxis",
        "parameters": {"k": 0.5},
    }
    out = migrate_v2_to_v3(spec)
    assert out["baseline"] == [
        {"name": "pkg.chemotaxis", "composite": "pkg.chemotaxis", "params": {"k": 0.5}}
    ]
    assert "composite" not in out and "parameters" not in out
