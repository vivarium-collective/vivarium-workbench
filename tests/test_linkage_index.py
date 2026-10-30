"""Tests for vivarium_workbench.lib.linkage_index (SP4a).

The linkage index is a derived, EPHEMERAL knowledge graph over the workspace
YAML — studies ↔ composites ↔ observables ↔ sources ↔ findings ↔ acceptance ↔
study-DAG — plus the cheap reverse queries that today need grep. Pure derive,
no writes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from discovery_engine import study_io
from vivarium_workbench.lib.linkage_index import (
    build_index,
    ac_gating_matrix,
    studies_for_source,
    findings_for_observable,
    study_dag,
    enrich_observable_edges,
    studies_for_observable,
    composite_emits,
)


# ---------------------------------------------------------------------------
# Workspace builders
# ---------------------------------------------------------------------------

def _write(path: Path, spec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    study_io.save_yaml_atomic(path, spec)


def _ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "workspace.yaml", {"name": "ws", "package_path": "pbg_ws"})
    return root


def _inv(root: Path, slug: str, spec: dict) -> None:
    spec = {"name": slug, **spec}
    _write(root / "investigations" / slug / "investigation.yaml", spec)


def _study(root: Path, slug: str, spec: dict, inv: str | None = None) -> None:
    spec = {"name": slug, **spec}
    if inv:
        spec.setdefault("investigation", inv)
    _write(root / "studies" / slug / "study.yaml", spec)


def _snapshot(root: Path) -> dict:
    snap: dict = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_inv_mixed_ac(tmp_path) -> Path:
    """One investigation: one AC keyed to a study (with a result), one AC with
    NO study: link (the gap)."""
    root = _ws(tmp_path)
    _inv(root, "the-inv", {
        "studies": ["s1"],
        "acceptance_criteria": [
            {"study": "s1", "behavior": "b1"},     # keyed
            {"behavior": "b2", "status": "failed"},  # unkeyed → gap
        ],
    })
    _study(root, "s1", {
        "tests": [{"name": "b1"}],
        "runs": [{"name": "r1", "status": "completed",
                  "outcomes": {"b1": {"result": "PASS"}}}],
    }, inv="the-inv")
    return root


@pytest.fixture
def tmp_ws_two_studies_cite_X(tmp_path) -> Path:
    root = _ws(tmp_path)
    _study(root, "s1", {"cites": ["bib-key-X", "other"]})
    _study(root, "s2", {
        "tests": [{"name": "t", "cites": ["bib-key-X"]}],
    })
    _study(root, "s3", {"cites": ["unrelated"]})
    return root


@pytest.fixture
def tmp_ws_finding_measures_obs(tmp_path) -> Path:
    root = _ws(tmp_path)
    _study(root, "s1", {
        "tests": [{
            "name": "mass-test",
            "measure": {"path": "listeners.mass.cell_mass"},
        }],
        "findings": [{
            "id": "F-01",
            "evidence": {"from_test": "mass-test"},
        }],
    })
    return root


@pytest.fixture
def tmp_ws_dag(tmp_path) -> Path:
    root = _ws(tmp_path)
    _inv(root, "the-inv", {"studies": ["a", "b"]})
    _study(root, "a", {"pipeline_gate": {"prerequisites": [], "enables": ["b"]}},
           inv="the-inv")
    _study(root, "b", {"pipeline_gate": {"prerequisites": ["a"], "enables": []}},
           inv="the-inv")
    return root


@pytest.fixture
def tmp_ws(tmp_path) -> Path:
    root = _ws(tmp_path)
    _inv(root, "the-inv", {
        "studies": ["s1"],
        "acceptance_criteria": [{"study": "s1", "behavior": "b1"}],
        "references": [{"path": "paper.pdf"}],
    })
    _study(root, "s1", {
        "cites": ["k1"],
        "tests": [{"name": "b1", "measure": {"path": "x.y"}}],
        "readouts": [{"name": "ro", "identifier": "x.y"}],
        "findings": [{"id": "F", "evidence": {"from_test": "b1"}}],
        "conditions": {"baseline": {"composite": "base"}},
        "pipeline_gate": {"prerequisites": [], "enables": []},
    }, inv="the-inv")
    return root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ac_gating_matrix_flags_unkeyed_criteria(tmp_inv_mixed_ac):
    m = ac_gating_matrix(tmp_inv_mixed_ac, "the-inv")
    assert any(r["covered_by"] for r in m["criteria"])  # keyed AC → a study
    assert any(r["gap"] is True and not r["covered_by"] for r in m["criteria"])


def test_studies_for_source_inverts_cites(tmp_ws_two_studies_cite_X):
    assert set(studies_for_source(tmp_ws_two_studies_cite_X, "bib-key-X")) == {"s1", "s2"}


def test_findings_for_observable(tmp_ws_finding_measures_obs):
    found = findings_for_observable(tmp_ws_finding_measures_obs, "listeners.mass.cell_mass")
    assert "F-01" in [f["finding"] for f in found]


def test_study_dag_edges(tmp_ws_dag):
    dag = study_dag(tmp_ws_dag, "the-inv")
    assert any(e["from"] and e["to"] for e in dag["edges"])


def test_build_index_shape(tmp_ws):
    idx = build_index(tmp_ws)
    assert "nodes" in idx and "edges" in idx
    types = {n["type"] for n in idx["nodes"]}
    assert {"study", "composite", "observable", "source"} <= types


def test_build_index_pure_read(tmp_ws):
    before = _snapshot(tmp_ws)
    build_index(tmp_ws)
    ac_gating_matrix(tmp_ws, "the-inv")
    studies_for_source(tmp_ws, "k1")
    findings_for_observable(tmp_ws, "x.y")
    study_dag(tmp_ws, "the-inv")
    assert _snapshot(tmp_ws) == before  # no writes


# ---------------------------------------------------------------------------
# SP4b: composite → observable edge enrichment (injected build) + registry
# ---------------------------------------------------------------------------

def _stub_obs(ref):
    """Injected stand-in for the dashboard's _observables_for_ref — NO real build."""
    if "baseline" in ref:
        return {
            "leaves": ["agents.0.listeners.mass.cell_mass", "agents.0.bulk[ATP[c]]"],
            "catalogs": {},
        }
    return {"leaves": [], "catalogs": {}}


@pytest.fixture
def tmp_ws_obs_registry(tmp_path) -> Path:
    """Two studies sharing a baseline composite that emits cell_mass; one study
    measures the BARE token the composite emits under an agents.N. prefix."""
    root = _ws(tmp_path)
    _study(root, "s1", {
        "conditions": {"baseline": {"composite": "v2ecoli.composites.baseline.baseline"}},
        "tests": [{"name": "mass", "measure": {"path": "listeners.mass.cell_mass"}}],
    })
    _study(root, "s2", {
        "conditions": {"baseline": {"composite": "v2ecoli.composites.baseline.baseline"}},
    })
    return root


def test_enrich_adds_composite_emits_edges(tmp_ws_obs_registry):
    idx = build_index(tmp_ws_obs_registry)
    assert not [e for e in idx["edges"] if e["type"] == "emits"]  # YAML core: none yet
    enrich_observable_edges(idx, _stub_obs)
    emits = [e for e in idx["edges"] if e["type"] == "emits"]
    assert emits
    assert any(e["from"].startswith("composite:") and e["to"].startswith("observable:")
               for e in emits)


def test_emits_edge_strips_lineage_prefix_to_match_study_token(tmp_ws_obs_registry):
    idx = build_index(tmp_ws_obs_registry)
    enrich_observable_edges(idx, _stub_obs)
    measures = {e["to"] for e in idx["edges"] if e["type"] == "measures"}
    emits = {e["to"] for e in idx["edges"] if e["type"] == "emits"}
    assert emits & measures  # normalized observable node id is shared
    assert "observable:listeners.mass.cell_mass" in emits


def test_studies_for_observable_cross_study(tmp_ws_obs_registry):
    res = studies_for_observable(tmp_ws_obs_registry, "listeners.mass.cell_mass",
                                 observables_for_ref=_stub_obs)
    assert set(res["studies"]) >= {"s1", "s2"}
    assert res["composites"]


def test_composite_emits(tmp_ws_obs_registry):
    res = composite_emits(tmp_ws_obs_registry, "v2ecoli.composites.baseline.baseline",
                          observables_for_ref=_stub_obs)
    assert any("cell_mass" in tok for tok in res["emits"])
    assert res["used_by_studies"]


def test_enrich_is_pure_given_injected_fn(tmp_ws_obs_registry):
    before = _snapshot(tmp_ws_obs_registry)
    idx = build_index(tmp_ws_obs_registry)
    enrich_observable_edges(idx, _stub_obs)
    assert _snapshot(tmp_ws_obs_registry) == before  # no writes


def test_enrich_tolerates_raising_observables_for_ref(tmp_ws_obs_registry):
    def boom(ref):
        raise RuntimeError("build failed")
    idx = build_index(tmp_ws_obs_registry)
    enrich_observable_edges(idx, boom)  # must NOT raise
    assert not [e for e in idx["edges"] if e["type"] == "emits"]


@pytest.fixture
def tmp_ws_bulk_dialects(tmp_path) -> Path:
    """Two studies declaring the SAME bulk observable in DIFFERENT dialects —
    a dotted store_path and a bracketed identifier — sharing a composite whose
    leaf is the third dialect (``agents.0.bulk[ATP[c]]``). The registry must
    reconcile all three (else the bulk class, which dominates v2ecoli, is
    silently dead)."""
    root = _ws(tmp_path)
    _study(root, "dotted", {
        "conditions": {"baseline": {"composite": "v2ecoli.composites.baseline.baseline"}},
        "readouts": [{"name": "atp", "store_path": "bulk.ATP[c]"}],
    })
    _study(root, "bracketed", {
        "conditions": {"baseline": {"composite": "v2ecoli.composites.baseline.baseline"}},
        "readouts": [{"name": "atp", "identifier": "bulk__count[ATP[c], ADP[c]]"}],
    })
    return root


def test_studies_for_observable_reconciles_bulk_dialects(tmp_ws_bulk_dialects):
    # Query in the dotted dialect; composite leaf is bracketed; one study uses a
    # bracketed identifier — all must match on the ATP[c] atom.
    res = studies_for_observable(tmp_ws_bulk_dialects, "bulk.ATP[c]",
                                 observables_for_ref=_stub_obs)
    assert set(res["studies"]) >= {"dotted", "bracketed"}, res
    # And the bracketed-identifier dialect resolves the same registry.
    res2 = studies_for_observable(tmp_ws_bulk_dialects, "bulk__count[ATP[c], ADP[c]]",
                                  observables_for_ref=_stub_obs)
    assert set(res2["studies"]) >= {"dotted", "bracketed"}, res2


def test_enrich_idempotent_no_duplicate_edges(tmp_ws_obs_registry):
    def dup_leaves(ref):
        # same leaf twice — must not produce two emits edges
        if "baseline" in ref:
            return {"leaves": ["agents.0.listeners.mass.cell_mass",
                               "agents.0.listeners.mass.cell_mass"], "catalogs": {}}
        return {"leaves": [], "catalogs": {}}
    idx = build_index(tmp_ws_obs_registry)
    enrich_observable_edges(idx, dup_leaves)
    enrich_observable_edges(idx, dup_leaves)  # second call: still no dups
    emits = [e for e in idx["edges"] if e["type"] == "emits"]
    assert len(emits) == len(set((e["from"], e["to"]) for e in emits))
