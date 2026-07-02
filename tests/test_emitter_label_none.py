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
    import vivarium_dashboard.lib.simulations_index as si

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: ws\n")
    # A study.yaml-sourced run carries emitter tag "none" (no step emitter).
    monkeypatch.setattr(si, "list_simulations", lambda _ws: [
        {"run_id": "summary-run", "emitter": "none", "db_path": None},
        {"run_id": "sqlite-run", "emitter": "sqlite", "db_path": None},
    ])

    data = si.build_simulations_data(ws)
    by_id = {s["run_id"]: s for s in data["simulations"]}
    assert by_id["summary-run"]["emitter_type"] == "—"
    assert by_id["sqlite-run"]["emitter_type"] == "SQLite"


def test_simulations_data_labels_dict_declared_emitters(tmp_path, monkeypatch):
    """A study.yaml run may declare its emitter as a structured dict
    ({"kind": "parquet", ...}) rather than a plain string. The dict must be
    normalised to its kind, NOT crash the emitter_type loop. Regression: a dict
    hitting .lower() raised AttributeError, the bare except swallowed it
    mid-loop, and every row after it (plus the dict row) lost its emitter_type
    and rendered as the "SQLite" default in the read-only dashboard.
    """
    import vivarium_dashboard.lib.simulations_index as si

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: ws\n")
    monkeypatch.setattr(si, "list_simulations", lambda _ws: [
        {"run_id": "none-run", "emitter": "none", "db_path": None},
        # dict-declared emitter appears mid-list; must not abort the loop.
        {"run_id": "parquet-dict-run",
         "emitter": {"kind": "parquet", "store": "out/x"}, "db_path": None},
        {"run_id": "xarray-run", "emitter": "xarray", "db_path": None},
        {"run_id": "parquet-str-run", "emitter": "parquet", "db_path": None},
    ])

    data = si.build_simulations_data(ws)
    by_id = {s["run_id"]: s for s in data["simulations"]}
    assert by_id["none-run"]["emitter_type"] == "—"
    assert by_id["parquet-dict-run"]["emitter_type"] == "Parquet"
    # Rows AFTER the dict row must still be labelled (loop didn't abort).
    assert by_id["xarray-run"]["emitter_type"] == "XArray"
    assert by_id["parquet-str-run"]["emitter_type"] == "Parquet"


def test_pill_js_conveys_summary_only_tooltip():
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    assert "no emitter (summary-only run)" in js
    assert "emitter-none" in js


def test_emitter_none_css_class_present():
    html = (_PKG / "templates" / "index.html.j2").read_text(encoding="utf-8")
    assert ".emitter-none" in html
