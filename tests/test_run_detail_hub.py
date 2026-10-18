"""Task E3: enrich `_showRunDetail` (Simulations tab, per-run detail panel)
into a real per-run hub — that run's figures (filtered to `run_id`), its
report cards (study-level, so a pointer to Tests rather than a fabricated
per-run inline render — see the brief's investigation below), and a compact
results/analysis summary — all inline in `#study-run-detail`, alongside the
existing metadata + Data/Analysis/Explorer buttons + close.

Investigation finding (state up front, per the brief): `report_card_urls` is
keyed by *card name* (`vivarium_workbench/lib/study_spec.py`,
`_renderRichReportCard` in `study-detail.js`) with NO run_id field anywhere in
its shape — report cards are STUDY-level, not per-run. So this task renders a
compact "N report cards → Tests" pointer via `_gotoStudyTab('tests')`, and
must NOT call `_renderRichReportCard` from the run-detail panel (that would
fabricate a per-run association the data doesn't have).

JS behavior is asserted against the SOURCE (same convention as
test_pillar_unify.py / test_study_artifacts_on_simulate.py) — this repo has
no jsdom/browser execution harness for study-detail.js; `node --check` covers
syntax. `_func_body` brace-matches (quote-aware) from a function's header to
its balanced closing `}`, rather than a fixed-size slice window, so these
tests don't silently truncate as the functions grow.

See .superpowers/sdd/fable-increment-a/task-E3-brief.md.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_workbench"
JS = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")


def _func_body(name: str, js: str = JS) -> str:
    """Slice `function <name>(...) { ... }` from its header to the balanced
    closing brace, skipping braces inside quoted strings."""
    i = js.index("function %s(" % name)
    start = js.index("{", i)
    depth = 0
    j = start
    in_str = None
    while j < len(js):
        c = js[j]
        if in_str:
            if c == "\\":
                j += 2
                continue
            if c == in_str:
                in_str = None
        elif c in ("'", '"'):
            in_str = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return js[i:j + 1]
        j += 1
    raise ValueError("unbalanced braces while slicing function %r" % name)


# ---------------------------------------------------------------------------
# Figures: filtered to this run, reusing existing figure-card sources — the
# native gallery (async, ONE run_id for the whole gallery), the study-charts
# payload (async, per-item run_id, V3), and embed_visualizations (server-
# rendered synchronously at page load, per-item run_id, V3) — no fork.
# ---------------------------------------------------------------------------

def test_figure_cards_for_run_reuses_render_chart_card_no_new_card_class():
    body = _func_body("_figureCardsForRun")
    # Chart-sourced figures render via the SAME existing renderer.
    assert "_renderChartCard(c)" in body
    # No new figure-card-shaped class invented in this function.
    assert 'class="figure-card' not in body
    assert 'class="run-figure' not in body
    assert 'class="run-detail-figure' not in body


def test_figure_cards_for_run_honors_run_id_filter_on_all_three_sources():
    body = _func_body("_figureCardsForRun")
    # embeds: server-rendered .embed-viz-card, filtered via its own
    # figure-run-link[data-run-id] (already wired for the async sources too).
    assert "embed-viz-card" in body
    assert "figure-run-link" in body and "data-run-id" in body
    # native gallery: one shared run_id for the whole gallery.
    assert "_nativeGalleryRunId" in body
    assert "native-gallery-panel" in body
    # charts: per-item run_id.
    assert "_chartsCache" in body
    assert "c.run_id" in body


def test_figure_cards_for_run_guards_missing_run_id_no_throw():
    body = _func_body("_figureCardsForRun")
    assert "if (!runId) return cards;" in body


def test_run_detail_figures_html_degrades_when_not_loaded_yet():
    # Not-yet-loaded degradation: trigger the SAME lazy/memoized loaders the
    # Visualizations tab uses (cheap — a second call is a no-op) and show a
    # quiet pointer rather than blocking or throwing.
    body = _func_body("_runDetailFiguresHtml")
    assert "_loadNativeGallery()" in body
    assert "_loadCharts('viz-charts-panel')" in body
    assert "figures load on the Overview tab" in body


def test_run_detail_figures_html_quiet_line_when_settled_and_empty():
    body = _func_body("_runDetailFiguresHtml")
    assert "no figures for this run" in body


def test_show_run_detail_mounts_figures_container_and_reuses_helper():
    body = _func_body("_showRunDetail")
    assert 'id="run-detail-figures"' in body
    assert "_runDetailFiguresHtml(row)" in body
    assert "_wireFigureRunLinks(host)" in body


# ---------------------------------------------------------------------------
# Report cards: study-level — a pointer to Tests, never a fabricated per-run
# inline render.
# ---------------------------------------------------------------------------

def test_report_cards_are_study_level_pointer_not_fabricated_per_run():
    body = _func_body("_runDetailReportCardsHtml")
    assert "report_card_urls" in body
    # 5-tab layout: report cards live in the Audit tab (Tests folded in).
    assert "_gotoStudyTab(\\'audit\\')" in body
    # Never call the per-card rich renderer from here — that would assert a
    # per-run association report_card_urls doesn't have.
    assert "_renderRichReportCard" not in body


def test_show_run_detail_wires_report_cards_pointer():
    body = _func_body("_showRunDetail")
    assert "_runDetailReportCardsHtml()" in body


# ---------------------------------------------------------------------------
# Results/analysis summary: compact, reuses existing row/gallery values,
# computes nothing new.
# ---------------------------------------------------------------------------

def test_results_summary_reuses_existing_row_fields_only():
    body = _func_body("_runDetailResultsSummaryHtml")
    assert "row.n_steps" in body
    assert "hasData" in body


def test_show_run_detail_wires_results_summary():
    body = _func_body("_showRunDetail")
    assert "_runDetailResultsSummaryHtml(row" in body


# ---------------------------------------------------------------------------
# Regression: existing metadata + the three action buttons + close survive.
# ---------------------------------------------------------------------------

def test_show_run_detail_regression_metadata_and_buttons_and_close():
    body = _func_body("_showRunDetail")
    assert "Run ID" in body
    assert "⬇ Data (raw emitter)" in body  # unicode DOWNWARDS ARROW glyph
    assert "⬇ Analysis (figures / cards)" in body
    assert "Open run in Composite Explorer" in body
    assert "✕" in body  # ✕ close button glyph
    assert "study-run-detail\\').innerHTML=\\'\\'" in body


# ---------------------------------------------------------------------------
# JS syntax.
# ---------------------------------------------------------------------------

def test_js_syntax_valid():
    r = subprocess.run(
        ["node", "--check", str(_PKG / "static" / "study-detail.js")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
