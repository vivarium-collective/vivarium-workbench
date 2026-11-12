"""The Visualizations tab must never 500.

Chart building pulls per-chart freshness badges from the OPTIONAL
``discovery_engine.viz.freshness`` module. When the discovery_engine installed in
the running server's env predates that module (a version skew), the unguarded
import used to raise ModuleNotFoundError and 500 the whole /api/study-charts
endpoint — taking down *all* figures for the study, not just the badge.

These tests pin the two layers of the fix:
  1. build_study_charts_payload degrades to "untracked" freshness and still
     renders charts when discovery_engine.viz is unimportable.
  2. the /api/study-charts route returns a valid empty payload (HTTP 200) on ANY
     build failure, so the UI shows "No figures yet" instead of a red banner.
"""
from __future__ import annotations

import builtins
from pathlib import Path

import yaml

from vivarium_workbench.lib.study_charts import build_study_charts_payload


def _break_viz_import(monkeypatch):
    """Force `from discovery_engine.viz.freshness import ...` to raise, emulating
    an engine install too old to carry the module."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "discovery_engine.viz.freshness" or name.startswith("discovery_engine.viz"):
            raise ModuleNotFoundError("No module named 'discovery_engine.viz'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def _study_with_declared_chart(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    charts = ws / "studies" / "demo" / "charts"
    charts.mkdir(parents=True)
    (ws / "studies" / "demo" / "study.yaml").write_text(
        yaml.safe_dump({
            "name": "demo",
            "visualizations": [{"name": "c", "chart": "charts/c.svg", "render": "cmd"}],
        }),
        encoding="utf-8",
    )
    (charts / "c.svg").write_text("<svg/>", encoding="utf-8")
    return ws


def test_build_survives_missing_viz_module(tmp_path, monkeypatch):
    ws = _study_with_declared_chart(tmp_path)
    _break_viz_import(monkeypatch)

    # Must not raise; the declared static chart still renders.
    payload = build_study_charts_payload(ws, "demo")

    assert payload["study"] == "demo"
    keys = {c.get("key") for c in payload["charts"]}
    assert "c" in keys, f"declared chart dropped when viz module missing: {keys}"
    # Freshness degrades rather than crashing.
    for c in payload["charts"]:
        assert c.get("freshness") in {"untracked", "declared", "fresh", "stale", None}


def test_route_never_500s_on_build_failure(inproc_client, tmp_path, monkeypatch):
    ws = _study_with_declared_chart(tmp_path)
    monkeypatch.setattr(
        "vivarium_workbench.api.app.build_study_charts_payload",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    client = inproc_client(ws)
    resp = client.get("/api/study-charts/demo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["study"] == "demo"
    assert body["charts"] == []
    assert body["db_exists"] is False
