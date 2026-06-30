"""framework-emitters — composite-resolution lint (dashboard slice).

The dashboard HAS the composite registry, so it can flag any study whose
declared composite ref (baseline / conditions / simulation_set) doesn't resolve
to a registered composite. This is what would have caught the autopoiesis
studies 2–4 (numpy-only, no registered composite).

Covers:
  - the pure helpers in composite_lookup (_study_composite_refs, _ref_resolves,
    unresolved_study_composite_refs fallback, known_composite_ids);
  - server._composite_resolution_findings + its surfacing through _report_lint;
  - the study-detail banner (_render_study_detail_html);
  - the explorer graceful-degrade payload + JS handling.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest

from vivarium_dashboard.lib import composite_lookup as cl

_PKG = Path(__file__).parent.parent / "vivarium_dashboard"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_study_composite_refs_collects_all_declaration_sites():
    spec = {
        "baseline": [{"name": "b", "composite": "pkg.composites.base"}],
        "conditions": {
            "baseline": {"composite": "pkg.composites.cond_base"},
            "variants": [{"name": "v", "composite": "pkg.composites.var"}],
        },
        "simulation_set": [{"composite": "pkg.composites.sim"}],
        # runs[] aliases are NOT canonical declarations and must be ignored.
        "runs": [{"name": "r", "composite": "short-alias"}],
    }
    refs = cl._study_composite_refs(spec)
    assert refs == [
        "pkg.composites.base",
        "pkg.composites.cond_base",
        "pkg.composites.var",
        "pkg.composites.sim",
    ]


def test_ref_resolves_exact_and_last_segment():
    known = {"pbg_autopoiesis.composites.membrane-metabolism-loop"}
    # exact dotted id
    assert cl._ref_resolves("pbg_autopoiesis.composites.membrane-metabolism-loop", known)
    # short alias matches the trailing .composites.<slug> segment
    assert cl._ref_resolves("membrane-metabolism-loop", known)
    # unknown ref does not resolve
    assert not cl._ref_resolves("pbg_autopoiesis.composites.spatial-containment", known)


def test_unresolved_study_composite_refs_flags_missing_only():
    known = {"pkg.composites.base"}
    spec = {
        "baseline": [
            {"name": "ok", "composite": "pkg.composites.base"},
            {"name": "bad", "composite": "pkg.composites.ghost"},
        ],
    }
    unresolved = cl.unresolved_study_composite_refs(spec, known)
    assert unresolved == ["pkg.composites.ghost"]


def test_unresolved_study_composite_refs_empty_when_all_resolve():
    known = {"pkg.composites.base"}
    spec = {"baseline": [{"name": "ok", "composite": "pkg.composites.base"}]}
    assert cl.unresolved_study_composite_refs(spec, known) == []


def test_unresolved_study_composite_refs_resolves_short_slug_alias():
    """A study declaring the short alias ``baseline`` must resolve against the
    registered dotted id ``pkg.composites.baseline`` — even though the canonical
    pbg_superpowers linter is a strict membership test that would flag it.

    Regression: every v2ecoli study uses ``conditions.baseline.composite:
    baseline``; without the local alias match they all false-flagged as
    "composite not found in registry: baseline".
    """
    known = {"v2ecoli.composites.baseline", "v2ecoli.composites.baseline.baseline"}
    spec = {"conditions": {"baseline": {"composite": "baseline"}}}
    assert cl.unresolved_study_composite_refs(spec, known) == []
    # A genuinely-unregistered ref is still flagged.
    bogus = {"conditions": {"baseline": {"composite": "totally_made_up"}}}
    assert cl.unresolved_study_composite_refs(bogus, known) == ["totally_made_up"]


# ---------------------------------------------------------------------------
# known_composite_ids over a real (tiny) workspace
# ---------------------------------------------------------------------------

def _make_ws(tmp_path: Path, *, with_composite: bool) -> Path:
    ws = tmp_path / "ws"
    (ws / "pbg_ws" / "composites").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\npackage_path: pbg_ws\n")
    if with_composite:
        (ws / "pbg_ws" / "composites" / "foo.composite.yaml").write_text(
            yaml.safe_dump({"name": "foo", "description": "x", "state": {}})
        )
    return ws


def test_known_composite_ids_finds_workspace_specs(tmp_path):
    ws = _make_ws(tmp_path, with_composite=True)
    ids = cl.known_composite_ids(ws)
    assert "pbg_ws.composites.foo" in ids


# ---------------------------------------------------------------------------
# server._composite_resolution_findings + _report_lint surfacing
# ---------------------------------------------------------------------------

def _seed_study(ws: Path, slug: str, composite: str):
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    sd.parent  # ensure
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": slug,
        "baseline": [{"name": "b", "composite": composite}],
    }))


def test_composite_resolution_findings_flags_unresolved(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    ws = _make_ws(tmp_path, with_composite=True)
    _seed_study(ws, "good-study", "pbg_ws.composites.foo")
    _seed_study(ws, "bad-study", "pbg_ws.composites.ghost")
    monkeypatch.setattr(srv, "WORKSPACE", ws)

    findings = srv._composite_resolution_findings(ws)
    bad = [f for f in findings if f["study"] == "bad-study"]
    good = [f for f in findings if f["study"] == "good-study"]
    assert bad, f"expected an unresolved-composite finding for bad-study; got {findings}"
    assert bad[0]["check"] == "unresolved_composite"
    assert bad[0]["severity"] == "warning"
    assert "ghost" in bad[0]["message"]
    assert not good, "resolvable baseline should not be flagged"


def test_report_lint_surfaces_unresolved_composite(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    ws = _make_ws(tmp_path, with_composite=False)
    _seed_study(ws, "bad-study", "pkg.composites.ghost")
    monkeypatch.setattr(srv, "WORKSPACE", ws)

    body, code = srv._report_lint(ws)
    assert code == 200
    findings = json.loads(body)["findings"]
    assert any(f.get("check") == "unresolved_composite" for f in findings), (
        f"report-lint should include the composite-resolution finding; got {findings}"
    )


# ---------------------------------------------------------------------------
# study-detail banner
# ---------------------------------------------------------------------------

def test_study_detail_renders_unresolved_banner(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    ws = _make_ws(tmp_path, with_composite=True)
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    spec = {
        "schema_version": 4,
        "name": "bad-study",
        "baseline": [{"name": "b", "composite": "pbg_ws.composites.ghost"}],
    }
    html = srv._render_study_detail_html("bad-study", spec)
    assert "composite not found in registry" in html
    assert "pbg_ws.composites.ghost" in html


def test_study_detail_no_banner_when_resolvable(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    ws = _make_ws(tmp_path, with_composite=True)
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    spec = {
        "schema_version": 4,
        "name": "good-study",
        "baseline": [{"name": "b", "composite": "pbg_ws.composites.foo"}],
    }
    html = srv._render_study_detail_html("good-study", spec)
    assert "composite not found in registry" not in html


# ---------------------------------------------------------------------------
# explorer graceful degrade — JS handling of the honest payload
# ---------------------------------------------------------------------------

def test_explorer_js_handles_unresolved_payload():
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    # The _ceFetch error path keys on the structured `unresolved` flag and
    # renders an honest message instead of a bare "error composite" node.
    assert "data.unresolved" in js
    assert "Composite not found in the" in js


def test_single_study_report_renders_unresolved_banner():
    from vivarium_dashboard.lib.single_study_report import _render_html
    html = _render_html(
        {"name": "s", "baseline": [{"composite": "pkg.composites.ghost"}]},
        [], investigation_slug=None, generated_at="now",
        unresolved_composites=["pkg.composites.ghost"],
    )
    assert "composite not found in registry" in html
    assert "pkg.composites.ghost" in html
