"""A study-declared visualization must not appear twice on the Visualizations tab.

The run renders a study's declared ``visualizations[]`` into ``.pbg/runs/<id>/
viz.json`` (shown inline by the study-detail "native gallery") AND
``render_study_visualizations`` renders them post-run into ``viz/<name>.html``
(discovered by ``build_study_charts_payload`` as a ``declared`` chart). Without
de-duplication the same figure shows once as a native panel and once as a
declared chart. ``build_study_charts_payload`` drops the declared chart when the
native gallery already renders it (matched by name); a viz not yet in viz.json
still renders as a declared chart.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib.study_charts import build_study_charts_payload


def _build_study(tmp_path: Path) -> tuple[Path, str]:
    ws = tmp_path / "ws"
    study_dir = ws / "studies" / "demo"
    study_dir.mkdir(parents=True)
    (study_dir / "fig.html").write_text("<html><body>fig</body></html>", encoding="utf-8")
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({
            "name": "demo",
            "visualizations": [{"name": "fig", "address": "html:fig.html"}],
        }),
        encoding="utf-8",
    )
    return ws, "demo"


def test_declared_chart_dropped_when_native_gallery_has_it(tmp_path, monkeypatch):
    ws, name = _build_study(tmp_path)
    # The native gallery already renders 'fig' inline from viz.json.
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_native_gallery.build_study_native_gallery",
        lambda ws_root, slug: {"run_id": "r1", "panels": {"fig": "<div>fig</div>"}},
    )
    payload = build_study_charts_payload(ws, name)
    declared = [c for c in payload["charts"] if c.get("source") == "declared"]
    assert declared == [], "declared chart should be deduped against the native gallery"


def test_declared_chart_kept_when_not_in_native_gallery(tmp_path, monkeypatch):
    ws, name = _build_study(tmp_path)
    # Native gallery empty (e.g. the viz isn't in viz.json yet) → chart still shows.
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_native_gallery.build_study_native_gallery",
        lambda ws_root, slug: {"run_id": None, "panels": {}},
    )
    payload = build_study_charts_payload(ws, name)
    declared = [c for c in payload["charts"] if c.get("source") == "declared"]
    assert len(declared) == 1 and declared[0]["key"] == "fig"
