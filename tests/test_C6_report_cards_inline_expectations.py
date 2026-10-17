"""C6 — report cards expand inline at each behavioral test row, and
`literature_anchors` (moved into Tests by C5) render as an "Expectations"
band using the G3 outcome_label vocabulary.

Part A: each `kind: report_card` row gets an inline expander (native
<details>) whose mount reuses _renderRichReportCard(card) (JS, lazy-filled
on first expand) instead of a "View report card ↑" scroll-away link to the
top panel. The separate top #report-cards-panel no longer remounts the full
per-card stack (that would double every card) — it is now
plotly-comparison-only, and disappears entirely (server-gated) when the
study has no comparison_plotly_url, rather than showing an empty box.

Part B: literature_anchors render under an "Expectations" heading; each
anchor's expectation/model_observable/source render, and where
status_in_v2ecoli is present it is routed through the outcome_label /
outcome_class / outcome_glyph filters (the same met / conditional-pass /
not met / not assessable vocabulary G3 established) instead of raw text.
"""
from __future__ import annotations

import json

import yaml

from vivarium_workbench.lib.study_page import outcome_glyph, outcome_label


_V3_BASE = {
    "schema_version": 3,
    "baseline": [{"name": "core", "composite": "pkg.composites.core", "params": {}}],
    "variants": [],
    "runs": [],
}


def _write_study(ws, slug, **extra):
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = dict(_V3_BASE, name=slug, objective="test", status="in_progress", **extra)
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return sd


def _panel(html, panel_id, next_panel_id=None):
    start = html.index(f'id="{panel_id}"')
    if next_panel_id:
        end = html.index(f'id="{next_panel_id}"', start + 1)
    else:
        end = len(html)
    return html[start:end]


# ---------------------------------------------------------------------------
# Part A — inline expander at the report_card row; no double-render at top.
# ---------------------------------------------------------------------------

def test_report_card_row_has_inline_expander_not_scroll_link(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    sd = _write_study(
        ws, "rc-study",
        behavior_tests=[
            {"name": "beh1", "kind": "behavioral", "description": "a behavioral check"},
            {"name": "card-test", "kind": "report_card", "card": "standard"},
        ],
    )
    (sd / "viz" / "report_card").mkdir(parents=True)
    (sd / "viz" / "report_card" / "standard.html").write_text("<h1>std</h1>", encoding="utf-8")
    (sd / "viz" / "report_card" / "standard.verdict.json").write_text(
        json.dumps({"overall": "within_tol"}), encoding="utf-8"
    )

    client = dashboard_client(ws)
    resp = client.get("/studies/rc-study")
    assert resp.status_code == 200
    html = resp.text
    tests_panel = _panel(html, "panel-audit", "panel-conclusions")

    # The row: an inline expander (native <details>) mounted at the row,
    # carrying data-card so the JS lazy-fill can find it; the verdict pill
    # stays on the row summary.
    assert '<details class="report-card-row-expander" data-card="standard">' in tests_panel
    assert 'class="report-card-row-mount" data-card="standard"' in tests_panel
    assert 'class="report-card-verdict" data-card="standard"' in tests_panel

    # The old scroll-away link to the top panel is gone.
    assert 'View report card' not in tests_panel
    assert 'href="#report-cards-panel"' not in tests_panel

    # Behavioral row is untouched.
    assert 'beh1' in tests_panel


def test_top_report_cards_panel_present_but_plotly_gated(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    sd = _write_study(
        ws, "rc-plotly-study",
        behavior_tests=[{"name": "card-test", "kind": "report_card", "card": "standard"}],
    )
    (sd / "viz" / "report_card").mkdir(parents=True)
    (sd / "viz" / "report_card" / "standard.html").write_text("<h1>std</h1>", encoding="utf-8")
    (sd / "viz" / "report_card" / "standard.verdict.json").write_text(
        json.dumps({"overall": "within_tol"}), encoding="utf-8"
    )
    (sd / "viz" / "comparison_plotly.html").write_text("<div>plotly</div>", encoding="utf-8")

    client = dashboard_client(ws)
    html = client.get("/studies/rc-plotly-study").text
    tests_panel = _panel(html, "panel-audit", "panel-conclusions")

    # The top mount exists (server-gated on comparison_plotly_url being
    # present), ready for the client-side plotly-only fill.
    assert 'id="report-cards-panel"' in tests_panel
    assert 'id="report-cards-section"' in tests_panel


def test_report_cards_section_absent_when_no_comparison_plotly(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    # A study with NO report cards and NO comparison plotly at all.
    _write_study(ws, "no-rc-study")

    client = dashboard_client(ws)
    html = client.get("/studies/no-rc-study").text
    tests_panel = _panel(html, "panel-audit", "panel-conclusions")

    assert 'id="report-cards-panel"' not in tests_panel
    assert 'id="report-cards-section"' not in tests_panel
    assert 'Report cards' not in tests_panel


def test_js_top_panel_no_longer_maps_full_card_stack_but_keeps_plotly():
    """Source-level check: _fillReportCardsTab (top panel) must not call
    _renderRichReportCard (that would double-render every card that also
    renders at its row), while the plotly comparison embed (viz-embed) is
    still emitted there, and a NEW row-binder wires _renderRichReportCard to
    the per-row mount instead."""
    js_path = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "vivarium_workbench" / "static" / "study-detail.js"
    )
    js = js_path.read_text(encoding="utf-8")

    start = js.index("function _fillReportCardsTab(")
    end = js.index("\n  }\n", start)
    top_panel_fn = js[start:end]
    assert "_renderRichReportCard" not in top_panel_fn
    assert "viz-embed" in top_panel_fn

    assert "report-card-row-mount" in js
    assert "report-card-row-expander" in js
    # Some function in the file wires the row mount to _renderRichReportCard.
    row_bind_idx = js.index("report-card-row-mount")
    assert "_renderRichReportCard" in js[row_bind_idx: row_bind_idx + 2000]


# ---------------------------------------------------------------------------
# Part B — literature_anchors as an "Expectations" band, outcome vocabulary.
# ---------------------------------------------------------------------------

def test_expectations_band_renders_heading_and_anchor_fields(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    _write_study(
        ws, "expect-study",
        literature_anchors=[
            {
                "expectation": "Doubling time matches wet-lab range",
                "model_observable": "doubling_time_s",
                "source": "Neidhardt 1990",
                "status_in_v2ecoli": "PASS",
            },
            {
                "expectation": "No status recorded yet",
                "model_observable": "obs_y",
            },
        ],
    )

    client = dashboard_client(ws)
    html = client.get("/studies/expect-study").text
    tests_panel = _panel(html, "panel-audit", "panel-conclusions")

    assert "Expectations" in tests_panel
    assert "Doubling time matches wet-lab range" in tests_panel
    assert "doubling_time_s" in tests_panel
    assert "Neidhardt 1990" in tests_panel
    assert "No status recorded yet" in tests_panel
    assert "obs_y" in tests_panel

    # status_in_v2ecoli: "PASS" is routed through the G3 outcome vocabulary,
    # not shown as the raw literal token.
    assert outcome_label("PASS") in tests_panel
    assert outcome_glyph("PASS") in tests_panel

    # The second anchor has no status_in_v2ecoli -> no fabricated status pill
    # for it specifically (absent != empty), but it still renders otherwise.


def test_expectations_band_absent_when_no_literature_anchors(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    _write_study(ws, "no-anchors-study")

    client = dashboard_client(ws)
    html = client.get("/studies/no-anchors-study").text
    tests_panel = _panel(html, "panel-audit", "panel-conclusions")

    assert "Expectations" not in tests_panel
