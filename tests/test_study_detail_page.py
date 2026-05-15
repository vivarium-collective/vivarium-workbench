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
