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
    """The header gate pill + readiness dropdown are gone; the status row now
    carries the two-axis Progress × Verdict markers (#study-axis-markers, filled
    client-side). Readiness moved into the Audit tab. Assert the server-side
    facts: the markers mount exists, the old `#readiness-panel` /
    `#header-audit-summary` mounts are gone, and no `#spine-summary` remains.
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

    assert 'id="study-axis-markers"' in html
    assert 'id="header-audit-summary"' not in html   # gate/readiness removed from header
    assert 'id="readiness-panel"' not in html        # old dropdown mount removed
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
    """6-tab layout: the Visualizations tab holds the figure gallery as a
    "Figures" section. It stays one gallery with no explanatory prose and no
    per-mount section chrome, just the three mounts (native gallery, embedded-viz
    iframes, latest-run charts) under the preserved `study-figures` /
    `#visualize-section` wrapper."""
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

    # The Visualizations tab/panel holds the gallery (its #visualize-section
    # wrapper is preserved); it is NOT in the Overview panel.
    assert 'id="panel-visualize"' in html
    assert 'data-kind="visualize"' in html
    visualize = html[html.index('id="panel-visualize"'):html.index('id="panel-compose"')]
    assert 'id="visualize-section"' in visualize
    assert 'study-figures' in visualize
    assert 'id="native-gallery-panel"' in visualize
    assert 'id="viz-charts-panel"' in visualize
    overview = html[html.index('id="panel-overview"'):html.index('id="panel-visualize"')]
    assert 'id="visualize-section"' not in overview

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
    # 5-tab layout (Phase 1): Tests is no longer its own tab — its content is
    # the FIRST section inside the Audit panel ("Tests — the bar"). Scope to the
    # Audit panel; the report-cards mount + "The bar" framing live there now.
    assert 'id="panel-tests"' not in html
    assert 'data-kind="tests"' not in html
    tests_panel = html[html.index('id="panel-audit"'):html.index('id="panel-conclusions"')]
    assert 'id="report-cards-panel"' in tests_panel
    assert '<h3>Tests — the bar</h3>' in tests_panel
    # The redundant "<h2>Tests</h2>" heading was removed (the tab bar names it);
    # the useful "The bar" subtitle stays as the top-of-section framing.
    assert '<h2>Tests</h2>' not in tests_panel
    assert 'The bar' in tests_panel


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

    # 5-tab layout: Tests content is the first section of the Audit panel now.
    tests_panel = html[html.index('id="panel-audit"'):html.index('id="panel-conclusions"')]

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

    # 6-tab study layout: exactly SIX pillars — Overview, Visualizations
    # (visualize), Model (compose), Simulations (simulate), Audit, Decide
    # (conclusions) — each carrying data-kind and calling _setStudyTab(kind)
    # directly. The folded-away kinds (readouts/results/analyses/tests/build) are
    # no longer pillars (Visualizations is a pillar again).
    six_kinds = ["overview", "visualize", "compose", "simulate", "audit", "conclusions"]
    for kind in six_kinds:
        assert f'data-kind="{kind}"' in html and f"_setStudyTab('{kind}')" in html, \
            f"pillar kind {kind!r} not wired to _setStudyTab('{kind}')"
    for gone in ("readouts", "results", "analyses", "tests", "build"):
        assert f"_setStudyTab('{gone}')" not in html, f"stale pillar for {gone!r}"
    import re
    assert len(re.findall(r'<button class="study-pillar[^"]*"', html)) == 6
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
    """5-tab study layout (mockup refinement): the per-act group-label headers
    (THE STUDY / DESIGN / EVIDENCE / ASSURANCE / DECISION) and their gate dots
    were removed — with only five tabs they were clutter. The nav is now ONE
    centered row (`study-tabs study-tabs-row`) of five icon+label pillars; no
    `.act-cluster` / `.act-label` / `.act-gate-dot` markup remains."""
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

    # Single clean row — no act clusters, no group labels, no gate dots.
    assert 'class="act-cluster' not in nav
    assert 'act-gate-dot' not in nav
    assert 'class="act-label"' not in nav
    for label in ("The Study", "Design", "Evidence", "Assurance", "Decision"):
        assert label not in nav, f"group label {label!r} should be removed"

    # Six icon+label pillars, in order, each with an inline-SVG icon.
    import re
    kinds = re.findall(r'<button class="study-pillar[^"]*" data-kind="([a-z]+)"', nav)
    assert kinds == ["overview", "visualize", "compose", "simulate", "audit", "conclusions"], kinds
    assert nav.count('class="pillar-icon"') == 6
    assert nav.count('<svg') == 6
    assert nav.count('class="pillar-label"') == 6


def test_act_clusters_group_label_with_own_tabs(tmp_path, dashboard_client):
    """5-tab study layout (mockup refinement): the `.act-cluster` grouping was
    dropped — each pillar is a direct child of the single `study-tabs-row` nav
    and pairs its own inline-SVG icon with its own label. This asserts the
    icon↔label pairing per pillar and that no cluster/group-label markup
    remains."""
    import re

    # NB: the fixture slug deliberately avoids the substring "act-cluster" — the
    # study-tabs nav now hosts the ↓ Export menu, which embeds the study slug, so
    # a slug containing "act-cluster" would false-trip the grouping-absence asserts
    # below.
    ws = tmp_path / "ws"
    sd = ws / "studies" / "pillar-nav-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "pillar-nav-study",
        "kind": "biological",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "purpose": {"question": "Does the demo composite run correctly?"},
        "status": "in_progress",
    }))

    client = dashboard_client(ws)
    resp = client.get("/studies/pillar-nav-study")
    assert resp.status_code == 200
    html = resp.text

    nav_i = html.index('<nav class="study-tabs"')
    nav_end = html.index('</nav>', nav_i)
    nav = html[nav_i:nav_end]

    # No act-cluster grouping / group labels / gate dots.
    assert 'act-cluster' not in nav
    assert 'act-label' not in nav
    assert 'act-gate-dot' not in nav

    # Each pillar = one <button> with an inline <svg class="pillar-icon"> and a
    # <span class="pillar-label">LABEL</span>, in order.
    expected = [
        ("overview", "Overview"),
        ("visualize", "Visualizations"),
        ("compose", "Model"),
        ("simulate", "Simulations"),
        ("audit", "Audit"),
        ("conclusions", "Decide"),
    ]
    btns = re.findall(
        r'<button class="study-pillar[^"]*" data-kind="([a-z]+)"[^>]*>(.*?)</button>',
        nav, re.DOTALL)
    assert [b[0] for b in btns] == [k for k, _ in expected], [b[0] for b in btns]
    for (kind, label), (got_kind, body) in zip(expected, btns):
        assert 'class="pillar-icon"' in body and '<svg' in body, f"{kind} missing icon"
        assert f'<span class="pillar-label">{label}</span>' in body, f"{kind} label != {label!r}"
    assert 'data-kind="data"' not in nav


def test_readouts_repositioned_to_slot_three(tmp_path, dashboard_client):
    """5-tab study layout (Phase 1): the pillar row is exactly Overview, Model,
    Simulations, Audit, Decide — in that order. Readouts is no longer its own
    pillar (it folds into Model); panel switching is unaffected
    (`_setStudyTab` selects by `data-kind`)."""
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

    kinds_in_order = ["overview", "visualize", "compose", "simulate", "audit", "conclusions"]
    positions = [html.index(f'data-kind="{k}"') for k in kinds_in_order]
    assert positions == sorted(positions), (
        f"expected pillar order {kinds_in_order}, got positions {positions}"
    )
    for kind in kinds_in_order:
        assert f'data-kind="{kind}"' in html and f"_setStudyTab('{kind}')" in html
    # Readouts is folded into Model — no readouts pillar any more.
    assert "_setStudyTab('readouts')" not in html
    assert 'data-kind="data"' not in html


def test_readouts_panel_renders_three_contract_blocks(tmp_path, dashboard_client):
    """5-tab study layout (Phase 1): the Readouts contract (emitter summary +
    emitted-paths table) is re-homed into the Model (compose) panel under a
    "Readouts" sub-section, with its element ids preserved. The old "Outputs &
    shapes" block stays removed."""
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

    # No standalone Readouts panel; the mounts live inside the Model panel.
    assert 'id="panel-readouts"' not in html
    panel = html[html.index('id="panel-compose"'):html.index('id="panel-simulate"')]
    assert '<h3>Readouts</h3>' in panel
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


# 5-tab layout: the six-gate `status ▾` ladder and the act-rail gate dots were
# both removed from the UI (original spec §6 drops the header ladder; the mockup
# refinement drops the per-act group-label dots). The server-side gate
# computation (`build_gate_ladder` / `act_gate_states`) is unchanged and still
# feeds other consumers, so these tests now exercise it DIRECTLY against the
# loaded+enriched spec rather than scraping removed markup.

def _g2_gates(ws, slug):
    from vivarium_workbench.lib._root import set_workspace_root
    from vivarium_workbench.lib.study_spec import load_study_detail_spec
    from vivarium_workbench.lib.study_page import build_gate_ladder
    set_workspace_root(ws)
    spec = load_study_detail_spec(ws, slug)
    assert spec is not None, f"spec for {slug} failed to load"
    return build_gate_ladder(spec, ws_root=ws, slug=slug)


def test_status_ladder_shows_six_gate_names(tmp_path):
    """build_gate_ladder relabels the six axes as the six gates (§13's table):
    Plan / Execution / Evidence / Quality / Decision / Release."""
    ws = tmp_path / "ws"
    _write_g2_study(ws, "gate-names-study", {"design_status": "designed"})
    names = [g["name"] for g in _g2_gates(ws, "gate-names-study")]
    for gate_name in ("Plan", "Execution", "Evidence", "Quality", "Decision", "Release"):
        assert gate_name in names, f"gate name {gate_name!r} missing from gate ladder"


def test_status_ladder_shows_authored_axis_state(tmp_path):
    """A study with an authored axis (design_status) carries that gate's
    (Plan's) authored value + passed state in the ladder."""
    ws = tmp_path / "ws"
    _write_g2_study(ws, "authored-axis-study", {"design_status": "designed"})
    gates = _g2_gates(ws, "authored-axis-study")
    plan = next(g for g in gates if g["name"] == "Plan")
    assert plan["authored_value"] == "designed"
    assert plan["authored_state"] == "passed"


def test_status_ladder_shows_computed_vs_authored_indicator(tmp_path):
    """Where a computed value exists for a gate (Execution, sourced from
    viva_superpowers.study_status.derive_status, attached to the spec as
    `derived_status` by load_study_detail_spec) the ladder carries a computed
    value for that gate."""
    ws = tmp_path / "ws"
    _write_g2_study(ws, "computed-axis-study", {
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed",
                   "outcomes": {"t1": {"result": "PASS"}}}],
    })
    gates = _g2_gates(ws, "computed-axis-study")
    execution = next(g for g in gates if g["name"] == "Execution")
    assert execution["computed_value"] is not None, execution
    assert execution["computed_source"], execution


def test_act_rail_dots_reflect_gate_states(tmp_path):
    """act_gate_states does NOT roll every act up as not-assessed for a study
    with real gate data — it reads the computed gate ladder."""
    from vivarium_workbench.lib.study_page import act_gate_states

    ws = tmp_path / "ws"
    _write_g2_study(ws, "act-dots-study", {
        "design_status": "designed",
        "gate_status": "passed",
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed",
                   "outcomes": {"t1": {"result": "FAIL"}}}],
    })
    states = act_gate_states(_g2_gates(ws, "act-dots-study"))
    assert set(states) == {"study", "design", "evidence", "assurance", "decision"}
    # design_status: designed -> Plan passes -> the "design" act is "passed".
    assert states["design"] == "passed"
    assert set(states.values()) != {"not-assessed"}


def test_act_rail_dots_reflect_computed_only_passed_state(tmp_path):
    """Fix round 1 (Fable G2): a study with NO hand-authored axes — only a
    derived/computed path (completed run + PASS outcome +
    `computed_gate_verdict.result == "passed"`) — must NOT collapse its act
    states to grey `not-assessed`; an empty authored axis defers to a real
    computed `passed`. Asserted directly against act_gate_states."""
    from vivarium_workbench.lib.study_page import act_gate_states

    ws = tmp_path / "ws"
    _write_g2_study(ws, "computed-passed-study", {
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed",
                   "outcomes": {"t1": {"result": "PASS"}}}],
        "findings": [{"statement": "the run behaved as expected"}],
        # Qualifying figure so Evidence's viz-gap signal stays out of scope.
        "embed_visualizations": [{"url": "/studies/computed-passed-study/viz/plot.html",
                                    "run_id": "r1"}],
    })
    states = act_gate_states(_g2_gates(ws, "computed-passed-study"))
    assert states["evidence"] == "passed", states
    assert states["assurance"] == "passed", states
    assert states["decision"] == "passed", states
    # Plan has no computed source and no authored value -> stays not-assessed.
    assert states["design"] == "not-assessed", states


# ---------------------------------------------------------------------------
# Task V5 — fold V4's visualization-readiness signal into Evidence's
# computed state (§5(B), §6(c.3)). Reuses the existing gate-state vocabulary
# (`passed-with-conditions` is the gap/conditional tier — NOT a new token,
# never `blocked`) and the existing worst-of rollup.
# ---------------------------------------------------------------------------

def test_visualization_gap_downgrades_evidence_gate_and_act_dot(tmp_path):
    """5-tab layout: the six-gate ladder + act-rail dots were removed from the
    UI (original spec §6 drops the header ladder; the mockup refinement drops the
    group-label gate dots), so the downgrade is asserted directly against the
    still-live `build_gate_ladder` / `act_gate_states` logic instead of rendered
    markup. A study that fails the viz bar (no figures) has its Evidence gate's
    COMPUTED state downgraded from `passed` to the `passed-with-conditions` gap
    tier (never a hard blocked/failed), diverging from the authored `evaluated`,
    and the rolled-up "evidence" act state reflects it."""
    from vivarium_workbench.lib.study_page import build_gate_ladder, act_gate_states

    ws = tmp_path / "ws"
    sd = ws / "studies" / "viz-gap-study"
    sd.mkdir(parents=True)
    # Completed run but NO visualizations -> study_visualization_status returns
    # qualifies=False, reason="no figures", gap_severity="warning".
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "viz-gap-study",
        "runs": [{"name": "r1", "status": "completed"}],
    }), encoding="utf-8")

    spec = {
        "evaluation_status": "evaluated",
        "derived_status": {
            "evaluation_status": {"value": "evaluated", "source": "derive_status"},
        },
    }
    gates = build_gate_ladder(spec, ws_root=ws, slug="viz-gap-study")
    evidence = next(g for g in gates if g["key"] == "evidence")
    assert evidence["computed_state"] == "passed-with-conditions", evidence
    assert evidence["state"] != "blocked", evidence
    assert evidence["diverges"] is True, evidence
    assert evidence["viz_gap_reason"] == "no figures", evidence
    assert act_gate_states(gates)["evidence"] == "passed-with-conditions"


def test_visualization_gap_qualifying_study_not_downgraded(tmp_path):
    """A study that PASSES the viz bar (an interactive, run-linked figure) is
    not touched: Evidence's computed state stays `passed`, no divergence, no gap
    note. Asserted directly against `build_gate_ladder` (the ladder/dot UI is
    gone)."""
    from vivarium_workbench.lib.study_page import build_gate_ladder

    ws = tmp_path / "ws"
    sd = ws / "studies" / "viz-pass-study"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "viz-pass-study",
        "runs": [{"name": "r1", "status": "completed"}],
        "embed_visualizations": [{"url": "/studies/viz-pass-study/viz/plot.html",
                                   "run_id": "r1"}],
    }), encoding="utf-8")

    spec = {
        "evaluation_status": "evaluated",
        "derived_status": {
            "evaluation_status": {"value": "evaluated", "source": "derive_status"},
        },
    }
    gates = build_gate_ladder(spec, ws_root=ws, slug="viz-pass-study")
    evidence = next(g for g in gates if g["key"] == "evidence")
    assert evidence["computed_state"] == "passed", evidence
    assert evidence["viz_gap_reason"] is None, evidence
    assert evidence["diverges"] is False, evidence


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

    # 5-tab layout: Tests content (incl. the acceptance band) is the first
    # section of the Audit panel now.
    tests_panel = html[html.index('id="panel-audit"'):html.index('id="panel-conclusions"')]

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

    # 5-tab layout: the Audit panel now starts with the folded-in Tests section,
    # then the audit intro ("Is the bar sound?") + its check groups.
    audit_panel = html[html.index('id="panel-audit"'):html.index('id="panel-conclusions"')]
    tests_section = audit_panel[:audit_panel.index('Is the bar sound?')]

    # Quality mount is NOT in the Tests section — it lives in the audit body.
    assert 'id="check-group-quality"' not in tests_section
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

    # 5-tab layout: Audit panel = folded Tests section + audit body.
    audit_panel = html[html.index('id="panel-audit"'):html.index('id="panel-conclusions"')]
    tests_section = audit_panel[:audit_panel.index('Is the bar sound?')]

    assert 'id="check-group-reproducibility"' not in tests_section
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

def test_audit_is_the_assurance_pillar(tmp_path):
    """5-tab layout: Assurance collapses to a single Audit pillar; Tests folds
    into Audit (first section) and Build folds into Model (Advanced). There are
    no standalone Tests/Build pillars any more."""
    from vivarium_workbench.lib.study_page import render_study_detail_html
    html = render_study_detail_html(tmp_path, "spine-study", {"name": "spine-study"})

    assert 'data-kind="audit"' in html and "_setStudyTab('audit')" in html
    assert "_setStudyTab('tests')" not in html
    assert "_setStudyTab('build')" not in html


def test_panel_audit_folds_tests_and_build_folds_into_model(tmp_path):
    """5-tab layout: Audit panel exists (with Tests folded in as its first
    section); the Build panel is gone and its #build-loop-state mount now lives
    inside the Model (compose) panel's Advanced section."""
    from vivarium_workbench.lib.study_page import render_study_detail_html
    html = render_study_detail_html(tmp_path, "spine-study", {"name": "spine-study"})

    assert 'data-kind="audit" id="panel-audit"' in html
    assert 'id="panel-build"' not in html
    assert 'id="audit-sufficiency"' in html
    # Tests folded into Audit (first section).
    audit_panel = html[html.index('id="panel-audit"'):html.index('id="panel-conclusions"')]
    assert '<h3>Tests — the bar</h3>' in audit_panel
    assert 'id="report-cards-panel"' in audit_panel or 'id="tests-list"' in audit_panel
    # Build provenance folded into Model.
    compose = html[html.index('id="panel-compose"'):html.index('id="panel-simulate"')]
    assert 'id="build-loop-state"' in compose
    assert 'model-advanced' in compose


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

    # 5-tab layout: the Tests content is the first section of the Audit panel.
    tests_panel = html[html.index('id="panel-audit"'):html.index('id="panel-conclusions"')]
    # All three cards appear in the Tests section, including the two with no
    # matching behavior_tests entry.
    for card in ("card-alpha", "card-beta", "card-gamma"):
        assert card in tests_panel, f"report card {card!r} missing from Tests section"


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


def test_simulate_is_a_top_level_pillar(tmp_path):
    """5-tab layout: Simulations is one of the five top-level pillars (no act
    clusters any more)."""
    html = _render_minimal(tmp_path)
    assert 'data-kind="simulate"' in html and "_setStudyTab('simulate')" in html


def test_results_and_analyses_are_simulations_subtabs(tmp_path):
    """6-tab layout: Results + Analyses are no longer top-level pillars — they
    are secondary subtabs (Runs | Results | Analysis) inside the Simulations
    panel. Visualizations is its own top-level pillar again."""
    html = _render_minimal(tmp_path)
    for gone in ("results", "analyses"):
        assert f"_setStudyTab('{gone}')" not in html
    assert "_setStudyTab('visualize')" in html
    sim_panel = html[html.index('id="panel-simulate"'):html.index('id="panel-audit"')]
    assert "_setSimSubtab('runs')" in sim_panel
    assert "_setSimSubtab('results')" in sim_panel
    assert "_setSimSubtab('analysis')" in sim_panel


def test_results_and_analyses_subpanels_exist(tmp_path):
    """6-tab layout: the Results + Analyses content lives in inner subpanels of
    the Simulations panel (ids preserved); there are no standalone
    panel-results / panel-analyses sections. Visualizations is its own panel."""
    html = _render_minimal(tmp_path)
    assert 'id="panel-results"' not in html
    assert 'id="panel-analyses"' not in html
    assert 'id="panel-visualize"' in html
    sim_panel = html[html.index('id="panel-simulate"'):html.index('id="panel-audit"')]
    assert 'id="sim-subpanel-runs"' in sim_panel
    assert 'id="sim-subpanel-results"' in sim_panel
    assert 'id="sim-subpanel-analysis"' in sim_panel
    assert 'id="results-section"' in sim_panel
    assert 'id="analyses-results-section"' in sim_panel
    # Visualizations gallery lives in its own panel.
    visualize = html[html.index('id="panel-visualize"'):html.index('id="panel-compose"')]
    assert 'id="visualize-section"' in visualize


# ---------------------------------------------------------------------------
# Study-spine reorg, Task 4 (spec §3.3; plan Task 4): the Results panel gains
# a per-store PREVIEW of the study's latest run (sparkline + first/last/
# min/max, /api/study-results -> lib/results_views), upgrading it from
# downloads-only (Task 1). The existing raw-data download group is untouched.
# ---------------------------------------------------------------------------

def test_results_subpanel_has_latest_run_preview_mount(tmp_path):
    """5-tab layout: the Results content (latest-run preview + raw-data group)
    lives in the Simulations panel's Results subpanel, with its element ids
    preserved."""
    html = _render_minimal(tmp_path)
    sim_panel = html[html.index('id="panel-simulate"'):html.index('id="panel-audit"')]
    results_sub = sim_panel[sim_panel.index('id="sim-subpanel-results"'):
                            sim_panel.index('id="sim-subpanel-analysis"')]
    assert 'id="results-preview"' in results_sub
    assert 'Latest run preview' in results_sub
    # Existing downloads group still present, unchanged element ids.
    assert 'id="raw-data-list"' in results_sub
    assert 'id="raw-data-download-all"' in results_sub
    assert 'id="exports-downloads"' in results_sub


def test_runs_results_analysis_split_across_sim_subpanels(tmp_path):
    """5-tab layout: inside the Simulations panel the runs table lives in the
    Runs subpanel while the Results (raw data) and Analysis (result files)
    markup live in their own subpanels — all ids preserved."""
    html = _render_minimal(tmp_path)
    sim_panel = html[html.index('id="panel-simulate"'):html.index('id="panel-audit"')]
    runs_sub = sim_panel[sim_panel.index('id="sim-subpanel-runs"'):
                         sim_panel.index('id="sim-subpanel-results"')]
    assert 'id="study-sim-table"' in runs_sub
    assert 'id="study-run-detail"' in runs_sub
    # Raw data / analysis result files are present in the panel (in their own
    # subpanels), not the Runs subpanel.
    assert 'id="raw-data-list"' not in runs_sub
    assert 'id="data-files"' not in runs_sub
    assert 'id="data-files"' in sim_panel
    assert 'id="raw-data-list"' in sim_panel


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
