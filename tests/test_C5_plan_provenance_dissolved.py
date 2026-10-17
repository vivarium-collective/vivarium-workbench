"""C5 — dissolve the Overview "Plan & provenance" grab-bag into the acts.

The five P&P sub-blocks are RELOCATED (moved, not duplicated) from the
Overview panel to the act each belongs to:

  pipeline_gate / key_assumptions / limitations  -> Decide (conclusions) panel
  expert_decisions_needed                        -> Decide, under "Open debts"
  literature_anchors                             -> Tests panel

Assertions: Overview no longer renders any of them (nor the "Plan & provenance"
heading); Decide/Tests each render their moved block exactly once page-wide;
the G7 attribution line on the expert-question card survives; and a study
MISSING these fields renders none of the moved blocks (no empty boxes).
"""
from __future__ import annotations

import pytest
import yaml

from vivarium_workbench.lib.study_page import actor_glyph
from vivarium_workbench.lib.study_spec import load_study_detail_spec
from vivarium_workbench.lib.study_page import render_study_detail_html


_V3_BASE = {
    "schema_version": 3,
    "baseline": [{"name": "core", "composite": "pkg.composites.core", "params": {}}],
    "variants": [],
    "runs": [],
}

# Unique tokens so occurrence-counting is unambiguous.
GATE_PROCEED = "GATE_PROCEED_TOKEN_C5"
ASSUMPTION = "ASSUMPTION_ALPHA_C5"
LIMITATION = "LIMITATION_BETA_C5"
EXPERT_Q1 = "EXPERT_Q_GAMMA_C5"
EXPERT_Q2 = "EXPERT_Q_DELTA_C5"
ANCHOR = "ANCHOR_EPSILON_C5"


def _write_study(ws, slug, **extra):
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = dict(_V3_BASE, name=slug, objective="test", status="in_progress", **extra)
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))


def _render(ws, slug):
    spec = load_study_detail_spec(ws, slug)
    return render_study_detail_html(ws, slug, spec)


def _panel(html, panel_id, next_panel_id=None):
    start = html.index(f'id="{panel_id}"')
    if next_panel_id:
        end = html.index(f'id="{next_panel_id}"', start + 1)
    else:
        end = len(html)
    return html[start:end]


@pytest.fixture
def ws_full(tmp_path):
    ws = tmp_path / "ws"
    _write_study(
        ws,
        "full-study",
        pipeline_gate={
            "prerequisites": ["prev-study"],
            "enables": ["next-study"],
            "proceed_condition": GATE_PROCEED,
        },
        key_assumptions=[ASSUMPTION],
        limitations=[LIMITATION],
        expert_decisions_needed=[
            {"id": "q1", "status": "open", "question": EXPERT_Q1, "asked_to": "Haochen"},
            {"id": "q2", "status": "open", "question": EXPERT_Q2},
        ],
        literature_anchors=[
            {
                "expectation": ANCHOR,
                "model_observable": "obs_x",
                "source": "Ref 2024",
                "status_in_v2ecoli": "matched",
            }
        ],
    )
    return ws


def test_overview_no_longer_renders_plan_and_provenance(ws_full):
    html = _render(ws_full, "full-study")
    overview = _panel(html, "panel-overview", "panel-compose")
    assert "Plan &amp; provenance" not in overview
    assert "Plan & provenance" not in overview
    for tok in (GATE_PROCEED, ASSUMPTION, EXPERT_Q1, EXPERT_Q2, ANCHOR):  # LIMITATION now also in the Overview Result caveat
        assert tok not in overview, f"{tok!r} still rendered in Overview"


def test_decide_panel_renders_gate_assumptions_limitations_and_open_debts(ws_full):
    html = _render(ws_full, "full-study")
    decide = _panel(html, "panel-conclusions")
    assert GATE_PROCEED in decide
    assert ASSUMPTION in decide
    assert LIMITATION in decide
    assert EXPERT_Q1 in decide
    assert EXPERT_Q2 in decide
    assert "Open debts" in decide


def test_tests_panel_renders_literature_anchors(ws_full):
    html = _render(ws_full, "full-study")
    tests = _panel(html, "panel-audit", "panel-conclusions")
    assert ANCHOR in tests


def test_g7_attribution_survives_on_expert_question(ws_full):
    html = _render(ws_full, "full-study")
    decide = _panel(html, "panel-conclusions")
    assert "asked to:" in decide
    assert "Haochen" in decide
    assert actor_glyph("Haochen") in decide
    # q2 has no asked_to -> the literal unattributed token, never blank.
    assert "unattributed" in decide


def test_each_moved_block_renders_exactly_once_page_wide(ws_full):
    html = _render(ws_full, "full-study")
    for tok in (GATE_PROCEED, ASSUMPTION, EXPERT_Q1, EXPERT_Q2, ANCHOR):  # LIMITATION now also in the Overview Result caveat
        assert html.count(tok) == 1, f"{tok!r} rendered {html.count(tok)}x (expected 1)"


@pytest.fixture
def ws_bare(tmp_path):
    ws = tmp_path / "ws"
    _write_study(ws, "bare-study")
    return ws


def test_missing_fields_render_no_moved_blocks_anywhere(ws_bare):
    html = _render(ws_bare, "bare-study")
    # No P&P heading anywhere, and no empty Open-debts / literature-anchors box.
    assert "Plan &amp; provenance" not in html
    assert "Open debts" not in html
    assert "Literature anchors" not in html
    for tok in (GATE_PROCEED, ASSUMPTION, LIMITATION, EXPERT_Q1, ANCHOR):
        assert tok not in html
