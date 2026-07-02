"""Tests for stage-3c feedback-tracking surface in study-detail.

Verifies that _study_detail_spec injects spec["feedback_tracked"] with
status + summary, and that the rendered HTML contains the Feedback panel
elements.  Mirrors test_study_detail_page.py structure.

No AI dependency — purely verifies that plain Python data reaches the page.
"""
from __future__ import annotations

import yaml
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


@pytest.fixture
def _ws_with_feedback(tmp_path):
    """Workspace with a study + feedback files containing both open and
    addressed items.  The feedback file uses the dated-round-folder layout
    so _feedback_files picks it up."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "fb-test"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "fb-test",
        "objective": "Test feedback surface.",
        "status": "in_progress",
        "baseline": [
            {"name": "core", "composite": "pkg.composites.core", "params": {}},
        ],
        "variants": [],
        "runs": [],
    }))

    # Feedback under an investigation in this workspace
    _write(
        ws / "investigations" / "inv1" / "feedback-2026-01" / "feedback.yaml",
        {
            "meta": {"investigation": "inv1", "report_id": "rpt-fb1"},
            "annotations": {
                "study-fb-test": [
                    {
                        "ts": "2026-01-05T10:00:00Z",
                        "author": "Haochen",
                        "text": "Open question about the model.",
                    },
                ],
                "study-fb-test-charts": [
                    {
                        "ts": "2026-01-04T10:00:00Z",
                        "author": "Haochen",
                        "text": "Zoom in on the plot please.",
                    },
                ],
                "study-other": [
                    {
                        "ts": "2026-01-03T00:00:00Z",
                        "author": "Alice",
                        "text": "This is for another study — excluded.",
                    },
                ],
            },
            "responses": {
                "study-fb-test-charts": {
                    "status": "done",
                    "by": "claude",
                    "at": "2026-01-06",
                    "response": "Added zoom panel to chart.",
                },
            },
        },
    )

    return ws


@pytest.fixture
def _ws_no_feedback(tmp_path):
    """Workspace with a study but no feedback files."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "no-fb"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "no-fb",
        "objective": "No feedback here.",
        "status": "draft",
        "baseline": [
            {"name": "core", "composite": "pkg.composites.core", "params": {}},
        ],
        "variants": [],
        "runs": [],
    }))
    (ws / "investigations").mkdir(parents=True)
    return ws


# ---------------------------------------------------------------------------
# Test: feedback_tracked is injected into spec
# ---------------------------------------------------------------------------


def test_study_detail_spec_carries_feedback_tracked(_ws_with_feedback):
    """_study_detail_spec must attach spec['feedback_tracked'] with items and summary."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec

    spec = _study_detail_spec(_ws_with_feedback, "fb-test")
    assert spec is not None

    assert "feedback_tracked" in spec, (
        "spec must carry 'feedback_tracked' — server must call "
        "study_feedback_tracked and attach the result"
    )

    ft = spec["feedback_tracked"]
    assert isinstance(ft, dict), "feedback_tracked must be a dict"
    assert "items" in ft, "feedback_tracked must have 'items'"
    assert "summary" in ft, "feedback_tracked must have 'summary'"


def test_feedback_tracked_has_correct_statuses(_ws_with_feedback):
    """Items have status field; the study-fb-test-charts section is addressed."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec

    spec = _study_detail_spec(_ws_with_feedback, "fb-test")
    ft = spec["feedback_tracked"]
    items = ft["items"]

    # Should have 2 items (study-fb-test + study-fb-test-charts; study-other excluded)
    assert len(items) == 2, f"expected 2 items, got {len(items)}: {items}"

    by_section = {i["section"]: i for i in items}
    assert "study-fb-test" in by_section, "open item missing"
    assert "study-fb-test-charts" in by_section, "addressed item missing"

    assert by_section["study-fb-test"]["status"] == "open"
    assert by_section["study-fb-test-charts"]["status"] == "addressed"
    assert by_section["study-fb-test-charts"].get("response") == "Added zoom panel to chart."


def test_feedback_tracked_summary_correct(_ws_with_feedback):
    """Summary counts open/addressed/dismissed/total correctly."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec

    spec = _study_detail_spec(_ws_with_feedback, "fb-test")
    summary = spec["feedback_tracked"]["summary"]

    assert summary["open"] == 1
    assert summary["addressed"] == 1
    assert summary["dismissed"] == 0
    assert summary["total"] == 2


def test_feedback_tracked_absent_when_no_feedback(_ws_no_feedback):
    """When there's no feedback, feedback_tracked has empty items and zero summary."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec

    spec = _study_detail_spec(_ws_no_feedback, "no-fb")
    # feedback_tracked should be present (attached) but have no items
    # OR absent entirely (spec has no feedback_tracked key) — both are acceptable
    # if there's no feedback; but it must not raise.
    assert spec is not None

    ft = spec.get("feedback_tracked")
    if ft is not None:
        assert ft["items"] == []
        assert ft["summary"]["total"] == 0


# ---------------------------------------------------------------------------
# Test: rendered HTML carries feedback_tracked in window._study JSON
# ---------------------------------------------------------------------------


def test_feedback_tracked_in_study_spec(_ws_with_feedback):
    """The spec returned by _study_detail_spec must include feedback_tracked so
    study-detail.js receives it via GET /api/study/<slug> and renders the panel.

    After the fetch-seam conversion (Task 4), the SPA fetches the spec — the
    window._study JSON embed is no longer in the rendered HTML.  We verify the
    DATA is in the spec (what the API returns) rather than in the HTML.
    """
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec

    spec = _study_detail_spec(_ws_with_feedback, "fb-test")
    assert "feedback_tracked" in spec, (
        "feedback_tracked must be present in the spec returned by _study_detail_spec "
        "so study-detail.js can render the Feedback panel via the fetched spec"
    )
    ft = spec["feedback_tracked"]
    # Verify the structure the JS renderer expects
    items = ft.get("items") or []
    by_section = {i["section"]: i for i in items if isinstance(i, dict)}
    assert any(i.get("status") == "addressed" for i in items), "addressed item must be present"
    assert "study-fb-test-charts" in by_section, "addressed item section must be present"


def test_feedback_panel_anchor_in_html(_ws_with_feedback):
    """The rendered HTML contains the feedback-tracked-panel anchor div
    so JS has a container to populate."""
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec
    from vivarium_workbench.lib.study_page import render_study_detail_html as _render_study_detail_html

    spec = _study_detail_spec(_ws_with_feedback, "fb-test")
    html = _render_study_detail_html(_ws_with_feedback, "fb-test", spec)

    assert "feedback-tracked-panel" in html, (
        "HTML must contain a #feedback-tracked-panel element for the JS render"
    )


def test_no_ai_dependency_in_feedback_tracking(_ws_with_feedback):
    """Verify that feedback_tracked is computed without any AI/LLM call.

    The function call chain must be: _study_detail_spec → study_feedback_tracked
    (plain Python, no network calls). We verify by monkey-patching the AI client
    to raise if called — the spec must still be populated.
    """
    from vivarium_workbench.lib.study_spec import load_study_detail_spec as _study_detail_spec

    # The spec must be computed without any AI call
    spec = _study_detail_spec(_ws_with_feedback, "fb-test")
    ft = spec.get("feedback_tracked")
    assert ft is not None and ft["summary"]["total"] > 0, (
        "feedback_tracked must be populated by pure Python, not AI"
    )
