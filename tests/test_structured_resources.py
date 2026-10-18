"""Unit tests for lib.structured_resources.load_structured_resources.

Mirrors tests/test_module_stats.py: a shared read-only fixture workspace
(``ws_structured_resources``, which ships a sample manifest at the canonical
``viva_genesis/data/vg_data/structured_resources.yaml`` path) drives the
grouping / reference-resolution assertions, and ``tmp_path`` workspaces (never
mutating the shared fixture) drive the tolerance / fallback assertions.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib.structured_resources import (
    TYPE_ORDER,
    load_structured_resources,
    manifest_path,
)

FIX = Path(__file__).parent / "_fixtures" / "ws_structured_resources"


# ── grouping + reference resolution (shared fixture) ────────────────────────

def test_loads_and_groups_by_type():
    data = load_structured_resources(FIX)
    assert data is not None
    groups = data["groups"]
    # Every type present in the sample manifest is its own group.
    assert set(groups) == {
        "lipid-structure", "reaction-pathway", "imported-engine", "paper-reference",
    }
    assert data["counts"]["lipid-structure"] == 1
    assert data["counts"]["reaction-pathway"] == 2
    assert data["counts"]["imported-engine"] == 1
    assert data["counts"]["paper-reference"] == 4
    assert data["n_resources"] == 8
    # Top-level manifest metadata is surfaced.
    assert data["name"] == "structured_resources"
    assert data["version"] == "1"
    assert data["description"]


def test_type_order_is_known_types_first():
    data = load_structured_resources(FIX)
    assert data is not None
    # All four known types present, so order equals the canonical TYPE_ORDER.
    assert data["type_order"] == list(TYPE_ORDER)


def test_resolves_paper_references_by_id():
    data = load_structured_resources(FIX)
    assert data is not None
    refs = data["references_by_id"]
    # Exactly the paper-reference entries are indexed, keyed by id.
    assert set(refs) == {
        "souza2021martini3", "janke2014oleic",
        "breslow1959mechanism", "muchowska2019synthesis",
    }
    assert refs["souza2021martini3"]["doi"] == "10.1038/s41592-021-01098-3"
    # Every id a non-paper resource cites resolves to a paper-reference entry.
    for rtype, items in data["groups"].items():
        if rtype == "paper-reference":
            continue
        for r in items:
            for rid in (r.get("references") or []):
                assert rid in refs, f"{r['id']} cites unresolved {rid}"


def test_reaction_pathway_revised_schema_preserved_verbatim():
    data = load_structured_resources(FIX)
    assert data is not None
    pathways = {r["id"]: r for r in data["groups"]["reaction-pathway"]}
    formose = pathways["pathway-formose"]
    # network_summary carried through untouched (real counts, thermo, span).
    ns = formose["network_summary"]
    assert ns["n_reactions"] == 5515
    assert ns["n_species"] == 1021
    assert ns["max_carbons"] == 6
    assert ns["thermo"]["p_h"] == 10.5
    assert ns["energetic_span_kj_mol"]["mean"] == -11.2
    assert any(f["name"] == "aldol-addition" for f in ns["rule_families"])
    # keystone_rules + honesty_notes present and verbatim.
    assert formose["keystone_rules"][0]["rule"] == "r409 doubling"
    assert formose["honesty_notes"] == [
        "linear skeletons only — full branched rTCA cannot close here",
        "only 50/1022 skeletons carry live eQuilibrator ΔG′",
    ]
    # provenance.
    assert formose["data_source"]["package"] == "viva_genesis"
    # The optional legacy `reactions` table is tolerated when present.
    frag = pathways["pathway-wljp-fragment"]
    assert frag["reactions"][0]["delta_g_kjmol"] == -3.5


def test_imported_engine_fields_preserved():
    data = load_structured_resources(FIX)
    assert data is not None
    engine = data["groups"]["imported-engine"][0]
    assert engine["engine"] == "eQuilibrator"
    assert engine["viva_package"] == "viva_equilibrator"
    assert "pathway-formose" in engine["used_by"]


# ── tolerance + path resolution (tmp_path, never touching FIX) ──────────────

def test_missing_manifest_returns_none(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: solo\n")
    assert load_structured_resources(tmp_path) is None
    assert manifest_path(tmp_path) is None
    # The shared fixture must be untouched by any test.
    assert (FIX / "viva_genesis" / "data" / "vg_data" / "structured_resources.yaml").is_file()


def test_glob_fallback_for_nonstandard_package(tmp_path):
    # A workspace whose package is NOT named viva_genesis but still files the
    # manifest under <pkg>/data/vg_data/ is found via the glob fallback.
    (tmp_path / "workspace.yaml").write_text("name: other\n")
    d = tmp_path / "otherpkg" / "data" / "vg_data"
    d.mkdir(parents=True)
    (d / "structured_resources.yaml").write_text(
        "name: m\nversion: '1'\nresources:\n"
        "- {id: a, type: lipid-structure, name: A}\n"
    )
    data = load_structured_resources(tmp_path)
    assert data is not None
    assert data["counts"]["lipid-structure"] == 1


def test_canonical_path_preferred_over_glob(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: both\n")
    canon = tmp_path / "viva_genesis" / "data" / "vg_data"
    canon.mkdir(parents=True)
    (canon / "structured_resources.yaml").write_text(
        "name: canonical\nresources:\n- {id: c, type: imported-engine, name: C}\n"
    )
    other = tmp_path / "zpkg" / "data" / "vg_data"
    other.mkdir(parents=True)
    (other / "structured_resources.yaml").write_text(
        "name: other\nresources:\n- {id: o, type: imported-engine, name: O}\n"
    )
    assert manifest_path(tmp_path) == canon / "structured_resources.yaml"
    assert load_structured_resources(tmp_path)["name"] == "canonical"


def test_malformed_entries_skipped_but_valid_kept(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: m\n")
    d = tmp_path / "viva_genesis" / "data" / "vg_data"
    d.mkdir(parents=True)
    (d / "structured_resources.yaml").write_text(yaml.safe_dump({
        "name": "m",
        "resources": [
            {"id": "good", "type": "lipid-structure", "name": "Good"},
            {"type": "lipid-structure", "name": "no-id"},      # missing id → skip
            {"id": "no-type", "name": "no-type"},               # missing type → skip
            "not-a-dict",                                        # not a mapping → skip
            {"id": "", "type": "lipid-structure"},              # empty id → skip
        ],
    }))
    data = load_structured_resources(tmp_path)
    assert data is not None
    assert data["n_resources"] == 1
    assert data["groups"]["lipid-structure"][0]["id"] == "good"


def test_empty_resources_returns_none(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: m\n")
    d = tmp_path / "viva_genesis" / "data" / "vg_data"
    d.mkdir(parents=True)
    (d / "structured_resources.yaml").write_text("name: m\nresources: []\n")
    assert load_structured_resources(tmp_path) is None


def test_invalid_yaml_returns_none(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: m\n")
    d = tmp_path / "viva_genesis" / "data" / "vg_data"
    d.mkdir(parents=True)
    (d / "structured_resources.yaml").write_text("name: m\nresources: [this is : broken\n")
    assert load_structured_resources(tmp_path) is None


def test_unknown_type_grouped_and_appended_after_known(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: m\n")
    d = tmp_path / "viva_genesis" / "data" / "vg_data"
    d.mkdir(parents=True)
    (d / "structured_resources.yaml").write_text(yaml.safe_dump({
        "name": "m",
        "resources": [
            {"id": "z", "type": "zeta-thing", "name": "Z"},
            {"id": "p", "type": "paper-reference", "name": "P"},
        ],
    }))
    data = load_structured_resources(tmp_path)
    assert data is not None
    # Known type first, unknown appended after.
    assert data["type_order"] == ["paper-reference", "zeta-thing"]
    assert "zeta-thing" in data["groups"]


def test_payload_model_validates_reader_output():
    # The server endpoint wraps the reader output in StructuredResourcesPayload;
    # assert the contract holds (catches drift between reader + model).
    from vivarium_workbench.lib.models import StructuredResourcesPayload

    data = load_structured_resources(FIX)
    assert data is not None
    payload = StructuredResourcesPayload.model_validate({**data, "available": True})
    assert payload.available is True
    assert payload.n_resources == 8
    assert set(payload.groups) == set(data["groups"])
    # The unavailable case is a well-formed payload too.
    empty = StructuredResourcesPayload(available=False)
    assert empty.available is False
    assert empty.groups == {}
