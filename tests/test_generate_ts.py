"""The generated TypeScript types must stay in sync with the pydantic models."""

from vivarium_dashboard.lib.generate_ts import OUTPUT_PATH, generate_ts


def test_generated_ts_is_current():
    """The committed domain.generated.d.ts must match a fresh generation.

    If this fails, run:  python -m vivarium_dashboard.lib.generate_ts
    (the models changed without regenerating the TypeScript types).
    """
    expected = generate_ts()
    actual = OUTPUT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "domain.generated.d.ts is stale — regenerate with "
        "`python -m vivarium_dashboard.lib.generate_ts`"
    )


def test_generated_ts_has_expected_contract():
    """Sanity: the key aliases/interfaces are present and reference each other."""
    ts = generate_ts()
    assert "export type EmitterKind = 'xarray' | 'parquet' | 'sqlite';" in ts
    assert "export interface SimRow {" in ts
    # nested-model references resolve by name, not inlined:
    assert "remote_origin: RemoteOrigin | null;" in ts
    assert "studies: StudyRef[];" in ts
    # the float/string distinction the hand-written overlay got wrong:
    assert "started_at: number;" in ts          # SimRow: epoch float
    assert "emitter: EmitterKind | null;" in ts  # named alias, not inlined union
