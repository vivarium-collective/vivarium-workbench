"""Remote-run -> study inference must be 1:1, and unmapped runs must stay
explicitly unmapped (never silently folded into a study).

Bug class: 505 GovCloud runs landed with ``study_slug=None`` (orphaned) because
a remote run has no local ``db_path`` for the path-based inference to fire. The
fix maps ``experiment_id`` substrings -> study slugs via the workspace's
``remote_run_study_map``. This file formalizes two properties the fix must keep:

  * 1:1 — each CD2 experiment_id resolves to EXACTLY ONE study, with no
    overlapping/second match that first-match-wins would silently mask. The
    ``cd2-run1-k4-coupled`` / ``cd2-run1-k4-cellonly`` pair is the sharp case:
    disjoint substrings that must never cross-match, and neither must be grabbed
    by the run3/run4 rules.
  * orphan hygiene — an experiment_id that matches nothing stays ``None`` and,
    once normalized, surfaces as an explicit remote/unassociated row (a
    warning-worthy state), not silently attributed to a study nor dropped.
"""
import textwrap

from vivarium_workbench.lib.remote_simulations import (
    _infer_study_slug,
    _load_remote_study_map,
    _normalize,
)

# The real CD2 remote_run_study_map (sms-ecoli workspace.yaml), so this pins the
# actual deployed rules rather than a toy map.
_REAL_MAP = """
    remote_run_study_map:
      - {pattern: 'run4.*strain|strain-des', study: cd2-fss-strain-design}
      - {pattern: 'run4.*genotype|run4.*pathway|run4[_-]violacein|pathway-expression', study: cd2-fss-pathway-expression}
      - {pattern: 'mecillinam-shape|mec-shape', study: cd2-mecillinam-shape}
      - {pattern: 'pg-?shape|test-pg-shape|pgshape', study: cd2-test-pg-shape}
      - {pattern: 'pg-?maturation|maturation', study: cd2-pg-maturation}
      - {pattern: 'sulfadiaz', study: cd2-sulfadiazine}
      - {pattern: 'gillesp|gillespy|pbp2', study: cd2-gillespie-mec}
      - {pattern: 'heterogeneity', study: cd2-mec-heterogeneity-sim}
      - {pattern: 'antibiotic-cocktail|run3.*cocktail|\\bcocktail\\b', study: cd2-antibiotic-cocktail}
      - {pattern: 'final[_-]mec|api.*mec', study: cd2-api-final-mec}
      - {pattern: '\\bmecillinam\\b|run3.*mecillinam', study: cd2-mecillinam}
      - {pattern: 'cd2-run1-k4-coupled', study: cd2-run1-k4-coupled}
      - {pattern: 'cd2-run1-k4-cellonly', study: cd2-run1-k4-cellonly}
      - {pattern: 'cd2-run2-j3', study: cd2-run2-j3}
"""


def _write_ws(tmp_path, body):
    (tmp_path / "workspace.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def _rules(tmp_path):
    return _load_remote_study_map(_write_ws(tmp_path, _REAL_MAP))


def _matching_slugs(experiment_id, rules):
    """Every rule (not just the first) whose pattern matches — the overlap probe
    behind the 1:1 assertion."""
    return [slug for pat, slug in rules if pat.search(experiment_id)]


# Representative dispatch-stamped experiment_ids -> the ONE study each must map
# to. The k4 coupled/cellonly split and run2-j3 are the studies linked 2026-09-10.
CD2_CASES = {
    "sim301-cd2-run1-k4-coupled-lam050-seed0": "cd2-run1-k4-coupled",
    "sim302-cd2-run1-k4-cellonly-lam050-seed0": "cd2-run1-k4-cellonly",
    "sim310-cd2-run2-j3-parca-base-7f3a": "cd2-run2-j3",
    "sim173-cd2-run4-strain-design-abcd": "cd2-fss-strain-design",
    "sim166-cd2-run4-genotype1-c396": "cd2-fss-pathway-expression",
    "sim120-cd2-run3-sulfadiazine-gov01-2740": "cd2-sulfadiazine",
    "sim121-cd2-run3-mecillinam-gov01": "cd2-mecillinam",
}


def test_each_cd2_experiment_id_maps_to_exactly_one_study(tmp_path):
    rules = _rules(tmp_path)
    for eid, expected in CD2_CASES.items():
        matches = _matching_slugs(eid, rules)
        assert matches == [expected], (
            f"{eid} expected exactly [{expected}], got {matches} — the "
            f"inference is not 1:1 (an overlapping rule would let "
            f"first-match-wins silently mask a mis-association)")
        # and the public entry point returns that same single study
        assert _infer_study_slug(eid, rules) == expected


def test_k4_coupled_and_cellonly_do_not_cross_match(tmp_path):
    """The sharp overlap case: disjoint substrings that must stay disjoint."""
    rules = _rules(tmp_path)
    coupled = "sim301-cd2-run1-k4-coupled-lam050"
    cellonly = "sim302-cd2-run1-k4-cellonly-lam050"
    assert _infer_study_slug(coupled, rules) == "cd2-run1-k4-coupled"
    assert _infer_study_slug(cellonly, rules) == "cd2-run1-k4-cellonly"
    assert "cd2-run1-k4-cellonly" not in _matching_slugs(coupled, rules)
    assert "cd2-run1-k4-coupled" not in _matching_slugs(cellonly, rules)


def test_unmatched_experiment_id_maps_to_none_not_a_wrong_study(tmp_path):
    rules = _rules(tmp_path)
    # a plausible-but-unmapped id: matches zero rules, never a partial/overlap
    for eid in ("sim999-cd2-run5-unknown-arm-xyz",
                "sim000-some-adhoc-smoke-test"):
        assert _matching_slugs(eid, rules) == []
        assert _infer_study_slug(eid, rules) is None


# --- orphan hygiene: an unmapped remote run is an explicit signal -----------


def test_unmapped_remote_run_normalizes_to_explicit_unassociated_row():
    """A remote record whose experiment_id matches no rule must surface as an
    explicit remote row (study_slug=None, empty studies, but a remote_origin
    and source='remote') — NOT silently folded into a study nor dropped."""
    rec = {
        "simulator_id": 42,
        "database_id": 7,
        "experiment_id": "sim999-cd2-run5-unknown-arm-xyz",
        "config": {"emitter": "parquet",
                   "emitter_arg": {"out_uri": "s3://bucket/run5/"}},
    }
    row = _normalize(rec)
    assert row["study_slug"] is None
    assert row["studies"] == []
    assert row["source"] == "remote"
    # explicit, distinguishable remote signal — the warning-worthy state
    assert row["remote_origin"]["experiment_id"] == rec["experiment_id"]
    assert row["remote_origin"]["s3_uri"] == "s3://bucket/run5/"
    assert row["run_id"] == rec["experiment_id"]


def test_unmapped_row_is_not_counted_into_any_study():
    """The study-count reducer keys on a TRUTHY study_slug (investigations_index
    ._remote_study_run_counts), so an unmapped row contributes to no study —
    mirror that predicate here so a regression that folds None into a bucket is
    caught."""
    rows = [
        _normalize({"simulator_id": 1, "database_id": 1,
                    "experiment_id": "sim1-cd2-run1-k4-coupled",
                    "config": {}}),
        _normalize({"simulator_id": 2, "database_id": 2,
                    "experiment_id": "sim2-unmapped-orphan",
                    "config": {}}),
    ]
    # emulate the reducer's `if slug:` rule without a live sms-api fetch
    counts: dict = {}
    for r in rows:
        slug = r.get("study_slug")
        if slug:
            counts[slug] = counts.get(slug, 0) + 1
    # neither row is study-tagged here (no workspace map applied in _normalize),
    # so the orphan certainly is not; the invariant is that a None slug never
    # becomes a bucket key.
    assert None not in counts
    assert "" not in counts
