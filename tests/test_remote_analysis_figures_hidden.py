"""The figure listing hides chromosome_state_view (per-timestep noise) from the
gallery, and only for viz listings — ptools/other prefixes are untouched."""
from __future__ import annotations

from vivarium_workbench.lib import remote_analysis_figures as raf


def test_figure_base_name_strips_variant_suffix():
    assert raf._figure_base_name(
        "viz/chromosome_state_view__variant=0_seed=0_gen=3_agent=000.html"
    ) == "chromosome_state_view"
    assert raf._figure_base_name("viz/mass_fraction_summary__variant=0.html") == "mass_fraction_summary"
    assert raf._figure_base_name("mass_fraction_summary.html") == "mass_fraction_summary"


class _FakeS3:
    """Minimal list_objects_v2 stub returning a fixed key set (no pagination)."""
    def __init__(self, keys):
        self._keys = keys

    def list_objects_v2(self, **kw):
        prefix = kw.get("Prefix", "")
        contents = [{"Key": k, "Size": 10} for k in self._keys if k.startswith(prefix)]
        return {"Contents": contents, "IsTruncated": False}


def test_list_prefix_hides_chromosome_only_under_viz():
    keys = [
        "out/analyses/a1/viz/chromosome_state_view__variant=0_gen=0_agent=0.html",
        "out/analyses/a1/viz/chromosome_state_view__variant=0_gen=1_agent=00.html",
        "out/analyses/a1/viz/mass_fraction_summary__variant=0.html",
    ]
    s3 = _FakeS3(keys)

    # viz listing: chromosome_state_view is filtered, the useful one stays.
    figs = raf._list_prefix(s3, "b", "out/analyses/a1/viz", "out/analyses/a1", raf._FIGURE_SUFFIXES)
    names = sorted(raf._figure_base_name(f["path"]) for f in figs)
    assert names == ["mass_fraction_summary"]
    assert all("chromosome_state_view" not in f["path"] for f in figs)

    # a NON-viz listing over the same keys keeps chromosome_state_view (the hide
    # is scoped to viz/, so ptools and other prefixes are never touched).
    all_html = raf._list_prefix(s3, "b", "out/analyses/a1", "out/analyses/a1", raf._FIGURE_SUFFIXES)
    assert any("chromosome_state_view" in f["path"] for f in all_html)
