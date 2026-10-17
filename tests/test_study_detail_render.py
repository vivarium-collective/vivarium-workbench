"""Wiring test for study `kind` reaching the study-detail template context,
plus (Task 4) real served-HTML assertions for the restructured header.

The `data-study-kind=` HTML attribute itself is added by study-detail.html in
a later task (Task 4), so there is no markup to assert on yet. Instead this
captures the kwargs `render_study_detail_html` passes to Jinja2's
``Template.render()`` (i.e. what the template sees as ``study.kind``) by
stubbing out template rendering — proving the wiring without depending on
markup that doesn't exist yet.
"""
from __future__ import annotations

from pathlib import Path

import jinja2
import yaml
import pytest


def test_render_study_detail_html_sets_study_kind_in_template_context(monkeypatch, tmp_path):
    from vivarium_workbench.lib.study_page import render_study_detail_html

    captured = {}

    class _StubTemplate:
        def render(self, **kwargs):
            captured.update(kwargs)
            return "<html></html>"

    def _fake_get_template(self, name):
        return _StubTemplate()

    monkeypatch.setattr(jinja2.Environment, "get_template", _fake_get_template)

    spec = {
        "name": "s1",
        "findings": [{"kind": "theoretical"}, {"kind": "theoretical"}],
    }
    render_study_detail_html(tmp_path, "s1", spec)

    assert captured["study"]["kind"] == "theoretical"


def test_render_study_detail_html_preserves_explicit_kind(monkeypatch, tmp_path):
    from vivarium_workbench.lib.study_page import render_study_detail_html

    captured = {}

    class _StubTemplate:
        def render(self, **kwargs):
            captured.update(kwargs)
            return "<html></html>"

    def _fake_get_template(self, name):
        return _StubTemplate()

    monkeypatch.setattr(jinja2.Environment, "get_template", _fake_get_template)

    spec = {"name": "s2", "kind": "biological"}
    render_study_detail_html(tmp_path, "s2", spec)

    assert captured["study"]["kind"] == "biological"


# ---------------------------------------------------------------------------
# Task 4: header restructure (7 elements -> 3) — real served-HTML assertions.
# ---------------------------------------------------------------------------

def test_header_has_kind_tag_and_question_and_no_spine(tmp_path, dashboard_client):
    """End-to-end: GET /studies/<slug> must emit the kind tag + promoted
    question headline, and must NOT emit the retired #spine-summary table.

    This is the flip of Task 1's context-only wiring test above (which
    stubbed out Jinja2 rendering because the template tag didn't exist yet)
    into a plain assertion against real served markup, now that Task 4 has
    added the `data-study-kind=` tag to study-detail.html.
    """
    ws = tmp_path / "ws"
    sd = ws / "studies" / "kind-question-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "kind-question-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/kind-question-study")
    assert resp.status_code == 200
    html = resp.text

    assert 'data-study-kind=' in html
    assert 'data-study-kind="biological"' in html
    assert 'id="study-question-headline"' in html
    assert "Does the demo composite run correctly?" in html
    assert 'id="spine-summary"' not in html


# ---------------------------------------------------------------------------
# Task 5: readiness -> inline link; #spine-summary populator removed.
# ---------------------------------------------------------------------------

def test_readiness_is_inline_not_banner(tmp_path, dashboard_client):
    """The readiness mount is still server-rendered as an empty container
    (its content is filled client-side by _renderReadinessPanel after
    GET /api/report-lint, so the "⚠ N gaps" / "✓ ready" text itself is NOT
    visible in served HTML and must not be asserted here). Only assert the
    server-side facts: the `#readiness-panel` mount exists and no trace of
    the retired `#spine-summary` banner/table remains.
    """
    ws = tmp_path / "ws"
    sd = ws / "studies" / "readiness-inline-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "readiness-inline-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/readiness-inline-study")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="readiness-panel"' in html
    assert 'id="spine-summary"' not in html
    assert 'spine-summary' not in html
    assert 'spine-row' not in html


# ---------------------------------------------------------------------------
# Task 6: overview de-biology — delete biology section, fold authored
# biological_summary into a fourth "Summary" card in Question & Approach,
# drop the counts strip.
# ---------------------------------------------------------------------------

def test_overview_no_biology_lean(tmp_path, dashboard_client):
    """The standalone biology block (heading + derived-from-findings
    restatement) and the counts strip must both be gone."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "no-biology-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "no-biology-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "findings": [{"statement": "Something was found."}],
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/no-biology-study")
    assert resp.status_code == 200
    html = resp.text

    assert "Biology — what this study is about" not in html
    assert "Derived from findings" not in html
    assert "study-counts-strip" not in html


def test_overview_biological_summary_folds_into_qa_card(tmp_path, dashboard_client):
    """An authored `biological_summary` renders as a fourth card inside the
    Question & Approach section (same `purpose-callout` class family as the
    Question/Hypothesis/Mechanism cards), not as a standalone "Biology"
    section/heading."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "qa-summary-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "qa-summary-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {
            "question": "Does the demo composite run correctly?",
            "expected_outcome": "Yes, it runs without error.",
            "mechanism": "Swap the metabolism process for a reduced model.",
        },
        "biological_summary": "Plain-English mechanism narrative for a non-modeler.",
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/qa-summary-study")
    assert resp.status_code == 200
    html = resp.text

    assert "Biology — what this study is about" not in html
    assert "study-counts-strip" not in html

    # biological_summary now folds in as muted CONTEXT under the Claim
    # (class `ctr-context`) — not a "Summary." purpose-callout in a
    # "Question & approach" section (the Overview is Claim -> Test -> Result).
    assert "Question &amp; approach" not in html
    assert "Plain-English mechanism narrative for a non-modeler." in html
    summary_idx = html.index("Plain-English mechanism narrative for a non-modeler.")
    ctx_start = html.rindex('class="ctr-context"', 0, summary_idx)
    assert ctx_start != -1
    # it sits under the Claim heading, before the Test heading
    assert html.index(">Claim</h2>") < summary_idx < html.index(">Model &amp; approach</h2>")


def test_overview_biological_summary_renders_for_legacy_schema_too(tmp_path, dashboard_client):
    """Fix round 1: a legacy-schema study (no `purpose` block, so the
    `{% else %}` branch renders) with an authored `biological_summary` must
    still get the Summary card — it must not silently drop the text just
    because the study predates the v4 `purpose.*` schema."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "legacy-summary-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "legacy-summary-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "question": "Legacy-schema research question.",
        "biological_summary": "Legacy-schema plain-English mechanism narrative.",
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/legacy-summary-study")
    assert resp.status_code == 200
    html = resp.text

    assert "Biology — what this study is about" not in html
    assert "Derived from findings" not in html
    assert "study-counts-strip" not in html

    # biological_summary now renders as ctr-context under the Claim (not a Summary. card)
    assert "Legacy-schema plain-English mechanism narrative." in html
    assert 'class="ctr-context"' in html


# ---------------------------------------------------------------------------
# Task 7: Decide tab — "Biological validation" -> "Empirical validation"
# rename (data-* attrs stay `biological_validation` for back-compat), and
# strip the two explanatory paragraphs (VERDICT & CONCLUSION intro, and the
# three-track "Each track is independent..." explainer). Fix round 1: also
# covers the "Conclusion logic — gate decision" block's <em>Biological
# validation:</em> label (rendered only when
# conclusion_logic.if_primary_tests_pass.biological_validation is set), which
# round 1 of this task missed.
# ---------------------------------------------------------------------------

def test_decide_empirical_rename_and_no_prose(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "decide-empirical-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "decide-empirical-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
        # Populate the conclusion-logic gate-decision branch so the
        # <em>Empirical validation:</em> label (formerly "Biological
        # validation:") actually renders in served HTML, not just the
        # three-track verdict card's labels.
        "conclusion_logic": {
            "if_primary_tests_pass": {
                "biological_validation": "Matches the literature band.",
            },
        },
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/decide-empirical-study")
    assert resp.status_code == 200
    html = resp.text

    assert "Empirical validation" in html
    assert "Biological validation" not in html
    assert "Synthesises the latest run" not in html
    assert "Each track is independent" not in html

    # The conclusion-logic gate-decision line rendered (proves the assertion
    # above actually covers that block, not just the three-track card).
    assert "Matches the literature band." in html

    # Back-compat: the machine-readable attributes/dict keys must be
    # unchanged — only the human-readable label text was renamed.
    assert 'data-verdict-track="biological_validation"' in html
    assert 'data-narrative-path="conclusion_verdicts.biological_validation.basis"' in html


# ---------------------------------------------------------------------------
# Task 8: Simulations tab -> read-only runs table. Remove the Reproduce/CLI
# card, both explanatory paragraphs, the simulation_set / "Simulation set (N
# runs planned)" cards block, the Configure & Run mount, and the remote-run
# (smsvpctest) form. Keep only the #study-sim-table mount (populated
# client-side from /api/simulations, so row data itself isn't in served
# HTML — assert only the mount + absence of the removed controls).
# ---------------------------------------------------------------------------

def test_simulations_is_readonly_table(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "sims-readonly-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "sims-readonly-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
        # Legacy simulation_set entry — must NOT render as cards anymore.
        "simulation_set": [{
            "name": "core-sim",
            "kind": "single",
            "base_model": "pkg.composites.core",
            "status": "ready",
            "seeds": [0],
            "duration_min": 5,
        }],
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/sims-readonly-study")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="reproduce-card"' not in html
    assert 'Run on remote' not in html
    assert 'Simulation set' not in html
    assert 'study-configure-run' not in html
    assert 'id="study-sim-table"' in html


def test_visualizations_stripped_to_gallery(tmp_path, dashboard_client):
    """Task 9: Visualizations tab is one figure gallery — no explanatory
    prose and no per-mount section chrome (headings), just the three mounts
    (native gallery, embedded-viz iframes, latest-run charts) under a single
    `study-figures` wrapper + `<h2>Figures</h2>`."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "viz-gallery-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "viz-gallery-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/viz-gallery-study")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="panel-visualize"' in html
    assert 'study-figures' in html
    assert 'id="native-gallery-panel"' in html
    assert 'id="viz-charts-panel"' in html

    assert 'Figures for this study' not in html
    assert 'native-gallery-section' not in html
    assert 'Baseline analysis gallery' not in html
    assert 'Embedded visualizations' not in html
    assert 'Latest-run charts' not in html
    assert 'No baseline figures yet' not in html


def test_visualizations_empty_state_unions_all_three_sources(tmp_path, dashboard_client):
    """Fable A #3: the "No figures yet." empty state must not be baked into
    the served HTML as visible text next to real figures. It lives in a
    single shared #figures-empty-message element, hidden by default, that JS
    only reveals once the native gallery AND latest-run charts (both loaded
    async) AND the embed_visualizations iframes (server-rendered here) have
    all reported empty. A study WITH embed_visualizations must still
    server-render the embed markup, so the union has content even though the
    (async, JS-only) native gallery can't be observed from served HTML."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "viz-gallery-study-embeds"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "viz-gallery-study-embeds",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
        "embed_visualizations": [
            {"name": "Preview", "url": "/reports/figures/viz-gallery-study-embeds/preview.html"},
        ],
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/viz-gallery-study-embeds")
    assert resp.status_code == 200
    html = resp.text

    # The embed renders as real figure content in the union.
    assert 'embed-viz-card' in html
    assert 'Preview' in html

    # The empty-state text exists exactly once, as the shared element, and
    # starts hidden — it is not painted unconditionally by any one source.
    assert html.count('No figures yet.') == 1
    assert 'id="figures-empty-message"' in html
    empty_idx = html.index('id="figures-empty-message"')
    tag_start = html.rindex('<p', 0, empty_idx)
    tag_end = html.index('>', empty_idx)
    assert 'style="display:none"' in html[tag_start:tag_end]


def test_visualizations_gallery_one_figure_card_no_source_chrome(tmp_path, dashboard_client):
    """Task V2 (Fable §4.5): the three figure sources collapse into ONE
    flowing gallery of a single `.figure-card` style — the `<h2>Figures</h2>`
    and the old `.embed-viz-card` header-bar chrome are gone, and every card
    carries a caption row with a muted source chip. A figure with no run_id
    (a hand-authored embed) renders no run-link — no fabricated provenance
    (the embed/chart sources get run_id in Task V3, next)."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "viz-gallery-study-v2"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "viz-gallery-study-v2",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
        "embed_visualizations": [
            {"name": "Preview", "url": "/reports/figures/viz-gallery-study-v2/preview.html",
             "description": "Hand-authored preview page, no run_id."},
        ],
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/viz-gallery-study-v2")
    assert resp.status_code == 200
    html = resp.text

    assert "<h2>Figures</h2>" not in html

    # One card style, dual-classed for the union empty-state's
    # `.embed-viz-card` selector (`_figuresHasEmbeds`) — but the old
    # header-bar/border/background chrome is gone.
    assert 'class="figure-card embed-viz-card"' in html
    assert 'border-bottom:1px solid #e5e7eb;background:#f9fafb' not in html
    assert 'border:1px solid #e2e8f0;border-radius:6px;margin-bottom:14px;background:#fff' not in html

    # Caption row: muted source chip + title + "Open in new tab ↗" affordance.
    assert 'figure-caption-row' in html
    assert 'figure-source-chip">embed<' in html
    assert 'Preview' in html
    assert 'Open in new tab' in html

    # No run_id on this source in V2 — the caption must omit the run link
    # rather than fabricate one. (`.figure-run-link` itself appears once, as
    # the CSS selector in the page's <style> block, since it's shared with
    # the JS-rendered sources — checking for the actual anchor markup, not
    # the bare class-name substring, is what proves nothing was fabricated.)
    assert 'class="figure-run-link"' not in html


def test_visualizations_embed_with_run_id_renders_run_link(tmp_path, dashboard_client):
    """Task V3: an embed sourced from studies/<name>/viz/*.html (auto-rendered
    by render_visualizations against runs.db) carries a genuine run_id, and
    the server-rendered card must render the `from run … ↗` link — reading
    v.run_id in the template — same as the native gallery / chart sources."""
    import sqlite3

    ws = tmp_path / "ws"
    sd = ws / "studies" / "viz-gallery-study-run-embed"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "viz-gallery-study-run-embed",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    # A completed run recorded in runs.db …
    db = sd / "runs.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT, spec_id TEXT, started_at REAL, "
        "completed_at REAL, status TEXT)"
    )
    conn.execute(
        "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?)",
        ("run-embed-1", "viz-gallery-study-run-embed", 0.0, 10.0, "completed"),
    )
    conn.commit()
    conn.close()

    # … and a matching auto-rendered viz HTML file, discovered + tagged with
    # that run's id by discover_viz_html_files.
    viz_dir = sd / "viz"
    viz_dir.mkdir()
    (viz_dir / "trace.html").write_text("<html>hi</html>")

    client = dashboard_client(ws)
    resp = client.get("/studies/viz-gallery-study-run-embed")
    assert resp.status_code == 200
    html = resp.text

    assert 'class="figure-run-link" data-run-id="run-embed-1"' in html
    assert "from run run-embed-1" in html


def test_visualizations_stale_embed_omits_run_link(tmp_path, dashboard_client):
    """V3 review fix (round 1): a STALE studies/<name>/viz/*.html — one that
    predates the latest of two recorded runs — must NOT be attributed to
    that latest run. The card renders (with its "may predate" warning) but
    carries no run_id, so the caption omits the run-link entirely rather
    than fabricate a link to a run the file never derived from."""
    import sqlite3

    ws = tmp_path / "ws"
    sd = ws / "studies" / "viz-gallery-study-stale-embed"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "viz-gallery-study-stale-embed",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    # TWO recorded runs — an older one and a newer "latest" one — so the
    # viz file can genuinely predate the latest without predating every run.
    db = sd / "runs.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT, spec_id TEXT, started_at REAL, "
        "completed_at REAL, status TEXT)"
    )
    conn.execute(
        "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?)",
        ("run-old", "viz-gallery-study-stale-embed", 0.0, 10.0, "completed"),
    )
    conn.execute(
        "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?)",
        ("run-new", "viz-gallery-study-stale-embed", 1_800_000_000.0,
         1_800_000_100.0, "completed"),
    )
    conn.commit()
    conn.close()

    # Rendered right after run-old, long before run-new even started → stale
    # relative to run-new (the latest).
    import os
    viz_dir = sd / "viz"
    viz_dir.mkdir()
    leftover = viz_dir / "leftover.html"
    leftover.write_text("<html>hi</html>")
    os.utime(leftover, (20.0, 20.0))

    client = dashboard_client(ws)
    resp = client.get("/studies/viz-gallery-study-stale-embed")
    assert resp.status_code == 200
    html = resp.text

    assert "leftover" in html  # the card itself still renders
    assert 'class="figure-run-link"' not in html
    assert "from run run-new" not in html
    assert "from run run-old" not in html


def test_visualizations_empty_study_shows_empty_state_element(tmp_path, dashboard_client):
    """Regression guard (Fable A #3 union logic, unaffected by Task V2): a
    study with no embed_visualizations still server-renders the shared,
    hidden `#figures-empty-message` line (JS reveals it once the async
    native gallery + charts sources also report empty)."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "viz-gallery-study-empty"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "viz-gallery-study-empty",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/viz-gallery-study-empty")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="figures-empty-message"' in html
    idx = html.index('id="figures-empty-message"')
    tag_start = html.rindex('<p', 0, idx)
    tag_end = html.index('>', idx)
    assert 'style="display:none"' in html[tag_start:tag_end]
    assert 'embed-viz-card' not in html


# ---------------------------------------------------------------------------
# Task 10: Tests — merge Report Cards + Behavioral Tests into ONE concept.
# The Tests pillar now has a single sub-nav member; report cards + behavioral
# gates render top-to-bottom in one `data-kind="tests"` panel, led by a
# gate/audit summary strip. No separate "Report Cards" tab/pillar remains.
# ---------------------------------------------------------------------------

def test_tests_merged_single_concept(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "tests-merged-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "tests-merged-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))
    # C6: the top report-cards mount is now server-gated on
    # comparison_plotly_url (absent != empty — no empty box when there's
    # nothing to show there any more, since per-card content moved inline to
    # each report_card row). Give the study a comparison plot so this test
    # can still verify placement of the mount inside the Tests panel.
    (sd / "viz").mkdir(parents=True)
    (sd / "viz" / "comparison_plotly.html").write_text("<div>plotly</div>", encoding="utf-8")

    client = dashboard_client(ws)
    resp = client.get("/studies/tests-merged-study")
    assert resp.status_code == 200
    html = resp.text

    # Gate/audit summary strip mount is present.
    assert 'tests-gate-summary' in html
    # The old explanatory prose is gone.
    assert 'Graded comparison scorecards' not in html
    # The report-cards sub-nav member / pillar-tab / standalone panel are gone.
    assert 'data-kind="report-cards"' not in html
    assert 'id="panel-report-cards"' not in html
    assert '_setStudyTab(\'report-cards\')' not in html
    # The report-cards mount now lives INSIDE the tests panel (one concept).
    tests_panel = html[html.index('id="panel-tests"'):html.index('id="panel-audit"')]
    assert 'id="report-cards-panel"' in tests_panel
    assert '<h2>Tests</h2>' in tests_panel
    assert 'The bar.' in tests_panel   # reframed subtitle (was "Audit — …", a collision with the Audit tab)


# ---------------------------------------------------------------------------
# Fable Increment A, Task 5 (change #4): Tests tab used to render its gate
# set twice — a client-side "N/M gates passed" strip that ALSO built its own
# per-gate ✓/✗ `<li>` rows (_renderTestsGateSummary), stacked directly above
# the server-rendered "Behavioral tests" list (#tests-list) that lists the
# same gates again, richer (requires/Assertion). Fable §4.6: merge into one
# score line + one list. The gate-summary mount (#tests-gate-summary) stays
# as the score-line container, but its render function must no longer build
# a second per-gate list — that detail lives ONCE, in #tests-list.
# ---------------------------------------------------------------------------

def test_tests_gate_summary_not_duplicated_by_behavioral_list(tmp_path, dashboard_client):
    import re
    from pathlib import Path
    import vivarium_workbench

    ws = tmp_path / "ws"
    sd = ws / "studies" / "gate-dedup-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "gate-dedup-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
        "behavior_tests": [
            {"name": "daughters_hydrated", "classification": "primary",
             "measure": "daughter_mass_ratio", "pass_if": "ratio <= 2.0"},
            {"name": "two_generations_complete", "classification": "primary",
             "requires_simulation": "baseline_2gen"},
        ],
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/gate-dedup-study")
    assert resp.status_code == 200
    html = resp.text

    tests_panel = html[html.index('id="panel-tests"'):html.index('id="panel-audit"')]

    # Exactly one score-line mount and one per-gate detail list in the
    # server-rendered markup — no second copy of either.
    assert tests_panel.count('id="tests-gate-summary"') == 1
    assert tests_panel.count('id="tests-list"') == 1

    # Each declared gate gets exactly one row (id="bt-<name>") in the served
    # panel — in the behavioral list; nothing pre-renders a second row for
    # it into the gate-summary strip server-side.
    assert tests_panel.count('id="bt-daughters_hydrated"') == 1
    assert tests_panel.count('id="bt-two_generations_complete"') == 1

    # The client-side gate-summary renderer (_renderTestsGateSummary) must
    # build the score line only, not a second per-gate list — assert its
    # function body no longer iterates the gate/test set to emit rows (the
    # duplication this task removes) while still emitting the score line.
    js = (Path(vivarium_workbench.__file__).parent / "static" / "study-detail.js").read_text(
        encoding="utf-8"
    )
    m = re.search(
        r"function _renderTestsGateSummary\(spec\) \{(.*?)\n  \}",
        js,
        re.DOTALL,
    )
    assert m, "_renderTestsGateSummary not found in study-detail.js"
    fn_body = m.group(1)
    assert "gates passed" in fn_body
    # No per-gate row construction left in the summary renderer (the old
    # duplicate list): no per-test iteration building <li> rows.
    assert "tests.map(" not in fn_body
    assert "<li" not in fn_body


# ---------------------------------------------------------------------------
# Task 11 / E2: Exports functional bits (result-files list, "Download all
# (.zip)" link, raw simulation-data list) now live on Simulations — the
# Exports tab itself was deleted (Task E4, its panel was an empty shell
# after E1-E3 relocated everything it held). See test_study_artifacts_on_
# simulate.py for the current home of these assertions.
# ---------------------------------------------------------------------------

def test_exports_tab_and_panel_removed(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "exports-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "exports-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/exports-study")
    assert resp.status_code == 200
    html = resp.text

    # Functional bits still present — now under the Analyses/Results Evidence
    # panels (study-spine reorg, spec §3.3/3.4; previously under
    # Simulations, Task E2).
    assert 'id="data-files"' in html
    assert 'id="raw-data-list"' in html
    assert 'id="data-download-all"' in html
    assert '/api/study-analysis-zip?study=exports-study' in html

    # The Exports tab + panel are gone (Task E4).
    assert 'data-kind="data"' not in html
    assert 'id="panel-data"' not in html


# ---------------------------------------------------------------------------
# Fable Increment A, Task 1: model_change.modified_processes must render as
# formatted fields, never as a bare Python dict repr (R1 — never render a
# raw Python dict or JSON blob in the default view).
# ---------------------------------------------------------------------------

def test_modified_processes_renders_fields_not_dict_repr(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "modified-processes-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "modified-processes-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
        "model_change": {
            "base_model": "pkg.composites.core",
            "modified_processes": [
                {
                    "name": "EcoliWCM._handle_division",
                    "why": "Daughter cells were not rehydrating water mass on split.\nFixed by recomputing hydration post-division.",
                    "status": "required",
                    "requirement_id": "req-2-daughter-hydration-fix",
                },
                "a legacy plain-string entry",
            ],
        },
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/modified-processes-study")
    assert resp.status_code == 200
    html = resp.text

    # Never a raw Python dict repr.
    assert "{'name':" not in html
    assert "'requirement_id':" not in html
    assert "{&#39;name&#39;" not in html

    # Fields rendered individually.
    assert "<code>EcoliWCM._handle_division</code>" in html
    assert "Daughter cells were not rehydrating water mass on split." in html
    assert "required" in html
    assert "req-2-daughter-hydration-fix" in html

    # Plain-string entries (the `is mapping` guard's else branch) still render.
    assert "a legacy plain-string entry" in html


# ---------------------------------------------------------------------------
# Fable Increment A, Task 8: delete three redundant/vestigial Overview blocks
# (§4.1 "Cut" — pure deletions only; the Relocate items — pipeline_gate /
# assumptions / limitations moving to Decide — are a later increment and are
# untouched here):
#   1. the Status subsection (verbatim duplicate of the header status pill)
#   2. the behavioral-tests count strip + "View on Tests tab ->"
#   3. the follow-up studies pointer block ("canonical surface is Decide")
# Findings / Question&approach / Conclusion must keep rendering.
# ---------------------------------------------------------------------------

def test_overview_deleted_blocks_absent_core_content_kept(tmp_path, dashboard_client):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "overview-declutter-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "overview-declutter-study",
        "kind": "biological",
        "phase": "simulate",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
        # Would render the tests-count strip + "View on Tests tab" if the
        # block still existed.
        "behavior_tests": [{"name": "beh-a", "classification": "primary"}],
        # Would render the follow-ups pointer block if it still existed.
        "follow_up_studies": [{"id": "study-2", "question": "Next question?"}],
        "findings": [{"statement": "Daughter cells hydrate within one tick."}],
        "report": {"conclusion": "The model reproduces the expected behavior."},
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/overview-declutter-study")
    assert resp.status_code == 200
    html = resp.text

    overview = html[html.index('id="panel-overview"'):html.index('id="panel-compose"')]

    # 1. Status subsection (verbatim header-pill duplicate) is gone.
    assert '<h3 class="overview-label">Status</h3>' not in overview
    assert 'Lifecycle phase:' not in overview

    # 2. Behavioral-tests count strip + its Tests-tab link is gone.
    assert '<h2 class="overview-label">Behavioral tests</h2>' not in overview
    assert 'View on Tests tab' not in overview

    # 3. Follow-up studies pointer block is gone.
    assert '<h2 class="overview-label">Follow-up studies</h2>' not in overview
    assert 'View follow-ups on the' not in overview
    assert 'canonical surface is the' not in overview

    # Overview is now Claim -> Test -> Result; findings still render, the
    # Conclusion editor moved to the Decide tab.
    assert '>Claim</h2>' in overview and '>Model &amp; approach</h2>' in overview and '>Result</h2>' in overview
    assert 'Findings' in overview
    assert 'Daughter cells hydrate within one tick.' in overview
    assert 'Question &amp; approach' not in overview


# ---------------------------------------------------------------------------
# Fable A #6: delete #study-subnav + the pillar/member indirection — the top
# `.study-pillar` buttons drive tab switching directly.
# ---------------------------------------------------------------------------

def test_pillars_drive_tabs_directly_no_subnav(tmp_path, dashboard_client):
    """Real served-HTML assertion (not just static grep): the second tab
    row (#study-subnav) must be entirely absent, all 11 `.study-pillar`
    buttons must be present and wired to call `_setStudyTab(<kind>)`
    directly, and the deleted pillar/member-indirection JS functions must
    not appear anywhere in the served page or its JS asset. (Task E4 dropped
    the Exports/data pillar; the study-spine reorg then added Results +
    Analyses (spec §1/§3.3/§3.4) and Audit + Build (spec §3.7/§3.8), so the
    count is 11, not 7.)
    """
    ws = tmp_path / "ws"
    sd = ws / "studies" / "pillars-direct-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "pillars-direct-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/pillars-direct-study")
    assert resp.status_code == 200
    html = resp.text

    # Second tab row is gone.
    assert 'id="study-subnav"' not in html
    assert 'study-subnav' not in html

    # All 11 pillar buttons present, each carrying data-kind and calling
    # _setStudyTab(kind) directly (pillar name == kind, except
    # "decide" -> "conclusions"). Exports/data pillar was deleted (Task E4);
    # Results + Analyses added by the study-spine reorg (spec §1/§3.3/§3.4);
    # Audit + Build added by the same reorg (spec §3.7/§3.8).
    pillar_to_kind = {
        "understand": "overview", "compose": "compose", "readouts": "readouts",
        "simulate": "simulate", "results": "results", "analyses": "analyses",
        "visualize": "visualize", "tests": "tests", "audit": "audit",
        "build": "build", "decide": "conclusions",
    }
    for pillar, kind in pillar_to_kind.items():
        assert f'data-kind="{kind}"' in html and f"_setStudyTab('{kind}')" in html, \
            f"pillar {pillar!r} not wired to _setStudyTab('{kind}')"
    import re
    assert len(re.findall(r'<button class="study-pillar[^"]*"', html)) == 11
    assert 'data-kind="data"' not in html

    # No pillar/member indirection left anywhere (grep-proven, both served
    # HTML and the JS asset it references).
    for fn in ("_setStudyPillar", "_showPillarSubnav", "_pillarForKind"):
        assert fn not in html
    js_path = Path(__file__).resolve().parents[1] / "vivarium_workbench" / "static" / "study-detail.js"
    js = js_path.read_text()
    for fn in ("_setStudyPillar", "_showPillarSubnav", "_pillarForKind"):
        assert fn not in js, f"{fn} should have been deleted from study-detail.js"


# ---------------------------------------------------------------------------
# Fable G1: act rail (five acts + per-act gate dots) + reposition Readouts to
# slot 3, so the acts group contiguously (Design = Model+Readouts, Evidence =
# Simulations+Visualizations). Spec: 2026-08-01-study-design-fable-pass.md
# §9.2 (five acts), §9.3 (revised tab bar + act rail).
# ---------------------------------------------------------------------------

def test_act_rail_renders_above_tab_nav(tmp_path, dashboard_client):
    """The five act labels render inside `<nav class="study-tabs">`, each
    with a gate-dot element carrying a stable `data-gate` hook (state wiring
    is Task G2's job — here it must be present but neutral/not-assessed).

    ACTRAIL fix: the act rail is no longer a standalone `.act-rail` row
    sitting above an independent `.study-pillars` row (those two sibling
    rows never lined up — no shared columns). Each act's label and its tabs
    now live together in one `.act-cluster` column, so this test asserts
    against the whole `<nav>` rather than a `.act-rail`/`.study-pillars`
    split. See test_act_clusters_group_label_with_own_tabs below for the
    per-cluster grouping assertion."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "act-rail-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "act-rail-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/act-rail-study")
    assert resp.status_code == 200
    html = resp.text

    nav_i = html.index('<nav class="study-tabs"')
    nav_end = html.index('</nav>', nav_i)
    nav = html[nav_i:nav_end]

    # The old standalone containers are gone — labels and tabs are grouped
    # into `.act-cluster` columns instead.
    assert 'class="act-rail"' not in nav
    assert 'class="study-pillars"' not in nav

    # Five act labels, in order. Task E4 removed the sixth (Record-drawer
    # "Exports") cluster — the rail is now exactly these five.
    for label in ("The Study", "Design", "Evidence", "Assurance", "Decision"):
        assert label in nav, f"act label {label!r} missing from act rail"
    order = [nav.index(label) for label in
             ("The Study", "Design", "Evidence", "Assurance", "Decision")]
    assert order == sorted(order), "act labels out of order"
    assert "Exports" not in nav

    # Each of the five acts carries a gate-dot hook: a stable class +
    # data-gate attribute, neutral state — G2 recolors it, doesn't need to
    # touch this markup.
    import re
    dots = re.findall(r'<span class="act-gate-dot" data-gate="([a-z]+)"[^>]*>', nav)
    assert dots == ["study", "design", "evidence", "assurance", "decision"], dots
    assert 'data-gate-state="not-assessed"' in nav


def test_act_clusters_group_label_with_own_tabs(tmp_path, dashboard_client):
    """ACTRAIL fix (user-selected: "grouped clusters, aligned"): each act's
    label and its own tabs live in ONE `.act-cluster[data-act=...]` column,
    so alignment is structural rather than hand-tuned spacing. This is the
    crux assertion — every `.act-cluster` must contain both its `.act-label`
    text and its own `data-kind` buttons, not just sit near them."""
    import re

    ws = tmp_path / "ws"
    sd = ws / "studies" / "act-cluster-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "act-cluster-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/act-cluster-study")
    assert resp.status_code == 200
    html = resp.text

    nav_i = html.index('<nav class="study-tabs"')
    nav_end = html.index('</nav>', nav_i)
    nav = html[nav_i:nav_end]

    # Split into per-cluster segments on the `.act-cluster` opening-tag
    # marker — each segment holds exactly one cluster's content (label +
    # its tabs) up to the next cluster (or the end of <nav>).
    segments = nav.split('<div class="act-cluster')
    clusters = {}
    for seg in segments[1:]:
        m = re.match(r'[^>]*data-act="([a-z]+)"', seg)
        assert m, f"act-cluster segment missing data-act: {seg[:80]!r}"
        clusters[m.group(1)] = seg

    # Task E4 deleted the sixth "record" cluster (Exports) — five narrative
    # acts remain, each with a gate dot. Study-spine reorg (spec §1): the
    # `simulate` pillar moved Evidence -> Design (after readouts), Evidence
    # gained `results` + `analyses` ahead of `visualize`, and Assurance
    # gained `audit` + `build` alongside `tests` (spec §3.7/§3.8).
    expected = {
        "study": {"label": "The Study", "kinds": ["overview"]},
        "design": {"label": "Design", "kinds": ["compose", "readouts", "simulate"]},
        "evidence": {"label": "Evidence", "kinds": ["results", "analyses", "visualize"]},
        "assurance": {"label": "Assurance", "kinds": ["tests", "audit", "build"]},
        "decision": {"label": "Decision", "kinds": ["conclusions"]},
    }
    assert set(clusters) == set(expected), clusters.keys()
    assert "record" not in clusters

    for act, spec in expected.items():
        seg = clusters[act]
        assert spec["label"] in seg, f"{act} cluster missing label {spec['label']!r}"
        for kind in spec["kinds"]:
            assert f'data-kind="{kind}"' in seg, f"{act} cluster missing data-kind={kind!r}"
            assert f"_setStudyTab('{kind}')" in seg, f"{act} cluster's {kind!r} button missing onclick"

        assert re.search(r'<span class="act-gate-dot" data-gate="%s" data-gate-state="[a-z-]+">' % act, seg), \
            f"{act} cluster missing its act-gate-dot with data-gate-state"

    # All 11 `.study-pillar` buttons are present in the nav (Exports/data
    # pillar was deleted — Task E4; Results + Analyses, then Audit + Build,
    # added by the study-spine reorg).
    all_kinds = ["overview", "compose", "readouts", "simulate", "results",
                 "analyses", "visualize", "tests", "audit", "build", "conclusions"]
    btns = re.findall(r'<button class="study-pillar[^"]*"[^>]*>', nav)
    assert len(btns) == 11, f"expected 11 pillar buttons, got {len(btns)}"
    for kind in all_kinds:
        assert f'data-kind="{kind}"' in nav and f"_setStudyTab('{kind}')" in nav
    assert 'data-kind="data"' not in nav


def test_readouts_repositioned_to_slot_three(tmp_path, dashboard_client):
    """Tab order is now Overview, Model, Readouts, Simulations, Results,
    Analyses, Visualizations, Tests, Audit, Build, Decide — Readouts moves
    so Design (Model+Readouts+Simulations) and Evidence (Results+Analyses+
    Visualizations) each group contiguously per act. Panel switching is
    unaffected (`_setStudyTab` selects by `data-kind`, not position). Task
    E4 later dropped the trailing Exports tab; the study-spine reorg moved
    Simulations into Design, added Results + Analyses to Evidence, and added
    Audit + Build to Assurance."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "readouts-order-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "readouts-order-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/readouts-order-study")
    assert resp.status_code == 200
    html = resp.text

    kinds_in_order = [
        "overview", "compose", "readouts", "simulate", "results",
        "analyses", "visualize", "tests", "audit", "build", "conclusions",
    ]
    positions = [html.index(f'data-kind="{k}"') for k in kinds_in_order]
    assert positions == sorted(positions), (
        f"expected pillar order {kinds_in_order}, got positions {positions}"
    )
    # Readouts now precedes Simulations in the pillar row.
    assert html.index('data-kind="readouts"') < html.index('data-kind="simulate"')
    # Simulations (Design) still precedes Results (Evidence).
    assert html.index('data-kind="simulate"') < html.index('data-kind="results"')

    # All 11 pillars still present and wired to _setStudyTab (Exports/data
    # pillar was deleted — Task E4; Results + Analyses, then Audit + Build,
    # added by the study-spine reorg).
    for kind in kinds_in_order:
        assert f'data-kind="{kind}"' in html and f"_setStudyTab('{kind}')" in html
    assert 'data-kind="data"' not in html


def test_readouts_panel_renders_three_contract_blocks(tmp_path, dashboard_client):
    """The streamlined Readouts panel carries the always-visible emitter summary
    mount + the emitted-paths table mount, in that order. (The old "Outputs &
    shapes" block was removed as redundant with the emitted-paths table.)"""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "readouts-blocks-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "readouts-blocks-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/readouts-blocks-study")
    assert resp.status_code == 200
    html = resp.text

    ro_start = html.index('id="panel-readouts"')
    ro_end = html.find('class="study-tab-panel"', ro_start + 10)
    panel = html[ro_start: ro_end if ro_end != -1 else len(html)]

    # The emitter summary label is JS-rendered into this mount at runtime.
    assert 'id="readouts-emitter"' in panel
    assert 'id="readouts-table"' in panel
    # The removed shapes block must be gone.
    assert 'id="readouts-shapes"' not in panel
    assert "Outputs &amp; shapes" not in panel and "Outputs & shapes" not in panel
    # Block order: emitter summary, then emitted-paths table.
    assert panel.index('id="readouts-emitter"') < panel.index('id="readouts-table"')


# ---------------------------------------------------------------------------
# Fable G2: six status axes → six gates in `status ▾`, computed-vs-authored,
# and feeding the act-rail gate dots (G1). Spec: 2026-08-01-study-design-
# fable-pass.md §13 (gating model), §13.1 (where gates live), §13.2 (states).
# ---------------------------------------------------------------------------

def _write_g2_study(ws, slug, extra_spec):
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = {
        "schema_version": 3,
        "name": slug,
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
    }
    spec.update(extra_spec)
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))


def test_status_ladder_shows_six_gate_names(tmp_path, dashboard_client):
    """`status ▾` relabels the six axes as the six gates (§13's table):
    Plan / Execution / Evidence / Quality / Decision / Release — not the old
    axis words (Design/Implementation/Simulation/Evaluation/Gate/Expert
    review)."""
    ws = tmp_path / "ws"
    _write_g2_study(ws, "gate-names-study", {"design_status": "designed"})
    client = dashboard_client(ws)
    resp = client.get("/studies/gate-names-study")
    assert resp.status_code == 200
    html = resp.text

    panel_i = html.index('class="status-detail-panel"')
    panel_end = html.index("</details>", panel_i)
    seg = html[panel_i:panel_end]
    for gate_name in ("Plan", "Execution", "Evidence", "Quality", "Decision", "Release"):
        assert gate_name in seg, f"gate name {gate_name!r} missing from status ladder"


def test_status_ladder_shows_authored_axis_state(tmp_path, dashboard_client):
    """A study with an authored axis (design_status) shows that gate's
    (Plan's) authored value in the ladder."""
    ws = tmp_path / "ws"
    _write_g2_study(ws, "authored-axis-study", {"design_status": "designed"})
    client = dashboard_client(ws)
    resp = client.get("/studies/authored-axis-study")
    assert resp.status_code == 200
    html = resp.text

    panel_i = html.index('class="status-detail-panel"')
    panel_end = html.index("</details>", panel_i)
    seg = html[panel_i:panel_end]
    assert "1 · Plan" in seg
    assert "designed" in seg
    assert 'gate-state-passed' in seg


def test_status_ladder_shows_computed_vs_authored_indicator(tmp_path, dashboard_client):
    """Where a computed value exists for a gate (Execution/Evidence, sourced
    from viva_superpowers.study_status.derive_status, already attached to the
    spec as `derived_status` by load_study_detail_spec) a `◆ computed:`
    indicator renders in the gate ladder body."""
    ws = tmp_path / "ws"
    _write_g2_study(ws, "computed-axis-study", {
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed",
                   "outcomes": {"t1": {"result": "PASS"}}}],
    })
    client = dashboard_client(ws)
    resp = client.get("/studies/computed-axis-study")
    assert resp.status_code == 200
    html = resp.text

    panel_i = html.index('class="status-detail-panel"')
    panel_end = html.index("</details>", panel_i)
    seg = html[panel_i:panel_end]
    assert "gate-computed-chip" in seg
    assert "◆ computed:" in seg
    # Execution's computed value is sourced from run history (derived_status).
    assert "2 · Execution" in seg


def test_act_rail_dots_reflect_gate_states(tmp_path, dashboard_client):
    """A study with real gate data does NOT render every act dot as
    not-assessed — G2 feeds `data-gate-state` from the computed gate ladder
    (G1 shipped the hooks neutral; this is the wiring)."""
    import re

    ws = tmp_path / "ws"
    _write_g2_study(ws, "act-dots-study", {
        "design_status": "designed",
        "gate_status": "passed",
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed",
                   "outcomes": {"t1": {"result": "FAIL"}}}],
    })
    client = dashboard_client(ws)
    resp = client.get("/studies/act-dots-study")
    assert resp.status_code == 200
    html = resp.text

    dots = re.findall(
        r'<span class="act-gate-dot" data-gate="([a-z]+)" data-gate-state="([a-z-]+)">',
        html,
    )
    assert len(dots) == 5, dots
    states = {gate: state for gate, state in dots}
    # design_status: designed -> Plan passes -> the "design" act dot is "passed".
    assert states["design"] == "passed"
    # Not every dot is the G1 placeholder "not-assessed" for a study with data.
    assert set(states.values()) != {"not-assessed"}


def test_act_rail_dots_reflect_computed_only_passed_state(tmp_path, dashboard_client):
    """Fix round 1 (Fable G2): the common real-world study has NO
    hand-authored axes — only a derived/computed path (a completed run with a
    PASS outcome + `computed_gate_verdict.result == "passed"`). The act-rail
    dots and the gate-ladder's own per-gate `state` must NOT collapse to the
    grey `not-assessed` placeholder just because the AUTHORED side is empty;
    an empty authored axis must defer to a real computed `passed`, not
    outrank it. This is the direction `test_act_rail_dots_reflect_gate_states`
    above does NOT cover (that test only authors a failing/blocked path, so
    it passed even when `not-assessed` incorrectly outranked `passed` in the
    combining rank)."""
    import re

    ws = tmp_path / "ws"
    _write_g2_study(ws, "computed-passed-study", {
        # No design_status / simulation_status / evaluation_status /
        # gate_status / expert_review_status authored at all.
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed",
                   "outcomes": {"t1": {"result": "PASS"}}}],
        # derive_evaluation_status requires a completed run AND recorded
        # verdicts/findings (viva_superpowers.study_status._has_recorded_verdicts)
        # -- without this, evaluation_status derives "not_evaluated", not
        # "evaluated", which is a correct not-assessed, not the bug under test.
        "findings": [{"statement": "the run behaved as expected"}],
        # Task V5: Evidence's computed state now also folds in V4's
        # visualization-readiness bar (lib.viz_gate.study_visualization_status).
        # This fixture is about the derived_status roll-up, not visualizations,
        # so give it a qualifying figure (interactive + run-linked) to keep it
        # out of scope for that signal — see test_visualization_gap_* below
        # for the downgrade path itself.
        "embed_visualizations": [{"url": "/studies/computed-passed-study/viz/plot.html",
                                    "run_id": "r1"}],
    })
    client = dashboard_client(ws)
    resp = client.get("/studies/computed-passed-study")
    assert resp.status_code == 200
    html = resp.text

    dots = re.findall(
        r'<span class="act-gate-dot" data-gate="([a-z]+)" data-gate-state="([a-z-]+)">',
        html,
    )
    assert len(dots) == 5, dots
    states = {gate: state for gate, state in dots}
    # Execution + Evidence (derived_status: simulation ran, evaluation
    # evaluated) roll up into the "evidence" act dot -> must read "passed",
    # not grey "not-assessed".
    assert states["evidence"] == "passed", states
    # Quality shares evaluation_status's computed value with Evidence per
    # §13's table -> the "assurance" act dot must also read "passed".
    assert states["assurance"] == "passed", states
    # Decision (computed_gate_verdict.result == "passed", no persisted
    # gate_evaluator so no divergence) -> "decision" act dot reads "passed".
    assert states["decision"] == "passed", states
    # "design" (Plan) has no computed source wired in at all (only
    # simulation_status/evaluation_status/gate_status are derivable) and no
    # authored design_status either -> correctly stays "not-assessed", the
    # one dot this fixture gives no data for.
    assert states["design"] == "not-assessed", states


# ---------------------------------------------------------------------------
# Task V5 — fold V4's visualization-readiness signal into Evidence's
# computed state (§5(B), §6(c.3)). Reuses the existing gate-state vocabulary
# (`passed-with-conditions` is the gap/conditional tier — NOT a new token,
# never `blocked`) and the existing worst-of rollup.
# ---------------------------------------------------------------------------

def test_visualization_gap_downgrades_evidence_gate_and_act_dot(tmp_path, dashboard_client):
    """A study that fails the viz bar (no figures at all) has its Evidence
    gate's COMPUTED state downgraded from `passed` to the existing
    `passed-with-conditions` gap tier -- never a hard `blocked`/`failed`.
    Authored Evidence == `evaluated` (-> `passed`) so the computed-vs-authored
    divergence marker must render too, and the act-rail Evidence dot must
    reflect the downgrade (§13's Execution+Evidence -> "evidence" act)."""
    import re

    ws = tmp_path / "ws"
    _write_g2_study(ws, "viz-gap-study", {
        "evaluation_status": "evaluated",  # authored -> "passed" gate state
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed",
                   "outcomes": {"t1": {"result": "PASS"}}}],
        "findings": [{"statement": "the run behaved as expected"}],
        # No visualizations declared anywhere -> viz_gate.study_visualization_status
        # returns qualifies=False, reason="no figures".
    })
    client = dashboard_client(ws)
    resp = client.get("/studies/viz-gap-study")
    assert resp.status_code == 200
    html = resp.text

    panel_i = html.index('class="status-detail-panel"')
    panel_end = html.index("</details>", panel_i)
    seg = html[panel_i:panel_end]
    # The Evidence row's computed chip must show the gap tier, not `passed`
    # and not a `blocked` state.
    # Skip past the stepper SUMMARY (which also mentions "3 · Evidence") to
    # the detail BODY's per-gate rows, where the computed chip/divergence/gap
    # note actually render.
    body_i = seg.index("status-stepper-body")
    evidence_row_i = seg.index("3 · Evidence", body_i)
    evidence_row = seg[max(0, evidence_row_i - 200):evidence_row_i + 900]
    assert "gate-computed-chip gate-state-passed-with-conditions" in evidence_row, evidence_row
    assert "gate-state-blocked" not in evidence_row, evidence_row
    # Divergence marker: authored says passed, computed says gap-tier.
    assert "diverges from authored" in evidence_row, evidence_row
    # The short reason line.
    assert "visualization gap: no figures" in evidence_row, evidence_row

    dots = re.findall(
        r'<span class="act-gate-dot" data-gate="([a-z]+)" data-gate-state="([a-z-]+)">',
        html,
    )
    states = {gate: state for gate, state in dots}
    assert states["evidence"] == "passed-with-conditions", states


def test_visualization_gap_qualifying_study_not_downgraded(tmp_path, dashboard_client):
    """A study that PASSES the viz bar (an interactive, run-linked figure) is
    not touched by V5 at all: Evidence's computed state stays whatever it
    already was (`passed`), no divergence marker, no gap note -- no
    regression to the pre-V5 Evidence signal."""
    ws = tmp_path / "ws"
    _write_g2_study(ws, "viz-pass-study", {
        "evaluation_status": "evaluated",
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed",
                   "outcomes": {"t1": {"result": "PASS"}}}],
        "findings": [{"statement": "the run behaved as expected"}],
        "embed_visualizations": [{"url": "/studies/viz-pass-study/viz/plot.html",
                                    "run_id": "r1"}],
    })
    client = dashboard_client(ws)
    resp = client.get("/studies/viz-pass-study")
    assert resp.status_code == 200
    html = resp.text

    panel_i = html.index('class="status-detail-panel"')
    panel_end = html.index("</details>", panel_i)
    seg = html[panel_i:panel_end]
    # Skip past the stepper SUMMARY (which also mentions "3 · Evidence") to
    # the detail BODY's per-gate rows, where the computed chip/divergence/gap
    # note actually render.
    body_i = seg.index("status-stepper-body")
    evidence_row_i = seg.index("3 · Evidence", body_i)
    evidence_row = seg[max(0, evidence_row_i - 200):evidence_row_i + 900]
    assert "gate-computed-chip gate-state-passed" in evidence_row, evidence_row
    assert "gate-state-passed-with-conditions" not in evidence_row, evidence_row
    assert "diverges from authored" not in evidence_row, evidence_row
    assert "visualization gap" not in evidence_row, evidence_row


def test_visualization_gap_unreadable_study_no_downgrade_no_500(monkeypatch):
    """If `study_visualization_status` itself can't compute (simulated here
    since the real function is internally tolerant and never raises), V5
    must treat that as NO SIGNAL: no downgrade, and no exception propagates
    (which is what a live request would turn into a 500) -- matching V4's
    tolerance for an unreadable study.

    Exercised directly against `build_gate_ladder` (no `dashboard_client`):
    `dashboard_client` spawns a real server SUBPROCESS (see conftest.py), so
    an in-process monkeypatch of `study_visualization_status` would silently
    have no effect there -- this is the only way to actually force the
    exception path."""
    import vivarium_workbench.lib.viz_gate as viz_gate_mod
    from vivarium_workbench.lib.study_page import build_gate_ladder

    def _boom(ws_root, slug):
        raise RuntimeError("simulated unreadable study")

    monkeypatch.setattr(viz_gate_mod, "study_visualization_status", _boom)

    spec = {
        "evaluation_status": "evaluated",
        "derived_status": {
            "evaluation_status": {"value": "evaluated", "source": "derive_status"},
        },
    }
    gates = build_gate_ladder(spec, ws_root=Path("/nonexistent"), slug="s1")
    evidence = next(g for g in gates if g["key"] == "evidence")
    assert evidence["computed_state"] == "passed"
    assert evidence["viz_gap_reason"] is None
    assert evidence["diverges"] is False


def test_visualization_gap_info_nudge_does_not_downgrade_evidence(tmp_path):
    """Task Vcal: a study that HAS an interactive figure and HAS been run,
    but nothing's linked to a run yet (`gap_severity == "info"`), must NOT
    downgrade Evidence -- ONLY the no-interactive `"warning"` case does.
    Exercised directly against `build_gate_ladder` with a REAL study.yaml
    (embed_visualizations + runs:) so `study_visualization_status` computes
    for real rather than being mocked -- proving the wiring actually reads
    `gap_severity`, not the old flat `qualifies` check."""
    from vivarium_workbench.lib.study_page import build_gate_ladder

    ws = tmp_path / "ws"
    sd = ws / "studies" / "info-study"
    sd.mkdir(parents=True)
    sd_spec = {
        "name": "info-study",
        "embed_visualizations": [
            {"url": "/studies/info-study/viz/plot.html", "run_id": None},
        ],
        "runs": [{"name": "r1", "status": "completed"}],
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(sd_spec), encoding="utf-8")

    spec = {
        "evaluation_status": "evaluated",
        "derived_status": {
            "evaluation_status": {"value": "evaluated", "source": "derive_status"},
        },
    }
    gates = build_gate_ladder(spec, ws_root=ws, slug="info-study")
    evidence = next(g for g in gates if g["key"] == "evidence")
    assert evidence["computed_state"] == "passed"
    assert evidence["viz_gap_reason"] is None
    assert evidence["diverges"] is False


def test_visualization_gap_unrun_study_does_not_downgrade_evidence(tmp_path):
    """Task Vcal: a study that HAS an interactive figure but has NEVER been
    run at all (`gap_severity is None`, the silent unrun case) must NOT
    downgrade Evidence either -- an unrun study isn't a visualization
    problem, so it must be treated identically to the fully-qualifying
    case for gate purposes."""
    from vivarium_workbench.lib.study_page import build_gate_ladder

    ws = tmp_path / "ws"
    sd = ws / "studies" / "unrun-study"
    sd.mkdir(parents=True)
    sd_spec = {
        "name": "unrun-study",
        "embed_visualizations": [
            {"url": "/studies/unrun-study/viz/plot.html", "run_id": None},
        ],
        # deliberately no "runs:" -> has_runs is False -> gap_severity None
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(sd_spec), encoding="utf-8")

    spec = {
        "evaluation_status": "evaluated",
        "derived_status": {
            "evaluation_status": {"value": "evaluated", "source": "derive_status"},
        },
    }
    gates = build_gate_ladder(spec, ws_root=ws, slug="unrun-study")
    evidence = next(g for g in gates if g["key"] == "evidence")
    assert evidence["computed_state"] == "passed"
    assert evidence["viz_gap_reason"] is None
    assert evidence["diverges"] is False


# ---------------------------------------------------------------------------
# Fable G4: Tests -> Acceptance criteria band (§10.1 band 1). `spine_acceptance`
# is already attached to the spec by study_spec.load_study_detail_spec (via
# study_enrichment.study_acceptance_criterion, a pure read of the owning
# investigation's PERSISTED executive.computed_acceptance, filtered to this
# study's criteria) but was never rendered. This band is the first consumer.
# ---------------------------------------------------------------------------

def _write_g4_investigation_and_study(ws, inv_slug, study_slug, criteria, verdict_status):
    inv = ws / "investigations" / inv_slug
    sd = inv / "studies" / study_slug
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": inv_slug,
        "executive": {
            "verdict_status": "in-progress",
            "computed_acceptance": {
                "verdict_status": verdict_status,
                "diverges_from_authored": False,
                "criteria": criteria,
            },
        },
    }))
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": study_slug,
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
        "behavior_tests": [{"name": "oric_timing", "classification": "primary"}],
    }))


def test_acceptance_criteria_band_renders_with_spine_acceptance(tmp_path, dashboard_client):
    """A study with spine_acceptance criteria (via its owning investigation's
    persisted computed_acceptance) renders the Acceptance-criteria band at
    the top of the Tests tab: criterion behavior, an outcome from the G3
    four-value vocabulary, and a link to the test it rests on."""
    ws = tmp_path / "ws"
    _write_g4_investigation_and_study(
        ws, "g4-inv", "g4-study",
        criteria=[{"study": "g4-study", "behavior": "oric_timing", "result": "failing"}],
        verdict_status="failing",
    )

    client = dashboard_client(ws)
    resp = client.get("/studies/g4-study")
    assert resp.status_code == 200
    html = resp.text

    tests_panel = html[html.index('id="panel-tests"'):html.index('id="panel-audit"')]

    # The band renders, ABOVE the existing gate summary (the bar comes before
    # the outcome, per §10.1's ordering).
    assert 'id="acceptance-criteria-band"' in tests_panel
    band_i = tests_panel.index('id="acceptance-criteria-band"')
    gate_i = tests_panel.index('id="tests-gate-summary"')
    assert band_i < gate_i, "acceptance criteria band must render above the gate summary"

    # Criterion behavior name + outcome, mapped through the SAME G3 four-value
    # vocabulary ("failing" -> "not met", sourced from
    # viva_superpowers.investigation_status's criterion-result tokens).
    band = tests_panel[band_i:gate_i]
    assert "oric_timing" in band
    assert "not met" in band
    assert "✗" in band
    assert 'class="outcome-chip outcome-not-met"' in band

    # Links to the test it rests on, in the behavioral list below.
    assert 'href="#bt-oric_timing"' in band
    assert 'id="bt-oric_timing"' in tests_panel


def test_acceptance_criteria_band_absent_without_spine_acceptance(tmp_path, dashboard_client):
    """A study with no owning investigation (so no spine_acceptance) renders
    NO acceptance-criteria band at all — absent, not an empty box (§2 R2:
    absent != empty; never fabricate criteria)."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "g4-lonely-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "g4-lonely-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/g4-lonely-study")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="acceptance-criteria-band"' not in html
    assert 'acceptance-criteria-band' not in html


# ---------------------------------------------------------------------------
# Fable G5: Quality check group (rigor scorecard, §10.1 band 2 "Checks",
# automated group). Server-rendered mount only — the scorecard itself is
# fetched client-side from GET /api/study-rigor?study=<slug> (JS-rendered
# content is out of scope for a served-HTML assertion; see
# test_rigor_views_lib.py / test_api_app.py for the route + wrapper
# behavior). Study-spine reorg (spec §3.6/§3.7, plan Task 3): this mount
# MOVED from the Tests panel to the new Assurance › Audit panel.
# ---------------------------------------------------------------------------

def test_quality_check_group_mount_renders_in_audit_panel(tmp_path, dashboard_client):
    """The #check-group-quality mount lives in the Audit panel (not Tests),
    so client-side JS has somewhere to fill in the rigor scorecard --
    present regardless of whether rigor can compute."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "g5-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "g5-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/g5-study")
    assert resp.status_code == 200
    html = resp.text

    tests_panel = html[html.index('id="panel-tests"'):html.index('id="panel-audit"')]
    audit_panel = html[html.index('id="panel-audit"'):html.index('id="panel-build"')]

    assert 'id="check-group-quality"' not in tests_panel
    assert 'id="check-group-quality"' in audit_panel
    assert 'data-check-group="quality"' in audit_panel


def test_reproducibility_check_group_mount_renders_in_audit_panel(tmp_path, dashboard_client):
    """The #check-group-reproducibility mount (Fable G6) lives in the Audit
    panel, alongside #check-group-quality (G5) and the sufficiency group --
    present regardless of whether study_audit can compute."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "g6-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "g6-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/g6-study")
    assert resp.status_code == 200
    html = resp.text

    tests_panel = html[html.index('id="panel-tests"'):html.index('id="panel-audit"')]
    audit_panel = html[html.index('id="panel-audit"'):html.index('id="panel-build"')]

    assert 'id="check-group-reproducibility"' not in tests_panel
    assert 'id="check-group-reproducibility"' in audit_panel
    assert 'data-check-group="reproducibility"' in audit_panel

    # All three Checks-band groups (Sufficiency, Quality, Reproducibility)
    # sit together in the Audit panel.
    sufficiency_i = audit_panel.index('id="audit-sufficiency"')
    quality_i = audit_panel.index('id="check-group-quality"')
    repro_i = audit_panel.index('id="check-group-reproducibility"')
    assert sufficiency_i < quality_i < repro_i


# ---------------------------------------------------------------------------
# Study-spine reorg (spec §3.7/§3.8, plan Task 3): the Assurance trio is
# complete — Audit (sufficiency + rigor + reproducibility) and Build (loop
# provenance) are new pillars + panels alongside Tests.
# ---------------------------------------------------------------------------

def test_audit_and_build_pillars_under_assurance_act(tmp_path):
    from vivarium_workbench.lib.study_page import render_study_detail_html
    html = render_study_detail_html(tmp_path, "spine-study", {"name": "spine-study"})

    i = html.index('data-act="assurance"')
    j = html.index('data-act="decision"', i)
    assurance = html[i:j]
    assert 'data-kind="tests"' in assurance
    assert 'data-kind="audit"' in assurance
    assert 'data-kind="build"' in assurance
    # Order: Tests, Audit, Build.
    order = [assurance.index('data-kind="%s"' % k) for k in ("tests", "audit", "build")]
    assert order == sorted(order)


def test_panel_audit_and_panel_build_sections_exist(tmp_path):
    from vivarium_workbench.lib.study_page import render_study_detail_html
    html = render_study_detail_html(tmp_path, "spine-study", {"name": "spine-study"})

    assert 'data-kind="audit" id="panel-audit"' in html
    assert 'data-kind="build" id="panel-build"' in html
    assert 'id="audit-sufficiency"' in html
    assert 'id="build-loop-state"' in html


# ---------------------------------------------------------------------------
# REQUIREMENT (study-spine reorg, plan Task 3): the Tests panel must show
# the COMPLETE set of a study's report cards — every card under
# `viz/report_card/`, each with its verdict — even when moving rigor/repro
# out to Audit. A multi-card fixture proves none are dropped.
# ---------------------------------------------------------------------------

def test_tests_panel_renders_every_report_card_from_multi_card_fixture(tmp_path, dashboard_client):
    import json

    ws = tmp_path / "ws"
    sd = ws / "studies" / "multi-card-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "multi-card-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
        # Only ONE of the three cards below is wired to a behavior_tests
        # entry — the other two exist only as files under viz/report_card/.
        # The old per-row expander mechanism alone would drop them; the
        # complete-set section must still render all three.
        "behavior_tests": [
            {"name": "card-alpha-test", "kind": "report_card", "card": "card-alpha",
             "classification": "primary"},
        ],
    }))

    rc_dir = sd / "viz" / "report_card"
    rc_dir.mkdir(parents=True)
    for card in ("card-alpha", "card-beta", "card-gamma"):
        (rc_dir / f"{card}.html").write_text(
            f"<div>{card} rendered content</div>", encoding="utf-8")
        (rc_dir / f"{card}.verdict.json").write_text(json.dumps({
            "overall": "within_tol",
            "groups": {"g1": {"verdict": "within_tol", "axes": [
                {"id": "ax1", "label": f"{card} axis", "verdict": "within_tol", "meter": 0.9},
            ]}},
        }), encoding="utf-8")

    client = dashboard_client(ws)
    resp = client.get("/studies/multi-card-study")
    assert resp.status_code == 200
    html = resp.text

    tests_panel = html[html.index('id="panel-tests"'):html.index('id="panel-audit"')]
    # All three cards appear in the Tests panel, including the two with no
    # matching behavior_tests entry.
    for card in ("card-alpha", "card-beta", "card-gamma"):
        assert card in tests_panel, f"report card {card!r} missing from Tests panel"


# ---------------------------------------------------------------------------
# Study-spine reorg, Task 1 (spec §1, §3.2/3.3/3.4; plan Task 1): the
# `simulate` pillar moves Evidence -> Design; Results + Analyses panels are
# added to Evidence, ahead of Visualizations. See also
# tests/test_study_artifacts_on_simulate.py for the loader/markup-relocation
# assertions.
# ---------------------------------------------------------------------------

def _render_minimal(tmp_path, name="spine-study"):
    from vivarium_workbench.lib.study_page import render_study_detail_html
    return render_study_detail_html(tmp_path, name, {"name": name})


def test_simulate_pillar_is_under_design_act(tmp_path):
    html = _render_minimal(tmp_path)
    i = html.index('data-act="design"')
    j = html.index('data-act="evidence"', i)
    design = html[i:j]
    assert 'data-kind="simulate"' in design
    k = html.index('data-act="assurance"', j)
    evidence = html[j:k]
    assert 'data-kind="simulate"' not in evidence


def test_results_and_analyses_pillars_are_under_evidence_act(tmp_path):
    html = _render_minimal(tmp_path)
    i = html.index('data-act="evidence"')
    j = html.index('data-act="assurance"', i)
    evidence = html[i:j]
    assert 'data-kind="results"' in evidence
    assert 'data-kind="analyses"' in evidence
    assert 'data-kind="visualize"' in evidence, "Visualizations pillar must still be present"


def test_panel_results_and_panel_analyses_sections_exist(tmp_path):
    html = _render_minimal(tmp_path)
    assert 'class="study-tab-panel" data-kind="results" id="panel-results"' in html
    assert 'class="study-tab-panel" data-kind="analyses" id="panel-analyses"' in html
    assert 'id="panel-visualize"' in html, "Visualizations panel must still be present"


# ---------------------------------------------------------------------------
# Study-spine reorg, Task 4 (spec §3.3; plan Task 4): the Results panel gains
# a per-store PREVIEW of the study's latest run (sparkline + first/last/
# min/max, /api/study-results -> lib/results_views), upgrading it from
# downloads-only (Task 1). The existing raw-data download group is untouched.
# ---------------------------------------------------------------------------

def test_results_panel_has_latest_run_preview_mount(tmp_path):
    html = _render_minimal(tmp_path)
    results_panel = html[html.index('id="panel-results"'):html.index('id="panel-analyses"')]
    assert 'id="results-preview"' in results_panel
    # The preview mount is not a bare placeholder for downloads-only markup —
    # it's introduced by its own heading, distinct from the raw-data group.
    assert 'Latest run preview' in results_panel
    # Existing downloads group (Task 1) still present, unchanged element ids.
    assert 'id="raw-data-list"' in results_panel
    assert 'id="raw-data-download-all"' in results_panel
    assert 'id="exports-downloads"' in results_panel


def test_analyses_and_raw_data_markup_no_longer_inside_panel_simulate(tmp_path):
    html = _render_minimal(tmp_path)
    i = html.index('id="panel-simulate"')
    nxt = html.find('class="study-tab-panel"', i + 10)
    sim_panel = html[i: nxt if nxt != -1 else len(html)]
    # Study artifacts strip (analysis result files + raw simulation data)
    # relocated out; only the runs table + run-detail mount remain.
    assert 'id="data-files"' not in sim_panel
    assert 'id="raw-data-list"' not in sim_panel
    assert 'id="study-artifacts-section"' not in sim_panel
    assert 'id="study-sim-table"' in sim_panel
    assert 'id="study-run-detail"' in sim_panel


# ---------------------------------------------------------------------------
# Study-spine reorg, Task 6 (plan Task 6): the Model (compose) tab shows the
# study's ACTUAL composite(s) as the same rich card the Modules/Composites
# view uses (full semantic detail — config schema, declared observables — at
# the "Full" loom zoom level), not just a bare composite name + button.
# _loadModelCards() (static/study-detail.js) reads the data-model-composite /
# data-model-overrides attributes off the existing .cond-block / variant-row
# markup and renders via the shared static/composite-card.js renderer
# (_renderCompositeCardFull) client-side — these are server-render (markup
# wiring) assertions; the client-side fetch/render itself is covered by the
# composite-card.js `node --check` syntax gate plus manual verification (see
# the Task 6 report).
# ---------------------------------------------------------------------------

def test_compose_panel_has_model_composite_cards_mount(tmp_path):
    html = _render_minimal(tmp_path)
    compose = html[html.index('id="panel-compose"'):html.index('id="panel-simulate"')]
    assert 'id="model-composite-cards"' in compose
    assert 'class="model-composite-cards"' in compose
    assert 'data-study="spine-study"' in compose
    # Mount sits OUTSIDE the `_has_build` guard so a study with none of
    # model_change/implementation_requirements/conditions/baseline still
    # gets the mount (and _loadModelCards() renders its own empty note),
    # never a silently-missing panel.
    assert 'Nothing to compose yet.' in compose or 'model-composite-cards' in compose


def test_compose_panel_keeps_notebook_and_explore_actions(tmp_path):
    from vivarium_workbench.lib.study_page import render_study_detail_html

    spec = {
        "name": "model-cards-study",
        "schema_version": 3,
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
    }
    html = render_study_detail_html(tmp_path, "model-cards-study", spec)
    compose = html[html.index('id="panel-compose"'):html.index('id="panel-simulate"')]

    # The mount is present and carries this study's name.
    assert 'id="model-composite-cards"' in compose
    assert 'data-study="model-cards-study"' in compose

    # Existing actions are UNCHANGED (Task 6 keeps them, doesn't fork them):
    # the notebook download button and, per baseline entry, the "explore &
    # run" loom action.
    assert '_vivStudyNotebookFromCard' in compose
    assert '↓ notebook' in compose
    assert "_openCompositeLoom('pkg.composites.core')" in compose
    assert '🧬 explore &amp; run ↗' in compose

    # The per-entry block still carries the same data-model-composite /
    # data-model-overrides attributes _loadModelConfig already used —
    # _loadModelCards() reads the SAME attributes (no new data threading).
    assert 'data-model-composite="pkg.composites.core"' in compose


def test_compose_panel_variant_rows_carry_model_composite_attrs(tmp_path):
    from vivarium_workbench.lib.study_page import render_study_detail_html

    spec = {
        "name": "variant-cards-study",
        "schema_version": 3,
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "conditions": {
            "baseline": {"composite": "pkg.composites.core"},
            "variants": [
                {"name": "hot", "base_composite": "pkg.composites.core", "params": {"temp": 42}},
                {"name": "inherits", "params": {}},
            ],
        },
    }
    html = render_study_detail_html(tmp_path, "variant-cards-study", spec)
    compose = html[html.index('id="panel-compose"'):html.index('id="panel-simulate"')]

    # The "hot" variant declares its own base_composite -> carries the
    # attribute so _loadModelCards() picks it up alongside the baseline.
    assert 'data-model-composite="pkg.composites.core"' in compose
    assert 'data-model-overrides=\'{&#34;temp&#34;: 42}\'' in compose or '"temp": 42' in compose
    # The "inherits" variant declares no composite of its own -> an empty
    # attribute the JS skips (never a stray undefined-id fetch).
    assert 'data-model-composite=""' in compose
