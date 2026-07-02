"""Tests for vivarium_workbench.lib.study_page.

Covers:
- build_study_detail_page: slug validation → 404, unknown slug → 404, valid → 200
- render_study_detail_html: real Jinja render against a minimal spec
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Workspace with one study under investigations/<slug>/spec.yaml.

    Uses the legacy v2 variants shape (one variant with source) which passes
    the spec validator — identical to the shape used by test_study_detail_page.py.
    """
    slug = "dnaa-01-binding"
    inv = tmp_path / "investigations" / slug
    inv.mkdir(parents=True)
    (inv / "spec.yaml").write_text(yaml.safe_dump({
        "name": slug,
        "title": "DnaA Binding Study",
        "baseline": "dnaa-binding-baseline",
        "status": "draft",
        "objective": "Test DnaA binding kinetics.",
        "question": "",
        "hypothesis": "",
        "comparisons": [],
        "conclusions": "",
        "variants": [
            {
                "name": "dnaa-binding-baseline",
                "source": "pbg_basic_processes.composites.test.dummy",
                "document": "./composites/dnaa-binding-baseline.yaml",
            },
        ],
        "runs": [],
    }), encoding="utf-8")
    return tmp_path


class TestBuildStudyDetailPage:
    def test_invalid_slug_returns_404_not_found(self, ws: Path):
        from vivarium_workbench.lib.study_page import build_study_detail_page
        html, status = build_study_detail_page(ws, "../etc/passwd")
        assert status == 404
        assert "<h1>Not found</h1>" in html

    def test_invalid_slug_leading_dot(self, ws: Path):
        from vivarium_workbench.lib.study_page import build_study_detail_page
        html, status = build_study_detail_page(ws, ".hidden")
        assert status == 404
        assert "<h1>Not found</h1>" in html

    def test_invalid_slug_uppercase(self, ws: Path):
        from vivarium_workbench.lib.study_page import build_study_detail_page
        html, status = build_study_detail_page(ws, "BadSlug")
        assert status == 404
        assert "<h1>Not found</h1>" in html

    def test_unknown_slug_returns_404_study_not_found(self, ws: Path):
        from vivarium_workbench.lib.study_page import build_study_detail_page
        html, status = build_study_detail_page(ws, "does-not-exist")
        assert status == 404
        assert "<h1>Study not found</h1>" in html
        assert "<code>does-not-exist</code>" in html

    def test_valid_study_returns_200(self, ws: Path):
        from vivarium_workbench.lib.study_page import build_study_detail_page
        html, status = build_study_detail_page(ws, "dnaa-01-binding")
        assert status == 200
        assert isinstance(html, str)
        assert len(html) > 100

    def test_valid_study_html_contains_study_name(self, ws: Path):
        from vivarium_workbench.lib.study_page import build_study_detail_page
        html, status = build_study_detail_page(ws, "dnaa-01-binding")
        assert status == 200
        # The template renders the study name somewhere on the page
        assert "dnaa-01-binding" in html or "DnaA Binding Study" in html

    def test_underscore_slug_is_valid(self, ws: Path, tmp_path: Path):
        """Slugs with underscores (e.g. generated study names) must be accepted."""
        from vivarium_workbench.lib.study_page import build_study_detail_page
        slug = "study-monod_kinetics-01"
        inv = tmp_path / "studies" / slug
        inv.mkdir(parents=True)
        (inv / "study.yaml").write_text(yaml.safe_dump({
            "name": slug,
            "baseline": "monod-kinetics",
            "status": "draft",
            "objective": "",
            "question": "",
            "hypothesis": "",
            "comparisons": [],
            "conclusions": "",
            "variants": [
                {
                    "name": "monod-kinetics",
                    "source": "some.module.composite",
                    "document": "./composites/monod-kinetics.yaml",
                },
            ],
            "runs": [],
        }), encoding="utf-8")
        html, status = build_study_detail_page(tmp_path, slug)
        assert status == 200


class TestRenderStudyDetailHtml:
    def test_render_produces_html_with_tab_scaffold(self, ws: Path):
        """The real Jinja render includes the 8-tab scaffold."""
        from vivarium_workbench.lib.study_page import render_study_detail_html
        from vivarium_workbench.lib.study_spec import load_study_detail_spec
        spec = load_study_detail_spec(ws, "dnaa-01-binding")
        assert spec is not None
        html = render_study_detail_html(ws, "dnaa-01-binding", spec)
        # All 8 base tabs present
        for kind in ("overview", "baseline", "variants", "interventions",
                     "tests", "runs", "visualizations", "conclusions"):
            assert f'data-kind="{kind}"' in html, f"tab {kind!r} missing from render"

    def test_render_includes_study_name_in_js(self, ws: Path):
        """The rendered page sets window._studyName (fetch-seam pattern)."""
        from vivarium_workbench.lib.study_page import render_study_detail_html
        from vivarium_workbench.lib.study_spec import load_study_detail_spec
        spec = load_study_detail_spec(ws, "dnaa-01-binding")
        assert spec is not None
        html = render_study_detail_html(ws, "dnaa-01-binding", spec)
        assert "window._studyName" in html

    def test_builder_delegates_to_render_via_monkeypatch(self, ws: Path, monkeypatch):
        """build_study_detail_page delegates to render_study_detail_html."""
        import vivarium_workbench.lib.study_page as sp
        called = []

        def fake_render(ws_root, name, spec):
            called.append((ws_root, name))
            return "<html>STUB</html>"

        monkeypatch.setattr(sp, "render_study_detail_html", fake_render)
        html, status = sp.build_study_detail_page(ws, "dnaa-01-binding")
        assert status == 200
        assert html == "<html>STUB</html>"
        assert called == [(ws, "dnaa-01-binding")]
