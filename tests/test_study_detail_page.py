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


def test_study_detail_page_has_five_tabs(_ws):
    """The 5-tab scaffold is present: Overview · Baseline · Variants · Interventions · Runs."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    # Five buttons
    for kind in ("overview", "baseline", "variants", "interventions", "runs"):
        assert f'class="study-tab' in html
        assert f'data-kind="{kind}"' in html
    # Five panels
    panels = html.count('class="study-tab-panel')
    assert panels == 5, f"expected 5 panel elements, got {panels}"
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


def test_overview_panel_has_objective_and_conclusion_editables(_ws):
    """Overview tab includes inline-editable objective and conclusion fields."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'id="objective-text"' in html
    assert 'id="conclusion-text"' in html
    assert 'data-editable="true"' in html


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


def test_runs_panel_includes_visualizations(_ws):
    """Runs panel folds in the visualizations section."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
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

    # 5 tabs scaffolded
    for kind in ("overview", "baseline", "variants", "interventions", "runs"):
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

    # Runs: both runs + viz section
    assert 'data-run-id="r1"' in html
    assert 'data-run-id="r2"' in html
    assert "growth-curve" in html

    # Conclusion text rendered
    assert "Variant `hi` showed faster early growth" in html
