"""Unit tests for the single-study report renderer.

Covers ``POST /api/study-report-single`` via the pure handler
``build_single_study_report_for_test`` and the underlying
``render_single_study_report``.
"""
from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.lib.single_study_report import (
    build_single_study_report_for_test,
    render_single_study_report,
    resolve_focus_study,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def _ws(tmp_path):
    """Workspace with the minimum directory layout the renderer expects."""
    ws = tmp_path / "ws"
    (ws / "investigations").mkdir(parents=True)
    (ws / "studies").mkdir(parents=True)
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: ws\nplugin_version: 0.6.1\npackage_path: pkg\n"
    )
    return ws


def _write_study(ws: Path, slug: str, **fields) -> Path:
    """Write a minimal study.yaml under studies/<slug>/."""
    p = ws / "studies" / slug / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": 4, "name": slug, **fields}
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def _write_investigation(ws: Path, slug: str, **fields) -> Path:
    p = ws / "investigations" / slug / "investigation.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"name": slug, **fields}
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def _write_viz(ws: Path, study_slug: str, name: str, html: str) -> Path:
    p = ws / "studies" / study_slug / "viz" / f"{name}.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html)
    return p


# ---------------------------------------------------------------------------
# resolve_focus_study
# ---------------------------------------------------------------------------


def test_resolve_focus_study_returns_field(_ws):
    _write_investigation(_ws, "inv", focus_study="dnaa-00")
    assert resolve_focus_study(_ws, "inv") == "dnaa-00"


def test_resolve_focus_study_none_when_field_missing(_ws):
    _write_investigation(_ws, "inv", title="something")
    assert resolve_focus_study(_ws, "inv") is None


def test_resolve_focus_study_none_when_investigation_missing(_ws):
    assert resolve_focus_study(_ws, "no-such-inv") is None


def test_resolve_focus_study_whitespace_stripped(_ws):
    _write_investigation(_ws, "inv", focus_study="  dnaa-00  ")
    assert resolve_focus_study(_ws, "inv") == "dnaa-00"


# ---------------------------------------------------------------------------
# render_single_study_report (low-level)
# ---------------------------------------------------------------------------


def test_render_writes_file_and_returns_path(_ws):
    _write_study(_ws, "s1", report={"title": "S1", "verdict": "passing"})
    out = render_single_study_report(_ws, "s1")
    assert out.exists()
    assert out.name == "single-study-s1.html"
    text = out.read_text()
    assert "S1" in text
    assert "Passing" in text


def test_render_inlines_viz_html(_ws):
    _write_study(_ws, "s1", report={"title": "S1"})
    _write_viz(_ws, "s1", "trace", "<html><body>VIZ_BODY</body></html>")
    out = render_single_study_report(_ws, "s1")
    text = out.read_text()
    # Viz HTML is iframe-srcdoc-embedded, so the marker should be present
    # (HTML-escaped). The viz name surfaces as a section heading.
    assert "trace" in text
    assert "VIZ_BODY" in text


def test_render_includes_biological_summary(_ws):
    _write_study(
        _ws, "s1",
        report={"title": "S1"},
        biological_summary="DnaA drives the cell into replication.",
    )
    out = render_single_study_report(_ws, "s1")
    text = out.read_text()
    assert "Biological summary" in text
    assert "DnaA drives the cell" in text


def test_render_emits_key_metrics(_ws):
    _write_study(_ws, "s1", report={
        "title": "S1",
        "key_metrics": [
            "band [300,800]",
            {"label": "DnaA count", "value": "~325", "status": "pass"},
        ],
    })
    out = render_single_study_report(_ws, "s1")
    text = out.read_text()
    assert "band [300,800]" in text
    assert "DnaA count" in text
    assert "~325" in text


def test_render_excludes_investigation_overview(_ws):
    """The whole point: the single-study report must NOT carry the
    investigation-level overview, comparative, or cross-study chrome."""
    _write_investigation(_ws, "inv", focus_study="s1",
                         lead="INVESTIGATION_LEAD",
                         at_a_glance=[{"study": "s1", "role": "AAG_ROLE"}],
                         glossary=[{"term": "X", "definition": "GLOSS_DEF"}])
    _write_study(_ws, "s1", report={"title": "S1"})
    out = render_single_study_report(_ws, "s1", investigation_slug="inv")
    text = out.read_text()
    assert "INVESTIGATION_LEAD" not in text
    assert "AAG_ROLE" not in text
    assert "GLOSS_DEF" not in text


def test_render_raises_when_study_missing(_ws):
    with pytest.raises(FileNotFoundError):
        render_single_study_report(_ws, "no-such-study")


# ---------------------------------------------------------------------------
# build_single_study_report_for_test (pure handler)
# ---------------------------------------------------------------------------


def test_handler_with_explicit_study(_ws):
    _write_study(_ws, "s1", report={"title": "S1"})
    resp, code = build_single_study_report_for_test(_ws, {"study": "s1"})
    assert code == 200, resp
    assert resp["study"] == "s1"
    assert resp["html_path"].endswith("single-study-s1.html")
    assert resp["size_bytes"] > 0


def test_handler_resolves_focus_study_from_investigation(_ws):
    _write_investigation(_ws, "inv", focus_study="s1")
    _write_study(_ws, "s1", report={"title": "S1"})
    resp, code = build_single_study_report_for_test(_ws, {"investigation": "inv"})
    assert code == 200, resp
    assert resp["study"] == "s1"
    assert resp["investigation"] == "inv"


def test_handler_explicit_study_wins_over_investigation_focus(_ws):
    _write_investigation(_ws, "inv", focus_study="s-focus")
    _write_study(_ws, "s-focus", report={"title": "Focus"})
    _write_study(_ws, "s-override", report={"title": "Override"})
    resp, code = build_single_study_report_for_test(
        _ws, {"investigation": "inv", "study": "s-override"},
    )
    assert code == 200, resp
    assert resp["study"] == "s-override"


def test_handler_400_when_neither_provided(_ws):
    resp, code = build_single_study_report_for_test(_ws, {})
    assert code == 400
    assert "required" in resp["error"]


def test_handler_404_when_investigation_has_no_focus_study(_ws):
    _write_investigation(_ws, "inv", title="no focus here")
    resp, code = build_single_study_report_for_test(_ws, {"investigation": "inv"})
    assert code == 404
    assert "focus_study" in resp["error"]


def test_handler_404_when_study_missing(_ws):
    resp, code = build_single_study_report_for_test(_ws, {"study": "nope"})
    assert code == 404
    assert "nope" in resp["error"] or "not found" in resp["error"]


def test_handler_writes_to_reports_dir(_ws):
    _write_study(_ws, "s1", report={"title": "S1"})
    resp, code = build_single_study_report_for_test(_ws, {"study": "s1"})
    assert code == 200
    out = _ws / "reports" / "single-study-s1.html"
    assert out.is_file()
    # html_path is relative to ws_root in the response
    assert resp["html_path"] == "reports/single-study-s1.html"


def test_handler_includes_report_narrative_slots(_ws):
    """The report.purpose/result/decision narrative slots from study.yaml
    should appear under 'Study narrative' in the rendered HTML."""
    _write_study(_ws, "s1", report={
        "title": "S1",
        "purpose": "PURPOSE_TEXT",
        "result": "RESULT_TEXT",
        "decision": "DECISION_TEXT",
    })
    resp, code = build_single_study_report_for_test(_ws, {"study": "s1"})
    assert code == 200
    text = (_ws / "reports" / "single-study-s1.html").read_text()
    assert "PURPOSE_TEXT" in text
    assert "RESULT_TEXT" in text
    assert "DECISION_TEXT" in text
    assert "Study narrative" in text


def test_renders_sticky_strip_with_title_and_section_nav(_ws):
    """Single-study reports get a sticky top-of-page strip with the
    title + verdict + jump nav to Overview / Biology / Visualisations.
    Stays pinned via CSS position:sticky so the user can navigate
    long viz-heavy reports without scrolling back to the topbar."""
    _write_study(_ws, "s1", report={
        "title": "My Study",
        "verdict": "passing",
        "conclusion": "All good.",
    }, biological_summary="Mechanism prose.")
    resp, code = build_single_study_report_for_test(_ws, {"study": "s1"})
    assert code == 200
    text = (_ws / "reports" / "single-study-s1.html").read_text()
    # Sticky strip + nav present
    assert 'class="ssr-sticky-strip"' in text
    assert 'class="ssr-sticky-title"' in text
    assert 'class="ssr-section-nav"' in text
    assert 'position: sticky' in text
    # Jump anchors target the section IDs
    assert 'href="#overview"' in text
    assert 'href="#biology"' in text
    assert 'id="overview"' in text
    assert 'id="biology"' in text


def test_section_nav_omits_chips_for_empty_sections(_ws):
    """If a section would render empty, its jump chip is suppressed —
    dead-end nav links are worse than no nav. The Overview chip still
    renders here because the report block contributes head_blocks."""
    _write_study(_ws, "s1", report={"title": "T", "conclusion": "x"})
    # No biological_summary / readouts / viz → those chips should be absent.
    resp, code = build_single_study_report_for_test(_ws, {"study": "s1"})
    assert code == 200
    text = (_ws / "reports" / "single-study-s1.html").read_text()
    assert 'href="#overview"' in text       # has head_blocks
    assert 'href="#biology"' not in text    # no biological_summary
    assert 'href="#viz"' not in text        # no viz embeds


# ---------------------------------------------------------------------------
# W24 — skeptical-reader report mode
# ---------------------------------------------------------------------------

# These exercise the new render paths that lean on pbg_superpowers.rigor /
# needs_attention. The renderer degrades gracefully when those aren't
# importable, so skip the strict-content assertions in that case.
_HAS_RIGOR = False
try:  # pragma: no cover - environment dependent
    from pbg_superpowers.rigor import study_rigor, finding_evidential_weight  # noqa: F401
    from pbg_superpowers.needs_attention import open_epistemic_debts  # noqa: F401
    _HAS_RIGOR = True
except Exception:  # pragma: no cover
    _HAS_RIGOR = False

_needs_rigor = pytest.mark.skipif(
    not _HAS_RIGOR, reason="pbg-superpowers rigor/needs_attention not importable")


def _rich_skeptic_study(ws: Path, slug: str = "s1") -> Path:
    """A study with the fields the skeptic mode / weight / debts read."""
    return _write_study(
        ws, slug,
        report={"title": "Rich", "conclusion": "done"},
        objective="Test the thing.",
        falsifiability="A growth rate outside [0.1, 0.5] would overturn this.",
        findings=[{
            "id": "F-01", "tier": "interpretation", "mechanism_origin": "emergent",
            "statement": "The model reproduces the observed division time.",
            "evidence": {"from_test": "division-time", "observed": "42 min"},
            "next_action": "Sweep the elongation rate to confirm.",
            "calibration_anchor": {"divergence_factor": 1.2},
        }],
        controls=[{
            "name": "shuffle-control", "kind": "negative", "result": "PASS",
            "observed": "no division", "expected": "no division",
        }],
        alternative_hypotheses=[
            {"claim": "It is an artifact.", "status": "excluded",
             "discriminated_by": "division-time"},
            {"claim": "Something else entirely.", "status": "not-excluded"},
        ],
        robustness={"n_replicates": 3, "seeds": [0, 1, 2]},
        limitations=["Single medium only."],
        behavior_tests=[{"name": "division-time", "pass_if": {"op": "in_range",
                                                              "low": 0.1, "high": 0.5}}],
    )


def test_skeptic_mode_writes_distinct_file_and_reorders(_ws):
    _rich_skeptic_study(_ws)
    resp, code = build_single_study_report_for_test(
        _ws, {"study": "s1", "skeptic": True})
    assert code == 200
    assert resp["skeptic"] is True
    assert resp["html_path"] == "reports/single-study-s1-skeptic.html"
    out = _ws / "reports" / "single-study-s1-skeptic.html"
    assert out.is_file()
    # The default (non-skeptic) file is NOT clobbered.
    assert not (_ws / "reports" / "single-study-s1.html").is_file()
    text = out.read_text()
    # Audit trail leads the body, before the conclusion verdicts.
    assert 'id="audit-trail"' in text
    if 'id="verdicts"' in text:
        assert text.index('id="audit-trail"') < text.index('id="verdicts"')


def test_skeptic_audit_trail_threshold_provenance_none(_ws):
    # No behavior-test band carries cites / calibration_anchor → "none".
    _write_study(_ws, "s1", report={"title": "T"},
                 findings=[{"id": "F-01", "statement": "claim"}],
                 behavior_tests=[{"name": "x", "pass_if": {"op": "at_least", "low": 1}}])
    render_single_study_report(_ws, "s1", skeptic=True)
    text = (_ws / "reports" / "single-study-s1-skeptic.html").read_text()
    assert "Threshold provenance" in text
    assert "none" in text.lower()


def test_non_skeptic_mode_has_no_audit_trail(_ws):
    _rich_skeptic_study(_ws)
    render_single_study_report(_ws, "s1")
    text = (_ws / "reports" / "single-study-s1.html").read_text()
    assert 'id="audit-trail"' not in text


# ---------------------------------------------------------------------------
# W8 — per-finding evidential-weight chip
# ---------------------------------------------------------------------------

@_needs_rigor
def test_finding_weight_chip_rendered(_ws):
    _rich_skeptic_study(_ws)
    render_single_study_report(_ws, "s1")
    text = (_ws / "reports" / "single-study-s1.html").read_text()
    assert 'class="finding-weight"' in text
    # A well-supported finding should not be labelled weak.
    assert ("strong" in text) or ("moderate" in text)


# ---------------------------------------------------------------------------
# W15 — open epistemic debts panel
# ---------------------------------------------------------------------------

@_needs_rigor
def test_epistemic_debts_panel_rendered(_ws):
    # A bare study with no controls/alternatives/replication accrues debts.
    _write_study(_ws, "s1", report={"title": "Bare"},
                 findings=[{"id": "F-01", "statement": "An untested claim."}])
    render_single_study_report(_ws, "s1")
    text = (_ws / "reports" / "single-study-s1.html").read_text()
    assert 'id="epistemic-debts"' in text
    assert "Open epistemic debts" in text
