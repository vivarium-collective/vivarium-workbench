"""The generated TypeScript types must stay in sync with the pydantic models.

The committed file carries each backend provider's ``payload_types()`` section (step P3 M1), so
``test_generated_ts_is_current`` needs the SMS plugin installed with providers on (it becomes a ``plugin``-marked
test, step P3 K1); the providers-off tests below are core.
"""

import pytest

from vivarium_workbench.lib import generate_ts as gen
from vivarium_workbench.lib.backends import provider
from vivarium_workbench.lib.generate_ts import OUTPUT_PATH, generate_ts


# Needs the SMS plugin with providers on (its payload_types() section).
@pytest.mark.plugin
def test_generated_ts_is_current():
    """The committed domain.generated.d.ts must match a fresh generation.

    If this fails, run:  python -m vivarium_workbench.lib.generate_ts
    (the models changed without regenerating the TypeScript types).
    """
    expected = generate_ts()
    actual = OUTPUT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "domain.generated.d.ts is stale — regenerate with "
        "`python -m vivarium_workbench.lib.generate_ts`"
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


@pytest.fixture
def providers_off(monkeypatch):
    monkeypatch.setenv(provider.PROVIDERS_ENV, "none")
    provider.reset()
    yield
    monkeypatch.delenv(provider.PROVIDERS_ENV)
    provider.reset()


def _blocks(text):
    return {b.strip() for b in text.split("\n\n")}


def test_providers_off_blocks_are_a_subset_of_the_committed_file(providers_off):
    """With providers off the core's own declarations are all there is, and every one of them is in the committed
    file verbatim (the comment header aside): the provider sections only add blocks."""
    off = {b for b in _blocks(generate_ts()) if not b.startswith("//")}
    assert off <= _blocks(OUTPUT_PATH.read_text(encoding="utf-8"))
    assert not any(b.startswith("export type RemoteJobStatus") for b in off)


def test_main_refuses_to_write_with_providers_off(providers_off, tmp_path, monkeypatch):
    out = tmp_path / "domain.generated.d.ts"
    monkeypatch.setattr(gen, "OUTPUT_PATH", out)
    with pytest.raises(SystemExit, match="no backend provider is active"):
        gen.main()
    assert not out.exists()
