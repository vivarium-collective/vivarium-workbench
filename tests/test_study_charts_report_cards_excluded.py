"""Report cards are Tests-tab verdicts, not Visualizations.

A study's ``viz/report_card/<card>.html`` (+ ``<card>.verdict.json``) renders on
the Tests / Report-cards tab via ``report_card_section.render_report_cards_section``.
It is NOT a visualization. A name-agnostic fall-through in
``discover_declared_figure_charts`` used to ``rglob`` every ``viz/**/*.html`` into
an iframe figure, which swept report cards into the Visualizations charts payload
(``build_study_charts_payload``) — they showed as blank "CHART <verdict-name>"
boxes (the card's caption, no figure) in both the live dashboard and the published
read-only snapshot, since both share this one payload builder.

These tests pin that report-card HTML is excluded from the charts list while a
real ``charts/*.svg`` figure (declared in ``visualizations:``) keeps rendering.
"""
from __future__ import annotations

import json

import yaml

from vivarium_workbench.lib.study_charts import (
    build_study_charts_payload,
    discover_declared_figure_charts,
)


def _ws_with_study(tmp_path):
    """A study that has BOTH a real declared figure and a report card."""
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    sd = tmp_path / "studies" / "s1"
    (sd / "charts").mkdir(parents=True, exist_ok=True)
    (sd / "viz" / "report_card").mkdir(parents=True, exist_ok=True)

    # Real figure: a committed SVG declared in visualizations[].
    (sd / "charts" / "01_grid.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', encoding="utf-8")

    # Report card: viz/report_card/<card>.html + verdict sidecar (Tests-tab only).
    (sd / "viz" / "report_card" / "ensembles_demonstrated.html").write_text(
        "<html><body><p>Severity x engine batch, gathered into one grid.</p>"
        "</body></html>", encoding="utf-8")
    (sd / "viz" / "report_card" / "ensembles_demonstrated.verdict.json").write_text(
        json.dumps({"overall": "pass"}), encoding="utf-8")

    (sd / "study.yaml").write_text(
        yaml.safe_dump({
            "name": "s1",
            "visualizations": [
                {"name": "Severity grid", "address": "image:charts/01_grid.svg"},
            ],
        }),
        encoding="utf-8",
    )
    return tmp_path, sd


def test_report_card_html_excluded_from_charts_payload(tmp_path):
    ws, _sd = _ws_with_study(tmp_path)
    p = build_study_charts_payload(ws, "s1")
    keys = {c.get("key") for c in p["charts"]}
    # The report-card verdict name must NOT appear as a chart.
    assert "ensembles_demonstrated" not in keys, \
        "a viz/report_card/*.html leaked into the Visualizations charts payload"
    # No chart iframe may point at the report_card dir.
    for c in p["charts"]:
        assert "report_card" not in (c.get("iframe_url") or ""), \
            f"a chart points at a report card: {c.get('iframe_url')}"
    # The real declared figure still renders — under its study-authored name
    # ("Severity grid"), not the bare file stem: the committed charts/01_grid.svg
    # is deduped into the single declared record (see test_study_charts_dedup).
    assert "Severity grid" in keys, "the real charts/*.svg figure disappeared"
    assert "01_grid" not in keys, "the committed figure was not deduped against its declaration"


def test_declared_figures_helper_skips_report_cards(tmp_path):
    _ws, sd = _ws_with_study(tmp_path)
    figs = discover_declared_figure_charts(sd, [
        {"name": "Severity grid", "address": "image:charts/01_grid.svg"},
    ], ws_root=sd)
    keys = {f.get("key") for f in figs}
    assert "ensembles_demonstrated" not in keys, \
        "discover_declared_figure_charts surfaced a report card as a figure"
    assert "01_grid" in keys or "Severity grid" in keys, \
        "the declared SVG figure was dropped"
