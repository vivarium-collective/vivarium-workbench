"""Thread-A / Task 2 (A1): surface the investigation acceptance roll-up.

``roll_up_acceptance`` is computed for the investigation and reaches the
``/api/investigation/<inv>`` response as ``computed_acceptance`` (per-criterion
study → behavior → result). When the spine has persisted
``executive.computed_acceptance.diverges_from_authored`` (written by the
investigation acceptance evaluator), that flag is surfaced too so the
executive fold can render a code-vs-authored divergence badge — without
recomputing the divergence here.
"""
from __future__ import annotations

from pathlib import Path

import yaml
import pytest

_PKG = Path(__file__).parent.parent / "vivarium_dashboard"

_V3_BASE = {
    "schema_version": 3,
    "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
    "variants": [],
}


def _v3_study(name, tests, runs):
    return dict(_V3_BASE, name=name, objective="test", status="in_progress",
                behavior_tests=tests, runs=runs)


def test_iset_response_carries_computed_acceptance(tmp_path, dashboard_client):
    """GET /api/investigation/<inv> carries computed_acceptance with per-criterion entries."""
    ws = tmp_path / "ws"
    inv_dir = ws / "investigations" / "my-inv"
    study_dir = inv_dir / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (study_dir / "study.yaml").write_text(yaml.safe_dump(_v3_study(
        "s1", tests=[{"name": "beh-a"}],
        runs=[{"name": "r1", "status": "completed",
               "outcomes": {"beh-a": {"result": "PASS"}}}])))
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "my-inv", "studies": ["s1"],
        "acceptance_criteria": [{"study": "s1", "behavior": "beh-a"}],
    }))

    client = dashboard_client(ws)
    resp = client.get("/api/investigation/my-inv")
    assert resp.status_code == 200
    data = resp.json()
    ca = data.get("computed_acceptance")
    assert ca and "criteria" in ca
    assert ca["criteria"][0]["study"] == "s1"
    assert ca["criteria"][0]["behavior"] == "beh-a"
    assert ca["criteria"][0]["result"] == "passing"


def test_iset_response_surfaces_persisted_divergence(tmp_path, dashboard_client):
    """When executive.computed_acceptance.diverges_from_authored is persisted,
    the response surfaces it (not recomputed)."""
    ws = tmp_path / "ws"
    inv_dir = ws / "investigations" / "div-inv"
    study_dir = inv_dir / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (study_dir / "study.yaml").write_text(yaml.safe_dump(_v3_study(
        "s1", tests=[{"name": "beh-a"}],
        runs=[{"name": "r1", "status": "completed",
               "outcomes": {"beh-a": {"result": "FAIL"}}}])))
    # Authored verdict says passing, but the spine persisted a diverging
    # computed acceptance.
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "div-inv", "studies": ["s1"],
        "acceptance_criteria": [{"study": "s1", "behavior": "beh-a"}],
        "executive": {
            "verdict_status": "passing",
            "computed_verdict_status": "failing",
            "computed_acceptance": {
                "criteria": [{"study": "s1", "behavior": "beh-a", "result": "failing"}],
                "unmet": [{"study": "s1", "behavior": "beh-a", "result": "failing"}],
                "diverges_from_authored": True,
            },
        },
    }))

    client = dashboard_client(ws)
    data = client.get("/api/investigation/div-inv").json()
    ca = data.get("computed_acceptance")
    assert ca is not None
    assert ca.get("diverges_from_authored") is True


def test_walkthrough_js_renders_acceptance_rollup():
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    assert "computed_acceptance" in js
    assert "diverges_from_authored" in js
    # renders the per-criterion table + a divergence badge
    assert "acceptance-rollup" in js
    assert "acceptance-divergence" in js
