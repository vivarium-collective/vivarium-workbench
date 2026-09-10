"""Remote run -> study association via the workspace's ``remote_run_study_map``.

A remote (GovCloud) run is surfaced with ``db_path=None`` (its store is an
``s3://`` uri), so the local index's study-slug-from-db_path inference can't fire
and every remote run lands orphaned (``study_slug=None``). ``_load_remote_study_map``
+ ``_infer_study_slug`` let a workspace declare how its remote runs'
``experiment_id``s map to its studies. These tests pin that contract.
"""
import textwrap

from vivarium_workbench.lib.remote_simulations import (
    _infer_study_slug,
    _load_remote_study_map,
)


def _write_ws(tmp_path, body: str):
    (tmp_path / "workspace.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_load_rules_parses_ordered_pattern_study_pairs(tmp_path):
    _write_ws(tmp_path, """
        remote_run_study_map:
          - {pattern: 'run4.*strain|strain-des', study: cd2-fss-strain-design}
          - {pattern: 'sulfadiaz', study: cd2-sulfadiazine}
    """)
    rules = _load_remote_study_map(tmp_path)
    assert [slug for _pat, slug in rules] == [
        "cd2-fss-strain-design", "cd2-sulfadiazine"]


def test_no_config_or_no_key_yields_no_rules(tmp_path):
    assert _load_remote_study_map(tmp_path) == []  # no workspace.yaml
    _write_ws(tmp_path, "name: sms-ecoli\n")       # present, but no key
    assert _load_remote_study_map(tmp_path) == []


def test_malformed_entries_are_skipped_not_fatal(tmp_path):
    _write_ws(tmp_path, """
        remote_run_study_map:
          - {pattern: '[unclosed', study: bad-regex}
          - {study: no-pattern}
          - {pattern: 'ok'}
          - {pattern: 'sulfadiaz', study: cd2-sulfadiazine}
    """)
    rules = _load_remote_study_map(tmp_path)
    assert [slug for _pat, slug in rules] == ["cd2-sulfadiazine"]


def test_infer_first_match_wins_and_is_case_insensitive(tmp_path):
    _write_ws(tmp_path, """
        remote_run_study_map:
          - {pattern: 'mecillinam-shape', study: cd2-mecillinam-shape}
          - {pattern: 'mecillinam',       study: cd2-mecillinam}
    """)
    rules = _load_remote_study_map(tmp_path)
    # the specific rule precedes the general one, so shape wins
    assert _infer_study_slug("sim105-CD2-Mecillinam-Shape-gov01", rules) == \
        "cd2-mecillinam-shape"
    assert _infer_study_slug("sim105-cd2-mecillinam-gov01", rules) == \
        "cd2-mecillinam"


def test_unmatched_experiment_id_returns_none(tmp_path):
    _write_ws(tmp_path, """
        remote_run_study_map:
          - {pattern: 'run4.*strain', study: cd2-fss-strain-design}
    """)
    rules = _load_remote_study_map(tmp_path)
    # a run1-k4 / run2-j3 id matches nothing -> stays unassociated (None)
    assert _infer_study_slug("sim155-cd2-run1-k4-cellonly-lam050", rules) is None
    assert _infer_study_slug("sim122-cd2-run2-j3-parca-base", rules) is None


def test_representative_cd2_ids_map_to_expected_studies(tmp_path):
    _write_ws(tmp_path, """
        remote_run_study_map:
          - {pattern: 'run4.*strain|strain-des', study: cd2-fss-strain-design}
          - {pattern: 'run4.*genotype|run4.*pathway|pathway-expression', study: cd2-fss-pathway-expression}
          - {pattern: 'sulfadiaz', study: cd2-sulfadiazine}
          - {pattern: 'antibiotic-cocktail|run3.*cocktail', study: cd2-antibiotic-cocktail}
          - {pattern: 'gillesp|pbp2', study: cd2-gillespie-mec}
    """)
    rules = _load_remote_study_map(tmp_path)
    cases = {
        "sim173-cd2-run4-strain-design-abcd": "cd2-fss-strain-design",
        "sim166-cd2-run4-genotype1-c396": "cd2-fss-pathway-expression",
        "sim105-cd2-sulfadiazine-gov01-2740": "cd2-sulfadiazine",
        "sim105-cd2-antibiotic-cocktail-gov01-90bf": "cd2-antibiotic-cocktail",
        "sim105-cd2-gillespie-mec-gov01": "cd2-gillespie-mec",
    }
    for eid, expected in cases.items():
        assert _infer_study_slug(eid, rules) == expected, eid
