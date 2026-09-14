"""Task V6 — declared `threejs:`/`html:` self-contained-HTML figures.

End-to-end (not monkeypatched): a study declares a `visualizations:` entry
pointing at a self-contained HTML file via `threejs:`/`html:`. It must:

  * resolve through `study_charts.build_study_charts_payload` to a chart
    record with the interactive `media` marker and an `iframe_url` (no
    img/svg payload — it renders as an iframe, not a static image);
  * make `viz_gate.study_visualization_status` report `has_interactive is
    True` for a study whose ONLY figure is that declared figure.

Also pins the no-regression requirement: the existing static schemes
(`png:`/`svg:`) still resolve to a static image record and do NOT flip
`has_interactive`.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from vivarium_workbench.lib import viz_gate
from vivarium_workbench.lib.study_charts import build_study_charts_payload


def _build_study(tmp_path: Path, *, address: str, fig_name: str, fig_bytes_or_text) -> tuple[Path, str]:
    ws = tmp_path / "ws"
    study_dir = ws / "studies" / "demo"
    study_dir.mkdir(parents=True)
    fig_path = study_dir / fig_name
    if isinstance(fig_bytes_or_text, bytes):
        fig_path.write_bytes(fig_bytes_or_text)
    else:
        fig_path.write_text(fig_bytes_or_text, encoding="utf-8")
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({
            "name": "demo",
            "visualizations": [{"name": "fig", "address": address}],
        }),
        encoding="utf-8",
    )
    return ws, "demo"


def test_threejs_declared_figure_is_interactive_end_to_end(tmp_path):
    ws, name = _build_study(
        tmp_path, address="threejs:scene.html", fig_name="scene.html",
        fig_bytes_or_text="<html><body>3D scene</body></html>",
    )
    payload = build_study_charts_payload(ws, name)
    [rec] = [c for c in payload["charts"] if c.get("source") == "declared"]
    assert rec["media"] == "threejs"
    assert rec["iframe_url"] == "/studies/demo/scene.html"
    assert "img" not in rec
    assert "svg" not in rec

    status = viz_gate.study_visualization_status(ws, name)
    assert status["has_interactive"] is True
    assert status["n_figures"] >= 1


def test_html_declared_figure_is_interactive_end_to_end(tmp_path):
    ws, name = _build_study(
        tmp_path, address="html:page.html", fig_name="page.html",
        fig_bytes_or_text="<html><body>self-contained</body></html>",
    )
    payload = build_study_charts_payload(ws, name)
    [rec] = [c for c in payload["charts"] if c.get("source") == "declared"]
    assert rec["media"] == "html"
    assert rec["iframe_url"] == "/studies/demo/page.html"

    status = viz_gate.study_visualization_status(ws, name)
    assert status["has_interactive"] is True


def test_declared_png_figure_still_static_no_regression(tmp_path):
    ws, name = _build_study(
        tmp_path, address="png:fig.png", fig_name="fig.png",
        fig_bytes_or_text=b"\x89PNG",
    )
    payload = build_study_charts_payload(ws, name)
    [rec] = [c for c in payload["charts"] if c.get("source") == "declared"]
    assert rec["media"] == "png"
    assert rec["img"].startswith("data:image/png;base64,")
    assert "iframe_url" not in rec

    status = viz_gate.study_visualization_status(ws, name)
    assert status["has_interactive"] is False


def test_declared_svg_figure_still_static_no_regression(tmp_path):
    ws, name = _build_study(
        tmp_path, address="svg:fig.svg", fig_name="fig.svg",
        fig_bytes_or_text="<svg><rect/></svg>",
    )
    payload = build_study_charts_payload(ws, name)
    [rec] = [c for c in payload["charts"] if c.get("source") == "declared"]
    assert rec["media"] == "svg"
    assert rec["svg"] == "<svg><rect/></svg>"

    status = viz_gate.study_visualization_status(ws, name)
    assert status["has_interactive"] is False


def test_declared_gif_figure_still_interactive_no_regression(tmp_path):
    ws, name = _build_study(
        tmp_path, address="gif:fig.gif", fig_name="fig.gif",
        fig_bytes_or_text=b"GIF89aFAKE",
    )
    payload = build_study_charts_payload(ws, name)
    [rec] = [c for c in payload["charts"] if c.get("source") == "declared"]
    assert rec["media"] == "gif"

    status = viz_gate.study_visualization_status(ws, name)
    assert status["has_interactive"] is True


# ---------------------------------------------------------------------------
# Real HTTP round-trip: GET /api/study-charts/<slug> must NOT strip
# `iframe_url` — ChartPayload is `extra="allow"` for exactly this kind of
# source-specific field, but that's worth pinning at the live-server seam
# (StudyChartsPayload is a real pydantic response_model, not a passthrough).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Static publish: a declared html: figure must be SELF-CONTAINED in the
# snapshot (a `srcdoc` with its Plotly.js inlined), not a bare `iframe_url`
# pointing at a file the bundle never materializes — which 404s on the
# read-only gh-pages snapshot, and doubly so under a non-root base path.
# ---------------------------------------------------------------------------

_PLOTLY_PANEL_HTML = (
    "<html><body>\n"
    '<div id="fig" class="plotly-graph-div"></div>\n'
    '<script charset="utf-8" src="../../../plotly.min.js"></script>\n'
    '<script>Plotly.newPlot("fig", [{"y":[1,2,3],"type":"scatter"}]);</script>\n'
    "</body></html>"
)


def _publish_charts_payload(ws: Path, out: Path, slug: str, *, base_path: str = "") -> dict:
    """Run the publisher and return the study-charts payload it wrote."""
    from vivarium_workbench import publish

    publish.build_bundle(ws, out, base_path=base_path)
    charts_file = out / "api" / "study-charts" / f"{slug}.json"
    assert charts_file.is_file(), "study-charts payload missing from bundle"
    return json.loads(charts_file.read_text(encoding="utf-8"))


def _declared_iframe_workspace(tmp_path: Path) -> tuple[Path, str]:
    """A one-study, one-investigation workspace whose only figure is a declared
    html: Plotly panel referencing the workspace's shared plotly.min.js."""
    ws = tmp_path / "ws"
    (ws / "workspace.yaml").parent.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    # The shared plotly.min.js the panel's ../../../plotly.min.js resolves to.
    (ws / "plotly.min.js").write_text(
        "/* PLOTLY-LIB-MARKER */ window.Plotly={newPlot:function(){}};",
        encoding="utf-8",
    )
    inv = ws / "investigations" / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "inv", "title": "Inv", "studies": ["demo"],
                        "status": "in_progress"}),
        encoding="utf-8",
    )
    study_dir = ws / "studies" / "demo"
    (study_dir / "viz").mkdir(parents=True)
    (study_dir / "viz" / "panel.html").write_text(_PLOTLY_PANEL_HTML, encoding="utf-8")
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({
            "schema_version": 3,
            "name": "demo",
            "baseline": [{"name": "core", "composite": "pkg.demo.Core"}],
            "variants": [],
            "visualizations": [{"name": "metrics", "address": "html:viz/panel.html"}],
        }),
        encoding="utf-8",
    )
    return ws, "demo"


def test_publish_inlines_declared_iframe_as_self_contained_srcdoc(tmp_path):
    ws, slug = _declared_iframe_workspace(tmp_path)
    payload = _publish_charts_payload(ws, tmp_path / "bundle", slug)

    [rec] = [c for c in payload["charts"] if c.get("source") == "declared"]
    assert rec["media"] == "html"
    # Self-contained: srcdoc present, dangling iframe_url gone.
    assert "iframe_url" not in rec, "static snapshot must not point at an unbundled file"
    assert "srcdoc" in rec and rec["srcdoc"], "declared figure must inline as srcdoc"
    srcdoc = rec["srcdoc"]
    # The figure's own newPlot call survives; the Plotly library is inlined
    # (the external <script src=…plotly…> loader is gone).
    assert "Plotly.newPlot" in srcdoc
    assert "PLOTLY-LIB-MARKER" in srcdoc, "shared plotly.min.js not inlined"
    assert "plotly.min.js" not in srcdoc, "external plotly loader must be removed"


def test_publish_srcdoc_is_base_path_independent(tmp_path):
    """Same figure published under a non-root base path stays self-contained —
    a srcdoc has no URL to rewrite, so base-path hosting can't break it."""
    ws, slug = _declared_iframe_workspace(tmp_path)
    payload = _publish_charts_payload(
        ws, tmp_path / "bundle", slug, base_path="/meta-modelers-guide/dashboard")

    [rec] = [c for c in payload["charts"] if c.get("source") == "declared"]
    assert "iframe_url" not in rec
    assert "PLOTLY-LIB-MARKER" in rec.get("srcdoc", "")


def test_publish_helper_leaves_non_iframe_and_missing_untouched(tmp_path):
    """`_inline_declared_iframe_figures` only rewrites html:/threejs: iframe
    records with a readable source; image/svg records and dangling refs are
    left exactly as-is (no crash, no spurious srcdoc)."""
    from vivarium_workbench.publish import _inline_declared_iframe_figures

    payload = {"charts": [
        {"media": "png", "img": "data:image/png;base64,AAAA", "source": "declared"},
        {"media": "svg", "svg": "<svg/>", "source": "declared"},
        {"media": "html", "iframe_url": "/studies/demo/viz/gone.html",
         "source": "declared"},  # file does not exist → untouched
    ]}
    _inline_declared_iframe_figures(payload, tmp_path)
    png, svg, missing = payload["charts"]
    assert png == {"media": "png", "img": "data:image/png;base64,AAAA", "source": "declared"}
    assert svg == {"media": "svg", "svg": "<svg/>", "source": "declared"}
    assert missing["iframe_url"] == "/studies/demo/viz/gone.html"
    assert "srcdoc" not in missing


def test_study_charts_api_carries_iframe_url_and_media(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    study_dir = ws / "studies" / "demo3d"
    study_dir.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (study_dir / "scene.html").write_text(
        "<html><body>3D scene</body></html>", encoding="utf-8")
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({
            "schema_version": 3,
            "name": "demo3d",
            "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
            "variants": [],
            "visualizations": [
                {"name": "colony-3d", "address": "threejs:scene.html"},
            ],
        }),
        encoding="utf-8",
    )

    client = dashboard_client(ws)
    resp = client.get("/api/study-charts/demo3d")
    assert resp.status_code == 200
    body = resp.json()
    [rec] = [c for c in body["charts"] if c.get("source") == "declared"]
    assert rec["media"] == "threejs"
    assert rec["iframe_url"] == "/studies/demo3d/scene.html"


def test_declared_figure_that_is_also_an_embed_is_deduped(tmp_path):
    """A visualizations[] figure that is ALSO declared in embed_visualizations
    (rendered separately as an iframe in the study tab + report) must NOT also
    appear as a declared chart — otherwise it shows twice. A declared figure with
    no matching embed is still included."""
    ws = tmp_path / "ws"
    study_dir = ws / "studies" / "demo"
    study_dir.mkdir(parents=True)
    (study_dir / "shown.html").write_text("<html>shown</html>", encoding="utf-8")
    (study_dir / "extra.html").write_text("<html>extra</html>", encoding="utf-8")
    (study_dir / "study.yaml").write_text(yaml.safe_dump({
        "name": "demo",
        "visualizations": [
            {"name": "shown", "address": "html:shown.html"},
            {"name": "extra", "address": "html:extra.html"},
        ],
        "embed_visualizations": [
            {"name": "shown", "url": "/studies/demo/shown.html"},
        ],
    }), encoding="utf-8")
    charts = build_study_charts_payload(ws, "demo")["charts"]
    declared = {c.get("key") for c in charts if c.get("source") == "declared"}
    assert "shown" not in declared, "an embedded figure must not also be a declared chart"
    assert "extra" in declared, "a declared figure with no embed must still render"
