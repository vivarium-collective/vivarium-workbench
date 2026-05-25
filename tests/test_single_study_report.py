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
