"""The post-run analysis-provider seam: discovery + the output-layout contract.

The core serves any workspace, so the post-run output contract is a provider's to
override; with no provider (the default) the landing helpers behave exactly as
before. These tests pin that default and the override/merge semantics.
"""
from vivarium_workbench.lib import analysis_provider as ap


def _reset():
    ap.reset()


def test_output_layout_defaults_when_no_provider(monkeypatch):
    monkeypatch.setattr(ap, "providers", lambda: ())
    layout = ap.output_layout()
    assert layout == ap.DEFAULT_OUTPUT_LAYOUT
    assert layout.analysis_json_glob == "**/analyses/*/analysis.json"
    assert layout.fold_subdirs == ("viz", "ptools")
    assert layout.filename_sep == "__"
    assert layout.ptools_glob == "**/ptools/*.tsv"


def test_output_layout_applies_a_partial_provider_override(monkeypatch):
    class _P:
        name = "fake"
        api_version = ap.ANALYSIS_PROVIDER_API_VERSION

        def output_layout(self):
            return {"filename_sep": "::", "fold_subdirs": ["figures"]}

    monkeypatch.setattr(ap, "providers", lambda: (_P(),))
    layout = ap.output_layout()
    assert layout.filename_sep == "::"
    assert layout.fold_subdirs == ("figures",)
    # keys the provider omits keep their default
    assert layout.analysis_json_glob == ap.DEFAULT_OUTPUT_LAYOUT.analysis_json_glob
    assert layout.ptools_glob == ap.DEFAULT_OUTPUT_LAYOUT.ptools_glob


def test_output_layout_ignores_a_broken_provider(monkeypatch):
    class _Bad:
        name = "bad"
        api_version = ap.ANALYSIS_PROVIDER_API_VERSION

        def output_layout(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(ap, "providers", lambda: (_Bad(),))
    assert ap.output_layout() == ap.DEFAULT_OUTPUT_LAYOUT  # defaults, not a crash


def test_providers_off_switch(monkeypatch):
    _reset()
    monkeypatch.setenv(ap.PROVIDERS_ENV, "none")
    _reset()
    assert ap.providers() == ()
    _reset()


def test_providers_empty_when_none_installed(monkeypatch):
    # No analysis-provider entry point in the test env -> empty tuple -> the
    # landing helpers fall back to DEFAULT_OUTPUT_LAYOUT (today's behaviour).
    monkeypatch.setattr(ap, "_entry_points", lambda: [])
    _reset()
    assert ap.providers() == ()
    _reset()
