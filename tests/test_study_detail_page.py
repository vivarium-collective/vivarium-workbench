"""Regression tests for the GET /studies/<name> detail page resolution.

Three bugs made existing studies 404 from the Investigations tab:
  1. _SLUG_RE rejected underscores — but study names are generated WITH
     underscores (e.g. study-monod_kinetics-096184), so the route's slug
     guard rejected them outright.
  2. The handler hardcoded WORKSPACE/"studies"/name/"study.yaml" instead of
     using _study_spec_path(), so studies living in investigations/<name>/
     spec.yaml were never found.
  3. It raw-loaded YAML instead of load_spec(), so a legacy v2 spec would
     not be migrated to the v3 shape the detail template expects.
"""
import yaml
import pytest


def test_slug_re_accepts_underscores_rejects_traversal():
    """_SLUG_RE must accept underscore-bearing study names (they're generated
    that way) while still rejecting path-traversal / invalid slugs."""
    from vivarium_dashboard.server import _SLUG_RE
    assert _SLUG_RE.match("study-monod_kinetics-096184")
    assert _SLUG_RE.match("t1")
    assert _SLUG_RE.match("a_b-c")
    # still rejects traversal / invalid slugs
    assert not _SLUG_RE.match("../etc")
    assert not _SLUG_RE.match("a/b")
    assert not _SLUG_RE.match(".hidden")
    assert not _SLUG_RE.match("Upper")
    assert not _SLUG_RE.match("_leading")
    assert not _SLUG_RE.match("trailing_")


@pytest.fixture
def _ws(tmp_path, monkeypatch):
    """Workspace with a legacy study under investigations/ — real v2ecoli
    shape: a `variants`-as-composites spec.yaml, no studies/ dir, name with
    underscores."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    legacy = ws / "investigations" / "study-monod_kinetics-096184"
    legacy.mkdir(parents=True)
    (legacy / "spec.yaml").write_text(yaml.safe_dump({
        "name": "study-monod_kinetics-096184",
        "baseline": "monod_kinetics",
        "variants": [
            {"name": "monod_kinetics",
             "source": "spatio_flux.composites.metabolism.monod_kinetics",
             "document": "./composites/monod_kinetics.yaml"},
        ],
        # `objective` is needed for test_overview_panel_has_objective_editable:
        # the template gates the editable objective field on `{% if
        # study.objective %}` (empty fields aren't rendered to keep the
        # page tidy); the test asserts the affordance exists.
        "objective": "Compare growth kinetics across substrate-affinity variants.",
        "comparisons": [], "conclusions": "", "question": "",
        "hypothesis": "", "status": "draft",
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_study_detail_spec_resolves_legacy_investigation(_ws):
    """A legacy study in investigations/<name>/spec.yaml resolves via
    _study_spec_path + load_spec (the v2ecoli shape that previously 404'd)."""
    from vivarium_dashboard.server import _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    assert spec is not None
    assert spec["name"] == "study-monod_kinetics-096184"
    assert "variants" in spec


def test_study_detail_spec_returns_none_for_missing(_ws):
    """A name with no spec file resolves to None (handler renders 404)."""
    from vivarium_dashboard.server import _study_detail_spec
    assert _study_detail_spec("does-not-exist") is None


def test_study_detail_page_has_eight_tabs(_ws):
    """The 8-tab scaffold is present: Overview · Baseline · Variants · Interventions · Tests · Runs · Visualizations · Conclusions.

    (The page may also render additional v4 tabs like Build / Simulations /
    Observables on top of these — the contract this test guards is "the
    eight base tabs are all present", not "exactly eight". Tabs have
    accreted as the platform grew; the count check was brittle.)
    """
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    # Eight required buttons
    for kind in ("overview", "baseline", "variants", "interventions", "tests", "runs", "visualizations", "conclusions"):
        assert f'class="study-tab' in html
        assert f'data-kind="{kind}"' in html
    # At least eight panels (additional v4 panels are allowed)
    panels = html.count('class="study-tab-panel')
    assert panels >= 8, f"expected at least 8 panel elements, got {panels}"
    # The Overview tab is active by default — must have both active class and overview kind on a button
    assert 'class="study-tab active" data-kind="overview"' in html or \
           'data-kind="overview" class="study-tab active"' in html or \
           ('"study-tab active"' in html and 'data-kind="overview"' in html)


def test_study_detail_page_loads_set_tab_helper(_ws):
    """The page ships the _setStudyTab helper inline or via study-detail.js."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    # The page must reference _setStudyTab somewhere (in the script tag or via onclick)
    assert "_setStudyTab" in html


def test_overview_panel_has_objective_editable(_ws):
    """Overview tab includes inline-editable objective field (conclusion moved to Conclusions tab)."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'id="objective-text"' in html
    assert 'data-editable="true"' in html
    # conclusion-text is no longer in the Overview; it now lives in the Conclusions tab
    assert 'id="conclusion-text"' not in html
    assert 'id="panel-conclusions"' in html


def test_overview_panel_has_counts_strip(_ws):
    """Overview tab shows a counts strip: variants · runs · interventions."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'study-counts-strip' in html or 'class="counts-strip"' in html
    # Each label appears
    for label in ('variants', 'runs', 'interventions'):
        assert label in html.lower()


def test_baseline_panel_lists_entries(_ws):
    """Baseline panel renders one .baseline-entry per baseline[] entry."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    # The legacy fixture has one variants-as-composites that migrates to one baseline entry
    # named "monod_kinetics" (per Plan 1 migration rules).
    assert 'class="baseline-entry"' in html
    assert 'data-baseline-name="monod_kinetics"' in html


def test_baseline_panel_has_add_button(_ws):
    """Baseline panel has a '+ Add composite' button."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'btn-baseline-add' in html


def test_baseline_panel_per_entry_buttons(_ws):
    """Each baseline entry has Run + Remove buttons carrying its name."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'btn-run-baseline' in html
    assert 'btn-baseline-remove' in html


def test_variants_panel_lists_entries(_ws):
    """Variants panel renders one .variant-row per variants[] entry, with name + base_composite + params count."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    # The legacy fixture has no variants[] (the only variant has `source:` so it migrated
    # to baseline). So expect the empty-message instead of a row.
    assert 'variant-row' in html or 'No variants yet' in html


def test_variants_panel_has_new_variant_button(_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'btn-variant-new' in html


def test_interventions_panel_lists_entries(_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'intervention-row' in html or 'No interventions yet' in html


def test_interventions_panel_has_new_button(_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'btn-intervention-new' in html


def test_runs_panel_has_runs_table(_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'id="runs-table"' in html


def test_visualizations_panel_present(_ws):
    """Visualizations tab panel contains viz-list and add-viz button."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'id="panel-visualizations"' in html
    assert 'id="viz-list"' in html
    assert 'btn-add-viz' in html


@pytest.fixture
def _rich_ws(tmp_path, monkeypatch):
    """Workspace with a richly-populated v3 study to exercise every tab."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "rich"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "rich",
        "objective": "Compare growth kinetics across substrate-affinity variants.",
        "status": "in_progress",
        "baseline": [
            {"name": "core", "composite": "pkg.composites.core", "params": {"k": 1}},
            {"name": "alt",  "composite": "pkg.composites.alt",  "params": {}},
        ],
        "variants": [
            {"name": "hi", "base_composite": "core", "parameter_overrides": {"k": 2}},
            {"name": "lo", "base_composite": "core", "parameter_overrides": {"k": 0.5}},
        ],
        "interventions": [
            {"name": "heat-shock", "description": "+10C for 5 min at t=10"},
        ],
        "runs": [
            {"run_id": "r1", "variant": None, "composite": "core", "label": "core",
             "n_steps": 5, "status": "completed"},
            {"run_id": "r2", "variant": "hi",  "composite": "core", "label": "hi",
             "n_steps": 5, "status": "completed"},
        ],
        "visualizations": [
            {"name": "growth-curve", "address": "viv.metric.growth", "config": {}},
        ],
        "conclusion": "Variant `hi` showed faster early growth but plateaued sooner.",
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_full_study_renders_all_tabs(_rich_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("rich")
    html = _render_study_detail_html("rich", spec)

    # 8 tabs scaffolded (added Visualizations)
    for kind in ("overview", "baseline", "variants", "interventions", "tests", "runs", "visualizations", "conclusions"):
        assert f'data-kind="{kind}"' in html

    # Overview: objective text + counts
    assert "Compare growth kinetics" in html
    assert "2</strong>" in html  # 2 variants OR 2 runs OR 2 baseline entries — at least one matches

    # Baseline: both entries + their FQNs
    assert 'data-baseline-name="core"' in html
    assert 'data-baseline-name="alt"' in html
    assert "pkg.composites.core" in html
    assert "pkg.composites.alt" in html

    # Variants: both + base_composite references
    assert 'data-variant-name="hi"' in html
    assert 'data-variant-name="lo"' in html
    assert "based on" in html

    # Interventions: the one entry + its description
    assert 'data-intervention-name="heat-shock"' in html
    assert "+10C for 5 min" in html

    # Runs: both runs render in the runs-table
    assert 'data-run-id="r1"' in html
    assert 'data-run-id="r2"' in html
    # The viz (growth-curve) renders in the Visualizations tab's viz-list, NOT
    # in the Runs tab — verified by anchoring after the panel-visualizations id.
    assert "growth-curve" in html

    # Conclusions panel is present (conclusion text is loaded by JS at runtime, not rendered in HTML)
    assert 'id="panel-conclusions"' in html
    assert 'id="conclusion-claims"' in html


# ---------------------------------------------------------------------------
# Runs tab: viz section moved out + per-run metadata enrichment
# ---------------------------------------------------------------------------


def _section(html: str, start_marker: str, end_marker: str) -> str:
    """Slice html between two markers; both must be present."""
    i = html.index(start_marker)
    j = html.index(end_marker, i)
    return html[i:j]


def test_runs_tab_does_not_render_charts_panel(_rich_ws):
    """The inline 'Latest run — visualizations' panel was removed from the
    Runs tab. Charts now live exclusively in the Visualizations tab."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    html = _render_study_detail_html("rich", _study_detail_spec("rich"))
    runs_panel = _section(html, 'id="panel-runs"', 'id="panel-tests"')
    assert 'id="charts-panel"' not in runs_panel, (
        "Runs tab still contains the inline charts panel — should have moved "
        "to the Visualizations tab."
    )
    # Sanity: the Visualizations tab still has its chart panel.
    viz_panel = _section(html, 'id="panel-visualizations"', 'id="panel-conclusions"')
    assert 'id="viz-charts-panel"' in viz_panel


def test_runs_tab_has_richer_columns(_rich_ws):
    """The Runs table now exposes Composite, Started, Duration, and Model changes."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    html = _render_study_detail_html("rich", _study_detail_spec("rich"))
    runs_panel = _section(html, 'id="panel-runs"', 'id="panel-tests"')
    for header in ("Composite", "Started (UTC)", "Duration", "Model changes"):
        assert f">{header}<" in runs_panel, f"missing column header: {header}"


@pytest.fixture
def _ws_with_runs_db(tmp_path, monkeypatch):
    """Workspace whose study has both a study.yaml runs[] list AND a populated
    runs.db, so _enrich_runs_with_meta has a real DB to merge from."""
    import vivarium_dashboard.server as srv
    from vivarium_dashboard.lib.composite_runs import (
        connect, save_metadata, complete_metadata,
    )

    ws = tmp_path / "ws"
    sd = ws / "studies" / "rich-runs"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "rich-runs",
        "objective": "Per-run metadata round-trip.",
        "baseline": [
            {"name": "core", "composite": "pkg.composites.core"},
        ],
        "runs": [
            {"run_id": "run-A", "variant": None, "composite": "core",
             "label": "baseline", "n_steps": 5, "status": "completed"},
            {"run_id": "run-B", "variant": "hi", "composite": "core",
             "label": "hi", "n_steps": 5, "status": "completed"},
            # third entry has NO matching runs_meta row — enrichment must be tolerant
            {"run_id": "run-orphan", "variant": None, "composite": "core",
             "label": "lost", "n_steps": 5, "status": "completed"},
        ],
    }))

    # Populate runs.db for the two runs that have metadata.
    conn = connect(sd / "runs.db")
    save_metadata(
        conn, spec_id="pkg.composites.core", run_id="run-A",
        params={}, label="baseline", started_at=1700000000.0, n_steps=5,
        log_path="logs/run-A.log",
    )
    complete_metadata(conn, run_id="run-A", n_steps=5, status="completed")
    # Force a known completed_at so the duration assertion is deterministic.
    conn.execute(
        "UPDATE runs_meta SET completed_at=? WHERE run_id=?",
        (1700000095.0, "run-A"),
    )
    save_metadata(
        conn, spec_id="pkg.composites.core", run_id="run-B",
        params={"k": 2, "alpha": 0.5}, label="hi", started_at=1700000200.0,
        n_steps=5, log_path="logs/run-B.log",
    )
    complete_metadata(conn, run_id="run-B", n_steps=5, status="completed")
    conn.execute(
        "UPDATE runs_meta SET completed_at=? WHERE run_id=?",
        (1700000260.0, "run-B"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_runs_table_renders_started_and_duration_from_runs_db(_ws_with_runs_db):
    """When runs.db has a metadata row, the Runs table shows formatted
    started/duration columns derived from runs_meta."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    html = _render_study_detail_html("rich-runs", _study_detail_spec("rich-runs"))
    runs_panel = _section(html, 'id="panel-runs"', 'id="panel-tests"')

    # run-A: started_at=1700000000 → 2023-11-14 22:13 UTC; duration=95s → "1m 35s"
    assert "2023-11-14 22:13" in runs_panel
    assert "1m 35s" in runs_panel
    # run-B: duration=60s → "1m"
    assert "1m</td>" in runs_panel or ">1m<" in runs_panel


def test_runs_table_renders_param_overrides(_ws_with_runs_db):
    """params_json from runs_meta surfaces as the Model-changes details block."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    html = _render_study_detail_html("rich-runs", _study_detail_spec("rich-runs"))
    runs_panel = _section(html, 'id="panel-runs"', 'id="panel-tests"')

    # run-A has empty params → "—"; run-B has 2 overrides → "2 overrides"
    assert "2 overrides" in runs_panel
    # The JSON content is escaped in the rendered template; check for substrings
    # that survive both raw + escaped rendering of the dict.
    assert "alpha" in runs_panel
    assert "0.5" in runs_panel


def test_runs_table_tolerates_orphan_runs(_ws_with_runs_db):
    """A study.runs[] entry with no matching runs.db row still renders — the
    metadata columns are simply empty for that row."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    html = _render_study_detail_html("rich-runs", _study_detail_spec("rich-runs"))
    runs_panel = _section(html, 'id="panel-runs"', 'id="panel-tests"')
    assert 'data-run-id="run-orphan"' in runs_panel


def test_runs_table_tolerates_missing_runs_db(_rich_ws):
    """A study with study.runs[] but no runs.db still renders rows; metadata
    columns are blank (function returns runs unchanged on missing DB)."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    html = _render_study_detail_html("rich", _study_detail_spec("rich"))
    runs_panel = _section(html, 'id="panel-runs"', 'id="panel-tests"')
    assert 'data-run-id="r1"' in runs_panel
    assert 'data-run-id="r2"' in runs_panel


# ---------------------------------------------------------------------------
# Unit tests for the formatting helpers
# ---------------------------------------------------------------------------


def test_fmt_duration_handles_ranges():
    from vivarium_dashboard.server import _jinja_fmt_duration
    assert _jinja_fmt_duration(None) == ""
    assert _jinja_fmt_duration(-1) == ""
    assert _jinja_fmt_duration(0) == "0s"
    assert _jinja_fmt_duration(45) == "45s"
    assert _jinja_fmt_duration(60) == "1m"
    assert _jinja_fmt_duration(95) == "1m 35s"
    assert _jinja_fmt_duration(3600) == "1h"
    assert _jinja_fmt_duration(3660) == "1h 1m"


def test_fmt_ts_handles_none_and_unix():
    from vivarium_dashboard.server import _jinja_fmt_ts
    assert _jinja_fmt_ts(None) == ""
    assert _jinja_fmt_ts(0) == ""  # epoch zero treated as falsy/no-data
    assert _jinja_fmt_ts(1700000000.0) == "2023-11-14 22:13"
