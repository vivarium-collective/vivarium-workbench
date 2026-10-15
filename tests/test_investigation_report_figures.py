"""The downloadable report inlines every study's figures + model.

Two gaps this covers:
  1. v4 studies keep their composite at ``conditions.baseline.composite`` — the
     report's model view read only the legacy top-level ``baseline:`` list, so
     current studies rendered no Model figure. ``_baseline_composite_id`` reads
     both shapes.
  2. ``local:`` live visualizations (what current workspaces use) are committed
     as rendered ``viz/*.html`` next to the study; ``_embed_html_figures`` inlines
     them so the self-contained report shows them (``_embed_visualizations`` only
     inlines ``image:``-addressed raster/vector figures).
"""
from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib.investigation_report import (
    _baseline_composite_id,
    _embed_html_figures,
)


# --- _baseline_composite_id --------------------------------------------------

def test_baseline_id_from_v4_conditions():
    spec = {"conditions": {"baseline": {"name": "b", "composite": "pkg.composites.mod.gen"}}}
    assert _baseline_composite_id(spec) == "pkg.composites.mod.gen"


def test_baseline_id_from_legacy_list():
    spec = {"baseline": [{"name": "b", "composite": "pkg.composites.legacy"}]}
    assert _baseline_composite_id(spec) == "pkg.composites.legacy"


def test_baseline_id_prefers_v4_over_legacy():
    spec = {"conditions": {"baseline": {"composite": "pkg.v4"}},
            "baseline": [{"composite": "pkg.legacy"}]}
    assert _baseline_composite_id(spec) == "pkg.v4"


def test_baseline_id_tolerates_list_shaped_conditions_baseline():
    spec = {"conditions": {"baseline": [{"composite": "pkg.listform"}]}}
    assert _baseline_composite_id(spec) == "pkg.listform"


def test_baseline_id_none_when_absent():
    assert _baseline_composite_id({}) is None
    assert _baseline_composite_id({"conditions": {"baseline": {"name": "b"}}}) is None


# --- _embed_html_figures -----------------------------------------------------

def _viz(tmp_path, sub, name, body="<div>x</div>"):
    d = tmp_path / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


def test_embed_html_figures_inlines_viz_and_charts(tmp_path):
    _viz(tmp_path, "viz", "mechanism-ladder.html", "<div>LADDER</div>")
    _viz(tmp_path, "charts", "timeseries.html", "<div>TS</div>")
    out = _embed_html_figures(tmp_path, [0], set())
    assert len(out) == 2
    by_name = {f["name"]: f for f in out}
    assert "LADDER" in by_name["Mechanism ladder"]["html"]
    assert "TS" in by_name["Timeseries"]["html"]


def test_embed_html_figures_skips_model_loom_and_dupes(tmp_path):
    _viz(tmp_path, "viz", "model-loom.html")          # reserved for the Model section
    _viz(tmp_path, "viz", "scene.html", "<div>SCENE</div>")
    out = _embed_html_figures(tmp_path, [0], {"scene.html"})   # already surfaced elsewhere
    assert out == []


def test_embed_html_figures_respects_size_cap(tmp_path):
    import vivarium_workbench.lib.investigation_report as mod
    _viz(tmp_path, "viz", "huge.html", "<div>big</div>")
    small = mod._HTML_PER_FILE_MAX
    mod._HTML_PER_FILE_MAX = 3          # force the file over the per-file cap
    try:
        out = _embed_html_figures(tmp_path, [0], set())
    finally:
        mod._HTML_PER_FILE_MAX = small
    assert len(out) == 1 and out[0].get("skipped") and "html" not in out[0]


def test_embed_html_figures_empty_when_no_viz_dirs(tmp_path):
    assert _embed_html_figures(tmp_path, [0], set()) == []


# --- _embed_native_gallery_panels --------------------------------------------

def test_embed_native_gallery_panels_inlines_run_viz(tmp_path, monkeypatch):
    """The latest run's native-gallery panels (viz.json, in-memory HTML) are
    inlined so modern studies' RESULT figures reach the downloadable report."""
    import vivarium_workbench.lib.investigation_report as mod
    monkeypatch.setattr(
        "vivarium_workbench.lib.investigation_figures.study_output_html_panels",
        lambda ws, slug: {"Vent timeseries": "<div>TS</div>", "Phase map": "<div>PM</div>"},
    )
    out = mod._embed_native_gallery_panels(tmp_path, "og-00", [0], set())
    assert {f["name"] for f in out} == {"Vent timeseries", "Phase map"}
    assert any("TS" in f.get("html", "") for f in out)


def test_embed_native_gallery_panels_dedupes_by_name(tmp_path, monkeypatch):
    """A panel whose display name is already embedded (e.g. a committed viz/*.html
    of the same figure) is not duplicated."""
    import vivarium_workbench.lib.investigation_report as mod
    monkeypatch.setattr(
        "vivarium_workbench.lib.investigation_figures.study_output_html_panels",
        lambda ws, slug: {"Shared": "<div>A</div>"},
    )
    assert mod._embed_native_gallery_panels(tmp_path, "og-00", [0], {"Shared"}) == []


def test_embed_native_gallery_panels_empty_on_error(tmp_path, monkeypatch):
    """A missing run / unreadable gallery never breaks the report."""
    import vivarium_workbench.lib.investigation_report as mod

    def _boom(ws, slug):
        raise RuntimeError("no completed run")

    monkeypatch.setattr(
        "vivarium_workbench.lib.investigation_figures.study_output_html_panels", _boom)
    assert mod._embed_native_gallery_panels(tmp_path, "og-00", [0], set()) == []


# --- _model_loom_img: dedicated (higher) per-file cap ------------------------

def test_loom_img_uses_its_own_higher_cap(tmp_path, monkeypatch):
    """A model-loom PNG larger than the GENERIC figure cap still inlines, up to
    the loom's own higher cap — a large atlas loom shouldn't silently vanish."""
    import vivarium_workbench.lib.investigation_report as mod
    d = tmp_path / "viz"; d.mkdir(parents=True)
    (d / "model-loom.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2000)  # ~2 KB
    # generic cap BELOW the file, loom cap ABOVE it → the loom still inlines
    monkeypatch.setattr(mod, "_IMG_PER_FILE_MAX", 500)
    monkeypatch.setattr(mod, "_LOOM_IMG_PER_FILE_MAX", 50_000)
    out = mod._model_loom_img(tmp_path, [0])
    assert out and out.startswith("data:image/png;base64,")


def test_loom_img_skipped_over_its_own_cap(tmp_path, monkeypatch):
    import vivarium_workbench.lib.investigation_report as mod
    d = tmp_path / "viz"; d.mkdir(parents=True)
    (d / "model-loom.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2000)
    monkeypatch.setattr(mod, "_LOOM_IMG_PER_FILE_MAX", 100)  # below the file size
    assert mod._model_loom_img(tmp_path, [0]) is None
