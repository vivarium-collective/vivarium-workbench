from vivarium_dashboard.lib.observables_views import _resolve_registry_ref

KEYS = [
    "v2ecoli.composites.baseline",
    "v2ecoli.composites.baseline.baseline",
    "v2ecoli.composites.baseline_millard.baseline_millard",
    "pbg_ketchup.composites.estimation.ketchup_baseline",
]


def test_exact_match_wins():
    assert _resolve_registry_ref("v2ecoli.composites.baseline", KEYS) == "v2ecoli.composites.baseline"


def test_short_ref_resolves_to_canonical_shortest():
    # 'baseline' matches only ...composites.baseline (tail 'baseline'); the
    # ...baseline.baseline key has tail 'baseline.baseline' and must NOT match.
    assert _resolve_registry_ref("baseline", KEYS) == "v2ecoli.composites.baseline"


def test_short_ref_does_not_falsely_match_other_composite():
    assert _resolve_registry_ref("baseline_millard", KEYS) == "v2ecoli.composites.baseline_millard.baseline_millard"
    # And "baseline" must NOT cross-resolve to the millard composite.
    assert _resolve_registry_ref("baseline", KEYS) == "v2ecoli.composites.baseline"
    assert _resolve_registry_ref("baseline", KEYS) != "v2ecoli.composites.baseline_millard.baseline_millard"


def test_unknown_ref_returns_none():
    assert _resolve_registry_ref("totally_unknown_xyz", KEYS) is None
