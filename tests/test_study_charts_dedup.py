"""A committed figure that is ALSO declared must render once, not twice.

``build_study_charts_payload`` merges two chart sources: pre-rendered
``charts/*.svg`` files (``discover_static_study_charts``, keyed by file STEM) and
the study's ``visualizations:`` declarations (``discover_declared_figure_charts``,
keyed by the entry NAME). When a declared visualization points at a committed
``charts/<stem>.svg``, the two sources describe the SAME file under DIFFERENT keys
(name vs stem), so the by-key dedup misses and the figure appears twice in the
Visualizations tab — once as the declared figure (with its study name) and once as
the pre-rendered static chart.

These tests pin that such a figure is deduped to a single record (the declared one,
which carries the study-authored name/caption) while a committed chart that is NOT
declared still shows.
"""
from __future__ import annotations

import yaml

from vivarium_workbench.lib.study_charts import build_study_charts_payload


def _ws(tmp_path, visualizations, extra_charts=()):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    sd = tmp_path / "studies" / "s1"
    (sd / "charts").mkdir(parents=True, exist_ok=True)
    (sd / "charts" / "01_grid.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', encoding="utf-8")
    for stem in extra_charts:
        (sd / "charts" / f"{stem}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', encoding="utf-8")
    (sd / "study.yaml").write_text(
        yaml.safe_dump({"name": "s1", "visualizations": visualizations}),
        encoding="utf-8")
    return tmp_path


def test_declared_committed_figure_renders_once(tmp_path):
    ws = _ws(tmp_path, [{"name": "Severity grid", "address": "image:charts/01_grid.svg"}])
    p = build_study_charts_payload(ws, "s1")
    figs = [c for c in p["charts"] if c.get("svg")]
    keys = [c.get("key") for c in figs]
    # Exactly one chart renders charts/01_grid.svg — not the declared + static pair.
    assert len(figs) == 1, f"declared+committed figure doubled: {keys}"
    # The study-authored declared record wins (keeps the name/caption).
    assert "Severity grid" in keys
    assert "01_grid" not in keys


def test_undeclared_committed_chart_still_shows(tmp_path):
    # A committed chart NOT referenced by visualizations[] must keep rendering.
    ws = _ws(tmp_path,
             [{"name": "Severity grid", "address": "image:charts/01_grid.svg"}],
             extra_charts=("99_extra",))
    p = build_study_charts_payload(ws, "s1")
    keys = {c.get("key") for c in p["charts"] if c.get("svg")}
    assert "99_extra" in keys, "an undeclared committed chart was dropped"
    assert "Severity grid" in keys
    assert "01_grid" not in keys
