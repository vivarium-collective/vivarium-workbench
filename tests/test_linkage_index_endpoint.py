"""SP4a: GET /api/linkage-index + the `_linkage_index` worker.

The endpoint runs the deterministic linkage index/queries from
``pbg_superpowers.linkage_index`` over the workspace and returns JSON so the
dashboard can render the AC→study gating matrix (and surface unlinked-AC gaps).
The dashboard adds no AI — it just runs the derive and renders. Tolerant:
never 500, empty on absence.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest


@pytest.fixture
def tmp_ws_mixed_ac(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    (ws).mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv_dir = ws / "investigations" / "the-inv"
    inv_dir.mkdir(parents=True)
    inv_dir.joinpath("investigation.yaml").write_text(yaml.safe_dump({
        "name": "the-inv",
        "studies": ["s1"],
        "acceptance_criteria": [
            {"study": "s1", "behavior": "b1"},      # keyed
            {"behavior": "b2", "status": "failed"},  # unkeyed → gap
        ],
    }))
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    sd.joinpath("study.yaml").write_text(yaml.safe_dump({
        "name": "s1", "investigation": "the-inv",
        "cites": ["bib-X"],
        "tests": [{"name": "b1"}],
        "runs": [{"name": "r1", "status": "completed",
                  "outcomes": {"b1": {"result": "PASS"}}}],
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_linkage_index_endpoint(tmp_ws_mixed_ac):
    import vivarium_dashboard.server as server
    body, code = server.Handler._linkage_index_test(
        server.WORKSPACE, investigation="the-inv")
    d = json.loads(body)
    assert code == 200
    assert "ac_matrix" in d or "nodes" in d
    matrix = d.get("ac_matrix") or {}
    assert any(r.get("gap") for r in matrix.get("criteria", []))


def test_linkage_index_source_query(tmp_ws_mixed_ac):
    import vivarium_dashboard.server as server
    body, code = server.Handler._linkage_index_test(
        server.WORKSPACE, source="bib-X")
    d = json.loads(body)
    assert code == 200
    assert "s1" in (d.get("studies") or [])


def test_linkage_index_tolerant_on_missing_ws(tmp_path, monkeypatch):
    import vivarium_dashboard.server as server
    missing = tmp_path / "does-not-exist"
    body, code = server.Handler._linkage_index_test(missing, investigation="nope")
    assert code == 200  # never 500
    json.loads(body)  # valid JSON


# --- SP4b: observable_registry + composite queries (INJECTED build) --------

@pytest.fixture
def tmp_ws_observable_registry(tmp_path, monkeypatch):
    """A workspace whose single study uses composite ``cm-comp`` and measures
    ``listeners.mass.cell_mass`` — so an injected build that emits a matching
    leaf links the composite to the study in the observable registry."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    sd.joinpath("study.yaml").write_text(yaml.safe_dump({
        "name": "s1",
        "baseline": {"name": "bl", "composite": "cm-comp"},
        "tests": [{"name": "b1",
                   "measure": {"field": "listeners.mass.cell_mass"}}],
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_linkage_index_observable_registry_query(tmp_ws_observable_registry, monkeypatch):
    import vivarium_dashboard.server as server
    # Stub the (expensive) composite build so the test never builds for real.
    monkeypatch.setattr(server, "_observables_for_ref",
                        lambda ws, ref: {"leaves": ["agents.0.listeners.mass.cell_mass"],
                                         "catalogs": {}})
    body, code = server.Handler._linkage_index_test(
        server.WORKSPACE, observable_registry="listeners.mass.cell_mass")
    d = json.loads(body)
    assert code == 200
    assert set(d) == {"studies", "composites"}
    assert "s1" in (d.get("studies") or [])
    assert "cm-comp" in (d.get("composites") or [])


def test_linkage_index_composite_query(tmp_ws_observable_registry, monkeypatch):
    import vivarium_dashboard.server as server
    monkeypatch.setattr(server, "_observables_for_ref",
                        lambda ws, ref: {"leaves": ["agents.0.listeners.mass.cell_mass"],
                                         "catalogs": {}})
    body, code = server.Handler._linkage_index_test(
        server.WORKSPACE, composite="cm-comp")
    d = json.loads(body)
    assert code == 200
    assert set(d) == {"emits", "used_by_studies"}
    assert "listeners.mass.cell_mass" in (d.get("emits") or [])
    assert "s1" in (d.get("used_by_studies") or [])


def test_linkage_index_observable_registry_tolerant_on_build_failure(
        tmp_ws_observable_registry, monkeypatch):
    import vivarium_dashboard.server as server

    def _boom(ws, ref):
        raise RuntimeError("composite build blew up")

    monkeypatch.setattr(server, "_observables_for_ref", _boom)
    body, code = server.Handler._linkage_index_test(
        server.WORKSPACE, observable_registry="listeners.mass.cell_mass")
    d = json.loads(body)
    assert code == 200  # never 500
    assert set(d) == {"studies", "composites"}

    body2, code2 = server.Handler._linkage_index_test(
        server.WORKSPACE, composite="cm-comp")
    d2 = json.loads(body2)
    assert code2 == 200
    assert set(d2) == {"emits", "used_by_studies"}
