"""Gate readouts are results, not visualizations.

`render_v4_test_charts` emits a card per behavior_test. Cards that carry a
rendered figure (`svg`/`img`) are visualizations. A figureless card — e.g. a
REFUSAL card for a declared `measure.path` whose observable has no data in any
run source — is a gate RESULT graded from run data, not a visualization, and
belongs under Simulations › Results, not the Visualizations tab.

Note on scope: a test with no `measure.path`, and a `kind: derived` scalar
measure, are no longer rendered as figureless gallery cards at all — they are
graded on the Audit tab. The one figureless *live* card that remains is the
refusal card, and this split routes it to `gate_results`.

`build_study_charts_payload` must therefore split its live cards: figure-bearing
ones into `charts`, figureless ones into `gate_results`.
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


def test_figureless_refusal_card_goes_to_gate_results_not_charts(tmp_path):
    # A non-derived measure.path whose observable has no data in any source
    # renders as a figureless REFUSAL card (status "refused") — a run-derived
    # result, not a visualization.
    ws = _ws_with_study(tmp_path, [
        {"name": "g-missing", "classification": "primary",
         "measure": {"path": "glucose"}, "pass_if": {}},
    ])
    p = build_study_charts_payload(ws, "s1")
    assert "gate_results" in p
    # the figureless card is NOT surfaced as a visualization …
    assert all(c.get("key") != "v4-g-missing" for c in p["charts"]), \
        "a figureless refusal card leaked into the Visualizations charts"
    # … it is surfaced as a gate result, carrying no figure.
    gr = [g for g in p["gate_results"] if g.get("key") == "v4-g-missing"]
    assert gr, "the refusal card should surface as a gate result"
    assert gr[0].get("status") == "refused"
    assert not (gr[0].get("svg") or gr[0].get("img") or gr[0].get("srcdoc")
                or gr[0].get("iframe_url"))
    # it counts toward the scan summary like any other signal
    assert p["gate_result_count"] == len(p["gate_results"])


def test_audit_only_tests_produce_no_visualization_cards(tmp_path):
    # A test with no measure.path, and a `kind: derived` scalar measure, are
    # graded on the Audit tab — neither is rendered as a figureless gallery card,
    # so neither lands in `charts` NOR is manufactured into `gate_results`.
    ws = _ws_with_study(tmp_path, [
        {"name": "g-nopath", "classification": "primary", "pass_if": {}},
        {"name": "g-scalar", "classification": "diagnostic",
         "measure": {"kind": "derived", "path": "x"}, "pass_if": {}},
    ])
    p = build_study_charts_payload(ws, "s1")
    keys = {c.get("key") for c in p["charts"]} | {g.get("key") for g in p["gate_results"]}
    assert "v4-g-nopath" not in keys, "a no-measure.path test leaked into the gallery"
    assert "v4-g-scalar" not in keys, "a derived-scalar test leaked into the gallery"


def test_charts_only_ever_hold_figures(tmp_path):
    # Include a figureless refusal card: it must be split OUT of charts and INTO
    # gate_results, so the assertion below is non-vacuous.
    ws = _ws_with_study(tmp_path, [
        {"name": "g-missing", "classification": "primary",
         "measure": {"path": "glucose"}, "pass_if": {}},
    ])
    p = build_study_charts_payload(ws, "s1")
    # every entry in charts carries a figure; nothing figureless survives there.
    for c in p["charts"]:
        assert c.get("svg") or c.get("img") or c.get("srcdoc") or c.get("iframe_url"), \
            f"non-figure card in charts: {c.get('key')}"
    # the figureless card really was produced and routed to gate_results.
    assert any(g.get("key") == "v4-g-missing" for g in p["gate_results"])
