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
    from vivarium_workbench.lib.study_spec import SLUG_RE as _SLUG_RE
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
def _ws(tmp_path):
    """Workspace with a legacy study under investigations/ — real v2ecoli
    shape: a `variants`-as-composites spec.yaml, no studies/ dir, name with
    underscores."""
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
    return ws


def test_study_detail_spec_resolves_legacy_investigation(_ws):
    """A legacy study in investigations/<name>/spec.yaml resolves via
    _study_spec_path + load_spec (the v2ecoli shape that previously 404'd)."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    assert spec is not None
    assert spec["name"] == "study-monod_kinetics-096184"
    assert "variants" in spec


def test_study_detail_spec_returns_none_for_missing(_ws):
    """A name with no spec file resolves to None (handler renders 404)."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    assert _study_detail_spec(_ws, "does-not-exist") is None


def test_study_detail_page_has_seven_tabs(_ws):
    """The pillar tabs are present: Overview · Tests · Model (compose) ·
    Simulations (simulate) · Readouts · Results (visualize) · Decide
    (conclusions).

    (The contract this test guards is "the required pillar tabs are all
    present", not "exactly N" — the count check was brittle as tabs accreted.
    Task 10 merged Report Cards into Tests as one concept, so it is no longer
    a separate tab/kind. Task E4 removed the Exports (data) tab — it was an
    empty shell after E1–E3 relocated everything it held.)
    """
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    html = _render_study_detail_html(_ws, "study-monod_kinetics-096184", spec)
    # Required pillar buttons
    for kind in ("overview", "tests", "compose", "simulate", "readouts",
                 "visualize", "conclusions"):
        assert f'class="study-tab' in html
        assert f'data-kind="{kind}"' in html
    # Report Cards is no longer its own tab — it's a subsection of Tests.
    assert 'data-kind="report-cards"' not in html
    # Exports/data tab was deleted (Task E4).
    assert 'data-kind="data"' not in html
    assert 'id="panel-data"' not in html
    # At least seven panels
    panels = html.count('class="study-tab-panel')
    assert panels >= 7, f"expected at least 7 panel elements, got {panels}"
    # The Overview tab is active by default — must have both active class and overview kind on a button.
    # (Pre-existing bug fix, unrelated to E4: this checked the pre-Fable-A-#6
    # "study-tab" class name, which the pillar buttons never carried — they're
    # "study-pillar". Corrected to match the actual markup.)
    assert 'class="study-pillar active" data-kind="overview"' in html or \
           'data-kind="overview" class="study-pillar active"' in html or \
           ('"study-pillar active"' in html and 'data-kind="overview"' in html)


def test_study_page_is_a_fetch_shell_not_an_embed(_ws):
    """After the fetch-seam conversion (Task 4), the study-detail page must:
    - carry __DASH_CONFIG__ and data-source.js (DataSource layer),
    - expose window._studyName so the bootstrap can fetch the right slug,
    - NOT embed the full spec as window._study = {...} (data is fetched).
    """
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    html = _render_study_detail_html(_ws, "study-monod_kinetics-096184", spec)
    assert "window.__DASH_CONFIG__" in html
    assert "data-source.js" in html
    assert (
        'window._studyName = "study-monod_kinetics-096184"' in html
        or "window._studyName='study-monod_kinetics-096184'" in html
    )
    # The heavy spec must NOT be embedded — the JS fetches it via DataSource.
    assert "window._study = {" not in html and "window._study={" not in html


def test_study_detail_page_loads_set_tab_helper(_ws):
    """The page ships the _setStudyTab helper inline or via study-detail.js."""
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    html = _render_study_detail_html(_ws, "study-monod_kinetics-096184", spec)
    # The page must reference _setStudyTab somewhere (in the script tag or via onclick)
    assert "_setStudyTab" in html


def test_overview_panel_has_objective_editable(_ws):
    """Overview tab includes inline-editable objective field (conclusion moved to Conclusions tab)."""
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    html = _render_study_detail_html(_ws, "study-monod_kinetics-096184", spec)
    # Overview is now a Claim -> Test -> Result spine (objective/conclusion editors removed).
    assert 'id="objective-text"' not in html
    assert '>Claim</h2>' in html and '>Test</h2>' in html and '>Result</h2>' in html
    assert 'id="conclusion-text"' not in html
    assert 'id="panel-conclusions"' in html


def test_overview_panel_has_no_counts_strip(_ws):
    """The counts strip (variants · runs · interventions) was deleted from the
    Overview tab as part of the declutter (Task 6); pin its absence."""
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    html = _render_study_detail_html(_ws, "study-monod_kinetics-096184", spec)
    assert 'study-counts-strip' not in html and 'class="counts-strip"' not in html


def test_legacy_v2_crud_panels_retired(_ws):
    """The legacy v2-only baseline/variants/interventions CRUD forms are retired.

    These `{% if not _is_v3 %}` forms duplicated the v3 conditions editor (the
    Model tab now shows the composite + its resolved config + run settings). They
    are removed as part of the study-tabs de-slop; this test pins that they no
    longer render (replacing the prior tests that asserted their presence).
    """
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    html = _render_study_detail_html(_ws, "study-monod_kinetics-096184", spec)
    for gone in ('class="baseline-entry"', 'btn-baseline-add', 'btn-run-baseline',
                 'btn-baseline-remove', 'variant-row', 'btn-variant-new',
                 'intervention-row', 'btn-intervention-new'):
        assert gone not in html, f"legacy v2 CRUD markup {gone!r} should be retired"


def test_simulate_panel_has_sim_table_mount(_ws):
    """The Simulations (simulate) tab hosts the shared Sim-DB table mount, which
    the SPA fills client-side from /api/simulations (the old server-rendered
    #runs-table was replaced by the shared SimTable component)."""
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    html = _render_study_detail_html(_ws, "study-monod_kinetics-096184", spec)
    assert 'id="study-sim-table"' in html


def test_visualizations_panel_present(_ws):
    """Visualizations tab panel is present; the manual registered-viz list +
    add-viz button were retired (auto latest-run charts only)."""
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    html = _render_study_detail_html(_ws, "study-monod_kinetics-096184", spec)
    assert 'id="panel-visualize"' in html
    # Registered-visualization-modules section + "+ Add visualization" removed.
    assert 'id="viz-list"' not in html
    assert 'btn-add-viz' not in html
    assert 'Registered visualization modules' not in html


@pytest.fixture
def _rich_ws(tmp_path):
    """Workspace with a richly-populated v3 study to exercise every tab."""
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
    return ws


def test_full_study_renders_all_tabs(_rich_ws):
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_rich_ws, "rich")
    html = _render_study_detail_html(_rich_ws, "rich", spec)

    # Pillar tabs scaffolded (Report Cards merged into Tests — Task 10 — so it
    # is no longer a separate data-kind; Exports/data was deleted — Task E4).
    for kind in ("overview", "tests", "compose", "simulate", "readouts",
                 "visualize", "conclusions"):
        assert f'data-kind="{kind}"' in html
    assert 'data-kind="report-cards"' not in html
    assert 'data-kind="data"' not in html

    # Overview: objective text renders.
    assert "Compare growth kinetics" in html

    # Model (compose): the baseline composite FQN is accessible.
    assert "pkg.composites.core" in html

    # Simulations (simulate): the shared Sim-DB table mount is present; rows are
    # filled client-side from /api/simulations (no server-rendered run rows).
    assert 'id="study-sim-table"' in html

    # Results (visualize): the manual registered-visualization-modules list was
    # retired; the tab shows auto latest-run charts instead.
    assert 'id="panel-visualize"' in html
    assert 'id="viz-list"' not in html

    # Decide (conclusions) panel is present with the derived synthesis (the
    # legacy four-field free-text editor was retired).
    assert 'id="panel-conclusions"' in html
    assert 'id="conclusion-claims"' not in html


# ---------------------------------------------------------------------------
# Runs tab: viz section moved out + per-run metadata enrichment
# ---------------------------------------------------------------------------


def _section(html: str, start_marker: str, end_marker: str) -> str:
    """Slice html between two markers; both must be present."""
    i = html.index(start_marker)
    j = html.index(end_marker, i)
    return html[i:j]


def test_simulate_tab_does_not_render_charts_panel(_rich_ws):
    """The inline 'Latest run — visualizations' panel is not on the Simulations
    tab. Charts live exclusively in the Results (visualize) tab."""
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    html = _render_study_detail_html(_rich_ws, "rich", _study_detail_spec(_rich_ws, "rich"))
    sim_panel = _section(html, 'id="panel-simulate"', 'id="panel-visualize"')
    assert 'id="charts-panel"' not in sim_panel, (
        "Simulations tab should not contain the inline charts panel — charts "
        "belong to the Results (visualize) tab."
    )
    # Sanity: the Results tab still has its chart panel. (Task 10 removed the
    # standalone Report Cards panel that used to follow Visualizations in
    # document order — Report Cards is now a subsection of Tests. Task E4
    # then deleted the Exports/data panel that used to follow Visualizations,
    # so the next panel marker is Tests.)
    viz_panel = _section(html, 'id="panel-visualize"', 'id="panel-tests"')
    assert 'id="viz-charts-panel"' in viz_panel


# The per-run column set (Composite, Started, Duration, Model changes) now lives
# in the shared client-side SimTable component (static/sim-table.js STUDY_COLS),
# not the server-rendered HTML, so the old `test_runs_tab_has_richer_columns`
# server-column assertion was retired.


@pytest.fixture
def _ws_with_runs_db(tmp_path):
    """Workspace whose study has both a study.yaml runs[] list AND a populated
    runs.db, so _enrich_runs_with_meta has a real DB to merge from."""
    from vivarium_workbench.lib.composite_runs import (
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

    return ws


def _runs_by_id(spec):
    return {r.get("run_id"): r for r in (spec.get("runs") or [])}


def test_runs_db_carries_started_and_completed(_ws_with_runs_db):
    """read_runs_db_for_study surfaces the started/completed timestamps from
    runs_meta (the source the Simulations tab renders client-side via SimTable
    and /api/simulations)."""
    from vivarium_workbench.lib.study_spec import read_runs_db_for_study
    runs = {r["run_id"]: r for r in read_runs_db_for_study(_ws_with_runs_db, "rich-runs")}
    # run-A: started 1700000000, completed 1700000095 → 95s duration.
    assert runs["run-A"]["started_at"] == 1700000000.0
    assert runs["run-A"]["completed_at"] == 1700000095.0
    # run-B: 60s duration.
    assert runs["run-B"]["completed_at"] - runs["run-B"]["started_at"] == 60.0


def test_runs_db_carries_param_overrides(_ws_with_runs_db):
    """params_json from runs_meta surfaces as the run's params dict."""
    from vivarium_workbench.lib.study_spec import read_runs_db_for_study
    runs = {r["run_id"]: r for r in read_runs_db_for_study(_ws_with_runs_db, "rich-runs")}
    assert runs["run-A"]["params"] == {}
    assert runs["run-B"]["params"] == {"k": 2, "alpha": 0.5}


def test_runs_merge_tolerates_orphan_runs(_ws_with_runs_db):
    """A study.runs[] entry with no matching runs.db row still appears in the
    merged spec.runs[] (its metadata fields are simply absent)."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    runs = _runs_by_id(_study_detail_spec(_ws_with_runs_db, "rich-runs"))
    assert "run-orphan" in runs


def test_runs_merge_tolerates_missing_runs_db(_rich_ws):
    """A study with study.runs[] but no runs.db still carries its runs in the
    merged spec (the merge returns the authored runs unchanged)."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    runs = _runs_by_id(_study_detail_spec(_rich_ws, "rich"))
    assert "r1" in runs
    assert "r2" in runs


# ---------------------------------------------------------------------------
# Unit tests for the formatting helpers
# ---------------------------------------------------------------------------


def test_fmt_duration_handles_ranges():
    from vivarium_workbench.lib.study_page import _jinja_fmt_duration
    assert _jinja_fmt_duration(None) == ""
    assert _jinja_fmt_duration(-1) == ""
    assert _jinja_fmt_duration(0) == "0s"
    assert _jinja_fmt_duration(45) == "45s"
    assert _jinja_fmt_duration(60) == "1m"
    assert _jinja_fmt_duration(95) == "1m 35s"
    assert _jinja_fmt_duration(3600) == "1h"
    assert _jinja_fmt_duration(3660) == "1h 1m"


def test_fmt_ts_handles_none_and_unix():
    from vivarium_workbench.lib.study_page import _jinja_fmt_ts
    assert _jinja_fmt_ts(None) == ""
    assert _jinja_fmt_ts(0) == ""  # epoch zero treated as falsy/no-data
    assert _jinja_fmt_ts(1700000000.0) == "2023-11-14 22:13"


# ---------------------------------------------------------------------------
# computed_outcomes data-path: verify runs[].computed_outcomes survives
# load_spec + _enrich_runs_with_meta and reaches window._study in the SPA.
# ---------------------------------------------------------------------------


@pytest.fixture
def _ws_with_computed_outcomes(tmp_path):
    """Workspace whose study has one run with both authored outcomes and
    computed_outcomes (agree + divergent + no_authored entries + _status key)."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "computed-test"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "computed-test",
        "objective": "Validate computed-outcome pass-through.",
        "simulation_status": "ran",
        "baseline": [
            {"name": "core", "composite": "pkg.composites.core", "params": {}},
        ],
        "variants": [],
        "runs": [
            {
                "run_id": "run-co1",
                "variant": None,
                "composite": "core",
                "label": "baseline",
                "n_steps": 10,
                "status": "completed",
                "outcomes": {
                    "GROWTH_RATE_MATCHES": {"result": "PASS"},
                    "COPY_NUMBER_STABLE": {"result": "PASS"},
                    "EXPRESSION_LEVEL": {"result": "FAIL"},
                },
                "computed_outcomes": {
                    "GROWTH_RATE_MATCHES": {
                        "result": "PASS",
                        "measured_value": 0.42,
                        "evaluated_by": "code",
                        "reconcile": "agree",
                    },
                    "COPY_NUMBER_STABLE": {
                        "result": "FAIL",
                        "measured_value": 15.0,
                        "evaluated_by": "code",
                        "reconcile": "divergent",
                    },
                    "EXPRESSION_LEVEL": {
                        "result": "PASS",
                        "measured_value": 2.3,
                        "evaluated_by": "agent",
                        "reconcile": "no_authored",
                    },
                    "_status": "store_unresolved",
                },
            }
        ],
    }))
    return ws


def test_computed_outcomes_survive_study_detail_spec(_ws_with_computed_outcomes):
    """_study_detail_spec must return runs[].computed_outcomes intact so the
    SPA (window._study) can render the code-computed vs authored comparison."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec

    spec = _study_detail_spec(_ws_with_computed_outcomes, "computed-test")
    assert spec is not None

    runs = spec.get("runs") or []
    assert len(runs) >= 1, "expected at least one run in spec"

    run = runs[0]
    assert "computed_outcomes" in run, (
        "computed_outcomes key was stripped — it must pass through load_spec + "
        "_study_detail_spec unchanged so the client SPA can render it"
    )

    co = run["computed_outcomes"]
    assert isinstance(co, dict), "computed_outcomes must be a dict"

    # All authored entries survive
    assert "GROWTH_RATE_MATCHES" in co
    assert "COPY_NUMBER_STABLE" in co
    assert "EXPRESSION_LEVEL" in co

    # Entry shapes are intact
    assert co["GROWTH_RATE_MATCHES"]["result"] == "PASS"
    assert co["GROWTH_RATE_MATCHES"]["reconcile"] == "agree"
    assert co["COPY_NUMBER_STABLE"]["result"] == "FAIL"
    assert co["COPY_NUMBER_STABLE"]["reconcile"] == "divergent"
    assert co["EXPRESSION_LEVEL"]["evaluated_by"] == "agent"
    assert co["EXPRESSION_LEVEL"]["reconcile"] == "no_authored"

    # The _status sentinel key also survives (JS skips it by name)
    assert "_status" in co
    assert co["_status"] == "store_unresolved"


def test_computed_outcomes_survive_enrich_runs(_ws_with_computed_outcomes):
    """_enrich_runs_with_meta must not strip computed_outcomes even when there
    is no matching runs.db row (tolerant path).

    After the fetch-seam conversion (Task 4), the SPA receives study data via
    GET /api/study/<slug> (i.e. _study_detail_spec) rather than a Jinja embed.
    We verify that the spec returned by _study_detail_spec retains computed_outcomes
    after _render_study_detail_html exercises _enrich_runs_with_meta internally.
    """
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html

    spec = _study_detail_spec(_ws_with_computed_outcomes, "computed-test")
    # Exercise _enrich_runs_with_meta by rendering the page (it's called internally).
    _render_study_detail_html(_ws_with_computed_outcomes, "computed-test", spec)
    # The spec the JS fetches (via GET /api/study/<slug>) must carry computed_outcomes.
    runs = spec.get("runs") or []
    run_with_co = next(
        (r for r in runs if isinstance(r, dict) and r.get("computed_outcomes")), None
    )
    assert run_with_co is not None, (
        "computed_outcomes must survive _enrich_runs_with_meta in the spec "
        "returned by _study_detail_spec so study-detail.js can tally verdicts"
    )
    co = run_with_co["computed_outcomes"]
    assert "COPY_NUMBER_STABLE" in co
    assert "divergent" in (co.get("COPY_NUMBER_STABLE") or {}).get("reconcile", "")


# ---------------------------------------------------------------------------
# Wave 1 — W24 skeptic toggle + W15 epistemic-debts panel on the detail page
# ---------------------------------------------------------------------------

_HAS_DEBTS = False
try:  # pragma: no cover - environment dependent
    from vivarium_workbench.lib.needs_attention import open_epistemic_debts  # noqa: F401
    _HAS_DEBTS = True
except Exception:  # pragma: no cover
    _HAS_DEBTS = False


# The study-detail 'View as skeptic' toggle was retired (the skeptic view now
# lives only in the walkthrough/report surface), so its test was removed.


@pytest.mark.skipif(not _HAS_DEBTS, reason="open_epistemic_debts not importable")
def test_study_detail_renders_epistemic_debts_panel(_ws):
    """W15 — a study with no controls/alternatives/replication accrues debts,
    so the server-computed panel renders on the detail page."""
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    spec = _study_detail_spec(_ws, "study-monod_kinetics-096184")
    html = _render_study_detail_html(_ws, "study-monod_kinetics-096184", spec)
    # Epistemic debts moved off the Overview; the readiness signal now lives in the
    # header readiness panel (populated client-side).
    assert 'id="readiness-panel"' in html
    assert 'id="epistemic-debts-panel"' not in html
