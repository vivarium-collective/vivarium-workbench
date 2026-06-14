"""framework-emitters — honest emitter label for emitter-less runs.

A run with no step emitter (a summary recorded in study.yaml, source tag
"none") used to be labeled "Recorded", which masqueraded as an emitter. It is
now labeled "—" with a "no emitter (summary-only run)" tooltip. Once a study
emits via SQLite it shows "SQLite" naturally.
"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_dashboard"


def test_simulations_data_labels_emitterless_run_as_dash(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    import vivarium_dashboard.lib.simulations_index as si

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: ws\n")
    monkeypatch.setattr(srv, "WORKSPACE", ws)

    # A study.yaml-sourced run carries emitter tag "none" (no step emitter).
    monkeypatch.setattr(si, "list_simulations", lambda _ws: [
        {"run_id": "summary-run", "emitter": "none", "db_path": None},
        {"run_id": "sqlite-run", "emitter": "sqlite", "db_path": None},
    ])

    data = srv._simulations_data(ws)
    by_id = {s["run_id"]: s for s in data["simulations"]}
    assert by_id["summary-run"]["emitter_type"] == "—"
    assert by_id["sqlite-run"]["emitter_type"] == "SQLite"


def test_pill_js_conveys_summary_only_tooltip():
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    assert "no emitter (summary-only run)" in js
    assert "emitter-none" in js


def test_emitter_none_css_class_present():
    html = (_PKG / "templates" / "index.html.j2").read_text(encoding="utf-8")
    assert ".emitter-none" in html
