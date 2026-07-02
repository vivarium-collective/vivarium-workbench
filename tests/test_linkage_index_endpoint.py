"""SP4a: GET /api/linkage-index + the `_linkage_index` worker.

The endpoint runs the deterministic linkage index/queries from
``pbg_superpowers.linkage_index`` over the workspace and returns JSON so the
dashboard can render the AC→study gating matrix (and surface unlinked-AC gaps).
The dashboard adds no AI — it just runs the derive and renders. Tolerant:
never 500, empty on absence.
"""
from __future__ import annotations

import yaml
import pytest

from vivarium_dashboard.lib.report_views import build_linkage_index


@pytest.fixture
def tmp_ws_mixed_ac(tmp_path):
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
    return ws


def test_linkage_index_endpoint(tmp_ws_mixed_ac):
    d, code = build_linkage_index(
        tmp_ws_mixed_ac, investigation="the-inv")
    assert code == 200
    assert "ac_matrix" in d or "nodes" in d
    matrix = d.get("ac_matrix") or {}
    assert any(r.get("gap") for r in matrix.get("criteria", []))


def test_linkage_index_source_query(tmp_ws_mixed_ac):
    d, code = build_linkage_index(
        tmp_ws_mixed_ac, source="bib-X")
    assert code == 200
    assert "s1" in (d.get("studies") or [])


def test_linkage_index_tolerant_on_missing_ws(tmp_path):
    missing = tmp_path / "does-not-exist"
    d, code = build_linkage_index(missing, investigation="nope")
    assert code == 200  # never 500
    assert isinstance(d, dict)  # valid payload


# --- SP4b: observable_registry + composite queries (INJECTED build) --------

@pytest.fixture
def tmp_ws_observable_registry(tmp_path):
    """A workspace whose single study uses composite ``cm-comp`` and measures
    ``listeners.mass.cell_mass`` — so an injected build that emits a matching
    leaf links the composite to the study in the observable registry."""
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
    return ws


def test_linkage_index_observable_registry_query(tmp_ws_observable_registry):
    # Stub the (expensive) composite build so the test never builds for real.
    stub = lambda ws, ref: {"leaves": ["agents.0.listeners.mass.cell_mass"],
                            "catalogs": {}}
    d, code = build_linkage_index(
        tmp_ws_observable_registry,
        observable_registry="listeners.mass.cell_mass",
        observables_for_ref_fn=stub)
    assert code == 200
    assert set(d) == {"studies", "composites"}
    assert "s1" in (d.get("studies") or [])
    assert "cm-comp" in (d.get("composites") or [])


def test_linkage_index_composite_query(tmp_ws_observable_registry):
    stub = lambda ws, ref: {"leaves": ["agents.0.listeners.mass.cell_mass"],
                            "catalogs": {}}
    d, code = build_linkage_index(
        tmp_ws_observable_registry, composite="cm-comp",
        observables_for_ref_fn=stub)
    assert code == 200
    assert set(d) == {"emits", "used_by_studies"}
    assert "listeners.mass.cell_mass" in (d.get("emits") or [])
    assert "s1" in (d.get("used_by_studies") or [])


def test_linkage_index_observable_registry_tolerant_on_build_failure(
        tmp_ws_observable_registry):

    def _boom(ws, ref):
        raise RuntimeError("composite build blew up")

    d, code = build_linkage_index(
        tmp_ws_observable_registry,
        observable_registry="listeners.mass.cell_mass",
        observables_for_ref_fn=_boom)
    assert code == 200  # never 500
    assert set(d) == {"studies", "composites"}

    d2, code2 = build_linkage_index(
        tmp_ws_observable_registry, composite="cm-comp",
        observables_for_ref_fn=_boom)
    assert code2 == 200
    assert set(d2) == {"emits", "used_by_studies"}
