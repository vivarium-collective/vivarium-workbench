"""discover_static_study_charts: SVG inline + PNG/GIF base64 + dedupe + meta."""
from pathlib import Path

from vivarium_workbench.lib.study_charts import (
    discover_static_study_charts,
    discover_declared_figure_charts,
)


def _charts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "charts"
    d.mkdir()
    return d


def test_missing_dir_returns_empty(tmp_path):
    assert discover_static_study_charts(tmp_path / "nope") == []


def test_svg_inlined_verbatim(tmp_path):
    d = _charts_dir(tmp_path)
    (d / "00_vector.svg").write_text("<svg>hi</svg>")
    [rec] = discover_static_study_charts(d)
    assert rec["media"] == "svg"
    assert rec["svg"] == "<svg>hi</svg>"
    assert "img" not in rec


def test_png_and_gif_become_data_uris(tmp_path):
    d = _charts_dir(tmp_path)
    (d / "01_photo.png").write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    (d / "02_anim.gif").write_bytes(b"GIF89aFAKE")
    recs = {r["key"]: r for r in discover_static_study_charts(d)}
    assert recs["01_photo"]["media"] == "png"
    assert recs["01_photo"]["img"].startswith("data:image/png;base64,")
    assert recs["02_anim"]["media"] == "gif"
    assert recs["02_anim"]["img"].startswith("data:image/gif;base64,")
    # raster records carry no inline svg
    assert "svg" not in recs["01_photo"]


def test_same_stem_svg_wins_over_raster(tmp_path):
    d = _charts_dir(tmp_path)
    (d / "fig.svg").write_text("<svg>vector</svg>")
    (d / "fig.png").write_bytes(b"\x89PNGdupe")
    recs = discover_static_study_charts(d)
    assert len(recs) == 1
    assert recs[0]["media"] == "svg"


def test_meta_sidecar_applies_to_raster(tmp_path):
    d = _charts_dir(tmp_path)
    (d / "01_photo.png").write_bytes(b"\x89PNG")
    (d / "01_photo.meta.json").write_text('{"title":"Photo T","caption":"cap"}')
    [rec] = discover_static_study_charts(d)
    assert rec["title"] == "Photo T"
    assert rec["caption"] == "cap"


def test_records_sorted_by_key(tmp_path):
    d = _charts_dir(tmp_path)
    (d / "02_b.png").write_bytes(b"\x89PNG")
    (d / "00_a.svg").write_text("<svg/>")
    (d / "01_c.gif").write_bytes(b"GIF89a")
    keys = [r["key"] for r in discover_static_study_charts(d)]
    assert keys == ["00_a", "01_c", "02_b"]


# ── hide_superseded (feedback-friction opt-in chart hiding) ──────────────────

def test_hide_superseded_default_keeps_all(tmp_path):
    """Default (hide_superseded=False) is unchanged — no charts hidden."""
    d = _charts_dir(tmp_path)
    (d / "00_a.svg").write_text("<svg/>")
    (d / "01_b.png").write_bytes(b"\x89PNG")
    assert len(discover_static_study_charts(d)) == 2


def test_hide_superseded_true_no_manifest_keeps_all(tmp_path):
    """Opt-in but no resolvable canonical run → empty skip-set → safe no-op."""
    d = _charts_dir(tmp_path)
    (d / "00_a.svg").write_text("<svg/>")
    (d / "01_b.png").write_bytes(b"\x89PNG")
    # No chart_store manifest / canonical run in this bare tmp study, so
    # superseded_chart_names() returns empty and nothing is hidden.
    assert len(discover_static_study_charts(d, hide_superseded=True)) == 2


def test_hide_superseded_filters_named_charts(monkeypatch, tmp_path):
    """When chart_store reports a superseded basename, it's dropped (True path)."""
    chart_store = __import__("viva_superpowers.chart_store",
                             fromlist=["superseded_chart_names"])
    monkeypatch.setattr(chart_store, "superseded_chart_names",
                        lambda study_dir: {"01_old.png"})
    d = _charts_dir(tmp_path)
    (d / "00_keep.svg").write_text("<svg/>")
    (d / "01_old.png").write_bytes(b"\x89PNG")
    keys = [r["key"] for r in discover_static_study_charts(d, hide_superseded=True)]
    assert keys == ["00_keep"]
    # Default path ignores the skip-set entirely.
    keys_default = [r["key"] for r in discover_static_study_charts(d)]
    assert set(keys_default) == {"00_keep", "01_old"}


# ── discover_declared_figure_charts (BUG 4: declared gif: figures embed) ──────

def test_declared_gif_address_in_study_root(tmp_path):
    """A `gif:colony.gif` visualization resolves a loose study-root file into a
    self-contained data-URI chart — the colonies-report regression."""
    (tmp_path / "colony.gif").write_bytes(b"GIF89aFAKE")
    viz = [{"name": "colony-animation", "address": "gif:colony.gif",
            "description": "Animated colony"}]
    [rec] = discover_declared_figure_charts(tmp_path, viz)
    assert rec["media"] == "gif"
    assert rec["img"].startswith("data:image/gif;base64,")
    assert rec["title"] == "colony-animation"
    assert rec["caption"] == "Animated colony"
    assert rec["source"] == "declared"


def test_declared_svg_address_inlined(tmp_path):
    (tmp_path / "fig.svg").write_text("<svg>x</svg>")
    [rec] = discover_declared_figure_charts(tmp_path, [{"address": "svg:fig.svg"}])
    assert rec["media"] == "svg"
    assert rec["svg"] == "<svg>x</svg>"


def test_declared_live_renderer_addresses_skipped(tmp_path):
    """local:/dashboard: renderer addresses are left to the live path."""
    viz = [{"address": "dashboard:study_charts"}, {"address": "local:Foo"}]
    assert discover_declared_figure_charts(tmp_path, viz) == []


def test_declared_missing_file_skipped(tmp_path):
    assert discover_declared_figure_charts(tmp_path, [{"address": "gif:gone.gif"}]) == []


def test_declared_figure_found_in_charts_subdir(tmp_path):
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "a.png").write_bytes(b"\x89PNG")
    [rec] = discover_declared_figure_charts(tmp_path, [{"address": "png:a.png"}])
    assert rec["img"].startswith("data:image/png;base64,")


# ── Task V6: threejs:/html: self-contained-HTML declared figures ──────────
#
# A `visualizations:` entry whose address is `threejs:<file>.html` or
# `html:<file>.html` resolves to a chart record that renders as an IFRAME
# (not an <img>/inline <svg>) and carries the interactive marker viz_gate's
# `_INTERACTIVE_KINDS` already recognises for `threejs`/`html`.

def test_declared_threejs_address_resolves_to_iframe_record(tmp_path):
    (tmp_path / "scene.html").write_text("<html><body>3D</body></html>")
    viz = [{"name": "colony-3d", "address": "threejs:scene.html",
            "description": "Interactive colony render"}]
    [rec] = discover_declared_figure_charts(tmp_path, viz)
    assert rec["media"] == "threejs"
    assert rec["iframe_url"] == "/scene.html"
    assert rec["title"] == "colony-3d"
    assert rec["caption"] == "Interactive colony render"
    assert rec["source"] == "declared"
    assert rec["run_id"] is None
    # Not a static-image record — no img/svg payload.
    assert "img" not in rec
    assert "svg" not in rec


def test_declared_html_address_resolves_to_iframe_record(tmp_path):
    (tmp_path / "page.html").write_text("<html><body>self-contained</body></html>")
    [rec] = discover_declared_figure_charts(tmp_path, [{"address": "html:page.html"}])
    assert rec["media"] == "html"
    assert rec["iframe_url"] == "/page.html"
    assert "img" not in rec
    assert "svg" not in rec


def test_declared_iframe_missing_file_skipped(tmp_path):
    assert discover_declared_figure_charts(tmp_path, [{"address": "threejs:gone.html"}]) == []


def test_declared_iframe_found_in_viz_subdir(tmp_path):
    (tmp_path / "viz").mkdir()
    (tmp_path / "viz" / "b.html").write_text("<html></html>")
    [rec] = discover_declared_figure_charts(tmp_path, [{"address": "html:b.html"}])
    assert rec["iframe_url"] == "/viz/b.html"


def test_declared_iframe_url_relative_to_ws_root_when_study_nested(tmp_path):
    """When the study lives under investigations/<inv>/studies/<slug> (nested
    layout), the iframe URL must be relative to the workspace root — the
    catch-all static route (`lib.static_serving.resolve_asset`) resolves
    every URL against `ws_root`, not the study dir."""
    ws_root = tmp_path / "ws"
    study_dir = ws_root / "investigations" / "inv1" / "studies" / "demo"
    study_dir.mkdir(parents=True)
    (study_dir / "scene.html").write_text("<html></html>")
    [rec] = discover_declared_figure_charts(
        study_dir, [{"address": "threejs:scene.html"}], ws_root=ws_root)
    assert rec["iframe_url"] == "/investigations/inv1/studies/demo/scene.html"


# ---------------------------------------------------------------------------
# run_id provenance (Fable §4.5, Task V3): a static chart's <name>.meta.json
# sidecar (viz_freshness.stamp_meta) is the genuine, already-recorded link to
# the run that produced it — reuse it rather than guess. A chart with no
# stamped source_run_id, or one resolved via a hand-authored `visualizations:`
# declaration, carries no run association and must stay null.
# ---------------------------------------------------------------------------


def test_static_chart_with_stamped_meta_carries_run_id(tmp_path):
    d = _charts_dir(tmp_path)
    (d / "01_photo.png").write_bytes(b"\x89PNG")
    (d / "01_photo.meta.json").write_text('{"source_run_id":"run-42"}')
    [rec] = discover_static_study_charts(d)
    assert rec["run_id"] == "run-42"


def test_static_chart_without_meta_run_id_is_null(tmp_path):
    d = _charts_dir(tmp_path)
    (d / "00_a.svg").write_text("<svg/>")
    [rec] = discover_static_study_charts(d)
    assert rec["run_id"] is None


def test_static_chart_with_meta_but_no_source_run_id_is_null(tmp_path):
    d = _charts_dir(tmp_path)
    (d / "01_photo.png").write_bytes(b"\x89PNG")
    (d / "01_photo.meta.json").write_text('{"title":"Photo T"}')
    [rec] = discover_static_study_charts(d)
    assert rec["run_id"] is None


def test_declared_figure_chart_run_id_always_null(tmp_path):
    """Declared/hand-authored visualizations[] figures never carry a run_id,
    even when the resolved file happens to have a stamped meta sidecar — the
    author declared it, so no run derivation is claimed."""
    (tmp_path / "charts").mkdir()
    fig = tmp_path / "charts" / "a.png"
    fig.write_bytes(b"\x89PNG")
    (tmp_path / "charts" / "a.meta.json").write_text('{"source_run_id":"run-1"}')
    [rec] = discover_declared_figure_charts(tmp_path, [{"address": "png:a.png"}])
    assert rec["run_id"] is None


# ── Tolerant discovery: figures declared via a bare file-pointer field ────────
#
# A `visualizations:` entry may point at a figure FILE with the bare
# `path:`/`file:`/`chart:`/`src` field instead of the canonical
# `address: <scheme>:<file>`. That must resolve too — including self-contained
# HTML (rendered as an iframe) — so a workspace that used `path: viz/x.html`
# still gets its figure rendered AND downloadable.

def test_declared_html_via_path_field_resolves_to_iframe(tmp_path):
    (tmp_path / "viz").mkdir()
    (tmp_path / "viz" / "chart.html").write_text("<html><body>plotly</body></html>")
    [rec] = discover_declared_figure_charts(tmp_path, [{"name": "chart", "path": "viz/chart.html"}])
    assert rec["media"] == "html"
    assert rec["iframe_url"] == "/viz/chart.html"
    assert rec["title"] == "chart"
    assert "img" not in rec and "svg" not in rec


def test_declared_image_via_path_field_resolves_to_img(tmp_path):
    (tmp_path / "viz").mkdir()
    (tmp_path / "viz" / "p.png").write_bytes(b"\x89PNG")
    [rec] = discover_declared_figure_charts(tmp_path, [{"name": "p", "path": "viz/p.png"}])
    assert rec["media"] == "png"
    assert rec["img"].startswith("data:image/png;base64,")
