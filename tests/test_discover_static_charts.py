"""discover_static_study_charts: SVG inline + PNG/GIF base64 + dedupe + meta."""
from pathlib import Path

from vivarium_dashboard.lib.study_charts import discover_static_study_charts


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
