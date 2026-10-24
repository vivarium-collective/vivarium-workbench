"""Gate readouts are results, not visualizations.

`render_v4_test_charts` emits a card per behavior_test. Cards that carry a
rendered figure (`svg`/`img`) are visualizations. Cards that don't — a
derived-scalar "Computed scalar readout (no time series)" or an "unresolved:
no measure.path" stub — are gate RESULTS graded from run data, and belong under
Simulations › Results, not the Visualizations tab.

`build_study_charts_payload` must therefore split its live cards: figure-bearing
ones into `charts`, figureless ones into a new `gate_results` list.
"""
from __future__ import annotations

import yaml

from vivarium_workbench.lib.study_charts import build_study_charts_payload


def _ws_with_study(tmp_path, tests):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    sd = tmp_path / "studies" / "s1"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "runs.db").touch()  # a db file is what reaches render_v4_test_charts' per-test loop
    (sd / "study.yaml").write_text(
        yaml.safe_dump({"name": "s1", "schema_version": 4, "behavior_tests": tests}),
        encoding="utf-8",
    )
    return tmp_path


def test_unresolved_gate_goes_to_gate_results_not_charts(tmp_path):
    ws = _ws_with_study(tmp_path, [
        {"name": "g-unresolved", "classification": "primary", "pass_if": {}},  # no measure.path
        # a path-bearing sibling so render_v4_test_charts runs its per-test loop
        # (it early-returns when NO test declares a measure.path).
        {"name": "g-scalar", "classification": "diagnostic",
         "measure": {"kind": "derived", "path": "x"}, "pass_if": {}},
    ])
    p = build_study_charts_payload(ws, "s1")
    assert "gate_results" in p
    # the unresolved card is NOT surfaced as a visualization …
    assert all(c.get("status") != "unresolved" for c in p["charts"]), \
        "unresolved gate leaked into the Visualizations charts"
    # … it is surfaced as a gate result.
    assert any(g.get("status") == "unresolved" and g.get("key") == "v4-g-unresolved"
               for g in p["gate_results"])


def test_scalar_readout_gate_goes_to_gate_results(tmp_path):
    ws = _ws_with_study(tmp_path, [
        {"name": "g-scalar", "classification": "diagnostic",
         "measure": {"kind": "derived", "path": "some_scalar"}, "pass_if": {}},
    ])
    p = build_study_charts_payload(ws, "s1")
    # a derived scalar with no emitted series renders as a "scalar readout" card:
    # figureless, so it is a gate result, never a chart.
    assert all(c.get("svg") or c.get("img") for c in p["charts"]), \
        "a figureless card leaked into charts"
    assert any("scalar readout" in (g.get("caption") or "") for g in p["gate_results"])


def test_charts_only_ever_hold_figures(tmp_path):
    ws = _ws_with_study(tmp_path, [
        {"name": "g1", "classification": "primary", "pass_if": {}},
        {"name": "g2", "classification": "diagnostic",
         "measure": {"kind": "derived", "path": "x"}, "pass_if": {}},
    ])
    p = build_study_charts_payload(ws, "s1")
    # every entry in charts carries a figure; nothing figureless survives there.
    for c in p["charts"]:
        assert c.get("svg") or c.get("img") or c.get("srcdoc") or c.get("iframe_url"), \
            f"non-figure card in charts: {c.get('key')}"
