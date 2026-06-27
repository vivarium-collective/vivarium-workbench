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
    # SimRow.studies is a union list (StudyRef objects OR bare slug strings); the
    # union must be parenthesised so it parses as an array of the whole union,
    # not "StudyRef | (string[])".
    assert "studies: (StudyRef | string)[];" in ts
    # the float/string distinction the hand-written overlay got wrong:
    assert "started_at: number | null;" in ts   # SimRow: nullable epoch float
    # SimRow.emitter was loosened to a free-form str, so it is no longer the
    # EmitterKind named alias; StudyRef.emitter (below) still uses the alias.
    assert "emitter: string | null;" in ts
