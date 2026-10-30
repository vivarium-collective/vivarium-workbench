"""An auto-discovered viz/<name>.html embed must not duplicate the native gallery.

A study's declared visualization is rendered into .pbg/runs/<id>/viz.json (shown
inline by the study-detail native gallery) AND into studies/<name>/viz/<name>.html
(auto-discovered and surfaced as an `embed_visualizations` entry). Both render the
same figure, so without de-duplication the Visualizations tab shows it twice.

`load_study_detail_spec` drops an auto-discovered embed whose viz name (the file
stem) the native gallery already renders; an embed NOT in the native gallery is
kept (correct fallback).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import yaml

from vivarium_workbench.lib.study_spec import load_study_detail_spec


def _build_study(tmp_path: Path) -> tuple[Path, str]:
    ws = tmp_path / "ws"
    (ws / "workspace.yaml").parent.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    study_dir = ws / "studies" / "demo"
    (study_dir / "viz").mkdir(parents=True)
    (study_dir / "viz" / "coculture_competition.html").write_text(
        "<html><body>fig</body></html>", encoding="utf-8")
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({"name": "demo", "schema_version": 4,
                        "baseline": [{"name": "demo", "composite": "pkg.composites.demo"}],
                        "visualizations": [{"name": "coculture_competition",
                                            "address": "local:CocultureCompetition"}]}),
        encoding="utf-8",
    )
    # A completed run so discover_viz_html_files surfaces the viz/*.html.
    db = study_dir / "runs.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT, "
                 "started_at REAL, completed_at REAL, status TEXT)")
    conn.execute("INSERT INTO runs_meta VALUES (?,?,?,?,?)",
                 ("r1", "demo", time.time() - 10, time.time() - 1, "completed"))
    conn.commit()
    conn.close()
    return ws, "demo"


def _auto_embeds(spec):
    return [e for e in (spec.get("embed_visualizations") or [])
            if str(e.get("name", "")).endswith("(auto)")]


def test_auto_embed_dropped_when_native_gallery_has_it(tmp_path, monkeypatch):
    ws, name = _build_study(tmp_path)
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_native_gallery.build_study_native_gallery",
        lambda ws_root, slug: {"run_id": "r1",
                               "panels": {"coculture_competition": "<div>fig</div>"}},
    )
    spec = load_study_detail_spec(ws, name)
    assert _auto_embeds(spec) == [], "auto-embed should be deduped against the native gallery"


def test_auto_embed_kept_when_not_in_native_gallery(tmp_path, monkeypatch):
    ws, name = _build_study(tmp_path)
    monkeypatch.setattr(
        "vivarium_workbench.lib.study_native_gallery.build_study_native_gallery",
        lambda ws_root, slug: {"run_id": None, "panels": {}},
    )
    spec = load_study_detail_spec(ws, name)
    embeds = _auto_embeds(spec)
    assert len(embeds) == 1 and embeds[0]["name"] == "coculture_competition (auto)"
