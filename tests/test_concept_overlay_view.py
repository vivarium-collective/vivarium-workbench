"""GET /api/concept-overlay builder — vivarium_workbench.lib.report_views.build_concept_overlay.

Derives the ephemeral Concept overlay for an investigation (one Concept per
plan_gates gate) and attaches each member study's canonical verdict. The
derivation lives in viva_superpowers; the builder degrades gracefully when that
package isn't importable. These tests inject a tiny workspace and exercise the
real derivation (importable via PYTHONPATH when the dev viva_superpowers worktree
is on the path), with a skip when it isn't.
"""
from __future__ import annotations

import pytest
import yaml

from vivarium_workbench.lib.report_views import build_concept_overlay

pytest.importorskip("viva_superpowers.concept_overlay")


@pytest.fixture
def tmp_ws(tmp_path):
    ws = tmp_path / "ws"
    (ws).mkdir()
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv_dir = ws / "investigations" / "og"
    inv_dir.mkdir(parents=True)
    inv_dir.joinpath("investigation.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "og",
        "title": "Origins",
        "studies": ["og-vent", "og-heredity"],
        "plan_gates": [
            {"id": "A1", "member_studies": ["og-vent"]},
            {"id": "D", "member_studies": ["og-heredity"]},
        ],
    }))
    studies = ws / "studies"
    # og-vent: a plain study with an authored verdict
    (studies / "og-vent").mkdir(parents=True)
    (studies / "og-vent" / "study.yaml").write_text(yaml.safe_dump({
        "name": "og-vent", "verdict": "supported",
        "baseline": [{"name": "b", "composite": "pkg.x"}],
    }))
    # og-heredity: an already-consolidated (arms-bearing) study → consolidated concept
    (studies / "og-heredity").mkdir(parents=True)
    (studies / "og-heredity" / "study.yaml").write_text(yaml.safe_dump({
        "name": "og-heredity", "arms": [{"source_study": "og-inheritance"}],
        "baseline": [{"name": "b", "composite": "pkg.x"}],
    }))
    return ws


def test_derives_concepts_and_verdicts(tmp_ws):
    body, status = build_concept_overlay(tmp_ws, investigation="og")
    assert status == 200
    assert [c["from_gate"] for c in body["concepts"]] == ["A1", "D"]
    assert body["concepts"][0]["member_studies"] == ["og-vent"]
    # the arms-bearing gate reads as consolidated
    assert body["concepts"][1]["org_state"] == "consolidated"
    # each member carries a canonical verdict; og-vent's authored verdict projects through
    assert body["study_verdicts"]["og-vent"]["verdict"] == "supported"
    assert "og-heredity" in body["study_verdicts"]


def test_tolerant_on_missing_investigation(tmp_ws):
    body, status = build_concept_overlay(tmp_ws, investigation="does-not-exist")
    assert status == 200
    assert body["concepts"] == []


def test_tolerant_on_missing_workspace(tmp_path):
    body, status = build_concept_overlay(tmp_path / "nope", investigation="og")
    assert status == 200
    assert body == {"investigation": "og", "concepts": [], "study_verdicts": {}, "diverges": []}


def test_injected_fns_bypass_package_import(tmp_ws):
    """derive_fn/verdict_fn are injectable (no dependency on the installed package)."""
    body, status = build_concept_overlay(
        tmp_ws,
        investigation="og",
        derive_fn=lambda inv, studies: [{"id": "x", "from_gate": "A1",
                                         "member_studies": ["og-vent"], "derived": True}],
        verdict_fn=lambda spec: {"verdict": "qualified", "diverges": True},
    )
    assert status == 200
    assert body["concepts"][0]["id"] == "x"
    assert body["study_verdicts"]["og-vent"]["verdict"] == "qualified"
    assert body["diverges"] == ["og-vent"]


def test_concept_overlay_endpoint(tmp_ws):
    """GET /api/concept-overlay wires the builder end-to-end through the FastAPI app."""
    from fastapi.testclient import TestClient
    from vivarium_workbench.api.app import create_app, get_workspace

    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_ws
    client = TestClient(app)

    r = client.get("/api/concept-overlay", params={"investigation": "og"})
    assert r.status_code == 200
    body = r.json()
    assert [c["from_gate"] for c in body["concepts"]] == ["A1", "D"]
    assert body["study_verdicts"]["og-vent"]["verdict"] == "supported"
    assert "/api/concept-overlay" in app.openapi()["paths"]
